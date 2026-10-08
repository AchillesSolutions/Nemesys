"""Match reader: turns the raw event stream into one live picture of the match.

This agent is deliberately plain code, not a language model. It is fast,
deterministic and testable, and every other agent reads the snapshot it
produces instead of re-deriving the score or possession from raw events.

For each event it returns a ``ReaderUpdate`` carrying:

* ``salience``: how much the moment matters, so the Producer knows when to
  speak and when to stay quiet (0 routine, 1 chance, 2 teachable stoppage,
  3 big moment);
* ``signals``: derived happenings no single event shows, such as a
  possession swing or a quiet spell that the League guide can fill;
* ``snapshot``: the full state after the event.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from pydantic import BaseModel

from .models import EventType, MatchEvent, World

WINDOW_S = 10 * 60
QUIET_SPELL_S = 150
SWING_THRESHOLD = 62.0

SALIENCE = {
    EventType.GOAL: 3, EventType.GOAL_DISALLOWED: 3, EventType.PENALTY: 3, EventType.RED_CARD: 3,
    EventType.VAR_DECISION: 3, EventType.FULL_TIME: 3,
    EventType.VAR_CHECK: 2, EventType.OFFSIDE: 2, EventType.YELLOW_CARD: 2, EventType.CORNER: 2,
    EventType.SUBSTITUTION: 2, EventType.HALF_TIME: 2, EventType.KICK_OFF: 2,
    EventType.SHOT: 1, EventType.SAVE: 1, EventType.FOUL: 1, EventType.FREE_KICK: 1,
}

# After these, the ball is out of play until the next event: the gap does not count as possession.
DEAD_BALL = {
    EventType.FOUL, EventType.OFFSIDE, EventType.GOAL, EventType.GOAL_DISALLOWED, EventType.YELLOW_CARD,
    EventType.RED_CARD, EventType.SUBSTITUTION, EventType.VAR_CHECK, EventType.VAR_DECISION,
    EventType.PENALTY, EventType.HALF_TIME, EventType.FULL_TIME,
}

# Attacking weight for momentum.
THREAT = {EventType.SHOT: 3, EventType.CORNER: 2, EventType.GOAL: 6, EventType.PENALTY: 4}


class TeamSnapshot(BaseModel):
    id: str
    name: str
    score: int
    possession_pct: float
    possession_last_10_pct: float
    shots: int
    shots_on_target: int
    corners: int
    fouls: int
    offsides: int
    yellow_cards: int
    red_cards: int
    passes: int
    pass_accuracy_pct: float
    players_on_pitch: list[str]
    lineup: list[str | None]  # formation slots in starting order; None where a player was sent off
    booked: list[str]
    sent_off: list[str]


class MatchSnapshot(BaseModel):
    status: str  # pre_match | live | half_time | full_time
    period: int
    minute: str
    elapsed: int
    home: TeamSnapshot
    away: TeamSnapshot
    in_possession: str | None
    ball: tuple[float, float] | None
    momentum: float  # -1 (away on top) .. 1 (home on top), last 10 minutes
    seconds_since_highlight: int


class ReaderUpdate(BaseModel):
    event: MatchEvent
    salience: int
    signals: list[dict]
    snapshot: MatchSnapshot


@dataclass
class _Team:
    id: str
    name: str
    on_pitch: list[str]
    lineup: list[str | None] = field(default_factory=list)
    score: int = 0
    shots: int = 0
    on_target: int = 0
    corners: int = 0
    fouls: int = 0
    offsides: int = 0
    passes: int = 0
    passes_completed: int = 0
    booked: list[str] = field(default_factory=list)
    sent_off: list[str] = field(default_factory=list)


class MatchReader:
    def __init__(self, world: World, home: str, away: str):
        self.home, self.away = home, away
        self.teams = {
            cid: _Team(cid, world.club(cid).short_name, list(world.club(cid).starting_xi), list(world.club(cid).starting_xi))
            for cid in (home, away)
        }
        self.status = "pre_match"
        self.period = 1
        self.minute = "0"
        self.elapsed = 0
        self.in_possession: str | None = None
        self.ball: tuple[float, float] | None = None
        self._ball_live = False
        self._possession_segments: list[tuple[int, int, str]] = []  # (start, end, team)
        self._threats: deque[tuple[int, str, int]] = deque()  # (elapsed, team, weight)
        self._last_highlight = 0
        self._quiet_flagged = False
        self._swing_leader: str | None = None

    # -- public --------------------------------------------------------------

    def apply(self, ev: MatchEvent) -> ReaderUpdate:
        self._credit_possession(ev.elapsed)
        self.elapsed, self.period, self.minute = ev.elapsed, ev.period, ev.minute
        if ev.x is not None and ev.y is not None:
            self.ball = (ev.end_x, ev.end_y) if ev.end_x is not None else (ev.x, ev.y)

        restart_after_goal = ev.type == EventType.KICK_OFF and self.status == "live"
        self._update_counts(ev)
        self._update_possession(ev)

        salience = 0 if restart_after_goal else SALIENCE.get(ev.type, 0)
        signals = self._signals(ev, salience)
        if salience >= 2:
            self._last_highlight = ev.elapsed
            self._quiet_flagged = False
        return ReaderUpdate(event=ev, salience=salience, signals=signals, snapshot=self.snapshot())

    def possession_share(self, window_s: int | None = None) -> dict[str, float]:
        start = self.elapsed - window_s if window_s else 0
        totals = {self.home: 0.0, self.away: 0.0}
        for s, e, team in self._possession_segments:
            overlap = min(e, self.elapsed) - max(s, start)
            if overlap > 0:
                totals[team] += overlap
        total = sum(totals.values())
        if not total:
            return {self.home: 50.0, self.away: 50.0}
        return {t: round(100 * v / total, 1) for t, v in totals.items()}

    def momentum(self, window_s: int = WINDOW_S) -> float:
        cutoff = self.elapsed - window_s
        while self._threats and self._threats[0][0] < cutoff:
            self._threats.popleft()
        home = sum(w for _, t, w in self._threats if t == self.home)
        away = sum(w for _, t, w in self._threats if t == self.away)
        return round((home - away) / (home + away), 2) if home + away else 0.0

    def snapshot(self) -> MatchSnapshot:
        overall, recent = self.possession_share(), self.possession_share(WINDOW_S)

        def team(cid: str) -> TeamSnapshot:
            t = self.teams[cid]
            return TeamSnapshot(
                id=cid, name=t.name, score=t.score,
                possession_pct=overall[cid], possession_last_10_pct=recent[cid],
                shots=t.shots, shots_on_target=t.on_target, corners=t.corners, fouls=t.fouls,
                offsides=t.offsides, yellow_cards=len(t.booked), red_cards=len(t.sent_off),
                passes=t.passes,
                pass_accuracy_pct=round(100 * t.passes_completed / t.passes, 1) if t.passes else 0.0,
                players_on_pitch=list(t.on_pitch), lineup=list(t.lineup), booked=list(t.booked), sent_off=list(t.sent_off),
            )

        return MatchSnapshot(
            status=self.status, period=self.period, minute=self.minute, elapsed=self.elapsed,
            home=team(self.home), away=team(self.away), in_possession=self.in_possession, ball=self.ball,
            momentum=self.momentum(), seconds_since_highlight=self.elapsed - self._last_highlight,
        )

    # -- internals -----------------------------------------------------------

    def _credit_possession(self, now: int) -> None:
        if self._ball_live and self.in_possession and now > self.elapsed:
            segs = self._possession_segments
            if segs and segs[-1][2] == self.in_possession and segs[-1][1] == self.elapsed:
                segs[-1] = (segs[-1][0], now, self.in_possession)
            else:
                segs.append((self.elapsed, now, self.in_possession))

    def _update_counts(self, ev: MatchEvent) -> None:
        t = self.teams.get(ev.team) if ev.team else None
        match ev.type:
            case EventType.KICK_OFF:
                self.status = "live"
            case EventType.HALF_TIME:
                self.status = "half_time"
            case EventType.FULL_TIME:
                self.status = "full_time"
            case EventType.PASS:
                t.passes += 1
                t.passes_completed += ev.outcome == "complete"
            case EventType.SHOT:
                t.shots += 1
                t.on_target += ev.outcome in ("saved", "goal")
            case EventType.CORNER:
                t.corners += 1
            case EventType.FOUL:
                t.fouls += 1
            case EventType.OFFSIDE:
                t.offsides += 1
            case EventType.YELLOW_CARD:
                t.booked.append(ev.player)
            case EventType.RED_CARD:
                t.sent_off.append(ev.player)
                t.on_pitch.remove(ev.player)
                t.lineup = [None if p == ev.player else p for p in t.lineup]
            case EventType.SUBSTITUTION:
                t.on_pitch = [ev.other_player if p == ev.player else p for p in t.on_pitch]
                t.lineup = [ev.other_player if p == ev.player else p for p in t.lineup]
            case EventType.GOAL | EventType.GOAL_DISALLOWED:
                for cid, goals in ev.detail["score"].items():
                    self.teams[cid].score = goals
        if t and ev.type in THREAT:
            self._threats.append((ev.elapsed, ev.team, THREAT[ev.type]))
        if t and ev.type == EventType.PASS and ev.outcome == "complete" and self._attacking_x(ev.team, ev.end_x) > 66:
            self._threats.append((ev.elapsed, ev.team, 1))

    def _update_possession(self, ev: MatchEvent) -> None:
        if ev.type == EventType.FOUL or ev.type == EventType.OFFSIDE:
            self.in_possession = self._other(ev.team)
        elif ev.type in (EventType.PASS, EventType.SHOT) and ev.outcome in ("complete", "saved", "off_target"):
            self.in_possession = ev.team
        elif ev.team and ev.type not in DEAD_BALL and ev.type != EventType.PASS:
            self.in_possession = ev.team
        self._ball_live = ev.type not in DEAD_BALL and not (ev.type == EventType.PASS and ev.outcome in ("out", "offside"))

    def _attacking_x(self, team: str, x: float | None) -> float:
        if x is None:
            return 0
        return x if team == self.home else 100 - x

    def _signals(self, ev: MatchEvent, salience: int) -> list[dict]:
        signals = []
        if (self.status == "live" and not self._quiet_flagged
                and ev.elapsed - self._last_highlight >= QUIET_SPELL_S and salience < 2):
            self._quiet_flagged = True
            signals.append({"kind": "quiet_spell", "seconds": ev.elapsed - self._last_highlight})

        if ev.elapsed >= WINDOW_S:
            recent = self.possession_share(WINDOW_S)
            leader = max(recent, key=recent.get)
            if recent[leader] >= SWING_THRESHOLD and leader != self._swing_leader:
                self._swing_leader = leader
                signals.append({"kind": "possession_dominance", "team": leader, "pct": recent[leader],
                                "window_minutes": WINDOW_S // 60})
            elif recent[leader] < 55:
                self._swing_leader = None

        if ev.type == EventType.RED_CARD:
            signals.append({"kind": "numerical_advantage", "team": self._other(ev.team),
                            "players": {cid: len(t.on_pitch) for cid, t in self.teams.items()}})
        if ev.type == EventType.GOAL:
            scored, conceded = self.teams[ev.team].score, self.teams[self._other(ev.team)].score
            kind = "equaliser" if scored == conceded else "takes_the_lead" if scored == conceded + 1 else "extends_lead"
            signals.append({"kind": kind, "team": ev.team, "score": dict(ev.detail["score"])})
        return signals

    def _other(self, team: str) -> str:
        return self.away if team == self.home else self.home
