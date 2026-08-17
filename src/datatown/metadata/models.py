"""Typed inputs for Datatown provenance records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DatasetFileInput:
    role: str
    filename: str
    object_key: str
    format: str
    sha256: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class DatasetSnapshotInput:
    source: str
    dataset: str
    acquired_at: datetime
    source_url: str | None
    description: str | None
    manifest_object_key: str
    files: tuple[DatasetFileInput, ...]
