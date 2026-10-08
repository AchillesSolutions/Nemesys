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
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from .agents import CommentarySession, ViewerProfile
from .agents.host import Host, HostReply
from .agents.llm import ClientFactory
from .chapters import Chapter, build_chapters, chapter_at
from .match_reader import MatchReader, ReaderUpdate
from .models import Match, World

log = logging.getLogger(__name__)

Send = Callable[[dict], Awaitable[None]]
Mode = Literal["full", "key_moments"]


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
            self.reader.apply(self.match.events[self.index])
            self.index += 1
        self.position = elapsed
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
                self._skip_to_next_chapter(ev.elapsed)
                continue

            delay = (ev.elapsed - self.position) / self.speed
            if delay > 0 and not await self._wait(delay):
                continue  # woken by a control change: re-evaluate
            await self._play(ev)

    async def _play(self, ev) -> None:
        self.index += 1
        self.position = ev.elapsed
        update = self.reader.apply(ev)
        chapter = chapter_at(self.chapters, ev.elapsed) if self.mode == "key_moments" else None
        if chapter and chapter is not self._chapter:
            self._chapter = chapter
            await self.send({"type": "chapter", "chapter": chapter.model_dump()})
        await self.send(event_message(update, self._gap_to_next(ev, chapter)))
        if self.session.should_wake(update):
            task = asyncio.create_task(self._commentate(update))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _commentate(self, update: ReaderUpdate) -> None:
        try:
            async for commentary in self.session.respond_now(update):
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

    def _skip_to_next_chapter(self, elapsed: int) -> None:
        upcoming = next((c for c in self.chapters if c.start > elapsed), None)
        self.seek(upcoming.start if upcoming else self.match.events[-1].elapsed + 1)

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


def event_message(update: ReaderUpdate, next_gap: int | None = None) -> dict:
    """What the browser needs to draw one event: the event, the live state after it, and how
    many match seconds until the next one (so the ball and clock can move smoothly in between)."""
    return {
        "type": "event",
        "event": update.event.model_dump(mode="json", exclude_none=True),
        "next_gap": next_gap,
        "salience": update.salience,
        "signals": update.signals,
        "snapshot": update.snapshot.model_dump(mode="json"),
    }
