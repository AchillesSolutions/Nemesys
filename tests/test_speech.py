"""The lobby host's voice: voice styles, safe SSML in several languages, and the endpoint."""

from fastapi.testclient import TestClient

from kickoff.agents.offline import OfflineChatClient
from kickoff.speech import Speech, build_ssml, pick_style
from kickoff.server import create_app


class FakeSpeech(Speech):
    def __init__(self):
        super().__init__(key="test", region="test")
        self.calls = []

    async def synthesise(self, text, language, tone="neutral"):
        self.calls.append((text, language, tone))
        return b"ID3fake-mp3"


def test_tone_picks_the_best_style_the_voice_supports():
    assert pick_style("excited", {"sports-commentary", "sports-commentary-excited"}) == "sports-commentary-excited"
    assert pick_style("disappointed", {"regretful", "sad"}) == "regretful"
    assert pick_style("excited", {"cheerful", "sad"}) == "cheerful"
    assert pick_style("disappointed", {"cheerful"}) is None


def test_ssml_escapes_text_and_applies_style():
    ssml = build_ssml("Kilburn 2 <-> 2 & rising", "en-GB", "en-GB-RyanNeural", "excited", {"cheerful"})
    assert "&lt;-&gt;" in ssml and "&amp;" in ssml
    assert '<mstts:express-as style="cheerful">' in ssml
    assert 'xml:lang="en-GB"' in ssml and 'name="en-GB-RyanNeural"' in ssml


def test_ssml_without_a_matching_style_is_plain():
    ssml = build_ssml("Hola", "es-MX", "es-MX-JorgeNeural", "disappointed", set())
    assert "express-as" not in ssml and "Hola" in ssml


def _client(speech):
    return TestClient(create_app(clients=lambda role: OfflineChatClient(role), speech=speech))


def test_a_multilingual_line_says_each_part_in_its_own_language():
    ssml = build_ssml("Welcome. [zh]欢迎来到 Nemesys。[/zh] [es]Bienvenido.[/es]", "en-GB", "en-GB-AdaMultilingualNeural",
                      "neutral", set())
    assert '<lang xml:lang="zh-CN">' in ssml and '<lang xml:lang="es-MX">' in ssml
    assert "[zh]" not in ssml and "Welcome." in ssml


def test_host_voice_reports_unavailable_without_keys():
    client = _client(Speech(key="", region=""))
    assert client.get("/api/health").json()["speech"] is False
    assert client.post("/api/rival/host", json={"text": "Welcome to Nemesys."}).status_code == 503


def test_host_voice_returns_audio():
    speech = FakeSpeech()
    client = _client(speech)
    assert client.get("/api/health").json()["speech"] is True
    r = client.post("/api/rival/host", json={"text": "[zh]欢迎[/zh]"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert speech.calls == [("[zh]欢迎[/zh]", "host", "neutral")]


def test_host_voice_rejects_empty_text():
    assert _client(FakeSpeech()).post("/api/rival/host", json={"text": ""}).status_code == 422
