"""The fictional world and generated match must tell the story the agents rely on."""

import pytest

from kickoff.generator import generate_match
from kickoff.match_reader import MatchReader
from kickoff.models import EventType
from kickoff.world import AWAY_ID, HOME_ID, build_world


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def match(world):
    return generate_match(world)


@pytest.fixture(scope="module")
def final(world, match):
    reader = MatchReader(world, match.home, match.away)
    updates = [reader.apply(ev) for ev in match.events]
    return updates


def test_table_is_consistent(world):
    table = world.table
    assert len(table) == 20
    assert all(r.played == world.matchweek - 1 for r in table)
    assert sum(r.won for r in table) == sum(r.lost for r in table)
    assert sum(r.goals_for for r in table) == sum(r.goals_against for r in table)
    points = [r.points for r in table]
    assert points == sorted(points, reverse=True)


def test_table_sets_up_the_stakes(world):
    names = [r.club for r in world.table]
    harbour = names.index("Harbour City")
    assert harbour == 4, "Harbour City should start 5th"
    gap = world.table[3].points - world.table[4].points
    assert 0 < gap < 3, "a Harbour City win should lift them into the top four"
    assert 5 < names.index("Kilburn Athletic") < 12


def test_generation_is_deterministic(world, match):
    assert generate_match(world).model_dump() == match.model_dump()


def test_events_are_ordered(match):
    elapsed = [e.elapsed for e in match.events]
    assert elapsed == sorted(elapsed)
    assert [e.id for e in match.events] == list(range(1, len(match.events) + 1))
    assert match.events[0].type == EventType.KICK_OFF
    assert match.events[-1].type == EventType.FULL_TIME


def test_coordinates_are_on_the_pitch(match):
    for e in match.events:
        for v in (e.x, e.y, e.end_x, e.end_y):
            assert v is None or 0 <= v <= 100


def test_story_beats_are_planted(match):
    types = [e.type for e in match.events]
    for required in (EventType.OFFSIDE, EventType.FREE_KICK, EventType.PENALTY, EventType.VAR_CHECK,
                     EventType.VAR_DECISION, EventType.YELLOW_CARD, EventType.RED_CARD,
                     EventType.SUBSTITUTION, EventType.GOAL_DISALLOWED, EventType.HALF_TIME):
        assert required in types, required
    assert types.count(EventType.OFFSIDE) >= 3, "repeat offsides let the Teacher shorten its explanation"
    assert match.events[-1].detail["score"] == {HOME_ID: 3, AWAY_ID: 2}


def test_style_shows_in_the_numbers(final):
    snap = final[-1].snapshot
    assert snap.home.possession_pct > 58, "the patient side should keep the ball"
    assert snap.home.pass_accuracy_pct > snap.away.pass_accuracy_pct


def test_counter_attack_goals_are_quick(match):
    counters = [e for e in match.events if e.type == EventType.GOAL and e.detail.get("pattern") == "counter_attack"]
    assert len(counters) == 2
    assert all(e.team == AWAY_ID and e.detail["seconds_from_recovery"] < 15 for e in counters)


def test_sent_off_player_takes_no_further_part(match):
    red = next(e for e in match.events if e.type == EventType.RED_CARD)
    later = [e for e in match.events if e.elapsed > red.elapsed and red.player in (e.player, e.other_player)]
    assert not later


def test_substituted_players_take_no_further_part(match):
    for sub in (e for e in match.events if e.type == EventType.SUBSTITUTION):
        later = [e for e in match.events if e.id > sub.id and sub.player in (e.player, e.other_player)]
        assert not later, f"{sub.player} appears after being substituted"
