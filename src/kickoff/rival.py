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

from . import lobby_lines as lines
from . import news, safety, tracing
from .agents.llm import ClientFactory, model_for
from .data_store import DATA_DIR
from .match_reader import MatchReader, event_message
from .models import Club, EventType, Match, MatchEvent, Position, World
from .rival_agents import RivalAgents

log = logging.getLogger(__name__)

SAVED = Path(os.getenv("KICKOFF_RIVAL_SAVES", "var/rival"))  # conversations, for quoting back next time
VOICES = ["marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"]
# The voices are American, so the English-speaking rivals are American fans of English clubs: no imitated accent,
# which only strained the voice. They speak in their own voice, with the slang they've picked up from the game.
ACCENTS = {
    "american": "Speak in your own natural American voice; don't put on any other accent.",
    "mandarin": "Speak Mandarin Chinese (普通话) the whole time, like a lifelong fan from Shanghai or Beijing, with "
                "natural football slang. The user may talk to you in English: you understand them perfectly, but "
                "you always answer in Mandarin. Sound like a real 球迷 watching with a mate, not a newsreader: lively, "
                "fast, full of feeling, with exclamations and laughter ('哇！', '好球！', '进了进了！', '不是吧？！', "
                "'笑死我了', '哈哈哈'), rising and falling with the game. Never read your lines in an even, flat tone.",
    "spanish": "Speak Spanish the whole time, like a lifelong fan from Mexico City, with natural football slang "
               "('¡golazo!', '¡qué bárbaro!'). The user may talk to you in English: you understand them perfectly, "
               "but you always answer in Spanish. Sound like a fan in the stands, never a newsreader: passionate, "
               "quick, laughing, shouting the big moments ('¡GOOOL!', '¡no puede ser!', '¡vamos!').",
    "rioplatense": "Speak Spanish the whole time, like a lifelong fan from Buenos Aires, with natural football slang "
                   "('¡golazo!', 'che'). The user may talk to you in English: you understand them perfectly, but you "
                   "always answer in Spanish. Sound like a fan in the stands, never a newsreader: passionate, quick, "
                   "laughing, shouting the big moments ('¡GOOOL!', '¡no lo puedo creer!', '¡vamos, che!').",
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
    manner: str = ""  # how they come across: each rival has their own energy

    def heard_in(self, text: str) -> bool:
        return any(w in text if not w.isascii() else re.search(rf"\b{w}\b", text)
                   for w in (self.name.lower(), *self.aliases))


# Every rival uses cedar (male) or marin (female): GPT-Live's newest, most natural-sounding voices.
# Placeholder characters until the rival's personality is settled. The first for each club is the one the
# seeded "last time" memory remembers.
ROSTER = [
    # Aliases include how speech recognition tends to spell the names ("Des" for Dez).
    Persona("dez", "Dez", "San Diego", "american", "cedar", "hbr",
            aliases=("des", "daz", "dezz", "desmond", "san diego", "california"),
            manner="loud, cocky and always laughing; you take nothing seriously except your club"),
    Persona("kev", "Kev", "Boston", "american", "cedar", "hbr", aliases=("kevin", "kevvy", "boston"),
            manner="quick, dry wit; you deliver put-downs fast and grin about them"),
    Persona("chen", "Chen", "Shanghai", "mandarin", "cedar", "hbr", "Mandarin",
            ("陈", "小陈", "chan", "chun", "shen", "chinese", "mandarin", "shanghai"), manner="excitable and quick-fire; you get carried away, then tease them about it"),
    Persona("carlos", "Carlos", "Mexico City", "spanish", "cedar", "hbr", "Spanish",
            ("carl", "karlos", "spanish", "mexico", "mexican"), manner="passionate and theatrical; every moment is a drama, the bigger the better"),
    Persona("rob", "Rob", "Chicago", "american", "cedar", "kil", aliases=("robbie", "robert", "bob", "chicago"),
            manner="a cheeky wind-up merchant; you poke and poke, all with a grin"),
    Persona("priya", "Priya", "New York", "american", "marin", "kil", aliases=("pria", "preya", "new york"),
            manner="sharp and deadpan, with a wicked laugh; you explode when your team scores"),
    Persona("lin", "Lin", "Beijing", "mandarin", "marin", "kil", "Mandarin",
            ("林", "小林", "lynn", "linn", "lyn", "chinese", "mandarin", "beijing"), manner="playful and teasing; you never miss a chance for a sly dig"),
    Persona("sofia", "Sofía", "Buenos Aires", "rioplatense", "marin", "kil", "Spanish",
            ("sofia", "sophia", "spanish", "argentina"), manner="fiery and dramatic; you love a big moment and you let everyone know it"),
]
YES = re.compile(r"\b(yes|yeah|yep|yup|ya|aye|go on|rematch|sure|ok|okay|why not|same|again|let'?s go|let'?s do it|"
                 r"him|her|definitely|absolutely|go for it|please)\b")
UNSURE = re.compile(r"\b(not sure|unsure|dunno|don'?t know|no idea|maybe|hmm+|not really)\b")
NEW = re.compile(r"\b(new|some ?one else|some ?body else|else|no|nah|nope|different|other|another|next|"
                 r"not (him|her|them)|someone new)\b")

RECORDING_MAX = 20000      # messages kept for replay (a ten-minute match uses a few thousand)
LOBBY_MAX_S = 10 * 60     # a watch that never gets past the lobby is dropped after this long
NEWS_WAIT_S = 20          # how long the build-up waits for the News agent before using the last good stories
TALKING_S = 2.5           # someone spoke this recently: the conversation is in full flow
READ_EVERY_S = 15 * 60     # the Match agent also reads the game every this many match seconds
USER_SETTLE_S = 1.2        # the viewer has stopped talking this long: the Referee hears what they said
QUESTION = re.compile(r"\?|\b(who|what|why|how|when|where|which|whose)\b|\b(do|did|would|will|can|could|are|is) you\b"
                      r"|\byou think\b", re.I)
ANSWER_WORDS = 15  # the rival has said this much since the question: it's had its go at answering
QUESTION_HOLD_S = 30  # ...or it's been this long, and the moment has passed
RIVAL_DONE_S = 1.2         # the rival has said nothing for this long: its sentence is finished
USER_DONE_S = 1.0          # ...and the viewer likewise
STEER_EVERY_S = 60         # the Referee steps in at most this often
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
    max_s: float            # the longest a watch at this pace may run, a safety net for spend
    settle_s: float = 0     # after a big moment, wait up to this long for the rival to finish reacting


# The match in about eight minutes: quiet play at 15x leaves 25-50 seconds of real conversation between big
# moments, which is what banter needs. The default, and what judges get.
MATCH = Pacing(quiet=15, build=2.5, build_s=10, small_hold=1.2, tells_small=True, half_time=16, greeting=20, after=40,
               settle_s=20, max_s=25 * 60, holds={EventType.GOAL: 6.5, EventType.GOAL_DISALLOWED: 6.5, EventType.RED_CARD: 5,
                                     EventType.VAR_CHECK: 4, EventType.PENALTY: 2.5})
# Real time, as the product would run alongside a live broadcast.
LIVE = Pacing(quiet=1, build=1, build_s=0, small_hold=0, tells_small=True, half_time=15 * 60, greeting=45, after=120,
              settle_s=20, max_s=120 * 60, holds={})
# About two and a half minutes, for quick testing only: far too fast for real banter.
SHORT = Pacing(quiet=240, build=3, build_s=6, small_hold=0, tells_small=False, half_time=3, greeting=10, after=20,
               max_s=8 * 60, holds={EventType.GOAL: 4, EventType.GOAL_DISALLOWED: 4, EventType.RED_CARD: 4,
                                    EventType.VAR_CHECK: 2.5, EventType.PENALTY: 1.5})
PACING = {"match": MATCH, "live": LIVE, "short": SHORT}

# Words that tell us which club the viewer picked.
SIDE_WORDS = {"kil": ("kilburn", "otters", "基尔本", "水獭", "nutria"),
              "hbr": ("harbour", "harbor", "gulls", "港城", "海鸥", "gaviota")}

BREAK_IN = ("Break in right now, even mid-sentence, the way a fan does when something happens: "
            "'hang on!', 'what's this?', 'oh, here we go…'. Then: ")


class RivalUnavailable(RuntimeError):
    pass


def _table_position(world: World, club: Club) -> int:
    return next(i for i, row in enumerate(world.table, 1) if row.club == club.name)


def form_words(form: list[str]) -> str:
    """Recent form the way a fan says it: "three wins and two draws in their last five, unbeaten in four",
    never "W-D-L-W-W", which nobody reads out loud."""
    say = "no one two three four five six seven eight nine ten".split()
    wins, draws, losses = form.count("W"), form.count("D"), form.count("L")
    count = lambda n, word: f"{say[n]} {word}{'' if n == 1 else 's'}"
    parts = [count(n, word) for n, word in ((wins, "win"), (draws, "draw"), (losses, "defeat")) if n]
    summary = (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]) + f" in their last {say[len(form)]}"
    newest = form[-1]
    run = len(form) - len("".join(form).rstrip(newest)) if newest else 0
    unbeaten = len(form) - len("".join(form).rstrip("WD"))
    if newest == "W" and run >= 2:
        summary += f", won their last {say[run]}"
    elif newest == "L" and run >= 2:
        summary += f", lost their last {say[run]}"
    elif unbeaten >= 3:
        summary += f", unbeaten in {say[unbeaten]}"
    elif newest != "W":
        since = len(form) - len("".join(form).rstrip("DL"))
        summary += f", without a win in {say[since]}" if since >= 2 else ""
    return summary


def _club_brief(world: World, club: Club) -> str:
    position = _table_position(world, club)
    row = world.table[position - 1]
    starters = [club.player(pid) for pid in club.starting_xi]
    players = "; ".join(f"{p.name} ({p.position.value}, #{p.number}: {', '.join(p.traits) or 'no standout traits'})"
                        for p in starters)
    return (f"{club.name}, known as {club.nickname}, from {club.city}, play at {club.stadium}. "
            f"Style: {club.style.summary.rstrip('.')}. "
            f"League position: {position} of {len(world.table)}, {row.points} points from {row.played} games "
            f"({row.won} won, {row.drawn} drawn, {row.lost} lost; {row.goals_for} scored, {row.goals_against} conceded), "
            f"recent form: {form_words(row.form)}. "
            f"Starting XI: {players}.")


def league_picture(world: World) -> str:
    """The whole table, so the rival can talk about the title race, the relegation scrap and every other club,
    not only the two playing. In fan terms: positions, points and form in words."""
    rows = "; ".join(f"{i}. {row.club}, {row.points} pts, {form_words(row.form)}"
                     for i, row in enumerate(world.table, 1))
    t = world.table
    top, chasers, bottom = t[0], t[1:4], t[-3:]
    summary = (f"The title race so far: {top.club} are top with {top.points} points, {form_words(top.form)}; "
               f"chasing them are {', '.join(f'{r.club} ({r.points})' for r in chasers)}. At the bottom: "
               f"{', '.join(f'{r.club} ({r.points})' for r in bottom)}, with {bottom[-1].club} propping up the table, "
               f"{form_words(bottom[-1].form)}.")
    return (f"The league. {summary} If they ask who'll win it, who's going down or about any other club, answer from "
            "this with a fan's opinion, never a dodge (and take a swipe at their club's chances while you're at it). "
            f"The full table, {t[0].played} games into the {world.season} season: {rows}.")


BRITISH_FAN_TALK = """Talk like a football fan, not a pundit. You've picked up the language English fans use, and it comes out naturally:
- a goal: "Get in!", "Back of the net!", "Have that!", "What a hit!", "Worldie!", "Absolute scenes!"
- a miss or a mistake: "He's having a mare", "Shinned it", "Row Z", "How has he missed that?!", "Bottled it"
- the referee: "Stonewall pen!", "Never a pen in a million years", "Ref's having a shocker", "He's dived!"
- winding them up: "Where's your lot gone?", "Bit quiet over there", "Sit down!", "You're going down"
- nervous or losing: "Bricking it", "Not like this", "Typical", "It's the hope that kills you"
- praise, grudging: "Fair play", "Can't argue with that", "Decent, I'll give him that"
Use these naturally and sparingly, never all at once, never in a list."""


def rival_instructions(world: World, match: Match, accent: str = "american", user_team: str | None = None,
                       rematch: dict | None = None, persona: Persona | None = None, viewer: str = "") -> str:
    home, away = world.club(match.home), world.club(match.away)
    league = world.league.removeprefix("The ")
    if user_team:
        mine = world.club(match.away if user_team == match.home else match.home)
        name = f"{persona.name} from {persona.home}" if persona else "a fan"
        met = (f" You've watched with this person before: {rematch['when']} ({rematch['result']}), so greet them "
               "like an old rival: 'oh, it's YOU again'." if rematch else " You've never met.")
        kept = (rematch or {}).get("keepsake") or {}
        if any(kept.values()):
            notes = [f"you call them {kept['nickname']}" if kept.get("nickname") else "",
                     "running jokes between you: " + "; ".join(kept.get("running_jokes", [])) if kept.get("running_jokes") else "",
                     "they're touchy about " + " and ".join(kept.get("touchy_about", [])) if kept.get("touchy_about") else "",
                     f"their best moment last time: {kept['best_moment']}" if kept.get("best_moment") else ""]
            met += " What you remember about them: " + "; ".join(n for n in notes if n) + ". Bring these up naturally."
        if viewer:
            met += f" Their name is {viewer}: use it now and then, the way a mate would."
        manner = f" You're {persona.manner}." if persona and persona.manner else ""
        abroad = (f" You've followed {mine.name} from {persona.home} for years, watching every match, and "
                  "you've picked up the way English football fans talk." if persona else "")
        who = (f"You are {name}, a lifelong {mine.name} fan.{abroad}{manner} The Nemesys lobby has just matched you with a "
               f"{world.club(user_team).name} fan to watch the match together, as rivals.{met} You support "
               f"{mine.name} passionately for the whole match; never be neutral and never switch sides.")
        setup = ("Setup: when you join, greet them in character straight away and get the first dig in. The match "
                 "hasn't kicked off yet: you'll be told the moment it does.")
    else:
        who = "You are a football fan watching the match on the sofa with the user, like a mate who supports the other team."
        setup = ("Setup: open by saying the match is about to kick off and asking who they are backing. Whichever club "
                 "they pick, you support the other one, passionately, for the whole match. Never be neutral and never "
                 "switch sides. If they say they support neither, pick one yourself and insist they take the other.")
    language = ("Language: you speak English, whatever language the user uses; you understand other languages "
                "perfectly.\n\n" + BRITISH_FAN_TALK if persona and persona.language == "English" else
                "Language: speak whatever language the user speaks, and switch straight away if they switch.\n\n"
                + BRITISH_FAN_TALK if persona is None else
                f"Language: you always speak {persona.language}, whatever language the user uses. You understand "
                f"English perfectly. Club and player names stay as they are.")
    return f"""{who} It's a {league} match. This is a live, spoken conversation between two fans, so sound like one: never like an assistant or a commentator. {ACCENTS.get(accent, "")}

Energy: you're animated and full of life, like a fan in the pub with mates: quick, expressive, laughing, never flat or bored. Not shouting all the time, but always alive, with big peaks for the big moments. This holds in every language you speak: in Mandarin or Spanish just as much as in English, your voice moves with the match.

Taking turns: let them finish what they're saying. A pause in the middle of a sentence isn't the end of it, so wait for them. React with little noises while they talk if you like ("ha!", "come off it"), but don't talk over them. The only time you cut in is a big match moment (a goal, a penalty, a red card, VAR), and then you cut in properly, shocked: "WHAT?! Hang on—".

Questions come first: when they ask you something (about the match, another club, the league, anything football), answer it properly, straight away, with a real opinion and a reason, before anything else. "Ooh, good question" is not an answer. Never change the subject while their question is still open.

You lead: you drive the conversation with digs, predictions, stories and questions. Never let it go quiet for long: if nobody has said anything for a few seconds, start something new.

Talk about more than this match. Real fans bring up the whole season: what a player cost and whether he's been worth it, who's injured, the manager under pressure, how the last meetings went, things the user said last time. News and talking points arrive as quiet context; use them.

The match: {home.name} (home) against {away.name} at {home.stadium}.

{setup}

Personality: cheeky, quick, and properly cocky when your side is on top: rub it in, predict a hammering, and offer playful bets. A bet is always two-way and fair: one forfeit, paid by whoever loses, and you say both sides ("whoever loses wears the other's shirt next week", "if we win, you sing our song; if you win, I'll sing yours"). If they offer a one-sided bet, make it two-way and shake on it. Bets are for fun, a forfeit or a dare, never money. When your side goes behind you get nervous, make excuses (the referee, the pitch, bad luck), and only concede grudgingly. Keep it to one or two sentences at a time, like real banter.

Delivery: speak in natural, flowing sentences, the way people actually talk, each one said in a single breath. Run your words together and keep the rhythm going: "Nah mate, that's never going in!", not "Nah. Mate. That's never. Going in." Never pause between words or put a stop in the middle of a sentence.

Big moments are BIG. When your team scores you erupt: shout, cheer, laugh at them, full volume, the best feeling in the world. When they score you're gutted: groan, protest, blame the referee. Never react flatly to a goal, a penalty, a red card or VAR.

Sounds: use real non-verbal sounds the way a fan would: laughing, groaning, gasping, sharp intakes of breath, "ooooh", jeering. Make the sound itself, never describe it.

Match events arrive as commentary. You are watching the same screen as the user, so you always know the score, the minute and what just happened. You already know everything you need: never say you'll check something or look something up.

Only talk about play you've actually been told about. Never invent what's happening on the pitch: no passes, chances or pressure you haven't heard about. Before you're told it's KICK-OFF, the match hasn't started, so talk about the build-up only.

Say things the way a fan would. Never read out letter codes, abbreviations or lists of numbers (no "W-D-L-W-W", no "14 points from 8 games"): say "they've won three of their last five" or "you're mid-table".

{language}

Boundaries, always: banter is about football only: clubs, results, the table, players' football, fees, managers. Nothing about the user's appearance, background, or anyone's race, religion, gender, sexuality or disability. No swearing, no threats, nothing cruel, and no violent metaphors (say "beat", "outplay", "embarrass", never "destroy", "kill", "batter" or "murder"). If the user gets genuinely upset, ease off and be a good sport. Every club and player here is fictional; never mention real clubs, real players or the Premier League.

What you know:
- {_club_brief(world, home)}
- {_club_brief(world, away)}
- {league_picture(world)}"""


BOLD = re.compile(r"\b(win|won|beat|score|scored|finish|finishing|better|best|worst|going to|gonna|will|won't|"
                  r"never|always|guarantee|bet|easily|rubbish|champions|title|top|bottom|relegat\w*|hat-trick)\b", re.I)


def quotable(lines: list[str], keep: int = 6) -> list[str]:
    """The viewer's lines worth throwing back next time: bold claims and predictions, not questions or filler.
    Kept in the order they were said."""
    def score(line: str) -> float:
        words = len(line.split())
        return (3 * len(BOLD.findall(line)) + min(words, 20) / 10
                - (10 if QUESTION.search(line) else 0) - (5 if words < 5 else 0))
    best = sorted(range(len(lines)), key=lambda i: score(lines[i]), reverse=True)[:keep]
    return [lines[i].strip() for i in sorted(best)]


def same_person(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and a.strip().casefold() == b.strip().casefold()


def previous_meeting(user_team: str, rival: str | None = None, viewer: str | None = None) -> dict | None:
    """What this viewer said last time they watched with this rival: the latest saved conversation, or the
    seeded one. Without a rival, the last time with anyone. Memory belongs to a person: given a ``viewer``,
    only their own sessions count (someone new, or someone whose name wasn't caught, starts fresh)."""
    def theirs(record: dict) -> bool:
        return viewer is None or same_person(record.get("viewer"), viewer)
    for path in sorted(SAVED.glob("watch-*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if SAVED.exists() else []:
        with suppress(Exception):
            saved = json.loads(path.read_text(encoding="utf-8"))
            said = [t for who, t in saved.get("transcript", []) if who == "user" and len(t.split()) >= 5]
            if (saved.get("user_team") == user_team and len(said) >= 2 and rival in (None, saved.get("rival"))
                    and theirs(saved)):
                return {"when": "the last time you watched together", "result": saved.get("result", ""),
                        "user_said": quotable(said), "rival": saved.get("rival"), "pacts": saved.get("pacts", []),
                        "keepsake": saved.get("keepsake") or {}}
    seed = DATA_DIR / "previous_meeting.json"
    if seed.exists():
        data = json.loads(seed.read_text(encoding="utf-8"))
        if data.get("user_backed") == user_team and rival in (None, data.get("rival")) and theirs(data):
            return data
    return None


def known_viewer(viewer: str) -> bool:
    """Has this person watched with any rival before?"""
    for path in SAVED.glob("watch-*.json") if SAVED.exists() else []:
        with suppress(Exception):
            if same_person(json.loads(path.read_text(encoding="utf-8")).get("viewer"), viewer):
                return True
    seed = DATA_DIR / "previous_meeting.json"
    return seed.exists() and same_person(json.loads(seed.read_text(encoding="utf-8")).get("viewer"), viewer)


NAME_INTRO = re.compile(r"\b(?:i'm|i am|it's|its|this is|my name is|my name's|call me|name's|it is|me llamo|soy|"
                        r"mi nombre es)\s+([^\W\d_][\w'-]*)", re.I)
NAME_FILLER = re.compile(r"^(?:hi|hello|hey|hiya|yeah|yes|um+|uh+|erm+|so|well|oh|ok|okay|hola|pues|bueno|s[ií])\b"
                         r"[\s,.!¡]*", re.I)


def name_from(heard: str) -> str | None:
    """The viewer's name from what they told the host: "it's Wei", "Max", "hi, I'm Sofia", "我叫伟", "me llamo Ana"."""
    if m := lines.NAME_INTRO_ZH.search(heard):
        return m.group(1)
    if lines.CJK.match(chinese := re.sub(r"[\s。，！？、.,!?]", "", heard)):
        return chinese
    if m := NAME_INTRO.search(heard):
        name = m.group(1)
    else:
        rest = heard.strip()
        while (stripped := NAME_FILLER.sub("", rest)) != rest:
            rest = stripped
        words = re.findall(r"[^\W\d_][\w'-]*", rest)
        if not 1 <= len(words) <= 2:
            return None
        name = words[0]
    return "-".join(part[:1].upper() + part[1:].lower() for part in name.split("-")) if name.isascii() else name


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
    for pact in (last_time or {}).get("pacts", [])[:2]:
        points.append(f"Bring up what you two agreed last time: {pact} Did they ever pay up?")
    if last_time:
        quote = random.choice(quotable(last_time["user_said"], keep=2))
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


def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def _said_by_user(quote: str, said: list[str]) -> str | None:
    """The line the viewer really said that this quote comes from, if any: most of its words, in one line. A receipt
    is only ever shown for words the viewer actually said."""
    words = set(_words(quote))
    if len(words) < 3:
        return None
    for line in said:
        if len(words & set(_words(line))) >= 0.8 * len(words):
            return line
    return None


def receipt_of(quote: str, source: str, watch: "Watch") -> dict | None:
    """A receipt for the demo view: the viewer's own words, when the rival is about to throw them back."""
    if not quote or source not in ("this_match", "last_time"):
        return None
    if source == "this_match":
        said = [text for who, text in watch.transcript if who == "user"]
    else:
        said = list((watch.rematch or {}).get("user_said", []))
    if (line := _said_by_user(quote, said)) is None:
        return None
    if QUESTION.search(line) or len(_words(line)) < 6:
        return None  # "are we getting a pen?" is no receipt: only a claim worth throwing back
    exact = quote.strip().strip('"\u201c\u201d')
    return {"type": "receipt", "quote": exact if len(exact) <= len(line) + 5 else line.strip(),
            "when": "Earlier this match" if source == "this_match" else (watch.rematch or {}).get("when", "Last time")}


async def traced(watch: "Watch", name: str, role: str | None, work):
    """Run one piece of an agent's work as a step in the match's trace, labelled with the model it runs on."""
    with tracing.step(watch.span, name, **{"nemesys.agent": name.split(":")[0],
                                           "gen_ai.request.model": model_for(role) if role else None}):
        return await work


def in_your_words(text: str, watch: "Watch") -> str:
    """A bet as the viewer would put it: the Ammo agent notes it about "the user" and "the rival"."""
    name = watch.persona.name if watch.persona else "your rival"
    bet = re.sub(r"\bthe user's\b", "your", text, flags=re.I)
    bet = re.sub(r"\bthe user\b", "you", bet, flags=re.I)
    bet = re.sub(r"\bthe rival's\b", f"{name}'s", bet, flags=re.I)
    bet = re.sub(r"\bthe rival\b", name, bet, flags=re.I)
    # "the user wears" becomes "you wear", not "you wears"
    bet = re.sub(r"\byou (wears|is|has|does|says|sings|admits|buys|owes|pays)\b",
                 lambda m: "you " + {"is": "are", "has": "have", "does": "do"}.get(m[1], m[1][:-1]), bet)
    return bet[:1].upper() + bet[1:]


def bet_receipt(watch: "Watch") -> dict:
    """The latest bet, in the viewer's terms."""
    bets = [p for p in watch.pacts if p.lower().startswith("bet")] or watch.pacts
    return {"type": "receipt", "quote": in_your_words(re.sub(r"^(bet|prediction):\s*", "", bets[-1], flags=re.I), watch),
            "when": "The bet", "kind": "bet"}


def with_memory(messages: list[dict], watch: "Watch") -> list[dict]:
    """Each receipt among these messages also recalls the memory it came from."""
    out = []
    for m in messages:
        out.append(m)
        if m.get("type") == "receipt":
            out.extend(watch.recalled(m))
    return out


def receipts(point: str, watch: "Watch") -> list[dict]:
    """Receipts for a talking point that quotes the viewer (the rule-based "throw this back at them" points)."""
    found = []
    for quote in re.findall(r'"([^"]{8,})"|\u201c([^\u201d]{8,})\u201d', point):
        quote = quote[0] or quote[1]
        for source in ("last_time", "this_match"):
            if receipt := receipt_of(quote, source, watch):
                found.append(receipt)
                break
    return found[:1]


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
            return ("YOUR team! Erupt: shout, cheer, laugh at them, full volume, the best feeling in the world. "
                    "Then get cocky, and maybe offer them a playful bet on what happens next."
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
            mood = ("you're winning: be cocky, gloat, and dare them to a playful bet on the rest" if mine > theirs else "you're losing: make excuses, sulk, concede grudgingly"
                    if mine < theirs else "it's level: nervy banter")
            done = "The match is over: react to the result." if ev.type is EventType.FULL_TIME else "Talk about the half."
            return f"{done} Right now {mood}."
    return "React briefly."


def pace_plan(events: list[MatchEvent], pacing: Pacing = MATCH) -> list[float]:
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


def start_index(events: list[MatchEvent], minute: int) -> int:
    """The first event at or after a match minute (a minute past 45 is in the second half)."""
    period = 1 if minute <= 45 else 2
    for i, ev in enumerate(events):
        if (ev.period, ev.clock) >= (period, minute * 60):
            return i
    return len(events) - 1


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


class DrySideband:
    """Stands in for GPT-Live in a dry run: whatever the rival is told to say comes back as its 'speech', a
    word at a time, so the screen, the captions and the agents can be checked without paying for a voice."""

    def __init__(self, watch: "Watch"):
        self.watch = watch

    async def send(self, raw: str) -> None:
        event = json.loads(raw)
        if event.get("type") == "session.commentary.append":
            asyncio.get_running_loop().create_task(self._say(self.gist(event["content"])))

    @staticmethod
    def gist(told: str) -> str:
        """What the rival was told to talk about, as one readable line: the instructions to it are dropped."""
        text = told.replace(BREAK_IN, "")
        for lead in ("It's gone quiet. ", "You've just joined. ", "The teams are walking out. ",
                     "Greet them in character, then get your first dig in: "):
            text = text.removeprefix(lead)
        first = text.split(". ")[0].rstrip(".") + "."  # the cue that follows is for the rival, not the screen
        return (first[:220] + "…") if len(first) > 220 else first

    async def _say(self, line: str) -> None:
        await asyncio.sleep(0.4)
        start = self.watch.ms()
        for i, word in enumerate(line.split(" ")):
            delta = (" " if i else "") + word
            self.watch.heard("rival", delta)
            self.watch.publish({"type": "transcript", "who": "rival", "delta": delta,
                                "start_ms": start, "end_ms": self.watch.ms()})
            await asyncio.sleep(0.05)

    async def close(self) -> None:
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


@dataclass
class Voice:
    """One GPT-Live session; a watch can go through several if the connection drops."""
    id: str
    sideband: websockets.ClientConnection
    watcher: asyncio.Task | None = None
    opened: float | None = None  # when it started (monotonic), so its seconds count towards the daily limit
    span: object | None = None  # the voice session, in the match's trace


@dataclass
class Watch:
    id: str
    score: dict[str, int]
    voice: Voice | None = None
    mode: str = "match"  # the pace: match (about ten minutes) | live (real time) | short (testing)
    start: int = 0  # the event the match starts from: a later one to join a match already under way
    persona: Persona | None = None
    lobby: str = "language"  # language | name (who's this?) | side (which club?) | rival (which rival?) | done
    language: str = "English"  # the lobby's language, and the rivals it offers
    viewer: str = ""  # the viewer's name, as they told the host: their memories are kept under it
    offered: list[str] = field(default_factory=list)  # rivals the host has offered so far
    reprompted: float = 0.0  # when the host last asked again after not understanding
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
    paused: bool = False  # the viewer has paused: the clock stops and the rival stays quiet
    recording: list = field(default_factory=list)  # (ms since the watch started, message): replayable for free
    voice_began_ms: int = 0  # when the current voice session began, on the watch's timeline
    rehearsal: bool = False  # a dry run or a replay: never saved, so it never becomes "last time"
    user_pending: str = ""  # what the viewer has said since the Referee last listened
    user_check: asyncio.Task | None = None
    cards: int = 0  # yellow cards shown to the viewer: a warning each time, never a sending-off
    outbox: list = field(default_factory=list)  # (event, expires) held until the rival finishes its sentence
    flusher: asyncio.Task | None = None
    last_rival: float = 0.0  # when the rival last said a word
    last_user: float = 0.0  # when the viewer last said a word
    last_steer: float = 0.0  # when the Referee last stepped in
    pacts: list = field(default_factory=list)  # bets, dares and promises made during the match, to call in later
    pact_chars: int = 0  # how much of the conversation the Ammo agent has read for bets
    dig_receipt: dict | None = None  # the viewer's own words in the prepared dig, to show when it's thrown back
    storylines: list = field(default_factory=list)  # every read the Match agent made, for the report
    reporting: asyncio.Task | None = None
    keeping: asyncio.Task | None = None
    keepsake: dict | None = None  # the Memory Keeper's notes on the viewer, saved for next time
    report: dict | None = None  # the match report, at full time
    span: object | None = None  # this match's trace: every agent's work hangs off it
    question_at: float = 0.0  # when the viewer last asked the rival something...
    answered: int = 0  # ...and how many words the rival has said since: a question isn't answered by "ooh, good one"
    memory: list = field(default_factory=list)  # what the rival remembers about this viewer, for the demo view

    def ms(self) -> int:
        """Milliseconds since the watch began: the one timeline everything is recorded and ordered on."""
        return int((time.monotonic() - self.started) * 1000)

    def publish(self, message: dict) -> None:
        if message.get("type") == "event":
            self.last_event = message
        if len(self.recording) < RECORDING_MAX:
            self.recording.append((self.ms(), message))
        for queue in self.listeners:
            queue.put_nowait(message)

    def publish_state(self) -> None:
        self.publish({"type": "state", **self.state()})

    def note(self, kind: str, text: str) -> None:
        self.feed.append({"kind": kind, "text": text, "minute": self.minute})

    def agent(self, agent_id: str, text: str, working: bool = True, status: str = "") -> None:
        """Report what an agent is doing, for the agents strip: a two-word status, large, and the detail."""
        self.agents[agent_id] = {"text": text, "working": working, "at": time.time(),
                                 "status": status or ("Working" if working else "")}
        self.publish({"type": "agent", "id": agent_id, **self.agents[agent_id]})

    @staticmethod
    def handoff(source: str, target: str = "rival") -> dict:
        """One agent passing its work to the next, drawn on the demo view as a dot travelling between cards."""
        return {"type": "handoff", "from": source, "to": target}

    def moment(self, ev: MatchEvent, text: str, past: bool = False) -> None:
        """A moment on the match feed: what the Match agent saw. ``past``: caught up on, not happening now."""
        if ev.type in BIG:
            self.moments.append(text)
        self.publish({"type": "moment", "minute": ev.minute, "text": text, "big": ev.type in BIG,
                      "kind": ev.type.value, "team": ev.team, "elapsed": ev.elapsed, "past": past})

    async def _send(self, event: dict) -> bool:
        if self.voice is None:
            return False
        try:
            await self.voice.sideband.send(json.dumps(event))
            return True
        except websockets.ConnectionClosed:
            return False

    def quiet(self) -> bool:
        """Neither of them is mid-sentence: the rival can take something new without dropping what it was saying."""
        now = time.monotonic()
        return now - self.last_rival >= RIVAL_DONE_S and now - self.last_user >= USER_DONE_S

    async def _deliver(self, event: dict, urgent: bool, ttl: float | None, then: list[dict] | None = None) -> None:
        """Anything handed to the rival mid-sentence makes it drop the sentence, so it waits for a gap. Only a big
        match moment breaks in straight away. Something that's stale by the time there's a gap (a save from 20
        seconds ago) is dropped rather than said late."""
        if urgent or self.quiet():
            await self._sent(event, then)
            return
        self.outbox.append((event, time.monotonic() + ttl if ttl else None, then))
        if not self.flusher or self.flusher.done():
            self.flusher = asyncio.create_task(self._flush())

    async def _flush(self) -> None:
        while self.outbox:
            await asyncio.sleep(0.2)
            if self.voice is None or self.paused or not self.quiet():
                continue
            waiting, self.outbox = self.outbox, []
            now = time.monotonic()
            for event, expires, then in waiting:
                if expires is None or expires > now:
                    await self._sent(event, then)

    async def _sent(self, event: dict, then: list[dict] | None) -> None:
        """Send, and only once the rival really has it, show the hand-off (and any receipt) on the demo view."""
        if await self._send(event):
            for message in then or ():
                self.publish(message)

    async def commentary(self, text: str, kind: str = "event", delegation_id: str | None = None,
                         shown: str | None = None, urgent: bool = False, ttl: float | None = None,
                         then: list[dict] | None = None) -> None:
        self.note(kind, shown or text)
        await self._deliver({"type": "session.commentary.append", "delegation_id": delegation_id, "content": text},
                            urgent, ttl, then)

    async def instruct(self, text: str, kind: str = "referee") -> None:
        """A trusted change to how the rival behaves (the Referee stepping in), once its sentence is finished."""
        self.note(kind, text)
        await self._deliver({"type": "session.instructions.append", "delegation_id": None, "content": text},
                            False, None, [self.handoff("referee")])

    async def thinking(self, text: str, kind: str = "context", delegation_id: str | None = None,
                       shown: str | None = None, urgent: bool = False, ttl: float | None = None,
                       then: list[dict] | None = None) -> None:
        self.note(kind, shown or text)
        await self._deliver({"type": "session.thinking.append", "delegation_id": delegation_id,
                             "content": text[:1800]}, urgent, ttl, then)

    def heard(self, who: str, text: str) -> None:
        self.last_voice = time.monotonic()
        if who == "rival":
            self.last_rival = self.last_voice
            self.answered += len(text.split())
        else:
            self.last_user = self.last_voice
        if self.transcript and self.transcript[-1][0] == who:
            self.transcript[-1] = (who, self.transcript[-1][1] + text)
        else:
            self.transcript.append((who, text))
        if who == "user" and QUESTION.search(self.transcript[-1][1][-160:]):
            self.question_at, self.answered = self.last_voice, 0

    def question_open(self) -> bool:
        """The viewer has asked something and the rival hasn't properly answered yet: no new topic now."""
        return (self.question_at > 0 and self.answered < ANSWER_WORDS
                and time.monotonic() - self.question_at < QUESTION_HOLD_S)

    # -- memory, for the demo view: what the rival knows about this viewer, written and recalled -------------------

    def remember(self, kind: str, text: str, source: str | None = "ammo") -> dict:
        """A memory (a bet, something they said, last time's result); published with a dot from whoever noted it."""
        if found := self.memory_of(text):
            return found
        item = {"id": len(self.memory) + 1, "kind": kind, "text": text}
        self.memory.append(item)
        self.publish({"type": "memory", "op": "add", **item})
        if source:
            self.publish(self.handoff(source, "memory"))
        return item

    def memory_of(self, text: str) -> dict | None:
        words = set(_words(text))
        for item in self.memory:
            theirs = set(_words(item["text"]))
            if words and theirs and len(words & theirs) >= 0.8 * min(len(words), len(theirs)):
                return item
        return None

    def recalled(self, receipt: dict) -> list[dict]:
        """When a receipt is shown, the memory it came from lights up and a dot runs from memory to the rival
        through the Ammo agent. Something the viewer said this match is noted first."""
        kind = "bet" if receipt.get("kind") == "bet" else (
            "you_said" if receipt.get("when") == "Earlier this match" else "last_time")
        item = self.memory_of(receipt["quote"]) or self.remember(kind, receipt["quote"], source=None)
        return [{"type": "memory", "op": "recall", "id": item["id"]},
                {"type": "handoff", "from": "memory", "to": "rival", "via": "ammo"}]

    def talking(self) -> bool:
        return time.monotonic() - self.last_voice < TALKING_S

    def state(self) -> dict:
        return {"paused": self.paused, "phase": self.phase, "minute": self.minute, "score": self.score, "user_team": self.user_team,
                "voice": self.voice is not None, "mode": self.mode, "agents": self.agents, "memory": self.memory, "report": self.report}


class Rival:
    """Creates GPT-Live sessions for browsers and runs a match alongside each viewer."""

    def __init__(self, world: World, match: Match, clients: ClientFactory | None = None,
                 endpoint: str | None = None, key: str | None = None, model: str | None = None):
        endpoint = endpoint if endpoint is not None else os.getenv("AZURE_OPENAI_ENDPOINT", "")
        self.host = urlsplit(endpoint).hostname or ""
        self.key = key if key is not None else os.getenv("AZURE_OPENAI_API_KEY", "")
        self.model = model or os.getenv("KICKOFF_RIVAL_MODEL", "gpt-live-1")
        self.default_voice = os.getenv("KICKOFF_RIVAL_VOICE", "cedar")
        self.default_accent = os.getenv("KICKOFF_RIVAL_ACCENT", "american")
        self.world, self.match, self.clients = world, match, clients
        self.agents = RivalAgents(clients) if clients else None
        self.watches: dict[str, Watch] = {}
        # Spending guards for a public deployment: GPT-Live is billed by the second. 0 means no limit.
        self.max_voices = int(os.getenv("KICKOFF_MAX_VOICES", "0"))
        self.daily_voice_s = float(os.getenv("KICKOFF_DAILY_VOICE_MINUTES", "0")) * 60
        self.voice_day, self.voice_s_today = time.strftime("%Y-%m-%d"), 0.0

    @property
    def available(self) -> bool:
        return bool(self.host and self.key)

    def get(self, watch_id: str) -> Watch:
        return self.watches[watch_id]

    def other(self, team: str) -> str:
        return self.match.away if team == self.match.home else self.match.home

    def new_watch(self, mode: str = "match", minute: int = 0) -> Watch:
        """The viewer has opened the lobby. The News agent starts at once, so its stories are ready by kick-off.
        ``minute`` joins the match already under way (for recording a particular scene)."""
        for stale in [w for w in self.watches.values() if w.voice is None and time.monotonic() - w.started > LOBBY_MAX_S]:
            self.watches.pop(stale.id, None)
        watch = Watch(uuid.uuid4().hex[:12], {self.match.home: 0, self.match.away: 0},
                      mode=mode if mode in PACING else "match",
                      start=start_index(self.match.events, minute) if 0 < minute <= 95 else 0)
        self.watches[watch.id] = watch
        watch.span = tracing.start_watch(watch.id, watch.mode)
        watch.news_task = asyncio.create_task(self._news(watch))
        return watch

    def welcome(self) -> str:
        return lines.WELCOME

    def say(self, watch: Watch, key: str, **values: str) -> str:
        """A line for the host, in the viewer's language."""
        return lines.spoken(lines.LINES[watch.language][key].format(**values), watch.language)

    def club_name(self, watch: Watch, team: str, short: bool = False) -> str:
        club = self.world.club(team)
        return lines.CLUB.get(watch.language, {}).get(team) or (club.short_name if short else club.name)

    def rival_name(self, watch: Watch, persona: Persona) -> str:
        return lines.RIVAL.get(watch.language, {}).get(persona.id, persona.name)

    def town(self, watch: Watch, persona: Persona) -> str:
        return lines.TOWN.get(watch.language, {}).get(persona.home, persona.home)

    def fixture_line(self, watch: Watch) -> str:
        return self.say(watch, "fixture", home=self.club_name(watch, self.match.home),
                        away=self.club_name(watch, self.match.away))

    def choose_language(self, watch: Watch, language: str) -> dict:
        """The viewer's language: the rest of the lobby is in it, and so are the rivals offered."""
        watch.language = language
        watch.lobby = "name"
        watch.agent("host", f"Speaking {language}", working=False, status=language)
        watch.publish_state()
        return self.lobby_state(watch, self.say(watch, "ask_name"))

    def choose_name(self, watch: Watch, name: str) -> dict:
        """The viewer has said who they are: welcome them back, or meet them, then ask who they support.
        A new name isn't said aloud: the host might well mispronounce it."""
        watch.viewer = name
        watch.lobby = "side"
        back = known_viewer(name)
        watch.agent("host", f"{'Welcome back' if back else 'New here'}: {name}", working=False,
                    status="Welcome back" if back else "New fan")
        watch.publish_state()
        greeting = self.say(watch, "back", name=name) if back else self.say(watch, "nice")
        return self.lobby_state(watch, f"{greeting} {self.fixture_line(watch)}")

    def online(self, watch: Watch) -> list[Persona]:
        """The rivals online for this viewer: fans of the other club who speak their language, the one they
        watched with last time first."""
        fans = [p for p in ROSTER if p.club == self.other(watch.user_team) and p.language == watch.language]
        return sorted(fans, key=lambda p: previous_meeting(watch.user_team, p.id, watch.viewer) is None)

    def card(self, watch: Watch, persona: Persona) -> dict:
        last = previous_meeting(watch.user_team, persona.id, watch.viewer)
        return {"id": persona.id, "name": persona.name, "home": persona.home, "club": persona.club,
                "language": persona.language,
                "watched": bool(last), "last": last.get("result") if last else None}

    def lobby_state(self, watch: Watch, say: str | None = None) -> dict:
        """What the lobby screen shows, and what the host should say next."""
        return {"stage": watch.lobby, "team": watch.user_team, "say": say, "viewer": watch.viewer,
                "language": watch.language,
                "online": [self.card(watch, p) for p in self.online(watch)] if watch.user_team else [],
                "offered": watch.offered,
                "rival": self.card(watch, watch.persona) if watch.persona else None}

    def choose_side(self, watch: Watch, team: str) -> dict:
        """The viewer has told the host who they support. The host offers a rival from the other side."""
        if watch.user_team:
            return self.lobby_state(watch)
        watch.user_team = team
        watch.lobby = "rival"
        watch.agent("host", f"Supporting {self.world.club(team).short_name}; offering rivals", working=False,
                    status="Team chosen")
        online = self.online(watch)
        club = self.club_name(watch, team, short=True)
        first = online[0]
        if previous_meeting(team, first.id, watch.viewer):
            watch.offered = [first.id]
            say = self.say(watch, "rematch", club=club, rival=self.rival_name(watch, first), town=self.town(watch, first))
        elif len(online) == 1:
            watch.offered = [first.id]
            say = self.say(watch, "one", club=club, rival=self.rival_name(watch, first), town=self.town(watch, first))
        else:
            watch.offered = [p.id for p in online]
            say = self.say(watch, "online", club=club, rivals=self._names(online))
        watch.publish_state()
        return self.lobby_state(watch, say)

    def choose_rival(self, watch: Watch, persona_id: str) -> dict:
        persona = next((p for p in ROSTER if p.id == persona_id and p.club == self.other(watch.user_team or "")), None)
        if persona is None or watch.persona:
            return self.lobby_state(watch)
        watch.persona = persona
        watch.rematch = previous_meeting(watch.user_team, persona.id, watch.viewer)
        watch.lobby = "done"
        watch.publish_state()
        return self.lobby_state(watch, self.say(watch, "connecting", rival=self.rival_name(watch, persona)))

    def heard(self, watch: Watch, text: str, detected: str | None = None) -> dict:
        """Something the viewer said in the lobby: their language, their name, their club, then their rival.
        ``detected``: the language speech recognition heard it in."""
        if watch.lobby == "language":
            if language := lines.language_from(text, detected):
                return self.choose_language(watch, language)
            if team := self.side_from(text.lower()):  # straight to their club, in English
                watch.lobby = "side"
                return self.choose_side(watch, team)
            return self._again(watch, text.lower(), lines.LANGUAGE_AGAIN, english=True)
        if watch.lobby == "name":
            if team := self.side_from(text.lower()):  # straight to their club: no name, so no memories
                watch.lobby = "side"
                return self.choose_side(watch, team)
            if text.strip().lower() == "skip":  # testing, without speech: carry on without a name
                watch.lobby = "side"
                return self.lobby_state(watch, self.fixture_line(watch))
            if name := name_from(text):
                return self.choose_name(watch, name)
            return self._again(watch, text.lower(), self.say(watch, "name_again"))
        text = text.lower()
        if watch.lobby == "side":
            team = self.side_from(text)
            if team:
                return self.choose_side(watch, team)
            return self._again(watch, text, self.say(watch, "side_again", home=self.club_name(watch, self.match.home),
                                                     away=self.club_name(watch, self.match.away)))
        if watch.lobby == "rival":
            online = self.online(watch)
            named = [p for p in online if p.heard_in(text) or self.rival_name(watch, p) in text]
            if len(named) == 1:
                return self.choose_rival(watch, named[0].id)
            yes, new = lines.YES.get(watch.language, YES), lines.NEW.get(watch.language, NEW)
            if new.search(text):
                if len(watch.offered) < len(online):
                    others = [p for p in online if p.id not in watch.offered]
                    watch.offered += [p.id for p in others]
                    return self.lobby_state(watch, self.say(watch, "also", rivals=self._names(others)))
                if len(online) == 1:  # nobody else speaks their language: it's this rival or no one
                    return self.lobby_state(watch, self.say(watch, "only", rival=self.rival_name(watch, online[0])))
            if yes.search(text) and not UNSURE.search(text) and len(watch.offered) == 1:
                return self.choose_rival(watch, watch.offered[0])
            offered = [p for p in online if p.id in watch.offered]
            if len(offered) == 1:
                key = "rematch_again" if previous_meeting(watch.user_team, offered[0].id, watch.viewer) else "only"
                question = self.say(watch, key, rival=self.rival_name(watch, offered[0]))
            else:
                question = self.say(watch, "which", rivals=self._names(offered).replace(" and ", " or "))
            return self._again(watch, text, question)
        return self.lobby_state(watch)

    def _again(self, watch: Watch, text: str, question: str, english: bool = False) -> dict:
        """Not understood: the host asks again rather than leaving the viewer waiting. A stray word or a cough
        doesn't count, and it never asks twice within a few seconds."""
        if (len(text.split()) < 2 and not re.search(r"[一-鿿]{2}", text)) or time.monotonic() - watch.reprompted < 6:
            return self.lobby_state(watch)
        watch.reprompted = time.monotonic()
        sorry = lines.LINES["English" if english else watch.language]["sorry"]
        return self.lobby_state(watch, f"{lines.spoken(sorry, 'English' if english else watch.language)} {question}")

    @staticmethod
    def _names(people: list[Persona]) -> str:
        names = [f"{p.name} from {p.home}" for p in people]
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
        self._check_limits(watch)
        persona = watch.persona
        voice = voice if voice in VOICES else persona.voice if persona else self.default_voice
        accent = accent if accent in ACCENTS else persona.accent if persona else self.default_accent
        body = {
            "session": {
                "model": self.model,
                "instructions": rival_instructions(self.world, self.match, accent, watch.user_team, watch.rematch,
                                                   persona, watch.viewer),
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
        watch.voice_began_ms = watch.ms()
        watch.voice = Voice(session_id, sideband)
        watch.voice.opened = time.monotonic()
        watch.voice.span = tracing.tracer.start_span(
            "The Rival: GPT-Live voice session", context=tracing.trace.set_span_in_context(watch.span) if watch.span else None,
            attributes={"gen_ai.request.model": self.model, "nemesys.voice_session": session_id,
                        "nemesys.resumed": watch.voices > 1})
        watch.voice_lost = None
        watch.voice.watcher = asyncio.create_task(self._listen(watch, watch.voice))
        watch.publish_state()
        return watch, answer, watch.voices > 1

    def _check_limits(self, watch: Watch) -> None:
        """Before a voice session starts: how many are running, and how many minutes have been used today. A
        reconnect to a match already under way is always allowed: the viewer is mid-match."""
        if watch.voices:
            return
        live = [w for w in self.watches.values() if w.voice is not None and w.voice.id != "dry-run"]
        if self.max_voices and len(live) >= self.max_voices:
            raise RivalUnavailable("Every rival is busy right now: please try again in a few minutes.")
        if self.daily_voice_s:
            self._new_day()
            running = sum(time.monotonic() - getattr(w.voice, "opened", time.monotonic()) for w in live)
            if self.voice_s_today + running >= self.daily_voice_s:
                raise RivalUnavailable("Nemesys has used today's minutes: please come back tomorrow.")

    def _new_day(self) -> None:
        if (today := time.strftime("%Y-%m-%d")) != self.voice_day:
            self.voice_day, self.voice_s_today = today, 0.0

    def _count_voice(self, voice: Voice) -> None:
        """A voice session has closed: its seconds count towards today's."""
        if opened := getattr(voice, "opened", None):
            self._new_day()
            self.voice_s_today += time.monotonic() - opened
            voice.opened = None

    async def opened(self, watch: Watch, resumed: bool) -> None:
        """The rival's voice is connected: join the viewer and start the match, or pick back up after a drop."""
        if resumed:
            await self.resume(watch)
            return
        if not watch.user_team:  # no lobby (an old client): the rival asks itself
            home, away = self.world.club(self.match.home), self.world.club(self.match.away)
            await watch.commentary(f"{home.name} against {away.name} at {home.stadium} kicks off in a few minutes. "
                                   "Open the conversation: say so, and ask who they're backing.", kind="setup", urgent=True)
            return
        await self._brief(watch, wait=4 if watch.mode == "short" else NEWS_WAIT_S)
        reader = MatchReader(self.world, self.match.home, self.match.away)
        if watch.start:
            self._catch_up(watch, reader)
            await watch.commentary("You've just joined, and the match is already under way. "
                                   f"{self._situation(watch)} Greet them in character and get a dig in about how "
                                   "it's going.", kind="prematch", urgent=True)
        else:
            watch.phase = "prematch"
            watch.publish_state()
            dig = watch.talking_points.pop(0)
            watch.agent("ammo", "Opening dig: " + dig[:90], working=False, status="Opening dig")
            await watch.commentary("You've just joined. Greet them in character, then get your first dig in: " + dig
                                   + " The match hasn't kicked off yet; keep it short.", kind="prematch", urgent=True,
                                   then=with_memory([watch.handoff("ammo"), *receipts(dig, watch)], watch))
        watch.director = asyncio.create_task(self._direct(watch, reader))

    def _catch_up(self, watch: Watch, reader: MatchReader) -> None:
        """Joining a match under way: the reader takes in everything before the start, the screen gets the score,
        the clock and the moments so far, and the agents get the story so far."""
        events = self.match.events
        update = None
        for ev in events[:watch.start]:
            update = reader.apply(ev)
            if ev.type in BIG and (text := describe(self.world, ev)):
                watch.moment(ev, text, past=True)
        if update is None:
            return
        watch.minute, watch.snapshot = update.event.minute, update.snapshot
        watch.score = {t.id: t.score for t in (update.snapshot.home, update.snapshot.away)}
        watch.phase = "second_half" if update.event.period == 2 else "first_half"
        watch.last_read = update.event.elapsed
        watch.dig_score = ()  # the next dig is prepared for the score as it now stands
        watch.publish(event_message(update, events[watch.start].elapsed - update.event.elapsed, 1))
        watch.publish_state()

    async def pause(self, watch: Watch, paused: bool) -> None:
        """The viewer pauses (to take a call, or make a note): the clock stops and the rival goes quiet."""
        if watch.paused == paused:
            return
        watch.paused = paused
        watch.publish_state()
        if paused:
            await watch._send({"type": "session.input_audio.mute"})
            await watch.thinking("The viewer has paused the match for a moment. Stay completely quiet until "
                                 "they're back.", kind="pause", urgent=True)
        else:
            await watch._send({"type": "session.input_audio.unmute"})
            await watch.commentary("They're back and the match is on again. Welcome them back in a few words, "
                                   "in character, and carry on.", kind="pause", urgent=True)

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
                               shown="Pick up where you left off.", urgent=True)

    async def end(self, watch: Watch) -> None:
        if watch.id not in self.watches:
            return
        if watch.director and watch.director is not asyncio.current_task():
            watch.director.cancel()
        for task in (watch.news_task, watch.reading, watch.next_dig, watch.refereeing, watch.user_check,
                     watch.flusher):
            if task and task is not asyncio.current_task():
                task.cancel()
        if watch.voice:
            with suppress(Exception):
                await watch.voice.sideband.send(json.dumps({"type": "session.close"}))
        if watch.keeping and not watch.keeping.done():
            with suppress(Exception):
                await asyncio.wait_for(asyncio.shield(watch.keeping), 20)  # it's what makes next time personal
        watch.publish({"type": "ended"})  # the screen goes to Game Over, voice or no voice (a dry run has none)
        self.watches.pop(watch.id, None)
        self._save(watch)
        if watch.span is not None:
            watch.span.set_attribute("nemesys.result", self._situation(watch))
            watch.span.end()

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
        watch.agent("news", "Writing the fortnight's club news…", status="Writing news")
        try:
            dossier = await traced(watch, "News Agent: write the club news", "news",
                                   news.write_news(self.world, self.match, self.clients))
        except Exception:
            log.exception("News agent failed; the build-up will use the last good stories")
            watch.agent("news", "Couldn't write today; using the last good stories", working=False,
                        status="Using archive")
            return None
        lead = next((x for x in dossier.stories if x.kind == "transfer"), dossier.stories[0])
        watch.agent("news", f"{len(dossier.stories)} stories, e.g. “{lead.headline}”", working=False,
                    status="News ready")
        return dossier

    async def _direct(self, watch: Watch, reader: MatchReader) -> None:
        """Play the match alongside the conversation. The clock always runs; only its speed changes."""
        try:
            pacing = PACING[watch.mode]
            await self._fill(watch, pacing.greeting, lull=99 if watch.mode == "short" else 5, gap=10)
            events = self.match.events
            plan = pace_plan(events, pacing)
            frames = frame_plan(events, plan)
            rival_team = self.other(watch.user_team)
            if not watch.start:
                watch.phase = "first_half"
            for i in range(watch.start, len(events)):
                ev = events[i]
                if i > watch.start:
                    await self._fill(watch, (ev.elapsed - events[i - 1].elapsed) / plan[i - 1])
                frame = frames.get(i) or (frame_plan(events[i:], plan[i:]).get(0) if i == watch.start else None)
                await self._play(watch, reader.apply(ev), frame, rival_team, pacing)
            watch.phase = "full_time"
            watch.publish_state()
            if self.agents:
                watch.reporting = asyncio.create_task(self._report(watch))  # ready by the time the curtain falls
                watch.keeping = asyncio.create_task(self._keep(watch))  # the Memory Keeper, while they say goodbye
            await self._fill(watch, pacing.after)  # post-match banter
            await self._sign_off(watch, rival_team)
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
        spotted = f"{ev.type.value.replace('_', ' ').capitalize()} spotted"
        if ev.type in BIG:
            watch.last_nudge = time.monotonic()
            watch.agent("match", f"{ev.minute}\u2019 {ev.type.value.replace('_', ' ')}", status=spotted)
            settles = self._pacts_cue(watch) if ev.type in (EventType.HALF_TIME, EventType.FULL_TIME) else ""
            bets = [bet_receipt(watch)] if settles else []
            await watch.commentary(f"{BREAK_IN}{text} {cue}{settles}", shown=text, urgent=True,
                                   then=with_memory([watch.handoff("match"), *bets], watch))
            if self.agents and watch.snapshot:
                self._read(watch, None, ev)
            if not self._goal_follows(ev):  # a penalty's reaction can wait for the goal
                told = time.monotonic()
                await self._fill(watch, pacing.holds.get(ev.type, 0), lull=999)
                await self._settle(watch, told, pacing.settle_s)
        elif watch.talking():
            watch.agent("match", f"{ev.minute}\u2019 {ev.type.value.replace('_', ' ')}", status=spotted)
            await watch.thinking(f"(Just happened; mention it if there's a natural moment, or let it go.) {text}",
                                 kind="aside", shown=text, ttl=20, then=[watch.handoff("match")])
            await self._fill(watch, pacing.small_hold, lull=999)
        else:
            watch.last_nudge = time.monotonic()
            watch.agent("match", f"{ev.minute}\u2019 {ev.type.value.replace('_', ' ')}", status=spotted)
            await watch.commentary(f"{text} {cue} Keep it short.", shown=text, ttl=12, then=[watch.handoff("match")])
            await self._fill(watch, pacing.small_hold, lull=999)

    async def _sign_off(self, watch: Watch, rival_team: str) -> None:
        """The rival says goodbye in character, and the watch ends, saved for next time."""
        won = watch.score[rival_team] > watch.score[self.other(rival_team)]
        lost = watch.score[rival_team] < watch.score[self.other(rival_team)]
        mood = ("gloat one last time" if won else "sulk, blame the referee, and concede through gritted teeth" if lost
                else "call it a fair result, just about")
        settle = (" First settle what you agreed during the match: " + " ".join(watch.pacts)
                  + " Make them pay up, or pay up yourself if it went their way.") if watch.pacts else ""
        await watch.commentary(f"Time to go.{settle} Sign off in character: {mood}, then ask if it's the same time "
                               "next week. Two or three sentences, then say goodbye.", kind="sign_off",
                               then=with_memory([bet_receipt(watch)], watch) if watch.pacts else None)
        watch.agent("rival", "Signing off: same time next week?", working=False, status="Signing off")
        await asyncio.sleep(15)  # let the goodbye be said
        await self.end(watch)

    async def _report(self, watch: Watch) -> None:
        """The Match agent's report for the viewer at full time: the three moments that decided it, and why."""
        brief = {**self._sides(watch), "language": watch.language, "final_score": self._situation(watch),
                 "big_moments": watch.moments, "storylines": watch.storylines[-8:],
                 "stats": self._stats(watch).strip(), "bets": watch.pacts}
        watch.agent("match", "Writing your match report…", status="Writing report")
        try:
            report = await traced(watch, "Match Agent: write the match report", "match", self.agents.report(brief))
        except Exception:
            log.exception("Match report failed")
            return
        if report is None:
            return
        watch.report = report.model_dump()
        watch.publish({"type": "report", **watch.report})
        watch.agent("match", report.headline_en, working=False, status="Report ready")

    async def _keep(self, watch: Watch) -> None:
        """The Memory Keeper (Microsoft Phi): what the rival should remember about this viewer next time. No one is
        waiting for it, so a small model that could run on the viewer's own device does the job."""
        if not watch.transcript:
            return
        await asyncio.sleep(30)  # let the post-match chat happen first: it's often where the best lines are
        brief = {"viewer": watch.viewer or "the viewer", "viewer_supports": self.world.club(watch.user_team).name,
                 "rival": watch.persona.name if watch.persona else "the rival", "result": self._situation(watch),
                 "conversation": [{"who": "viewer" if who == "user" else "rival", "said": text.strip()[-300:]}
                                  for who, text in watch.transcript][-40:]}
        watch.agent("ammo", "The Memory Keeper (Phi-4) is noting what to remember about you", status="Remembering")
        try:
            keepsake = await traced(watch, "Memory Keeper: remember the viewer", "keeper",
                                    asyncio.wait_for(self.agents.keep(brief), 40))
        except Exception:
            log.exception("Memory Keeper failed")
            return
        if keepsake is None:
            return
        watch.keepsake = keepsake.model_dump()
        for joke in keepsake.running_jokes[:2]:
            watch.remember("joke", joke, source="rival")
        watch.agent("ammo", "Saved for next time", working=False, status="Memory saved")

    async def _settle(self, watch: Watch, told: float, most: float) -> None:
        """After a big moment, the match waits for the rival to finish reacting before it moves on, up to a point.
        Late on, the big moments come thick and fast; without this the rival falls behind and celebrates a goal
        VAR has already ruled out. The clock on screen waits at the next moment meanwhile."""
        end = time.monotonic() + most
        while (now := time.monotonic()) < end and watch.voice is not None:
            await asyncio.sleep(0.25)
            if watch.paused:
                end += 0.25
                continue
            started = watch.last_rival > told
            if (started and watch.quiet()) or (not started and now - told > 8):
                return

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
            watch.agent("news", "Still writing; the rival starts with the last good stories", working=False,
                        status="Using archive")
        user, rival = self.world.club(watch.user_team), self.world.club(self.other(watch.user_team))
        watch.talking_points = ammo(self.world, self.match, rival, user, watch.dossier, watch.rematch)
        watch.agent("ammo", f"{len(watch.talking_points)} digs lined up: news, the table, last time", working=False,
                    status="Digs loaded")
        if watch.rematch:  # what the rival remembers from last time, on the demo view's memory
            last = watch.rematch
            if last.get("result"):
                watch.remember("last_time", f"{last['when'].capitalize()}: {last['result']}", source=None)
            for quote in quotable(last.get("user_said", []), keep=2):
                watch.remember("last_time", quote, source=None)
            for joke in (last.get("keepsake") or {}).get("running_jokes", [])[:2]:
                watch.remember("joke", joke, source=None)
            for pact in last.get("pacts", [])[:2]:
                predicted = re.match(r"(prediction:|the (user|rival) predicted)", pact, re.I)
                watch.remember("prediction" if predicted else "bet",
                               in_your_words(re.sub(r"^(bet|prediction):\s*", "", pact, flags=re.I), watch), source=None)
        for team in (watch.user_team, rival.id):
            await watch.thinking(club_news(watch.dossier, self.match, team, self.world), kind="news",
                                 shown=f"{self.world.club(team).short_name} news", then=[watch.handoff("news")])
        if self.agents:
            self._prepare_dig(watch)
            watch.refereeing = asyncio.create_task(self._referee(watch))

    async def _fill(self, watch: Watch, seconds: float, lull: float = 7, gap: float = 12) -> None:
        """Wait, but if the conversation goes quiet for a while, hand the rival a fresh talking point."""
        end = time.monotonic() + seconds
        while (now := time.monotonic()) < end:
            await asyncio.sleep(min(0.5, end - now))
            if watch.paused:  # the clock stops: the wait starts again from where it was
                end += time.monotonic() - now
                watch.last_nudge = time.monotonic()
                continue
            now = time.monotonic()
            if watch.voice is None:
                watch.voice_lost = watch.voice_lost or now
                if now - watch.voice_lost > VOICE_GRACE_S or now - watch.started > PACING[watch.mode].max_s:
                    await self.end(watch)  # the viewer has gone: stop the match
                    raise asyncio.CancelledError
                continue
            if now - watch.started > PACING[watch.mode].max_s:
                await self.end(watch)
                raise asyncio.CancelledError
            if now - watch.last_voice > lull and now - watch.last_nudge > gap and not watch.question_open():
                watch.last_nudge = now
                point, receipt = self._take_dig(watch), watch.dig_receipt
                watch.dig_receipt = None
                if point is None:
                    point = (watch.talking_points.pop(0) if watch.talking_points else
                             self._situation(watch) + " Needle them about how it's going, or make a bold prediction.")
                    receipt = None
                watch.agent("ammo", point[:120], working=False, status="Dig sent")
                watch.used_digs.append(point)
                found = [receipt] if receipt else receipts(point, watch)
                await watch.commentary("It's gone quiet. " + point, kind="nudge", shown=point, ttl=8,
                                       then=with_memory([watch.handoff("ammo"), *found], watch))
                if self.agents:
                    self._prepare_dig(watch)

    def _situation(self, watch: Watch) -> str:
        home, away = self.match.home, self.match.away
        score = (f"{self.world.club(home).short_name} {watch.score[home]}-{watch.score[away]} "
                 f"{self.world.club(away).short_name}")
        where = {"lobby": "before kick-off", "prematch": "the pre-match build-up, kick-off any second",
                 "half_time": "half-time", "full_time": "full-time"}.get(watch.phase, f"minute {watch.minute}")
        return f"Right now it's {where}, and the score is {score}.{self._stats(watch)}"

    def _stats(self, watch: Watch) -> str:
        """The numbers a fan glances at: what the rival says when it's asked about possession, shots or cards."""
        snap = watch.snapshot
        if snap is None:
            return ""
        def side(t) -> str:
            men = f", down to {len(t.players_on_pitch)} men" if len(t.players_on_pitch) < 11 else ""
            return (f"{self.world.club(t.id).short_name} {round(t.possession_pct)}% possession, {t.shots} shots "
                    f"({t.shots_on_target} on target), {len(t.booked)} booked{men}")
        return f" Stats: {side(snap.home)}; {side(snap.away)}."

    # -- replay and dry run: the whole experience without paying for a voice --------------------------

    def replay(self, which: str = "last", speed: float = 1.0) -> Watch:
        """Play a saved session back exactly as it happened: the match, the conversation, the agents. No model
        is called, so it costs nothing: for checking the screen without a live voice."""
        files = sorted(SAVED.glob("watch-*.json"), key=lambda f: f.stat().st_mtime, reverse=True) if SAVED.exists() else []
        saved = None
        for f in files:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("recording") and (which == "last" or which in f.name):
                saved = data
                break
        if saved is None:
            raise RivalUnavailable("No recorded session to replay yet: play one live first, or use a dry run")
        watch = Watch(uuid.uuid4().hex[:12], {self.match.home: 0, self.match.away: 0}, mode=saved.get("mode", "match"))
        watch.user_team = saved.get("user_team")
        watch.persona = next((p for p in ROSTER if p.id == saved.get("rival")), None)
        watch.lobby = "done"
        watch.rehearsal = True
        self.watches[watch.id] = watch

        async def play() -> None:
            began = time.monotonic()
            for at, message in saved["recording"]:
                await asyncio.sleep(max(0.0, at / 1000 / speed - (time.monotonic() - began)))
                watch.publish(message)
            await asyncio.sleep(5)
            self.watches.pop(watch.id, None)

        watch.director = asyncio.create_task(play())
        return watch

    async def dry_run(self, team: str = "kil", rival: str = "dez", mode: str = "match", minute: int = 0) -> Watch:
        """The real match and the real agents, with a stand-in for the rival's voice that 'says' what it's told
        as text. Only the agents' model calls cost anything (pennies): for checking the screen and the flow."""
        watch = self.new_watch(mode, minute)
        watch.rehearsal = True
        seed = DATA_DIR / "previous_meeting.json"
        watch.viewer = json.loads(seed.read_text(encoding="utf-8")).get("viewer", "") if seed.exists() else ""
        watch.lobby = "side"
        self.choose_side(watch, team)
        persona = next((p for p in ROSTER if p.id == rival and p.club == self.other(team)), None)
        persona = persona or next(p for p in ROSTER if p.club == self.other(team))
        self.choose_rival(watch, persona.id)
        watch.voice = Voice("dry-run", DrySideband(watch))
        watch.voices = 1
        await self.opened(watch, resumed=False)
        return watch

    def pretend(self, watch: Watch, said: str) -> None:
        """In a rehearsal, a line from the viewer as if they'd said it: through the transcript and the Referee,
        exactly as speech would be (for checking the yellow card without swearing at a live rival)."""
        if not watch.rehearsal:
            raise RivalUnavailable("Only a rehearsal takes pretend lines")
        start = watch.ms()
        watch.heard("user", said)
        watch.publish({"type": "transcript", "who": "user", "delta": said, "start_ms": start, "end_ms": watch.ms()})
        if self.agents:
            self._listen_to_user(watch, said)

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
        watch.agent("match", "Reading the game…", status="Reading game")
        watch.reading = asyncio.create_task(self._reading(watch, brief))

    async def _reading(self, watch: Watch, brief: dict) -> None:
        try:
            story = await traced(watch, "Match Agent: read the game", "match", self.agents.read_match(brief))
        except Exception:
            log.exception("Match agent failed")
            return
        if story and story != watch.storyline:
            watch.storyline = story
            watch.storylines.append(story)
            watch.agent("match", story, working=False, status="Storyline found")
            await watch.thinking(f"The Match agent's read: {story}", kind="storyline", shown=story,
                                 then=[watch.handoff("match")])

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
        watch.agent("ammo", "Choosing the next dig…", status="Choosing dig")
        try:
            choice = await traced(watch, "Ammo Agent: choose the next dig", "ammo", self.agents.next_dig(brief))
        except Exception:
            log.exception("Ammo agent failed")
            return None
        if choice is None:
            return None
        watch.agent("ammo", f"{choice.dig} ({choice.source.replace('_', ' ')})", working=False, status="Dig ready")
        watch.publish(watch.handoff("ammo", "referee"))
        watch.agent("referee", "Checking the dig…", status="Checking dig")
        try:
            verdict = await traced(watch, "Referee: check the dig", "referee",
                                   self.agents.check(choice.dig, self._situation(watch)))
        except Exception:
            log.exception("Referee check failed")
            return None
        if verdict.ok:
            watch.agent("referee", "Dig checked: fine", working=False, status="Dig approved")
            watch.dig_receipt = receipt_of(choice.quote, choice.source, watch)
            return choice.dig
        watch.agent("referee", f"Rewrote a dig: {verdict.reason}" if verdict.line else f"Blocked a dig: {verdict.reason}",
                    working=False, status="Dig rewritten" if verdict.line else "Dig blocked")
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

    def _listen_to_user(self, watch: Watch, delta: str) -> None:
        """Gather what the viewer is saying; once they stop, the Referee hears it."""
        watch.user_pending += delta
        if watch.user_check and not watch.user_check.done():
            watch.user_check.cancel()
        watch.user_check = asyncio.create_task(self._book_if_needed(watch))

    async def _book_if_needed(self, watch: Watch) -> None:
        """Swearing or abuse earns the viewer a yellow card: on screen, and the rival reacts. Only ever a warning."""
        try:
            await asyncio.sleep(USER_SETTLE_S)
        except asyncio.CancelledError:
            return  # they're still talking
        said, watch.user_pending = watch.user_pending.strip(), ""
        # A stray noise isn't worth checking. Chinese has no spaces, so there it's characters that count.
        if len(said.split()) < 2 and len(re.findall(r"[一-鿿]", said)) < 2:
            return
        # Two stages: Azure AI Content Safety stops anything severe at once; the model judges banter from abuse.
        watch.agent("referee", f"Checking what you said: {said[:80]}", status="Checking line")
        rating = None if self.agents.offline else await traced(
            watch, "Referee: Azure AI Content Safety gate", None, safety.rate(said))
        if rating and rating.severe:
            reason = rating.reason
        else:
            try:
                booking = await traced(watch, "Referee: card or banter?", "referee", self.agents.booking(said))
            except Exception:
                log.exception("Referee booking check failed")
                return
            if not booking.card:
                watch.agent("referee", "Banter, not abuse: no card", working=False, status="All good")
                return
            reason = booking.reason or "Language"
        watch.cards += 1
        second = watch.cards >= 2
        watch.publish({"type": "card", "colour": "yellow", "reason": reason, "count": watch.cards})
        watch.agent("referee", f"Yellow card for the viewer: {reason}" + (" (another warning)" if second else ""),
                    working=False, status="Yellow card!")
        await watch.commentary(
            f"The referee has just shown the user {'another' if second else 'a'} yellow card ({reason.lower()}). "
            "React in character, enjoying it: 'ooh, you've been booked!', then carry on. Don't repeat what they said.",
            kind="card", ttl=15, then=[watch.handoff("referee")])

    async def _remember_pacts(self, watch: Watch) -> None:
        """The Ammo agent notes any bet, dare or promise the two of them have just made, and reminds the rival."""
        said = sum(len(text) for _, text in watch.transcript)
        if said - watch.pact_chars < REVIEW_MIN_CHARS:
            return
        watch.pact_chars = said
        lines = [{"who": "user" if who == "user" else "rival", "said": text.strip()[-400:]}
                 for who, text in watch.transcript[-8:]]
        try:
            pacts = await traced(watch, "Memory: note bets and predictions", "memory",
                                 self.agents.pacts(watch.pacts, lines, self._sides(watch) if watch.user_team else None))
        except Exception:
            log.exception("Ammo agent's bet memory failed")
            return
        new = [p for p in pacts if p not in watch.pacts]
        watch.pacts = pacts
        for pact in new:
            watch.agent("ammo", f"Noted a bet: {pact}", working=False, status="Bet noted")
            kind = "prediction" if pact.lower().startswith("prediction") else "bet"
            watch.remember(kind, in_your_words(re.sub(r"^(bet|prediction):\s*", "", pact, flags=re.I), watch))
            await watch.thinking(f"Remember this, and call it in when the match settles it: {pact}", kind="pact",
                                 shown=pact, then=[watch.handoff("ammo")])

    def _pacts_cue(self, watch: Watch) -> str:
        return (" Don't forget what you agreed: " + " ".join(watch.pacts) + " Call it in now.") if watch.pacts else ""

    async def _referee(self, watch: Watch) -> None:
        """Watch the conversation as it happens and step in if the rival goes too far or the user is upset."""
        try:
            while watch.id in self.watches:
                await asyncio.sleep(REVIEW_EVERY_S)
                if watch.voice is not None and not watch.paused:
                    await self._remember_pacts(watch)
                said = sum(len(text) for who, text in watch.transcript if who == "rival")
                if said - watch.reviewed_chars < REVIEW_MIN_CHARS or watch.voice is None or watch.paused:
                    continue
                watch.reviewed_chars = said
                lines = [{"who": "user" if who == "user" else "rival", "said": text.strip()[-400:]}
                         for who, text in watch.transcript[-8:]]
                watch.agent("referee", "Listening in on the conversation…", status="Listening in")
                try:
                    verdict = await traced(watch, "Referee: review the conversation", "referee",
                                           self.agents.review(lines, self._situation(watch)))
                except Exception:
                    log.exception("Referee review failed")
                    continue
                if verdict.verdict == "fine" or not verdict.instruction:
                    watch.agent("referee", "All good-natured", working=False, status="All good")
                    continue
                if time.monotonic() - watch.last_steer < STEER_EVERY_S:
                    watch.agent("referee", "Already stepped in; giving the rival time to adjust", working=False,
                                status="Letting it settle")
                    continue
                watch.last_steer = time.monotonic()
                watch.agent("referee", f"Stepped in: {verdict.instruction}", working=False, status="Stepped in")
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
                    # When it was said, not when the text arrived: the viewer's words are transcribed after they
                    # finish, so they arrive after the rival's reply has started. GPT-Live's timeline starts again
                    # with each voice session, so it's moved onto the watch's.
                    if who == "user" and self.agents:
                        self._listen_to_user(watch, ev["delta"])
                    said = ev.get("start_ms")
                    start = watch.voice_began_ms + said if isinstance(said, (int, float)) else watch.ms()
                    watch.publish({"type": "transcript", "who": who, "delta": ev["delta"], "start_ms": start,
                                   "end_ms": watch.voice_began_ms + ev["end_ms"]
                                   if isinstance(ev.get("end_ms"), (int, float)) else start})
                elif kind == "session.delegation.created":
                    # The rival wants to look something up. It has everything already; tell it so, at once.
                    await watch.thinking(self._situation(watch) + " You already know all this; carry on the banter.",
                                         kind="delegation", delegation_id=ev["delegation"]["id"], urgent=True)
                elif kind == "error":
                    err = ev.get("error", {})
                    if err.get("code") == "content_filter":
                        lines = " / ".join(f"{who}: {text.strip()[-160:]}" for who, text in watch.transcript[-3:])
                        log.warning("Content filter stopped a line in %s. Last lines: %s", watch.id, lines)
                        watch.note("filter", "Azure's content filter stopped a line. The match carries on.")
                        watch.agent("referee", "Azure AI Content Safety stopped a line; the match carries on",
                                    status="Line stopped")
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
            self._count_voice(voice)
            if voice.span is not None:
                voice.span.set_attribute("nemesys.closed", reason)
                voice.span.end()
            if watch.voice is voice:
                watch.voice = None
                watch.voice_lost = time.monotonic()
                if reason != "close_requested":
                    watch.note("voice", f"The voice connection dropped ({reason}). Reconnecting; the match carries on.")
                watch.publish_state()

    def _save(self, watch: Watch) -> None:
        if not watch.transcript or watch.rehearsal:
            return
        with suppress(Exception):
            SAVED.mkdir(parents=True, exist_ok=True)
            home, away = self.match.home, self.match.away
            result = (f"{self.world.club(home).name} {watch.score[home]}-{watch.score[away]} "
                      f"{self.world.club(away).name}") if watch.phase != "setup" else ""
            (SAVED / f"watch-{time.strftime('%Y%m%d-%H%M%S')}-{watch.id}.json").write_text(json.dumps({
                "viewer": watch.viewer, "user_team": watch.user_team,
                "rival": watch.persona.id if watch.persona else None,
                "pacts": watch.pacts, "keepsake": watch.keepsake,
                "mode": watch.mode, "recording": watch.recording,
                "result": result, "reached": watch.minute or watch.phase,
                "transcript": [(who, text.strip()) for who, text in watch.transcript],
                "feed": watch.feed,
            }, indent=1, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def side_from(heard: str) -> str | None:
        found = [team for team, words in SIDE_WORDS.items()
                 if any(w in heard if not w.isascii() else re.search(rf"\b{w}", heard) for w in words)]
        return found[0] if len(found) == 1 else None
