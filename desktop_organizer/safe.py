from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


def sanitize_name(value, max_len: int = 40, default: str = "") -> str:
    s = unicodedata.normalize("NFC", str(value or ""))
    s = _ILLEGAL.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    s = s[:max_len].strip(" .")
    if not s or s.casefold() in _RESERVED:
        return default
    return s


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").casefold())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def split_category_path(text: str) -> list[str]:
    return [p for p in re.split(r"[/\\>]+", str(text or "")) if p.strip()]


def is_within(child: Path, parent: Path) -> bool:
    try:
        Path(os.path.abspath(child)).relative_to(Path(os.path.abspath(parent)))
        return True
    except ValueError:
        return False


def unique_dest(path: Path, is_dir: bool = False) -> Path:
    if not os.path.lexists(path):
        return path
    stem, suffix = (path.name, "") if is_dir else (path.stem, path.suffix)
    for i in range(1, 10000):
        cand = path.with_name(f"{stem} ({i}){suffix}")
        if not os.path.lexists(cand):
            return cand
    raise OSError(f"Não foi possível achar nome livre para {path}")
