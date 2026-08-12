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


class SourceReferenceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    source_app: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_-]*$")
    source_type: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_-]*$")
    source_id: str = Field(min_length=1, max_length=120)

    @field_validator("source_id")
    @classmethod
    def normalize_source_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("source_id must not be empty")
        return normalized


class NoteCreate(NoteWrite):
    links: list[SourceReferenceInput] = Field(default_factory=list, max_length=20)


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
    workspace_id: str
    created_by: str
    updated_by: str
    deleted_at: str | None


class NoteLinkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    note_id: int
    source_app: str
    source_type: str
    source_id: str
    source_title_snapshot: str
    created_at: str
    workspace_id: str
    orphan: bool
