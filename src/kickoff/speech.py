"""Spoken commentary with Azure AI Speech.

Each language has a neural voice; the Producer's tone (excited, tense,
disappointed ...) becomes one of that voice's speaking styles where the
voice supports it. Supported styles differ per voice, so they are read
from the service's voice list rather than assumed.

Configured with AZURE_SPEECH_REGION and AZURE_SPEECH_KEY. A Microsoft
Foundry (multi-service) resource includes Speech, so its key is used when
no separate Speech key is set. Without a region the endpoint reports itself
unavailable and the browser falls back to its own voices. Voices can be
changed with KICKOFF_VOICE_EN / _ZH / _ES.
"""

from __future__ import annotations

import os
import re
import time
from collections import OrderedDict
from xml.sax.saxutils import escape

import httpx

# (locale, default voice) per language. The MAI-Voice "Flash" voices share one wide set of emotions
# (excited, hopeful, regretful, determined ...) and are tuned for low latency; Yunjian has dedicated
# football-commentary styles in Mandarin.
VOICES = {
    "en": ("en-US", "en-US-Olivia:MAI-Voice-2.1-Flash"),
    "zh": ("zh-CN", "zh-CN-YunjianNeural"),
    "es": ("es-MX", "es-MX-Valeria:MAI-Voice-2.1-Flash"),
}

# For each tone, the styles to try in order; the first one the voice supports wins.
TONE_STYLES = {
    "excited": ["sports-commentary-excited", "excited", "joyful", "cheerful"],
    "hopeful": ["hopeful", "encouraging", "cheerful"],
    "tense": ["anxious", "determined", "serious", "sports-commentary"],
    "disappointed": ["regretful", "disappointed", "saddisappointed", "sad"],
    "calm": ["calm", "narration-relaxed", "reflective", "chat"],
    "neutral": ["sports-commentary", "newscast", "chat"],
}
TONE_RATE = {"excited": "+22%", "hopeful": "+16%", "tense": "+14%", "disappointed": "+6%", "calm": "+8%", "neutral": "+13%"}

CACHE_SIZE = 400


class SpeechUnavailable(RuntimeError):
    pass


class Speech:
    def __init__(self, key: str | None = None, region: str | None = None):
        self.key = key if key is not None else os.getenv("AZURE_SPEECH_KEY") or os.getenv("AZURE_OPENAI_API_KEY")
        self.region = region if region is not None else os.getenv("AZURE_SPEECH_REGION")
        self.voices = {lang: (locale, os.getenv(f"KICKOFF_VOICE_{lang.upper()}", voice))
                       for lang, (locale, voice) in VOICES.items()}
        self._styles: dict[str, set[str]] | None = None
        self._cache: OrderedDict[tuple[str, str, str], bytes] = OrderedDict()
        self._token: tuple[str, float] | None = None

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
            wanted = {voice for _, voice in self.voices.values()}
            self._styles = {v["ShortName"]: set(v.get("StyleList") or []) for v in r.json() if v["ShortName"] in wanted}
        return self._styles

    async def token(self) -> str:
        """A ten-minute authorisation token for the browser's speech recogniser."""
        if not self.available:
            raise SpeechUnavailable("Azure AI Speech is not configured")
        if self._token and self._token[1] > time.monotonic():
            return self._token[0]
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"https://{self.region}.api.cognitive.microsoft.com/sts/v1.0/issueToken",
                                  headers={"Ocp-Apim-Subscription-Key": self.key})
            r.raise_for_status()
        self._token = (r.text, time.monotonic() + 9 * 60)
        return r.text

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
            locale, voice = self.voices.get(language, self.voices["en"])
            ssml = build_ssml(aloud(text, language), locale, voice, tone, styles.get(voice, set()))
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


NOUGHT = re.compile(r"(?<![\w.,])0(?![\w%]|[.,]\d)")  # a lone 0: not 10, 0.5, 0% or 0th


def aloud(text: str, language: str) -> str:
    """How a line is said rather than written: in English commentary a zero in a score is "nil"
    ("1-0" is "one-nil", "0-0" is "nil-nil"); the text on screen keeps the digits."""
    return NOUGHT.sub("nil", text) if language == "en" else text


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
