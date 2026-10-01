from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from . import __version__
from .cache import Cache
from .classifier import Classifier
from .config import ConfigError, load_config, resolve_paths, write_default
from .executor import execute, list_runs, managed_dirs, new_run_id, undo
from .ollama_client import OllamaClient, OllamaError
from .planner import build_plan, plan_from_json, plan_to_json, render_plan
from .scanner import scan

log = logging.getLogger("desktop_organizer")


def setup_logging(data_dir: Path, verbose: bool) -> None:
    log.setLevel(logging.DEBUG)
    if log.handlers:
        return
    try:
        (data_dir / "logs").mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(data_dir / "logs" / "organizer.log", maxBytes=2_000_000,
                                                  backupCount=5, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    except OSError as e:
        print(f"Aviso: sem log em arquivo ({e})", file=sys.stderr)
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO if verbose else logging.ERROR)
    ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log.addHandler(ch)


def say(msg: str = "") -> None:
    print(msg, flush=True)


def build_client(cfg: dict, args) -> tuple[OllamaClient, str, str]:
    o = cfg["ollama"]
    mode = getattr(args, "mode", None) or o["mode"]
    model = getattr(args, "model", None) or (o["model_cloud"] if mode == "cloud" else o["model_local"])
    url = o["cloud_url"] if mode == "cloud" else o["local_url"]
    api_key = os.environ.get(o["api_key_env"], "") if mode == "cloud" else ""
    if mode == "cloud" and not api_key:
        say(f"Aviso: variável {o['api_key_env']} não definida; a Ollama Cloud direta exige chave de API "
            "(alternativa: modo local com ollama signin e um modelo '...-cloud').")
    schema_ok = o["use_json_schema"] and mode == "local" and not model.endswith("-cloud")
    client = OllamaClient(url, model, api_key, o["timeout_seconds"], o["retries"], o["temperature"],
                          o["num_ctx"], use_schema=schema_ok)
    return client, mode, model


def analyze(cfg, args, target: Path, data_dir: Path):
    cache = Cache(data_dir / "cache.json", read_enabled=cfg["classification"]["use_cache"] and not args.no_cache)
    say(f"Analisando {target} ...")
    items, skipped = scan(target, cfg, cache, managed_dirs(data_dir, target))
    cache.save()
    say(f"{len(items)} item(ns) encontrados, {len(skipped)} ignorado(s).")
    client, mode, model = build_client(cfg, args)
    warnings = []
    try:
        v = client.version()
        say(f"Ollama {v} em {client.base} (modelo: {model})")
    except OllamaError as e:
        warnings.append(f"Ollama indisponível ({e}); usando apenas heurística por extensão, com baixa confiança")
        say("AVISO: " + warnings[-1])
    clf = Classifier(cfg, client, cache, mode, progress=say)
    if warnings:
        clf.unavailable_reason = "Ollama indisponível"
    cls = clf.classify(items)
    cache.save()
    say(f"Classificação: {clf.stats['ia']} pela IA, {clf.stats['cache']} do cache, "
        f"{clf.stats['heuristica']} por heurística, {clf.stats['falha']} falhas ({clf.stats['requisicoes']} requisições).")
    warnings += sorted(set(clf.errors))[:10]
    plan = build_plan(items, cls, cfg, target, f"{mode}:{model}", skipped, warnings)
    return plan


def save_plan(plan, data_dir: Path, name: str | None = None) -> Path:
    d = data_dir / "plans"
    d.mkdir(parents=True, exist_ok=True)
    p = Path(name) if name else d / f"plan-{new_run_id()}.json"
    p.write_text(plan_to_json(plan), encoding="utf-8")
    return p


def confirm(question: str) -> bool:
    try:
        return input(f"{question} [s/N] ").strip().lower() in ("s", "sim", "y", "yes")
    except EOFError:
        return False


def run_plan(plan, cfg, args, data_dir: Path) -> int:
    say(render_plan(plan, detailed=args.detailed))
    pfile = save_plan(plan, data_dir, getattr(args, "plan_file", None))
    say(f"\nPlano salvo em: {pfile}")
    dry = args.dry_run or cfg["dry_run"]
    actionable = [e for e in plan.entries if e.action in ("mover", "revisar")]
    protected = [e for e in plan.entries if e.action == "protegido"]
    if dry:
        res = execute(plan, data_dir, dry_run=True)
        say(f"\n[DRY-RUN] Nada foi movido. {len(res['moved'])} movimentações seriam feitas; {len(res['skipped'])} seriam ignoradas.")
        return 0
    if not actionable and not (args.move_protected and protected):
        say("\nNada a mover.")
        return 0
    if not args.yes and not confirm(f"\nExecutar {len(actionable)} movimentação(ões)?"):
        say("Cancelado. Nenhum arquivo foi alterado.")
        return 0
    approved: set = set()
    for e in protected:
        if not e.dest_dir:
            continue
        if args.move_protected or (not args.yes and args.ask_protected and confirm(f"Mover item PROTEGIDO '{e.name}' ({e.note})?")):
            approved.add(e.id)
    res = execute(plan, data_dir, approved_protected=approved)
    say(f"\nConcluído ({res['status']}): {len(res['moved'])} movidos, {len(res['failed'])} falhas, {len(res['skipped'])} ignorados.")
    for name, err in res["failed"] + res["skipped"]:
        say(f"   ! {name}: {err}")
    say(f"Journal: {data_dir / 'journal' / (res['run_id'] + '.jsonl')}   |   Para desfazer: python -m desktop_organizer undo")
    return 0 if not res["failed"] and res["status"] == "completed" else 1


def cmd_organize(cfg, args, target, data_dir):
    return run_plan(analyze(cfg, args, target, data_dir), cfg, args, data_dir)


def cmd_plan(cfg, args, target, data_dir):
    plan = analyze(cfg, args, target, data_dir)
    say(render_plan(plan, detailed=args.detailed))
    say(f"\nPlano salvo em: {save_plan(plan, data_dir, args.plan_file)}  (aplique com: apply <arquivo>)")
    return 0


def cmd_apply(cfg, args, target, data_dir):
    try:
        plan = plan_from_json(Path(args.plan).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as e:
        say(f"Plano inválido: {e}")
        return 1
    if Path(plan.target) != target:
        say(f"O plano é para {plan.target}, mas o alvo atual é {target}. Abortando.")
        return 1
    args.plan_file = None
    return run_plan(plan, cfg, args, data_dir)


def cmd_undo(cfg, args, target, data_dir):
    runs = [r for r in list_runs(data_dir) if not r["undone"]]
    if not runs:
        say("Nada para desfazer.")
        return 0
    run = next((r for r in runs if r["run_id"] == args.run_id), None) if args.run_id else runs[-1]
    if not run:
        say("Execução não encontrada ou já desfeita.")
        return 1
    say(f"Desfazendo {run['run_id']} ({run['moved']} movimentações, {run['ts']})")
    if not (args.dry_run or args.yes or confirm("Restaurar os arquivos às posições originais?")):
        say("Cancelado.")
        return 0
    res = undo(data_dir, run["run_id"], dry_run=args.dry_run)
    say(f"{'[DRY-RUN] ' if args.dry_run else ''}Restaurados: {len(res['restored'])}  Falhas: {len(res['failed'])}")
    for n in res.get("notes", []):
        say(f"   - {n}")
    for name, err in res["failed"]:
        say(f"   ! {name}: {err}")
    return 0 if not res["failed"] else 1


def cmd_history(cfg, args, target, data_dir):
    runs = list_runs(data_dir)
    if not runs:
        say("Nenhuma execução registrada.")
    for r in runs:
        say(f"{r['run_id']}  {r['ts']}  movidos={r['moved']} falhas={r['failed']}  {r['status']}{'  [DESFEITA]' if r['undone'] else ''}")
    return 0


def cmd_duplicates(cfg, args, target, data_dir):
    from .planner import find_duplicates
    cache = Cache(data_dir / "cache.json")
    items, _ = scan(target, cfg, cache)
    cache.save()
    dups = find_duplicates(items)
    for d in dups:
        say(f"Arquivo A: {d['names'][0]}\nArquivo B: {', '.join(d['names'][1:])}\nmesmo hash ({d['sha256'][:16]}…), mesmo tamanho ({d['size']} bytes)\n")
    say(f"{len(dups)} grupo(s) de duplicatas. Nada foi apagado; a decisão é sua.")
    return 0


def cmd_models(cfg, args, target, data_dir):
    client, mode, model = build_client(cfg, args)
    try:
        say(f"Servidor Ollama {client.version()} em {client.base}")
        for m in client.list_models():
            say(f"  - {m}{'   <== configurado' if m == model or m.split(':')[0] == model else ''}")
    except OllamaError as e:
        say(f"Erro: {e}")
        return 1
    return 0


def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="caminho do config.json")
    common.add_argument("--dir", help="diretório a organizar (padrão: Desktop)")
    common.add_argument("--mode", choices=["local", "cloud"], help="Ollama local ou cloud")
    common.add_argument("--model", help="modelo Ollama")
    common.add_argument("-v", "--verbose", action="store_true")
    act = argparse.ArgumentParser(add_help=False)
    act.add_argument("--dry-run", action="store_true", help="só simula; não move nada")
    act.add_argument("--yes", "-y", action="store_true", help="não pedir confirmação")
    act.add_argument("--detailed", action="store_true", help="mostrar plano no formato detalhado")
    act.add_argument("--no-cache", action="store_true", help="ignorar classificações em cache")
    act.add_argument("--plan-file", help="onde salvar o plano JSON")
    act.add_argument("--ask-protected", action="store_true", help="perguntar item a item sobre arquivos protegidos")
    act.add_argument("--move-protected", action="store_true", help="mover também os protegidos (use com cuidado)")

    ap = argparse.ArgumentParser(prog="desktop_organizer", description="Organizador de Desktop com IA (Ollama)")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("organize", parents=[common, act], help="analisa, mostra o plano, confirma e executa (padrão)")
    sub.add_parser("plan", parents=[common, act], help="só gera e salva o plano")
    p_apply = sub.add_parser("apply", parents=[common, act], help="executa um plano salvo")
    p_apply.add_argument("plan")
    p_undo = sub.add_parser("undo", parents=[common], help="desfaz a última organização")
    p_undo.add_argument("--run-id")
    p_undo.add_argument("--dry-run", action="store_true")
    p_undo.add_argument("--yes", "-y", action="store_true")
    sub.add_parser("history", parents=[common], help="lista execuções registradas")
    sub.add_parser("duplicates", parents=[common], help="lista possíveis duplicatas (sem apagar)")
    sub.add_parser("models", parents=[common], help="lista modelos do servidor Ollama")
    sub.add_parser("gui", parents=[common], help="abre a interface gráfica")
    p_init = sub.add_parser("init-config", help="cria um config.json com os padrões")
    p_init.add_argument("path", nargs="?", default="config.json")

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version"):
        argv = ["organize"] + argv
    args = ap.parse_args(argv)
    if args.cmd is None:
        ap.print_help()
        return 0
    if args.cmd == "gui":
        from .gui.app import main as gui_main
        return gui_main(args.config)
    if args.cmd == "init-config":
        try:
            write_default(args.path)
        except ConfigError as e:
            say(str(e))
            return 1
        say(f"Configuração criada em {args.path}")
        return 0
    try:
        cfg = load_config(args.config)
        target, data_dir = resolve_paths(cfg)
        if args.dir:
            target = Path(os.path.expandvars(os.path.expanduser(args.dir)))
    except ConfigError as e:
        say(f"Erro de configuração: {e}")
        return 1
    if not target.is_dir():
        say(f"Diretório alvo não encontrado: {target}")
        return 1
    setup_logging(data_dir, args.verbose)
    handler = {"organize": cmd_organize, "plan": cmd_plan, "apply": cmd_apply, "undo": cmd_undo,
               "history": cmd_history, "duplicates": cmd_duplicates, "models": cmd_models}[args.cmd]
    try:
        return handler(cfg, args, target, data_dir)
    except KeyboardInterrupt:
        say("\nInterrompido.")
        return 130
    except Exception as e:
        log.exception("Erro inesperado")
        say(f"Erro inesperado: {type(e).__name__}: {e} (detalhes em {data_dir / 'logs' / 'organizer.log'})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
