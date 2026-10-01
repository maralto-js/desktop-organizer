from __future__ import annotations

import dataclasses
from collections import Counter, defaultdict
from pathlib import Path

from ..cache import Cache
from ..classifier import Classifier
from ..executor import execute, list_runs, managed_dirs, undo
from ..models import Plan
from ..ollama_client import OllamaError
from ..planner import build_plan
from ..scanner import scan


class Cancelado(Exception):
    pass


def analisar(cfg: dict, cliente, mode: str, modelo: str, target: Path, data_dir: Path,
             usar_cache: bool = True, progress=None, cancelar=None) -> tuple[Plan, dict]:
    say = progress or (lambda msg: None)
    cache = Cache(Path(data_dir) / "cache.json", read_enabled=cfg["classification"]["use_cache"] and usar_cache)
    say(f"Analisando {target} ...")
    items, skipped = scan(Path(target), cfg, cache, managed_dirs(Path(data_dir), Path(target)))
    cache.save()
    say(f"{len(items)} item(ns) encontrados, {len(skipped)} ignorado(s).")

    warnings: list[str] = []
    try:
        say(f"Ollama {cliente.version()} em {cliente.base} (modelo: {modelo})")
    except OllamaError as e:
        warnings.append(f"Ollama indisponível ({e}); usando apenas heurística por extensão, com baixa confiança")
        say("AVISO: " + warnings[-1])

    def passo(msg: str) -> None:
        if cancelar is not None and cancelar.is_set():
            raise Cancelado()
        say(msg)

    clf = Classifier(cfg, cliente, cache, mode, progress=passo)
    if warnings:
        clf.unavailable_reason = "Ollama indisponível"
    cls = clf.classify(items)
    cache.save()
    warnings += sorted(set(clf.errors))[:10]
    plan = build_plan(items, cls, cfg, Path(target), f"{mode}:{modelo}", skipped, warnings)
    return plan, dict(clf.stats)


def texto_estatisticas(stats: dict) -> str:
    return (f"{stats.get('ia', 0)} pela IA, {stats.get('cache', 0)} do cache, "
            f"{stats.get('heuristica', 0)} por heurística, {stats.get('falha', 0)} falhas "
            f"({stats.get('requisicoes', 0)} requisições)")


def agrupar(plan: Plan) -> list[tuple[str, list]]:
    por_destino: dict[str, list] = defaultdict(list)
    for e in plan.entries:
        if e.action in ("mover", "revisar"):
            por_destino[e.dest_dir].append(e)
    ordem = sorted(por_destino, key=lambda d: (d.startswith("_"), d.lower()))
    return [(d, por_destino[d]) for d in ordem]


def ficam(plan: Plan) -> list:
    return [e for e in plan.entries if e.action in ("manter", "protegido")]


def resumo(plan: Plan) -> str:
    c = Counter(e.action for e in plan.entries)
    return (f"{c['mover']} a mover, {c['revisar']} para revisão, {c['manter']} mantidos, "
            f"{c['protegido']} protegidos, {len(plan.duplicates)} grupo(s) de duplicatas.")


def marcacao_inicial(plan: Plan) -> dict[str, bool]:
    marcas: dict[str, bool] = {}
    for e in plan.entries:
        if e.action in ("mover", "revisar"):
            marcas[e.id] = True
        elif e.action == "protegido" and e.dest_dir:
            marcas[e.id] = False
    return marcas


def aplicar_selecao(plan: Plan, marcas: dict[str, bool]) -> tuple[Plan, set[str]]:
    novas = []
    aprovados: set[str] = set()
    for e in plan.entries:
        if e.action in ("mover", "revisar") and not marcas.get(e.id, True):
            e = dataclasses.replace(e, action="manter", note=(e.note + " " if e.note else "") + "desmarcado por você")
        elif e.action == "protegido" and e.dest_dir and marcas.get(e.id, False):
            aprovados.add(e.id)
        novas.append(e)
    return dataclasses.replace(plan, entries=novas), aprovados


def contar_selecionados(plan: Plan, marcas: dict[str, bool]) -> tuple[int, int]:
    mov = sum(1 for e in plan.entries if e.action in ("mover", "revisar") and marcas.get(e.id, True))
    prot = sum(1 for e in plan.entries if e.action == "protegido" and e.dest_dir and marcas.get(e.id, False))
    return mov, prot


def executar(plan: Plan, data_dir: Path, marcas: dict[str, bool], dry_run: bool = False, progress=None) -> dict:
    filtrado, aprovados = aplicar_selecao(plan, marcas)
    total = sum(1 for e in filtrado.entries
                if e.action in ("mover", "revisar") or (e.id in aprovados and e.dest_dir))
    feitos = [0]

    def passo(nome: str) -> None:
        feitos[0] += 1
        if progress:
            progress(feitos[0], total, nome)

    res = execute(filtrado, Path(data_dir), approved_protected=aprovados, dry_run=dry_run, progress=passo)
    res["total_planejado"] = total
    return res


def historico(data_dir: Path) -> list[dict]:
    return list(reversed(list_runs(Path(data_dir))))


def desfazer(data_dir: Path, run_id: str, dry_run: bool = False) -> dict:
    return undo(Path(data_dir), run_id, dry_run=dry_run)
