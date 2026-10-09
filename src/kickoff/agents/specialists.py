"""The three specialist agents: Teacher, Storyteller and League guide.

Each specialist is a workflow executor wrapping an Agent Framework ``Agent``.
Plain code first decides whether the specialist has anything to add (the
Teacher stays silent about rules the viewer already knows; the League guide
waits for quiet spells and goals). Only then is the model called, so most
events cost no model calls at all.
"""

from __future__ import annotations

import itertools
import json
import logging

from agent_framework import Agent, Executor, WorkflowContext, handler

from ..league import stakes
from ..models import EventType, World
from .context import Describer
from .glossary import RULES
from .i18n import concept_label
from .messages import Moment, Proposal, SpecialistReply, SpecialistResult
from .viewer import LANGUAGE_NAMES, HelpLevel, depth_for

log = logging.getLogger(__name__)
_ids = itertools.count(1)

STYLE_GUIDE = """
You are part of Kick-Off, a live companion that helps people enjoy football matches.
Write for the ear: short spoken sentences, plain words, no lists, no emoji, no markdown.
Keep each proposal under 30 words (under 60 characters in Mandarin): it is spoken over a live match.
Say minutes the way a commentator would: "the 62nd minute", in Mandarin "第62分钟", in Spanish "el minuto 62".
Say scores the way they sound aloud: "Kilburn 2, Harbour City 1", "it's 2-2", "two all" or "level at two each";
never put a team name on both sides of a score ("Kilburn 2-2 Harbour City"). In English a zero in a score is
"nil" ("one-nil", "nil-nil"), never "zero".
You don't know the viewer's name. Never address them, and never start a sentence with a team's name followed by a
comma as if speaking to it.
Write in {language}. Keep player and club names exactly as given.
Use only the facts in the brief. Never invent players, statistics or rules.
Keep it family-friendly and never mention betting or odds.
If you have nothing worth saying, return an empty list of proposals.
""".strip()

FAN_VOICE = """
The viewer supports {team}. Speak like a fellow {team} fan sitting next to them: in lines about how the match
is going (goals, chances, momentum, nerves) call {team} "we", "us" and "our" (in Mandarin 我们, in Spanish
"nosotros", "nuestro equipo", "¡vamos!"). Still use the name {team} the first time you mention them in a line
that teaches a rule, so a newcomer always knows which team is which. Call the other side by its name.
Be on their side but fair: disappointment about a decision is fine, calling a correct decision unfair is not.
""".strip()

LEVEL_GUIDE = {
    HelpLevel.BEGINNER: "The viewer is brand new to football. Assume no knowledge at all.",
    HelpLevel.GETTING_THERE: "The viewer knows the basics (goals, fouls, corners) but not the finer rules.",
    HelpLevel.BIG_PICTURE: "The viewer knows the rules and wants the story of the match and what is at stake.",
    HelpLevel.ANALYST: ("The viewer is an analyst or broadcaster. Be precise and tactical: shape, pressing, "
                        "possession and chance quality, with numbers from the brief."),
}


def instructions(role: str, language_name: str = "the language named in the brief") -> str:
    return f"{STYLE_GUIDE.format(language=language_name)}\n\n{role.strip()}"


class Specialist(Executor):
    """Base executor: decide relevance in code, then ask the model."""

    name: str
    kind: str

    def __init__(self, agent: Agent, world: World, describer: Describer):
        super().__init__(id=self.name)
        self.agent, self.world, self.describer = agent, world, describer

    def brief(self, m: Moment) -> dict | None:
        """The facts for the model, or None if this specialist has nothing to add."""
        raise NotImplementedError

    def base_brief(self, m: Moment) -> dict:
        p = m.profile
        team = self.describer.club(p.team) if p.team else None
        return {
            "language": LANGUAGE_NAMES[p.language],
            "viewer": LEVEL_GUIDE[p.level],
            "team_the_viewer_supports": team or "none (neutral viewer)",
            **({"voice": FAN_VOICE.format(team=team)} if team else {}),
            "event": m.event_text,
            "situation": m.situation,
            "recently_said": m.recent_lines[-4:],
        }

    @handler
    async def run(self, m: Moment, ctx: WorkflowContext[SpecialistResult]) -> None:
        brief = self.brief(m)
        proposals: list[Proposal] = []
        if brief is not None:
            try:
                reply = await self.agent.run(json.dumps(brief, ensure_ascii=False))
                drafts = reply.value.proposals if isinstance(reply.value, SpecialistReply) else []
            except Exception:  # a failed specialist must not silence the others
                log.exception("%s failed", self.name)
                drafts = []
            proposals = [
                Proposal(id=next(_ids), agent=self.name, text=d.text.strip(), priority=max(1, min(10, d.priority)),
                         kind=self.kind, concept=self._concept(d.concept, brief), made_at=m.update.event.elapsed,
                         score=(m.update.snapshot.home.score, m.update.snapshot.away.score))
                for d in drafts[:2] if d.text.strip()
            ]
        await ctx.send_message(SpecialistResult(agent=self.name, moment=m, proposals=proposals))


    @staticmethod
    def _concept(concept: str | None, brief: dict) -> str | None:
        """Only the rule the brief asked to be taught counts as taught: a story line mentioning
        possession has not explained it."""
        return concept if concept and concept == brief.get("concept") else None


class Teacher(Specialist):
    name, kind = "teacher", "explain"
    ROLE = """
You are the Teacher. You explain football rules the moment they matter, using the rule fact in the brief.
The brief says how deep to go:
- "full": first time the viewer meets this. Two short sentences: what the rule is, then how it applies to what just happened.
- "reminder": they have heard it once. One short sentence linking the rule to this moment.
- "nod": they know it now. Eight words or fewer.
Priority: full 8, reminder 5, nod 3. Set "concept" to the concept id from the brief.
"""

    def brief(self, m: Moment) -> dict | None:
        for concept in m.concepts:
            depth = depth_for(m.profile, m.knowledge, concept, m.update.event.elapsed)
            if depth and concept in RULES:
                return {**self.base_brief(m), "concept": concept, "concept_name": concept_label(concept, m.profile.language),
                        "rule_fact": RULES[concept], "depth": depth}
        return None


class Storyteller(Specialist):
    name, kind = "storyteller", "story"
    ROLE = """
You are the Storyteller. You spot the bigger picture a newcomer would miss: momentum swings, turning points,
how each team's style is shaping the match, what a goal or red card changes.
Say one or two sentences. Do not explain rules; the Teacher does that.
Priority: 8-9 for turning points (goals, red cards, comebacks), 5-6 for patterns, 3-4 for colour.
A "bridge" signal means play skipped ahead between highlights: in one sentence, sum up how the skipped
minutes went using possession_in_gap and shots_in_gap, but only if something stands out (priority 5).
If nothing stands out, return no proposals.
If the brief has a "voice" instruction, follow it. Leave "concept" empty
unless you are illustrating "possession" or "counter_attack".
"""
    TRIGGERS = {"possession_dominance", "numerical_advantage", "equaliser", "takes_the_lead", "extends_lead", "bridge"}
    EVENTS = {EventType.HALF_TIME, EventType.FULL_TIME, EventType.SUBSTITUTION, EventType.VAR_DECISION,
              EventType.GOAL_DISALLOWED, EventType.PENALTY}

    def brief(self, m: Moment) -> dict | None:
        ev = m.update.event
        triggers = self.TRIGGERS | ({"quiet_spell"} if m.profile.level == HelpLevel.ANALYST else set())
        if not (m.signal_kinds & triggers or ev.type in self.EVENTS):
            return None
        home, away = m.update.snapshot.home.id, m.update.snapshot.away.id
        return {**self.base_brief(m), "signals": m.signals,
                "home_club": self.describer.club_profile(home), "away_club": self.describer.club_profile(away)}


class LeagueGuide(Specialist):
    name, kind = "league_guide", "stakes"
    ROLE = """
You are the League guide. You explain what this match means for the league table, using only the table and
the projected positions in the brief. On a "bridge" (play skipping ahead between highlights) you may remind
the viewer where the teams stand, at priority 4. "if_score_stands" is where each club would finish this matchweek if the
current score were the final score: describe that, and do not invent other scorelines. A club that would not
move can still be interesting (who they would stay level with, how far from the top four).
One or two sentences. Priority 3-6; up to 7 at full-time. Leave "concept" empty.
"""
    TRIGGERS = {"quiet_spell", "equaliser", "takes_the_lead", "extends_lead", "bridge"}

    def brief(self, m: Moment) -> dict | None:
        ev = m.update.event
        if not (m.signal_kinds & self.TRIGGERS or ev.type in (EventType.HALF_TIME, EventType.FULL_TIME)):
            return None
        snap = m.update.snapshot
        return {**self.base_brief(m), "league": self.world.league, "matchweek": self.world.matchweek,
                "table_before_this_match": self.describer.table(top=8),
                "if_score_stands": stakes(self.world, snap.home.id, snap.away.id, snap.home.score, snap.away.score)}


SPECIALISTS: list[type[Specialist]] = [Teacher, Storyteller, LeagueGuide]
