import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import fake_tk
from mock_ollama import MockOllama
from test_organizer import PNG

MSG = FILES = None
appmod = None


def setUpModule():
    global MSG, FILES, appmod
    MSG, FILES = fake_tk.install()
    from desktop_organizer.gui import app as _app
    appmod = _app


def tearDownModule():
    fake_tk.uninstall()


class SyncThread:

    def __init__(self, target=None, daemon=None, **k):
        self.target = target

    def start(self):
        self.target()


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.desk = self.tmp / "Desktop"
        self.desk.mkdir()
        (self.desk / "foto.png").write_bytes(PNG)
        (self.desk / "codigo.py").write_text("print(1)")
        (self.desk / "coisa.txt").write_text("nada claro")
        self.srv = MockOllama("ok")
        cfgp = self.tmp / "config.json"
        cfgp.write_text(json.dumps({
            "target_dir": str(self.desk), "data_dir": str(self.tmp / "data"),
            "ollama": {"local_url": self.srv.url, "retries": 0, "timeout_seconds": 5},
            "scan": {"recently_modified_seconds": 0}}), encoding="utf-8")
        MSG.chamadas.clear()
        MSG.respostas_sim_nao = True
        self.patch = mock.patch.object(appmod, "threading",
                                       types.SimpleNamespace(Thread=SyncThread, Event=threading.Event))
        self.patch.start()
        self.app = appmod.App(str(cfgp))
        self.data = self.tmp / "data"

    def tearDown(self):
        self.patch.stop()
        self.srv.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_abre_com_pasta_e_estado_inicial(self):
        a = self.app
        self.assertEqual(a.pasta.get(), str(self.desk))
        self.assertEqual(a.botao_executar["state"], "disabled")
        self.assertEqual(a.botao_analisar["state"], "normal")
        self.assertIn("Local", a.aviso_privacidade.get())
        self.assertEqual(a.cota_texto_sessao.get(), "—")
        self.assertIn("sem limite", a.cota_msg.get())

    def test_status_local_lista_modelos_e_salva_preferencia(self):
        a = self.app
        a._atualizar_status_local()
        self.assertIn("2 modelo(s)", a.status_local.get())
        self.assertEqual(a.rotulo_local["style"], "StatusOk.TLabel")
        self.assertEqual(len(a.combo_local["values"]), 2)
        self.assertTrue(a.modelo_local_puro())
        prefs = json.loads((self.data / "gui_settings.json").read_text())
        self.assertEqual(prefs["modelo_local"], a.modelo_local_puro())

    def test_servidor_local_fora_mostra_erro(self):
        self.app.cfg["ollama"]["local_url"] = "http://127.0.0.1:1"
        self.app._atualizar_status_local()
        self.assertIn("não detectado", self.app.status_local.get())
        self.assertEqual(self.app.rotulo_local["style"], "StatusErro.TLabel")

    def test_fluxo_completo_analisar_conferir_executar_desfazer(self):
        a = self.app
        a._atualizar_status_local()
        a._analisar()
        self.assertIsNotNone(a.plano)
        self.assertIn("Classificação:", a.status.get())
        self.assertEqual(a.botao_executar["state"], "normal")
        self.assertEqual(a.botao_analisar["state"], "normal")
        self.assertEqual(a.barra["mode"], "determinate")
        self.assertTrue((self.desk / "foto.png").exists())
        self.assertTrue(list((self.data / "plans").glob("plan-*.json")))

        grupos = [i for i in a.tabela.get_children("") if i.startswith("g:")]
        self.assertTrue(grupos)
        linhas = {a.tabela.item(i, "text").rstrip("/"): i for i in a._entradas}
        self.assertIn("foto.png", linhas)
        self.assertEqual(a.tabela.set(linhas["foto.png"], "sel"), "☑")
        self.assertIn("Marcados para mover:", a.contagem.get())

        alvo = linhas["codigo.py"]
        antes = a.contagem.get()
        a._alternar(alvo)
        self.assertEqual(a.tabela.set(alvo, "sel"), "☐")
        self.assertNotEqual(a.contagem.get(), antes)
        a._alternar(alvo)
        self.assertEqual(a.tabela.set(alvo, "sel"), "☑")
        a._alternar(alvo)

        a._executar()
        self.assertTrue(any(c[0] == "askyesno" for c in MSG.chamadas))
        self.assertTrue((self.desk / "codigo.py").exists())
        self.assertFalse((self.desk / "foto.png").exists())
        self.assertIn("Concluído", a.resultado.get())
        self.assertIsNone(a.plano)
        self.assertEqual(a.botao_executar["state"], "disabled")
        self.assertEqual(len(appmod.fluxo.historico(self.data)), 1)

        run = appmod.fluxo.historico(self.data)[0]
        appmod.fluxo.desfazer(self.data, run["run_id"])
        self.assertTrue((self.desk / "foto.png").exists())

    def test_executar_recusado_nao_move_nada(self):
        a = self.app
        a._atualizar_status_local()
        a._analisar()
        MSG.respostas_sim_nao = False
        a._executar()
        self.assertTrue((self.desk / "foto.png").exists())
        self.assertIsNotNone(a.plano)
        self.assertIn("Nenhum arquivo foi alterado", a.status.get())

    def test_simular_nao_pede_confirmacao_nem_move(self):
        a = self.app
        a._atualizar_status_local()
        a._analisar()
        a.somente_simular.set(True)
        MSG.chamadas.clear()
        a._executar()
        self.assertFalse(any(c[0] == "askyesno" for c in MSG.chamadas))
        self.assertTrue((self.desk / "foto.png").exists())
        self.assertIn("SIMULAÇÃO", a.resultado.get())
        self.assertIsNotNone(a.plano)

    def test_grupo_marca_e_desmarca_todos_os_filhos(self):
        a = self.app
        a._atualizar_status_local()
        a._analisar()
        gid = next(g for g, f in a._grupos.items() if len(f) >= 1 and g != "g:ficam")
        a._alternar(gid)
        self.assertTrue(all(a.tabela.set(f, "sel") == "☐" for f in a._grupos[gid]))
        self.assertEqual(a.tabela.set(gid, "sel"), "☐")
        a._alternar(gid)
        self.assertEqual(a.tabela.set(gid, "sel"), "☑")
        a._marcar_todos(False)
        self.assertIn("0 item", a.contagem.get())

    def test_nada_marcado_avisa_e_nao_executa(self):
        a = self.app
        a._atualizar_status_local()
        a._analisar()
        a._marcar_todos(False)
        MSG.chamadas.clear()
        a._executar()
        self.assertEqual(MSG.chamadas[0][0], "info")
        self.assertTrue((self.desk / "foto.png").exists())

    def test_pasta_inexistente_e_modelo_vazio(self):
        a = self.app
        a.pasta.set(str(self.tmp / "nao_existe"))
        a._analisar()
        self.assertEqual(MSG.chamadas[-1][0], "error")
        a.pasta.set(str(self.desk))
        a.modelo_local.set("")
        a._analisar()
        self.assertEqual(MSG.chamadas[-1][0], "warning")
        self.assertIsNone(a.plano)

    def test_cloud_api_sem_chave_avisa(self):
        a = self.app
        a._abas.opts["_sel"] = 2
        a._ao_trocar_aba()
        self.assertEqual(a.modo_motor.get(), "cloud_api")
        self.assertIn("Nuvem", a.aviso_privacidade.get())
        a._analisar()
        self.assertEqual(MSG.chamadas[-1][0], "warning")

    def test_cota_com_chave_atualiza_barras(self):
        a = self.app
        a._abas.opts["_sel"] = 2
        a._ao_trocar_aba()
        a.chave_api.set("k")
        with mock.patch.object(appmod.motor, "consultar_uso_cloud",
                               return_value=(True, {"sessao": 25.0, "semana": 60.0, "bruto": (0.25, 60.0)})):
            a._atualizar_cota()
        self.assertEqual(a.cota_texto_sessao.get(), "75% restante")
        self.assertEqual(a.cota_texto_semana.get(), "40% restante")
        with mock.patch.object(appmod.motor, "consultar_uso_cloud", return_value=(False, "HTTP 404")):
            a._atualizar_cota()
        self.assertEqual(a.cota_texto_sessao.get(), "—")
        self.assertEqual(a.cota_msg.get(), "HTTP 404")

    def test_cota_login_sem_chave_explica(self):
        a = self.app
        a._abas.opts["_sel"] = 1
        a._ao_trocar_aba()
        self.assertIn("exige uma chave de API", a.cota_msg.get())

    def test_trocar_conta_pede_confirmacao_e_chama_signout(self):
        a = self.app
        MSG.respostas_sim_nao = False
        with mock.patch.object(appmod.motor, "rodar_signout") as so:
            a._trocar_conta()
            so.assert_not_called()
            MSG.respostas_sim_nao = True
            so.return_value = (True, "signed out")
            a._trocar_conta()
            so.assert_called_once()
        self.assertIn("Conta desconectada", a.status_cloud.get())

    def test_signin_com_link_abre_navegador(self):
        a = self.app
        with mock.patch.object(appmod.motor, "rodar_signin",
                               return_value=(False, "abra o link", "https://ollama.com/connect?x=1")), \
                mock.patch.object(appmod.webbrowser, "open") as abrir:
            a._conectar_cloud()
        abrir.assert_called_once_with("https://ollama.com/connect?x=1")
        self.assertIn("conclua o login", a.status_cloud.get())
        self.assertEqual(a.botao_signin["state"], "normal")

    def test_verificar_cloud_ok_e_falha(self):
        a = self.app
        with mock.patch.object(appmod.motor, "verificar_login_cloud", return_value=(True, "ok!")):
            a._verificar_cloud()
        self.assertTrue(a.status_cloud.get().startswith("● Conectado!"))
        with mock.patch.object(appmod.motor, "verificar_login_cloud", return_value=(False, "sign in")):
            a._verificar_cloud()
        self.assertTrue(a.status_cloud.get().startswith("● Não conectado."))

    def test_buscar_modelos_api(self):
        a = self.app
        a.chave_api.set("k")
        with mock.patch.object(appmod.motor, "listar_modelos_cloud_api", return_value=(True, ["a", "b"])):
            a._buscar_modelos_api()
        self.assertEqual(a.combo_api["values"], ["a", "b"])
        self.assertIn("2 modelo(s)", a.status_modelos_api.get())
        self.assertEqual(a.botao_lista["state"], "normal")
        with mock.patch.object(appmod.motor, "listar_modelos_cloud_api", return_value=(False, "chave inválida")):
            a._buscar_modelos_api()
        self.assertIn("chave inválida", a.status_modelos_api.get())

    def test_abrir_plano_de_outra_pasta_e_salvar_plano(self):
        a = self.app
        a._atualizar_status_local()
        a._analisar()
        salvo = self.tmp / "meu_plano.json"
        FILES.save = str(salvo)
        a._salvar_plano()
        self.assertTrue(salvo.exists())
        a._limpar_plano("x")
        FILES.open = str(salvo)
        a._abrir_plano()
        self.assertIsNotNone(a.plano)
        a._limpar_plano("x")
        a.pasta.set(str(self.tmp))
        MSG.chamadas.clear()
        a._abrir_plano()
        self.assertIsNone(a.plano)
        self.assertEqual(MSG.chamadas[-1][0], "error")

    def test_checkbox_de_atalhos_controla_se_atalhos_sao_organizados(self):
        a = self.app
        (self.desk / "Discord.lnk").write_bytes(b"x")
        a._atualizar_status_local()
        a.organizar_atalhos.set(False)
        a._analisar()
        e = next(e for e in a.plano.entries if e.name == "Discord.lnk")
        self.assertEqual(e.action, "protegido")
        a.organizar_atalhos.set(True)
        a._analisar()
        e = next(e for e in a.plano.entries if e.name == "Discord.lnk")
        self.assertNotEqual(e.action, "protegido")

    def test_historico_abre_sem_erro_e_lista_execucoes(self):
        a = self.app
        a._atualizar_status_local()
        a._analisar()
        a._executar()
        a._abrir_historico()
        janelas = [w for w in a.children_w if isinstance(w, appmod.tk.Toplevel)]
        self.assertTrue(janelas)

    def _achar(self, raiz, tipo, texto=None):
        pilha = [raiz]
        while pilha:
            w = pilha.pop()
            if isinstance(w, tipo) and (texto is None or w.opts.get("text") == texto):
                return w
            pilha.extend(w.children_w)
        raise AssertionError(f"widget não encontrado: {tipo.__name__} {texto!r}")

    def test_historico_botao_desfazer_restaura_arquivos(self):
        a = self.app
        a._atualizar_status_local()
        a._analisar()
        a._executar()
        self.assertFalse((self.desk / "foto.png").exists())
        a._abrir_historico()
        jan = next(w for w in a.children_w if isinstance(w, appmod.tk.Toplevel))
        tabela = self._achar(jan, appmod.ttk.Treeview)
        texto = self._achar(jan, appmod.tk.Text)
        linhas = tabela.get_children("")
        self.assertEqual(len(linhas), 1)
        self.assertEqual(tabela.item(linhas[0], "values")[-1], "ativa")

        MSG.chamadas.clear()
        self._achar(jan, appmod.ttk.Button, "↩  Desfazer selecionada").opts["command"]()
        self.assertEqual(MSG.chamadas[-1][0], "info")
        self.assertFalse((self.desk / "foto.png").exists())

        tabela.selection_set(linhas[0])
        self._achar(jan, appmod.ttk.Button, "Simular desfazer").opts["command"]()
        self.assertIn("SIMULAÇÃO", texto.texto)
        self.assertFalse((self.desk / "foto.png").exists())
        self._achar(jan, appmod.ttk.Button, "↩  Desfazer selecionada").opts["command"]()
        self.assertTrue((self.desk / "foto.png").exists())
        self.assertIn("Restaurados:", texto.texto)
        self.assertEqual(tabela.item(tabela.get_children("")[0], "values")[-1], "desfeita")

        tabela.selection_set(tabela.get_children("")[0])
        MSG.chamadas.clear()
        self._achar(jan, appmod.ttk.Button, "↩  Desfazer selecionada").opts["command"]()
        self.assertEqual(MSG.chamadas[-1][0], "info")


if __name__ == "__main__":
    unittest.main()
