"""Synthetic match generator.

Two layers produce the event stream:

* Open play is simulated from each club's ``Style``: tempo, pass accuracy,
  directness and counter-attack threat decide how long chains last and how
  the ball moves. A patient side really does keep more of the ball, and a
  counter-attacking side really does break fast after winning it. Open play
  creates shots, saves, corners, fouls and offsides, but never goals.
* Story beats are planted at fixed minutes. They carry the scoreline and
  the teaching moments a first-time viewer should see: an offside, a free
  kick with a wall, a VAR penalty, a tactical substitution, a second yellow,
  a goal ruled out by VAR and a late comeback.

Everything is driven by a seeded RNG, so the same seed gives the same match.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass

from .models import Club, EventType, Match, MatchEvent, Position, World
from .positions import ROLES, Formation, metres, mirror
from .world import AWAY_ID, HOME_ID

HALF = 45 * 60
FIRST_HALF_ADDED = 2 * 60
SECOND_HALF_ADDED = 4 * 60
DEFAULT_SEED = 2026

# Team-relative state: (team in possession, x, y) in that team's attacking frame,
# where x = 100 is the opponent's goal line.
State = tuple[str, float, float]


def _clamp(v: float, lo: float = 2, hi: float = 98) -> float:
    return max(lo, min(hi, v))


def _display_minute(period: int, clock: int) -> str:
    end = HALF * period
    if clock >= end:
        return f"{end // 60}+{(clock - end) // 60 + 1}"
    return str(clock // 60 + 1)


@dataclass
class _Beat:
    period: int
    clock: int  # match clock in seconds
    run: Callable[["_Sim", State], State]


class _Sim:
    def __init__(self, world: World, home: str, away: str, seed: int):
        self.rng = random.Random(seed)
        self.clubs: dict[str, Club] = {home: world.club(home), away: world.club(away)}
        self.home, self.away = home, away
        self.on_pitch = {cid: list(c.starting_xi) for cid, c in self.clubs.items()}
        self.score = {home: 0, away: 0}
        self.events: list[MatchEvent] = []
        self.elapsed = 0
        self.period = 1
        self.period_start = 0
        self.base_clock = 0
        self.formation = Formation(self.on_pitch, home, random.Random(seed + 1))
        self._moved_at = 0
        self._ball: tuple[float, float] = (50, 50)  # pitch coordinates
        self._possession: str | None = home
        self._held_line: tuple[str, float] | None = None  # (team, x in its own frame): a back line held for a picture

    # -- helpers -----------------------------------------------------------

    def opp(self, team: str) -> str:
        return self.away if team == self.home else self.home

    @property
    def clock(self) -> int:
        return self.base_clock + self.elapsed - self.period_start

    def until(self, period: int, clock: int) -> int:
        assert period == self.period, "beats must be scheduled in the current period"
        return self.period_start + clock - self.base_clock

    def tick(self, seconds: float) -> None:
        self.elapsed += max(1, round(seconds))

    def _to_pitch(self, team: str | None, x: float | None, y: float | None) -> tuple[float | None, float | None]:
        if x is None or y is None:
            return x, y
        if team == self.away:
            x, y = 100 - x, 100 - y
        return round(x, 1), round(y, 1)

    def emit(self, type_: EventType, team: str | None = None, player: str | None = None,
             at: tuple[float, float] | None = None, to: tuple[float, float] | None = None,
             **fields) -> MatchEvent:
        """Record an event. ``at``/``to`` are in ``team``'s attacking frame."""
        x, y = self._to_pitch(team, *(at or (None, None)))
        end_x, end_y = self._to_pitch(team, *(to or (None, None)))
        self._move_players(type_, team, player, at, to, fields)
        ev = MatchEvent(
            id=len(self.events) + 1, elapsed=self.elapsed, period=self.period,
            clock=self.clock, minute=_display_minute(self.period, self.clock),
            type=type_, team=team, player=player, x=x, y=y, end_x=end_x, end_y=end_y,
            frame=self.formation.frame(), **fields,
        )
        self.events.append(ev)
        return ev

    def _move_players(self, type_: EventType, team: str | None, player: str | None,
                      at: tuple[float, float] | None, to: tuple[float, float] | None, fields: dict) -> None:
        """Advance every player to this moment, then put the players involved where the event says."""
        f = self.formation
        other = fields.get("other_player")
        if type_ == EventType.SUBSTITUTION:
            f.substitute(team, player, other)
        elif type_ == EventType.RED_CARD:
            f.send_off(team, player)
        elif type_ == EventType.KICK_OFF:
            f.kick_off_shape()

        if team and (to or at):
            self._ball = self._to_pitch(team, *(to or at))
        if team:
            self._possession = self.opp(team) if type_ in (EventType.FOUL, EventType.OFFSIDE) else team

        pinned: dict[str, tuple[float, float]] = {}
        if team and player and at and type_ not in (EventType.SUBSTITUTION, EventType.RED_CARD):
            pinned[player] = at
        if type_ == EventType.PASS and other and to and fields.get("outcome") in ("complete", "offside"):
            pinned[other] = to
        if type_ in (EventType.FOUL, EventType.TACKLE) and other and at:
            pinned[other] = (100 - at[0] + 1.2, 100 - at[1])  # the opponent involved, just beside the ball
        # Each pinned point is in its own player's team frame (opponents were mirrored above).
        f.advance(self.elapsed - self._moved_at, self._possession, self._ball, pinned)
        if self._held_line:
            f.set_back_line(*self._held_line)
        self._moved_at = self.elapsed

    def hold_line(self, team: str, x: float | None) -> None:
        """Hold ``team``'s back four on one line (its own frame) for the next events, or release it."""
        self._held_line = (team, x) if x is not None else None

    def pitch_x(self, team: str, x: float) -> float:
        """An x in ``team``'s frame, as a pitch x."""
        return round(x if team == self.home else 100 - x, 1)

    def holder_near(self, team: str, p: tuple[float, float]) -> str:
        return self.formation.nearest(team, p, outfield=False)

    def pick(self, team: str, x: float, exclude: str | None = None) -> str:
        """Pick a plausible player for a spot on the pitch (team frame)."""
        if x < 33:
            weights = {Position.GK: 1 if x < 15 else 0, Position.DF: 4, Position.MF: 2, Position.FW: 0.2}
        elif x < 66:
            weights = {Position.GK: 0, Position.DF: 1.5, Position.MF: 4, Position.FW: 1}
        else:
            weights = {Position.GK: 0, Position.DF: 0.3, Position.MF: 2.5, Position.FW: 4}
        club = self.clubs[team]
        options = [p for p in self.on_pitch[team] if p != exclude]
        w = [weights[club.player(p).position] for p in options]
        return self.rng.choices(options, weights=w)[0]

    def keeper(self, team: str) -> str:
        club = self.clubs[team]
        return next(p for p in self.on_pitch[team] if club.player(p).position == Position.GK)

    def passes(self, team: str, chain: list[tuple[str, float, float]], tempo: float = 3.0) -> None:
        """Emit completed passes along a scripted chain of (player, x, y)."""
        for (p1, x1, y1), (p2, x2, y2) in zip(chain, chain[1:]):
            self.tick(tempo)
            self.emit(EventType.PASS, team, p1, at=(x1, y1), to=(x2, y2), other_player=p2, outcome="complete")

    def score_detail(self) -> dict:
        return {"score": dict(self.score)}

    def goal(self, team: str, scorer: str, at: tuple[float, float], **detail) -> State:
        self.tick(2)
        self.emit(EventType.SHOT, team, scorer, at=at, to=(100, 50), outcome="goal",
                  detail={k: v for k, v in detail.items() if k in ("body_part", "from")})
        self.score[team] += 1
        self.emit(EventType.GOAL, team, scorer, at=at, detail={**detail, **self.score_detail()})
        self.tick(60)
        conceding = self.opp(team)
        self.emit(EventType.KICK_OFF, conceding, self.pick(conceding, 50), at=(50, 50))
        return conceding, 50, 50

    # -- open play ---------------------------------------------------------

    def open_play(self, state: State, until: int) -> State:
        """Simulate style-driven possession chains until ``until`` elapsed seconds.

        Every pass goes to where a teammate actually is; the receiver is chosen by the club's
        directness, and a pass is more likely to be cut out when an opponent is close to its target.
        """
        f = self.formation
        team, x, y = state
        holder = self.holder_near(team, (x, y))
        counter = False
        while self.elapsed < until:
            style = self.clubs[team].style
            opp = self.opp(team)
            hx, hy = f.at(team, holder)

            if hx > 76 and self.rng.random() < style.shot_appetite * (1.6 if counter else 1):
                team, x, y = self._shot(team, holder, hx, hy)
                holder, counter = self.holder_near(team, (x, y)), False
                continue

            tempo = style.pass_tempo_s * (0.55 if counter else 1)
            dt = max(1.5, self.rng.gauss(tempo, tempo * 0.3))
            # Carry the ball into space before passing, if there is any.
            if f.distance_to_nearest(opp, mirror((hx, hy))) > 8 and hx < 88:
                hx = min(hx + dt * 3.0 * self.rng.uniform(0, 1) * 100 / 105, 90)
                f.place(team, holder, (hx, hy))
            self.tick(dt)
            receiver, (tx, ty) = self._choose_pass(team, holder, (hx, hy), counter)
            slot = f.lineup[team].index(receiver)

            if ROLES[slot] == "FW" and tx > 78 and tx - hx > 8 and self.rng.random() < 0.012:
                line = tx - self.rng.uniform(1.5, 4)
                self.hold_line(opp, 100 - line)
                line_x = self.pitch_x(team, line)
                self.emit(EventType.PASS, team, holder, at=(hx, hy), to=(tx, ty), other_player=receiver,
                          outcome="offside", detail={"offside_line_x": line_x})
                self.emit(EventType.OFFSIDE, team, receiver, at=(tx, ty),
                          detail={"passer": holder, "offside_line_x": line_x})
                self.hold_line(opp, None)
                self.tick(20)
                spot = mirror((tx, ty))
                taker = f.nearest(opp, spot)
                self.emit(EventType.FREE_KICK, opp, taker, at=spot, detail={"reason": "offside"})
                team, holder, counter = opp, taker, False
                continue

            distance = metres((hx, hy), (tx, ty))
            space = f.distance_to_nearest(opp, mirror((tx, ty)))
            success = style.pass_accuracy + 0.05 - max(0.0, 6 - space) * 0.035 - max(0.0, distance - 25) * 0.004
            if self.rng.random() < success:
                self.emit(EventType.PASS, team, holder, at=(hx, hy), to=(tx, ty), other_player=receiver,
                          outcome="complete")
                holder = receiver
                continue

            # Possession lost, to whoever is actually nearest.
            r = self.rng.random()
            winner, cut, reach = self._interceptor(opp, (hx, hy), (tx, ty))
            if r < 0.5 and reach <= 7:
                self.emit(EventType.PASS, team, holder, at=(hx, hy), to=(tx, ty), other_player=receiver,
                          outcome="intercepted")
                self.tick(1)
                self.emit(EventType.INTERCEPTION, opp, winner, at=mirror(cut))
                new_x = 100 - cut[0]
            elif r < 0.5:
                # Nobody can reach the pass: it arrives, and the receiver is closed down and tackled.
                self.emit(EventType.PASS, team, holder, at=(hx, hy), to=(tx, ty), other_player=receiver,
                          outcome="complete")
                self.tick(1.5)
                winner = f.nearest(opp, mirror((tx, ty)))
                self.emit(EventType.TACKLE, opp, winner, at=mirror((tx, ty)), other_player=receiver, outcome="won")
                new_x = 100 - tx
            elif r < 0.75:
                winner = f.nearest(opp, mirror((hx, hy)))
                self.emit(EventType.TACKLE, opp, winner, at=mirror((hx, hy)), other_player=holder, outcome="won")
                new_x = 100 - hx
            elif r < 0.9:
                out = (tx, 1.0 if ty < 50 else 99.0)
                self.emit(EventType.PASS, team, holder, at=(hx, hy), to=out, other_player=receiver, outcome="out")
                self.tick(12)
                spot = mirror(out)
                winner = f.nearest(opp, spot)
                self.emit(EventType.THROW_IN, opp, winner, at=spot)
                new_x = spot[0]
            else:
                fouler = f.nearest(opp, mirror((hx, hy)))
                self.emit(EventType.FOUL, opp, fouler, at=mirror((hx, hy)), other_player=holder)
                self.tick(18)
                self.emit(EventType.FREE_KICK, team, holder, at=(hx, hy), detail={"reason": "foul"})
                continue

            # A turnover deep in your own half is the trigger for a counter-attack.
            counter = new_x < 45 and self.rng.random() < self.clubs[opp].style.counter_threat
            team, holder = opp, winner
        x, y = f.at(team, holder)
        return team, x, y

    def _interceptor(self, opp: str, a: tuple[float, float], b: tuple[float, float]):
        """The opponent best placed to cut out a pass from a to b: who, where on its path, and how far they must go."""
        f = self.formation
        best = None
        for player in f.pos[opp]:
            q = mirror(f.at(opp, player))  # into the passing team's frame
            ax, ay, bx, by = *a, *b
            seg = (bx - ax) ** 2 + (by - ay) ** 2 or 1
            k = max(0.3, min(1.0, ((q[0] - ax) * (bx - ax) + (q[1] - ay) * (by - ay)) / seg))
            point = (ax + (bx - ax) * k, ay + (by - ay) * k)
            d = metres(q, point)
            if best is None or d < best[0]:
                best = (d, player, point)
        return best[1], best[2], best[0]

    def _choose_pass(self, team: str, holder: str, at: tuple[float, float], counter: bool):
        """Pick a teammate to pass to: direct sides look forward, everyone prefers 10-30 metres."""
        f = self.formation
        beta = self.clubs[team].style.directness / 60 * (2 if counter else 1)
        options = []
        for slot, player in enumerate(f.lineup[team]):
            if player in (None, holder) or (ROLES[slot] == "GK" and at[0] > 35):
                continue
            q = f.at(team, player)
            d = metres(at, q)
            if d < 5:
                continue
            weight = math.exp(beta * (q[0] - at[0])) * math.exp(-(((d - 17) / 13) ** 2)) + 1e-6
            options.append((weight, player, q))
        _, player, q = self.rng.choices(options, weights=[o[0] for o in options])[0]
        lead = self.rng.uniform(0, 4) if q[0] > at[0] else 0  # pass into the runner's path
        return player, (_clamp(q[0] + lead), _clamp(q[1] + self.rng.uniform(-2, 2)))

    def _shot(self, team: str, shooter: str, x: float, y: float) -> State:
        opp = self.opp(team)
        self.tick(2)
        r = self.rng.random()
        outcome = "saved" if r < 0.3 else "off_target" if r < 0.7 else "blocked"
        self.emit(EventType.SHOT, team, shooter, at=(x, y), to=(100, _clamp(50 + self.rng.gauss(0, 12))), outcome=outcome)
        if outcome == "saved":
            self.emit(EventType.SAVE, opp, self.keeper(opp), at=(2, 50))
            if self.rng.random() < 0.4:
                return self._corner(team)
            self.tick(6)
            return opp, 6, 50
        if outcome == "off_target":
            self.tick(25)
            self.emit(EventType.GOAL_KICK, opp, self.keeper(opp), at=(6, 50))
            return opp, 6, 50
        return opp, 100 - x, 100 - y

    def _corner(self, team: str) -> State:
        opp = self.opp(team)
        self.tick(25)
        side = self.rng.choice((2, 98))
        self.emit(EventType.CORNER, team, self.pick(team, 70), at=(99, side))
        self.tick(4)
        if self.rng.random() < 0.5:
            header = self.pick(team, 90)
            self.emit(EventType.SHOT, team, header, at=(92, 50), to=(100, 40), outcome="off_target", detail={"body_part": "head"})
            self.tick(25)
            self.emit(EventType.GOAL_KICK, opp, self.keeper(opp), at=(6, 50))
            return opp, 6, 50
        defender = self.pick(opp, 8)
        self.emit(EventType.INTERCEPTION, opp, defender, at=(8, 50), detail={"clearance": True})
        return opp, 25, 50


# -- story beats -------------------------------------------------------------
# Each beat takes the current state and returns the state after it.
# Coordinates are in the acting team's attacking frame.

def _kick_off(s: _Sim, _: State) -> State:
    s.emit(EventType.KICK_OFF, s.home, "kil-9", at=(50, 50))
    return s.home, 50, 50


def _offside(team: str, builder: str, passer: str, runner: str) -> Callable[[_Sim, State], State]:
    """A ball over the top to a runner who has left too early."""
    def run(s: _Sim, _: State) -> State:
        s.passes(team, [(builder, 30, 50), (passer, 52, 45)], tempo=3)
        s.tick(2)
        # The back line steps up just as the pass is played; the runner has already gone beyond it.
        opp = s.opp(team)
        s.hold_line(opp, 100 - 81)
        line = {"offside_line_x": s.pitch_x(team, 81)}
        s.emit(EventType.PASS, team, passer, at=(52, 45), to=(84, 40), other_player=runner, outcome="offside",
               detail=line)
        s.emit(EventType.OFFSIDE, team, runner, at=(84, 40),
               detail={"passer": passer, "flagged_by": "assistant referee", **line})
        s.hold_line(opp, None)
        s.tick(20)
        s.emit(EventType.FREE_KICK, opp, s.keeper(opp), at=(16, 60), detail={"reason": "offside"})
        return opp, 16, 60
    return run


def _free_kick_wall(s: _Sim, _: State) -> State:
    s.passes("kil", [("kil-6", 55, 50), ("kil-10", 77, 55)], tempo=4)
    s.tick(2)
    s.emit(EventType.FOUL, "hbr", "hbr-5", at=(23, 45), other_player="kil-10")
    s.tick(45)
    s.emit(EventType.FREE_KICK, "kil", "kil-10", at=(77, 55),
           detail={"reason": "foul", "direct": True, "wall": True, "wall_distance_yards": 10, "distance_to_goal_m": 24})
    s.tick(3)
    s.emit(EventType.SHOT, "kil", "kil-10", at=(77, 55), to=(100, 46), outcome="saved", detail={"from": "free kick"})
    s.emit(EventType.SAVE, "hbr", "hbr-1", at=(2, 54), detail={"tipped_over": True})
    s.tick(25)
    s.emit(EventType.CORNER, "kil", "kil-10", at=(99, 98))
    s.tick(4)
    s.emit(EventType.INTERCEPTION, "hbr", "hbr-6", at=(8, 50), detail={"clearance": True})
    return "hbr", 25, 50


def _first_yellow(s: _Sim, _: State) -> State:
    s.emit(EventType.FOUL, "hbr", "hbr-4", at=(52, 60), other_player="kil-6", detail={"severity": "reckless"})
    s.tick(30)
    s.emit(EventType.YELLOW_CARD, "hbr", "hbr-4", at=(52, 60), detail={"reason": "reckless challenge", "booking_number": 1})
    s.tick(10)
    s.emit(EventType.FREE_KICK, "kil", "kil-6", at=(48, 40), detail={"reason": "foul"})
    return "kil", 48, 40


def _corner_header(s: _Sim, _: State) -> State:
    s.passes("kil", [("kil-3", 70, 85), ("kil-7", 88, 80)], tempo=4)
    s.tick(2)
    s.emit(EventType.SHOT, "kil", "kil-7", at=(88, 80), to=(100, 45), outcome="blocked")
    s.tick(20)
    s.emit(EventType.CORNER, "kil", "kil-10", at=(99, 98), detail={"deflected_by": "hbr-2"})
    s.tick(4)
    s.emit(EventType.SHOT, "kil", "kil-4", at=(93, 48), to=(100, 50), outcome="off_target", detail={"body_part": "head"})
    s.tick(25)
    s.emit(EventType.GOAL_KICK, "hbr", "hbr-1", at=(6, 50))
    return "hbr", 6, 50


def _counter_goal(passer_lost: str, chain: list[tuple[str, float, float]], scorer_at: tuple[float, float],
                  assist: str) -> Callable[[_Sim, State], State]:
    def run(s: _Sim, _: State) -> State:
        team = chain[0][0].split("-")[0]
        opp = s.opp(team)
        start = s.elapsed
        rx, ry = chain[0][1], chain[0][2]
        s.emit(EventType.PASS, opp, passer_lost, at=(100 - rx - 5, 100 - ry), to=(100 - rx + 5, 100 - ry), outcome="intercepted")
        s.emit(EventType.INTERCEPTION, team, chain[0][0], at=(rx, ry), other_player=passer_lost)
        s.passes(team, chain, tempo=2.2)
        scorer = chain[-1][0]
        return s.goal(team, scorer, scorer_at, assist=assist, pattern="counter_attack",
                      passes=len(chain) - 1, seconds_from_recovery=s.elapsed - start + 2)
    return run


def _half_time(s: _Sim, _: State) -> State:
    s.emit(EventType.HALF_TIME, detail=s.score_detail())
    s.period, s.period_start, s.base_clock = 2, s.elapsed, HALF
    s.emit(EventType.KICK_OFF, s.away, "hbr-9", at=(50, 50))
    return s.away, 50, 50


def _var_penalty(s: _Sim, _: State) -> State:
    s.passes("kil", [("kil-6", 60, 50), ("kil-10", 75, 40), ("kil-7", 88, 30)], tempo=3.5)
    s.tick(2)
    tackle = s.emit(EventType.TACKLE, "hbr", "hbr-3", at=(12, 70), other_player="kil-7", outcome="won",
                    detail={"in_penalty_area": True, "referee_signal": "play on"})
    s.tick(12)
    s.emit(EventType.VAR_CHECK, detail={"reason": "possible penalty", "for_team": "kil", "incident_event_id": tackle.id,
                                        "incident": "Pike's tackle on Quintero in the penalty area"})
    s.tick(75)
    s.emit(EventType.VAR_DECISION, detail={
        "decision": "penalty", "for_team": "kil", "on_field_review": True, "overturned": True,
        "explanation": "Replays show Pike caught Quintero's ankle before reaching the ball.",
    })
    s.tick(60)
    s.emit(EventType.PENALTY, "kil", "kil-9", at=(89, 50), other_player="hbr-3", detail={"fouled": "kil-7"})
    s.tick(5)
    return s.goal("kil", "kil-9", (89, 50), assist=None, pattern="penalty", **{"from": "penalty"})


def _substitution(team: str, off: str, on: str, note: str) -> Callable[[_Sim, State], State]:
    def run(s: _Sim, state: State) -> State:
        s.tick(15)
        s.emit(EventType.SUBSTITUTION, team, off, other_player=on, detail={"reason": "tactical", "note": note})
        s.on_pitch[team] = [on if p == off else p for p in s.on_pitch[team]]
        s.tick(15)
        return state
    return run


def _second_yellow(s: _Sim, _: State) -> State:
    s.emit(EventType.FOUL, "hbr", "hbr-4", at=(40, 25), other_player="kil-17", detail={"severity": "cynical",
                                                                                      "stopped_attack": True})
    s.tick(30)
    s.emit(EventType.YELLOW_CARD, "hbr", "hbr-4", at=(40, 25), detail={"reason": "stopping a promising attack",
                                                                     "booking_number": 2})
    s.emit(EventType.RED_CARD, "hbr", "hbr-4", at=(40, 25), detail={"reason": "second yellow card"})
    s.on_pitch["hbr"].remove("hbr-4")
    s.tick(40)
    s.emit(EventType.FREE_KICK, "kil", "kil-17", at=(60, 75), detail={"reason": "foul"})
    return "kil", 60, 75


def _equaliser(s: _Sim, _: State) -> State:
    chain = [("kil-4", 40, 50), ("kil-6", 55, 45), ("kil-8", 68, 55), ("kil-10", 75, 40),
             ("kil-3", 82, 88), ("kil-6", 78, 60), ("kil-17", 88, 85)]
    s.passes("kil", chain, tempo=4.5)
    s.tick(3)
    s.emit(EventType.PASS, "kil", "kil-17", at=(88, 85), to=(93, 50), other_player="kil-9",
           outcome="complete", detail={"cross": True})
    return s.goal("kil", "kil-9", (93, 50), assist="kil-17", pattern="sustained_pressure",
                  passes=len(chain), body_part="head")


def _disallowed_goal(s: _Sim, _: State) -> State:
    s.passes("kil", [("kil-8", 60, 50), ("kil-10", 72, 48)], tempo=3.5)
    s.tick(2)
    # Harbour City's line holds at 88.5; Whitcombe is a shoulder beyond it when the pass is played.
    s.hold_line("hbr", 100 - 88.5)
    offside = {"offside_line_x": s.pitch_x("kil", 88.5), "offside_player": "kil-9",
               "offside_player_at": [s.pitch_x("kil", 90), 45]}
    s.emit(EventType.PASS, "kil", "kil-10", at=(72, 48), to=(90, 45), other_player="kil-9", outcome="complete",
           detail={"through_ball": True})
    s.hold_line("hbr", None)
    s.tick(2)
    s.emit(EventType.SHOT, "kil", "kil-9", at=(90, 45), to=(100, 52), outcome="goal")
    s.score["kil"] += 1
    goal = s.emit(EventType.GOAL, "kil", "kil-9", at=(90, 45), detail={"assist": "kil-10", **s.score_detail()})
    s.tick(35)
    s.emit(EventType.VAR_CHECK, detail={"reason": "possible offside", "for_team": "kil", "incident_event_id": goal.id,
                                        "incident": "Whitcombe's position when Andersen played the pass"})
    s.tick(80)
    s.emit(EventType.VAR_DECISION, detail={
        "decision": "no goal", "for_team": "kil", "on_field_review": False, "overturned": True,
        "explanation": "Whitcombe's shoulder was beyond the last defender when the pass was played.",
        **offside,
    })
    s.score["kil"] -= 1
    s.emit(EventType.GOAL_DISALLOWED, "kil", "kil-9", detail={"reason": "offside", "goal_event_id": goal.id,
                                                              **offside, **s.score_detail()})
    s.tick(30)
    s.emit(EventType.FREE_KICK, "hbr", "hbr-1", at=(10, 55), detail={"reason": "offside"})
    return "hbr", 10, 55


def _late_winner(s: _Sim, _: State) -> State:
    s.passes("kil", [("kil-6", 65, 50), ("kil-17", 85, 80)], tempo=3.5)
    s.tick(2)
    s.emit(EventType.SHOT, "kil", "kil-17", at=(85, 80), to=(100, 44), outcome="saved")
    s.emit(EventType.SAVE, "hbr", "hbr-1", at=(2, 56), detail={"tipped_wide": True})
    s.tick(30)
    s.emit(EventType.CORNER, "kil", "kil-10", at=(99, 2))
    s.tick(4)
    return s.goal("kil", "kil-4", (94, 46), assist="kil-10", pattern="corner", body_part="head")


def _full_time(s: _Sim, _: State) -> State:
    s.emit(EventType.FULL_TIME, detail=s.score_detail())
    return _


def _m(minute: int, second: int = 0) -> int:
    return minute * 60 + second


STORY: list[_Beat] = [
    _Beat(1, 0, _kick_off),
    _Beat(1, _m(7, 40), _offside("hbr", "hbr-5", "hbr-10", "hbr-9")),
    _Beat(1, _m(15, 20), _free_kick_wall),
    _Beat(1, _m(21, 30), _first_yellow),
    _Beat(1, _m(28, 10), _corner_header),
    _Beat(1, _m(33, 30), _counter_goal(
        "kil-8", [("hbr-8", 30, 55), ("hbr-10", 48, 50), ("hbr-7", 70, 80), ("hbr-9", 88, 55)],
        (88, 55), assist="hbr-7")),
    _Beat(1, _m(37, 45), _offside("kil", "kil-5", "kil-8", "kil-7")),
    # Two down at the break: the rival is unbearable, and the comeback has somewhere to come back from.
    _Beat(1, _m(41, 50), _counter_goal(
        "kil-5", [("hbr-8", 35, 40), ("hbr-10", 55, 45), ("hbr-7", 86, 62)], (86, 62), assist="hbr-10")),
    _Beat(1, HALF + FIRST_HALF_ADDED - 10, _half_time),
    _Beat(2, _m(52, 40), _var_penalty),
    _Beat(2, _m(48, 5), _offside("hbr", "hbr-6", "hbr-8", "hbr-9")),
    _Beat(2, _m(60, 30), _substitution("hbr", "hbr-11", "hbr-15",
                                       "A defensive midfielder to sit in front of the defence and spring counters")),
    _Beat(2, _m(61, 10), _substitution("kil", "kil-11", "kil-17",
                                       "Fresh pace on the wing against a tiring full-back")),
    _Beat(2, _m(73, 50), _second_yellow),
    _Beat(2, _m(81, 40), _equaliser),
    _Beat(2, _m(87, 30), _disallowed_goal),
    _Beat(2, 2 * HALF + _m(2, 30), _late_winner),
    _Beat(2, 2 * HALF + SECOND_HALF_ADDED - 5, _full_time),
]


def generate_match(world: World, home: str = HOME_ID, away: str = AWAY_ID, seed: int = DEFAULT_SEED) -> Match:
    sim = _Sim(world, home, away, seed)
    state: State = (home, 50, 50)
    for beat in STORY:
        if sim.events:
            state = sim.open_play(state, sim.until(beat.period, beat.clock))
        state = beat.run(sim, state)
    home_club = world.club(home)
    return Match(id=f"mw{world.matchweek}-{home}-{away}", home=home, away=away, venue=home_club.stadium,
                 matchweek=world.matchweek, seed=seed, events=sim.events)
