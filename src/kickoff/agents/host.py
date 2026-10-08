"""The Host: the agent the viewer talks to.

The viewer speaks (or types) to Kick-Off: "I'm new to football and I support
Kilburn", "switch to Mandarin", "why was that goal ruled out?". The Host
answers in a sentence or two and uses tools to act and to look things up:
it changes the viewer's settings, reads the live match state and recent
events, looks up players and the table, and explains rules from the same
accurate rule facts the Teacher uses.

Tool calls go through Agent Framework's function calling, so the model
decides what to do; the tools themselves are plain, tested Python. The
same tools are what the MCP server exposes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal

from agent_framework import Agent, BaseChatClient, tool

from ..league import stakes
from ..match_reader import MatchReader
from ..models import Match, World
from .context import Describer
from .glossary import RULES
from .viewer import LANGUAGE_NAMES, ViewerProfile

INSTRUCTIONS = """
You are the Host of Kick-Off, a friendly companion that helps people enjoy a football match.
The viewer talks to you by voice. Reply in one or two short spoken sentences: no lists, no markdown, no emoji.

When the viewer states which team they support, which language they want, or how much they know,
call the matching setting tool ("I'm new" or "first match" means level "beginner").
Don't call setting tools when the viewer is only asking a question.

Look facts up before answering; never invent scores, names, statistics or rules:
- About something that just happened ("why", "what was that", "what happened"): call recent_events first,
  then explain_rule if a rule is involved, and answer from those facts, not with guesses.
- About the score, possession or cards: match_state. About a player: player_info. About the table: league_table.

Always reply in the viewer's language after any change you made (if they asked for Mandarin, reply in Mandarin).
Keep it family-friendly and never mention betting or odds.
""".strip()

LEVELS = Literal["beginner", "getting_there", "big_picture", "analyst"]


@dataclass
class HostReply:
    answer: str
    changes: dict = field(default_factory=dict)
    tools_used: list[str] = field(default_factory=list)


class Host:
    """One Host per viewer. ``get_profile``/``set_profile`` connect it to that viewer's session."""

    def __init__(self, client: BaseChatClient, world: World, match: Match,
                 get_profile: Callable[[], ViewerProfile], set_profile: Callable[..., ViewerProfile],
                 reader: Callable[[], MatchReader | None] = lambda: None,
                 played: Callable[[], int] = lambda: 0):
        self.world, self.match = world, match
        self.describer = Describer(world, match.home, match.away)
        self.get_profile, self.set_profile = get_profile, set_profile
        self.reader, self.played = reader, played
        self._turn: HostReply | None = None
        self.agent = Agent(client, INSTRUCTIONS, name="host", tools=self.tools())

    async def ask(self, question: str) -> HostReply:
        self._turn = HostReply(answer="")
        profile = self.get_profile()
        context = (f"Viewer settings: supports {self.describer.club(profile.team) or 'nobody (neutral)'}, "
                   f"language {LANGUAGE_NAMES[profile.language]}, level {profile.level}.\n"
                   f"The viewer says: {question}")
        response = await self.agent.run(context)
        self._turn.answer = (response.text or "").strip()
        return self._turn

    def _note(self, name: str, **changes) -> None:
        if self._turn is not None:
            self._turn.tools_used.append(name)
            self._turn.changes.update(changes)

    def _change(self, name: str, field: str, value) -> bool:
        """Apply one setting; only a real difference counts as a change."""
        current = getattr(self.get_profile(), field)
        if current == value:
            self._note(name)
            return False
        self.set_profile(**{field: value})
        self._note(name, **{field: value})
        return True

    # -- tools -------------------------------------------------------------------

    def tools(self) -> list:
        clubs = {c.id: c for c in self.world.clubs}

        @tool(name="set_team", description="Set which team the viewer supports, or neutral.")
        def set_team(team: Annotated[str, "The club's name, or 'neutral'"]) -> str:
            wanted = team.lower().strip(" .,!?")
            club = next((c for c in clubs.values() if wanted and any(
                wanted == c.id or wanted in name or name in wanted
                for name in (c.name.lower(), c.short_name.lower(), c.nickname.lower()))), None)
            self._change("set_team", "team", club.id if club else None)
            return f"Now supporting {club.name}." if club else "Now neutral."

        @tool(name="set_language", description="Change the language Kick-Off speaks and writes in.")
        def set_language(language: Annotated[Literal["en", "zh", "es"], "en English, zh Mandarin, es Spanish"]) -> str:
            self._change("set_language", "language", language)
            return f"Language set to {LANGUAGE_NAMES[language]}. Reply in that language."

        @tool(name="set_help_level", description="Change how much explanation the viewer gets.")
        def set_help_level(level: Annotated[LEVELS, "beginner: new to football; getting_there: knows the basics; "
                                                    "big_picture: story and stakes only; analyst: tactics and numbers"]) -> str:
            self._change("set_help_level", "level", level)
            return f"Help level set to {level}."

        @tool(name="match_state", description="The live score, minute, possession, shots and cards.")
        def match_state() -> str:
            self._note("match_state")
            reader = self.reader()
            if reader is None or reader.status == "pre_match":
                return (f"The match has not started. {self.describer.club_profile(self.match.home)} "
                        f"{self.describer.club_profile(self.match.away)}")
            return self.describer.snapshot_summary(reader.snapshot())

        @tool(name="recent_events", description="What happened most recently in the match, newest last.")
        def recent_events(count: Annotated[int, "How many events, 1 to 15"] = 8) -> str:
            self._note("recent_events")
            played = self.match.events[: self.played()]
            notable = [e for e in played if e.type.value not in ("pass", "throw_in", "goal_kick")]
            lines = [f"{e.minute}': {self.describer.event(e)}" for e in notable[-max(1, min(count, 15)):]]
            return "\n".join(lines) or "Nothing has happened yet."

        @tool(name="player_info", description="Look up a player by name or shirt number.")
        def player_info(name: Annotated[str, "Full name, surname or shirt number"]) -> str:
            self._note("player_info")
            q = name.lower().strip()
            for club in clubs.values():
                for p in club.squad:
                    if q in (p.name.lower(), str(p.number)) or q in p.name.lower().split():
                        reader = self.reader()
                        status = ""
                        if reader:
                            team = reader.teams[club.id]
                            status = (" Sent off." if p.id in team.sent_off else " On the pitch." if p.id in team.on_pitch
                                      else " Not on the pitch.") + (" On a yellow card." if p.id in team.booked else "")
                        return (f"{p.name}, number {p.number}, {p.position.value} for {club.name}, age {p.age}. "
                                f"Known for: {', '.join(p.traits)}.{status}")
            return f"No player called {name} in either squad."

        @tool(name="league_table", description="The league table and what the current score would mean.")
        def league_table() -> str:
            self._note("league_table")
            reader = self.reader()
            h = reader.teams[self.match.home].score if reader else 0
            a = reader.teams[self.match.away].score if reader else 0
            return (f"{self.world.league}, before this match:\n{self.describer.table(top=10)}\n"
                    f"If the score stays {h}-{a}: {stakes(self.world, self.match.home, self.match.away, h, a)}")

        @tool(name="explain_rule", description="The accurate rule fact for a football concept.")
        def explain_rule(concept: Annotated[str, "e.g. offside, penalty, var, red_card, corner, free_kick"]) -> str:
            self._note("explain_rule")
            key = concept.lower().replace(" ", "_").replace("-", "_")
            match = RULES.get(key) or next((v for k, v in RULES.items() if key in k or k in key), None)
            return match or f"No rule fact for {concept}. Known concepts: {', '.join(sorted(RULES))}."

        return [set_team, set_language, set_help_level, match_state, recent_events, player_info,
                league_table, explain_rule]
