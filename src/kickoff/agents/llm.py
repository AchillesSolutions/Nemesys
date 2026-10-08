"""Chat clients for each model provider, chosen by environment variables.

KICKOFF_LLM selects the provider:

* ``offline`` (default): no network. Template answers built from the brief,
  so the whole pipeline runs and tests pass without any keys.
* ``azure``: an Azure OpenAI deployment in Microsoft Foundry
  (AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, KICKOFF_MODEL = deployment name).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from agent_framework import BaseChatClient

ClientFactory = Callable[[str], BaseChatClient]


def load_env(path: Path = Path(".env")) -> None:
    """Minimal .env loader: KEY=value lines, existing variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def client_factory(provider: str | None = None) -> ClientFactory:
    """Return a function giving the chat client for an agent role."""
    load_env()
    provider = (provider or os.getenv("KICKOFF_LLM", "offline")).lower()

    if provider == "offline":
        from .offline import OfflineChatClient
        return lambda role: OfflineChatClient(role)

    from agent_framework.openai import OpenAIChatCompletionClient

    if provider == "azure":
        endpoint, key, model = (os.environ[k] for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "KICKOFF_MODEL"))
        v1_base = azure_v1_base_url(endpoint)
        if v1_base:
            # Foundry's OpenAI-compatible v1 API: plain OpenAI client, no api-version.
            client = OpenAIChatCompletionClient(model=model, api_key=key, base_url=v1_base)
        else:
            client = OpenAIChatCompletionClient(
                model=model, azure_endpoint=endpoint, api_key=key,
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"))
    else:
        raise ValueError(f"Unknown KICKOFF_LLM provider: {provider}")
    return lambda role: client


def azure_v1_base_url(endpoint: str) -> str | None:
    """Normalise a Foundry v1 endpoint as copied from the portal, or None for a classic endpoint.

    Accepts e.g. ``https://res.services.ai.azure.com/openai/v1/responses`` and returns
    ``https://res.services.ai.azure.com/openai/v1/``.
    """
    marker = "/openai/v1"
    if marker not in endpoint:
        return None
    return endpoint[: endpoint.index(marker) + len(marker)] + "/"
