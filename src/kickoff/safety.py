"""Azure AI Content Safety: the Referee's first, fast gate.

Every line the viewer says is scored for hate, violence, sexual content and self-harm. Anything rated Medium or
worse earns a yellow card at once; the rest goes to the Referee's model, which tells banter from abuse. Content
Safety can't make that call alone: in our evaluation it rated "your keeper's useless" and "you're an idiot" the
same (Low), and missed Mandarin swearing entirely (see evals/RESULTS.md). It runs on the Foundry resource's own key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

API_VERSION = "2024-09-01"
SEVERE = 4  # Medium, on the four-level scale (0, 2, 4, 6)
NAMES = {"Hate": "Hate speech", "Violence": "Threats or violence", "Sexual": "Sexual language", "SelfHarm": "Self-harm"}


@dataclass
class Rating:
    worst: int = 0
    category: str = ""

    @property
    def severe(self) -> bool:
        return self.worst >= SEVERE

    @property
    def reason(self) -> str:
        return NAMES.get(self.category, self.category)


def endpoint() -> str | None:
    """The Content Safety endpoint: set outright, or the Foundry resource's own."""
    if explicit := os.getenv("AZURE_CONTENT_SAFETY_ENDPOINT"):
        return explicit.rstrip("/")
    foundry = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    if "//" not in foundry:
        return None
    name = foundry.split("//", 1)[1].split(".", 1)[0]
    return f"https://{name}.cognitiveservices.azure.com"


async def rate(text: str, timeout: float = 4) -> Rating | None:
    """How severe a line is, or None if Content Safety isn't configured or doesn't answer in time."""
    base, key = endpoint(), os.getenv("AZURE_CONTENT_SAFETY_KEY") or os.getenv("AZURE_OPENAI_API_KEY")
    if not base or not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{base}/contentsafety/text:analyze?api-version={API_VERSION}",
                                  headers={"Ocp-Apim-Subscription-Key": key},
                                  json={"text": text[:1000], "outputType": "FourSeverityLevels"})
            r.raise_for_status()
    except httpx.HTTPError:
        return None
    worst = max(r.json().get("categoriesAnalysis", []), key=lambda c: c.get("severity", 0), default=None)
    return Rating(worst["severity"], worst["category"]) if worst else Rating()
