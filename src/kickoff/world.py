"""The fictional world: two fully detailed clubs and a league table.

Only the two clubs in our match get squads and playing styles. The other 18
clubs exist as names with results, produced by simulating eight matchweeks of
a round-robin season, so the table is internally consistent (wins equal
losses, goals for equal goals against) and gives the agents real
stakes to talk about.
"""

from __future__ import annotations

import math
import random

from .league import rank
from .models import Club, Player, Position, Style, TableRow, World

LEAGUE_NAME = "The Meridian League"
SEASON = "2026/27"
MATCHWEEK = 9  # the match we replay; the table covers matchweeks 1-8
TABLE_SEED = 7

HOME_ID = "kil"
AWAY_ID = "hbr"
EUROPE_PLACES = 4  # the top four qualify for European competition

# Background to this fixture, for the build-up before kick-off. Invented, like the clubs.
FIXTURE_NOTES = [
    "Both clubs started the season aiming for a European place: the top four qualify for European competition.",
    "Last season this fixture finished 2-2 at Lockside Park, with Harbour City equalising in stoppage time.",
    "Kilburn have not beaten Harbour City in their last three meetings.",
    "The styles are opposites: Kilburn keep the ball and pass patiently; Harbour City defend deep and break fast.",
]

OTHER_CLUBS = [
    "Kestrel Bay", "Millbrook Athletic", "Stonegate United", "Larkhill Forest",
    "Eastmoor", "Saltmarsh Town", "Copperfield", "Thornwick Wanderers",
    "Greywater", "Highcliff", "Ashgrove Rovers", "Pennwick",
    "Ravensholt", "Brackenridge", "Oakhaven", "Wexmoor City",
    "Fenwick Park", "Silverton Vale",
]


def _squad(prefix: str, rows: list[tuple[int, str, Position, int, list[str]]]) -> list[Player]:
    return [
        Player(id=f"{prefix}-{num}", name=name, number=num, position=pos, age=age, traits=traits)
        for num, name, pos, age, traits in rows
    ]


GK, DF, MF, FW = Position.GK, Position.DF, Position.MF, Position.FW

KILBURN = Club(
    id=HOME_ID,
    name="Kilburn Athletic",
    short_name="Kilburn",
    nickname="The Otters",
    city="Kilburn, London",
    stadium="Lockside Park",
    colours=("#0f8b8d", "#ffffff"),
    style=Style(
        summary="Patient passing side: keep the ball, move it side to side, wait for a gap.",
        pass_tempo_s=4.0,
        pass_accuracy=0.89,
        directness=4.5,
        counter_threat=0.08,
        shot_appetite=0.045,
    ),
    squad=_squad("kil", [
        (1, "Tomas Brennan", GK, 31, ["commanding in the air", "calm with the ball at his feet"]),
        (2, "Kofi Mensah-Lane", DF, 24, ["quick recovery runs"]),
        (4, "Elias Varga", DF, 29, ["captain", "dangerous at corners"]),
        (5, "Jun-ho Park", DF, 26, ["comfortable on the ball"]),
        (3, "Callum Reid", DF, 22, ["overlapping full-back", "good crosser"]),
        (6, "Mateo Rios", MF, 27, ["metronome passer", "rarely loses the ball"]),
        (8, "Sami Haddad", MF, 25, ["line-breaking passes"]),
        (10, "Felix Andersen", MF, 23, ["playmaker", "set-piece specialist"]),
        (7, "Rafa Quintero", FW, 21, ["tricky dribbler", "draws fouls"]),
        (9, "Ollie Whitcombe", FW, 28, ["penalty-box poacher", "penalty taker"]),
        (11, "Dario Kessler", FW, 24, ["hard-working winger"]),
        (13, "Liam Fairweather", GK, 34, ["experienced back-up"]),
        (14, "Arno Visser", DF, 27, ["tall centre-back"]),
        (15, "Tariq Bello", MF, 22, ["energetic"]),
        (17, "Leo Castellanos", FW, 19, ["super-sub", "frightening pace"]),
        (18, "Noah Pereira", MF, 30, ["experienced head"]),
        (19, "Hugo Lindqvist", DF, 21, ["academy graduate"]),
        (20, "Malik Osei", FW, 23, ["strong hold-up play"]),
    ]),
    starting_xi=[f"kil-{n}" for n in (1, 2, 4, 5, 3, 6, 8, 10, 7, 9, 11)],
)

HARBOUR_CITY = Club(
    id=AWAY_ID,
    name="Harbour City",
    short_name="Harbour City",
    nickname="The Gulls",
    city="Harbour City",
    stadium="The Quay",
    colours=("#1d2b53", "#ff8c1a"),
    style=Style(
        summary="Counter-attacking side: defend deep, win the ball, then break forward fast.",
        pass_tempo_s=3.0,
        pass_accuracy=0.78,
        directness=9.0,
        counter_threat=0.45,
        shot_appetite=0.08,
    ),
    squad=_squad("hbr", [
        (1, "Viktor Halloran", GK, 33, ["shot-stopper"]),
        (2, "Ade Ogunleye", DF, 25, ["fast full-back"]),
        (5, "Marco Bellandi", DF, 30, ["captain", "aggressive tackler"]),
        (6, "Sven Ekdahl", DF, 27, ["strong header of the ball"]),
        (3, "Jordan Pike", DF, 23, ["eager, sometimes rash"]),
        (4, "Ruben Aalders", MF, 26, ["ball-winner", "picks up bookings"]),
        (8, "Kenji Mori", MF, 24, ["reads passes early", "quick distributor"]),
        (10, "Danny Shaw", MF, 29, ["counter-attack conductor"]),
        (7, "Theo Marsh", FW, 22, ["rapid winger"]),
        (9, "Isaac Mwangi", FW, 27, ["runs in behind", "clinical finisher"]),
        (11, "Luka Petrovic", FW, 25, ["direct runner"]),
        (13, "Pavel Novak", GK, 28, ["reliable back-up"]),
        (14, "Ryan Doyle", DF, 26, ["no-nonsense defender"]),
        (15, "Sam Kowalczyk", MF, 28, ["defensive midfielder", "tidy tackler"]),
        (16, "Nico Ferraro", FW, 20, ["exciting prospect"]),
        (17, "Elijah Grant", MF, 24, ["box-to-box runner"]),
        (18, "Owen Pritchard", FW, 31, ["veteran striker"]),
        (19, "Felipe Moreau", DF, 22, ["versatile defender"]),
    ]),
    starting_xi=[f"hbr-{n}" for n in (1, 2, 5, 6, 3, 4, 8, 10, 7, 9, 11)],
)


def _poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _round_robin(n: int) -> list[list[tuple[int, int]]]:
    """Circle-method fixtures: rounds of (home, away) index pairs."""
    teams = list(range(n))
    rounds = []
    for r in range(n - 1):
        pairs = [(teams[i], teams[n - 1 - i]) for i in range(n // 2)]
        rounds.append([(a, b) if r % 2 == 0 else (b, a) for a, b in pairs])
        teams = [teams[0], teams[-1], *teams[1:-1]]
    return rounds


def _simulate_table(seed: int) -> tuple[list[TableRow], set[frozenset[int]]]:
    rng = random.Random(seed)
    strength = [rng.uniform(0.8, 1.6) for _ in range(20)]
    results = {i: [] for i in range(20)}  # (gf, ga) per match
    met: set[frozenset[int]] = set()
    for fixtures in _round_robin(20)[: MATCHWEEK - 1]:
        for h, a in fixtures:
            hg = _poisson(rng, 1.45 * strength[h] / strength[a])
            ag = _poisson(rng, 1.15 * strength[a] / strength[h])
            results[h].append((hg, ag))
            results[a].append((ag, hg))
            met.add(frozenset((h, a)))
    rows = []
    for i, games in results.items():
        form = ["W" if f > a else "D" if f == a else "L" for f, a in games]
        rows.append(TableRow(
            club=str(i), played=len(games),
            won=form.count("W"), drawn=form.count("D"), lost=form.count("L"),
            goals_for=sum(f for f, _ in games), goals_against=sum(a for _, a in games),
            form=form[-5:],
        ))
    return rank(rows), met


def build_table(seed: int = TABLE_SEED) -> list[TableRow]:
    """Simulate matchweeks 1-8 and name the clubs so the story works.

    Harbour City are placed 5th, within one win of the top four, and
    Kilburn are a mid-table side Harbour City have not played yet.
    The seed is searched deterministically until those conditions hold.
    """
    while True:
        ranked, met = _simulate_table(seed)
        fourth, fifth = ranked[3], ranked[4]
        gap = fourth.points - fifth.points
        harbour = int(fifth.club)
        candidates = [r for r in ranked[6:12] if frozenset((harbour, int(r.club))) not in met]
        if gap in (1, 2) and candidates:
            kilburn = int(candidates[0].club)
            names = iter(OTHER_CLUBS)
            for row in ranked:
                idx = int(row.club)
                row.club = (
                    HARBOUR_CITY.name if idx == harbour
                    else KILBURN.name if idx == kilburn
                    else next(names)
                )
            return ranked
        seed += 1


def build_world() -> World:
    return World(
        league=LEAGUE_NAME,
        season=SEASON,
        matchweek=MATCHWEEK,
        clubs=[KILBURN, HARBOUR_CITY],
        table=build_table(),
    )
