"""Who is watching, and what they already know.

Concepts are language-independent ids (``offside``, ``var`` ...), so the
record of what a viewer has learned works the same in English, Mandarin
or Spanish.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Language(StrEnum):
    EN = "en"
    ZH = "zh"
    ES = "es"


LANGUAGE_NAMES = {Language.EN: "English", Language.ZH: "Simplified Chinese (Mandarin)",
                  Language.ES: "Latin American Spanish"}


class HelpLevel(StrEnum):
    BEGINNER = "beginner"          # explain everything, assume nothing
    GETTING_THERE = "getting_there"  # knows the basics, explain the less common rules
    BIG_PICTURE = "big_picture"    # skip rules, focus on story and stakes
    ANALYST = "analyst"            # tactics and numbers, for analysts and broadcasters


class ViewerProfile(BaseModel):
    id: str = "viewer"
    team: str | None = Field(None, description="Club id the viewer supports, or None for neutral")
    language: Language = Language.EN
    level: HelpLevel = HelpLevel.BEGINNER
    hands_free: bool = Field(False, description="Watching alone: the microphone is always on and everything said "
                                                "goes to Achilles, without saying its name")


# Concepts every level is assumed to know already.
ASSUMED_KNOWN: dict[HelpLevel, set[str]] = {
    HelpLevel.BEGINNER: set(),
    HelpLevel.GETTING_THERE: {"kick_off", "goal", "throw_in", "goal_kick", "corner", "foul", "free_kick",
                              "yellow_card", "half_time", "substitution", "possession", "counter_attack"},
    HelpLevel.BIG_PICTURE: {"*"},
    HelpLevel.ANALYST: {"*"},
}


class Knowledge(BaseModel):
    """How many times each concept has been explained to this viewer."""

    seen: dict[str, int] = Field(default_factory=dict)
    last_explained: dict[str, int] = Field(default_factory=dict, description="Match elapsed seconds")
    learned_order: list[str] = Field(default_factory=list)

    def times_explained(self, concept: str) -> int:
        return self.seen.get(concept, 0)

    def record(self, concept: str, elapsed: int) -> None:
        if concept not in self.seen:
            self.learned_order.append(concept)
        self.seen[concept] = self.seen.get(concept, 0) + 1
        self.last_explained[concept] = elapsed


REMINDER_GAP_S = 5 * 60


def depth_for(profile: ViewerProfile, knowledge: Knowledge, concept: str, now: int) -> str | None:
    """How much explanation a concept deserves now: full, reminder, nod, or None (skip)."""
    assumed = ASSUMED_KNOWN[profile.level]
    if "*" in assumed or concept in assumed:
        return None
    last = knowledge.last_explained.get(concept)
    if last is not None and now - last < REMINDER_GAP_S:
        return None
    match knowledge.times_explained(concept):
        case 0:
            return "full"
        case 1:
            return "reminder"
        case 2:
            return "nod"
        case _:
            return None
