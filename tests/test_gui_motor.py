import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from desktop_organizer.config import DEFAULT_CONFIG
from desktop_organizer.gui import motor

import copy


class Fake(BaseHTTPRequestHandler):
    modo = "ok"
    visto = {}

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        Fake.visto["auth"] = self.headers.get("Authorization")
        if self.path == "/api/usage":
            if Fake.modo == "404":
                return self._json(404, {"error": "nope"})
            if Fake.modo == "401":
                return self._json(401, {"error": "unauthorized"})
            if Fake.modo == "lixo":
                return self._json(200, {"outro": 1})
            return self._json(200, {"limits": {"session": {"usage": 0.25}, "weekly": {"usage": 60}}})
        if Fake.modo == "401":
            return self._json(401, {"error": "unauthorized"})
        if Fake.modo == "vazio":
            return self._json(200, {"models": []})
        self._json(200, {"models": [{"name": "zeta:1b", "size": 2_600_000_000}, {"name": "alfa:8b", "size": 5_000_000_000}]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        Fake.visto["chat"] = json.loads(self.rfile.read(n))
        if Fake.modo == "erro_chat":
            return self._json(401, {"error": "you need to sign in"})
        self._json(200, {"message": {"content": "oi"}})


class MotorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Fake)
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Fake.modo = "ok"
        Fake.visto = {}

    def test_tamanho_humano(self):
        self.assertEqual(motor.tamanho_humano(None), "?")
        self.assertEqual(motor.tamanho_humano(512), "512 B")
        self.assertEqual(motor.tamanho_humano(2_600_000_000), "2.4 GB")

    def test_separar_nome(self):
        self.assertEqual(motor.separar_nome_modelo("qwen3:4b  (2.4 GB)"), "qwen3:4b")
        self.assertEqual(motor.separar_nome_modelo("llama3.2"), "llama3.2")
        self.assertEqual(motor.separar_nome_modelo(""), "")

    def test_modelos_locais_ordenados_com_tamanho(self):
        ok, dados = motor.listar_modelos_locais(self.url)
        self.assertTrue(ok)
        self.assertEqual([m["nome"] for m in dados], ["alfa:8b", "zeta:1b"])
        self.assertEqual(dados[0]["tamanho"], "4.7 GB")

    def test_modelos_locais_servidor_fora(self):
        ok, msg = motor.listar_modelos_locais("http://127.0.0.1:1")
        self.assertFalse(ok)
        self.assertIn("Não consegui falar", msg)

    def test_modelos_cloud_api_envia_chave(self):
        ok, nomes = motor.listar_modelos_cloud_api("  segredo ", self.url)
        self.assertTrue(ok)
        self.assertEqual(Fake.visto["auth"], "Bearer segredo")
        self.assertEqual(nomes, ["alfa:8b", "zeta:1b"])

    def test_modelos_cloud_api_sem_chave_nao_chama_rede(self):
        ok, msg = motor.listar_modelos_cloud_api("   ", self.url)
        self.assertFalse(ok)
        self.assertNotIn("auth", Fake.visto)

    def test_modelos_cloud_api_chave_invalida_e_lista_vazia(self):
        Fake.modo = "401"
        ok, msg = motor.listar_modelos_cloud_api("x", self.url)
        self.assertFalse(ok)
        self.assertIn("inválida", msg)
        Fake.modo = "vazio"
        ok, msg = motor.listar_modelos_cloud_api("x", self.url)
        self.assertFalse(ok)
        self.assertIn("vazia", msg)

    def test_verificar_login_ok_e_recusado(self):
        ok, _ = motor.verificar_login_cloud(self.url, "gpt-oss:20b-cloud")
        self.assertTrue(ok)
        self.assertEqual(Fake.visto["chat"]["options"]["num_predict"], 1)
        self.assertEqual(Fake.visto["chat"]["model"], "gpt-oss:20b-cloud")
        Fake.modo = "erro_chat"
        ok, msg = motor.verificar_login_cloud(self.url, "")
        self.assertFalse(ok)
        self.assertEqual(Fake.visto["chat"]["model"], motor.MODELOS_CLOUD_EXEMPLO[0])

    def test_signin_sem_cli(self):
        with mock.patch.object(motor, "cli_disponivel", return_value=False):
            ok, msg, url = motor.rodar_signin()
            self.assertFalse(ok)
            self.assertIsNone(url)
            ok, msg = motor.rodar_signout()
            self.assertFalse(ok)

    def test_signin_extrai_link_impresso(self):
        r = mock.Mock(returncode=1, stdout="Open https://ollama.com/connect?name=pc&key=abc to sign in\n", stderr="")
        with mock.patch.object(motor, "cli_disponivel", return_value=True), \
                mock.patch.object(motor, "_rodar_cli", return_value=r):
            ok, saida, url = motor.rodar_signin()
        self.assertEqual(url, "https://ollama.com/connect?name=pc&key=abc")

    def test_signout_repassa_saida(self):
        r = mock.Mock(returncode=0, stdout="You've been signed out.", stderr="")
        with mock.patch.object(motor, "cli_disponivel", return_value=True), \
                mock.patch.object(motor, "_rodar_cli", return_value=r) as c:
            ok, saida = motor.rodar_signout()
        self.assertTrue(ok)
        c.assert_called_once_with(["signout"], 30)

    def test_construir_cliente_por_modo(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        c, mode, m = motor.construir_cliente(cfg, "local", "llama3.2")
        self.assertEqual((mode, c.use_schema, c.api_key), ("local", True, ""))
        c, mode, m = motor.construir_cliente(cfg, "cloud_login", "gpt-oss:20b-cloud", "ignorada")
        self.assertEqual((mode, c.use_schema, c.api_key, c.base), ("cloud", False, "", cfg["ollama"]["local_url"]))
        c, mode, m = motor.construir_cliente(cfg, "local", "gpt-oss:120b-cloud")
        self.assertEqual((mode, c.use_schema), ("cloud", False))
        c, mode, m = motor.construir_cliente(cfg, "cloud_api", "gpt-oss:120b", " k ")
        self.assertEqual((mode, c.use_schema, c.api_key, c.base), ("cloud", False, "k", cfg["ollama"]["cloud_url"]))
        with self.assertRaises(ValueError):
            motor.construir_cliente(cfg, "outro", "x")

    def test_usado_para_percentual(self):
        f = motor.usado_para_percentual
        self.assertEqual(f(0.25), 25.0)
        self.assertEqual(f(60), 60.0)
        self.assertEqual(f(1.0), 100.0)
        self.assertEqual(f(0.5, "percentual"), 0.5)
        self.assertEqual(f(42, "fracao"), 100.0)
        self.assertEqual(f(-3, "percentual"), 0.0)

    def test_cota_ok_e_erros(self):
        u = self.url + "/api/usage"
        ok, d = motor.consultar_uso_cloud(" k ", url=u)
        self.assertTrue(ok)
        self.assertEqual((d["sessao"], d["semana"], d["bruto"]), (25.0, 60.0, (0.25, 60.0)))
        self.assertEqual(Fake.visto["auth"], "Bearer k")
        Fake.modo = "404"
        ok, msg = motor.consultar_uso_cloud("k", url=u)
        self.assertFalse(ok)
        self.assertIn("404", msg)
        Fake.modo = "401"
        ok, msg = motor.consultar_uso_cloud("k", url=u)
        self.assertIn("inválida", msg)
        Fake.modo = "lixo"
        ok, msg = motor.consultar_uso_cloud("k", url=u)
        self.assertIn("formato", msg)
        ok, msg = motor.consultar_uso_cloud("", url=u)
        self.assertFalse(ok)
        ok, msg = motor.consultar_uso_cloud("k", url="http://127.0.0.1:1/api/usage")
        self.assertFalse(ok)

    def test_preferencias_chave_so_se_lembrar(self):
        with tempfile.TemporaryDirectory() as d:
            base = {"modo_motor": "cloud_api", "modelo_local": "a", "modelo_cloud": "b",
                    "lembrar_chave": False, "chave_api": "segredo"}
            motor.salvar_preferencias(d, base)
            self.assertNotIn("segredo", (Path(d) / "gui_settings.json").read_text())
            self.assertEqual(motor.carregar_preferencias(d)["chave_api"], "")
            motor.salvar_preferencias(d, dict(base, lembrar_chave=True))
            p = motor.carregar_preferencias(d)
            self.assertEqual((p["chave_api"], p["modo_motor"]), ("segredo", "cloud_api"))

    def test_preferencias_arquivo_corrompido_ou_modo_invalido(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(motor.carregar_preferencias(d)["modo_motor"], "local")
            (Path(d) / "gui_settings.json").write_text("{quebrado", encoding="utf-8")
            self.assertEqual(motor.carregar_preferencias(d)["modo_motor"], "local")
            (Path(d) / "gui_settings.json").write_text('{"modo_motor": "xyz", "lembrar_chave": "sim"}', encoding="utf-8")
            p = motor.carregar_preferencias(d)
            self.assertEqual(p["modo_motor"], "local")
            self.assertIs(p["lembrar_chave"], False)


if __name__ == "__main__":
    unittest.main()
