from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NoteType = Literal["general", "meeting_minutes", "research_note"]


class NoteWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=False, extra="forbid")

    title: str = Field(max_length=120)
    content: str = Field(default="", max_length=100000)
    note_type: NoteType = "general"
    is_pinned: bool = False

    @field_validator("title")
    @classmethod
    def normalize_title(cls, title: str) -> str:
        normalized = title.strip()
        if not normalized:
            raise ValueError("title must not be empty")
        return normalized


class NoteCreate(NoteWrite):
    pass


class NoteUpdate(NoteWrite):
    pass


class NoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    title: str
    content: str
    note_type: NoteType
    is_pinned: bool
    created_at: str
    updated_at: str
