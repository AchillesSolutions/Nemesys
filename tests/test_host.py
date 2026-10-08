"""The Host agent: tool calls that change settings and look facts up."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from kickoff.agents.host import Host
from kickoff.agents.offline import OfflineChatClient
from kickoff.agents.viewer import ViewerProfile
from kickoff.generator import generate_match
from kickoff.match_reader import MatchReader
from kickoff.models import EventType
from kickoff.server import create_app
from kickoff.speech import Speech
from kickoff.world import build_world


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def match(world):
    return generate_match(world)


def make_host(world, match, profile=None, upto=None):
    state = {"profile": profile or ViewerProfile()}

    def set_profile(**changes):
        state["profile"] = ViewerProfile.model_validate(state["profile"].model_dump() | changes)
        return state["profile"]

    reader = MatchReader(world, match.home, match.away)
    played = 0
    for i, ev in enumerate(match.events):
        if upto is None or ev.type == upto and played:
            break
        reader.apply(ev)
        played = i + 1
        if ev.type == upto:
            break
    host = Host(OfflineChatClient("host"), world, match, lambda: state["profile"], set_profile,
                reader=lambda: reader, played=lambda: played)
    return host, state


def ask(host, text):
    return asyncio.run(host.ask(text))


def test_setup_sentence_sets_team_and_level(world, match):
    host, state = make_host(world, match, ViewerProfile(level="analyst"))
    reply = ask(host, "It's my first ever football match and I'm supporting Kilburn")
    assert state["profile"].team == "kil" and state["profile"].level == "beginner"
    assert reply.changes == {"team": "kil", "level": "beginner"}
    assert "set_team" in reply.tools_used


def test_setting_the_same_value_is_not_a_change(world, match):
    host, state = make_host(world, match, ViewerProfile(language="zh"))
    reply = ask(host, "中文 please")
    assert reply.tools_used == ["set_language"] and reply.changes == {}


def test_switching_language(world, match):
    host, state = make_host(world, match)
    reply = ask(host, "Can you switch to Mandarin?")
    assert state["profile"].language == "zh" and reply.changes == {"language": "zh"}


def test_player_lookup_uses_the_squad(world, match):
    host, _ = make_host(world, match, upto=EventType.HALF_TIME)
    reply = ask(host, "Who is number 9?")
    assert "player_info" in reply.tools_used
    assert "Ollie Whitcombe" in reply.answer or "Isaac Mwangi" in reply.answer


def test_why_question_reads_recent_events(world, match):
    host, _ = make_host(world, match, upto=EventType.GOAL_DISALLOWED)
    reply = ask(host, "Why didn't that count?")
    assert reply.tools_used == ["recent_events"]
    assert "VAR decision: no goal" in reply.answer


def test_unknown_player_is_not_invented(world, match):
    host, _ = make_host(world, match)
    tools = {t.name: t for t in host.tools()}
    assert tools["player_info"].func("Zlatan").startswith("No player called")


@pytest.fixture()
def client(world, match):
    return TestClient(create_app(world, match, clients=lambda role: OfflineChatClient(role),
                                 speech=Speech(key="", region="")))


def test_setup_endpoint(client):
    r = client.post("/api/setup", json={"text": "I'm new and I'm supporting Harbour, in Spanish please"})
    body = r.json()
    assert r.status_code == 200
    assert body["profile"] == {"id": "viewer", "team": "hbr", "language": "es", "level": "beginner"}


def test_ask_over_the_live_connection_changes_profile(client):
    with client.websocket_connect("/ws?speed=1") as ws:
        ws.receive_json()
        ws.send_json({"action": "pause"})
        ws.send_json({"action": "ask", "text": "switch to Mandarin please"})
        seen = []
        while not any(m["type"] == "host" for m in seen):
            seen.append(ws.receive_json())
    profile = next(m for m in seen if m["type"] == "profile")
    host = next(m for m in seen if m["type"] == "host")
    assert profile["profile"]["language"] == "zh"
    assert host["changes"] == {"language": "zh"} and host["language"] == "zh"


def test_speech_token_unavailable_without_speech(client):
    assert client.get("/api/speech/token").status_code == 503
