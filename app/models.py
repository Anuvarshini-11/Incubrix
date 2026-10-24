from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

class Word(BaseModel):
    id: str
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    text: str
    speaker_id: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)

class Source(BaseModel):
    media_id: str
    sha256: str
    duration_s: float = Field(gt=0)
    width: int | None = None
    height: int | None = None
    frame_rate: str = "25/1"
    has_audio: bool = True

class Segment(BaseModel):
    id: str
    source_start_s: float = Field(ge=0)
    source_end_s: float = Field(gt=0)
    speed: float = Field(default=1, ge=0.25, le=4)

class Anchor(BaseModel):
    word_ids: list[str] = Field(default_factory=list)
    source_start_s: float | None = None
    source_end_s: float | None = None

class CaptionStyle(BaseModel):
    color: str = "white"
    size: int = Field(default=36, ge=8, le=160)
    position: Literal["top", "center", "bottom"] = "bottom"

class Caption(BaseModel):
    id: str
    text: str
    anchor: Anchor
    style: CaptionStyle = Field(default_factory=CaptionStyle)

class Bleep(BaseModel):
    id: str
    source_start_s: float = Field(ge=0)
    source_end_s: float = Field(gt=0)

class Timeline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeline_id: str
    schema_version: str = "1.0.0"
    source: Source
    transcript: list[Word] = Field(default_factory=list)
    segments: list[Segment]
    captions: list[Caption] = Field(default_factory=list)
    bleeps: list[Bleep] = Field(default_factory=list)
    mute_audio: bool = False
    audio_gain: float = Field(default=1.0, ge=0, le=1)
    background_music_id: str | None = None
    music_gain: float = Field(default=0.25, ge=0, le=1)
    duck_music: bool = True
    revision: int = 0
    warnings: list[str] = Field(default_factory=list)

class InstructionResult(BaseModel):
    status: Literal["applied", "needs_clarification", "rejected"]
    operations: list[dict] = Field(default_factory=list)
    summary: str
    question: str | None = None
    options: list[str] = Field(default_factory=list)
    timeline_hash: str
    duration_s: float
    warnings: list[str] = Field(default_factory=list)
    revision_id: str
    clarification_id: str | None = None
