from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_ollama import MockOllama

from desktop_organizer import cli, executor, scanner
from desktop_organizer.cache import Cache
from desktop_organizer.classifier import Classifier, validate_item
from desktop_organizer.config import DEFAULT_CONFIG, ConfigError, deep_merge, load_config
from desktop_organizer.models import Classification, FileItem
from desktop_organizer.ollama_client import OllamaClient, OllamaUnavailable, extract_json
from desktop_organizer.planner import build_plan, find_duplicates, plan_from_json, plan_to_json, render_plan
from desktop_organizer.safe import sanitize_name

def make_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 10 50 Td ({text}) Tj ET".encode()
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
            b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer<</Root 1 0 R/Size %d>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


MINI_PDF = make_pdf("Fatura de cartao vencimento")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def make_cfg(tmp: Path, **over) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["data_dir"] = str(tmp / "data")
    cfg["scan"]["recently_modified_seconds"] = 0
    cfg["ollama"]["retries"] = 0
    cfg["ollama"]["timeout_seconds"] = 5
    for k, v in over.items():
        sect, key = k.split("__")
        cfg[sect][key] = v
    return cfg


def snapshot(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        out[p.relative_to(root).as_posix()] = None if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def files_only(root: Path) -> list[str]:
    return sorted(hashlib.sha256(p.read_bytes()).hexdigest() + p.name for p in root.rglob("*") if p.is_file())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.desk = self.tmp / "Desktop"
        self.desk.mkdir()
        self.cfg = make_cfg(self.tmp)
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def server(self, mode="ok") -> MockOllama:
        s = MockOllama(mode)
        self.servers.append(s)
        return s

    def write(self, name, data="conteudo"):
        p = self.desk / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
        return p

    def scan(self, cfg=None):
        cfg = cfg or self.cfg
        cache = Cache(self.tmp / "data" / "cache.json")
        items, skipped = scanner.scan(self.desk, cfg, cache)
        return items, skipped, cache

    def classifier(self, srv, cache, cfg=None, timeout=5, mode="local", use_schema=True):
        cfg = cfg or self.cfg
        client = OllamaClient(srv.url, "m", timeout=timeout, retries=0, sleep=lambda s: None, use_schema=use_schema)
        return Classifier(cfg, client, cache, mode)

    def full(self, srv, cfg=None):
        items, skipped, cache = self.scan(cfg)
        clf = self.classifier(srv, cache, cfg)
        cls = clf.classify(items)
        plan = build_plan(items, cls, cfg or self.cfg, self.desk, "t", skipped)
        return items, cls, plan, clf


class TestSafe(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(sanitize_name("../../Windows"), "Windows")
        self.assertEqual(sanitize_name("a<b>c:d|e?"), "a b c d e")
        self.assertEqual(sanitize_name("CON"), "")
        self.assertEqual(sanitize_name("  ..  "), "")
        self.assertEqual(len(sanitize_name("x" * 200, 40)), 40)
        self.assertNotIn("/", sanitize_name("a/b\\c"))

    def test_extract_json(self):
        self.assertEqual(extract_json('<think>x</think>```json\n{"a":1}\n```'), {"a": 1})
        self.assertEqual(extract_json('Aqui: {"a": [1,2]} fim'), {"a": [1, 2]})
        with self.assertRaises(Exception):
            extract_json("nada de json")

    def test_config_validation(self):
        with self.assertRaises(ConfigError):
            deep_merge(copy.deepcopy(DEFAULT_CONFIG), {"ollama": {"modo": "x"}})
        with self.assertRaises(ConfigError):
            deep_merge(copy.deepcopy(DEFAULT_CONFIG), {"ollama": {"batch_size": "dez"}})
        p = Path(tempfile.mkdtemp()) / "c.json"
        p.write_text(json.dumps({"ollama": {"mode": "nuvem"}}))
        with self.assertRaises(ConfigError):
            load_config(str(p))
        p.write_text(json.dumps({"_nota": "ok", "classification": {"confidence_threshold": 0.9}}))
        self.assertEqual(load_config(str(p))["classification"]["confidence_threshold"], 0.9)


class TestScanner(Base):
    def test_file_kinds_and_content(self):
        self.write("nota.txt", "Reunião de projeto sobre o orçamento")
        self.write("foto.png", PNG)
        self.write("fatura.pdf", MINI_PDF)
        self.write("semextensao", "texto simples sem extensão")
        self.write("binario_sem_ext", b"\x00\x01\x02\xff" * 100)
        self.write("script.py", "print('oi')")
        with zipfile.ZipFile(self.desk / "arquivos.zip", "w") as z:
            z.writestr("a.txt", "x")
            z.writestr("b/c.txt", "y")
        with zipfile.ZipFile(self.desk / "NemonicCombat.jar", "w") as z:
            z.writestr("plugin.yml", "name: NemonicCombat\nmain: a.b.C\n")
        items, _, _ = self.scan()
        by = {i.name: i for i in items}
        self.assertEqual(by["nota.txt"].kind, "texto")
        self.assertIn("orçamento", by["nota.txt"].snippet)
        self.assertEqual(by["foto.png"].kind, "imagem")
        self.assertIsNone(by["foto.png"].snippet)
        self.assertEqual(by["fatura.pdf"].kind, "pdf")
        self.assertIn("Fatura", by["fatura.pdf"].snippet or "")
        self.assertEqual(by["semextensao"].kind, "texto")
        self.assertEqual(by["binario_sem_ext"].kind, "binario")
        self.assertIsNone(by["binario_sem_ext"].snippet)
        self.assertEqual(by["arquivos.zip"].kind, "arquivo_zip")
        self.assertIn("b/c.txt", by["arquivos.zip"].listing)
        self.assertIn("plugin.yml", by["NemonicCombat.jar"].meta["arquivos_de_metadados"])

    def test_protection(self):
        self.write("setup.exe", b"MZ")
        self.write("run.bat", "echo")
        self.write(".oculto", "x")
        self.write("~$doc.docx", "x")
        self.write("baixando.crdownload", "x")
        self.write("atalho.lnk", "x")
        self.write("livre.txt", "x")
        proj = self.desk / "meuprojeto"
        proj.mkdir()
        (proj / "package.json").write_text("{}")
        items, _, _ = self.scan()
        prot = {i.name for i in items if i.protected}
        self.assertEqual(prot, {"setup.exe", "run.bat", ".oculto", "~$doc.docx", "baixando.crdownload", "meuprojeto"})
        cfg = make_cfg(self.tmp, scan__move_shortcuts=False)
        items, _, _ = self.scan(cfg)
        self.assertIn("atalho.lnk", {i.name for i in items if i.protected})

    def test_recent_files_are_protected(self):
        self.write("recem.txt", "x")
        cfg = make_cfg(self.tmp)
        cfg["scan"]["recently_modified_seconds"] = 3600
        items, _, _ = self.scan(cfg)
        self.assertTrue(items[0].protected)

    def test_unreadable_file_does_not_break_scan(self):
        self.write("trancado.txt", "segredo")
        self.write("ok.txt", "ok")
        real = scanner.sha256_file

        def flaky(path):
            if path.endswith("trancado.txt"):
                raise PermissionError("bloqueado")
            return real(path)
        with mock.patch.object(scanner, "sha256_file", flaky):
            items, skipped, _ = self.scan()
        by = {i.name: i for i in items}
        self.assertIn("ok.txt", by)
        self.assertTrue(by["trancado.txt"].read_error)

    def test_large_file_not_hashed_and_snippet_bounded(self):
        cfg = make_cfg(self.tmp)
        cfg["scan"]["max_hash_bytes"] = 1000
        p = self.desk / "enorme.log"
        with open(p, "wb") as f:
            f.write(b"linha de log\n" * 20000)
        items, _, _ = self.scan(cfg)
        it = items[0]
        self.assertIsNone(it.sha256)
        self.assertLessEqual(len(it.snippet), cfg["classification"]["max_snippet_chars"])

    def test_groups_hint(self):
        for n in ("NemonicCombat.jar", "NemonicCombat-config.yml", "NemonicCombat-readme.md", "outra.txt"):
            self.write(n, "x")
        (self.desk / "NemonicCombat").mkdir()
        items, _, _ = self.scan()
        g = {i.name: i.group_hint for i in items}
        self.assertEqual({g[n] for n in ("NemonicCombat.jar", "NemonicCombat-config.yml", "NemonicCombat-readme.md", "NemonicCombat")}, {"NemonicCombat"})
        self.assertEqual(g["outra.txt"], "")

    def test_sensitive_names_never_send_content(self):
        self.write(".env", "API_KEY=SEGREDO123")
        self.write("minhas_senhas.txt", "banco: SEGREDO456")
        cfg = make_cfg(self.tmp)
        cfg["scan"]["protect_hidden"] = False
        items, _, cache = self.scan(cfg)
        srv = self.server()
        self.classifier(srv, cache, cfg).classify(items)
        blob = json.dumps(srv.requests)
        self.assertNotIn("SEGREDO", blob)

    def test_duplicates(self):
        self.write("a.txt", "igual")
        self.write("b.txt", "igual")
        self.write("c.txt", "diferente")
        items, _, _ = self.scan()
        d = find_duplicates(items)
        self.assertEqual(len(d), 1)
        self.assertEqual(sorted(d[0]["names"]), ["a.txt", "b.txt"])
        self.assertEqual(sorted(p.name for p in self.desk.iterdir()), ["a.txt", "b.txt", "c.txt"])


class TestClassifier(Base):
    def setUp(self):
        super().setUp()
        for n, c in (("relatorio.txt", "texto"), ("foto.jpg", PNG), ("fatura.pdf", MINI_PDF), ("codigo.py", "print(1)")):
            self.write(n, c)

    def run_mode(self, mode, **kw):
        srv = self.server(mode)
        items, _, cache = self.scan()
        clf = self.classifier(srv, cache, **kw)
        return srv, items, clf, clf.classify(items)

    def test_ok_and_privacy(self):
        srv, items, clf, cls = self.run_mode("ok")
        self.assertTrue(all(c.source == "ia" for c in cls.values()))
        blob = json.dumps(srv.requests)
        self.assertNotIn(str(self.desk), blob)
        self.assertIn("format", srv.requests[0])
        by = {i.name: cls[i.id] for i in items}
        self.assertEqual(by["fatura.pdf"].subcategory, "Financeiro")

    def test_cache_avoids_resending(self):
        srv, items, clf, _ = self.run_mode("ok")
        n = len(srv.requests)
        items2, _, cache2 = self.scan()
        clf2 = self.classifier(srv, cache2)
        cls2 = clf2.classify(items2)
        self.assertEqual(len(srv.requests), n)
        self.assertTrue(all(c.source == "cache" for c in cls2.values()))
        self.write("relatorio.txt", "conteudo totalmente novo")
        items3, _, cache3 = self.scan()
        self.classifier(srv, cache3).classify(items3)
        self.assertEqual(len(srv.requests), n + 1)

    def test_garbage_response_falls_back_without_crash(self):
        _, _, clf, cls = self.run_mode("garbage")
        self.assertTrue(all(c.source == "falha" for c in cls.values()))
        self.assertTrue(clf.errors)

    def test_wrong_ids_and_empty(self):
        for m in ("wrong_ids", "empty_items"):
            _, _, _, cls = self.run_mode(m)
            self.assertTrue(all(c.source == "falha" for c in cls.values()), m)

    def test_partial_response_retries_missing(self):
        _, _, _, cls = self.run_mode("partial")
        self.assertTrue(all(c.source == "ia" for c in cls.values()))

    def test_weird_values_sanitized(self):
        _, items, _, cls = self.run_mode("weird_values")
        for c in cls.values():
            self.assertEqual(c.confidence, 0.95)
            self.assertNotIn("/", c.category + c.subcategory)
            self.assertNotIn("..", c.category)

    def test_fenced_and_think(self):
        _, _, _, cls = self.run_mode("fenced")
        self.assertTrue(all(c.source == "ia" for c in cls.values()))

    def test_http500_and_timeout(self):
        _, _, _, cls = self.run_mode("http500")
        self.assertTrue(all(c.source == "falha" for c in cls.values()))
        srv = self.server("slow")
        srv.delay = 1.0
        items, _, cache = self.scan()
        clf = self.classifier(srv, cache, timeout=0.3)
        cls = clf.classify(items)
        self.assertTrue(all(c.source == "falha" for c in cls.values()))

    def test_offline_uses_heuristic_once(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        items, _, cache = self.scan()
        client = OllamaClient(f"http://127.0.0.1:{port}", "m", retries=0, sleep=lambda x: None)
        clf = Classifier(self.cfg, client, cache, "local")
        cls = clf.classify(items)
        self.assertTrue(all(c.source == "heuristica" for c in cls.values()))
        self.assertEqual(clf.stats["requisicoes"], 1)
        with self.assertRaises(OllamaUnavailable):
            client.version()

    def test_auth_error_is_unavailable(self):
        _, _, clf, cls = self.run_mode("auth")
        self.assertTrue(all(c.source == "heuristica" for c in cls.values()))
        self.assertIn("autenticação", clf.unavailable_reason)

    def test_schema_rejected_falls_back_to_prompt_only(self):
        srv, _, _, cls = self.run_mode("http400_schema")
        self.assertTrue(all(c.source == "ia" for c in cls.values()))
        self.assertNotIn("format", srv.requests[-1])

    def test_cloud_mode_sends_no_schema(self):
        srv = self.server("ok")
        items, _, cache = self.scan()
        self.classifier(srv, cache, mode="cloud", use_schema=False).classify(items)
        self.assertNotIn("format", srv.requests[0])

    def test_validate_item(self):
        ids = {"f1": FileItem("f1", "/x", "x", "", False, 0, 0, 0)}
        with self.assertRaises(ValueError):
            validate_item({"id": "f1", "category": ""}, ids, 40)
        with self.assertRaises(ValueError):
            validate_item("texto", ids, 40)
        _, c = validate_item({"id": "f1", "category": "A/B/C", "confidence": True}, ids, 40)
        self.assertEqual((c.category, c.subcategory, c.confidence), ("A", "B", 0.0))


class TestPlanner(Base):
    def test_low_confidence_goes_to_review(self):
        self.write("coisa1.txt", "x")
        self.write("relatorio.txt", "x")
        _, _, plan, _ = self.full(self.server())
        by = {e.name: e for e in plan.entries}
        self.assertEqual(by["coisa1.txt"].action, "revisar")
        self.assertEqual(by["coisa1.txt"].dest_dir, "_Revisar")
        self.assertEqual(by["relatorio.txt"].action, "mover")

    def test_low_confidence_keep_option(self):
        self.write("coisa1.txt", "x")
        cfg = make_cfg(self.tmp, classification__low_confidence_action="keep")
        _, _, plan, _ = self.full(self.server(), cfg)
        self.assertEqual(plan.entries[0].action, "manter")

    def test_project_grouping(self):
        for n in ("NemonicCombat.jar", "NemonicCombat-config.yml", "NemonicCombat-readme.md"):
            self.write(n, "x")
        (self.desk / "NemonicCombat").mkdir()
        (self.desk / "NemonicCombat" / "a.txt").write_text("x")
        cfg = make_cfg(self.tmp)
        cfg["scan"]["protect_project_dirs"] = False
        _, _, plan, _ = self.full(self.server(), cfg)
        dest = {e.name: e.dest_dir for e in plan.entries}
        self.assertEqual(dest["NemonicCombat"], "Desenvolvimento/Minecraft")
        for n in ("NemonicCombat.jar", "NemonicCombat-config.yml", "NemonicCombat-readme.md"):
            self.assertEqual(dest[n], "Desenvolvimento/Minecraft/NemonicCombat")

    def test_category_limit(self):
        srv = self.server()
        for i in range(30):
            self.write(f"arq{i}.txt", f"x{i}")
        items, skipped, cache = self.scan()
        clf = self.classifier(srv, cache)
        cls = clf.classify(items)
        for n, it in enumerate(items):
            cls[it.id] = Classification(category=f"Cat{n}", confidence=0.9, source="ia")
        for n in range(5):
            cls[items[n].id] = Classification(category="Grande", confidence=0.9, source="ia")
        plan = build_plan(items, cls, self.cfg, self.desk, "t")
        tops = {e.dest_dir.split("/")[0] for e in plan.entries if e.action == "mover"}
        self.assertLessEqual(len(tops), self.cfg["classification"]["max_top_level_categories"])
        self.assertIn("Grande", tops)
        self.assertTrue(any("Categorias demais" in w for w in plan.warnings))
        depth = max(len(e.dest_dir.split("/")) for e in plan.entries if e.action == "mover")
        self.assertLessEqual(depth, self.cfg["classification"]["max_depth"] + 1)

    def test_similar_names_merge(self):
        self.write("a.txt", "1"); self.write("b.txt", "2"); self.write("c.txt", "3")
        items, _, cache = self.scan()
        cls = {items[0].id: Classification(category="Documentos", confidence=0.9, source="ia"),
               items[1].id: Classification(category="documentos", confidence=0.9, source="ia"),
               items[2].id: Classification(category="Documentos", confidence=0.9, source="ia")}
        plan = build_plan(items, cls, self.cfg, self.desk, "t")
        self.assertEqual({e.dest_dir for e in plan.entries}, {"Documentos"})

    def test_protected_never_planned_to_move(self):
        self.write("setup.exe", b"MZ")
        _, _, plan, _ = self.full(self.server())
        self.assertEqual(plan.entries[0].action, "protegido")

    def test_existing_folder_used_as_category(self):
        (self.desk / "Documentos").mkdir()
        (self.desk / "Documentos" / "velho.txt").write_text("v")
        self.write("a.txt", "1"); self.write("b.txt", "2")
        _, _, plan, _ = self.full(self.server())
        by = {e.name: e for e in plan.entries}
        self.assertEqual(by["Documentos"].action, "manter")
        self.assertEqual(by["a.txt"].dest_dir.split("/")[0], "Documentos")

    def test_file_named_like_category(self):
        self.write("Documentos", "sou um arquivo")
        self.write("a.txt", "1")
        items, _, cache = self.scan()
        cls = {i.id: Classification(category="Documentos", confidence=0.9, source="ia") for i in items}
        plan = build_plan(items, cls, self.cfg, self.desk, "t")
        self.assertTrue(all(e.dest_dir == "Documentos (pasta)" for e in plan.entries))

    def test_render_and_json_roundtrip(self):
        self.write("a.txt", "1"); self.write("b.txt", "1")
        _, _, plan, _ = self.full(self.server())
        txt = render_plan(plan, detailed=True)
        self.assertIn("duplicatas", txt)
        self.assertIn("destino: Desktop/", txt)
        p2 = plan_from_json(plan_to_json(plan))
        self.assertEqual([e.dest_dir for e in p2.entries], [e.dest_dir for e in plan.entries])


class TestExecutor(Base):
    def prepare(self, names=("a.txt", "b.txt", "foto.png", "c.py")):
        for n in names:
            self.write(n, PNG if n.endswith(".png") else n + " conteudo")
        _, _, plan, _ = self.full(self.server())
        return plan

    def data(self):
        return self.tmp / "data"

    def test_dry_run_changes_nothing(self):
        plan = self.prepare()
        before = snapshot(self.desk)
        res = executor.execute(plan, self.data(), dry_run=True)
        self.assertEqual(snapshot(self.desk), before)
        self.assertGreater(len(res["moved"]), 0)
        self.assertFalse((self.data() / "journal").exists())

    def test_execute_and_undo_roundtrip(self):
        plan = self.prepare()
        before = snapshot(self.desk)
        res = executor.execute(plan, self.data())
        self.assertEqual(len(res["failed"]), 0)
        self.assertNotEqual(snapshot(self.desk), before)
        u = executor.undo(self.data())
        self.assertEqual(len(u["failed"]), 0)
        self.assertEqual(snapshot(self.desk), before)
        self.assertEqual(executor.undo(self.data())["error"], "nenhuma execução pendente de desfazer")

    def test_nothing_is_ever_deleted(self):
        plan = self.prepare()
        before = files_only(self.desk)

        def boom(*a, **k):
            raise AssertionError("tentativa de apagar arquivo")
        with mock.patch("os.remove", boom), mock.patch("os.unlink", boom), mock.patch("shutil.rmtree", boom):
            executor.execute(plan, self.data())
            self.assertEqual(files_only(self.desk), before)
            executor.undo(self.data())
            self.assertEqual(files_only(self.desk), before)

    def test_name_collision_never_overwrites(self):
        plan = self.prepare(("a.txt", "b.txt"))
        dest = self.desk / plan.entries[0].dest_dir
        dest.mkdir(parents=True)
        (dest / "a.txt").write_text("EXISTENTE")
        executor.execute(plan, self.data())
        self.assertEqual((dest / "a.txt").read_text(), "EXISTENTE")
        self.assertTrue((dest / "a (1).txt").exists())
        executor.undo(self.data())
        self.assertEqual((dest / "a.txt").read_text(), "EXISTENTE")
        self.assertTrue((self.desk / "a.txt").exists())

    def test_one_failure_does_not_stop_the_rest(self):
        plan = self.prepare()
        real = os.rename

        def flaky(src, dst):
            if str(src).endswith("b.txt"):
                raise PermissionError("arquivo bloqueado")
            return real(src, dst)
        with mock.patch("os.rename", flaky):
            res = executor.execute(plan, self.data())
        self.assertEqual(len(res["failed"]), 1)
        self.assertEqual(len(res["moved"]), 3)
        self.assertTrue((self.desk / "b.txt").exists())

    def test_interruption_then_undo(self):
        plan = self.prepare()
        before = snapshot(self.desk)
        real, calls = os.rename, {"n": 0}

        def interrupt(src, dst):
            calls["n"] += 1
            if calls["n"] == 3:
                raise KeyboardInterrupt()
            return real(src, dst)
        with mock.patch("os.rename", interrupt):
            res = executor.execute(plan, self.data())
        self.assertEqual(res["status"], "interrompido")
        self.assertEqual(len(res["moved"]), 2)
        self.assertEqual(executor.list_runs(self.data())[-1]["status"], "interrompido")
        u = executor.undo(self.data())
        self.assertEqual(len(u["failed"]), 0)
        self.assertEqual(snapshot(self.desk), before)

    def test_crash_after_rename_before_confirmation_is_undoable(self):
        plan = self.prepare(("a.txt",))
        e = plan.entries[0]
        j = executor.Journal(self.data(), "crash-run")
        j.write(op="run_start", target=str(self.desk))
        dst_dir = self.desk / e.dest_dir
        dst_dir.mkdir(parents=True)
        j.write(op="mkdir", path=str(dst_dir))
        dst = dst_dir / e.name
        j.write(op="move_intent", n=1, id=e.id, src=e.src, dst=str(dst))
        os.rename(e.src, dst)
        with open(j.path, "a") as f:
            f.write('{"op": "move_do')
        u = executor.undo(self.data())
        self.assertEqual(len(u["restored"]), 1)
        self.assertTrue(Path(e.src).exists())

    def test_modified_file_after_plan_is_skipped(self):
        plan = self.prepare(("a.txt", "b.txt"))
        (self.desk / "a.txt").write_text("alterado depois da análise, agora com outro tamanho")
        res = executor.execute(plan, self.data())
        self.assertTrue(any(n == "a.txt" for n, _ in res["skipped"]))
        self.assertTrue((self.desk / "a.txt").exists())

    def test_malicious_destinations_rejected(self):
        plan = self.prepare(("a.txt",))
        for bad in ("../fora", "/etc", "C:\\Windows", "ok/../../fora", "", "a//b"):
            plan.entries[0].dest_dir = bad
            res = executor.execute(plan, self.data(), dry_run=True)
            self.assertEqual(len(res["moved"]), 0, bad)
        self.assertTrue((self.desk / "a.txt").exists())

    def test_ai_returning_system_path_is_contained(self):
        self.write("a.txt", "x")
        _, _, plan, _ = self.full(self.server("injection_obey"))
        executor.execute(plan, self.data())
        for p in self.desk.rglob("*"):
            self.assertTrue(str(p).startswith(str(self.desk)))
        self.assertFalse(Path("C:\\Windows").exists() if os.name != "nt" else False)
        self.assertEqual(plan.entries[0].dest_dir.split("/")[0], "C")

    def test_undo_when_source_slot_reoccupied(self):
        plan = self.prepare(("a.txt",))
        executor.execute(plan, self.data())
        (self.desk / "a.txt").write_text("novo arquivo do usuário")
        u = executor.undo(self.data())
        self.assertEqual((self.desk / "a.txt").read_text(), "novo arquivo do usuário")
        self.assertTrue((self.desk / "a (restaurado).txt").exists())
        self.assertEqual(len(u["failed"]), 0)

    def test_undo_partial_when_dest_missing_is_retryable(self):
        plan = self.prepare(("a.txt", "b.txt"))
        executor.execute(plan, self.data())
        moved = [p for p in self.desk.rglob("a.txt")][0]
        moved.rename(self.tmp / "levado_para_fora.txt")
        u = executor.undo(self.data())
        self.assertEqual(len(u["failed"]), 1)
        self.assertTrue((self.desk / "b.txt").exists())
        self.assertFalse(executor.list_runs(self.data())[-1]["undone"])

    def test_directory_moves_as_unit(self):
        d = self.desk / "PastaX"
        d.mkdir()
        (d / "in.txt").write_text("dentro")
        cfg = make_cfg(self.tmp)
        items, skipped, cache = self.scan(cfg)
        cls = {items[0].id: Classification(category="Estudos", confidence=0.9, source="ia")}
        plan = build_plan(items, cls, cfg, self.desk, "t")
        executor.execute(plan, self.data())
        self.assertTrue((self.desk / "Estudos" / "PastaX" / "in.txt").exists())
        executor.undo(self.data())
        self.assertTrue((self.desk / "PastaX" / "in.txt").exists())
        self.assertFalse((self.desk / "Estudos").exists())

    def test_managed_dirs_skipped_next_scan(self):
        plan = self.prepare(("a.txt", "b.txt"))
        executor.execute(plan, self.data())
        names = executor.managed_dirs(self.data(), self.desk)
        self.assertTrue(names)
        cache = Cache(self.tmp / "data" / "cache.json")
        items, skipped = scanner.scan(self.desk, self.cfg, cache, names)
        self.assertEqual(items, [])
        executor.undo(self.data())
        self.assertEqual(executor.managed_dirs(self.data(), self.desk), set())


class TestCLI(Base):
    def cfg_file(self, url, **extra):
        cfg = make_cfg(self.tmp)
        cfg["target_dir"] = str(self.desk)
        cfg["ollama"]["local_url"] = url
        cfg["ollama"]["batch_size"] = 5
        for k, v in extra.items():
            sect, key = k.split("__")
            cfg[sect][key] = v
        p = self.tmp / "config.json"
        p.write_text(json.dumps(cfg))
        return str(p)

    def run_cli(self, *args, stdin=None):
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch("builtins.input", side_effect=stdin or (lambda *a: "n")):
            code = cli.main(list(args))
        return code, buf.getvalue()

    def populate(self):
        self.write("relatorio.txt", "texto do relatório")
        self.write("relatorio_copia.txt", "texto do relatório")
        self.write("foto.jpg", PNG)
        self.write("fatura.pdf", MINI_PDF)
        self.write("codigo.py", "print(1)")
        self.write("coisa_estranha.dat", b"\x00\x01\x02")
        self.write("setup.exe", b"MZ")
        self.write("NemonicCombat.jar", "x")
        self.write("NemonicCombat-config.yml", "a: 1")
        self.write("NemonicCombat-readme.md", "# leia")
        return snapshot(self.desk)

    def test_dry_run_e2e(self):
        srv = self.server()
        before = self.populate()
        code, out = self.run_cli("--dry-run", "--config", self.cfg_file(srv.url))
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", out)
        self.assertIn("Possíveis duplicatas", out)
        self.assertEqual(snapshot(self.desk), before)

    def test_declined_confirmation_changes_nothing(self):
        srv = self.server()
        before = self.populate()
        code, out = self.run_cli("--config", self.cfg_file(srv.url), stdin=lambda *a: "n")
        self.assertIn("Cancelado", out)
        self.assertEqual(snapshot(self.desk), before)

    def test_full_flow_and_undo(self):
        srv = self.server()
        before = self.populate()
        files_before = files_only(self.desk)
        cfgf = self.cfg_file(srv.url)
        code, out = self.run_cli("--config", cfgf, "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(files_only(self.desk), files_before)
        self.assertTrue((self.desk / "setup.exe").exists())
        self.assertTrue((self.desk / "_Revisar" / "coisa_estranha.dat").exists())
        self.assertTrue((self.desk / "Desenvolvimento" / "Minecraft" / "NemonicCombat" / "NemonicCombat.jar").exists())
        n_requests = len(srv.requests)
        code, out = self.run_cli("history", "--config", cfgf)
        self.assertIn("movidos=", out)
        code, out = self.run_cli("undo", "--config", cfgf, "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(snapshot(self.desk), before)
        self.assertEqual(len(srv.requests), n_requests)

    def test_second_run_uses_cache(self):
        srv = self.server()
        self.populate()
        cfgf = self.cfg_file(srv.url)
        self.run_cli("--dry-run", "--config", cfgf)
        n = len(srv.requests)
        code, out = self.run_cli("--dry-run", "--config", cfgf)
        self.assertEqual(len(srv.requests), n)
        self.assertIn("do cache", out)

    def test_ollama_offline_e2e(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        before = self.populate()
        files_before = files_only(self.desk)
        code, out = self.run_cli("--config", self.cfg_file(f"http://127.0.0.1:{port}"), "--yes")
        self.assertEqual(code, 0, out)
        self.assertIn("AVISO", out)
        self.assertTrue((self.desk / "_Revisar").is_dir())
        self.assertEqual(files_only(self.desk), files_before)
        self.run_cli("undo", "--config", self.cfg_file(f"http://127.0.0.1:{port}"), "--yes")
        self.assertEqual(snapshot(self.desk), before)

    def test_invalid_ai_response_e2e(self):
        srv = self.server("garbage")
        before = self.populate()
        code, out = self.run_cli("--config", self.cfg_file(srv.url), "--yes")
        self.assertEqual(code, 0, out)
        self.assertNotIn("Traceback", out)
        self.run_cli("undo", "--config", self.cfg_file(srv.url), "--yes")
        self.assertEqual(snapshot(self.desk), before)

    def test_plan_then_apply(self):
        srv = self.server()
        before = self.populate()
        cfgf = self.cfg_file(srv.url)
        planfile = str(self.tmp / "meu_plano.json")
        code, out = self.run_cli("plan", "--config", cfgf, "--plan-file", planfile)
        self.assertEqual(snapshot(self.desk), before)
        self.assertTrue(Path(planfile).exists())
        code, out = self.run_cli("apply", planfile, "--config", cfgf, "--yes")
        self.assertEqual(code, 0, out)
        self.assertNotEqual(snapshot(self.desk), before)

    def test_ask_protected_moves_only_when_approved(self):
        srv = self.server()
        self.populate()
        cfgf = self.cfg_file(srv.url)
        answers = iter(["s", "n"])
        self.run_cli("--config", cfgf, "--ask-protected", stdin=lambda *a: next(answers))
        self.assertTrue((self.desk / "setup.exe").exists())

    def test_models_and_bad_config(self):
        srv = self.server()
        code, out = self.run_cli("models", "--config", self.cfg_file(srv.url))
        self.assertIn("llama3.2:latest", out)
        bad = self.tmp / "bad.json"
        bad.write_text('{"ollama": {"mode": "x"}}')
        code, out = self.run_cli("--config", str(bad), "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("Erro de configuração", out)

    def test_duplicates_command(self):
        self.write("a.txt", "igual"); self.write("b.txt", "igual")
        code, out = self.run_cli("duplicates", "--config", self.cfg_file("http://127.0.0.1:1"))
        self.assertIn("mesmo hash", out)
        self.assertTrue((self.desk / "a.txt").exists() and (self.desk / "b.txt").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
