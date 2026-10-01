from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime
from pathlib import Path

from .models import Plan
from .safe import is_within, unique_dest

log = logging.getLogger("desktop_organizer")


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


class Journal:
    def __init__(self, data_dir: Path, run_id: str):
        self.path = Path(data_dir) / "journal" / f"{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id

    def write(self, **rec) -> None:
        rec["ts"] = datetime.now().isoformat(timespec="seconds")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())


def read_journal(path: Path) -> list[dict]:
    recs = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    log.warning("Linha truncada/inválida ignorada em %s", path.name)
    except OSError as e:
        log.error("Não foi possível ler o journal %s: %s", path, e)
    return recs


def list_runs(data_dir: Path) -> list[dict]:
    jdir = Path(data_dir) / "journal"
    runs = []
    for p in sorted(jdir.glob("*.jsonl")) if jdir.is_dir() else []:
        recs = read_journal(p)
        if not recs:
            continue
        start = next((r for r in recs if r.get("op") == "run_start"), {})
        runs.append({
            "run_id": p.stem, "path": p, "target": start.get("target", ""), "ts": start.get("ts", ""),
            "moved": sum(1 for r in recs if r.get("op") == "move_done"),
            "failed": sum(1 for r in recs if r.get("op") == "move_failed"),
            "status": next((r.get("status") for r in reversed(recs) if r.get("op") == "run_end"), "interrompido"),
            "undone": any(r.get("op") == "undo_done" for r in recs),
        })
    return runs


def managed_dirs(data_dir: Path, target: Path) -> set[str]:
    names = set()
    for run in list_runs(data_dir):
        if run["undone"] or Path(run["target"]) != Path(target):
            continue
        for r in read_journal(run["path"]):
            if r.get("op") == "mkdir" and Path(r["path"]).parent == Path(target):
                names.add(Path(r["path"]).name)
    return names


def _validate_entry(e, target: Path):
    src = Path(e.src)
    if os.path.dirname(os.path.abspath(src)) != os.path.abspath(target):
        return "origem fora do diretório alvo"
    if not os.path.lexists(src):
        return "origem não existe mais"
    if not e.is_dir:
        try:
            st = os.lstat(src)
        except OSError as ex:
            return f"origem inacessível: {ex}"
        if st.st_size != e.size or st.st_mtime_ns != e.mtime_ns:
            return "arquivo foi modificado depois da análise (pode estar em uso)"
    parts = e.dest_dir.replace("\\", "/").split("/")
    if not e.dest_dir or any(p in ("", ".", "..") for p in parts) or os.path.isabs(e.dest_dir) or ":" in e.dest_dir:
        return f"destino inválido: {e.dest_dir!r}"
    dest_dir = Path(os.path.normpath(target / e.dest_dir))
    if not is_within(dest_dir, target):
        return "destino escapa do diretório alvo"
    if e.is_dir and is_within(dest_dir, src):
        return "não é possível mover uma pasta para dentro dela mesma"
    return ""


def execute(plan: Plan, data_dir: Path, approved_protected: set | None = None, dry_run: bool = False,
            run_id: str | None = None, progress=None) -> dict:
    target = Path(plan.target)
    approved_protected = approved_protected or set()
    todo = [e for e in plan.entries
            if e.action in ("mover", "revisar") or (e.action == "protegido" and e.id in approved_protected and e.dest_dir)]
    todo.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    result = {"moved": [], "failed": [], "skipped": [], "dry_run": dry_run, "run_id": None, "status": "completed"}
    if dry_run:
        for e in todo:
            err = _validate_entry(e, target)
            if err:
                result["skipped"].append((e.name, err))
            else:
                result["moved"].append((e.src, str(Path(target / e.dest_dir / e.name))))
                log.info("[dry-run] mover %s -> %s", e.src, Path(target / e.dest_dir))
        return result

    run_id = run_id or new_run_id()
    result["run_id"] = run_id
    j = Journal(data_dir, run_id)
    j.write(op="run_start", target=str(target), model=plan.model, planned=len(todo))
    created: set[str] = set()
    n = 0
    try:
        for e in todo:
            err = _validate_entry(e, target)
            if err:
                j.write(op="skip", src=e.src, reason=err)
                result["skipped"].append((e.name, err))
                log.warning("Ignorado %s: %s", e.name, err)
                continue
            dest_dir = Path(os.path.normpath(target / e.dest_dir))
            try:
                _mkdirs(dest_dir, target, j, created)
                dst = unique_dest(dest_dir / e.name, is_dir=e.is_dir)
                n += 1
                j.write(op="move_intent", n=n, id=e.id, src=e.src, dst=str(dst), category=e.category,
                        confidence=e.confidence, source=e.source, reason=e.reason)
                os.rename(e.src, dst)
                j.write(op="move_done", n=n, src=e.src, dst=str(dst))
                result["moved"].append((e.src, str(dst)))
                log.info("Movido %s -> %s", e.src, dst)
            except OSError as ex:
                j.write(op="move_failed", n=n, src=e.src, error=f"{type(ex).__name__}: {ex}")
                result["failed"].append((e.name, f"{type(ex).__name__}: {ex}"))
                log.error("Falha ao mover %s: %s", e.src, ex)
            if progress:
                progress(e.name)
    except KeyboardInterrupt:
        result["status"] = "interrompido"
        j.write(op="run_end", status="interrompido", moved=len(result["moved"]), failed=len(result["failed"]))
        log.warning("Interrompido pelo usuário; use 'undo' para reverter o que já foi movido")
        return result
    j.write(op="run_end", status="concluido", moved=len(result["moved"]), failed=len(result["failed"]))
    return result


def _mkdirs(dest_dir: Path, target: Path, j: Journal, created: set) -> None:
    missing = []
    p = dest_dir
    while not os.path.lexists(p) and is_within(p, target) and p != target:
        missing.append(p)
        p = p.parent
    for d in reversed(missing):
        os.mkdir(d)
        created.add(str(d))
        j.write(op="mkdir", path=str(d))


def undo(data_dir: Path, run_id: str | None = None, dry_run: bool = False) -> dict:
    runs = [r for r in list_runs(data_dir) if not r["undone"]]
    if run_id:
        runs = [r for r in runs if r["run_id"] == run_id]
    if not runs:
        return {"error": "nenhuma execução pendente de desfazer", "restored": [], "failed": []}
    run = runs[-1]
    recs = read_journal(run["path"])
    done_n = {r["n"] for r in recs if r.get("op") == "move_done"}
    moves = []
    for r in recs:
        if r.get("op") != "move_intent":
            continue
        if r["n"] in done_n or (not os.path.lexists(r["src"]) and os.path.lexists(r["dst"])):
            moves.append(r)
    result = {"run_id": run["run_id"], "restored": [], "failed": [], "notes": [], "dry_run": dry_run}
    j = None if dry_run else Journal(data_dir, run["run_id"])
    for r in reversed(moves):
        src, dst = r["src"], r["dst"]
        try:
            if not os.path.lexists(dst):
                if os.path.lexists(src):
                    result["notes"].append(f"{Path(src).name}: já estava restaurado")
                else:
                    result["failed"].append((Path(dst).name, "destino não existe mais (movido ou apagado depois)"))
                continue
            back = Path(src)
            if os.path.lexists(back):
                back = unique_dest(Path(src).with_name(Path(src).stem + " (restaurado)" + Path(src).suffix)
                                   if not os.path.isdir(dst) else Path(src).with_name(Path(src).name + " (restaurado)"))
                result["notes"].append(f"{Path(src).name}: origem ocupada; restaurado como {back.name}")
            if not dry_run:
                os.rename(dst, back)
                j.write(op="undo_move", src=str(dst), dst=str(back))
            result["restored"].append((dst, str(back)))
        except OSError as ex:
            result["failed"].append((Path(dst).name, f"{type(ex).__name__}: {ex}"))
    if not dry_run:
        for r in reversed([r for r in recs if r.get("op") == "mkdir"]):
            try:
                if os.path.isdir(r["path"]) and not os.listdir(r["path"]):
                    os.rmdir(r["path"])
            except OSError as ex:
                result["notes"].append(f"pasta {Path(r['path']).name} mantida: {ex}")
        j.write(op="undo_done" if not result["failed"] else "undo_partial",
                restored=len(result["restored"]), failed=len(result["failed"]))
    return result
