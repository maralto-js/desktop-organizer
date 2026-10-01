import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organizer import Base, files_only, make_cfg, snapshot
from mock_ollama import MARKER

from desktop_organizer import appinfo, executor
from desktop_organizer.classifier import Classifier
from desktop_organizer.models import PROTEGIDO_TIPO
from desktop_organizer.planner import build_plan


def make_lnk(alvo: str, pasta: str = "", args: str = "--token=SEGREDO", icone: str = "", unicode_: bool = True) -> bytes:
    flags = (1 << 1) | (1 << 4 if pasta else 0) | (1 << 5 if args else 0) | (1 << 6 if icone else 0) | (1 << 7 if unicode_ else 0)
    header = struct.pack("<I16sI", 0x4C, b"\x01\x14\x02\x00" + b"\0" * 12, flags) + b"\0" * (76 - 24)
    local = alvo.encode("cp1252") + b"\0"
    cab = 0x1C
    vol = b"\x10\0\0\0" + b"\x03\0\0\0" + b"\0\0\0\0" + b"\x10\0\0\0" + b"\0"
    off_vol = cab
    off_local = off_vol + len(vol)
    suf = b"\0"
    off_suf = off_local + len(local)
    info = struct.pack("<7I", cab + len(vol) + len(local) + len(suf), cab, 1, off_vol, off_local, 0, off_suf) + vol + local + suf

    def s(t: str) -> bytes:
        return struct.pack("<H", len(t)) + (t.encode("utf-16-le") if unicode_ else t.encode("cp1252"))

    dados = header + info
    if pasta:
        dados += s(pasta)
    if args:
        dados += s(args)
    if icone:
        dados += s(icone)
    return dados


def make_pe(produto="", empresa="", descricao="") -> bytes:
    def par(chave, valor):
        return b"\0\0" + chave.encode("utf-16-le") + b"\0\0" + b"\0\0" + valor.encode("utf-16-le") + b"\0\0"
    corpo = b""
    for k, v in (("CompanyName", empresa), ("FileDescription", descricao), ("ProductName", produto)):
        if v:
            corpo += par(k, v)
    return b"MZ" + b"\0" * 62 + b"PE\0\0" + b"\0" * 200 + corpo + b"\0" * 64


class ParsersTests(unittest.TestCase):
    def test_lnk_unicode_e_ansi(self):
        for uni in (True, False):
            d = appinfo.ler_lnk(make_lnk(r"C:\Riot Games\Riot Client\RiotClientServices.exe",
                                         pasta=r"C:\Riot Games", icone=r"C:\Riot Games\icone.ico", unicode_=uni))
            self.assertEqual(d["alvo"], r"C:\Riot Games\Riot Client\RiotClientServices.exe")
            self.assertEqual(d["pasta"], r"C:\Riot Games")
            self.assertEqual(d["icone"], r"C:\Riot Games\icone.ico")
            self.assertNotIn("args", d)
            self.assertNotIn("SEGREDO", str(d))

    def test_lnk_invalido(self):
        with self.assertRaises(ValueError):
            appinfo.ler_lnk(b"isto nao e um atalho" * 10)

    def test_url_remove_query_string(self):
        d = appinfo.ler_url("[InternetShortcut]\nURL=steam://rungameid/730?token=abc#x\nIconFile=C:\\Steam\\730.ico\n")
        self.assertEqual(d["endereco"], "steam://rungameid/730")
        self.assertEqual(d["icone"], "C:\\Steam\\730.ico")

    def test_desktop_entry(self):
        d = appinfo.ler_desktop("[Desktop Entry]\nName=Firefox\nExec=/usr/bin/firefox %u\nCategories=Network;WebBrowser;\n")
        self.assertEqual((d["nome"], d["alvo"]), ("Firefox", "/usr/bin/firefox"))
        self.assertIn("WebBrowser", d["categorias"])

    def test_versao_pe(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "x.exe"
            p.write_bytes(make_pe(produto="League of Legends", empresa="Riot Games, Inc.", descricao="Jogo"))
            self.assertEqual(appinfo.ler_versao_pe(str(p)),
                             {"produto": "League of Legends", "empresa": "Riot Games, Inc.", "descricao": "Jogo"})
            (Path(t) / "y.exe").write_bytes(b"MZ" + b"\0" * 100)
            self.assertEqual(appinfo.ler_versao_pe(str(Path(t) / "y.exe")), {})
            (Path(t) / "z.exe").write_bytes(b"")
            self.assertEqual(appinfo.ler_versao_pe(str(Path(t) / "z.exe")), {})

    def test_adivinhar_tipo(self):
        casos = {"League of Legends": "Jogos", "Riot Client": "Jogos", r"C:\Steam\steamapps\common\X\x.exe": "Jogos",
                 "Visual Studio Code": "Desenvolvimento", "Discord": "Comunicação", "Brave": "Navegadores",
                 "Spotify": "Mídia", "Kaspersky": "Utilitários", "Obsidian": "Produtividade",
                 "Gamers Club Anti-Cheat": "Jogos", "digital ocean": "", "Nova pasta": ""}
        for texto, esperado in casos.items():
            self.assertEqual(appinfo.adivinhar_tipo(texto), esperado, texto)

    def test_anonimiza_usuario(self):
        self.assertEqual(appinfo.anonimizar_caminho(r"C:\Users\Maralto\AppData\Roaming\x.exe"),
                         r"%USERPROFILE%\AppData\Roaming\x.exe")
        self.assertEqual(appinfo.anonimizar_caminho("/home/maria/jogos"), "~/jogos")

    def test_contexto_nunca_levanta(self):
        self.assertEqual(appinfo.contexto_do_item("/nao/existe.lnk", "x.lnk", ".lnk"), {})
        self.assertEqual(appinfo.contexto_do_item("/nao/existe.exe", "x.exe", ".exe"), {})
        self.assertEqual(appinfo.contexto_do_item("/x.txt", "x.txt", ".txt"), {})


class IntegracaoTests(Base):
    def setUp(self):
        super().setUp()
        self.write("League of Legends.lnk", make_lnk(r"C:\Riot Games\League of Legends\LeagueClient.exe",
                                                      pasta=r"C:\Users\Maralto\Jogos"))
        self.write("Steam Jogo.url", "[InternetShortcut]\nURL=steam://rungameid/730?t=SEG\n")
        self.write("Discord.lnk", make_lnk(r"C:\Users\Maralto\AppData\Local\Discord\Update.exe"))
        self.write("Hytale Launcher.exe", make_pe(produto="Hytale", empresa="Hypixel Studios"))
        self.write("Spotify.lnk", make_lnk(r"C:\Spotify\Spotify.exe"))
        self.write("notas.txt", "texto qualquer")

    def test_scanner_extrai_contexto_e_nao_protege_atalhos(self):
        items, _, _ = self.scan()
        por = {i.name: i for i in items}
        lol = por["League of Legends.lnk"]
        self.assertEqual(lol.kind, "atalho")
        self.assertFalse(lol.protected)
        self.assertEqual(lol.meta["tipo_provavel_app"], "Jogos")
        self.assertIn("LeagueClient.exe", lol.meta["alvo_do_atalho"])
        self.assertEqual(por["Steam Jogo.url"].meta["alvo_do_atalho"], "steam://rungameid/730")
        self.assertNotIn("%USERPROFILE%", por["League of Legends.lnk"].meta.get("pasta", ""))
        self.assertTrue(por["Discord.lnk"].meta["alvo_do_atalho"].startswith("%USERPROFILE%"))
        exe = por["Hytale Launcher.exe"]
        self.assertEqual((exe.kind, exe.protected), ("executavel", True))
        self.assertTrue(exe.protect_reason.startswith(PROTEGIDO_TIPO))
        self.assertEqual(exe.meta["produto"], "Hytale")
        self.assertEqual(exe.meta["tipo_provavel_app"], "Jogos")

    def test_contexto_vai_no_payload_para_a_ia_sem_argumentos(self):
        items, _, cache = self.scan()
        srv = self.server()
        clf = self.classifier(srv, cache)
        clf.classify(items)
        enviado = " ".join(r["messages"][-1]["content"] for r in srv.requests)
        self.assertIn("alvo_do_atalho", enviado)
        self.assertIn("tipo_provavel_app", enviado)
        self.assertNotIn("SEGREDO", enviado)
        self.assertNotIn("Maralto", enviado)
        self.assertIn("ATALHOS E PROGRAMAS", srv.requests[0]["messages"][0]["content"])

    def test_context_desligado_nao_le_nada(self):
        cfg = make_cfg(self.tmp, scan__app_context=False)
        items, _, _ = self.scan(cfg)
        lol = next(i for i in items if i.name == "League of Legends.lnk")
        self.assertEqual(lol.meta, {})

    def test_nome_sensivel_nao_tem_contexto_lido(self):
        self.write("senha do banco.lnk", make_lnk(r"C:\Banco\app.exe"))
        items, _, _ = self.scan()
        item = next(i for i in items if i.name == "senha do banco.lnk")
        self.assertTrue(item.content_blocked)
        self.assertNotIn("alvo_do_atalho", item.meta)

    def test_ia_offline_ainda_organiza_programas_por_palavras_chave(self):
        items, _, cache = self.scan()
        clf = Classifier(self.cfg, None, cache, "local")
        cls = clf.classify(items)
        plan = build_plan(items, cls, self.cfg, self.desk, "t")
        por = {e.name: e for e in plan.entries}
        self.assertEqual(por["League of Legends.lnk"].action, "mover")
        self.assertTrue(por["League of Legends.lnk"].dest_dir.startswith("Programas"))
        self.assertIn("Jogos", por["League of Legends.lnk"].dest_dir)

    def test_plano_com_ia_programas_agrupados_e_exe_protegido_com_sugestao(self):
        items, skipped, cache = self.scan()
        srv = self.server()

        clf = self.classifier(srv, cache)
        cls = clf.classify(items)
        from desktop_organizer.models import Classification
        for it in items:
            if it.kind in ("atalho", "executavel"):
                sub = it.meta.get("tipo_provavel_app", "")
                cls[it.id] = Classification(category="Programas", subcategory=sub, confidence=0.9, source="ia",
                                            description="programa", reason="contexto")
        plan = build_plan(items, cls, self.cfg, self.desk, "t", skipped)
        por = {e.name: e for e in plan.entries}
        self.assertEqual(por["League of Legends.lnk"].action, "mover")
        self.assertEqual(por["League of Legends.lnk"].dest_dir, "Programas/Jogos")
        self.assertEqual(por["Steam Jogo.url"].dest_dir, "Programas/Jogos")
        exe = por["Hytale Launcher.exe"]
        self.assertEqual((exe.action, exe.dest_dir), ("protegido", "Programas/Jogos"))

    def test_exe_protegido_so_move_se_aprovado_e_desfaz(self):
        items, skipped, cache = self.scan()
        from desktop_organizer.models import Classification
        cls = {it.id: Classification(category="Programas", subcategory=it.meta.get("tipo_provavel_app", ""),
                                     confidence=0.9, source="ia") for it in items}
        plan = build_plan(items, cls, self.cfg, self.desk, "t", skipped)
        exe = next(e for e in plan.entries if e.name == "Hytale Launcher.exe")
        antes = files_only(self.desk)
        res = executor.execute(plan, self.tmp / "data")
        self.assertTrue((self.desk / "Hytale Launcher.exe").exists())
        self.assertFalse((self.desk / "League of Legends.lnk").exists())
        res2 = executor.execute(plan, self.tmp / "data2", approved_protected={exe.id})
        self.assertFalse(res2["failed"])
        self.assertEqual(files_only(self.desk), antes)
        executor.undo(self.tmp / "data")
        self.assertTrue((self.desk / "League of Legends.lnk").exists())

    def test_impressao_digital_de_programas_muda_para_invalidar_cache_antigo(self):
        import hashlib
        items, _, _ = self.scan()
        lol = next(i for i in items if i.name == "League of Legends.lnk")
        txt = next(i for i in items if i.name == "notas.txt")
        antigo = hashlib.sha1(f"{lol.name}|{lol.ext}|{lol.sha256}".encode()).hexdigest()
        self.assertNotEqual(lol.fingerprint, antigo)
        antigo_txt = hashlib.sha1(f"{txt.name}|{txt.ext}|{txt.sha256}".encode()).hexdigest()
        self.assertEqual(txt.fingerprint, antigo_txt)


if __name__ == "__main__":
    unittest.main()
