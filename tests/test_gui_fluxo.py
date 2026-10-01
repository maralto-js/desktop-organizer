import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organizer import Base, PNG, files_only, snapshot

from desktop_organizer.gui import fluxo, motor


class FluxoTests(Base):
    def setUp(self):
        super().setUp()
        self.write("foto.png", PNG)
        self.write("codigo.py", "print('oi')")
        self.write("coisa.txt", "algo qualquer")
        self.write("setup.exe", b"MZ" + b"\0" * 32)
        self.data = self.tmp / "data"

    def analisar(self, srv, **kw):
        cliente, mode, modelo = motor.construir_cliente(self.cfg | {"ollama": {**self.cfg["ollama"], "local_url": srv.url}},
                                                         "local", "llama3.2")
        return fluxo.analisar(self.cfg, cliente, mode, modelo, self.desk, self.data, **kw)

    def test_analisar_gera_plano_e_estatisticas(self):
        srv = self.server()
        antes = snapshot(self.desk)
        plano, stats = self.analisar(srv)
        self.assertEqual(snapshot(self.desk), antes)
        acoes = {e.name: e.action for e in plano.entries}
        self.assertEqual(acoes["foto.png"], "mover")
        self.assertEqual(acoes["setup.exe"], "protegido")
        self.assertEqual(acoes["coisa.txt"], "revisar")
        self.assertGreaterEqual(stats["ia"], 3)
        self.assertIn("pela IA", fluxo.texto_estatisticas(stats))
        self.assertIn("a mover", fluxo.resumo(plano))

    def test_agrupar_e_marcacao_inicial(self):
        plano, _ = self.analisar(self.server())
        grupos = fluxo.agrupar(plano)
        destinos = [d for d, _ in grupos]
        self.assertEqual(destinos, sorted(destinos, key=lambda d: (d.startswith("_"), d.lower())))
        self.assertTrue(destinos[-1].startswith("_"))
        marcas = fluxo.marcacao_inicial(plano)
        por_nome = {e.name: e for e in plano.entries}
        self.assertTrue(marcas[por_nome["foto.png"].id])
        self.assertFalse(marcas.get(por_nome["setup.exe"].id, False))
        self.assertEqual(fluxo.contar_selecionados(plano, marcas)[1], 0)

    def test_cancelar_interrompe_sem_alterar_nada(self):
        srv = self.server()
        antes = snapshot(self.desk)
        ev = threading.Event()
        ev.set()
        with self.assertRaises(fluxo.Cancelado):
            self.analisar(srv, cancelar=ev)
        self.assertEqual(snapshot(self.desk), antes)

    def test_servidor_fora_usa_heuristica_e_avisa(self):
        cliente, mode, modelo = motor.construir_cliente(self.cfg, "local", "x")
        cliente.base = "http://127.0.0.1:1"
        cliente.retries = 0
        plano, stats = fluxo.analisar(self.cfg, cliente, mode, modelo, self.desk, self.data)
        self.assertTrue(any("indisponível" in w for w in plano.warnings))
        self.assertEqual(stats["ia"], 0)

    def test_desmarcado_fica_no_lugar_e_protegido_marcado_move(self):
        plano, _ = self.analisar(self.server())
        por_nome = {e.name: e for e in plano.entries}
        marcas = fluxo.marcacao_inicial(plano)
        marcas[por_nome["codigo.py"].id] = False
        marcas[por_nome["setup.exe"].id] = True
        if not por_nome["setup.exe"].dest_dir:
            por_nome["setup.exe"].dest_dir = "Programas"
        filtrado, aprovados = fluxo.aplicar_selecao(plano, marcas)
        acoes = {e.name: e.action for e in filtrado.entries}
        self.assertEqual(acoes["codigo.py"], "manter")
        self.assertIn("desmarcado por você", {e.name: e.note for e in filtrado.entries}["codigo.py"])
        self.assertEqual(aprovados, {por_nome["setup.exe"].id})
        self.assertEqual(plano.entries[[e.name for e in plano.entries].index("codigo.py")].action, "mover")

    def test_executar_historico_e_desfazer(self):
        plano, _ = self.analisar(self.server())
        antes_arquivos = files_only(self.desk)
        por_nome = {e.name: e for e in plano.entries}
        marcas = fluxo.marcacao_inicial(plano)
        marcas[por_nome["codigo.py"].id] = False
        passos = []
        res = fluxo.executar(plano, self.data, marcas, progress=lambda f, t, n: passos.append((f, t, n)))
        self.assertEqual(res["status"], "completed")
        self.assertTrue((self.desk / "codigo.py").exists())
        self.assertFalse((self.desk / "foto.png").exists())
        self.assertTrue((self.desk / "setup.exe").exists())
        self.assertEqual(passos[-1][0], passos[-1][1])
        self.assertEqual(files_only(self.desk), antes_arquivos)

        hist = fluxo.historico(self.data)
        self.assertEqual(len(hist), 1)
        self.assertFalse(hist[0]["undone"])
        sim = fluxo.desfazer(self.data, hist[0]["run_id"], dry_run=True)
        self.assertTrue(sim["restored"])
        self.assertFalse((self.desk / "foto.png").exists())
        real = fluxo.desfazer(self.data, hist[0]["run_id"])
        self.assertFalse(real["failed"])
        self.assertTrue((self.desk / "foto.png").exists())
        self.assertTrue(fluxo.historico(self.data)[0]["undone"])

    def test_executar_simulacao_nao_move(self):
        plano, _ = self.analisar(self.server())
        antes = snapshot(self.desk)
        res = fluxo.executar(plano, self.data, fluxo.marcacao_inicial(plano), dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertTrue(res["moved"])
        self.assertEqual(snapshot(self.desk), antes)
        self.assertEqual(fluxo.historico(self.data), [])

    def test_tudo_desmarcado_nao_move_nada(self):
        plano, _ = self.analisar(self.server())
        marcas = {k: False for k in fluxo.marcacao_inicial(plano)}
        antes = snapshot(self.desk)
        res = fluxo.executar(plano, self.data, marcas)
        self.assertEqual((res["moved"], res["total_planejado"]), ([], 0))
        self.assertEqual(snapshot(self.desk), antes)


if __name__ == "__main__":
    unittest.main()
