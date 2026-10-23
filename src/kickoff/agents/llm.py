"""Chat clients for each agent role, chosen by environment variables.

KICKOFF_LLM selects the provider:

* ``offline`` (default): no network. Template answers built from the brief,
  so the whole pipeline runs and tests pass without any keys.
* ``azure``: model deployments in Microsoft Foundry
  (AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY).

Each agent has its own model, matched to its job by evaluation (ROLE_MODELS below, and evals/README.md).
Any of them can be overridden with KICKOFF_MODEL_<ROLE> (e.g. KICKOFF_MODEL_MATCH); KICKOFF_MODEL is the
fallback for anything else.

Reasoning models (the GPT-5 and o-series) take a reasoning effort instead of
a temperature: KICKOFF_REASONING_EFFORT (default "low", to keep live latency down).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

from agent_framework import BaseChatClient

ClientFactory = Callable[[str], BaseChatClient]

# The rival's agents, each on the model that fits its job, chosen by evaluation (evals/RESULTS.md). Any of them can
# be overridden with KICKOFF_MODEL_<ROLE>, e.g. KICKOFF_MODEL_MATCH.
ROLE_MODELS = {
    "news": "gpt-5.5",        # writes the fortnight's club news once, before kick-off: quality over speed
    "match": "gpt-5.5",       # reads the stats into a storyline: the only candidates with every fact right
    "ammo": "gpt-5.4-mini",   # picks the next dig: wit and judgement, fast enough for a lull in conversation
    "memory": "gpt-5.4-nano", # notes bets and predictions: structured extraction, run often
    "keeper": "Phi-4",        # after the match, what to remember about the viewer: no one is waiting, small and private
    "referee": "gpt-5.4-mini",  # banter or abuse, after Content Safety's gate: on every line, so speed counts
}
REASONING_MODEL = re.compile(r"^(gpt-5|o\d)", re.IGNORECASE)


def load_env(path: Path = Path(".env")) -> None:
    """Minimal .env loader: KEY=value lines, existing variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def model_for(role: str) -> str:
    return os.getenv(f"KICKOFF_MODEL_{role.upper()}", "") or ROLE_MODELS.get(role) or os.environ.get("KICKOFF_MODEL", "")


def is_reasoning(model: str) -> bool:
    return bool(REASONING_MODEL.match(model))


def options_for(client: BaseChatClient, options: dict) -> dict:
    """Adapt an agent's chat options to its model: reasoning models get an effort, not a temperature."""
    model = getattr(client, "kickoff_model", "")
    if not is_reasoning(model):
        return options
    adapted = {k: v for k, v in options.items() if k != "temperature"}
    adapted["reasoning_effort"] = os.getenv("KICKOFF_REASONING_EFFORT", "low")
    return adapted


def client_factory(provider: str | None = None) -> ClientFactory:
    """Return a function giving the chat client for an agent role."""
    load_env()
    provider = (provider or os.getenv("KICKOFF_LLM", "offline")).lower()

    if provider == "offline":
        from .offline import OfflineChatClient
        return lambda role: OfflineChatClient(role)

    if provider != "azure":
        raise ValueError(f"Unknown KICKOFF_LLM provider: {provider}")

    from agent_framework.openai import OpenAIChatCompletionClient

    endpoint, key = os.environ["AZURE_OPENAI_ENDPOINT"], os.environ["AZURE_OPENAI_API_KEY"]
    v1_base = azure_v1_base_url(endpoint)
    clients: dict[str, BaseChatClient] = {}

    def client(role: str) -> BaseChatClient:
        model = model_for(role)
        if model not in clients:
            if v1_base:
                # Foundry's OpenAI-compatible v1 API: plain OpenAI client, no api-version.
                c = OpenAIChatCompletionClient(model=model, api_key=key, base_url=v1_base)
            else:
                c = OpenAIChatCompletionClient(model=model, azure_endpoint=endpoint, api_key=key,
                                               api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"))
            c.kickoff_model = model  # remembered so options can be matched to the model
            clients[model] = c
        return clients[model]

    return client


def azure_v1_base_url(endpoint: str) -> str | None:
    """Normalise a Foundry v1 endpoint as copied from the portal, or None for a classic endpoint.

    Accepts e.g. ``https://res.services.ai.azure.com/openai/v1/responses`` and returns
    ``https://res.services.ai.azure.com/openai/v1/``.
    """
    marker = "/openai/v1"
    if marker not in endpoint:
        return None
    return endpoint[: endpoint.index(marker) + len(marker)] + "/"
