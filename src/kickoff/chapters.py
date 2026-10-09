"""Chapters for Key moments mode.

A chapter is a short window of play around one moment worth seeing: a goal,
the first offside, the VAR penalty. Playing only the chapters turns a
90-minute match into about ten minutes, which is what judges and the demo
video need. Chapters are derived from the events, so they follow the match.
"""

from __future__ import annotations

from pydantic import BaseModel

from .agents.i18n import HEADLINE_TITLES, concept_label
from .agents.viewer import Language
from .models import EventType, Match

LEAD_IN_S = 8     # just enough build-up to see how the moment happened, not general passing
LEAD_OUT_S = 8    # and a little after; the player also lets commentary finish before moving on
MERGE_GAP_S = 150  # moments closer than this play as one (a whole VAR review: check, penalty, goal)

# The first time each of these happens is worth a chapter, for the teaching moment.
FIRST_OCCURRENCE = {
    EventType.KICK_OFF: "kick_off",
    EventType.OFFSIDE: "offside",
    EventType.FREE_KICK: "direct_free_kick",  # only direct free kicks with a wall (see below)
    EventType.YELLOW_CARD: "yellow_card",
    EventType.SUBSTITUTION: "substitution",
}
# Every occurrence of these is a chapter.
ALWAYS = {EventType.VAR_CHECK, EventType.GOAL, EventType.GOAL_DISALLOWED, EventType.PENALTY, EventType.RED_CARD,
          EventType.HALF_TIME, EventType.FULL_TIME}


class Chapter(BaseModel):
    id: int
    start: int  # match elapsed seconds
    end: int
    moment: int  # elapsed second of the key event
    event_id: int
    minute: str
    titles: dict[Language, str]


def _titles(ev_type: EventType, concept: str | None) -> dict[Language, str]:
    if ev_type in HEADLINE_TITLES:
        return dict(HEADLINE_TITLES[ev_type])
    key = concept or ev_type.value
    return {lang: concept_label(key, lang) for lang in Language}


def build_chapters(match: Match) -> list[Chapter]:
    picked = []
    seen_first: set[EventType] = set()
    for ev in match.events:
        if ev.type in ALWAYS:
            concept = {EventType.HALF_TIME: "half_time", EventType.VAR_CHECK: "var"}.get(ev.type)
            picked.append((ev, concept))
        elif ev.type in FIRST_OCCURRENCE and ev.type not in seen_first:
            if ev.type == EventType.FREE_KICK and not ev.detail.get("wall"):
                continue
            seen_first.add(ev.type)
            picked.append((ev, FIRST_OCCURRENCE[ev.type]))

    chapters: list[Chapter] = []
    for ev, concept in picked:
        start, end = max(0, ev.elapsed - LEAD_IN_S), ev.elapsed + LEAD_OUT_S
        if chapters and start <= chapters[-1].end + MERGE_GAP_S:
            # Close windows (VAR check, penalty, goal) become one chapter named after the first moment.
            chapters[-1].end = max(chapters[-1].end, end)
            continue
        chapters.append(Chapter(id=len(chapters) + 1, start=start, end=end, moment=ev.elapsed, event_id=ev.id,
                                minute=ev.minute, titles=_titles(ev.type, concept)))
    last = match.events[-1].elapsed
    chapters[-1].end = min(chapters[-1].end, last)
    return chapters


def chapter_at(chapters: list[Chapter], elapsed: int) -> Chapter | None:
    return next((c for c in chapters if c.start <= elapsed <= c.end), None)
