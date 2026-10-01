from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

PROTEGIDO_TIPO = "tipo sensível ao sistema/programas"


@dataclass
class FileItem:
    id: str
    path: str
    name: str
    ext: str
    is_dir: bool
    size: int
    mtime_ns: int
    ctime: float
    kind: str = "binario"
    protected: bool = False
    protect_reason: str = ""
    sha256: Optional[str] = None
    snippet: Optional[str] = None
    listing: Optional[list] = None
    meta: dict = field(default_factory=dict)
    group_hint: str = ""
    fingerprint: str = ""
    cache_key: str = ""
    read_error: str = ""
    content_blocked: bool = False


@dataclass
class Classification:
    category: str = ""
    subcategory: str = ""
    project: str = ""
    description: str = ""
    confidence: float = 0.0
    reason: str = ""
    related: list = field(default_factory=list)
    source: str = "ia"
    error: str = ""


@dataclass
class PlanEntry:
    id: str
    name: str
    src: str
    is_dir: bool
    size: int
    mtime_ns: int
    action: str
    dest_dir: str = ""
    category: str = ""
    project: str = ""
    confidence: float = 0.0
    description: str = ""
    reason: str = ""
    source: str = ""
    note: str = ""


@dataclass
class Plan:
    target: str
    model: str
    created_at: str
    entries: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
