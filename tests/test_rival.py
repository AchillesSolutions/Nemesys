"""The Rival: what it's told, when, and how the match streams alongside it (GPT-Live itself is faked)."""

import asyncio
import json

import pytest

from kickoff import news
from kickoff import rival as rival_module
from kickoff.generator import generate_match
from kickoff.models import EventType
from kickoff.rival import (BIG, FULL, ROSTER, SHORT, Pacing, Rival, Voice, Watch, ammo, describe, feeling,
                           match_seconds, pace_plan, previous_meeting, rival_instructions)
from kickoff.world import build_world


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def match(world):
    return generate_match(world)


@pytest.fixture()
def dossier():
    return news.Dossier(stories=[
        news.Story(club="home", kind="transfer", headline="Whitcombe's £3m fee questioned", detail="One goal in six."),
        news.Story(club="away", kind="form", headline="Mwangi on fire", detail="Three in four."),
        news.Story(club="both", kind="history", headline="Last three meetings", detail="Harbour City 2-1 Kilburn."),
    ])


class FakeSideband:
    """Stands in for the GPT-Live sideband: records what the server sends."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self) -> None:
        pass


INSTANT = {"quiet": 1e9, "build": 1e9, "holds": {}, "small_hold": 0, "half_time": 0, "greeting": 0, "after": 0}


def fast(monkeypatch):
    monkeypatch.setattr(rival_module, "NEWS_WAIT_S", 0.01)
    monkeypatch.setattr(rival_module, "PACING", {
        "short": Pacing(build_s=SHORT.build_s, tells_small=False, **INSTANT),
        "full": Pacing(build_s=FULL.build_s, tells_small=True, **INSTANT)})
    monkeypatch.setattr(rival_module.Rival, "_fill", _no_wait)


async def _no_wait(self, watch, seconds, lull=0, gap=0):
    await asyncio.sleep(0)


def test_instructions_cover_both_clubs_the_boundaries_and_the_accent(world, match):
    text = rival_instructions(world, match, "london")
    assert "Kilburn Athletic" in text and "Harbour City" in text
    assert "London accent" in text
    assert "never mention real clubs" in text
    assert "no violent metaphors" in text
    assert "a The" not in text and ".." not in text
    assert "London accent" not in rival_instructions(world, match, "none")


def test_the_rival_knows_who_it_is_after_the_lobby(world, match):
    rematch = {"when": "last season, at The Quay", "result": "Harbour City 2-1 Kilburn Athletic"}
    dez = next(p for p in ROSTER if p.id == "dez")
    text = rival_instructions(world, match, dez.accent, user_team="kil", rematch=rematch, persona=dez)
    assert "You are Dez from Bristol, a lifelong Harbour City fan" in text
    assert "Bristol, West Country accent" in text
    assert "matched you with a Kilburn Athletic fan" in text
    assert "it's YOU again" in text and "Harbour City 2-1 Kilburn Athletic" in text
    assert "asking who they are backing" not in text, "the lobby has already asked"


def test_ammo_goes_beyond_the_match(world, match, dossier):
    last_time = {"when": "last season", "result": "Harbour City 2-1 Kilburn Athletic", "user_said": ["We'll win it."]}
    points = ammo(world, match, world.club("hbr"), world.club("kil"), dossier, last_time)
    assert "8th" in points[0], "the table dig leads"
    assert any("Whitcombe's £3m fee" in p and "Wind them up" in p for p in points), "their club's news is a dig"
    assert any("Mwangi on fire" in p and "Boast" in p for p in points), "our club's good news is a boast"
    assert any("We'll win it." in p for p in points), "what they said last time comes back"
    assert any("history" in p for p in points)


def test_the_seeded_last_meeting_is_for_a_kilburn_fan(tmp_path, monkeypatch):
    monkeypatch.setattr(rival_module, "SAVED", tmp_path)
    assert previous_meeting("kil")["result"] == "Harbour City 2-1 Kilburn Athletic"
    assert previous_meeting("hbr") is None


def test_a_saved_conversation_is_quoted_before_the_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(rival_module, "SAVED", tmp_path)
    (tmp_path / "watch-1.json").write_text(json.dumps({"user_team": "kil", "result": "Kilburn 3-2 Harbour City",
        "transcript": [["user", "Whitcombe is the best striker in this league"], ["rival", "Hardly."],
                       ["user", "Watch us come back from two down today"]]}))
    last = previous_meeting("kil")
    assert last["result"] == "Kilburn 3-2 Harbour City"
    assert "Watch us come back from two down today" in last["user_said"]


def test_moments_land_from_the_rivals_side(world, match):
    goals = [ev for ev in match.events if ev.type is EventType.GOAL]
    assert "Erupt" in feeling(goals[0], "hbr"), "Harbour City's first goal, for a Harbour City rival"
    assert "Gutted" in feeling(goals[0], "kil")
    red = next(ev for ev in match.events if ev.type is EventType.RED_CARD)
    assert "jeer" in feeling(red, "kil") and "Protest" in feeling(red, "hbr")
    half = next(ev for ev in match.events if ev.type is EventType.HALF_TIME)
    assert "gloat" in feeling(half, "hbr") and "excuses" in feeling(half, "kil")


def test_every_big_moment_is_described(world, match):
    described = [describe(world, ev) for ev in match.events if ev.type in BIG]
    goals = [d for d in described if "GOAL for" in d]
    assert len(goals) == 6
    assert "0-2" in goals[1], "two down at the break"
    assert "Kilburn 3-2 Harbour City" in goals[-1]
    assert any("RED CARD" in d for d in described)
    assert any("rules the" in d for d in described)


def test_the_short_match_fits_a_two_minute_demo(match):
    assert match_seconds(match.events, SHORT) + SHORT.greeting < 105, "with the lobby, about two minutes"
    assert 4 * 60 < match_seconds(match.events, FULL) < 6 * 60


def test_play_slows_before_big_moments_and_runs_quickly_otherwise(match, monkeypatch):
    plan = pace_plan(match.events, SHORT)
    assert len(plan) == len(match.events)
    goal = next(i for i, ev in enumerate(match.events) if ev.type is EventType.GOAL)
    assert plan[goal - 1] == SHORT.build, "the move before a goal plays slowly"
    assert plan.count(SHORT.quiet) > len(plan) * 0.8, "most of the match plays quickly"
    assert all(pace > 0 for pace in plan), "the clock never stops or jumps"


@pytest.mark.parametrize("heard, team", [
    ("kilburn", "kil"), ("i'm with the otters", "kil"), ("harbour city mate", "hbr"), ("the gulls", "hbr"),
    ("neither", None), ("kilburn not harbour", None),
])
def test_side_comes_from_what_the_user_says(heard, team):
    assert Rival.side_from(heard) == team


def lobby(world, match, tmp_path, monkeypatch):
    monkeypatch.setattr(rival_module, "SAVED", tmp_path)
    rival = Rival(world, match, endpoint="https://example.invalid", key="test")
    watch = Watch("w1", {"kil": 0, "hbr": 0})
    return rival, watch


def test_the_lobby_offers_a_rematch_first(world, match, tmp_path, monkeypatch):
    rival, watch = lobby(world, match, tmp_path, monkeypatch)
    assert rival.heard(watch, "erm, not sure")["say"] is None, "nothing named, nothing said"
    step = rival.heard(watch, "Kilburn, obviously")
    assert step["stage"] == "rival" and step["offered"] == ["dez"]
    assert "Dez from Bristol" in step["say"] and "Rematch, or someone new?" in step["say"]
    step = rival.heard(watch, "go on then, rematch")
    assert step["stage"] == "done" and step["rival"]["id"] == "dez" and step["rival"]["watched"]
    assert watch.persona.voice == "cedar" and watch.rematch["result"] == "Harbour City 2-1 Kilburn Athletic"


def test_someone_new_then_by_name(world, match, tmp_path, monkeypatch):
    rival, watch = lobby(world, match, tmp_path, monkeypatch)
    rival.heard(watch, "Kilburn")
    step = rival.heard(watch, "nah, someone new")
    assert "Also online: Siobhan from Dublin and Kev from Manchester" in step["say"]
    step = rival.heard(watch, "Siobhan please")
    assert step["rival"]["id"] == "siobhan" and not step["rival"]["watched"]
    assert watch.rematch is None, "never met: no 'last time' to quote"


def test_a_harbour_fan_meets_new_kilburn_rivals(world, match, tmp_path, monkeypatch):
    rival, watch = lobby(world, match, tmp_path, monkeypatch)
    step = rival.heard(watch, "the gulls")
    assert step["offered"] == ["rob", "priya", "callum"], "nobody to rematch: all three are offered"
    assert step["say"].endswith("Who do you fancy?")
    assert rival.heard(watch, "Callum")["rival"]["home"] == "Glasgow"


def run_match(world, match, monkeypatch, tmp_path, drop_voice_at: str | None = None, mode: str = "full"):
    persona = next(p for p in ROSTER if p.id == "dez")
    fast(monkeypatch)
    monkeypatch.setattr(rival_module, "SAVED", tmp_path)

    async def run():
        rival = Rival(world, match, endpoint="https://example.invalid", key="test")
        sideband = FakeSideband()
        watch = Watch("w1", {"kil": 0, "hbr": 0}, voice=Voice("v1", sideband), mode=mode, persona=persona)
        rival.watches[watch.id] = watch
        stream: asyncio.Queue = asyncio.Queue()
        watch.listeners.add(stream)
        rival.choose_side(watch, "kil")
        await rival.opened(watch, resumed=False)
        while not watch.director.done():
            await asyncio.sleep(0)
            if drop_voice_at and watch.minute == drop_voice_at:
                watch.voice = None  # GPT-Live went away mid-match
        messages = []
        while not stream.empty():
            messages.append(stream.get_nowait())
        return watch, sideband.sent, messages

    return asyncio.run(run())


def test_the_match_streams_and_the_rival_hears_the_big_moments(world, match, monkeypatch, tmp_path):
    watch, sent, messages = run_match(world, match, monkeypatch, tmp_path)
    assert watch.phase == "full_time"
    assert watch.score == {"kil": 3, "hbr": 2}

    events = [m for m in messages if m["type"] == "event"]
    assert len(events) == len(match.events), "every event reaches the pitch"
    assert events[-1]["snapshot"]["status"] == "full_time"

    assert sent[0]["type"] == "session.thinking.append" and sent[0]["content"].startswith("News on"), "news first"
    spoken = [m["content"] for m in sent if m["type"] == "session.commentary.append"]
    assert spoken[0].startswith("You've just joined. Greet them"), "the rival greets, with a dig"
    goals = [s for s in spoken if "GOAL for" in s]
    assert len(goals) == 6
    assert all(s.startswith(rival_module.BREAK_IN) for s in goals), "big moments break in"
    assert "Erupt" in goals[0] and "Gutted" in goals[2], "Harbour City's goal thrills the rival; Kilburn's doesn't"
    phases = [m["phase"] for m in messages if m["type"] == "state"]
    assert phases[0] == "lobby" and "prematch" in phases and "half_time" in phases and phases[-1] == "full_time"
    moments = [m for m in messages if m["type"] == "moment"]
    assert sum(m["big"] for m in moments) >= 12, "the match feed shows the big moments"
    assert {m["id"] for m in messages if m["type"] == "agent"} >= {"ammo", "match"}, "agents report their work"


def test_the_short_match_tells_only_the_big_moments(world, match, monkeypatch, tmp_path):
    watch, sent, messages = run_match(world, match, monkeypatch, tmp_path, mode="short")
    assert watch.phase == "full_time" and watch.score == {"kil": 3, "hbr": 2}
    assert len([m for m in messages if m["type"] == "event"]) == len(match.events), "the clock never jumps"
    spoken = [m["content"] for m in sent if m["type"] == "session.commentary.append"]
    assert sum("GOAL for" in s for s in spoken) == 6
    assert not any("big save" in s for s in spoken), "small moments stay off the short match"


def test_the_match_carries_on_when_the_voice_drops(world, match, monkeypatch, tmp_path):
    watch, sent, messages = run_match(world, match, monkeypatch, tmp_path, drop_voice_at="56")
    assert watch.phase == "full_time", "a live game never stops"
    assert watch.score == {"kil": 3, "hbr": 2}
    assert len([m for m in messages if m["type"] == "event"]) == len(match.events)
    spoken = [m["content"] for m in sent if m["type"] == "session.commentary.append"]
    assert not any("90+4'" in s for s in spoken), "nothing is sent while there's no voice"
    assert any("90+4'" in f["text"] for f in watch.feed), "but the feed still shows what happened"
