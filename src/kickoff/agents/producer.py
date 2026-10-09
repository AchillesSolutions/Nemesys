"""The Producer: decides what actually reaches the viewer.

It receives every specialist's proposals at once (fan-in) and works in two
layers:

* Hard rules in code: drop stale backlog items, cap how much is said per
  moment, never repeat a line, and skip the model entirely when there is
  only one obvious thing to say.
* Judgement from the model: rank competing proposals, merge two into one
  natural line when they fit together, pick the tone for this viewer, and
  hold back things that are better said in a quiet spell.
"""

from __future__ import annotations

import json
import logging

from agent_framework import Agent, Executor, WorkflowContext, handler
from typing_extensions import Never

from ..models import EventType
from .context import Describer
from .i18n import concept_label, kind_label
from .messages import Decision, Line, Moment, Proposal, ProducerReply, SpecialistResult, Tone
from .specialists import FAN_VOICE, instructions
from .viewer import LANGUAGE_NAMES

log = logging.getLogger(__name__)

BACKLOG_MAX_AGE_S = 10 * 60

ROLE = """
You are the Producer of a live football companion, like the producer in a broadcast truck.
Specialists have proposed things to say. You decide what is said now.
Rules:
- Say at most {max_lines} line(s). Fewer is better: silence is fine when nothing matters.
- In big moments, lead with what just happened, then the most useful explanation.
- You may merge two proposals into one smooth line, but never change their facts and keep it under 45 words.
- A proposal marked "first_explanation" teaches a rule for the first time. If you use it, keep the explanation
  itself, not just the word, so the viewer actually learns the rule.
- Do not repeat anything in "recently_said".
- Choose the tone for this viewer: if they support a team, sound hopeful or excited when things go well for it
  and tense or disappointed when they do not. Neutral viewers get neutral or excited.
- If the brief has a "voice" instruction, the final lines must follow it (a supporter hears "we" for their team).
- Put stakes or story proposals that are not urgent into defer_ids; they will be offered again in a quiet spell.
- card_title is two to four words in the viewer's language.
- Never address the viewer by a team's name ("Kilburn, ..."): you are talking to a person, not to a team.
- Say minutes as a commentator would: "the 62nd minute" (第62分钟 / "el minuto 62"), never a bare number.
"""


def max_lines_for(m: Moment) -> int:
    return 2 if m.salience >= 3 else 1


def team_tone(m: Moment) -> Tone:
    """Deterministic tone for single-proposal moments the model does not see."""
    ev, team = m.update.event, m.profile.team
    if ev.type in (EventType.GOAL, EventType.PENALTY, EventType.RED_CARD, EventType.GOAL_DISALLOWED):
        if team is None:
            return Tone.EXCITED
        good = (ev.team == team) != (ev.type in (EventType.RED_CARD, EventType.GOAL_DISALLOWED))
        return Tone.EXCITED if good else Tone.DISAPPOINTED
    if m.signal_kinds & {"quiet_spell"}:
        return Tone.CALM
    return Tone.NEUTRAL


def still_true(m: Moment, recent: set[str]) -> list[Proposal]:
    """Held-back lines worth reusing: not too old, not already said, and written at this same score."""
    now = m.update.event.elapsed
    score = (m.update.snapshot.home.score, m.update.snapshot.away.score)
    return [p for p in m.backlog
            if now - p.made_at <= BACKLOG_MAX_AGE_S and p.score == score and p.text not in recent]


class Producer(Executor):
    def __init__(self, agent: Agent, describer: Describer):
        super().__init__(id="producer")
        self.agent, self.describer = agent, describer

    @handler
    async def decide(self, results: list[SpecialistResult], ctx: WorkflowContext[Never, Decision]) -> None:
        m = results[0].moment
        recent = set(m.recent_lines)
        fresh = [p for r in results for p in r.proposals if p.text not in recent]
        backlog = still_true(m, recent)
        candidates = sorted(fresh + backlog, key=lambda p: -p.priority)

        if not candidates:
            await ctx.yield_output(Decision(m, [], [], [], "Nothing worth saying."))
        elif len(candidates) == 1:
            p = candidates[0]
            line = Line(text=p.text, tone=team_tone(m), card_title=self._title(p, m), sources=[p.agent],
                        proposal_ids=[p.id], concepts=[p.concept] if p.concept else [])
            await ctx.yield_output(Decision(m, candidates, [line], [], f"Only the {p.agent} had something to add."))
        else:
            await ctx.yield_output(await self._judge(m, candidates))

    async def _judge(self, m: Moment, candidates: list[Proposal]) -> Decision:
        by_id = {p.id: p for p in candidates}
        brief = {
            "language": LANGUAGE_NAMES[m.profile.language],
            "team_the_viewer_supports": self.describer.club(m.profile.team) if m.profile.team else "none (neutral viewer)",
            **({"voice": FAN_VOICE.format(team=self.describer.club(m.profile.team))} if m.profile.team else {}),
            "event": m.event_text,
            "importance": ["routine", "a chance", "a stoppage worth explaining", "a big moment"][m.salience],
            "recently_said": m.recent_lines[-4:],
            "proposals": [{"id": p.id, "from": p.agent, "priority": p.priority, "kind": p.kind, "text": p.text,
                           "first_explanation": p.agent == "teacher" and p.priority >= 8}
                          for p in candidates],
        }
        prompt = ROLE.format(max_lines=max_lines_for(m)) + "\n\nBrief:\n" + json.dumps(brief, ensure_ascii=False)
        try:
            reply = (await self.agent.run(prompt)).value
            if not isinstance(reply, ProducerReply):
                raise ValueError("no structured reply")
        except Exception:
            log.exception("producer model failed; falling back to top priority")
            return self._fallback(m, candidates)

        lines = []
        for pl in reply.lines[: max_lines_for(m)]:
            used = [by_id[i] for i in pl.proposal_ids if i in by_id]
            lines.append(Line(text=pl.text.strip(), tone=pl.tone, card_title=pl.card_title,
                              sources=sorted({p.agent for p in used}), proposal_ids=[p.id for p in used],
                              concepts=[p.concept for p in used if p.concept]))
        used_ids = {i for pl in reply.lines for i in pl.proposal_ids}
        deferred = [by_id[i] for i in reply.defer_ids if i in by_id and i not in used_ids]
        return Decision(m, candidates, lines, deferred, reply.reasoning)

    def _fallback(self, m: Moment, candidates: list[Proposal]) -> Decision:
        top = candidates[: max_lines_for(m)]
        lines = [Line(text=p.text, tone=team_tone(m), card_title=self._title(p, m), sources=[p.agent],
                      proposal_ids=[p.id], concepts=[p.concept] if p.concept else []) for p in top]
        return Decision(m, candidates, lines, [], "Fallback: highest priority first.")

    @staticmethod
    def _title(p: Proposal, m: Moment) -> str:
        lang = m.profile.language
        return concept_label(p.concept, lang) if p.concept else kind_label(p.kind, lang)


def producer_instructions() -> str:
    return instructions("You are the Producer. Follow the rules given with each brief.")
