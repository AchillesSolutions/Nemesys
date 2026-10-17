"""The Match reader keeps an accurate live picture from the event stream."""

import asyncio

import pytest

from kickoff.generator import generate_match
from kickoff.match_reader import MatchReader
from kickoff.models import EventType, MatchEvent
from kickoff.replayer import replay
from kickoff.world import AWAY_ID, HOME_ID, build_world


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def match(world):
    return generate_match(world)


def _run(world, events):
    reader = MatchReader(world, HOME_ID, AWAY_ID)
    return reader, [reader.apply(e) for e in events]


def _ev(i, elapsed, type_, team=None, **kw):
    return MatchEvent(id=i, elapsed=elapsed, period=1, clock=elapsed, minute=str(elapsed // 60 + 1),
                      type=type_, team=team, **kw)


def test_final_snapshot(world, match):
    _, updates = _run(world, match.events)
    snap = updates[-1].snapshot
    assert snap.status == "full_time"
    assert (snap.home.score, snap.away.score) == (3, 2)
    assert snap.away.red_cards == 1 and len(snap.away.players_on_pitch) == 10
    assert len(snap.home.players_on_pitch) == 11
    assert "kil-17" in snap.home.players_on_pitch and "kil-11" not in snap.home.players_on_pitch
    # Lineup slots keep formation order: the substitute takes the replaced player's slot,
    # and a sent-off player's slot is left empty.
    assert snap.home.lineup[10] == "kil-17"
    assert snap.away.lineup[5] is None and len(snap.away.lineup) == 11
    assert snap.home.possession_pct + snap.away.possession_pct == pytest.approx(100, abs=0.2)


def test_disallowed_goal_reverts_score(world, match):
    _, updates = _run(world, match.events)
    disallowed = next(u for u in updates if u.event.type == EventType.GOAL_DISALLOWED)
    assert (disallowed.snapshot.home.score, disallowed.snapshot.away.score) == (2, 2)


def test_big_moments_have_top_salience(world, match):
    _, updates = _run(world, match.events)
    for u in updates:
        if u.event.type in (EventType.GOAL, EventType.RED_CARD, EventType.PENALTY):
            assert u.salience == 3
        if u.event.type == EventType.PASS:
            assert u.salience == 0


def test_goal_signals_describe_the_scoreline(world, match):
    _, updates = _run(world, match.events)
    kinds = [s["kind"] for u in updates if u.event.type == EventType.GOAL for s in u.signals]
    # 0-1, 0-2, 1-2, 2-2, 3-2 (ruled out), 3-2
    assert kinds == ["takes_the_lead", "extends_lead", "pulls_one_back", "equaliser", "takes_the_lead",
                     "takes_the_lead"]


def test_red_card_signals_numerical_advantage(world, match):
    _, updates = _run(world, match.events)
    red = next(u for u in updates if u.event.type == EventType.RED_CARD)
    sig = next(s for s in red.signals if s["kind"] == "numerical_advantage")
    assert sig["team"] == HOME_ID and sig["players"] == {HOME_ID: 11, AWAY_ID: 10}


def test_possession_counts_only_live_ball(world):
    events = [
        _ev(1, 0, EventType.KICK_OFF, HOME_ID, x=50, y=50),
        _ev(2, 30, EventType.FOUL, AWAY_ID, x=60, y=50),       # home had it for 30s
        _ev(3, 90, EventType.FREE_KICK, HOME_ID, x=40, y=50),  # 60s stoppage: not counted
        _ev(4, 100, EventType.INTERCEPTION, AWAY_ID, x=50, y=50),  # home 10s more
        _ev(5, 140, EventType.PASS, AWAY_ID, x=50, y=50, outcome="complete"),  # away 40s
    ]
    reader, _ = _run(world, events)
    share = reader.possession_share()
    assert share[HOME_ID] == pytest.approx(50.0)
    assert share[AWAY_ID] == pytest.approx(50.0)


def test_quiet_spell_signal_fires_once(world):
    events = [_ev(1, 0, EventType.KICK_OFF, HOME_ID, x=50, y=50)] + [
        _ev(i, i * 20, EventType.PASS, HOME_ID, x=50, y=50, outcome="complete") for i in range(2, 20)
    ]
    _, updates = _run(world, events)
    quiet = [s for u in updates for s in u.signals if s["kind"] == "quiet_spell"]
    assert len(quiet) == 1


def test_replayer_skips_ahead_and_keeps_order(match):
    async def collect():
        return [e async for e in replay(match, speed=1e9, start_minute=80)]

    events = asyncio.run(collect())
    assert events and events[0].elapsed >= 80 * 60
    assert events[-1].type == EventType.FULL_TIME
