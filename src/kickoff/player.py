"""Plays the match for one viewer: replay, Match reader and commentary together.

The player owns the clock. It releases events at match speed, feeds every
event to the Match reader, and hands updates to the viewer's commentary
session without waiting for the agents (they answer in the background, so
the ball keeps moving while they think).

It also handles the viewer's controls: pause, speed, jumping to a chapter,
and changing team, language or help level mid-match. Jumps replay the skipped
events silently through a fresh reader, so the score and statistics are
always exactly right, while what the viewer has learned is kept.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from .agents import CommentarySession, ViewerProfile
from .agents.session import speech_seconds
from .agents.host import Host, HostReply
from .agents.llm import ClientFactory
from .chapters import Chapter, build_chapters, chapter_at
from .match_reader import SALIENCE, MatchReader, ReaderUpdate
from .models import EventType, Match, World

log = logging.getLogger(__name__)

Send = Callable[[dict], Awaitable[None]]
Mode = Literal["full", "key_moments"]

QUIET_PACE = 8.0         # quiet play inside a key moment runs this much faster...
QUIET_AHEAD_S = 15       # ...when nothing notable is coming within this many match seconds
QUIET_BEHIND_S = 8       # ...and nothing notable has just happened
BRIDGE_MIN_GAP_S = 240   # jumps of at least four minutes get a bridging line between moments
BRIDGE_MAX_PAUSE_S = 10
LAND_MAX_S = 6           # how long to let a moment's explanation finish before skipping ahead
MAX_STILL_S = 4.0        # in highlights, the longest the picture stands still unless Kick-Off is talking
HOLD_MAX_S = 20          # the longest play waits for an explanation to finish before the next big moment
BUILD_UP_S = 6           # ...and it waits before the build-up, so the attack itself is seen while it is called
SPEECH_SLACK_S = 1.0     # the voice starts a little after the line is sent (audio download)


class MatchPlayer:
    def __init__(self, world: World, match: Match, profile: ViewerProfile, clients: ClientFactory, send: Send,
                 mode: Mode = "full", speed: float = 1.0):
        self.world, self.match, self.send = world, match, send
        self.mode: Mode = mode
        self.speed = speed
        self.chapters = build_chapters(match)
        self.session = CommentarySession(world, match, profile, clients)
        self.reader = MatchReader(world, match.home, match.away)
        self.host = Host(clients("host"), world, match, get_profile=lambda: self.session.profile,
                         set_profile=self.set_profile, reader=lambda: self.reader, played=lambda: self.index)
        self.index = 0          # next event to play
        self.position = 0       # match elapsed seconds reached
        self.paused = False
        self.finished = False
        self._wake = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._chapter: Chapter | None = None
        self._last_update: ReaderUpdate | None = None
        self._jumped_to = 0  # where play last jumped to: commentary about earlier moments is stale
        self.voice_speaking: bool | None = None  # reported by the viewer's browser; None = no voice reports
        self._notable = sorted(e.elapsed for e in match.events if SALIENCE.get(e.type, 0) >= 2)
        self._held_for: set[int] = set()  # big moments whose build-up has already waited for quiet

    # -- controls --------------------------------------------------------------

    def pause(self) -> None:
        self.paused = True
        self._wake.set()

    def resume(self) -> None:
        self.paused = False
        self._wake.set()

    def set_speed(self, speed: float) -> None:
        self.speed = max(0.25, min(speed, 600.0))
        self._wake.set()

    def set_mode(self, mode: Mode) -> None:
        self.mode = mode
        self._wake.set()

    def set_profile(self, **changes) -> ViewerProfile:
        """Change team, language or level. What the viewer has learned is kept."""
        self.session.profile = ViewerProfile.model_validate(self.session.profile.model_dump() | changes)
        return self.session.profile

    async def ask(self, question: str, spoken: str | None = None) -> HostReply:
        """The viewer spoke to Kick-Off. The Host answers and may change their settings."""
        return await self.host.ask(question, spoken)

    def seek(self, elapsed: int) -> None:
        """Jump to a point in the match, replaying skipped events silently."""
        if elapsed < self.position:
            self.reader = MatchReader(self.world, self.match.home, self.match.away)
            self.index = 0
        while self.index < len(self.match.events) and self.match.events[self.index].elapsed < elapsed:
            self._last_update = self.reader.apply(self.match.events[self.index])
            self.index += 1
        self.position = elapsed
        self._jumped_to = elapsed
        self._held_for = {t for t in self._held_for if t < elapsed}  # rewatching waits for quiet again
        self.finished = False
        self._chapter = None
        self._wake.set()

    def next_chapter(self) -> None:
        """Skip to the next key moment after the current position."""
        upcoming = next((c for c in self.chapters if c.start > self.position), None)
        self.mode = "key_moments"
        self.seek(upcoming.start if upcoming else self.match.events[-1].elapsed)

    def seek_chapter(self, chapter_id: int) -> None:
        chapter = next(c for c in self.chapters if c.id == chapter_id)
        self.mode = "key_moments"
        self.seek(chapter.start)

    # -- main loop ---------------------------------------------------------------

    async def run(self) -> None:
        await self.send({"type": "ready", "chapters": [c.model_dump() for c in self.chapters],
                         "profile": self.session.profile.model_dump(), "mode": self.mode, "speed": self.speed})
        while True:
            if self.index >= len(self.match.events):
                if not self.finished:
                    self.finished = True
                    await self.send({"type": "finished"})
                await self._wait(None)
                continue
            if self.paused:
                await self._wait(None)
                continue

            ev = self.match.events[self.index]
            if self.mode == "key_moments" and not chapter_at(self.chapters, ev.elapsed):
                await self._bridge_to_next_chapter(ev.elapsed)
                continue

            if self._is_restart_after_half_time(ev):
                await self._let_moment_land()  # the half-time break: the summary is said before play restarts
            elif self.mode == "key_moments" and self.speed <= 4 and self._big_moment_coming(ev):
                await self._hold_for_explanation()  # never play a big moment over an explanation
            delay = self._delay_to(ev)
            if delay > 0 and not await self._wait(delay):
                continue  # woken by a control change: re-evaluate
            await self._play(ev)

    async def _play(self, ev) -> None:
        self.index += 1
        self.position = ev.elapsed
        update = self.reader.apply(ev)
        self._last_update = update
        chapter = chapter_at(self.chapters, ev.elapsed) if self.mode == "key_moments" else None
        if chapter and chapter is not self._chapter:
            self._chapter = chapter
            await self.send({"type": "chapter", "chapter": chapter.model_dump()})
        gap = self._gap_to_next(ev, chapter)
        nxt = self.match.events[self.index] if self.index < len(self.match.events) else None
        pace = self.pace_at(ev.elapsed)
        if self.mode == "key_moments" and gap and nxt and self._delay_to(nxt) > 0:
            pace = round(max(pace, gap / (self._delay_to(nxt) * self.speed)), 2)  # dead time is compressed
        await self.send(event_message(update, gap, pace))
        if self._outranked_by_next(ev, nxt):
            return  # a second yellow and its red card come together: talk about the red, not before it
        if self.session.should_wake(update):
            task = asyncio.create_task(self._commentate(update))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif call := self.session.call_only(update):
            await self.send({"type": "commentary", **call.model_dump(mode="json")})

    async def _commentate(self, update: ReaderUpdate) -> None:
        try:
            async for commentary in self.session.respond_now(update):
                if update.event.elapsed < self._jumped_to:
                    return  # play has moved on past this moment while the agents were thinking
                await self.send({"type": "commentary", **commentary.model_dump(mode="json")})
        except Exception:
            log.exception("commentary failed for event %s", update.event.id)

    def _gap_to_next(self, ev, chapter: Chapter | None) -> int | None:
        """Match seconds until the next event plays, or None if play jumps (next chapter, or the end)."""
        if self.index >= len(self.match.events):
            return None
        nxt = self.match.events[self.index]
        if self.mode == "key_moments" and (chapter is None or nxt.elapsed > chapter.end):
            return None
        return nxt.elapsed - ev.elapsed

    def _big_moment_coming(self, ev) -> bool:
        """Is this the start of the build-up to a big moment (a goal's attack, the foul before a card)?
        Play waits here once, so the whole move is seen and called as it happens, not under an
        explanation of something else."""
        i = bisect.bisect_left(self._notable, ev.elapsed)
        if i == len(self._notable) or self._notable[i] - ev.elapsed > BUILD_UP_S or self._notable[i] in self._held_for:
            return False
        self._held_for.add(self._notable[i])
        return True

    @staticmethod
    def _outranked_by_next(ev, nxt) -> bool:
        """Events in the same second (a shot and its goal, a second yellow and its red card) are one
        moment: only the biggest of them is talked about."""
        return (nxt is not None and nxt.elapsed == ev.elapsed
                and SALIENCE.get(nxt.type, 0) > SALIENCE.get(ev.type, 0))

    async def _hold_for_explanation(self) -> None:
        """Wait until what is being explained has been written and said, so the viewer can take it in."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + HOLD_MAX_S
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=HOLD_MAX_S)
        while self._still_talking() and loop.time() < deadline:
            await self._wait(0.25)

    def _still_talking(self) -> bool:
        """Is Achilles still speaking? The browser says so exactly; otherwise use the estimate."""
        if self.voice_speaking is not None:
            return self.voice_speaking or self.session.busy_until > self.session.clock()
        return self.session.busy_until + SPEECH_SLACK_S > self.session.clock()

    def set_voice_speaking(self, speaking: bool) -> None:
        self.voice_speaking = speaking  # the hold checks this several times a second; no need to wake playback

    def _is_restart_after_half_time(self, ev) -> bool:
        last = self._last_update.event if self._last_update else None
        return ev.type == EventType.KICK_OFF and last is not None and last.type == EventType.HALF_TIME

    def _delay_to(self, ev) -> float:
        """Real seconds to wait before playing ``ev``. In highlights, dead time (VAR reviews, celebrations,
        substitutions) never leaves the screen silent and still: at most a few seconds, or until
        Kick-Off finishes what it is saying."""
        natural = (ev.elapsed - self.position) / (self.speed * self.pace_at(self.position))
        if self.mode != "key_moments":
            return natural
        talking = max(0.0, self.session.busy_until - self.session.clock())
        return min(natural, max(MAX_STILL_S / max(1.0, self.speed), talking + 0.4))

    def pace_at(self, elapsed: int) -> float:
        """How fast play runs from here: quiet stretches inside a key moment are compressed."""
        if self.mode != "key_moments":
            return 1.0
        i = bisect.bisect_right(self._notable, elapsed)
        ahead = self._notable[i] - elapsed if i < len(self._notable) else 10 ** 6
        behind = elapsed - self._notable[i - 1] if i > 0 else 10 ** 6
        return QUIET_PACE if ahead > QUIET_AHEAD_S and behind > QUIET_BEHIND_S else 1.0

    async def _bridge_to_next_chapter(self, elapsed: int) -> None:
        """Skip to the next key moment. After a long gap, the agents may sum up what was skipped."""
        upcoming = next((c for c in self.chapters if c.start > elapsed), None)
        await self._let_moment_land()
        before = self._last_update
        self.seek(upcoming.start if upcoming else self.match.events[-1].elapsed + 1)
        if upcoming and before and self._last_update and upcoming.start - before.event.elapsed >= BRIDGE_MIN_GAP_S:
            await self._bridge(before, self._last_update)

    async def _let_moment_land(self) -> None:
        """Before skipping ahead, finish the moment: its lead-out time, any explanation still being
        written, and what Kick-Off is still saying (within reason). Fast-forwarding skips the waiting."""
        if self.speed > 4:
            if self._tasks:
                await asyncio.wait(list(self._tasks), timeout=0.5)
            return
        if self._chapter and self._chapter.end > self.position:
            await self._wait((self._chapter.end - self.position) / (self.speed * self.pace_at(self.position)))
        deadline = asyncio.get_running_loop().time() + LAND_MAX_S
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=LAND_MAX_S)
        while self.session.is_speaking() and asyncio.get_running_loop().time() < deadline:
            await self._wait(0.25)

    async def _bridge(self, before: ReaderUpdate, after: ReaderUpdate) -> None:
        gap_s = after.event.elapsed - before.event.elapsed
        b, a = before.snapshot, after.snapshot
        signal = {
            "kind": "bridge", "from_minute": before.event.minute, "to_minute": after.event.minute,
            "possession_in_gap": self.reader.possession_share(gap_s),
            "shots_in_gap": {a.home.id: a.home.shots - b.home.shots, a.away.id: a.away.shots - b.away.shots},
            "corners_in_gap": {a.home.id: a.home.corners - b.home.corners, a.away.id: a.away.corners - b.away.corners},
        }
        await self.send({"type": "bridge", "from_minute": signal["from_minute"], "to_minute": signal["to_minute"]})
        # The bridging line is written while play moves on to the next moment (whose quiet build-up it
        # covers), rather than holding the picture still.
        task = asyncio.create_task(self._bridge_line(after.model_copy(update={"signals": [signal], "salience": 0})))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        await self._wait(1.5 if self.speed <= 4 else 0.05)

    async def _bridge_line(self, update: ReaderUpdate) -> None:
        try:
            async for commentary in self.session.respond_now(update):
                await self.send({"type": "commentary", **commentary.model_dump(mode="json")})
        except Exception:
            log.exception("bridge commentary failed")

    async def _wait(self, seconds: float | None) -> bool:
        """Sleep, but wake early on any control change. True if the full time passed."""
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
            return False
        except TimeoutError:
            return True

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()


def event_message(update: ReaderUpdate, next_gap: int | None = None, pace: float = 1.0) -> dict:
    """What the browser needs to draw one event: the event, the live state after it, how many match
    seconds until the next one, and how fast that stretch plays (so ball and clock move smoothly)."""
    return {
        "type": "event",
        "event": update.event.model_dump(mode="json", exclude_none=True),
        "next_gap": next_gap,
        "pace": pace,
        "salience": update.salience,
        "signals": update.signals,
        "snapshot": update.snapshot.model_dump(mode="json"),
    }
