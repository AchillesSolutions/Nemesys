"""League table arithmetic: what happens to the table if the current score stands."""

from __future__ import annotations

from .models import TableRow, World


def rank(rows: list[TableRow]) -> list[TableRow]:
    return sorted(rows, key=lambda r: (-r.points, -r.goal_difference, -r.goals_for, r.club))


def projected_table(world: World, home: str, away: str, home_goals: int, away_goals: int) -> list[TableRow]:
    """The table after this match if it ended with the given score."""
    names = {home: world.club(home).name, away: world.club(away).name}
    rows = []
    for row in world.table:
        row = row.model_copy(deep=True)
        for cid, gf, ga in ((home, home_goals, away_goals), (away, away_goals, home_goals)):
            if row.club == names[cid]:
                result = "W" if gf > ga else "D" if gf == ga else "L"
                row.played += 1
                row.won += result == "W"
                row.drawn += result == "D"
                row.lost += result == "L"
                row.goals_for += gf
                row.goals_against += ga
                row.form = [*row.form, result][-5:]
        rows.append(row)
    return rank(rows)


def position(table: list[TableRow], club_name: str) -> int:
    return next(i for i, r in enumerate(table, 1) if r.club == club_name)


def stakes(world: World, home: str, away: str, home_goals: int, away_goals: int) -> dict:
    """Positions now and if the score stands, for both clubs: facts for the League guide."""
    after = projected_table(world, home, away, home_goals, away_goals)
    out = {}
    for cid in (home, away):
        name = world.club(cid).name
        out[name] = {
            "position_before": position(world.table, name),
            "position_if_score_stands": position(after, name),
            "points_if_score_stands": after[position(after, name) - 1].points,
        }
    out["top_four_cutoff_points_now"] = world.table[3].points
    return out
