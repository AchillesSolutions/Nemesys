"""The server: the fixed data, the lobby by voice, and the old address."""

import pytest
from fastapi.testclient import TestClient

from kickoff import rival as rival_module
from kickoff.agents.offline import OfflineChatClient
from kickoff.generator import generate_match
from kickoff.server import create_app
from kickoff.world import build_world


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def match(world):
    return generate_match(world)


@pytest.fixture()
def client(world, match, tmp_path, monkeypatch):
    monkeypatch.setattr(rival_module, "SAVED", tmp_path)  # no saved sessions: everyone is new
    return TestClient(create_app(world, match, clients=lambda role: OfflineChatClient(role)))


def test_the_fixed_data_draws_the_fixture(client, match):
    assert client.get("/api/health").json()["status"] == "ok"
    info = client.get("/api/match").json()
    assert info["home"] == match.home and info["duration"] == match.events[-1].elapsed
    table = client.get("/api/world").json()["table"]
    assert len(table) == 20 and "points" in table[0]


def test_the_lobby_by_voice(client):
    welcome = client.get("/api/rival/welcome").json()["text"]
    assert "Welcome to Nemesys" in welcome and "中文" in welcome
    lobby = client.post("/api/rival/watch").json()
    assert lobby["stage"] == "language"
    heard = lambda text, lang=None: client.post(f"/api/rival/{lobby['id']}/heard", json={"text": text, "lang": lang}).json()
    assert heard("English please")["stage"] == "name"
    assert heard("I'm Sam")["say"].startswith("Nice to meet you.")
    step = heard("Kilburn")
    assert step["stage"] == "rival" and step["offered"] == ["dez", "kev"]
    assert heard("Kev")["rival"]["id"] == "kev"
    assert client.get(f"/api/rival/{lobby['id']}/state").json()["user_team"] == "kil"
    assert client.post(f"/api/rival/{lobby['id']}/end").json() == {"ok": True}
    assert client.get(f"/api/rival/{lobby['id']}/state").status_code == 404


def test_the_old_address_still_works(client):
    r = client.get("/rival.html?dry=1&auto=1", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/?dry=1&auto=1"
