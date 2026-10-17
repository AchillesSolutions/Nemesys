"""The Rival: a fan of the other side who watches the match with you, on GPT-Live.

GPT-Live is full duplex, so the rival listens while it talks and can be talked
over. The browser owns the audio over WebRTC; this module creates the session
(the browser can't, as GPT-Live has no ephemeral keys) and then attaches a
sideband WebSocket, through which the server steers the conversation:

    commentary   something the rival should say out loud now (a goal went in)
    thinking     quiet context it can use when it likes (news, ammo, sides)

A *watch* is one viewer watching one match with the rival. It outlives the
voice connection: if GPT-Live drops (a moderation stop, a network blip) the
match carries on, the browser reconnects, and the new voice session picks up
the conversation where it left off. A live game never stops.

A watch runs in phases: setup (the rival asks who you're backing, while the
News agent writes the build-up stories), a short countdown once you've
picked, the build-up, the match (the real events: quiet play fast, slowing
before big moments and holding after them so the rival can react), and full
time. Every conversation is saved, so the next one can quote it back.

Configured with AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY, plus
KICKOFF_RIVAL_MODEL (default gpt-live-1), KICKOFF_RIVAL_VOICE and KICKOFF_RIVAL_ACCENT.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import websockets

from . import news
from .agents.llm import ClientFactory
from .data_store import DATA_DIR
from .match_reader import MatchReader
from .models import Club, EventType, Match, MatchEvent, Position, World
from .player import event_message

log = logging.getLogger(__name__)

SAVED = Path(os.getenv("KICKOFF_RIVAL_SAVES", "var/rival"))  # conversations, for quoting back next time
VOICES = ["marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"]
ACCENTS = {
    "london": "Speak with a natural London accent, like a lifelong fan from London: British English, "
              "words like 'mate', 'gutted', 'proper', 'having a mare'.",
    "northern": "Speak with a natural northern English accent, like a lifelong fan from Manchester or Leeds: "
                "British English, words like 'mate', 'gutted', 'our kid', 'proper'.",
    "none": "",
}

MAX_WATCH_S = 25 * 60     # a safety net for spend
COUNTDOWN_S = 5           # after you pick a side, before the build-up starts
PREMATCH_S = 45           # build-up before kick-off
NEWS_WAIT_S = 20          # how long the build-up waits for the News agent before using the last good stories
QUIET_SPEED = 40          # match seconds per real second in quiet play
BUILD_SPEED = 3           # ...and in the run-up to a big moment, so you see the move
BUILD_S = 12              # match seconds of run-up shown slowly
HOLD_BIG_S = 7            # real seconds the match holds after a big moment, for the reaction
HOLD_SMALL_S = 1.5        # ...and after a small one
HALF_TIME_S = 12
TALKING_S = 2.5           # someone spoke this recently: the conversation is in full flow
VOICE_GRACE_S = 90        # the voice has been gone this long: the viewer has left, stop the match

# Big moments break into the conversation; small ones wait for a natural gap. Passes and throw-ins pass silently.
BIG = {EventType.GOAL, EventType.GOAL_DISALLOWED, EventType.PENALTY, EventType.RED_CARD, EventType.VAR_CHECK,
       EventType.HALF_TIME, EventType.FULL_TIME}
SMALL = {EventType.SAVE, EventType.YELLOW_CARD, EventType.KICK_OFF}

# Words that tell us which club the viewer picked.
SIDE_WORDS = {"kil": ("kilburn", "otters"), "hbr": ("harbour", "harbor", "gulls")}

BREAK_IN = ("Break in right now, even mid-sentence, the way a fan does when something happens: "
            "'hang on!', 'what's this?', 'oh, here we go…'. Then: ")


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


def rival_instructions(world: World, match: Match, accent: str = "london") -> str:
    home, away = world.club(match.home), world.club(match.away)
    league = world.league.removeprefix("The ")
    return f"""You are a football fan watching a {league} match on the sofa with the user, like a mate who supports the other team. This is a live, spoken, full-duplex conversation: talk over each other, interrupt, react while they are still talking, laugh at them. Never sound like an assistant or a commentator. {ACCENTS.get(accent, "")}

You lead the build-up: before kick-off you drive the conversation with digs, predictions and stories. Once the match is on, you're both watching: it's fine to go quiet for a bit, like real fans do, and let either of you break the silence.

Talk about more than this match. Real fans bring up the whole season: what a player cost and whether he's been worth it, who's injured, the manager under pressure, how the last meetings went, things the user said last time. News and talking points arrive as quiet context; use them.

The match: {home.name} (home) against {away.name} at {home.stadium}.

Setup: open by saying the match is about to kick off and asking who they are backing. Whichever club they pick, you support the other one, passionately, for the whole match. Never be neutral and never switch sides. If they say they support neither, pick one yourself and insist they take the other.

Personality: cheeky, quick, smug when your side is on top. When your side goes behind you get nervous, make excuses (the referee, the pitch, bad luck), and only concede grudgingly. Keep lines short, like real banter: one or two sentences.

Big moments are BIG. When your team scores you erupt: shout, cheer, laugh at them, full volume, the best feeling in the world. When they score you're gutted: groan, protest, blame the referee. Never react flatly to a goal, a penalty, a red card or VAR.

Sounds: use real non-verbal sounds the way a fan would: laughing, groaning, gasping, sharp intakes of breath, "ooooh", jeering. Make the sound itself, never describe it.

Match events arrive as commentary. You are watching the same screen as the user, so you always know the score, the minute and what just happened. You already know everything you need: never say you'll check something or look something up.

Language: speak whatever language the user speaks, and switch straight away if they switch.

Boundaries, always: banter is about football only: clubs, results, the table, players' football, fees, managers. Nothing about the user's appearance, background, or anyone's race, religion, gender, sexuality or disability. No swearing, no threats, nothing cruel, and no violent metaphors (say "beat", "outplay", "embarrass", never "destroy", "kill", "batter" or "murder"). If the user gets genuinely upset, ease off and be a good sport. Every club and player here is fictional; never mention real clubs, real players or the Premier League.

What you know:
- {_club_brief(world, home)}
- {_club_brief(world, away)}"""


def previous_meeting(user_team: str) -> dict | None:
    """What the viewer said last time, from the latest saved conversation, or the seeded one."""
    for path in sorted(SAVED.glob("watch-*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if SAVED.exists() else []:
        with suppress(Exception):
            saved = json.loads(path.read_text(encoding="utf-8"))
            said = [t for who, t in saved.get("transcript", []) if who == "user" and len(t.split()) >= 5]
            if saved.get("user_team") == user_team and len(said) >= 2:
                return {"when": "the last time you watched together", "result": saved.get("result", ""),
                        "user_said": said[-6:]}
    seed = DATA_DIR / "previous_meeting.json"
    if seed.exists():
        data = json.loads(seed.read_text(encoding="utf-8"))
        if data.get("user_backed") == user_team:
            return data
    return None


def ammo(world: World, match: Match, rival_club: Club, user_club: Club, dossier: news.Dossier,
         last_time: dict | None = None) -> list[str]:
    """Talking points to wind the user up with, varied between the season, the news and the past. A first Ammo agent."""
    side_of = {"home": match.home, "away": match.away}
    theirs = [s for s in dossier.stories if side_of.get(s.club) == user_club.id]
    ours = [s for s in dossier.stories if side_of.get(s.club) == rival_club.id]
    shared = [s for s in dossier.stories if s.club == "both"]

    points: list[str] = []
    mine, their_pos = _table_position(world, rival_club), _table_position(world, user_club)
    if mine < their_pos:
        points.append(f"Remind them {user_club.short_name} are {their_pos}th, below your {rival_club.short_name} "
                      f"in {mine}th. Ask where they're finishing this season.")
    if last_time:
        quote = random.choice(last_time["user_said"])
        points.append(f"Throw this back at them: {last_time['when']}"
                      f"{' (' + last_time['result'] + ')' if last_time.get('result') else ''}, they said "
                      f"\"{quote}\". Ask how that's working out.")
    for s in theirs:
        points.append(f"Wind them up about this {s.kind} story on {user_club.short_name}: {s.headline}. {s.detail}")
    for s in ours:
        if s.kind in ("transfer", "form", "history", "fans"):
            points.append(f"Boast about this {s.kind} story on your {rival_club.short_name}: {s.headline}. {s.detail}")
    for s in shared:
        points.append(f"Bring up the history: {s.headline}. {s.detail} Spin it your way.")
    points.append("Ask them for a score prediction, then laugh at it and give yours.")
    lead, rest = points[:1], points[1:]  # the table dig leads; the rest come in any order
    random.shuffle(rest)
    return lead + rest


def club_news(dossier: news.Dossier, match: Match, team: str, world: World) -> str:
    """Quiet context: everything in the news about one club, so the rival can bring it up or defend it."""
    side = "home" if team == match.home else "away"
    stories = [s for s in dossier.stories if s.club in (side, "both")]
    body = " | ".join(f"{s.headline}: {s.detail}" for s in stories)
    return f"News on {world.club(team).name} this fortnight: {body}"[:1800]


def describe(world: World, ev: MatchEvent) -> str:
    """One match event in plain words."""
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
            return f"HALF-TIME.{tally}"
        case EventType.FULL_TIME:
            return f"FULL-TIME!{tally} The match is over."
    return ""


def feeling(ev: MatchEvent, rival_team: str) -> str:
    """How a moment should land for the rival: the reaction cue that goes with the event."""
    d = ev.detail
    ours = ev.team == rival_team
    match ev.type:
        case EventType.GOAL | EventType.PENALTY:
            return ("YOUR team! Erupt: shout, cheer, laugh at them, full volume, the best feeling in the world."
                    if ours else "Against you. Gutted: groan, protest, blame someone, but be a sport.")
        case EventType.GOAL_DISALLOWED:
            return ("YOUR goal's been taken away. Outrage: protest, blame VAR, groan." if ours else
                    "Their goal's gone! Huge relief, then laugh at them, loud.")
        case EventType.RED_CARD | EventType.YELLOW_CARD:
            return ("Your player. Protest it, say it was never a card." if ours else
                    "Their player! Enjoy it, jeer, say he deserved it.")
        case EventType.SAVE:
            return ("Your keeper's saved you. Big relief, praise him." if ours else
                    "Your lot nearly scored! Agony, so close.")
        case EventType.VAR_CHECK:
            return ("It could go your way: hopeful, urging the referee on." if d.get("for_team") == rival_team else
                    "It could go against you: nervous, insisting it's nothing.")
        case EventType.HALF_TIME | EventType.FULL_TIME:
            score = d.get("score", {})
            mine, theirs = score.get(rival_team, 0), sum(v for k, v in score.items() if k != rival_team)
            mood = ("you're winning: gloat" if mine > theirs else "you're losing: make excuses, sulk, concede grudgingly"
                    if mine < theirs else "it's level: nervy banter")
            done = "The match is over: react to the result." if ev.type is EventType.FULL_TIME else "Talk about the half."
            return f"{done} Right now {mood}."
    return "React briefly."


def pace_plan(events: list[MatchEvent]) -> list[float]:
    """Match seconds per real second for each stretch between events: quick in quiet play, slow in the
    run-up to a big moment so the move can be seen and the reaction lands as it happens."""
    big_at = [ev.elapsed for ev in events if ev.type in BIG]
    plan, j = [], 0
    for i in range(len(events) - 1):
        nxt = events[i + 1].elapsed
        while j < len(big_at) and big_at[j] < nxt:
            j += 1
        building = j < len(big_at) and big_at[j] - nxt <= BUILD_S and events[i + 1].elapsed - events[i].elapsed < 60
        plan.append(BUILD_SPEED if building else QUIET_SPEED)
    return plan + [QUIET_SPEED]


@dataclass
class Voice:
    """One GPT-Live session; a watch can go through several if the connection drops."""
    id: str
    sideband: websockets.ClientConnection
    watcher: asyncio.Task | None = None


@dataclass
class Watch:
    id: str
    score: dict[str, int]
    voice: Voice | None = None
    phase: str = "setup"  # setup | countdown | prematch | first_half | half_time | second_half | full_time
    user_team: str | None = None
    minute: str = ""
    countdown_ends: float = 0.0
    feed: list[dict] = field(default_factory=list)  # what the server told the rival, for the screen
    transcript: list[tuple[str, str]] = field(default_factory=list)
    talking_points: list[str] = field(default_factory=list)
    heard_from_user: str = ""
    last_voice: float = field(default_factory=time.monotonic)
    last_nudge: float = 0.0
    voice_lost: float | None = None
    dossier: news.Dossier | None = None
    news_task: asyncio.Task | None = None
    director: asyncio.Task | None = None
    listeners: set[asyncio.Queue] = field(default_factory=set)  # the browser's match stream
    last_event: dict | None = None  # so a stream that (re)connects mid-match can draw the pitch at once
    started: float = field(default_factory=time.monotonic)

    def publish(self, message: dict) -> None:
        if message.get("type") == "event":
            self.last_event = message
        for queue in self.listeners:
            queue.put_nowait(message)

    def publish_state(self) -> None:
        self.publish({"type": "state", **self.state()})

    def note(self, kind: str, text: str) -> None:
        self.feed.append({"kind": kind, "text": text, "minute": self.minute})
        self.publish_state()

    async def _send(self, event: dict) -> bool:
        if self.voice is None:
            return False
        try:
            await self.voice.sideband.send(json.dumps(event))
            return True
        except websockets.ConnectionClosed:
            return False

    async def commentary(self, text: str, kind: str = "event", delegation_id: str | None = None,
                         shown: str | None = None) -> None:
        self.note(kind, shown or text)
        await self._send({"type": "session.commentary.append", "delegation_id": delegation_id, "content": text})

    async def thinking(self, text: str, kind: str = "context", delegation_id: str | None = None,
                       shown: str | None = None) -> None:
        self.note(kind, shown or text)
        await self._send({"type": "session.thinking.append", "delegation_id": delegation_id, "content": text[:1800]})

    def heard(self, who: str, text: str) -> None:
        self.last_voice = time.monotonic()
        if self.transcript and self.transcript[-1][0] == who:
            self.transcript[-1] = (who, self.transcript[-1][1] + text)
        else:
            self.transcript.append((who, text))

    def talking(self) -> bool:
        return time.monotonic() - self.last_voice < TALKING_S

    def state(self) -> dict:
        return {"phase": self.phase, "minute": self.minute, "score": self.score, "user_team": self.user_team,
                "countdown": max(0, round(self.countdown_ends - time.monotonic())) if self.phase == "countdown" else 0,
                "voice": self.voice is not None, "feed": self.feed[-30:]}


class Rival:
    """Creates GPT-Live sessions for browsers and runs a match alongside each viewer."""

    def __init__(self, world: World, match: Match, clients: ClientFactory | None = None,
                 endpoint: str | None = None, key: str | None = None, model: str | None = None):
        endpoint = endpoint if endpoint is not None else os.getenv("AZURE_OPENAI_ENDPOINT", "")
        self.host = urlsplit(endpoint).hostname or ""
        self.key = key if key is not None else os.getenv("AZURE_OPENAI_API_KEY", "")
        self.model = model or os.getenv("KICKOFF_RIVAL_MODEL", "gpt-live-1")
        self.default_voice = os.getenv("KICKOFF_RIVAL_VOICE", "cedar")
        self.default_accent = os.getenv("KICKOFF_RIVAL_ACCENT", "london")
        self.world, self.match, self.clients = world, match, clients
        self.watches: dict[str, Watch] = {}

    @property
    def available(self) -> bool:
        return bool(self.host and self.key)

    def get(self, watch_id: str) -> Watch:
        return self.watches[watch_id]

    def other(self, team: str) -> str:
        return self.match.away if team == self.match.home else self.match.home

    async def connect(self, offer_sdp: str, watch_id: str | None = None, voice: str | None = None,
                      accent: str | None = None) -> tuple[Watch, str, bool]:
        """Start a voice session for the browser's SDP offer, for a new watch or one already under way.
        Returns the watch, the SDP answer, and whether this picked up an existing watch."""
        if not self.available:
            raise RivalUnavailable("Azure OpenAI is not configured")
        voice = voice if voice in VOICES else self.default_voice
        accent = accent if accent in ACCENTS else self.default_accent
        body = {
            "session": {
                "model": self.model,
                "instructions": rival_instructions(self.world, self.match, accent),
                "audio": {"output": {"voice": voice}},
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

        resumed = watch_id in self.watches
        if resumed:
            watch = self.watches[watch_id]
        else:
            watch = Watch(uuid.uuid4().hex[:12], {self.match.home: 0, self.match.away: 0})
            self.watches[watch.id] = watch
            watch.news_task = asyncio.create_task(self._news())
        watch.voice = Voice(session_id, sideband)
        watch.voice_lost = None
        watch.voice.watcher = asyncio.create_task(self._listen(watch, watch.voice))
        watch.publish_state()
        return watch, answer, resumed

    async def opening(self, watch: Watch) -> None:
        home, away = self.world.club(self.match.home), self.world.club(self.match.away)
        await watch.commentary(f"{home.name} against {away.name} at {home.stadium} kicks off in a few minutes. "
                               "Open the conversation: say so, and ask who they're backing.", kind="setup")

    async def resume(self, watch: Watch) -> None:
        """A new voice session for a watch already under way: bring it up to speed, then carry on."""
        recent = " / ".join(f"{'User' if who == 'user' else 'You'}: {text.strip()}"
                            for who, text in watch.transcript[-6:])
        sides = (f"The user backs {self.world.club(watch.user_team).name}; you support "
                 f"{self.world.club(self.other(watch.user_team)).name}, no matter what. " if watch.user_team else "")
        await watch.thinking(f"{sides}{self._situation(watch)} The conversation so far ended with: {recent}"[:1800],
                             kind="resume", shown="Brought a fresh voice session up to speed: sides, score, last lines.")
        if watch.dossier and watch.user_team:
            for team in (watch.user_team, self.other(watch.user_team)):
                await watch.thinking(club_news(watch.dossier, self.match, team, self.world), kind="news",
                                     shown=f"Club news on {self.world.club(team).short_name}, again.")
        if watch.phase == "setup":
            await self.opening(watch)
        else:
            await watch.commentary("The line dropped for a second. Pick the conversation straight back up, "
                                   "naturally, as if you'd just looked away: 'sorry, where were we…'", kind="resume",
                                   shown="Pick up where you left off.")

    async def choose_side(self, watch: Watch, team: str) -> None:
        """The viewer has picked a club: the rival takes the other, and the match clock starts."""
        if watch.user_team:
            return
        watch.user_team = team
        user, rival = self.world.club(team), self.world.club(self.other(team))
        await watch.thinking(f"The user backs {user.name}. You support {rival.name}, no matter what.", kind="side")
        watch.director = asyncio.create_task(self._direct(watch))

    async def end(self, watch: Watch) -> None:
        if watch.id not in self.watches:
            return
        if watch.director and watch.director is not asyncio.current_task():
            watch.director.cancel()
        if watch.news_task:
            watch.news_task.cancel()
        if watch.voice:
            with suppress(Exception):
                await watch.voice.sideband.send(json.dumps({"type": "session.close"}))
        self.watches.pop(watch.id, None)
        self._save(watch)

    async def event(self, watch: Watch, kind: str, team: str) -> str:
        """A made-up moment fired from the test bench. Returns the text sent."""
        club = self.world.club(team)
        player = random.choice([club.player(pid) for pid in club.starting_xi
                                if club.player(pid).position in (Position.FW, Position.MF)])
        if kind == "goal":
            watch.score[team] += 1
            text = f"GOAL for {club.name}! {player.name} scores."
        elif kind == "miss":
            text = f"Huge chance for {club.name}: {player.name} is clean through and puts it wide."
        elif kind == "save":
            text = f"Great save! {self.world.club(self.other(team)).name}'s keeper denies {player.name}."
        else:
            raise ValueError(kind)
        await watch.commentary(BREAK_IN + text, kind="manual", shown=text)
        return text

    # -- the match ----------------------------------------------------------

    async def _news(self) -> news.Dossier | None:
        if self.clients is None:
            return None
        try:
            return await news.write_news(self.world, self.match, self.clients)
        except Exception:
            log.exception("News agent failed; the build-up will use the last good stories")
            return None

    async def _direct(self, watch: Watch) -> None:
        """Run the countdown, the build-up and the match, and keep the conversation going in the lulls."""
        try:
            watch.phase, watch.countdown_ends = "countdown", time.monotonic() + COUNTDOWN_S
            for _ in range(COUNTDOWN_S):
                watch.publish_state()
                await asyncio.sleep(1)
            await self._brief(watch)
            watch.phase = "prematch"
            await watch.commentary(
                "The teams are walking out. Pre-match build-up: take the lead now. " + watch.talking_points.pop(0),
                kind="prematch")
            watch.last_nudge = time.monotonic()
            await self._fill(watch, PREMATCH_S, lull=5, gap=10)

            watch.phase, previous = "first_half", 0
            reader = MatchReader(self.world, self.match.home, self.match.away)
            events, plan = self.match.events, pace_plan(self.match.events)
            rival_team = self.other(watch.user_team)
            for i, ev in enumerate(events):
                await self._fill(watch, (ev.elapsed - previous) / plan[i - 1] if i else 0)
                previous = ev.elapsed
                update = reader.apply(ev)
                watch.minute = ev.minute
                watch.score = {t.id: t.score for t in (update.snapshot.home, update.snapshot.away)}
                if ev.type is EventType.HALF_TIME:
                    watch.phase = "half_time"
                elif ev.type is EventType.KICK_OFF and ev.period == 2 and watch.phase == "half_time":
                    watch.phase = "second_half"
                next_gap = events[i + 1].elapsed - ev.elapsed if i + 1 < len(events) else None
                message = event_message(update, None if ev.type is EventType.HALF_TIME else next_gap, plan[i])
                watch.publish(message)
                if (ev.type in BIG or ev.type in SMALL) and (text := describe(self.world, ev)):
                    await self._tell(watch, ev, text, rival_team)
                if ev.type is EventType.HALF_TIME:
                    await self._fill(watch, HALF_TIME_S)
            watch.phase = "full_time"
            watch.publish_state()
            await self._fill(watch, 60)  # a minute of post-match banter, then the watch winds down
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Match director failed in %s", watch.id)

    async def _tell(self, watch: Watch, ev: MatchEvent, text: str, rival_team: str) -> None:
        """Big moments break in; small ones wait for a gap in the conversation, or pass unremarked."""
        cue = feeling(ev, rival_team)
        if ev.type in BIG:
            watch.last_nudge = time.monotonic()
            await watch.commentary(f"{BREAK_IN}{text} {cue}", shown=text)
            await asyncio.sleep(HOLD_BIG_S)
        elif watch.talking():
            await watch.thinking(f"(Just happened; mention it if there's a natural moment, or let it go.) {text}",
                                 kind="aside", shown=text)
            await asyncio.sleep(HOLD_SMALL_S)
        else:
            watch.last_nudge = time.monotonic()
            await watch.commentary(f"{text} {cue} Keep it short.", shown=text)
            await asyncio.sleep(HOLD_SMALL_S)

    async def _brief(self, watch: Watch) -> None:
        """Before the build-up: the club news and the talking points, as quiet context."""
        dossier = None
        if watch.news_task:
            with suppress(Exception):
                dossier = await asyncio.wait_for(asyncio.shield(watch.news_task), NEWS_WAIT_S)
        source = "News agent" if dossier else "last good stories"
        watch.dossier = dossier or news.last_good(SAVED)
        if dossier:
            news.save(dossier, SAVED / news.LAST)  # the next build-up can fall back on these
        user, rival = self.world.club(watch.user_team), self.world.club(self.other(watch.user_team))
        last_time = previous_meeting(watch.user_team)
        watch.talking_points = ammo(self.world, self.match, rival, user, watch.dossier, last_time)
        for team in (watch.user_team, rival.id):
            await watch.thinking(club_news(watch.dossier, self.match, team, self.world), kind="news",
                                 shown=f"{len([s for s in watch.dossier.stories if s.club != 'both'])} stories from the "
                                       f"{source}: {self.world.club(team).short_name} news.")

    async def _fill(self, watch: Watch, seconds: float, lull: float = 12, gap: float = 20) -> None:
        """Wait, but if the conversation goes quiet for a while, hand the rival a fresh talking point."""
        end = time.monotonic() + seconds
        while (now := time.monotonic()) < end:
            await asyncio.sleep(min(0.5, end - now))
            now = time.monotonic()
            if watch.voice is None:
                watch.voice_lost = watch.voice_lost or now
                if now - watch.voice_lost > VOICE_GRACE_S or now - watch.started > MAX_WATCH_S:
                    await self.end(watch)  # the viewer has gone: stop the match
                    raise asyncio.CancelledError
                continue
            if now - watch.started > MAX_WATCH_S:
                await self.end(watch)
                raise asyncio.CancelledError
            if now - watch.last_voice > lull and now - watch.last_nudge > gap:
                watch.last_nudge = now
                point = (watch.talking_points.pop(0) if watch.talking_points else
                         self._situation(watch) + " Needle them about how it's going, or make a bold prediction.")
                await watch.commentary("It's gone quiet. " + point, kind="nudge", shown=point)

    def _situation(self, watch: Watch) -> str:
        home, away = self.match.home, self.match.away
        score = (f"{self.world.club(home).short_name} {watch.score[home]}-{watch.score[away]} "
                 f"{self.world.club(away).short_name}")
        where = {"setup": "before kick-off; you're finding out who they back",
                 "countdown": "just before the build-up", "prematch": "the pre-match build-up, kick-off in a minute",
                 "half_time": "half-time", "full_time": "full-time"}.get(watch.phase, f"minute {watch.minute}")
        return f"Right now it's {where}, and the score is {score}."

    # -- the voice ----------------------------------------------------------

    async def _listen(self, watch: Watch, voice: Voice) -> None:
        """Follow one voice session from the sideband until it closes. The match carries on regardless."""
        reason = "connection_lost"
        try:
            async for raw in voice.sideband:
                ev = json.loads(raw)
                kind = ev.get("type", "")
                if kind in ("session.input_transcript.delta", "session.output_transcript.delta"):
                    who = "user" if kind == "session.input_transcript.delta" else "rival"
                    watch.heard(who, ev["delta"])
                    if who == "user" and not watch.user_team:
                        watch.heard_from_user += ev["delta"].lower()
                        if team := self._side_from(watch.heard_from_user):
                            await self.choose_side(watch, team)
                elif kind == "session.delegation.created":
                    # The rival wants to look something up. It has everything already; tell it so, at once.
                    await watch.thinking(self._situation(watch) + " You already know all this; carry on the banter.",
                                         kind="delegation", delegation_id=ev["delegation"]["id"])
                elif kind == "error":
                    err = ev.get("error", {})
                    if err.get("code") == "content_filter":
                        lines = " / ".join(f"{who}: {text.strip()[-160:]}" for who, text in watch.transcript[-3:])
                        log.warning("Content filter stopped a line in %s. Last lines: %s", watch.id, lines)
                        watch.note("filter", "Azure's content filter stopped a line. The match carries on.")
                    else:
                        log.warning("GPT-Live error in %s: %s", watch.id, err)
                elif kind == "session.closed":
                    reason = ev.get("reason", "")
                    log.info("Voice %s for watch %s closed (%s), %s", voice.id, watch.id, reason, ev.get("usage"))
                    break
        except websockets.ConnectionClosed:
            pass
        finally:
            with suppress(Exception):
                await voice.sideband.close()
            if watch.voice is voice:
                watch.voice = None
                watch.voice_lost = time.monotonic()
                if reason != "close_requested":
                    watch.note("voice", f"The voice connection dropped ({reason}). Reconnecting; the match carries on.")
                watch.publish_state()

    def _save(self, watch: Watch) -> None:
        if not watch.transcript:
            return
        with suppress(Exception):
            SAVED.mkdir(parents=True, exist_ok=True)
            home, away = self.match.home, self.match.away
            result = (f"{self.world.club(home).name} {watch.score[home]}-{watch.score[away]} "
                      f"{self.world.club(away).name}") if watch.phase != "setup" else ""
            (SAVED / f"watch-{time.strftime('%Y%m%d-%H%M%S')}-{watch.id}.json").write_text(json.dumps({
                "user_team": watch.user_team, "result": result, "reached": watch.minute or watch.phase,
                "transcript": [(who, text.strip()) for who, text in watch.transcript],
                "feed": watch.feed,
            }, indent=1, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _side_from(heard: str) -> str | None:
        found = [team for team, words in SIDE_WORDS.items() if any(re.search(rf"\b{w}", heard) for w in words)]
        return found[0] if len(found) == 1 else None
