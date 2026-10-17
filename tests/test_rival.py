"""The Rival: what it's told, when, and how the match streams alongside it (GPT-Live itself is faked)."""

import asyncio
import json

import pytest

from kickoff import rival as rival_module
from kickoff.generator import generate_match
from kickoff.models import EventType
from kickoff.rival import Rival, RivalSession, ammo, describe, rival_instructions
from kickoff.world import build_world


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def match(world):
    return generate_match(world)


class FakeSideband:
    """Stands in for the GPT-Live sideband: records what the server sends."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self) -> None:
        pass


def test_instructions_cover_both_clubs_and_the_boundaries(world, match):
    text = rival_instructions(world, match)
    assert "Kilburn Athletic" in text and "Harbour City" in text
    assert "You lead" in text
    assert "never mention real clubs" in text
    assert "a The" not in text and ".." not in text


def test_ammo_digs_at_the_users_club(world):
    points = ammo(world, world.club("hbr"), world.club("kil"))
    assert any("8th" in p for p in points), "the table is the first dig"
    assert any("Whitcombe" in p for p in points), "the striker the rival doubts is their number 9"
    assert not any("rarely loses" in p for p in points), "a compliment is not a weakness"


def test_every_big_moment_is_described(world, match):
    described = [describe(world, ev) for ev in match.events if ev.type in rival_module.REACTS_TO]
    goals = [d for d in described if "GOAL for" in d]
    assert len(goals) == 6
    assert "0-2" in goals[1], "two down at the break"
    assert "Kilburn 3-2 Harbour City" in goals[-1]
    assert any("RED CARD" in d for d in described)
    assert any("ruled the" in d or "rules the" in d for d in described)


@pytest.mark.parametrize("heard, team", [
    ("kilburn", "kil"), ("i'm with the otters", "kil"), ("harbour city mate", "hbr"), ("the gulls", "hbr"),
    ("neither", None), ("kilburn not harbour", None),
])
def test_side_comes_from_what_the_user_says(heard, team):
    assert Rival._side_from(heard) == team


def test_the_match_streams_and_the_rival_hears_the_big_moments(world, match, monkeypatch):
    monkeypatch.setattr(rival_module, "COUNTDOWN_S", 1)
    monkeypatch.setattr(rival_module, "PREMATCH_S", 0)
    monkeypatch.setattr(rival_module, "HALF_TIME_S", 0)
    monkeypatch.setattr(rival_module, "MATCH_SPEED", 1e9)

    async def run():
        rival = Rival(world, match, endpoint="https://example.invalid", key="test")
        sideband = FakeSideband()
        session = RivalSession("s1", sideband, {"kil": 0, "hbr": 0})
        stream: asyncio.Queue = asyncio.Queue()
        session.listeners.add(stream)
        await rival.choose_side(session, "kil")
        await asyncio.wait_for(session.director, timeout=30)
        messages = []
        while not stream.empty():
            messages.append(stream.get_nowait())
        return session, sideband.sent, messages

    session, sent, messages = asyncio.run(run())
    assert session.phase == "full_time"
    assert session.score == {"kil": 3, "hbr": 2}

    events = [m for m in messages if m["type"] == "event"]
    assert len(events) == len(match.events), "every event reaches the pitch"
    assert events[-1]["snapshot"]["status"] == "full_time"

    spoken = [m["content"] for m in sent if m["type"] == "session.commentary.append"]
    assert "Kilburn" in sent[0]["content"] and sent[0]["type"] == "session.thinking.append", "sides first"
    assert spoken[0].startswith("The teams are walking out")
    assert sum("GOAL for" in s for s in spoken) == 6
    assert spoken[-1].startswith("FULL-TIME")
    states = [m for m in messages if m["type"] == "state"]
    assert states[0]["user_team"] == "kil", "the screen shows the sides as soon as they're picked"
    phases = [s["phase"] for s in states]
    assert phases[1] == "countdown" and "half_time" in phases and phases[-1] == "full_time"
    assert len([e for e in events if e["event"]["type"] == EventType.HALF_TIME]) == 1
