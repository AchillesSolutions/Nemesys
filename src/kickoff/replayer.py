"""Replays a generated match as a live feed.

Events are released at their real spacing divided by ``speed``: 1.0 is real
match time and 60 plays a minute of football per second. ``start_minute``
skips ahead, which is handy for demos.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from .models import Match, MatchEvent


async def replay(match: Match, speed: float = 1.0, start_minute: float = 0) -> AsyncIterator[MatchEvent]:
    if speed <= 0:
        raise ValueError("speed must be positive")
    start = int(start_minute * 60)
    previous: int | None = None
    for ev in match.events:
        if ev.elapsed < start:
            continue
        if previous is not None and ev.elapsed > previous:
            await asyncio.sleep((ev.elapsed - previous) / speed)
        previous = ev.elapsed
        yield ev
