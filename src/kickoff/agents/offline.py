"""An offline stand-in for the language model.

It reads the same JSON brief the real model gets and answers with simple
templates, in English only. It exists so the full pipeline can run in tests,
in CI and on a laptop with no keys; it is not meant to sound good.
"""

from __future__ import annotations

import itertools
import json
import re

from agent_framework import BaseChatClient, ChatResponse, Content, FunctionInvocationLayer, Message

_call_ids = itertools.count(1)

# Keyword -> tool call for the offline Host. Real models decide this themselves.
_HOST_RULES = [
    (r"mandarin|chinese|中文|普通话", "set_language", {"language": "zh"}),
    (r"spanish|español|espanol", "set_language", {"language": "es"}),
    (r"\benglish\b", "set_language", {"language": "en"}),
    (r"support(?:ing)?\s+(\w+)", "set_team", None),
    (r"\bneutral\b", "set_team", {"team": "neutral"}),
    (r"just me|watching alone|on my own|by myself", "set_hands_free", {"on": True}),
    (r"friends are here|not alone anymore|people are here", "set_hands_free", {"on": False}),
    (r"\bnew\b|first (?:ever )?(?:football )?match|beginner", "set_help_level", {"level": "beginner"}),
    (r"analyst|tactic", "set_help_level", {"level": "analyst"}),
    (r"\bwhy\b|what happened|what was that", "recent_events", {"count": 5}),
    (r"who is|number (\d+)|\d+号", "player_info", None),
    (r"table|league|position", "league_table", {}),
    (r"score|winning|losing", "match_state", {}),
]


def _brief(messages: list[Message]) -> dict:
    text = messages[-1].text
    if "Brief:\n" in text:
        text = text.split("Brief:\n", 1)[1]
    return json.loads(text)


def _teacher(b: dict) -> dict:
    if "concepts" in b:  # starter points before kick-off: one per rule
        return {"proposals": [{"text": c["rule_fact"], "priority": 8, "concept": c["concept"]} for c in b["concepts"]]}
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
    if "fixture_notes" in b:
        text, priority = b["fixture_notes"][-1], 6
    elif "bridge" in kinds:
        gap = kinds["bridge"]["possession_in_gap"]
        leader = max(gap, key=gap.get)
        text, priority = f"Since then, {leader.upper()} have had {round(gap[leader])}% of the ball.", 5
    elif "takes_the_lead" in kinds or "equaliser" in kinds:
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
    if "if_home_win" in b:
        return {"proposals": [{"text": f"Both clubs are chasing a European place. {b['european_places']}", "priority": 7,
                               "concept": None}]}
    stakes = {k: v for k, v in b["if_score_stands"].items() if isinstance(v, dict)}
    club, move = max(stakes.items(), key=lambda kv: abs(kv[1]["position_before"] - kv[1]["position_if_score_stands"]))
    before, after = move["position_before"], move["position_if_score_stands"]
    if before == after:
        text = f"If it stays like this, {club} would stay {_ordinal(before)} in the table."
    else:
        text = f"If it stays like this, {club} would move from {_ordinal(before)} to {_ordinal(after)} in the table."
    return {"proposals": [{"text": text, "priority": 4, "concept": None}]}


def _host_calls(text: str) -> list[Content]:
    calls = []
    low = text.lower()
    for pattern, name, args in _HOST_RULES:
        m = re.search(pattern, low)
        if not m:
            continue
        if name == "set_team":
            args = args or {"team": m.group(1)}
        if name == "player_info":
            number = re.search(r"(\d+)", low)
            args = {"name": number.group(1) if number else low.split("who is", 1)[-1].strip(" ?")}
        calls.append(Content.from_function_call(f"call_{next(_call_ids)}", name, arguments=json.dumps(args)))
    return calls


def _host(messages: list[Message]) -> Message:
    last = messages[-1]
    if last.role == "tool":  # second round: answer from what the tools returned
        results = [str(c.result) for c in last.contents if c.type == "function_result"]
        return Message("assistant", [" ".join(results)[:400] or "Done."])
    question = last.text.split("The viewer says:", 1)[-1]
    calls = _host_calls(question)
    return Message("assistant", calls or ["(ignore)"])


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
        if self.role == "host":
            return ChatResponse(messages=[_host(messages)])
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
            case _:
                body = {"proposals": []}
        return ChatResponse(messages=[Message("assistant", [json.dumps(body)])])

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        return self._respond(list(messages))
