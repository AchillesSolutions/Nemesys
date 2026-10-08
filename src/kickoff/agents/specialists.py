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
Write in {language}. Keep player and club names exactly as given.
Use only the facts in the brief. Never invent players, statistics or rules.
Keep it family-friendly and never mention betting or odds.
If you have nothing worth saying, return an empty list of proposals.
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
        return {
            "language": LANGUAGE_NAMES[p.language],
            "viewer": LEVEL_GUIDE[p.level],
            "supports": self.describer.club(p.team) if p.team else "neutral",
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
                         kind=self.kind, concept=d.concept, made_at=m.update.event.elapsed)
                for d in drafts[:2] if d.text.strip()
            ]
        await ctx.send_message(SpecialistResult(agent=self.name, moment=m, proposals=proposals))


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
If the viewer supports a team, speak as a friend who supports it too, but stay fair. Leave "concept" empty
unless you are illustrating "possession" or "counter_attack".
"""
    TRIGGERS = {"possession_dominance", "numerical_advantage", "equaliser", "takes_the_lead", "extends_lead"}
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
the projected positions in the brief. "if_score_stands" is where each club would finish this matchweek if the
current score were the final score: describe that, and do not invent other scorelines. A club that would not
move can still be interesting (who they would stay level with, how far from the top four).
One or two sentences. Priority 3-6; up to 7 at full-time. Leave "concept" empty.
"""
    TRIGGERS = {"quiet_spell", "equaliser", "takes_the_lead", "extends_lead"}

    def brief(self, m: Moment) -> dict | None:
        ev = m.update.event
        if not (m.signal_kinds & self.TRIGGERS or ev.type in (EventType.HALF_TIME, EventType.FULL_TIME)):
            return None
        snap = m.update.snapshot
        return {**self.base_brief(m), "league": self.world.league, "matchweek": self.world.matchweek,
                "table_before_this_match": self.describer.table(top=8),
                "if_score_stands": stakes(self.world, snap.home.id, snap.away.id, snap.home.score, snap.away.score)}


SPECIALISTS: list[type[Specialist]] = [Teacher, Storyteller, LeagueGuide]
