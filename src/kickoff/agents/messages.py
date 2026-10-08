"""Messages passed between agents in the commentary workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from ..match_reader import ReaderUpdate
from .viewer import Knowledge, ViewerProfile


class Tone(StrEnum):
    NEUTRAL = "neutral"
    CALM = "calm"
    EXCITED = "excited"
    HOPEFUL = "hopeful"
    TENSE = "tense"
    DISAPPOINTED = "disappointed"


class ProposalDraft(BaseModel):
    """What a specialist model returns for one thing it would like to say."""

    text: str = Field(description="What to say, in the viewer's language, ready to be spoken aloud")
    priority: int = Field(description="1 (nice to have) to 10 (must say now)")
    concept: str | None = Field(description="Concept id this teaches, if any")


class SpecialistReply(BaseModel):
    proposals: list[ProposalDraft]


@dataclass
class Proposal:
    id: int
    agent: str
    text: str
    priority: int
    kind: Literal["explain", "story", "stakes"]
    concept: str | None = None
    made_at: int = 0  # match elapsed seconds


@dataclass
class Moment:
    """Everything the specialists need to know about one trigger."""

    update: ReaderUpdate
    profile: ViewerProfile
    knowledge: Knowledge
    event_text: str
    situation: str
    concepts: list[str]
    recent_lines: list[str]
    backlog: list[Proposal] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)

    @property
    def salience(self) -> int:
        return self.update.salience

    @property
    def signal_kinds(self) -> set[str]:
        return {s["kind"] for s in self.signals}


@dataclass
class SpecialistResult:
    agent: str
    moment: Moment
    proposals: list[Proposal]


class Line(BaseModel):
    """One thing Kick-Off actually says and shows."""

    text: str
    tone: Tone = Tone.NEUTRAL
    card_title: str = ""
    sources: list[str] = Field(default_factory=list)
    proposal_ids: list[int] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    headline: bool = False


class ProducerLine(BaseModel):
    text: str = Field(description="Final words to speak, in the viewer's language")
    tone: Tone
    card_title: str = Field(description="Two to four word title for the on-screen card, in the viewer's language")
    proposal_ids: list[int] = Field(description="Ids of the proposals this line uses")


class ProducerReply(BaseModel):
    lines: list[ProducerLine]
    defer_ids: list[int] = Field(description="Proposal ids worth saving for a quieter moment")
    reasoning: str = Field(description="One short English sentence on why, for the debug panel")


@dataclass
class Decision:
    moment: Moment
    proposals: list[Proposal]
    lines: list[Line]
    deferred: list[Proposal]
    reasoning: str
