"""Where every player is, all match long.

The generator moves 22 players with a team-shape model so the event data
is spatially consistent: passes go to where a teammate actually is,
interceptions are made by the nearest opponent, and an offside runner
really is beyond the second-last defender.

Each team plays a 4-3-3 arranged in three lines (defence, midfield,
attack). The lines slide up and down the pitch with the ball: the team in
possession pushes up and spreads wide, the team without it drops back
and narrows towards the ball. Players run towards their spot at a
realistic top speed, so nobody teleports.

Positions are held in each team's attacking frame (x = 100 is the
opponent's goal line) and converted to pitch coordinates (home attacking
towards x = 100) for output.
"""

from __future__ import annotations

import math
import random

ROLES = ["GK", "DF", "DF", "DF", "DF", "MF", "MF", "MF", "FW", "FW", "FW"]
BASE_Y = [50, 14, 38, 62, 86, 30, 50, 70, 16, 50, 84]
FULL_BACKS = {1, 4}
RUN_SPEED_M_S = 7.0
PITCH_M = (105.0, 68.0)

Pt = tuple[float, float]


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def metres(a: Pt, b: Pt) -> float:
    return math.hypot((a[0] - b[0]) * PITCH_M[0] / 100, (a[1] - b[1]) * PITCH_M[1] / 100)


def mirror(p: Pt) -> Pt:
    """Convert a point between the two teams' attacking frames."""
    return 100 - p[0], 100 - p[1]


class Formation:
    def __init__(self, lineups: dict[str, list[str]], home: str, rng: random.Random):
        self.home = home
        self.lineup: dict[str, list[str | None]] = {t: list(xi) for t, xi in lineups.items()}
        # A fixed personal offset per player, so lines are never ruler-straight.
        self.offset = {p: (rng.uniform(-2, 2), rng.uniform(-2.5, 2.5)) for xi in lineups.values() for p in xi}
        self.pos: dict[str, dict[str, Pt]] = {t: {} for t in lineups}
        self.kick_off_shape()

    # -- shape -------------------------------------------------------------------

    def target(self, team: str, slot: int, player: str, attacking: bool, ball: Pt) -> Pt:
        """Where this player wants to be, given the ball (in this team's frame)."""
        bx, by = ball
        role = ROLES[slot]
        if role == "GK":
            x = _clamp(4 + max(0.0, bx - 50) * 0.12, 3, 16) if attacking else 4.0
            return x, 50 + (by - 50) * 0.15
        if attacking:
            lines = {"DF": _clamp(bx - 30, 14, 58), "MF": _clamp(bx - 10, 28, 76), "FW": _clamp(bx + 12, 45, 90)}
            width, pull = 1.0, 0.22
        else:
            df = _clamp(bx * 0.72 - 2, 7, 42)
            lines = {"DF": df, "MF": df + 13, "FW": min(df + 28, 72)}
            width, pull = 0.72, 0.38
        ox, oy = self.offset.get(player, (0, 0))
        x = lines[role] + ox + (8 if attacking and slot in FULL_BACKS else 0)
        y = 50 + (BASE_Y[slot] - 50) * width + (by - 50) * pull + oy
        return _clamp(x, 3, 97), _clamp(y, 4, 96)

    def advance(self, dt: float, possession: str | None, ball_pitch: Pt, pinned: dict[str, Pt] | None = None) -> None:
        """Move everyone towards their spot for ``dt`` seconds at running speed."""
        pinned = pinned or {}
        reach = RUN_SPEED_M_S * max(dt, 0)
        for team, players in self.pos.items():
            ball = ball_pitch if team == self.home else mirror(ball_pitch)
            for slot, player in enumerate(self.lineup[team]):
                if player is None or player in pinned:
                    continue
                goal = self.target(team, slot, player, possession == team, ball)
                here = players.get(player, goal)
                dist = metres(here, goal)
                if dist <= reach or dist == 0:
                    players[player] = goal
                else:
                    f = reach / dist
                    players[player] = (here[0] + (goal[0] - here[0]) * f, here[1] + (goal[1] - here[1]) * f)
        for player, p in pinned.items():
            self.place(self.team_of(player), player, p)

    def kick_off_shape(self) -> None:
        """Both teams in their own half, as the rules require at a kick-off."""
        for team in self.pos:
            for slot, player in enumerate(self.lineup[team]):
                if player is not None:
                    x, y = self.target(team, slot, player, False, (50, 50))
                    self.pos[team][player] = (min(x, 46), y)

    # -- changes -----------------------------------------------------------------

    def place(self, team: str, player: str, p: Pt) -> None:
        if player in self.pos[team]:
            self.pos[team][player] = (_clamp(p[0], 0, 100), _clamp(p[1], 0, 100))

    def substitute(self, team: str, off: str, on: str) -> None:
        self.lineup[team] = [on if p == off else p for p in self.lineup[team]]
        self.pos[team][on] = self.pos[team].pop(off)
        self.offset.setdefault(on, self.offset.get(off, (0, 0)))

    def send_off(self, team: str, player: str) -> None:
        self.lineup[team] = [None if p == player else p for p in self.lineup[team]]
        self.pos[team].pop(player, None)

    def set_back_line(self, team: str, x: float) -> None:
        """Step this team's back four up (or down) to one line, in its own frame."""
        for slot, player in enumerate(self.lineup[team]):
            if player is not None and ROLES[slot] == "DF":
                self.pos[team][player] = (x, self.pos[team][player][1])

    # -- queries -----------------------------------------------------------------

    def team_of(self, player: str) -> str:
        return next(t for t, players in self.pos.items() if player in players)

    def at(self, team: str, player: str) -> Pt:
        return self.pos[team][player]

    def nearest(self, team: str, p: Pt, exclude: tuple[str, ...] = (), outfield: bool = True) -> str:
        """The player of ``team`` closest to ``p`` (``p`` in that team's frame)."""
        options = [(metres(q, p), pl) for slot, pl in enumerate(self.lineup[team])
                   if pl is not None and pl not in exclude and not (outfield and ROLES[slot] == "GK")
                   for q in [self.pos[team][pl]]]
        return min(options)[1]

    def distance_to_nearest(self, team: str, p: Pt) -> float:
        return min(metres(q, p) for q in self.pos[team].values())

    def offside_line(self, defending: str) -> float:
        """The second-last defender's x in the *attacking* team's frame (the goalkeeper is usually last)."""
        xs = sorted(q[0] for q in self.pos[defending].values())
        return 100 - xs[1] if len(xs) > 1 else 100 - xs[0]

    def frame(self) -> dict[str, list[float]]:
        """Every player's position in pitch coordinates, for the event stream."""
        out = {}
        for team, players in self.pos.items():
            for player, (x, y) in players.items():
                if team != self.home:
                    x, y = 100 - x, 100 - y
                out[player] = [round(x, 1), round(y, 1)]
        return out
