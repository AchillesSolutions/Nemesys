"""An offline stand-in for the language model.

It reads the same JSON brief each of the rival's agents gets and answers with
simple templates, in English only. It exists so the full pipeline can run in tests,
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


class OfflineChatClient(FunctionInvocationLayer, BaseChatClient):
    def __init__(self, role: str):
        super().__init__()
        self.role = role
        self.calls = 0

    async def _respond(self, messages: list[Message]) -> ChatResponse:
        self.calls += 1
        brief = _brief(messages)
        match self.role:
            case "match" if "final_score" in brief:  # the full-time report
                moments = [{"minute": m.split("'")[0], "what": m[:60], "why": "Offline: it changed the match.",
                            "what_en": m[:60], "why_en": "Offline: it changed the match."}
                           for m in brief.get("big_moments", [])[:3]]
                body = {"headline": f"Offline report: {brief['final_score']}", "headline_en": f"Offline report: {brief['final_score']}",
                        "moments": moments, "verdict": "Offline verdict.", "verdict_en": "Offline verdict.",
                        "bets": brief.get("bets", []), "bets_en": brief.get("bets", [])}
            case "match":
                body = {"storyline": f"Offline read: {brief.get('score', '')} after {brief.get('minute', '?')} minutes."}
            case "ammo" | "memory" if brief.get("task") == "pacts":
                lines = [l["said"] for l in brief.get("conversation", []) if " if " in f" {l['said'].lower()} "]
                body = {"pacts": list(dict.fromkeys(brief.get("noted", []) + lines))}
            case "ammo":
                used = set(brief.get("used", []))
                fresh = [c for c in brief.get("candidates", []) if c not in used]
                body = {"dig": fresh[0] if fresh else "Ask them for a score prediction.", "source": "news"}
            case "referee" if brief.get("task") == "user":
                rude = re.search(r"\b(idiot|moron|shut up|damn|bloody|kill you)\b", brief["said"].lower())
                body = {"card": bool(rude), "reason": "Personal abuse" if rude else ""}
            case "referee" if brief.get("task") == "check":
                body = {"ok": True, "line": brief["line"], "reason": "offline: passed"}
            case "referee":
                body = {"verdict": "fine", "instruction": ""}
            case "keeper":
                return ChatResponse(messages=[Message("assistant", [json.dumps(
                    {"nickname": "", "running_jokes": ["offline: the keeper"], "touchy_about": [], "best_moment": ""})])])
            case "news":
                body = {"stories": []}
            case _:
                body = {}
        return ChatResponse(messages=[Message("assistant", [json.dumps(body)])])

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        return self._respond(list(messages))
