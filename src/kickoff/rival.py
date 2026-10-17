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

A watch runs in phases. In the lobby a host voice (Azure AI Speech, not the
rival) asks who you support, then offers you a rival: the one you watched with
last time, or someone new, each a fan of the other club with their own home
town, accent and voice. The News agent writes the build-up stories meanwhile.
Then the rival joins, greets you, and the match plays continuously: quiet play
fast-forwarded, slowing for the run-up to each big moment and holding while
the rival reacts. The short pace fits the whole match in about a minute and a
half; the full pace takes about five. Every conversation is saved, so the next
one can quote it back.

Agents report what they're doing (news, match, ammo, referee) so the screen can
show them at work.

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
from .rival_agents import RivalAgents

log = logging.getLogger(__name__)

SAVED = Path(os.getenv("KICKOFF_RIVAL_SAVES", "var/rival"))  # conversations, for quoting back next time
VOICES = ["marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"]
ACCENTS = {
    "london": "Speak with a natural London accent: British English, words like 'mate', 'gutted', 'proper', 'having a mare'.",
    "northern": "Speak with a natural Manchester accent: British English, words like 'mate', 'gutted', 'our kid', 'proper'.",
    "west": "Speak with a natural Bristol, West Country accent: British English, words like 'mate', 'gert', 'gutted', 'proper'.",
    "mandarin": "Speak Mandarin Chinese (普通话) the whole time, like a lifelong fan from Shanghai or Beijing, with "
                "natural football slang. The user may talk to you in English: you understand them perfectly, but "
                "you always answer in Mandarin.",
    "spanish": "Speak Spanish the whole time, like a lifelong fan from Mexico City, with natural football slang "
               "('¡golazo!', '¡qué bárbaro!'). The user may talk to you in English: you understand them perfectly, "
               "but you always answer in Spanish.",
    "rioplatense": "Speak Spanish the whole time, like a lifelong fan from Buenos Aires, with natural football slang "
                   "('¡golazo!', 'che'). The user may talk to you in English: you understand them perfectly, but you "
                   "always answer in Spanish.",
    "midlands": "Speak with a natural East Midlands accent, from Leicester: British English, words like 'mate', 'me duck', 'gutted'.",
    "none": "",
}


@dataclass(frozen=True)
class Persona:
    """A rival the lobby can match you with: a fan of the other club, with a home town, accent and voice."""
    id: str
    name: str
    home: str
    accent: str
    voice: str
    club: str
    language: str = "English"  # rivals who speak another language say so in the lobby, and only speak it
    aliases: tuple[str, ...] = ()  # other things a viewer might say to pick them ("the Spanish one")

    def heard_in(self, text: str) -> bool:
        return any(re.search(rf"\b{w}\b", text) for w in (self.name.lower(), *self.aliases))


# Placeholder characters until the rival's personality is settled. The first for each club is the one the
# seeded "last time" memory remembers.
ROSTER = [
    Persona("dez", "Dez", "Bristol", "west", "cedar", "hbr"),
    Persona("kev", "Kev", "Manchester", "northern", "ash", "hbr"),
    Persona("chen", "Chen", "Shanghai", "mandarin", "verse", "hbr", "Mandarin", ("chan", "chinese", "mandarin")),
    Persona("carlos", "Carlos", "Mexico City", "spanish", "echo", "hbr", "Spanish", ("spanish", "mexico")),
    Persona("rob", "Rob", "Kilburn", "london", "ballad", "kil"),
    Persona("priya", "Priya", "Leicester", "midlands", "shimmer", "kil"),
    Persona("lin", "Lin", "Beijing", "mandarin", "sage", "kil", "Mandarin", ("lynn", "linn", "chinese", "mandarin")),
    Persona("sofia", "Sofía", "Buenos Aires", "rioplatense", "coral", "kil", "Spanish",
            ("sofia", "sophia", "spanish", "argentina")),
]
YES = re.compile(r"\b(yes|yeah|yep|yup|go on|rematch|sure|ok|okay|why not|same|let'?s go|him|her)\b")
NEW = re.compile(r"\b(new|someone else|somebody else|no|nah|different|other|another)\b")

MAX_WATCH_S = 25 * 60     # a safety net for spend
NEWS_WAIT_S = 20          # how long the build-up waits for the News agent before using the last good stories
TALKING_S = 2.5           # someone spoke this recently: the conversation is in full flow
READ_EVERY_S = 15 * 60     # the Match agent also reads the game every this many match seconds
REVIEW_EVERY_S = 10        # the Referee reviews the conversation at most this often (real seconds)
REVIEW_MIN_CHARS = 60      # ...once the rival has said at least this much more
VOICE_GRACE_S = 90        # the voice has been gone this long: the viewer has left, stop the match

# Big moments break into the conversation; small ones wait for a natural gap. Passes and throw-ins pass silently.
BIG = {EventType.GOAL, EventType.GOAL_DISALLOWED, EventType.PENALTY, EventType.RED_CARD, EventType.VAR_CHECK,
       EventType.HALF_TIME, EventType.FULL_TIME}
SMALL = {EventType.SAVE, EventType.YELLOW_CARD, EventType.KICK_OFF}


@dataclass(frozen=True)
class Pacing:
    """How fast a match plays. The clock always runs; it only changes speed."""
    quiet: float            # match seconds per real second in quiet play
    build: float            # ...and in the run-up to a big moment, so you see the move
    build_s: float          # match seconds of run-up shown slowly
    holds: dict             # real seconds the match holds after each kind of big moment, for the reaction
    small_hold: float       # ...and after a small moment the rival hears about
    tells_small: bool       # whether small moments (saves, bookings) are passed to the rival at all
    half_time: float        # real seconds of half-time
    greeting: float         # the rival's greeting and first dig, before kick-off
    after: float            # post-match banter before the watch winds down


SHORT = Pacing(quiet=240, build=3, build_s=6, small_hold=0, tells_small=False, half_time=3, greeting=10, after=20,
               holds={EventType.GOAL: 4, EventType.GOAL_DISALLOWED: 4, EventType.RED_CARD: 4,
                      EventType.VAR_CHECK: 2.5, EventType.PENALTY: 1.5, EventType.HALF_TIME: 3})
FULL = Pacing(quiet=40, build=3, build_s=12, small_hold=1.5, tells_small=True, half_time=12, greeting=45, after=60,
              holds={t: 7 for t in (EventType.GOAL, EventType.GOAL_DISALLOWED, EventType.PENALTY,
                                    EventType.RED_CARD, EventType.VAR_CHECK, EventType.HALF_TIME)})
PACING = {"short": SHORT, "full": FULL}

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


def rival_instructions(world: World, match: Match, accent: str = "london", user_team: str | None = None,
                       rematch: dict | None = None, persona: Persona | None = None) -> str:
    home, away = world.club(match.home), world.club(match.away)
    league = world.league.removeprefix("The ")
    if user_team:
        mine = world.club(match.away if user_team == match.home else match.home)
        name = f"{persona.name} from {persona.home}" if persona else "a fan"
        met = (f" You've watched with this person before: {rematch['when']} ({rematch['result']}), so greet them "
               "like an old rival: 'oh, it's YOU again'." if rematch else " You've never met.")
        who = (f"You are {name}, a lifelong {mine.name} fan. Kick-Off's rival lobby has just matched you with a "
               f"{world.club(user_team).name} fan to watch the match together, as rivals.{met} You support "
               f"{mine.name} passionately for the whole match; never be neutral and never switch sides.")
        setup = ("Setup: when you join, greet them in character straight away and get the first dig in. "
                 "Kick-off is seconds away, so keep the greeting short.")
    else:
        who = "You are a football fan watching the match on the sofa with the user, like a mate who supports the other team."
        setup = ("Setup: open by saying the match is about to kick off and asking who they are backing. Whichever club "
                 "they pick, you support the other one, passionately, for the whole match. Never be neutral and never "
                 "switch sides. If they say they support neither, pick one yourself and insist they take the other.")
    language = ("Language: speak whatever language the user speaks, and switch straight away if they switch."
                if persona is None or persona.language == "English" else
                f"Language: you always speak {persona.language}, whatever language the user uses. You understand "
                f"English perfectly. Club and player names stay as they are.")
    return f"""{who} It's a {league} match. This is a live, spoken, full-duplex conversation: talk over each other, interrupt, react while they are still talking, laugh at them. Never sound like an assistant or a commentator. {ACCENTS.get(accent, "")}

You lead the build-up: before kick-off you drive the conversation with digs, predictions and stories. Once the match is on, you're both watching: it's fine to go quiet for a bit, like real fans do, and let either of you break the silence.

Talk about more than this match. Real fans bring up the whole season: what a player cost and whether he's been worth it, who's injured, the manager under pressure, how the last meetings went, things the user said last time. News and talking points arrive as quiet context; use them.

The match: {home.name} (home) against {away.name} at {home.stadium}.

{setup}

Personality: cheeky, quick, smug when your side is on top. When your side goes behind you get nervous, make excuses (the referee, the pitch, bad luck), and only concede grudgingly. Keep lines short, like real banter: one or two sentences.

Big moments are BIG. When your team scores you erupt: shout, cheer, laugh at them, full volume, the best feeling in the world. When they score you're gutted: groan, protest, blame the referee. Never react flatly to a goal, a penalty, a red card or VAR.

Sounds: use real non-verbal sounds the way a fan would: laughing, groaning, gasping, sharp intakes of breath, "ooooh", jeering. Make the sound itself, never describe it.

Match events arrive as commentary. You are watching the same screen as the user, so you always know the score, the minute and what just happened. You already know everything you need: never say you'll check something or look something up.

{language}

Boundaries, always: banter is about football only: clubs, results, the table, players' football, fees, managers. Nothing about the user's appearance, background, or anyone's race, religion, gender, sexuality or disability. No swearing, no threats, nothing cruel, and no violent metaphors (say "beat", "outplay", "embarrass", never "destroy", "kill", "batter" or "murder"). If the user gets genuinely upset, ease off and be a good sport. Every club and player here is fictional; never mention real clubs, real players or the Premier League.

What you know:
- {_club_brief(world, home)}
- {_club_brief(world, away)}"""


def previous_meeting(user_team: str, rival: str | None = None) -> dict | None:
    """What the viewer said last time they watched with this rival: the latest saved conversation, or the
    seeded one. Without a rival, the last time with anyone."""
    for path in sorted(SAVED.glob("watch-*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if SAVED.exists() else []:
        with suppress(Exception):
            saved = json.loads(path.read_text(encoding="utf-8"))
            said = [t for who, t in saved.get("transcript", []) if who == "user" and len(t.split()) >= 5]
            if saved.get("user_team") == user_team and len(said) >= 2 and rival in (None, saved.get("rival")):
                return {"when": "the last time you watched together", "result": saved.get("result", ""),
                        "user_said": said[-6:], "rival": saved.get("rival")}
    seed = DATA_DIR / "previous_meeting.json"
    if seed.exists():
        data = json.loads(seed.read_text(encoding="utf-8"))
        if data.get("user_backed") == user_team and rival in (None, data.get("rival")):
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


def pace_plan(events: list[MatchEvent], pacing: Pacing = FULL) -> list[float]:
    """Match seconds per real second for each stretch between events: quick in quiet play, slow in the
    run-up to a big moment so the move can be seen and the reaction lands as it happens."""
    big_at = [ev.elapsed for ev in events if ev.type in BIG]
    plan, j = [], 0
    for i in range(len(events) - 1):
        nxt = events[i + 1].elapsed
        while j < len(big_at) and big_at[j] < nxt:
            j += 1
        building = (j < len(big_at) and big_at[j] - nxt <= pacing.build_s
                    and events[i + 1].elapsed - events[i].elapsed <= 20)
        plan.append(pacing.build if building else pacing.quiet)
    return plan + [pacing.quiet]


def match_seconds(events: list[MatchEvent], pacing: Pacing) -> float:
    """How long the match takes to play at this pace, in real seconds, holds included."""
    plan = pace_plan(events, pacing)
    play = sum((b.elapsed - a.elapsed) / pace for a, b, pace in zip(events, events[1:], plan))
    holds = sum(pacing.holds.get(ev.type, 0) for ev in events if ev.type in BIG)
    small = sum(pacing.small_hold for ev in events if ev.type in SMALL) if pacing.tells_small else 0
    return play + holds + small + pacing.half_time


MIN_FRAME_S = 0.25  # in fast play the pitch gets a new picture at most this often
ROUTINE = {EventType.PASS, EventType.INTERCEPTION, EventType.TACKLE, EventType.THROW_IN}


def frame_plan(events: list[MatchEvent], plan: list[float]) -> dict[int, tuple[int | None, float]]:
    """Which events the browser is sent, each with the match seconds to the next one sent and the pace in
    between. Fast-forwarded play would otherwise send dozens of passes a second, more than a browser can
    draw while it also plays the rival's voice; routine play is thinned to a few pictures a second, and
    anything that isn't routine is always sent. The Match reader still sees every event."""
    sent, waited = [0], 0.0
    for i in range(1, len(events)):
        waited += (events[i].elapsed - events[i - 1].elapsed) / plan[i - 1]
        if events[i].type not in ROUTINE or waited >= MIN_FRAME_S or i == len(events) - 1:
            sent.append(i)
            waited = 0.0
    frames: dict[int, tuple[int | None, float]] = {}
    for a, b in zip(sent, sent[1:]):
        gap = events[b].elapsed - events[a].elapsed
        real = sum((events[k + 1].elapsed - events[k].elapsed) / plan[k] for k in range(a, b))
        frames[a] = (gap, gap / real if real > 0 else plan[a])
    frames[sent[-1]] = (None, plan[sent[-1]])
    return frames


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
    mode: str = "short"  # short (the match in about a minute and a half) | full
    persona: Persona | None = None
    lobby: str = "side"  # side (which club?) | rival (which rival?) | done
    offered: list[str] = field(default_factory=list)  # rivals the host has offered so far
    phase: str = "lobby"  # lobby | prematch | first_half | half_time | second_half | full_time
    user_team: str | None = None
    minute: str = ""
    feed: list[dict] = field(default_factory=list)  # what the server told the rival, for the screen
    transcript: list[tuple[str, str]] = field(default_factory=list)
    talking_points: list[str] = field(default_factory=list)
    last_voice: float = field(default_factory=time.monotonic)
    last_nudge: float = 0.0
    voice_lost: float | None = None
    dossier: news.Dossier | None = None
    news_task: asyncio.Task | None = None
    director: asyncio.Task | None = None
    listeners: set[asyncio.Queue] = field(default_factory=set)  # the browser's match stream
    last_event: dict | None = None  # so a stream that (re)connects mid-match can draw the pitch at once
    started: float = field(default_factory=time.monotonic)
    voices: int = 0  # voice sessions so far: the first one greets, later ones pick up where the last left off
    agents: dict[str, dict] = field(default_factory=dict)  # what each agent last did, for the screen
    rematch: dict | None = None
    snapshot: object | None = None  # the Match reader's latest view of the match
    moments: list[str] = field(default_factory=list)  # big moments so far, for the Match agent
    storyline: str = ""  # the Match agent's latest read
    last_read: int = 0  # match second of the Match agent's last read
    reading: asyncio.Task | None = None
    used_digs: list[str] = field(default_factory=list)
    next_dig: asyncio.Task | None = None  # the Ammo agent's next dig, prepared (and refereed) in advance
    dig_score: tuple = ()  # the score when that dig was prepared: a goal makes it stale
    refereeing: asyncio.Task | None = None
    reviewed_chars: int = 0

    def publish(self, message: dict) -> None:
        if message.get("type") == "event":
            self.last_event = message
        for queue in self.listeners:
            queue.put_nowait(message)

    def publish_state(self) -> None:
        self.publish({"type": "state", **self.state()})

    def note(self, kind: str, text: str) -> None:
        self.feed.append({"kind": kind, "text": text, "minute": self.minute})

    def agent(self, agent_id: str, text: str, working: bool = True) -> None:
        """Report what an agent is doing, for the agents strip under the pitch."""
        self.agents[agent_id] = {"text": text, "working": working, "at": time.time()}
        self.publish({"type": "agent", "id": agent_id, **self.agents[agent_id]})

    def moment(self, ev: MatchEvent, text: str) -> None:
        """A moment on the match feed: what the Match agent saw."""
        if ev.type in BIG:
            self.moments.append(text)
        self.publish({"type": "moment", "minute": ev.minute, "text": text, "big": ev.type in BIG,
                      "kind": ev.type.value, "team": ev.team, "elapsed": ev.elapsed})

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

    async def instruct(self, text: str, kind: str = "referee") -> None:
        """A trusted change to how the rival behaves (the Referee stepping in)."""
        self.note(kind, text)
        await self._send({"type": "session.instructions.append", "delegation_id": None, "content": text})

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
                "voice": self.voice is not None, "mode": self.mode, "agents": self.agents}


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
        self.agents = RivalAgents(clients) if clients else None
        self.watches: dict[str, Watch] = {}

    @property
    def available(self) -> bool:
        return bool(self.host and self.key)

    def get(self, watch_id: str) -> Watch:
        return self.watches[watch_id]

    def other(self, team: str) -> str:
        return self.match.away if team == self.match.home else self.match.home

    def new_watch(self, mode: str = "short") -> Watch:
        """The viewer has opened the lobby. The News agent starts at once, so its stories are ready by kick-off."""
        for stale in [w for w in self.watches.values() if w.voice is None and time.monotonic() - w.started > MAX_WATCH_S]:
            self.watches.pop(stale.id, None)
        watch = Watch(uuid.uuid4().hex[:12], {self.match.home: 0, self.match.away: 0},
                      mode=mode if mode in PACING else "short")
        self.watches[watch.id] = watch
        watch.news_task = asyncio.create_task(self._news(watch))
        return watch

    def welcome(self) -> str:
        home, away = self.world.club(self.match.home), self.world.club(self.match.away)
        return f"Welcome to Kick-Off. {home.name} against {away.name} is about to kick off. Who are you supporting?"

    def online(self, watch: Watch) -> list[Persona]:
        """The rivals online for this viewer: fans of the other club, the one they watched with last time first."""
        fans = [p for p in ROSTER if p.club == self.other(watch.user_team)]
        return sorted(fans, key=lambda p: previous_meeting(watch.user_team, p.id) is None)

    def card(self, watch: Watch, persona: Persona) -> dict:
        last = previous_meeting(watch.user_team, persona.id)
        return {"id": persona.id, "name": persona.name, "home": persona.home, "club": persona.club,
                "language": persona.language,
                "watched": bool(last), "last": last.get("result") if last else None}

    def lobby_state(self, watch: Watch, say: str | None = None) -> dict:
        """What the lobby screen shows, and what the host should say next."""
        return {"stage": watch.lobby, "team": watch.user_team, "say": say,
                "online": [self.card(watch, p) for p in self.online(watch)] if watch.user_team else [],
                "offered": watch.offered,
                "rival": self.card(watch, watch.persona) if watch.persona else None}

    def choose_side(self, watch: Watch, team: str) -> dict:
        """The viewer has told the host who they support. The host offers a rival from the other side."""
        if watch.user_team:
            return self.lobby_state(watch)
        watch.user_team = team
        watch.lobby = "rival"
        watch.agent("host", f"Supporting {self.world.club(team).short_name}; offering rivals", working=False)
        online = self.online(watch)
        club = self.world.club(team).short_name
        if previous_meeting(team, online[0].id):
            first = online[0]
            watch.offered = [first.id]
            say = (f"{club}. {first.name} from {first.home} is online, and you watched together last time. "
                   "Rematch, or someone new?")
        else:
            watch.offered = [p.id for p in online]
            say = f"{club}. {self._names(online)} are online. Who do you fancy?"
        watch.publish_state()
        return self.lobby_state(watch, say)

    def choose_rival(self, watch: Watch, persona_id: str) -> dict:
        persona = next((p for p in ROSTER if p.id == persona_id and p.club == self.other(watch.user_team or "")), None)
        if persona is None or watch.persona:
            return self.lobby_state(watch)
        watch.persona = persona
        watch.rematch = previous_meeting(watch.user_team, persona.id)
        watch.lobby = "done"
        watch.publish_state()
        return self.lobby_state(watch, f"Connecting you to {persona.name}.")

    def heard(self, watch: Watch, text: str) -> dict:
        """Something the viewer said in the lobby: their club, then their rival."""
        text = text.lower()
        if watch.lobby == "side":
            team = self.side_from(text)
            return self.choose_side(watch, team) if team else self.lobby_state(watch)
        if watch.lobby == "rival":
            online = self.online(watch)
            named = [p for p in online if p.heard_in(text)]
            if len(named) == 1:
                return self.choose_rival(watch, named[0].id)
            if NEW.search(text) and len(watch.offered) < len(online):
                others = [p for p in online if p.id not in watch.offered]
                watch.offered += [p.id for p in others]
                return self.lobby_state(watch, f"Also online: {self._names(others)}. Who do you fancy?")
            if YES.search(text) and len(watch.offered) == 1:
                return self.choose_rival(watch, watch.offered[0])
        return self.lobby_state(watch)

    @staticmethod
    def _names(people: list[Persona]) -> str:
        names = [f"{p.name} from {p.home}" + (f", who speaks {p.language}" if p.language != "English" else "")
                 for p in people]
        if len(names) == 1:
            return names[0]
        return ", ".join(names[:-1]) + ("," if len(names) > 2 else "") + f" and {names[-1]}"

    async def connect(self, offer_sdp: str, watch_id: str, voice: str | None = None,
                      accent: str | None = None) -> tuple[Watch, str, bool]:
        """Start a voice session for the browser's SDP offer, joining the viewer's watch. Returns the watch,
        the SDP answer, and whether this picks up a conversation already under way (after a drop)."""
        if not self.available:
            raise RivalUnavailable("Azure OpenAI is not configured")
        watch = self.watches.get(watch_id)
        if watch is None:
            raise RivalUnavailable("That match has finished")
        persona = watch.persona
        voice = voice if voice in VOICES else persona.voice if persona else self.default_voice
        accent = accent if accent in ACCENTS else persona.accent if persona else self.default_accent
        body = {
            "session": {
                "model": self.model,
                "instructions": rival_instructions(self.world, self.match, accent, watch.user_team, watch.rematch,
                                                   persona),
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
        watch.voices += 1
        watch.voice = Voice(session_id, sideband)
        watch.voice_lost = None
        watch.voice.watcher = asyncio.create_task(self._listen(watch, watch.voice))
        watch.publish_state()
        return watch, answer, watch.voices > 1

    async def opened(self, watch: Watch, resumed: bool) -> None:
        """The rival's voice is connected: join the viewer and start the match, or pick back up after a drop."""
        if resumed:
            await self.resume(watch)
            return
        if not watch.user_team:  # no lobby (an old client): the rival asks itself
            home, away = self.world.club(self.match.home), self.world.club(self.match.away)
            await watch.commentary(f"{home.name} against {away.name} at {home.stadium} kicks off in a few minutes. "
                                   "Open the conversation: say so, and ask who they're backing.", kind="setup")
            return
        await self._brief(watch, wait=4 if watch.mode == "short" else NEWS_WAIT_S)
        watch.phase = "prematch"
        watch.publish_state()
        dig = watch.talking_points.pop(0)
        watch.agent("ammo", "Opening dig: " + dig[:90])
        await watch.commentary("You've just joined. Greet them in character, then get your first dig in: " + dig
                               + " Kick-off is seconds away; keep it short.", kind="prematch")
        watch.director = asyncio.create_task(self._direct(watch))

    async def resume(self, watch: Watch) -> None:
        """A new voice session for a watch already under way: bring it up to speed, then carry on."""
        recent = " / ".join(f"{'User' if who == 'user' else 'You'}: {text.strip()}"
                            for who, text in watch.transcript[-6:])
        await watch.thinking(f"{self._situation(watch)} The conversation so far ended with: {recent}"[:1800],
                             kind="resume", shown="Brought a fresh voice session up to speed: score, last lines.")
        if watch.dossier and watch.user_team:
            for team in (watch.user_team, self.other(watch.user_team)):
                await watch.thinking(club_news(watch.dossier, self.match, team, self.world), kind="news",
                                     shown=f"Club news on {self.world.club(team).short_name}, again.")
        await watch.commentary("The line dropped for a second. Pick the conversation straight back up, "
                               "naturally, as if you'd just looked away: 'sorry, where were we…'", kind="resume",
                               shown="Pick up where you left off.")

    async def end(self, watch: Watch) -> None:
        if watch.id not in self.watches:
            return
        if watch.director and watch.director is not asyncio.current_task():
            watch.director.cancel()
        for task in (watch.news_task, watch.reading, watch.next_dig, watch.refereeing):
            if task and task is not asyncio.current_task():
                task.cancel()
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

    async def _news(self, watch: Watch) -> news.Dossier | None:
        if self.clients is None:
            return None
        watch.agent("news", "Writing the fortnight's club news…")
        try:
            dossier = await news.write_news(self.world, self.match, self.clients)
        except Exception:
            log.exception("News agent failed; the build-up will use the last good stories")
            watch.agent("news", "Couldn't write today; using the last good stories", working=False)
            return None
        lead = next((x for x in dossier.stories if x.kind == "transfer"), dossier.stories[0])
        watch.agent("news", f"{len(dossier.stories)} stories, e.g. “{lead.headline}”", working=False)
        return dossier

    async def _direct(self, watch: Watch) -> None:
        """Play the match alongside the conversation. The clock always runs; only its speed changes."""
        try:
            pacing = PACING[watch.mode]
            await self._fill(watch, pacing.greeting, lull=5 if watch.mode == "full" else 99, gap=10)
            reader = MatchReader(self.world, self.match.home, self.match.away)
            events = self.match.events
            plan = pace_plan(events, pacing)
            frames = frame_plan(events, plan)
            rival_team = self.other(watch.user_team)
            watch.phase = "first_half"
            for i, ev in enumerate(events):
                if i:
                    await self._fill(watch, (ev.elapsed - events[i - 1].elapsed) / plan[i - 1])
                await self._play(watch, reader.apply(ev), frames.get(i), rival_team, pacing)
            watch.phase = "full_time"
            watch.publish_state()
            await self._fill(watch, pacing.after)  # some post-match banter, then the watch winds down
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Match director failed in %s", watch.id)

    async def _play(self, watch: Watch, update, frame: tuple[int | None, float] | None, rival_team: str,
                    pacing: Pacing) -> None:
        """One event: on the pitch, on the match feed, and to the rival if it's worth saying."""
        ev = update.event
        watch.minute = ev.minute
        watch.snapshot = update.snapshot
        watch.score = {t.id: t.score for t in (update.snapshot.home, update.snapshot.away)}
        if self.agents and ev.elapsed - watch.last_read >= READ_EVERY_S and ev.type not in BIG:
            self._read(watch, update)
        if self.agents and watch.dig_score and watch.dig_score != tuple(watch.score.values()):
            if watch.next_dig and not watch.next_dig.done():
                watch.next_dig.cancel()
            watch.next_dig = None
            self._prepare_dig(watch)  # the score has moved on: a fresh dig for the new situation
        if ev.type is EventType.HALF_TIME:
            watch.phase = "half_time"
        elif ev.type is EventType.KICK_OFF and ev.period == 2 and watch.phase == "half_time":
            watch.phase = "second_half"
        if frame:  # thinned in fast play: see frame_plan
            next_gap, pace = frame
            watch.publish(event_message(update, None if ev.type is EventType.HALF_TIME else next_gap, pace))
        if ev.type in BIG or (ev.type in SMALL and pacing.tells_small):
            if text := describe(self.world, ev):
                watch.moment(ev, text)
                await self._tell(watch, ev, text, rival_team, pacing)
        if ev.type is EventType.HALF_TIME:
            watch.publish_state()
            await self._fill(watch, pacing.half_time)

    async def _tell(self, watch: Watch, ev: MatchEvent, text: str, rival_team: str, pacing: Pacing) -> None:
        """Big moments break in; small ones wait for a gap in the conversation, or pass unremarked."""
        cue = feeling(ev, rival_team)
        if ev.type in BIG:
            watch.last_nudge = time.monotonic()
            watch.agent("match", f"{ev.minute}\u2019 {ev.type.value.replace('_', ' ')}")
            await watch.commentary(f"{BREAK_IN}{text} {cue}", shown=text)
            if self.agents and watch.snapshot:
                self._read(watch, None, ev)
            if not self._goal_follows(ev):  # a penalty's reaction can wait for the goal
                await asyncio.sleep(pacing.holds.get(ev.type, 0))
        elif watch.talking():
            watch.agent("match", f"{ev.minute}\u2019 {ev.type.value.replace('_', ' ')}")
            await watch.thinking(f"(Just happened; mention it if there's a natural moment, or let it go.) {text}",
                                 kind="aside", shown=text)
            await asyncio.sleep(pacing.small_hold)
        else:
            watch.last_nudge = time.monotonic()
            watch.agent("match", f"{ev.minute}\u2019 {ev.type.value.replace('_', ' ')}")
            await watch.commentary(f"{text} {cue} Keep it short.", shown=text)
            await asyncio.sleep(pacing.small_hold)

    def _goal_follows(self, ev: MatchEvent) -> bool:
        return ev.type is EventType.PENALTY and any(
            e.type is EventType.GOAL and 0 <= e.elapsed - ev.elapsed <= 30 for e in self.match.events)

    async def _brief(self, watch: Watch, wait: float) -> None:
        """Before the rival greets the viewer: the club news and the talking points, as quiet context."""
        dossier = None
        if watch.news_task:
            with suppress(Exception):
                dossier = await asyncio.wait_for(asyncio.shield(watch.news_task), wait)
        watch.dossier = dossier or news.last_good(SAVED)
        if dossier:
            news.save(dossier, SAVED / news.LAST)  # the next build-up can fall back on these
        elif watch.news_task and not watch.news_task.done():
            watch.agent("news", "Still writing; the rival starts with the last good stories", working=False)
        user, rival = self.world.club(watch.user_team), self.world.club(self.other(watch.user_team))
        watch.talking_points = ammo(self.world, self.match, rival, user, watch.dossier, watch.rematch)
        watch.agent("ammo", f"{len(watch.talking_points)} digs lined up: news, the table, last time", working=False)
        for team in (watch.user_team, rival.id):
            await watch.thinking(club_news(watch.dossier, self.match, team, self.world), kind="news",
                                 shown=f"{self.world.club(team).short_name} news")
        if self.agents:
            self._prepare_dig(watch)
            watch.refereeing = asyncio.create_task(self._referee(watch))

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
                point = self._take_dig(watch)
                if point is None:
                    point = (watch.talking_points.pop(0) if watch.talking_points else
                             self._situation(watch) + " Needle them about how it's going, or make a bold prediction.")
                    watch.agent("ammo", point[:120], working=False)
                watch.used_digs.append(point)
                await watch.commentary("It's gone quiet. " + point, kind="nudge", shown=point)
                if self.agents:
                    self._prepare_dig(watch)

    def _situation(self, watch: Watch) -> str:
        home, away = self.match.home, self.match.away
        score = (f"{self.world.club(home).short_name} {watch.score[home]}-{watch.score[away]} "
                 f"{self.world.club(away).short_name}")
        where = {"lobby": "before kick-off", "prematch": "the pre-match build-up, kick-off any second",
                 "half_time": "half-time", "full_time": "full-time"}.get(watch.phase, f"minute {watch.minute}")
        return f"Right now it's {where}, and the score is {score}."

    # -- the agents ---------------------------------------------------------

    def _player(self, player_id: str) -> str:
        return self.world.club(player_id.split("-")[0]).player(player_id).name

    def _sides(self, watch: Watch) -> dict:
        return {"rival_supports": self.world.club(self.other(watch.user_team)).name,
                "user_supports": self.world.club(watch.user_team).name}

    def _read(self, watch: Watch, update, ev: MatchEvent | None = None) -> None:
        """Ask the Match agent for a read of the game, without holding anything up. One read at a time."""
        if watch.reading and not watch.reading.done():
            return
        snap = watch.snapshot
        watch.last_read = (ev.elapsed if ev else update.event.elapsed)
        home, away = snap.home, snap.away
        brief = {
            **self._sides(watch), "minute": watch.minute, "phase": watch.phase,
            "score": f"{home.name} {home.score}-{away.score} {away.name}",
            "just_happened": watch.moments[-1] if ev else None,
            "big_moments_so_far": watch.moments[-8:],
            "previous_storyline": watch.storyline,
            "stats": {t.name: {"possession_pct": t.possession_pct, "shots": t.shots, "on_target": t.shots_on_target,
                               "players": len(t.players_on_pitch), "booked": len(t.booked), "sent_off": len(t.sent_off),
                               "scorers": [f"{self._player(g.player)} {g.minute}'" for g in t.goals]}
                      for t in (home, away)},
        }
        watch.agent("match", "Reading the game…")
        watch.reading = asyncio.create_task(self._reading(watch, brief))

    async def _reading(self, watch: Watch, brief: dict) -> None:
        try:
            story = await self.agents.read_match(brief)
        except Exception:
            log.exception("Match agent failed")
            return
        if story and story != watch.storyline:
            watch.storyline = story
            watch.agent("match", story, working=False)
            await watch.thinking(f"The Match agent's read: {story}", kind="storyline", shown=story)

    def _prepare_dig(self, watch: Watch) -> None:
        """Have the Ammo agent choose the next dig and the Referee check it, ahead of the next lull."""
        if watch.next_dig and not watch.next_dig.done():
            return
        watch.dig_score = tuple(watch.score.values())
        watch.next_dig = asyncio.create_task(self._dig(watch))

    async def _dig(self, watch: Watch) -> str | None:
        brief = {
            **self._sides(watch), "phase": watch.phase, "minute": watch.minute,
            "score": self._situation(watch), "match_read": watch.storyline,
            "candidates": watch.talking_points[:10],
            "user_said_this_match": [text.strip()[-300:] for who, text in watch.transcript if who == "user"][-6:],
            "rival_said_recently": [text.strip()[-300:] for who, text in watch.transcript if who == "rival"][-4:],
            "used": watch.used_digs[-12:],
        }
        watch.agent("ammo", "Choosing the next dig…")
        try:
            choice = await self.agents.next_dig(brief)
        except Exception:
            log.exception("Ammo agent failed")
            return None
        if choice is None:
            return None
        watch.agent("ammo", f"{choice.dig} ({choice.source.replace('_', ' ')})", working=False)
        watch.agent("referee", "Checking the dig…")
        try:
            verdict = await self.agents.check(choice.dig, self._situation(watch))
        except Exception:
            log.exception("Referee check failed")
            return None
        if verdict.ok:
            watch.agent("referee", "Dig checked: fine", working=False)
            return choice.dig
        watch.agent("referee", f"Rewrote a dig: {verdict.reason}" if verdict.line else f"Blocked a dig: {verdict.reason}",
                    working=False)
        return verdict.line or None

    def _take_dig(self, watch: Watch) -> str | None:
        """The prepared dig, if it's ready and still fits the score."""
        task = watch.next_dig
        if not task or not task.done() or task.cancelled():
            return None
        watch.next_dig = None
        dig = task.result() if not task.exception() else None
        if dig is None or watch.dig_score != tuple(watch.score.values()):
            return None  # the score has moved on since it was written
        return dig

    async def _referee(self, watch: Watch) -> None:
        """Watch the conversation as it happens and step in if the rival goes too far or the user is upset."""
        try:
            while watch.id in self.watches:
                await asyncio.sleep(REVIEW_EVERY_S)
                said = sum(len(text) for who, text in watch.transcript if who == "rival")
                if said - watch.reviewed_chars < REVIEW_MIN_CHARS or watch.voice is None:
                    continue
                watch.reviewed_chars = said
                lines = [{"who": "user" if who == "user" else "rival", "said": text.strip()[-400:]}
                         for who, text in watch.transcript[-8:]]
                watch.agent("referee", "Listening in on the conversation…")
                try:
                    verdict = await self.agents.review(lines, self._situation(watch))
                except Exception:
                    log.exception("Referee review failed")
                    continue
                if verdict.verdict == "fine" or not verdict.instruction:
                    watch.agent("referee", "All good-natured", working=False)
                    continue
                watch.agent("referee", f"Stepped in: {verdict.instruction}", working=False)
                await watch.instruct(f"The Referee says: {verdict.instruction}")
        except asyncio.CancelledError:
            pass

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
                        watch.agent("referee", "Azure AI Content Safety stopped a line; the match carries on")
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
                "user_team": watch.user_team, "rival": watch.persona.id if watch.persona else None,
                "result": result, "reached": watch.minute or watch.phase,
                "transcript": [(who, text.strip()) for who, text in watch.transcript],
                "feed": watch.feed,
            }, indent=1, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def side_from(heard: str) -> str | None:
        found = [team for team, words in SIDE_WORDS.items() if any(re.search(rf"\b{w}", heard) for w in words)]
        return found[0] if len(found) == 1 else None
