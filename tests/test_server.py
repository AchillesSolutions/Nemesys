"""The server streams a match to each viewer and obeys their controls."""

import pytest
from fastapi.testclient import TestClient

from kickoff.agents.offline import OfflineChatClient
from kickoff.chapters import build_chapters
from kickoff.generator import generate_match
from kickoff.models import EventType
from kickoff.server import create_app
from kickoff.world import build_world


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def match(world):
    return generate_match(world)


@pytest.fixture()
def client(world, match):
    return TestClient(create_app(world, match, clients=lambda role: OfflineChatClient(role)))


def receive_until(ws, kind: str, limit: int = 5000) -> list[dict]:
    seen = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if msg["type"] == kind:
            return seen
    raise AssertionError(f"no {kind!r} message in {limit} messages")


def test_rest_endpoints(client):
    assert client.get("/api/health").json()["status"] == "ok"
    world = client.get("/api/world").json()
    assert {c["id"] for c in world["clubs"]} == {"kil", "hbr"}
    assert world["table"][0]["points"] >= world["table"][-1]["points"]
    match = client.get("/api/match").json()
    assert match["home"] == "kil" and len(match["chapters"]) >= 10


def test_key_moments_plays_only_chapters_with_correct_score(client, match):
    chapters = build_chapters(match)
    with client.websocket_connect("/ws?mode=key_moments&speed=600&team=kil") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready" and len(ready["chapters"]) == len(chapters)
        messages = receive_until(ws, "finished")

    events = [m for m in messages if m["type"] == "event"]
    assert all(any(c.start <= m["event"]["elapsed"] <= c.end for c in chapters) for m in events)
    assert len(events) < len(match.events) / 2, "most of the match should be skipped"
    final = events[-1]["snapshot"]
    assert final["status"] == "full_time"
    assert (final["home"]["score"], final["away"]["score"]) == (3, 2), "skipped events still count"
    assert [m["chapter"]["id"] for m in messages if m["type"] == "chapter"] == [c.id for c in chapters]
    headlines = [line for m in messages if m["type"] == "commentary" for line in m["lines"] if line["headline"]]
    assert headlines, "big moments should produce instant headlines"
    gaps = [m["next_gap"] for m in events]
    assert gaps[-1] is None and all(g is None or g >= 0 for g in gaps)
    assert sum(g is None for g in gaps) == len(chapters), "play jumps exactly once per chapter"


def test_switching_language_mid_match_keeps_what_was_learned(client):
    with client.websocket_connect("/ws?mode=key_moments&speed=600") as ws:
        ws.receive_json()
        ws.send_json({"action": "pause"})
        ws.send_json({"action": "seek_chapter", "chapter": 2})  # first offside
        ws.send_json({"action": "profile", "language": "zh"})
        profile = receive_until(ws, "profile")[-1]
        assert profile["profile"]["language"] == "zh"
        ws.send_json({"action": "resume"})
        messages = receive_until(ws, "finished")

    lines = [line for m in messages if m["type"] == "commentary" for line in m["lines"]]
    goal = next(line for line in lines if line["headline"] and line["card_title"] == "进球！")
    assert "进球" in goal["text"]


def test_bad_control_messages_are_reported_not_fatal(client):
    with client.websocket_connect("/ws?speed=600") as ws:
        ws.receive_json()
        ws.send_json({"action": "pause"})
        ws.send_json({"action": "fly"})
        assert receive_until(ws, "error")[-1]["message"].startswith("Bad control message")
        ws.send_json({"action": "profile", "language": "klingon"})
        assert receive_until(ws, "error")[-1]["type"] == "error"
        ws.send_json({"action": "profile", "level": "analyst"})
        assert receive_until(ws, "profile")[-1]["profile"]["level"] == "analyst"


def test_next_chapter_skips_ahead_without_spoiling(client, match):
    chapters = build_chapters(match)
    with client.websocket_connect("/ws?mode=key_moments&speed=1") as ws:
        ws.receive_json()
        ws.send_json({"action": "next_chapter"})  # from kick-off to the first offside chapter
        seen = receive_until(ws, "chapter")
        while seen[-1]["chapter"]["id"] == 1:
            seen = receive_until(ws, "chapter")
    assert seen[-1]["chapter"]["id"] == chapters[1].id


def test_full_mode_plays_every_event(client, match):
    with client.websocket_connect("/ws?mode=full&speed=100000") as ws:
        ws.receive_json()
        messages = receive_until(ws, "finished", limit=20000)
    events = [m for m in messages if m["type"] == "event"]
    assert len(events) == len(match.events)
    assert events[-1]["event"]["type"] == EventType.FULL_TIME


def test_highlights_compress_quiet_play_and_bridge_long_gaps(client, match):
    with client.websocket_connect("/ws?mode=key_moments&speed=600") as ws:
        ws.receive_json()
        messages = receive_until(ws, "finished")
    events = [m for m in messages if m["type"] == "event"]
    paces = {m["pace"] for m in events}
    assert min(paces) == 1.0 and max(paces) >= 8.0, "quiet stretches and dead time run faster, moments at normal speed"
    notable = [m for m in events if m["salience"] >= 2]
    assert all(m["pace"] == 1.0 for m in notable[1:]), "the build-up to and aftermath of moments play at normal speed"
    bridges = [m for m in messages if m["type"] == "bridge"]
    assert bridges, "long jumps between moments get a bridge"
    assert all(b["from_minute"] != b["to_minute"] for b in bridges)
    # The offline Storyteller sums up the skipped minutes on a bridge.
    texts = [line["text"] for m in messages if m["type"] == "commentary" for line in m["lines"]]
    assert any(t.startswith("Since then") for t in texts)


def test_full_match_plays_at_real_pace(client):
    with client.websocket_connect("/ws?mode=full&speed=100000") as ws:
        ws.receive_json()
        messages = receive_until(ws, "finished", limit=20000)
    assert {m["pace"] for m in messages if m["type"] == "event"} == {1.0}
    assert not [m for m in messages if m["type"] == "bridge"]


def test_every_notable_moment_is_commentated_before_play_skips_ahead(client):
    """A moment at the very end of a chapter (like the first offside) must still get its call and
    explanation: play lets the moment land before jumping to the next one."""
    with client.websocket_connect("/ws?mode=key_moments&speed=600") as ws:
        ws.receive_json()
        messages = receive_until(ws, "finished")
    notable = {m["event"]["id"] for m in messages if m["type"] == "event"
               and m["event"]["type"] in ("offside", "yellow_card", "var_check", "goal", "red_card")}
    commentated = {m["event_id"] for m in messages if m["type"] == "commentary"}
    assert notable and notable <= commentated, sorted(notable - commentated)
