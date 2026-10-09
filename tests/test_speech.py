"""Spoken commentary: voice styles, safe SSML and the speech endpoint."""

from fastapi.testclient import TestClient

from kickoff.agents.offline import OfflineChatClient
from kickoff.speech import Speech, SpeechUnavailable, build_ssml, pick_style
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


def test_speech_endpoint_reports_unavailable_without_keys():
    client = _client(Speech(key="", region=""))
    assert client.get("/api/health").json()["speech"] is False
    r = client.post("/api/speech", json={"text": "Goal!", "language": "en", "tone": "excited"})
    assert r.status_code == 503


def test_speech_endpoint_returns_audio():
    speech = FakeSpeech()
    client = _client(speech)
    assert client.get("/api/health").json()["speech"] is True
    r = client.post("/api/speech", json={"text": "进球了！", "language": "zh", "tone": "excited"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert speech.calls == [("进球了！", "zh", "excited")]


def test_speech_rejects_empty_text():
    assert _client(FakeSpeech()).post("/api/speech", json={"text": "", "language": "en"}).status_code == 422


def test_unconfigured_speech_raises():
    import asyncio
    try:
        asyncio.run(Speech(key="", region="").synthesise("hi", "en"))
    except SpeechUnavailable:
        return
    raise AssertionError("expected SpeechUnavailable")


def test_scores_are_said_like_a_commentator_in_english():
    from kickoff.speech import aloud
    assert aloud("Harbour City lead 1-0", "en") == "Harbour City lead one nil"
    assert aloud("It's 1-1, and then 2–2 at half-time.", "en") == "It's one one, and then two two at half-time."
    assert aloud("It's 0-0.", "en") == "It's nil nil."
    assert aloud("Kilburn 2, Harbour City 0.", "en") == "Kilburn 2, Harbour City nil."
    assert aloud("the 90th minute, 10 shots, 0.5 seconds", "en") == "the 90th minute, 10 shots, 0.5 seconds"
    assert aloud("Harbour City gana 1 a 0", "es") == "Harbour City gana 1 a 0"


def test_var_is_spelt_out_letter_by_letter():
    ssml = build_ssml("VAR is checking. Varga waits.", "en-US", "v", "neutral", set())
    assert '<say-as interpret-as="characters">VAR</say-as> is checking. Varga waits.' in ssml
    assert "El uve a erre revisa." in build_ssml("El VAR revisa.", "es-MX", "v", "neutral", set())
