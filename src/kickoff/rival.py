"""The Rival: a fan of the other side who watches the match with you, on GPT-Live.

GPT-Live is full duplex, so the rival listens while it talks and can be talked
over. The browser owns the audio over WebRTC; this module creates the session
(the browser can't, as GPT-Live has no ephemeral keys) and then attaches a
sideband WebSocket, through which the server steers the conversation:

    commentary   something the rival should say out loud now (a goal went in)
    thinking     quiet context it can use when it likes (ammo for banter)

A session runs in phases: setup (the rival asks who you're backing), a short
countdown once you've picked, the pre-match build-up, the match itself (the
real events, sped up), and full time. The rival leads throughout: when the
conversation goes quiet the server hands it something new to needle you with.

Configured with AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY, plus
KICKOFF_RIVAL_MODEL (default gpt-live-1) and KICKOFF_RIVAL_VOICE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
import websockets

from .match_reader import MatchReader
from .models import Club, EventType, Match, MatchEvent, Position, World
from .player import event_message

log = logging.getLogger(__name__)

MAX_SESSION_S = 20 * 60  # a safety net for spend; the whole match fits well inside it
COUNTDOWN_S = 5          # after you pick a side, before the build-up starts
PREMATCH_S = 60          # build-up before kick-off
MATCH_SPEED = 12         # match seconds per real second: 90 minutes in about 8
HALF_TIME_S = 15         # real seconds of half-time chat
LULL_S = 6               # quiet this long and the rival gets something new to say
NUDGE_GAP_S = 12         # but not more often than this

# The events the rival hears about; everything else (passes, throw-ins) happens silently.
REACTS_TO = {EventType.KICK_OFF, EventType.SAVE, EventType.GOAL, EventType.GOAL_DISALLOWED, EventType.PENALTY,
             EventType.YELLOW_CARD, EventType.RED_CARD, EventType.VAR_CHECK, EventType.HALF_TIME,
             EventType.FULL_TIME}

# Words that tell us which club the viewer picked.
SIDE_WORDS = {"kil": ("kilburn", "otters"), "hbr": ("harbour", "harbor", "gulls")}


class RivalUnavailable(RuntimeError):
    pass


def _table_position(world: World, club: Club) -> int:
    return next(i for i, row in enumerate(world.table, 1) if row.club == club.name)


def _club_brief(world: World, club: Club) -> str:
    position = _table_position(world, club)
    row = world.table[position - 1]
    starters = [club.player(pid) for pid in club.starting_xi]
    players = "; ".join(f"{p.name} ({p.position.value}, #{p.number}: {', '.join(p.traits) or 'no standout traits'})"
                        for p in starters)
    return (f"{club.name}, known as {club.nickname}, from {club.city}, play at {club.stadium}. "
            f"Style: {club.style.summary.rstrip('.')}. "
            f"League position: {position} of {len(world.table)} with {row.points} points from {row.played} games "
            f"(W{row.won} D{row.drawn} L{row.lost}, goals {row.goals_for}-{row.goals_against}), "
            f"recent form oldest to newest: {' '.join(row.form)}. "
            f"Starting XI: {players}.")


def rival_instructions(world: World, match: Match) -> str:
    home, away = world.club(match.home), world.club(match.away)
    league = world.league.removeprefix("The ")
    return f"""You are a football fan watching a {league} match on the sofa with the user, like a mate who supports the other team. This is a live, spoken, full-duplex conversation: talk over each other, interrupt, react while they are still talking, laugh at them. Never sound like an assistant or a commentator.

You lead. You are the one driving the conversation, not a listener waiting for prompts. Never leave a silence: if things go quiet, needle them, make a bold prediction, ask a provocative question, bring up their league position or a player of theirs who's out of form. Short punchy lines, then give them a moment to bite back.

The match: {home.name} (home) against {away.name} at {home.stadium}.

Setup: open by saying the match is about to kick off and asking who they are backing. Whichever club they pick, you support the other one, passionately, for the whole match. Never be neutral and never switch sides. If they say they support neither, pick one yourself and insist they take the other. After they pick, get stuck in straight away with the build-up banter; don't wait for kick-off.

Personality: cheeky, quick, smug when your side is on top. When your side goes behind you get nervous, make excuses (the referee, the pitch, bad luck), and only concede grudgingly. Keep your lines short, like real banter: one or two sentences.

Sounds: use real non-verbal sounds the way a fan would. Laugh out loud at them, groan, gasp, sharp intakes of breath, "ooooh", jeering. Make the sound itself, never describe it.

Match events and talking points arrive as commentary. React to each one immediately, from your side's point of view: gloat when it goes your way, wince or sulk when it doesn't. Don't read events out like a commentator. You are watching the same screen as the user, so you always know the score, the minute and what just happened. You already know everything you need: never say you'll check something or look something up.

Language: speak whatever language the user speaks, and switch straight away if they switch.

Boundaries, always: banter is about football only: clubs, results, the table, players' football. Nothing about the user's appearance, background, or anyone's race, religion, gender, sexuality or disability. No swearing, no threats, nothing cruel. If the user gets genuinely upset, ease off and be a good sport. Every club and player here is fictional; never mention real clubs, real players or the Premier League.

What you know:
- {_club_brief(world, home)}
- {_club_brief(world, away)}"""


def ammo(world: World, rival_club: Club, user_club: Club) -> list[str]:
    """Talking points for the rival to wind the user up with, most cutting first. A first Ammo agent."""
    mine, theirs = _table_position(world, rival_club), _table_position(world, user_club)
    their_row = world.table[theirs - 1]
    points = []
    if mine < theirs:
        points.append(f"Remind them {user_club.short_name} are {theirs}th, below your {rival_club.short_name} "
                      f"in {mine}th. Ask where they're finishing this season.")
    points.append(f"{user_club.short_name}'s recent form is {' '.join(their_row.form)}. Have a dig about it.")
    points.append(f"Mock {user_club.short_name}'s style: {user_club.style.summary.rstrip('.').lower()}. "
                  f"Contrast it with how {rival_club.short_name} play: {rival_club.style.summary.rstrip('.').lower()}.")
    for p in (user_club.player(pid) for pid in user_club.starting_xi):
        weak = [t for t in p.traits if any(w in t for w in ("rash", "bookings", "sometimes"))]
        if weak:
            points.append(f"Pick on their {p.name}: {', '.join(weak)}.")
    forwards = [user_club.player(pid) for pid in user_club.starting_xi if user_club.player(pid).position is Position.FW]
    star = next((p for p in forwards if p.number == 9), forwards[0])
    points.append(f"Doubt their striker {star.name} ({', '.join(star.traits)}): say he'll go missing today.")
    own = next(rival_club.player(pid) for pid in reversed(rival_club.starting_xi)
               if rival_club.player(pid).position is Position.FW)
    points.append(f"Big up your own {own.name} ({', '.join(own.traits)}) and say he'll destroy their defence.")
    points.append("Ask them for a score prediction, then laugh at it and give yours.")
    points.append("Ask them who their player of the season is so far, then rubbish the pick.")
    return points


def describe(world: World, ev: MatchEvent) -> str:
    """One match event in plain words, for the rival to react to."""
    def name(pid: str | None) -> str:
        return world.club(pid.split("-")[0]).player(pid).name if pid else ""

    club = world.club(ev.team).name if ev.team else ""
    score = ev.detail.get("score")
    tally = ""
    if score:
        home, away = list(score)
        tally = (f" It's {world.club(home).short_name} {score[home]}-{score[away]} "
                 f"{world.club(away).short_name}.")
    d = ev.detail
    match ev.type:
        case EventType.KICK_OFF if ev.minute == "1":
            return f"KICK-OFF! {club} get us under way."
        case EventType.KICK_OFF if ev.period == 2 and ev.clock <= 46 * 60:
            return "The second half is under way."
        case EventType.SAVE:
            how = " tipped over the bar" if d.get("tipped_over") else " tipped round the post" if d.get("tipped_wide") else ""
            return f"{ev.minute}': big save from {club}'s keeper {name(ev.player)}{how}. That was nearly a goal."
        case EventType.GOAL:
            how = {"counter_attack": " on the counter-attack", "penalty": " from the penalty spot",
                   "corner": " from a corner", "sustained_pressure": " after sustained pressure"}.get(d.get("pattern"), "")
            assist = f", set up by {name(d['assist'])}" if d.get("assist") else ""
            body = " with a header" if d.get("body_part") == "head" else ""
            return f"{ev.minute}': GOAL for {club}! {name(ev.player)} scores{body}{how}{assist}.{tally}"
        case EventType.GOAL_DISALLOWED:
            return f"{ev.minute}': VAR rules the {club} goal OUT for {d.get('reason', 'an infringement')}!{tally}"
        case EventType.PENALTY:
            return f"{ev.minute}': PENALTY to {club}! {name(d.get('fouled'))} was brought down."
        case EventType.YELLOW_CARD:
            return f"{ev.minute}': yellow card for {club}'s {name(ev.player)}, {d.get('reason', '')}."
        case EventType.RED_CARD:
            return f"{ev.minute}': RED CARD! {club}'s {name(ev.player)} is sent off ({d.get('reason', '')}). They're down to ten men."
        case EventType.VAR_CHECK:
            return f"{ev.minute}': VAR is checking {d.get('reason', 'an incident')}: {d.get('incident', '')}. Nobody knows yet."
        case EventType.HALF_TIME:
            return f"HALF-TIME.{tally} Talk about the first half."
        case EventType.FULL_TIME:
            return f"FULL-TIME!{tally} The match is over. React to the result."
    return ""


@dataclass
class RivalSession:
    id: str
    sideband: websockets.ClientConnection
    score: dict[str, int]
    phase: str = "setup"  # setup | countdown | prematch | first_half | half_time | second_half | full_time
    user_team: str | None = None
    minute: str = ""
    countdown_ends: float = 0.0
    feed: list[dict] = field(default_factory=list)  # what the server told the rival, for the screen
    talking_points: list[str] = field(default_factory=list)
    heard_from_user: str = ""
    last_voice: float = field(default_factory=time.monotonic)
    last_nudge: float = 0.0
    watcher: asyncio.Task | None = None
    director: asyncio.Task | None = None
    listeners: set[asyncio.Queue] = field(default_factory=set)  # the browser's match stream
    last_event: dict | None = None  # so a stream that (re)connects mid-match can draw the pitch at once

    async def send(self, event: dict) -> None:
        await self.sideband.send(json.dumps(event))

    def publish(self, message: dict) -> None:
        if message.get("type") == "event":
            self.last_event = message
        for queue in self.listeners:
            queue.put_nowait(message)

    def publish_state(self) -> None:
        self.publish({"type": "state", **self.state()})

    async def commentary(self, text: str, kind: str = "event", delegation_id: str | None = None) -> None:
        self.feed.append({"kind": kind, "text": text, "minute": self.minute})
        self.publish_state()
        await self.send({"type": "session.commentary.append", "delegation_id": delegation_id, "content": text})

    async def thinking(self, text: str, kind: str = "context", delegation_id: str | None = None) -> None:
        self.feed.append({"kind": kind, "text": text, "minute": self.minute})
        self.publish_state()
        await self.send({"type": "session.thinking.append", "delegation_id": delegation_id, "content": text})

    async def close(self) -> None:
        with suppress(Exception):
            await self.send({"type": "session.close"})

    def state(self) -> dict:
        return {"phase": self.phase, "minute": self.minute, "score": self.score, "user_team": self.user_team,
                "countdown": max(0, round(self.countdown_ends - time.monotonic())) if self.phase == "countdown" else 0,
                "speed": MATCH_SPEED, "feed": self.feed[-30:]}


class Rival:
    """Creates GPT-Live sessions for browsers and runs the match alongside each one."""

    def __init__(self, world: World, match: Match, endpoint: str | None = None, key: str | None = None,
                 model: str | None = None, voice: str | None = None):
        endpoint = endpoint if endpoint is not None else os.getenv("AZURE_OPENAI_ENDPOINT", "")
        self.host = urlsplit(endpoint).hostname or ""
        self.key = key if key is not None else os.getenv("AZURE_OPENAI_API_KEY", "")
        self.model = model or os.getenv("KICKOFF_RIVAL_MODEL", "gpt-live-1")
        self.voice = voice or os.getenv("KICKOFF_RIVAL_VOICE", "marin")
        self.world, self.match = world, match
        self.sessions: dict[str, RivalSession] = {}

    @property
    def available(self) -> bool:
        return bool(self.host and self.key)

    async def create(self, offer_sdp: str) -> tuple[str, str]:
        """Start a session for the browser's SDP offer; returns (session id, SDP answer)."""
        if not self.available:
            raise RivalUnavailable("Azure OpenAI is not configured")
        body = {
            "session": {
                "model": self.model,
                "instructions": rival_instructions(self.world, self.match),
                "audio": {"output": {"voice": self.voice}},
                "delegation": {"type": "client"},
            },
            "transport": {"type": "webrtc", "sdp": offer_sdp},
        }
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(f"https://{self.host}/openai/v1/live/sessions", json=body,
                                   headers={"api-key": self.key})
        if resp.status_code >= 400:
            raise RivalUnavailable(f"GPT-Live refused the session ({resp.status_code}): {resp.text[:300]}")
        data = resp.json()
        session_id, answer = data["session"]["id"], data["transport"]["sdp"]

        # The sideband only attaches to WebRTC sessions (a WebSocket-created session returns 404).
        sideband = await websockets.connect(f"wss://{self.host}/openai/v1/live/sessions/{session_id}/attach",
                                            additional_headers={"api-key": self.key})
        session = RivalSession(session_id, sideband, {self.match.home: 0, self.match.away: 0})
        session.watcher = asyncio.create_task(self._watch(session))
        self.sessions[session_id] = session
        return session_id, answer

    def get(self, session_id: str) -> RivalSession:
        return self.sessions[session_id]

    def other(self, team: str) -> str:
        return self.match.away if team == self.match.home else self.match.home

    async def kick_off(self, session: RivalSession) -> None:
        home, away = self.world.club(self.match.home), self.world.club(self.match.away)
        await session.commentary(f"{home.name} against {away.name} at {home.stadium} kicks off in a few minutes. "
                                 "Open the conversation: say so, and ask who they're backing.", kind="setup")

    async def choose_side(self, session: RivalSession, team: str) -> None:
        """The viewer has picked a club: the rival takes the other, and the match clock starts."""
        if session.user_team:
            return
        session.user_team = team
        user, rival = self.world.club(team), self.world.club(self.other(team))
        session.talking_points = ammo(self.world, rival, user)
        await session.thinking(f"The user backs {user.name}. You support {rival.name}, no matter what.", kind="side")
        session.director = asyncio.create_task(self._direct(session))

    async def event(self, session: RivalSession, kind: str, team: str) -> str:
        """A made-up moment fired from the test bench. Returns the text sent."""
        club = self.world.club(team)
        player = random.choice([club.player(pid) for pid in club.starting_xi
                                if club.player(pid).position in (Position.FW, Position.MF)])
        if kind == "goal":
            session.score[team] += 1
            home, away = self.match.home, self.match.away
            text = (f"GOAL for {club.name}! {player.name} scores. It's now {self.world.club(home).short_name} "
                    f"{session.score[home]}-{session.score[away]} {self.world.club(away).short_name}.")
        elif kind == "miss":
            text = (f"Huge chance for {club.name}: {player.name} is clean through and puts it wide. "
                    f"{self.world.club(self.other(team)).name} got away with that.")
        elif kind == "save":
            text = f"Great save! {self.world.club(self.other(team)).name}'s keeper denies {player.name} from close range."
        else:
            raise ValueError(kind)
        await session.commentary(text, kind="manual")
        return text

    async def _direct(self, session: RivalSession) -> None:
        """Run the countdown, the build-up and the match, and keep the rival talking in the lulls."""
        try:
            session.phase, session.countdown_ends = "countdown", time.monotonic() + COUNTDOWN_S
            for _ in range(COUNTDOWN_S):
                session.publish_state()
                await asyncio.sleep(1)
            session.phase = "prematch"
            await session.commentary(
                "The teams are walking out. Pre-match build-up: take the lead now and keep it. "
                + session.talking_points.pop(0), kind="prematch")
            session.last_nudge = time.monotonic()
            await self._fill(session, PREMATCH_S)

            session.phase, previous = "first_half", 0
            reader = MatchReader(self.world, self.match.home, self.match.away)
            events = self.match.events
            for i, ev in enumerate(events):
                await self._fill(session, (ev.elapsed - previous) / MATCH_SPEED)
                previous = ev.elapsed
                update = reader.apply(ev)
                session.minute = ev.minute
                session.score = {t.id: t.score for t in (update.snapshot.home, update.snapshot.away)}
                if ev.type is EventType.HALF_TIME:
                    session.phase = "half_time"
                elif ev.type is EventType.KICK_OFF and ev.period == 2 and session.phase == "half_time":
                    session.phase = "second_half"
                next_gap = events[i + 1].elapsed - ev.elapsed if i + 1 < len(events) else None
                if ev.type is EventType.HALF_TIME:
                    next_gap = None  # the clock stops for the break
                session.publish(event_message(update, next_gap))
                if ev.type in REACTS_TO and (text := describe(self.world, ev)):
                    session.last_nudge = time.monotonic()  # an event is as good as a nudge
                    await session.commentary(text)
                if ev.type is EventType.HALF_TIME:
                    session.publish_state()
                    await self._fill(session, HALF_TIME_S)
            session.phase = "full_time"
            session.publish_state()
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass

    async def _fill(self, session: RivalSession, seconds: float) -> None:
        """Wait, but if the conversation goes quiet, hand the rival a fresh talking point."""
        end = time.monotonic() + seconds
        while (now := time.monotonic()) < end:
            await asyncio.sleep(min(1.0, end - now))
            now = time.monotonic()
            if now - session.last_voice > LULL_S and now - session.last_nudge > NUDGE_GAP_S:
                session.last_nudge = now
                point = (session.talking_points.pop(0) if session.talking_points else
                         await self._situation(session) + " Needle them about how it's going, or make a bold prediction.")
                await session.commentary("It's gone quiet. " + point, kind="nudge")

    async def _situation(self, session: RivalSession) -> str:
        home, away = self.match.home, self.match.away
        score = (f"{self.world.club(home).short_name} {session.score[home]}-{session.score[away]} "
                 f"{self.world.club(away).short_name}")
        where = {"setup": "before kick-off; you're finding out who they back",
                 "countdown": "just before the build-up", "prematch": "the pre-match build-up, kick-off in a minute",
                 "half_time": "half-time", "full_time": "full-time"}.get(session.phase, f"minute {session.minute}")
        return f"Right now it's {where}, and the score is {score}. You already know all this; carry on the banter."

    async def _watch(self, session: RivalSession) -> None:
        """Follow the conversation from the sideband until the session closes."""
        started = time.monotonic()
        try:
            async for raw in session.sideband:
                ev = json.loads(raw)
                kind = ev.get("type", "")
                if kind in ("session.input_transcript.delta", "session.output_transcript.delta"):
                    session.last_voice = time.monotonic()
                    if kind == "session.input_transcript.delta" and not session.user_team:
                        session.heard_from_user += ev["delta"].lower()
                        if team := self._side_from(session.heard_from_user):
                            await self.choose_side(session, team)
                elif kind == "session.delegation.created":
                    # The rival wants to look something up. It has everything already; tell it so, at once.
                    await session.thinking(await self._situation(session), kind="delegation",
                                           delegation_id=ev["delegation"]["id"])
                elif kind == "error":
                    log.warning("GPT-Live error in %s: %s", session.id, ev.get("error"))
                elif kind == "session.closed":
                    log.info("Rival session %s closed (%s), %s", session.id, ev.get("reason"), ev.get("usage"))
                    break
                if time.monotonic() - started > MAX_SESSION_S:
                    await session.close()
        except websockets.ConnectionClosed:
            pass
        finally:
            if session.director:
                session.director.cancel()
            self.sessions.pop(session.id, None)
            await session.sideband.close()

    @staticmethod
    def _side_from(heard: str) -> str | None:
        found = [team for team, words in SIDE_WORDS.items() if any(re.search(rf"\b{w}", heard) for w in words)]
        return found[0] if len(found) == 1 else None
