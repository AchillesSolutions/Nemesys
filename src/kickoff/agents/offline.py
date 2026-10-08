"""An offline stand-in for the language model.

It reads the same JSON brief the real model gets and answers with simple
templates, in English only. It exists so the full pipeline can run in tests,
in CI and on a laptop with no keys; it is not meant to sound good.
"""

from __future__ import annotations

import json
import re

from agent_framework import BaseChatClient, ChatResponse, FunctionInvocationLayer, Message


def _brief(messages: list[Message]) -> dict:
    text = messages[-1].text
    if "Brief:\n" in text:
        text = text.split("Brief:\n", 1)[1]
    return json.loads(text)


def _teacher(b: dict) -> dict:
    fact, depth = b["rule_fact"], b["depth"]
    if depth == "full":
        text, priority = fact, 8
    elif depth == "reminder":
        text, priority = f"Remember: {fact.split('. ')[0].rstrip('.')}.", 5
    else:
        text, priority = f"{b['concept_name']} again.", 3
    return {"proposals": [{"text": text, "priority": priority, "concept": b["concept"]}]}


def _storyteller(b: dict) -> dict:
    kinds = {s["kind"]: s for s in b.get("signals", [])}
    if "takes_the_lead" in kinds or "equaliser" in kinds:
        text, priority = "That goal changes the whole feel of this match.", 8
    elif "numerical_advantage" in kinds:
        text, priority = "With a player more, the other side now has a spare player to attack with.", 8
    elif "possession_dominance" in kinds:
        text, priority = "One side is keeping the ball for long spells, but having the ball is not the same as scoring.", 5
    else:
        text, priority = "Both managers will be thinking about what comes next.", 3
    return {"proposals": [{"text": text, "priority": priority, "concept": None}]}


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _league_guide(b: dict) -> dict:
    stakes = {k: v for k, v in b["if_score_stands"].items() if isinstance(v, dict)}
    club, move = max(stakes.items(), key=lambda kv: abs(kv[1]["position_before"] - kv[1]["position_if_score_stands"]))
    before, after = move["position_before"], move["position_if_score_stands"]
    if before == after:
        text = f"If it stays like this, {club} would stay {_ordinal(before)} in the table."
    else:
        text = f"If it stays like this, {club} would move from {_ordinal(before)} to {_ordinal(after)} in the table."
    return {"proposals": [{"text": text, "priority": 4, "concept": None}]}


def _producer(b: dict, prompt: str) -> dict:
    max_lines = int(re.search(r"at most (\d+) line", prompt).group(1))
    ranked = sorted(b["proposals"], key=lambda p: -p["priority"])
    chosen = ranked[:max_lines]
    defer = [p["id"] for p in ranked[max_lines:] if p["kind"] in ("stakes", "story")]
    return {
        "lines": [{"text": p["text"], "tone": "neutral", "card_title": p["kind"].title(), "proposal_ids": [p["id"]]}
                  for p in chosen],
        "defer_ids": defer,
        "reasoning": "Offline producer: highest priority first.",
    }


class OfflineChatClient(FunctionInvocationLayer, BaseChatClient):
    def __init__(self, role: str):
        super().__init__()
        self.role = role
        self.calls = 0

    async def _respond(self, messages: list[Message]) -> ChatResponse:
        self.calls += 1
        brief = _brief(messages)
        match self.role:
            case "teacher":
                body = _teacher(brief)
            case "storyteller":
                body = _storyteller(brief)
            case "league_guide":
                body = _league_guide(brief)
            case "producer":
                body = _producer(brief, messages[-1].text)
            case _:
                body = {"proposals": []}
        return ChatResponse(messages=[Message("assistant", [json.dumps(body)])])

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        return self._respond(list(messages))
