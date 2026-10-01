from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import PROTEGIDO_TIPO, Classification, FileItem, Plan, PlanEntry
from .safe import fold, sanitize_name

OTHERS = "Outros"


def find_duplicates(items: list[FileItem]) -> list[dict]:
    by_key: dict[tuple, list[FileItem]] = defaultdict(list)
    for it in items:
        if not it.is_dir and it.sha256 and it.size > 0:
            by_key[(it.size, it.sha256)].append(it)
    return [
        {"sha256": sha, "size": size, "names": [i.name for i in g], "paths": [i.path for i in g]}
        for (size, sha), g in by_key.items() if len(g) > 1
    ]


def _canonical(values: list[str]) -> dict[str, str]:
    counts: dict[str, Counter] = defaultdict(Counter)
    for v in values:
        if v:
            counts[fold(v)][v] += 1
    return {k: c.most_common(1)[0][0] for k, c in counts.items()}


def build_plan(items, classifications, cfg, target: Path, model: str, skipped=None, warnings=None) -> Plan:
    cc = cfg["classification"]
    thr, maxlen = cc["confidence_threshold"], cc["max_folder_name_length"]
    review = cc["review_folder"]
    plan = Plan(target=str(target), model=model, created_at=datetime.now().isoformat(timespec="seconds"),
                skipped=list(skipped or []), warnings=list(warnings or []))
    plan.duplicates = find_duplicates(items)
    unhashed = sum(1 for i in items if not i.is_dir and i.size > 0 and not i.sha256)
    if unhashed:
        plan.warnings.append(f"{unhashed} arquivo(s) sem hash (grandes ou ilegíveis): duplicatas não verificadas para eles")

    work = {}
    for it in items:
        c = classifications.get(it.id) or Classification(source="falha", error="sem classificação")
        cat = sanitize_name(c.category, maxlen)
        ok = c.source in ("ia", "cache", "heuristica") and bool(cat)
        work[it.id] = {"it": it, "c": c, "cat": cat, "sub": sanitize_name(c.subcategory, maxlen),
                       "proj": sanitize_name(c.project, maxlen), "ok": ok,
                       "movable": ok and c.confidence >= thr
                                  and (not it.protected or it.protect_reason.startswith(PROTEGIDO_TIPO))}

    movers = [w for w in work.values() if w["movable"]]

    cat_canon = _canonical([w["cat"] for w in movers])
    for w in movers:
        w["cat"] = cat_canon[fold(w["cat"])]
    sub_canon = _canonical([f"{fold(w['cat'])}/{w['sub']}" for w in movers if w["sub"]])
    for w in movers:
        if w["sub"]:
            w["sub"] = sub_canon[fold(f"{w['cat']}/{w['sub']}")].split("/", 1)[1]
    if cc["max_depth"] == 1:
        for w in movers:
            w["sub"] = ""

    counts = Counter(w["cat"] for w in movers)
    if len(counts) > cc["max_top_level_categories"]:
        keep = {c for c, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[: cc["max_top_level_categories"] - 1]}
        merged = sorted(set(counts) - keep)
        plan.warnings.append(f"Categorias demais ({len(counts)}); {len(merged)} foram agrupadas em '{OTHERS}': {', '.join(merged)}")
        for w in movers:
            if w["cat"] not in keep:
                w["sub"] = w["cat"] if cc["max_depth"] >= 2 and not w["sub"] else w["sub"]
                w["cat"] = OTHERS

    groups: dict[str, list] = defaultdict(list)
    for w in movers:
        if w["proj"]:
            groups[fold(w["proj"])].append(w)
    for key, members in groups.items():
        if len(members) < cc["min_project_files"]:
            for w in members:
                w["proj"] = ""
            continue
        name = Counter(w["proj"] for w in members).most_common(1)[0][0]
        (cat, sub), _ = Counter((w["cat"], w["sub"]) for w in members).most_common(1)[0]
        for w in members:
            w["proj"], w["cat"], w["sub"] = name, cat, sub

    def folders() -> set:
        out = set()
        for w in movers:
            out.add((w["cat"],))
            if w["sub"]:
                out.add((w["cat"], w["sub"]))
            if w["proj"]:
                out.add((w["cat"], w["sub"], w["proj"]) if w["sub"] else (w["cat"], w["proj"]))
        return out

    def sub_sizes():
        return Counter((w["cat"], w["sub"]) for w in movers if w["sub"])

    for (cat, sub), n in sub_sizes().items():
        has_proj = any(w["proj"] for w in movers if w["cat"] == cat and w["sub"] == sub)
        if n < cc["min_files_per_subfolder"] and not has_proj:
            for w in movers:
                if w["cat"] == cat and w["sub"] == sub:
                    w["sub"] = ""
    while len(folders()) > cc["max_new_folders"] and sub_sizes():
        (cat, sub), _ = min(sub_sizes().items(), key=lambda kv: (kv[1], kv[0]))
        for w in movers:
            if w["cat"] == cat and w["sub"] == sub:
                w["sub"] = ""
        plan.warnings.append(f"Limite de pastas: subpasta '{cat}/{sub}' foi fundida à categoria")
    if len(folders()) > cc["max_new_folders"]:
        plan.warnings.append(f"Ainda há {len(folders())} pastas planejadas (limite {cc['max_new_folders']}); revise o plano")

    planned_tops = {fold(w["cat"]) for w in movers} | {fold(review)}

    def top_name(t: str) -> str:
        p = target / t
        if os.path.lexists(p) and not p.is_dir():
            return f"{t} (pasta)"
        return t

    def dest_for(w: dict, it: FileItem) -> str:
        parts = [top_name(w["cat"])] + ([w["sub"]] if w["sub"] else [])
        if w["proj"] and not (it.is_dir and fold(it.name) == fold(w["proj"])):
            parts.append(w["proj"])
        return "/".join(parts)

    entries = []
    for it in items:
        w = work[it.id]
        c: Classification = w["c"]
        label = "/".join(x for x in (w["cat"], w["sub"]) if x) if w["ok"] else ""
        e = PlanEntry(id=it.id, name=it.name, src=it.path, is_dir=it.is_dir, size=it.size, mtime_ns=it.mtime_ns,
                      action="manter", category=label, project=w["proj"] if w["movable"] else "",
                      confidence=round(c.confidence, 3), description=c.description, reason=c.reason or c.error,
                      source=c.source)
        if it.protected:
            e.action, e.note = "protegido", it.protect_reason
            if w["movable"]:
                e.dest_dir = dest_for(w, it)
        elif not w["ok"]:
            e.action = "revisar" if cc["low_confidence_action"] == "review" else "manter"
            e.note = f"sem classificação utilizável ({c.error or 'falha'})"
            e.dest_dir = top_name(review) if e.action == "revisar" else ""
        elif c.confidence < thr:
            e.action = "revisar" if cc["low_confidence_action"] == "review" else "manter"
            e.note = f"confiança {c.confidence:.2f} < limite {thr:.2f}"
            e.dest_dir = top_name(review) if e.action == "revisar" else ""
        elif it.is_dir and fold(it.name) in planned_tops:
            e.action, e.note = "manter", "pasta existente já usada como categoria"
            e.category = ""
        else:
            e.action = "mover"
            e.dest_dir = dest_for(w, it)
        if it.read_error and e.action != "protegido":
            e.note = (e.note + "; " if e.note else "") + it.read_error
        entries.append(e)
    plan.entries = entries
    return plan


def render_plan(plan: Plan, detailed: bool = False) -> str:
    lines = [f"PLANO DE ORGANIZAÇÃO  |  alvo: {plan.target}  |  modelo: {plan.model}  |  {plan.created_at}", ""]
    by_dest: dict[str, list[PlanEntry]] = defaultdict(list)
    for e in plan.entries:
        if e.action in ("mover", "revisar"):
            by_dest[e.dest_dir].append(e)
    for dest in sorted(by_dest, key=lambda d: (d.startswith("_"), d.lower())):
        lines.append(f"{dest}/   ({len(by_dest[dest])})")
        for e in by_dest[dest]:
            tag = f"[{e.confidence:.2f}]" if e.source != "heuristica" else f"[{e.confidence:.2f} heurística]"
            lines.append(f"   • {e.name}{'/' if e.is_dir else ''}  {tag}  {e.description or e.reason}".rstrip())
            if detailed:
                lines.append(f"       arquivo: {e.name} | categoria: {e.category or '-'} | confiança: {e.confidence} | "
                             f"ação: {e.action} | destino: Desktop/{e.dest_dir}/ | motivo: {e.reason} {e.note}".rstrip())
    kept = [e for e in plan.entries if e.action in ("manter", "protegido")]
    if kept:
        lines += ["", "Ficam onde estão:"]
        for e in kept:
            kind = "PROTEGIDO" if e.action == "protegido" else "mantido"
            sugestao = f"; sugestão: {e.dest_dir}/ (só com --ask-protected ou --move-protected)" if e.dest_dir else ""
            lines.append(f"   • {e.name}  ({kind}: {e.note or e.reason}{sugestao})")
    if plan.duplicates:
        lines += ["", "Possíveis duplicatas (mesmo tamanho e mesmo hash; nada será apagado):"]
        for d in plan.duplicates:
            lines.append(f"   • {' == '.join(d['names'])}   ({d['size']} bytes, sha256 {d['sha256'][:12]}…)")
    if plan.skipped:
        lines += ["", f"Ignorados na varredura ({len(plan.skipped)}):"]
        lines += [f"   • {s['name']}: {s['reason']}" for s in plan.skipped[:15]]
        if len(plan.skipped) > 15:
            lines.append(f"   … e mais {len(plan.skipped) - 15}")
    for w in plan.warnings:
        lines.append(f"AVISO: {w}")
    c = Counter(e.action for e in plan.entries)
    lines += ["", f"Resumo: {c['mover']} a mover, {c['revisar']} para revisão, {c['manter']} mantidos, "
                  f"{c['protegido']} protegidos, {len(plan.duplicates)} grupo(s) de duplicatas."]
    return "\n".join(lines)


def plan_to_json(plan: Plan) -> str:
    return json.dumps(asdict(plan), ensure_ascii=False, indent=2)


def plan_from_json(text: str) -> Plan:
    d = json.loads(text)
    entries = [PlanEntry(**e) for e in d.pop("entries", [])]
    p = Plan(**d)
    p.entries = entries
    return p
