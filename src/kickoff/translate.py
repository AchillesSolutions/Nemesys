"""Azure AI Translator, for the conversation window: anything said in another language, by the viewer or the
rival, is shown in English too, so someone watching (a judge, a friend) can follow a match enjoyed in Mandarin
or Spanish.

Uses the same Foundry (multi-service) key as speech: AZURE_TRANSLATOR_KEY, else AZURE_SPEECH_KEY, else
AZURE_OPENAI_API_KEY, with the resource's region (AZURE_TRANSLATOR_REGION, else AZURE_SPEECH_REGION).
"""

from __future__ import annotations

import os
from collections import OrderedDict

import httpx

ENDPOINT = "https://api.cognitive.microsofttranslator.com/translate"
CACHE_SIZE = 500


class TranslatorUnavailable(RuntimeError):
    pass


class Translator:
    def __init__(self, key: str | None = None, region: str | None = None):
        self.key = key if key is not None else (os.getenv("AZURE_TRANSLATOR_KEY") or os.getenv("AZURE_SPEECH_KEY")
                                                or os.getenv("AZURE_OPENAI_API_KEY", ""))
        self.region = region if region is not None else (os.getenv("AZURE_TRANSLATOR_REGION")
                                                         or os.getenv("AZURE_SPEECH_REGION", ""))
        self._cache: OrderedDict[tuple[str, str], tuple[str, str]] = OrderedDict()  # -> (translation, detected)

    @property
    def available(self) -> bool:
        return bool(self.key and self.region)

    async def to(self, language: str, texts: list[str]) -> list[tuple[str, str]]:
        """Each text in ``language`` (e.g. "en"), with the language it was detected as."""
        if not self.available:
            raise TranslatorUnavailable("Azure AI Translator is not configured")
        todo = [t for t in dict.fromkeys(texts) if (t, language) not in self._cache]
        if todo:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(ENDPOINT, params={"api-version": "3.0", "to": language},
                                      json=[{"Text": t} for t in todo],
                                      headers={"Ocp-Apim-Subscription-Key": self.key,
                                               "Ocp-Apim-Subscription-Region": self.region})
                r.raise_for_status()
            for text, result in zip(todo, r.json()):
                self._cache[(text, language)] = (result["translations"][0]["text"],
                                                 result.get("detectedLanguage", {}).get("language", ""))
                if len(self._cache) > CACHE_SIZE:
                    self._cache.popitem(last=False)
        return [self._cache[(t, language)] for t in texts]
