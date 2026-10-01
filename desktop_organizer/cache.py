from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from pathlib import Path

from .models import Classification

log = logging.getLogger("desktop_organizer")


class Cache:
    def __init__(self, path: Path, read_enabled: bool = True):
        self.path = Path(path)
        self.read_enabled = read_enabled
        self.hashes: dict = {}
        self.classes: dict = {}
        self.dirty = False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.hashes = data.get("hashes", {}) if isinstance(data, dict) else {}
            self.classes = data.get("classes", {}) if isinstance(data, dict) else {}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("Cache ilegível (%s); começando vazio", e)

    def get_hash(self, path: str, size: int, mtime_ns: int):
        e = self.hashes.get(path)
        if e and e.get("size") == size and e.get("mtime_ns") == mtime_ns:
            return e.get("sha256")
        return None

    def set_hash(self, path: str, size: int, mtime_ns: int, sha: str) -> None:
        self.hashes[path] = {"size": size, "mtime_ns": mtime_ns, "sha256": sha}
        self.dirty = True

    def get_class(self, fp: str):
        if not self.read_enabled:
            return None
        e = self.classes.get(fp)
        if not isinstance(e, dict):
            return None
        try:
            c = Classification(**e)
        except TypeError:
            return None
        c.source = "cache"
        return c

    def set_class(self, fp: str, c: Classification) -> None:
        if c.source not in ("ia", "cache"):
            return
        self.classes[fp] = asdict(c)
        self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"hashes": self.hashes, "classes": self.classes}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError as e:
            log.warning("Não foi possível salvar o cache: %s", e)
