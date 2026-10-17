"""The News agent: the last fortnight of club news, written before kick-off.

The rival needs more to talk about than the match in front of it: a record
signing who hasn't scored, a manager under pressure, an injury crisis, how
the last few meetings went. The News agent writes that as a set of short
stories about the two clubs, consistent with the world (squads, table, form).
Everything is fictional, as the hackathon rules require.

It runs while the rival is still finding out who you support. If the model is
slow or unavailable, the stories from its last good run are used instead (or
data/news.json, shipped with the project), so the build-up never waits on it.
``python -m kickoff news`` refreshes the shipped stories.
"""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from pathlib import Path
from typing import Literal

from agent_framework import Agent
from pydantic import BaseModel, Field

from .agents.llm import ClientFactory, options_for
from .data_store import DATA_DIR
from .models import Match, World

log = logging.getLogger(__name__)

CACHE = DATA_DIR / "news.json"

INSTRUCTIONS = """You are the News agent for a fictional football league. Write the last two weeks of news about two clubs before they play each other: the kind of things rival fans wind each other up about.

Write 16 short stories, a mix of:
- transfer: what a player cost (in pounds), whether he's been worth it, a deal that fell through
- form: a player's goals or mistakes over recent games, a run of results
- injury: who is out or struggling (only players NOT in the starting XI can be out)
- manager: pressure on the manager, a tactical row, a rant at the press
- history: exactly ONE story, with club "both": the last three meetings, with scores and a memorable moment each. The most recent one is given in the facts; keep it exactly.
- fans: a chant, a banner, a ticket-price row
- finance: wages, a stadium move, a sponsor

Rules:
- Use only the clubs and players you are given; invent manager names. Everything is fictional: never mention real clubs, real players, real people or the Premier League.
- Stay consistent with the facts given (league position, form, players' traits, the last meeting).
- Name people correctly: a story about the manager names the manager, never a player.
- Football only. Nothing about anyone's private life, background or appearance; nothing cruel; no violent language.
- Each headline is under 12 words. Each detail is one or two sentences, with numbers where they help (fees, goals, minutes).
- Balance it: roughly half the stories about each club, some good news and some bad for each."""


class Story(BaseModel):
    club: Literal["home", "away", "both"] = Field(description="Which club the story is about")
    kind: Literal["transfer", "form", "injury", "manager", "history", "fans", "finance"]
    headline: str
    detail: str


class Dossier(BaseModel):
    stories: list[Story]


def brief(world: World, match: Match) -> str:
    """The facts the News agent must stay consistent with."""
    lines = [f"League: {world.league}, season {world.season}, after {world.matchweek - 1} matchweeks."]
    seed = DATA_DIR / "previous_meeting.json"
    if seed.exists():
        last = json.loads(seed.read_text(encoding="utf-8"))
        lines.append(f"The last meeting: {last['result']}, {last['when']}.")
    for side, cid in (("home", match.home), ("away", match.away)):
        club = world.club(cid)
        position = next(i for i, row in enumerate(world.table, 1) if row.club == club.name)
        row = world.table[position - 1]
        xi = [club.player(pid) for pid in club.starting_xi]
        bench = [p for p in club.squad if p.id not in club.starting_xi]
        describe = lambda ps: "; ".join(f"{p.name} ({p.position.value}, age {p.age}: {', '.join(p.traits) or '-'})"
                                        for p in ps)
        lines += [
            f"\n{side.upper()}: {club.name} ('{club.nickname}'), {club.city}, stadium {club.stadium}.",
            f"Style: {club.style.summary}",
            f"Table: {position}th of {len(world.table)}, {row.points} pts, W{row.won} D{row.drawn} L{row.lost}, "
            f"goals {row.goals_for}-{row.goals_against}, last five {' '.join(row.form)} (oldest first).",
            f"Starting XI: {describe(xi)}",
            f"Rest of the squad: {describe(bench)}",
        ]
    return "\n".join(lines)


async def write_news(world: World, match: Match, clients: ClientFactory) -> Dossier:
    """Ask the News agent for the stories; raises if it fails or returns nothing usable."""
    client = clients("news")
    agent = Agent(client, INSTRUCTIONS, name="news",
                  default_options=options_for(client, {"response_format": Dossier, "temperature": 0.9}))
    reply = await agent.run(brief(world, match))
    if not isinstance(reply.value, Dossier) or len(reply.value.stories) < 6:
        raise ValueError("The News agent returned no usable stories")
    return reply.value


LAST = "news-last.json"  # the latest stories the agent wrote, kept with the saved conversations


def last_good(saved: Path) -> Dossier:
    """The latest stories the agent wrote, or the ones shipped with the project."""
    for path in (saved / LAST, CACHE):
        with suppress(Exception):
            return Dossier.model_validate_json(path.read_text(encoding="utf-8"))
    return Dossier(stories=[])


def save(dossier: Dossier, path: Path = CACHE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dossier.model_dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
