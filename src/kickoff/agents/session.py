"""One viewer's commentary session.

The session sits between the Match reader and the agent workflow. It decides
when to wake the agents, keeps the viewer's knowledge and backlog, and keeps
a talk-time budget: Kick-Off never starts a new line while the last one is
still being spoken, unless something big happens.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from pydantic import BaseModel, Field

from ..match_reader import MatchSnapshot, ReaderUpdate
from ..models import EventType, Match, MatchEvent, Position, World
from .context import Describer, concepts_for
from .i18n import (CALLS, FAN_HEADLINES, HEADLINE_TITLES, HEADLINES, INTRO_CLOSE, INTRO_OPEN, INTRO_TITLES, KIT_LINE,
                   KIT_LINE_FAN, KITS, concept_label, score_phrase)
from .llm import ClientFactory
from .messages import REPORTER, Decision, Line, Moment, Proposal
from .producer import team_tone
from .viewer import ASSUMED_KNOWN, Knowledge, Language, ViewerProfile, depth_for
from .workflow import build_workflow

SIGNALS_WORTH_WAKING = {"quiet_spell", "possession_dominance", "numerical_advantage"}
BACKLOG_SIZE = 3


def speech_seconds(text: str, language: Language) -> float:
    """Rough time to speak a line aloud, at Kick-Off's brisk commentary pace."""
    if language == Language.ZH:
        return len(text) / 5.2
    return len(text.split()) / 3.0


class Commentary(BaseModel):
    """What the session sends to the viewer for one event."""

    event_id: int
    minute: str
    elapsed: int = 0  # match second of the moment it is about, so the feed can stay in match order
    language: Language = Language.EN  # what it was written in: lines in an old language are not spoken
    lines: list[Line]
    reasoning: str = ""
    proposals: list[dict] = Field(default_factory=list, description="Everything the specialists suggested")
    learned: list[str] = Field(default_factory=list, description="Labels of concepts learned so far, in order")


class CommentarySession:
    def __init__(self, world: World, match: Match, profile: ViewerProfile, clients: ClientFactory,
                 clock: Callable[[], float] = time.monotonic):
        self.world, self.match, self.profile, self.clock = world, match, profile, clock
        self.describer = Describer(world, match.home, match.away)
        self.workflow = build_workflow(world, self.describer, clients)
        self.knowledge = Knowledge()
        self.spoken: list[Line] = []
        self.backlog: list[Proposal] = []
        self.busy_until = 0.0
        self._pending_quiet = False
        self._lock = asyncio.Lock()
        self.on_progress: Callable[[dict], Awaitable[None]] | None = None  # what each agent is doing, for the demo

    # -- triggering ----------------------------------------------------------

    def is_speaking(self) -> bool:
        return self.clock() < self.busy_until

    def first_time(self, update: ReaderUpdate) -> bool:
        """Does this moment involve a rule the viewer has not had explained yet?"""
        return any(depth_for(self.profile, self.knowledge, c, update.event.elapsed) == "full"
                   for c in concepts_for(update.event))

    def should_wake(self, update: ReaderUpdate) -> bool:
        if update.salience >= 3:
            return True
        if update.salience >= 2 and self.first_time(update):
            return True  # a new rule is always explained, queued after whatever is being said
        wants = update.salience >= 2 or bool({s["kind"] for s in update.signals} & SIGNALS_WORTH_WAKING)
        if any(s["kind"] == "quiet_spell" for s in update.signals):
            self._pending_quiet = True
        if self.is_speaking() or self._lock.locked():
            return False
        return wants or self._pending_quiet

    def headline(self, update: ReaderUpdate) -> Line | None:
        ev = update.event
        templates = HEADLINES.get(ev.type)
        if not templates:
            return None
        snap, lang = update.snapshot, self.profile.language
        score = self.spoken_score(update, final=ev.type == EventType.FULL_TIME)
        if self.profile.team and ev.team:
            side = "ours" if ev.team == self.profile.team else "theirs"
            templates = FAN_HEADLINES.get((ev.type, side), templates)
        text = templates[lang].format(team=self.describer.club(ev.team), player=self.describer.player(ev.player),
                                      score=score)
        moment = self._moment(update)
        return Line(text=text, tone=team_tone(moment), card_title=HEADLINE_TITLES[ev.type][lang], headline=True)

    def call(self, update: ReaderUpdate) -> Line | None:
        """A quick play-by-play line for this event, if it has one ("Mwangi shoots… saved!")."""
        ev, lang = update.event, self.profile.language
        variant = {"shot": ev.outcome or "", "var_check": ev.detail.get("reason", ""),
                   "var_decision": ev.detail.get("decision", ""), "kick_off": str(ev.period),
                   "free_kick": "wall" if ev.detail.get("wall") else "none"}.get(ev.type.value, "")
        templates = CALLS.get((ev.type, variant)) or CALLS.get((ev.type, ""))
        if not templates or (ev.type == EventType.KICK_OFF and update.salience < 2):
            return None
        snap = update.snapshot
        surname = lambda pid: (self.describer.player(pid) or "").split(" ")[-1]  # noqa: E731
        defending = snap.away if ev.team == snap.home.id else snap.home
        keeper = next((p for p in defending.players_on_pitch
                       if self.world.club(defending.id).player(p).position == Position.GK), None)
        text = templates[lang].format(
            kits=self.kit_line(), player=surname(ev.player), keeper=surname(keeper),
            team=self.describer.club(ev.team or ev.detail.get("for_team")),
            on=surname(ev.other_player), off=surname(ev.player),
            score=self.spoken_score(update))
        return Line(text=text, tone=team_tone(self._moment(update)), call=True)

    def spoken_score(self, update: ReaderUpdate, final: bool = False) -> str:
        snap = update.snapshot
        phrase = score_phrase(self.profile.language, snap.home.name, snap.home.score, snap.away.name, snap.away.score, final)
        return phrase[0].upper() + phrase[1:]

    def kit_line(self) -> str:
        """Which team wears what: the supporter's own team first, as "we"."""
        lang, home, away = self.profile.language, self.match.home, self.match.away
        ours = self.profile.team if self.profile.team in (home, away) else None
        first, second = (ours, away if ours == home else home) if ours else (home, away)
        template = KIT_LINE_FAN if ours else KIT_LINE
        return template[lang].format(a=self.describer.club(first), ka=KITS[first][lang],
                                     b=self.describer.club(second), kb=KITS[second][lang])

    def instant(self, update: ReaderUpdate) -> Line | None:
        """What to say the instant an event happens: a headline for big moments, otherwise a call."""
        return self.headline(update) or self.call(update)

    def call_only(self, update: ReaderUpdate) -> Commentary | None:
        """For events the agents are not woken for: just the call, if there is one and there is room for it.
        Notable moments (salience 2+) always get their call; minor ones only when Kick-Off is quiet."""
        if update.salience < 2 and self.is_speaking():
            return None
        line = self.call(update)
        if not line:
            return None
        self._speak([line])
        return Commentary(event_id=update.event.id, minute=update.event.minute, elapsed=update.event.elapsed,
                          language=self.profile.language, lines=[line],
                          reasoning="Play-by-play call.", learned=self.learned_labels())

    # -- main entry point ----------------------------------------------------

    async def respond(self, update: ReaderUpdate) -> AsyncIterator[Commentary]:
        """Yield commentary for an update: the agents if it is worth waking them, otherwise maybe a quick call."""
        if self.should_wake(update):
            async for commentary in self.respond_now(update):
                yield commentary
        elif call := self.call_only(update):
            yield call

    async def respond_now(self, update: ReaderUpdate) -> AsyncIterator[Commentary]:
        """Yield commentary without the wake check: an instant line first, then the agents' lines."""
        first = self.instant(update)
        if first:
            self._speak([first])
            yield Commentary(event_id=update.event.id, minute=update.event.minute, elapsed=update.event.elapsed,
                             language=self.profile.language, lines=[first],
                             reasoning="Instant line, before the agents.", learned=self.learned_labels())

        async with self._lock:
            decision = await self._run_agents(update)
        self._apply(decision)
        if decision.lines:
            yield Commentary(
                event_id=update.event.id, minute=update.event.minute, elapsed=update.event.elapsed,
                language=decision.moment.profile.language,
                lines=decision.lines,
                reasoning=decision.reasoning, learned=self.learned_labels(),
                proposals=[{"id": p.id, "agent": p.agent, "priority": p.priority, "text": p.text,
                            "used": any(p.id in line.proposal_ids for line in decision.lines)}
                           for p in decision.proposals],
            )

    async def pre_match(self, snapshot: MatchSnapshot) -> AsyncIterator[Commentary]:
        """The introduction before kick-off: a fixed opening, the agents' starter points (the rules from the
        Teacher, the stakes from the League guide, the build-up from the Storyteller, put in order by the
        Producer), then a fixed closing as the teams walk out."""
        lang = self.profile.language
        event = MatchEvent(id=0, elapsed=0, period=1, clock=0, minute="0", type=EventType.PRE_MATCH)
        update = ReaderUpdate(event=event, salience=2, signals=[], snapshot=snapshot)

        def said(lines: list[Line], reasoning: str, decision: Decision | None = None) -> Commentary:
            self._speak(lines)
            return Commentary(event_id=0, minute="0", elapsed=0, language=lang, lines=lines, reasoning=reasoning,
                              learned=self.learned_labels(),
                              proposals=[{"id": p.id, "agent": p.agent, "priority": p.priority, "text": p.text,
                                          "used": any(p.id in line.proposal_ids for line in decision.lines)}
                                         for p in decision.proposals] if decision else [])

        yield said([Line(text=INTRO_OPEN[lang], tone="hopeful", card_title=INTRO_TITLES[lang], headline=True)],
                   "Fixed opening, while the agents write the starter points.")
        async with self._lock:
            decision = await self._run_agents(update)
        self.backlog = []  # nothing from the introduction is kept for later
        for line in decision.lines:
            for concept in line.concepts:
                self.knowledge.record(concept, 0)
        if decision.lines:
            yield said(decision.lines, decision.reasoning, decision)
        yield said([Line(text=INTRO_CLOSE[lang], tone="excited", card_title=INTRO_TITLES[lang], headline=True)],
                   "Fixed closing: kick-off is next.")

    async def _run_agents(self, update: ReaderUpdate) -> Decision:
        moment = self._moment(update)
        if self._pending_quiet and "quiet_spell" not in moment.signal_kinds:
            moment.signals.append({"kind": "quiet_spell", "seconds": update.snapshot.seconds_since_highlight})
        self._pending_quiet = False
        token = REPORTER.set(self.on_progress)
        try:
            result = await self.workflow.run(moment)
        finally:
            REPORTER.reset(token)
        return result.get_outputs()[-1]

    # -- bookkeeping ---------------------------------------------------------

    def _moment(self, update: ReaderUpdate) -> Moment:
        bridge = next((s for s in update.signals if s["kind"] == "bridge"), None)
        event_text = (f"Play has moved on from minute {bridge['from_minute']} to minute {bridge['to_minute']}."
                      if bridge else self.describer.event(update.event))
        return Moment(
            update=update, profile=self.profile, knowledge=self.knowledge.model_copy(deep=True),
            event_text=event_text, situation=self.describer.situation(update),
            concepts=[] if bridge else concepts_for(update.event), recent_lines=[line.text for line in self.spoken[-6:]],
            backlog=list(self.backlog), signals=list(update.signals),
        )

    def _apply(self, decision: Decision) -> None:
        used_ids = {i for line in decision.lines for i in line.proposal_ids}
        self.backlog = [p for p in self.backlog if p.id not in used_ids]
        self.backlog = (decision.deferred + self.backlog)[:BACKLOG_SIZE]
        known = ASSUMED_KNOWN[self.profile.level]
        for line in decision.lines:
            for concept in line.concepts:
                if "*" not in known and concept not in known:  # nothing is "learned" that they knew already
                    self.knowledge.record(concept, decision.moment.update.event.elapsed)
        self._speak(decision.lines)

    def _speak(self, lines: list[Line]) -> None:
        self.spoken.extend(lines)
        seconds = sum(speech_seconds(line.text, self.profile.language) for line in lines)
        self.busy_until = max(self.busy_until, self.clock()) + seconds

    def learned_labels(self) -> list[str]:
        return [concept_label(c, self.profile.language) for c in self.knowledge.learned_order]
