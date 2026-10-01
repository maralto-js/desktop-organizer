from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULT_CONFIG: dict = {
    "target_dir": "auto",
    "data_dir": "~/.desktop_organizer",
    "dry_run": False,
    "ollama": {
        "mode": "local",
        "local_url": "http://localhost:11434",
        "cloud_url": "https://ollama.com",
        "api_key_env": "OLLAMA_API_KEY",
        "model_local": "llama3.2",
        "model_cloud": "gpt-oss:120b",
        "timeout_seconds": 180,
        "retries": 2,
        "temperature": 0,
        "num_ctx": 8192,
        "batch_size": 10,
        "use_json_schema": True,
        "cloud_send_content": True,
    },
    "classification": {
        "confidence_threshold": 0.75,
        "low_confidence_action": "review",
        "review_folder": "_Revisar",
        "max_snippet_chars": 1500,
        "max_top_level_categories": 8,
        "max_depth": 2,
        "max_new_folders": 25,
        "max_folder_name_length": 40,
        "min_files_per_subfolder": 2,
        "min_project_files": 2,
        "category_hints": [],
        "fallback_confidence": 0.5,
        "use_cache": True,
        "never_send_content_patterns": [
            "*.env", ".env*", "*password*", "*senha*", "*secret*", "*token*", "*credential*",
            "*.pem", "*.key", "*.pfx", "*.p12", "id_rsa*", "*.kdbx", "*.ppk", "*wallet*",
        ],
    },
    "scan": {
        "ignored_extensions": [],
        "ignored_names": ["desktop.ini", "Thumbs.db", ".DS_Store"],
        "protected_extensions": [
            ".exe", ".bat", ".cmd", ".ps1", ".dll", ".lnk", ".url", ".sys", ".msi", ".reg",
            ".vbs", ".ini", ".cfg", ".conf", ".config", ".sln", ".csproj", ".desktop", ".app",
        ],
        "temp_extensions": [".tmp", ".temp", ".part", ".crdownload", ".download", ".swp", ".lock"],
        "protect_hidden": True,
        "protect_project_dirs": True,
        "project_markers": [
            ".git", "pom.xml", "package.json", "build.gradle", "build.gradle.kts", "settings.gradle",
            "pyproject.toml", "requirements.txt", "Cargo.toml", "go.mod", "CMakeLists.txt", "Makefile",
            "plugin.yml", "fabric.mod.json", "mods.toml",
        ],
        "move_shortcuts": True,
        "shortcut_extensions": [".lnk", ".url", ".desktop"],
        "app_context": True,
        "recently_modified_seconds": 30,
        "organize_directories": True,
        "max_items": 3000,
        "max_hash_bytes": 536870912,
        "archive_list_limit": 40,
        "dir_list_limit": 30,
    },
}


class ConfigError(Exception):
    pass


def deep_merge(base: dict, over: dict, path: str = "") -> dict:
    for k, v in over.items():
        if k not in base:
            raise ConfigError(f"Chave desconhecida na configuração: {path}{k}")
        if isinstance(base[k], dict):
            if not isinstance(v, dict):
                raise ConfigError(f"'{path}{k}' deve ser um objeto")
            deep_merge(base[k], v, f"{path}{k}.")
        else:
            b = base[k]
            if isinstance(b, bool):
                ok = isinstance(v, bool)
            elif isinstance(b, (int, float)):
                ok = isinstance(v, (int, float)) and not isinstance(v, bool)
            else:
                ok = isinstance(v, type(b))
            if not ok:
                raise ConfigError(f"Tipo inválido em '{path}{k}': esperado {type(b).__name__}")
            base[k] = v
    return base


def find_desktop() -> Path:
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "OneDrive" / "Desktop",
        home / "OneDrive" / "Área de Trabalho",
        home / "Área de Trabalho",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p)))


def validate(cfg: dict) -> None:
    o, c, s = cfg["ollama"], cfg["classification"], cfg["scan"]
    if o["mode"] not in ("local", "cloud"):
        raise ConfigError("ollama.mode deve ser 'local' ou 'cloud'")
    if not 0.0 <= c["confidence_threshold"] <= 1.0:
        raise ConfigError("classification.confidence_threshold deve estar entre 0 e 1")
    if c["low_confidence_action"] not in ("review", "keep"):
        raise ConfigError("classification.low_confidence_action deve ser 'review' ou 'keep'")
    if c["max_depth"] not in (1, 2, 3):
        raise ConfigError("classification.max_depth deve ser 1, 2 ou 3")
    for key in ("max_top_level_categories", "max_new_folders", "max_snippet_chars", "min_project_files"):
        if not isinstance(c[key], int) or c[key] < 1:
            raise ConfigError(f"classification.{key} deve ser inteiro >= 1")
    if not isinstance(o["batch_size"], int) or o["batch_size"] < 1:
        raise ConfigError("ollama.batch_size deve ser inteiro >= 1")
    if o["timeout_seconds"] <= 0:
        raise ConfigError("ollama.timeout_seconds deve ser > 0")
    if not c["review_folder"].strip() or "/" in c["review_folder"] or "\\" in c["review_folder"]:
        raise ConfigError("classification.review_folder deve ser um nome simples de pasta")
    if s["max_items"] < 1:
        raise ConfigError("scan.max_items deve ser >= 1")


def load_config(path: str | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    chosen = None
    if path:
        chosen = Path(path)
        if not chosen.is_file():
            raise ConfigError(f"Arquivo de configuração não encontrado: {path}")
    else:
        for cand in (Path.cwd() / "config.json", _expand(DEFAULT_CONFIG["data_dir"]) / "config.json"):
            if cand.is_file():
                chosen = cand
                break
    if chosen:
        try:
            user = json.loads(chosen.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ConfigError(f"Não foi possível ler {chosen}: {e}")
        if not isinstance(user, dict):
            raise ConfigError("O arquivo de configuração deve conter um objeto JSON")
        user = _strip_comments(user)
        deep_merge(cfg, user)
    validate(cfg)
    cfg["_config_path"] = str(chosen) if chosen else ""
    return cfg


def _strip_comments(d: dict) -> dict:
    return {k: (_strip_comments(v) if isinstance(v, dict) else v) for k, v in d.items() if not k.startswith("_")}


def resolve_paths(cfg: dict) -> tuple[Path, Path]:
    t = cfg["target_dir"]
    target = find_desktop() if t == "auto" else _expand(t)
    return target, _expand(cfg["data_dir"])


def write_default(path: str) -> None:
    p = Path(path)
    if p.exists():
        raise ConfigError(f"{p} já existe; não vou sobrescrever")
    p.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
