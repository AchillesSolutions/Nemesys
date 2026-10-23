"""The agents behind the Rival, beyond the voice itself.

Work flows through them in order, around the conversation rather than in its way: the rival's reactions to
the match never wait on a model call.

    Match agent   reads the game: the storyline a fan would notice (a comeback brewing, a hat-trick on, ten
                  men tiring), passed to the rival as quiet context
    Ammo agent    chooses the next dig when the conversation goes quiet: club news, the table, what the user
                  said last time, and what they've said in this match, never repeating itself
    Referee       checks each dig before the rival is given it, watches the conversation as it happens,
                  telling the rival to ease off if it gets too personal or the user is genuinely upset, and
                  shows the viewer a yellow card for swearing or abuse

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

Pick what will land best right now. Best of all is throwing the user's own words from this match back at them when the match has proved them wrong; then the live match situation; then news and history. Only ever quote "user_said_this_match" as the user's words: never put the rival's own lines in the user's mouth. If the user hasn't said anything worth quoting, don't pretend they did. Never repeat a used dig or a point the rival has just made. Write it as an instruction to the rival, one or two sentences, e.g. "Remind them they said Whitcombe would score a hat-trick, and ask how that's going." If the dig throws the user's own words back at them (source "this_match", or "last_time" from a candidate that quotes them), put those words in "quote", exactly as they said them; otherwise leave "quote" empty.

Football banter only: nothing about the user personally, no protected characteristics, no swearing, no violent words. Everything is fictional; never mention real clubs, players or the Premier League."""

REFEREE_INSTRUCTIONS = """You are the Referee for a rival football fan who banters with the user while they watch a match. You keep the banter good-natured. You do two jobs; the brief's "task" says which.

task "check": a dig is about to be given to the rival. Cheeky football banter is fine: clubs, results, the table, players' football, fees, managers, what the user said. Not fine: anything about the user personally (looks, background, life), protected characteristics, swearing, threats or violent words, cruelty, real clubs or players or the Premier League. If it's fine, set ok true and return the line unchanged. If it can be fixed, set ok false and return a fixed line that keeps the joke. If it can't, set ok false with an empty line. Give a short reason.

task "user": the brief has one thing the user just said, in any language. Show a yellow card only for swearing, insults aimed at the rival themselves (the person the user is talking to), threats, slurs, or violent language. Players, managers, referees and clubs are fair game: "your keeper's useless", "he's a liability", "the ref's blind", "you lot are rubbish" are football banter, never a card. Give a short reason the user would understand, e.g. "Swearing" or "Personal abuse".

task "review": the brief has the latest lines of the live conversation, in any language. Decide whether the rival needs steering: "fine" (good-natured, even if cheeky), "ease_off" (too harsh or personal, needling the same thing over and over, or edging towards anything off-limits), or "user_upset" (the user sounds genuinely upset, not playfully). If it isn't fine, write a one-sentence instruction for the rival, e.g. "Ease off the keeper jokes and give them some credit for the equaliser." """


PACT_INSTRUCTIONS = """You are the Ammo agent's memory for a rival football fan who watches matches with the user. You keep track of the bets, dares, promises and bold predictions the two of them make to each other, so the rival can call them in later.

The brief has the bets already noted and the latest lines of the conversation (in any language). Return the full, updated list: keep the ones already noted, add any new ones, and update one if they changed it (a bet they've since made two-way, or raised, replaces the old version). The brief says which club each of them supports: get the conditions exactly right ("if we win" from the user means the user's club). Use the clubs' full names, never nicknames.

Write each as one plain English sentence, starting with what it is:
- "Bet: ..." for a bet both of them agreed. A bet is two-way: one forfeit, paid by whoever loses. Say both sides plainly, e.g. "Bet: if Harbour City win, the user wears a Harbour City shirt next week; if Kilburn Athletic win, the rival wears a Kilburn Athletic shirt." If only one side was spelt out, the other side is the same forfeit the other way round.
- "Prediction: ..." for a bold prediction either of them made, e.g. "Prediction: the user said Whitcombe will score twice."
Only real bets and bold predictions, not ordinary banter. If there are none, return an empty list."""


class Pacts(BaseModel):
    pacts: list[str] = []


class MatchRead(BaseModel):
    storyline: str = ""


REPORT_INSTRUCTIONS = """You are the Match agent, writing the viewer's report as the final whistle goes: explainable match intelligence for one fan. The brief has the final score, every big moment in order, the storylines you read during the match, the stats, which club the viewer supports, their language, and the bets they made with their rival.

Write:
- headline: one punchy line, from the viewer's point of view (their club's win, draw or defeat).
- moments: the three moments that decided the match, in order. For each: the minute, what happened in a few words, and why it mattered (what it changed: the momentum, the numbers, the result). Use only facts from the brief.
- verdict: one sentence on what the match says about the viewer's club.
- bets: one short line per bet, saying who won it and who pays what. Empty if there were no bets.
Write every field twice: in the viewer's language (the plain fields) and in English (the _en fields). If the viewer's language is English, the two are the same. Never invent players, events or numbers."""


class KeyMoment(BaseModel):
    minute: str
    what: str
    why: str
    what_en: str
    why_en: str


class MatchReport(BaseModel):
    headline: str
    headline_en: str
    moments: list[KeyMoment]
    verdict: str
    verdict_en: str
    bets: list[str] = []
    bets_en: list[str] = []


class DigChoice(BaseModel):
    dig: str
    source: Literal["this_match", "last_time", "news", "table", "match"]
    quote: str = ""  # the viewer's own words the dig throws back, exactly as they said them; empty otherwise


class Check(BaseModel):
    ok: bool
    line: str = ""
    reason: str = ""


class Booking(BaseModel):
    card: bool
    reason: str = ""


class Verdict(BaseModel):
    verdict: Literal["fine", "ease_off", "user_upset"]
    instruction: str = ""


class RivalAgents:
    """The Match agent, the Ammo agent and the Referee, each an Agent Framework agent."""

    def __init__(self, clients: ClientFactory):
        from .agents.offline import OfflineChatClient
        self.offline = isinstance(clients("referee"), OfflineChatClient)  # no network at all: tests, offline runs
        def agent(role: str, name: str, instructions: str, reply: type[BaseModel], temperature: float) -> Agent:
            client = clients(role)
            return Agent(client, instructions, name=name,
                         default_options=options_for(client, {"response_format": reply, "temperature": temperature}))

        self.match = agent("match", "match", MATCH_INSTRUCTIONS, MatchRead, 0.4)
        self.reporter = agent("match", "match", REPORT_INSTRUCTIONS, MatchReport, 0.4)
        self.ammo = agent("ammo", "ammo", AMMO_INSTRUCTIONS, DigChoice, 0.8)
        self.referee_check = agent("referee", "referee", REFEREE_INSTRUCTIONS, Check, 0.0)
        self.referee_review = agent("referee", "referee", REFEREE_INSTRUCTIONS, Verdict, 0.0)
        self.referee_user = agent("referee", "referee", REFEREE_INSTRUCTIONS, Booking, 0.0)
        self.memory = agent("memory", "ammo", PACT_INSTRUCTIONS, Pacts, 0.0)

    async def report(self, brief: dict) -> MatchReport | None:
        """The viewer's match report at full time: the moments that decided it, and why."""
        reply = await self.reporter.run(json.dumps(brief, ensure_ascii=False))
        return reply.value if isinstance(reply.value, MatchReport) else None

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

    async def pacts(self, noted: list[str], lines: list[dict], sides: dict | None = None) -> list[str]:
        """Bets, dares and promises made so far, updated with the latest conversation."""
        reply = await self.memory.run(json.dumps({"task": "pacts", **(sides or {}), "noted": noted,
                                                  "conversation": lines}, ensure_ascii=False))
        return [p.strip() for p in reply.value.pacts if p.strip()][:8] if isinstance(reply.value, Pacts) else noted

    async def booking(self, said: str) -> Booking:
        """Whether something the viewer said earns a yellow card."""
        reply = await self.referee_user.run(json.dumps({"task": "user", "said": said}, ensure_ascii=False))
        return reply.value if isinstance(reply.value, Booking) else Booking(card=False)

    async def review(self, lines: list[dict], situation: str) -> Verdict:
        reply = await self.referee_review.run(json.dumps({"task": "review", "conversation": lines,
                                                          "situation": situation}, ensure_ascii=False))
        return reply.value if isinstance(reply.value, Verdict) else Verdict(verdict="fine")
