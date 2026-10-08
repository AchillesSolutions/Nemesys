"""Spoken commentary with Azure AI Speech.

Each language has a neural voice; the Producer's tone (excited, tense,
disappointed ...) becomes one of that voice's speaking styles where the
voice supports it. Supported styles differ per voice, so they are read
from the service's voice list rather than assumed.

Configured with AZURE_SPEECH_KEY and AZURE_SPEECH_REGION. Without them the
endpoint reports itself unavailable and the browser falls back to its own
voices.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from xml.sax.saxutils import escape

import httpx

# (locale, voice) per language. Chosen for clarity and for the speaking styles they offer.
VOICES = {
    "en": ("en-GB", "en-GB-RyanNeural"),
    "zh": ("zh-CN", "zh-CN-YunjianNeural"),
    "es": ("es-MX", "es-MX-JorgeNeural"),
}

# For each tone, the styles to try in order; the first one the voice supports wins.
TONE_STYLES = {
    "excited": ["sports_commentary_excited", "excited", "cheerful"],
    "hopeful": ["hopeful", "cheerful", "friendly"],
    "tense": ["serious", "sports_commentary", "chat"],
    "disappointed": ["disappointed", "sad", "empathetic"],
    "calm": ["calm", "chat", "friendly"],
    "neutral": ["sports_commentary", "chat", "narration-professional"],
}
TONE_RATE = {"excited": "+8%", "hopeful": "+3%", "tense": "0%", "disappointed": "-6%", "calm": "-4%", "neutral": "0%"}

CACHE_SIZE = 400


class SpeechUnavailable(RuntimeError):
    pass


class Speech:
    def __init__(self, key: str | None = None, region: str | None = None):
        self.key = key or os.getenv("AZURE_SPEECH_KEY")
        self.region = region or os.getenv("AZURE_SPEECH_REGION")
        self._styles: dict[str, set[str]] | None = None
        self._cache: OrderedDict[tuple[str, str, str], bytes] = OrderedDict()

    @property
    def available(self) -> bool:
        return bool(self.key and self.region)

    @property
    def _base(self) -> str:
        return f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices"

    async def styles(self, client: httpx.AsyncClient) -> dict[str, set[str]]:
        """Speaking styles each of our voices supports, fetched once."""
        if self._styles is None:
            r = await client.get(f"{self._base}/voices/list", headers={"Ocp-Apim-Subscription-Key": self.key})
            r.raise_for_status()
            wanted = {voice for _, voice in VOICES.values()}
            self._styles = {v["ShortName"]: set(v.get("StyleList") or []) for v in r.json() if v["ShortName"] in wanted}
        return self._styles

    async def synthesise(self, text: str, language: str, tone: str = "neutral") -> bytes:
        """MP3 audio for one line of commentary."""
        if not self.available:
            raise SpeechUnavailable("Azure AI Speech is not configured")
        key = (text, language, tone)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        async with httpx.AsyncClient(timeout=15) as client:
            styles = await self.styles(client)
            locale, voice = VOICES.get(language, VOICES["en"])
            ssml = build_ssml(text, locale, voice, tone, styles.get(voice, set()))
            r = await client.post(f"{self._base}/v1", content=ssml.encode("utf-8"), headers={
                "Ocp-Apim-Subscription-Key": self.key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                "User-Agent": "kickoff",
            })
            r.raise_for_status()
        self._cache[key] = r.content
        if len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return r.content


def pick_style(tone: str, supported: set[str]) -> str | None:
    return next((s for s in TONE_STYLES.get(tone, TONE_STYLES["neutral"]) if s in supported), None)


def build_ssml(text: str, locale: str, voice: str, tone: str, supported: set[str]) -> str:
    body = f'<prosody rate="{TONE_RATE.get(tone, "0%")}">{escape(text)}</prosody>'
    style = pick_style(tone, supported)
    if style:
        body = f'<mstts:express-as style="{style}">{body}</mstts:express-as>'
    return (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="{locale}">'
        f'<voice name="{voice}">{body}</voice></speak>'
    )
