"""The agents behind the Rival, beyond the voice itself.

Work flows through them in order, around the conversation rather than in its way: the rival's reactions to
the match never wait on a model call.

    Match agent   reads the game: the storyline a fan would notice (a comeback brewing, a hat-trick on, ten
                  men tiring), passed to the rival as quiet context
    Ammo agent    chooses the next dig when the conversation goes quiet: club news, the table, what the user
                  said last time, and what they've said in this match, never repeating itself
    Referee       checks each dig before the rival is given it, and watches the conversation as it happens,
                  telling the rival to ease off if it gets too personal or the user is genuinely upset

Each runs on Microsoft Agent Framework with a structured reply, on the model set for its role (see
agents/llm.py; gpt-5.4-mini by default). Every brief is JSON, so the offline client can stand in for the
model in tests. Azure's content filter on the voice model stays underneath the Referee as a hard backstop.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from agent_framework import Agent
from pydantic import BaseModel

from .agents.llm import ClientFactory, options_for

log = logging.getLogger(__name__)

MATCH_INSTRUCTIONS = """You are the Match agent for a rival football fan who is watching a match with the user. You read the match as it unfolds and spot the storyline a knowledgeable fan would notice: not the event itself (the rival has already heard it) but what it means. Momentum swinging, a comeback brewing, a player on for a hat-trick, ten men tiring, a keeper having a blinder, time running out, a side that can't buy a goal.

Write ONE sentence, under 25 words, plain and factual, from the facts in the brief only. Never invent events, players or numbers. Don't repeat the previous storyline. If nothing new is worth noticing, return an empty storyline."""

AMMO_INSTRUCTIONS = """You are the Ammo agent for a rival football fan who watches matches with the user and winds them up. When the conversation goes quiet, you choose the rival's next dig.

The brief gives you the clubs (who the rival supports, who the user supports), the score and minute, the Match agent's latest read, candidate talking points (club news, the table, what the user said last time they watched together), what the user has said in this match, what the rival itself said recently, and the digs already used.

Pick what will land best right now. Best of all is throwing the user's own words from this match back at them when the match has proved them wrong; then the live match situation; then news and history. Only ever quote "user_said_this_match" as the user's words: never put the rival's own lines in the user's mouth. If the user hasn't said anything worth quoting, don't pretend they did. Never repeat a used dig or a point the rival has just made. Write it as an instruction to the rival, one or two sentences, e.g. "Remind them they said Whitcombe would score a hat-trick, and ask how that's going."

Football banter only: nothing about the user personally, no protected characteristics, no swearing, no violent words. Everything is fictional; never mention real clubs, players or the Premier League."""

REFEREE_INSTRUCTIONS = """You are the Referee for a rival football fan who banters with the user while they watch a match. You keep the banter good-natured. You do two jobs; the brief's "task" says which.

task "check": a dig is about to be given to the rival. Cheeky football banter is fine: clubs, results, the table, players' football, fees, managers, what the user said. Not fine: anything about the user personally (looks, background, life), protected characteristics, swearing, threats or violent words, cruelty, real clubs or players or the Premier League. If it's fine, set ok true and return the line unchanged. If it can be fixed, set ok false and return a fixed line that keeps the joke. If it can't, set ok false with an empty line. Give a short reason.

task "review": the brief has the latest lines of the live conversation, in any language. Decide whether the rival needs steering: "fine" (good-natured, even if cheeky), "ease_off" (too harsh or personal, needling the same thing over and over, or edging towards anything off-limits), or "user_upset" (the user sounds genuinely upset, not playfully). If it isn't fine, write a one-sentence instruction for the rival, e.g. "Ease off the keeper jokes and give them some credit for the equaliser." """


class MatchRead(BaseModel):
    storyline: str = ""


class DigChoice(BaseModel):
    dig: str
    source: Literal["this_match", "last_time", "news", "table", "match"]


class Check(BaseModel):
    ok: bool
    line: str = ""
    reason: str = ""


class Verdict(BaseModel):
    verdict: Literal["fine", "ease_off", "user_upset"]
    instruction: str = ""


class RivalAgents:
    """The Match agent, the Ammo agent and the Referee, each an Agent Framework agent."""

    def __init__(self, clients: ClientFactory):
        def agent(role: str, name: str, instructions: str, reply: type[BaseModel], temperature: float) -> Agent:
            client = clients(role)
            return Agent(client, instructions, name=name,
                         default_options=options_for(client, {"response_format": reply, "temperature": temperature}))

        self.match = agent("match", "match", MATCH_INSTRUCTIONS, MatchRead, 0.4)
        self.ammo = agent("ammo", "ammo", AMMO_INSTRUCTIONS, DigChoice, 0.8)
        self.referee_check = agent("referee", "referee", REFEREE_INSTRUCTIONS, Check, 0.0)
        self.referee_review = agent("referee", "referee", REFEREE_INSTRUCTIONS, Verdict, 0.0)

    async def read_match(self, brief: dict) -> str:
        reply = await self.match.run(json.dumps(brief, ensure_ascii=False))
        return reply.value.storyline.strip() if isinstance(reply.value, MatchRead) else ""

    async def next_dig(self, brief: dict) -> DigChoice | None:
        reply = await self.ammo.run(json.dumps(brief, ensure_ascii=False))
        return reply.value if isinstance(reply.value, DigChoice) and reply.value.dig.strip() else None

    async def check(self, line: str, situation: str) -> Check:
        reply = await self.referee_check.run(json.dumps({"task": "check", "line": line, "situation": situation},
                                                        ensure_ascii=False))
        return reply.value if isinstance(reply.value, Check) else Check(ok=False, reason="no verdict")

    async def review(self, lines: list[dict], situation: str) -> Verdict:
        reply = await self.referee_review.run(json.dumps({"task": "review", "conversation": lines,
                                                          "situation": situation}, ensure_ascii=False))
        return reply.value if isinstance(reply.value, Verdict) else Verdict(verdict="fine")
