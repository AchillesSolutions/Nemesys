"""Shared data models for the fictional world and the match event stream.

Pitch coordinates are 0-100 on both axes. The home side always attacks
towards x = 100 (no switch at half time) so the frontend can draw events
without knowing the period.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Position(StrEnum):
    GK = "GK"
    DF = "DF"
    MF = "MF"
    FW = "FW"


class Player(BaseModel):
    id: str
    name: str
    number: int
    position: Position
    age: int
    traits: list[str] = Field(default_factory=list)


class Style(BaseModel):
    """How a club plays. The generator turns these numbers into events."""

    summary: str
    pass_tempo_s: float = Field(description="Average seconds between passes")
    pass_accuracy: float = Field(description="Base chance a pass finds a teammate")
    directness: float = Field(description="Average forward progress per pass, in pitch units")
    counter_threat: float = Field(description="Chance of launching a fast break after winning the ball")
    shot_appetite: float = Field(description="Chance of shooting per action in the final third")


class Club(BaseModel):
    id: str
    name: str
    short_name: str
    nickname: str
    city: str
    stadium: str
    colours: tuple[str, str]
    style: Style
    squad: list[Player]
    starting_xi: list[str]

    def player(self, player_id: str) -> Player:
        return next(p for p in self.squad if p.id == player_id)


class TableRow(BaseModel):
    club: str
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    form: list[str] = Field(default_factory=list, description="Last five results, oldest first: W/D/L")

    @property
    def points(self) -> int:
        return self.won * 3 + self.drawn

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


class World(BaseModel):
    league: str
    season: str
    matchweek: int
    clubs: list[Club]
    table: list[TableRow]

    def club(self, club_id: str) -> Club:
        return next(c for c in self.clubs if c.id == club_id)


class EventType(StrEnum):
    KICK_OFF = "kick_off"
    PASS = "pass"
    SHOT = "shot"
    SAVE = "save"
    GOAL = "goal"
    GOAL_DISALLOWED = "goal_disallowed"
    TACKLE = "tackle"
    INTERCEPTION = "interception"
    FOUL = "foul"
    FREE_KICK = "free_kick"
    CORNER = "corner"
    THROW_IN = "throw_in"
    GOAL_KICK = "goal_kick"
    OFFSIDE = "offside"
    YELLOW_CARD = "yellow_card"
    RED_CARD = "red_card"
    SUBSTITUTION = "substitution"
    VAR_CHECK = "var_check"
    VAR_DECISION = "var_decision"
    PENALTY = "penalty"
    HALF_TIME = "half_time"
    FULL_TIME = "full_time"
    PRE_MATCH = "pre_match"  # not in the match data: the introduction before kick-off


class MatchEvent(BaseModel):
    id: int
    elapsed: int = Field(description="Seconds of play since kick-off, monotonic; the half-time break is not counted")
    period: int
    clock: int = Field(description="Match clock in seconds; the second half starts at 2700")
    minute: str = Field(description="Display minute, e.g. '23' or '45+2'")
    type: EventType
    team: str | None = None
    player: str | None = None
    other_player: str | None = Field(None, description="Receiver, fouled player, player coming on, etc.")
    x: float | None = None
    y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    outcome: str | None = None
    detail: dict = Field(default_factory=dict)
    frame: dict[str, list[float]] | None = Field(None, description="Every player's [x, y] on the pitch at this event")


class Match(BaseModel):
    id: str
    home: str
    away: str
    venue: str
    matchweek: int
    seed: int
    events: list[MatchEvent]
