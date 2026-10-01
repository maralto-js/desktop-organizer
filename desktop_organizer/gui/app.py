from __future__ import annotations

import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import __version__
from ..cli import save_plan, setup_logging
from ..config import ConfigError, load_config, resolve_paths
from ..planner import plan_from_json, plan_to_json
from . import fluxo, motor, tema

INDICE_POR_MODO = {"local": 0, "cloud_login": 1, "cloud_api": 2}
CAIXA_ON, CAIXA_OFF, CAIXA_PARCIAL, SEM_CAIXA = "☑", "☐", "▣", "—"


class App(tk.Tk):
    def __init__(self, config_path: str | None = None) -> None:
        super().__init__()
        self.title("Organizador de Desktop com IA")
        self.configure(bg=tema.COR_FUNDO)
        tema.preparar_estilo(self)

        try:
            self.cfg = load_config(config_path)
            self.target_padrao, self.data_dir = resolve_paths(self.cfg)
        except ConfigError as e:
            messagebox.showerror("Erro de configuração", str(e))
            raise SystemExit(1)
        setup_logging(self.data_dir, False)
        prefs = motor.carregar_preferencias(self.data_dir)

        self.modo_motor = tk.StringVar(value=prefs["modo_motor"])
        self.modelo_local = tk.StringVar(value=prefs["modelo_local"] or self.cfg["ollama"]["model_local"])
        self.modelo_cloud = tk.StringVar(value=prefs["modelo_cloud"])
        self.chave_api = tk.StringVar(value=prefs["chave_api"])
        self.lembrar_chave = tk.BooleanVar(value=prefs["lembrar_chave"])
        self.status_local = tk.StringVar(value="Verificando...")
        self.status_cloud = tk.StringVar(value="Ainda não verificado. Clique em 'Entrar' ou 'Verificar conexão'.")
        self.status_modelos_api = tk.StringVar(
            value="A lista é só de exemplo. Clique em 'Buscar lista ao vivo' para ver o catálogo real da sua conta.")
        self.cota_texto_sessao = tk.StringVar(value="")
        self.cota_texto_semana = tk.StringVar(value="")
        self.cota_msg = tk.StringVar(value="")
        self._cota_consultando = False
        self.pasta = tk.StringVar(value=str(self.target_padrao))
        self.reanalisar = tk.BooleanVar(value=False)
        self.somente_simular = tk.BooleanVar(value=bool(self.cfg["dry_run"]))
        self.organizar_atalhos = tk.BooleanVar(value=bool(self.cfg["scan"]["move_shortcuts"]))
        self.aviso_privacidade = tk.StringVar(value="")
        self.status = tk.StringVar(value="Escolha o motor de IA e a pasta para começar.")
        self.plano = None
        self.marcas: dict[str, bool] = {}
        self._entradas: dict[str, object] = {}
        self._grupos: dict[str, list[str]] = {}
        self.resumo_plano = tk.StringVar(value="Nenhum plano ainda. Clique em 'Analisar'.")
        self.contagem = tk.StringVar(value="")
        self.avisos_plano = tk.StringVar(value="")
        self.resultado = tk.StringVar(value="")
        self._ocupado = False
        self._cancelar = threading.Event()

        self._montar_ui()
        self._ajustar_janela()
        self._atualizar_privacidade()
        self._atualizar_botoes()
        self.after(200, self._atualizar_status_local)
        self.after(1500, self._ciclo_cota)

    def _ajustar_janela(self) -> None:
        self.update_idletasks()
        w = min(920, self.winfo_screenwidth() - 60)
        h = min(860, self.winfo_screenheight() - 80)
        self.geometry(f"{w}x{h}")
        self.minsize(min(w, 780), min(h, 620))

    def _em_thread(self, trabalho, aplicar) -> None:
        def alvo():
            try:
                resultado = trabalho()
            except Exception as e:
                resultado = (False, f"Erro inesperado: {type(e).__name__}: {e}")
            try:
                self.after(0, lambda: aplicar(resultado))
            except (tk.TclError, RuntimeError):
                pass
        threading.Thread(target=alvo, daemon=True).start()

    def _link(self, pai, texto: str, url: str) -> None:
        l = ttk.Label(pai, text=texto, style="Fraco.TLabel", cursor="hand2", foreground=tema.COR_ACENTO)
        l.pack(anchor="w")
        l.bind("<Button-1>", lambda e: webbrowser.open(url))

    def _local_url(self) -> str:
        return self.cfg["ollama"]["local_url"]

    def _montar_ui(self) -> None:
        canvas = tk.Canvas(self, bg=tema.COR_FUNDO, highlightthickness=0)
        rolagem = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=rolagem.set)
        canvas.pack(side="left", fill="both", expand=True)
        rolagem.pack(side="right", fill="y")
        pagina = ttk.Frame(canvas, style="TFrame")
        janela_id = canvas.create_window((0, 0), window=pagina, anchor="nw")
        pagina.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(janela_id, width=e.width))

        def rolar(evento):
            try:
                if evento.widget.winfo_toplevel() is not self or isinstance(evento.widget, ttk.Treeview):
                    return
            except (AttributeError, KeyError, tk.TclError):
                return
            if getattr(evento, "num", None) == 4:
                canvas.yview_scroll(-1, "units")
            elif getattr(evento, "num", None) == 5:
                canvas.yview_scroll(1, "units")
            else:
                canvas.yview_scroll(int(-1 * (evento.delta / 120)), "units")

        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind_all(seq, rolar)

        corpo = ttk.Frame(pagina, style="TFrame", padding=(18, 16))
        corpo.pack(fill="both", expand=True)

        cab = ttk.Frame(corpo, style="TFrame")
        cab.pack(fill="x", pady=(0, 14))
        titulos = ttk.Frame(cab, style="TFrame")
        titulos.pack(side="left")
        ttk.Label(titulos, text="🗂  Organizador de Desktop", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(titulos, style="Subtitulo.TLabel",
                  text=f"A IA propõe, você confere e confirma. Nada é apagado e tudo pode ser desfeito.  (v{__version__})"
                  ).pack(anchor="w", pady=(2, 0))
        ttk.Button(cab, text="🕘  Histórico / Desfazer", style="Normal.TButton",
                   command=self._abrir_historico).pack(side="right")

        self._montar_cartao_motor(corpo)
        self._montar_cartao_pasta(corpo)
        self._montar_cartao_plano(corpo)
        self._montar_cartao_executar(corpo)

    def _montar_cartao_motor(self, corpo) -> None:
        externo, c = tema.cartao(corpo, "1) Motor de IA")
        externo.pack(fill="x", pady=(0, 12))
        abas = ttk.Notebook(c)
        abas.pack(fill="x")
        aba_local = ttk.Frame(abas, style="Cartao.TFrame", padding=14)
        aba_login = ttk.Frame(abas, style="Cartao.TFrame", padding=14)
        aba_api = ttk.Frame(abas, style="Cartao.TFrame", padding=14)
        abas.add(aba_local, text="💻  Ollama local")
        abas.add(aba_login, text="☁️  Nuvem: entrar com conta")
        abas.add(aba_api, text="🔑  Nuvem: chave de API")
        self._abas = abas
        abas.select(INDICE_POR_MODO.get(self.modo_motor.get(), 0))
        self._montar_aba_local(aba_local)
        self._montar_aba_login(aba_login)
        self._montar_aba_api(aba_api)
        abas.bind("<<NotebookTabChanged>>", self._ao_trocar_aba)

        quadro = ttk.Frame(c, style="Cartao.TFrame")
        quadro.pack(fill="x", pady=(14, 0))
        ttk.Label(quadro, text="☁ Cota da nuvem (o que ainda resta)", style="Fraco.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        ttk.Label(quadro, text="Sessão (~5 h)", style="Fraco.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8))
        self.barra_cota_sessao = tema.BarraRetro(quadro)
        self.barra_cota_sessao.grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(quadro, textvariable=self.cota_texto_sessao, style="Fraco.TLabel").grid(
            row=1, column=2, sticky="w", padx=8)
        ttk.Label(quadro, text="Semana (~7 dias)", style="Fraco.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 8))
        self.barra_cota_semana = tema.BarraRetro(quadro)
        self.barra_cota_semana.grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(quadro, textvariable=self.cota_texto_semana, style="Fraco.TLabel").grid(
            row=2, column=2, sticky="w", padx=8)
        ttk.Button(quadro, text="🔄 Atualizar", style="Normal.TButton", command=self._atualizar_cota).grid(
            row=1, column=3, rowspan=2, padx=(8, 0))
        ttk.Label(quadro, textvariable=self.cota_msg, style="Fraco.TLabel", wraplength=740, justify="left").grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self._mostrar_cota_indisponivel(self._texto_cota_sem_dados())

    def _montar_aba_local(self, pai) -> None:
        linha = ttk.Frame(pai, style="Cartao.TFrame")
        linha.pack(fill="x")
        self.rotulo_local = ttk.Label(linha, textvariable=self.status_local, style="Fraco.TLabel")
        self.rotulo_local.pack(side="left")
        ttk.Button(linha, text="🔄 Atualizar", style="Normal.TButton",
                   command=self._atualizar_status_local).pack(side="right")
        ttk.Label(pai, text="Modelo instalado a usar:", style="Cartao.TLabel").pack(anchor="w", pady=(14, 4))
        self.combo_local = ttk.Combobox(pai, textvariable=self.modelo_local, state="readonly",
                                        values=[self.modelo_local.get()])
        self.combo_local.pack(fill="x")
        self.combo_local.bind("<<ComboboxSelected>>", lambda e: self._salvar())
        ttk.Label(pai, style="Fraco.TLabel", wraplength=740, justify="left",
                  text="Não tem o Ollama instalado ainda? Baixe grátis em ollama.com/download, "
                       "instale e clique em 'Atualizar' aqui.").pack(anchor="w", pady=(14, 0))
        self._link(pai, "Abrir ollama.com/download", "https://ollama.com/download")

    def _atualizar_status_local(self) -> None:
        self.status_local.set("Verificando se o Ollama está rodando...")
        self.rotulo_local.configure(style="Fraco.TLabel")
        self._em_thread(lambda: motor.listar_modelos_locais(self._local_url()), self._aplicar_status_local)

    def _aplicar_status_local(self, resultado) -> None:
        ok, dados = resultado
        if not ok:
            self.status_local.set("● Ollama não detectado neste computador.")
            self.rotulo_local.configure(style="StatusErro.TLabel")
            return
        if not dados:
            self.status_local.set("● Ollama rodando, mas sem modelo baixado. No terminal: ollama pull llama3.2")
            self.rotulo_local.configure(style="StatusAviso.TLabel")
            return
        self.status_local.set(f"● Ollama rodando: {len(dados)} modelo(s) instalado(s).")
        self.rotulo_local.configure(style="StatusOk.TLabel")
        valores = [f"{m['nome']}  ({m['tamanho']})" for m in dados]
        nomes = [m["nome"] for m in dados]
        self.combo_local["values"] = valores
        atual = motor.separar_nome_modelo(self.modelo_local.get())
        escolhido = atual if atual in nomes else next((n for n in nomes if n.split(":")[0] == atual), nomes[0])
        self.modelo_local.set(valores[nomes.index(escolhido)])
        self._salvar()

    def modelo_local_puro(self) -> str:
        return motor.separar_nome_modelo(self.modelo_local.get())

    def _montar_aba_login(self, pai) -> None:
        ttk.Label(pai, style="Cartao.TLabel", wraplength=740, justify="left",
                  text="Conecta sua conta gratuita da ollama.com. Depois de entrar uma vez, os modelos de "
                       "nuvem (nomes terminados em '-cloud') funcionam direto, sem guardar chave aqui."
                  ).pack(anchor="w", pady=(0, 12))
        botoes = ttk.Frame(pai, style="Cartao.TFrame")
        botoes.pack(fill="x")
        self.botao_signin = ttk.Button(botoes, text="🔐 Entrar com minha conta Ollama",
                                       style="Normal.TButton", command=self._conectar_cloud)
        self.botao_signin.pack(side="left")
        ttk.Button(botoes, text="✓ Verificar conexão", style="Normal.TButton",
                   command=self._verificar_cloud).pack(side="left", padx=(8, 0))
        self.botao_signout = ttk.Button(botoes, text="🔓 Trocar de conta / Sair",
                                        style="Normal.TButton", command=self._trocar_conta)
        self.botao_signout.pack(side="left", padx=(8, 0))
        self.rotulo_cloud = ttk.Label(pai, textvariable=self.status_cloud, style="Fraco.TLabel",
                                      wraplength=740, justify="left")
        self.rotulo_cloud.pack(anchor="w", pady=(10, 14))
        ttk.Label(pai, text="Modelo de nuvem a usar:", style="Cartao.TLabel").pack(anchor="w", pady=(0, 4))
        combo = ttk.Combobox(pai, textvariable=self.modelo_cloud, values=motor.MODELOS_CLOUD_EXEMPLO)
        combo.pack(fill="x")
        combo.bind("<FocusOut>", lambda e: self._salvar())
        combo.bind("<<ComboboxSelected>>", lambda e: self._salvar())
        ttk.Label(pai, style="Fraco.TLabel", wraplength=740, justify="left",
                  text="Os exemplos vêm da documentação e podem mudar: a Ollama aposenta e lança modelos com "
                       "frequência. Este modo não consegue buscar a lista ao vivo. Para o catálogo atualizado, "
                       "abra o link abaixo ou use a aba 'Nuvem: chave de API'."
                  ).pack(anchor="w", pady=(6, 0))
        self._link(pai, "Abrir catálogo de modelos cloud em ollama.com", "https://ollama.com/search?c=cloud")

    def _cloud_status(self, texto: str, estilo: str) -> None:
        self.status_cloud.set(texto)
        self.rotulo_cloud.configure(style=estilo)

    def _modelo_cloud_puro(self) -> str:
        return self.modelo_cloud.get().strip() or motor.MODELOS_CLOUD_EXEMPLO[0]

    def _conectar_cloud(self) -> None:
        self.botao_signin.configure(state="disabled")
        self._cloud_status("Rodando 'ollama signin'... uma aba do navegador deve abrir pedindo login. "
                           "Pode levar alguns segundos.", "Fraco.TLabel")

        def aplicar(res):
            ok, saida, url = res if len(res) == 3 else (res[0], res[1], None)
            self.botao_signin.configure(state="normal")
            curta = saida if len(saida) < 400 else saida[:400] + "..."
            if url:
                webbrowser.open(url)
                self._cloud_status(f"Abri este link no seu navegador; conclua o login lá:\n{url}\n\n"
                                   "Depois de autorizar, clique em 'Verificar conexão' (é isso que confirma).",
                                   "StatusAviso.TLabel")
            elif ok:
                self._cloud_status("O comando terminou (a conta pode já estar conectada). Saída:\n"
                                   f"{curta}\n\nIsso ainda não é garantia: clique em 'Verificar conexão'.",
                                   "StatusAviso.TLabel")
            else:
                self._cloud_status(curta, "StatusErro.TLabel")

        self._em_thread(motor.rodar_signin, aplicar)

    def _trocar_conta(self) -> None:
        if not messagebox.askyesno(
                "Trocar de conta",
                "Isso vai desconectar a conta Ollama atual desta instalação ('ollama signout'). "
                "Depois, clique em 'Entrar com minha conta Ollama' para entrar com outro e-mail.\n\nContinuar?"):
            return
        self.botao_signout.configure(state="disabled")
        self._cloud_status("Saindo da conta atual...", "Fraco.TLabel")

        def aplicar(res):
            ok, saida = res
            self.botao_signout.configure(state="normal")
            curta = saida if len(saida) < 300 else saida[:300] + "..."
            if ok:
                self._cloud_status("Conta desconectada. Clique em 'Entrar com minha conta Ollama' para entrar "
                                   f"com outro e-mail.\n\nSaída do comando: {curta}", "StatusAviso.TLabel")
            else:
                self._cloud_status(curta, "StatusErro.TLabel")

        self._em_thread(motor.rodar_signout, aplicar)

    def _verificar_cloud(self) -> None:
        self._cloud_status("Verificando conexão com a conta Ollama Cloud...", "Fraco.TLabel")
        modelo = self._modelo_cloud_puro()

        def aplicar(res):
            ok, msg = res
            self._cloud_status(("● Conectado! " if ok else "● Não conectado. ") + msg,
                               "StatusOk.TLabel" if ok else "StatusErro.TLabel")

        self._em_thread(lambda: motor.verificar_login_cloud(self._local_url(), modelo), aplicar)

    def _montar_aba_api(self, pai) -> None:
        ttk.Label(pai, style="Cartao.TLabel", wraplength=740, justify="left",
                  text="Modo avançado: usa uma chave de API pessoal para falar direto com a ollama.com, sem "
                       "depender do login local. Tem cota de uso. A chave também alimenta a barra de cota."
                  ).pack(anchor="w", pady=(0, 12))
        ttk.Label(pai, text="Chave de API:", style="Cartao.TLabel").pack(anchor="w")
        entrada = ttk.Entry(pai, textvariable=self.chave_api, show="*")
        entrada.pack(fill="x", pady=(4, 0))
        entrada.bind("<FocusOut>", lambda e: self._salvar())
        ttk.Checkbutton(pai, text="Lembrar a chave neste computador (fica salva em texto no arquivo de "
                                  "preferências; desmarcado, vale só até fechar)",
                        variable=self.lembrar_chave, command=self._salvar).pack(anchor="w", pady=(6, 0))
        self._link(pai, "Criar/ver minhas chaves em ollama.com/settings/keys", "https://ollama.com/settings/keys")
        linha = ttk.Frame(pai, style="Cartao.TFrame")
        linha.pack(fill="x", pady=(14, 0))
        ttk.Label(linha, text="Modelo de nuvem a usar:", style="Cartao.TLabel").pack(side="left")
        self.botao_lista = ttk.Button(linha, text="🔄 Buscar lista ao vivo da ollama.com",
                                      style="Normal.TButton", command=self._buscar_modelos_api)
        self.botao_lista.pack(side="right")
        self.combo_api = ttk.Combobox(pai, textvariable=self.modelo_cloud, values=motor.MODELOS_CLOUD_EXEMPLO)
        self.combo_api.pack(fill="x", pady=(4, 0))
        self.combo_api.bind("<FocusOut>", lambda e: self._salvar())
        self.combo_api.bind("<<ComboboxSelected>>", lambda e: self._salvar())
        self.rotulo_api = ttk.Label(pai, textvariable=self.status_modelos_api, style="Fraco.TLabel",
                                    wraplength=740, justify="left")
        self.rotulo_api.pack(anchor="w", pady=(6, 0))

    def _buscar_modelos_api(self) -> None:
        self._salvar()
        chave = self.chave_api.get()
        cloud_url = self.cfg["ollama"]["cloud_url"]
        self.botao_lista.configure(state="disabled")
        self.status_modelos_api.set("Buscando lista de modelos na ollama.com...")
        self.rotulo_api.configure(style="Fraco.TLabel")

        def aplicar(res):
            ok, dados = res
            self.botao_lista.configure(state="normal")
            if ok:
                self.combo_api["values"] = dados
                self.status_modelos_api.set(f"✓ {len(dados)} modelo(s) na sua conta ollama.com (lista ao vivo).")
                self.rotulo_api.configure(style="StatusOk.TLabel")
            else:
                self.status_modelos_api.set(f"Não consegui buscar a lista: {dados}")
                self.rotulo_api.configure(style="StatusErro.TLabel")

        self._em_thread(lambda: motor.listar_modelos_cloud_api(chave, cloud_url), aplicar)

    def _texto_cota_sem_dados(self) -> str:
        modo = self.modo_motor.get()
        if modo == "local":
            return "Ollama local não tem cota: é grátis e sem limite. A barra só funciona no modo Nuvem."
        if not self.chave_api.get().strip():
            if modo == "cloud_login":
                return ("Consultar a cota exige uma chave de API (o login por conta não fornece uma). Crie uma em "
                        "ollama.com/settings/keys e cole na aba 'Nuvem: chave de API'; ela serve só para esta barra.")
            return "Preencha a chave de API para ver a cota."
        return ""

    def _mostrar_cota_indisponivel(self, mensagem: str) -> None:
        self.barra_cota_sessao.definir(None)
        self.barra_cota_semana.definir(None)
        self.cota_texto_sessao.set("—")
        self.cota_texto_semana.set("—")
        self.cota_msg.set(mensagem)

    def _atualizar_cota(self) -> None:
        if self._cota_consultando:
            return
        sem_dados = self._texto_cota_sem_dados()
        if sem_dados:
            self._mostrar_cota_indisponivel(sem_dados)
            return
        chave = self.chave_api.get().strip()
        self._cota_consultando = True

        def aplicar(res):
            self._cota_consultando = False
            ok, dados = res
            if not ok:
                self._mostrar_cota_indisponivel(str(dados))
                return
            resta_s, resta_w = 100.0 - dados["sessao"], 100.0 - dados["semana"]
            self.barra_cota_sessao.definir(resta_s)
            self.barra_cota_semana.definir(resta_w)
            self.cota_texto_sessao.set(f"{resta_s:.0f}% restante")
            self.cota_texto_semana.set(f"{resta_w:.0f}% restante")
            bs, bw = dados["bruto"]
            self.cota_msg.set(f"Atualizado às {time.strftime('%H:%M:%S')} · valor bruto da API: sessão {bs:g}, "
                              f"semana {bw:g} (se não bater com ollama.com/settings, avise para ajustar a unidade).")

        self._em_thread(lambda: motor.consultar_uso_cloud(chave), aplicar)

    def _ciclo_cota(self) -> None:
        try:
            if self.modo_motor.get() != "local" and self.chave_api.get().strip():
                self._atualizar_cota()
        finally:
            self.after(60000, self._ciclo_cota)

    def _ao_trocar_aba(self, _evento=None) -> None:
        indice = self._abas.index(self._abas.select())
        self.modo_motor.set({v: k for k, v in INDICE_POR_MODO.items()}.get(indice, "local"))
        self._salvar()
        self._atualizar_privacidade()
        self._atualizar_cota()

    def modelo_ativo(self) -> str:
        return self.modelo_local_puro() if self.modo_motor.get() == "local" else self._modelo_cloud_puro()

    def _salvar(self) -> None:
        try:
            motor.salvar_preferencias(self.data_dir, {
                "modo_motor": self.modo_motor.get(),
                "modelo_local": self.modelo_local_puro(),
                "modelo_cloud": self._modelo_cloud_puro(),
                "lembrar_chave": bool(self.lembrar_chave.get()),
                "chave_api": self.chave_api.get(),
            })
        except OSError as e:
            self.status.set(f"Aviso: não consegui salvar as preferências ({e}).")
        self._atualizar_privacidade()

    def _atualizar_privacidade(self) -> None:
        if self.modo_motor.get() == "local" and not self.modelo_ativo().endswith(("-cloud", ":cloud")):
            self.aviso_privacidade.set("🔒 Local: nada sai do seu computador.")
        elif self.cfg["ollama"]["cloud_send_content"]:
            self.aviso_privacidade.set("☁ Nuvem: os nomes e um trecho inicial dos arquivos de texto são enviados à "
                                       "ollama.com (mude 'cloud_send_content' no config.json para enviar só nomes).")
        else:
            self.aviso_privacidade.set("☁ Nuvem: só nomes e metadados são enviados à ollama.com "
                                       "(cloud_send_content desligado).")

    def _montar_cartao_pasta(self, corpo) -> None:
        externo, c = tema.cartao(corpo, "2) Pasta a organizar")
        externo.pack(fill="x", pady=(0, 12))
        linha = ttk.Frame(c, style="Cartao.TFrame")
        linha.pack(fill="x")
        ttk.Entry(linha, textvariable=self.pasta, state="readonly").pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.botao_pasta = ttk.Button(linha, text="Escolher pasta...", style="Normal.TButton",
                                      command=self._escolher_pasta)
        self.botao_pasta.pack(side="left")
        ttk.Checkbutton(c, text="Reanalisar tudo (ignorar classificações guardadas no cache)",
                        variable=self.reanalisar).pack(anchor="w", pady=(8, 0))
        ttk.Checkbutton(c, text="Organizar atalhos de programas e jogos (.lnk, .url) por tipo: Jogos, "
                                "Navegadores, Comunicação...", variable=self.organizar_atalhos
                        ).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(c, text="Só simular (não move nada; mostra o que seria feito)",
                        variable=self.somente_simular).pack(anchor="w", pady=(4, 0))
        ttk.Label(c, textvariable=self.aviso_privacidade, style="Fraco.TLabel", wraplength=740,
                  justify="left").pack(anchor="w", pady=(8, 0))

        botoes = ttk.Frame(c, style="Cartao.TFrame")
        botoes.pack(fill="x", pady=(12, 0))
        self.botao_analisar = ttk.Button(botoes, text="🔍  Analisar", style="Acento.TButton", command=self._analisar)
        self.botao_analisar.pack(side="left")
        self.botao_cancelar = ttk.Button(botoes, text="Cancelar", style="Normal.TButton",
                                         command=self._cancelar_analise, state="disabled")
        self.botao_cancelar.pack(side="left", padx=(8, 0))
        self.botao_abrir_plano = ttk.Button(botoes, text="📂 Abrir plano salvo...", style="Normal.TButton",
                                            command=self._abrir_plano)
        self.botao_abrir_plano.pack(side="left", padx=(8, 0))

        self.barra = ttk.Progressbar(c, mode="determinate", style="Horizontal.TProgressbar")
        self.barra.pack(fill="x", pady=(12, 6))
        ttk.Label(c, textvariable=self.status, style="Fraco.TLabel", wraplength=740, justify="left").pack(anchor="w")

    def _escolher_pasta(self) -> None:
        d = filedialog.askdirectory(title="Escolha a pasta a organizar", initialdir=self.pasta.get() or None)
        if d:
            self.pasta.set(d)
            self._limpar_plano("Pasta alterada. Clique em 'Analisar'.")

    def _definir_ocupado(self, ocupado: bool) -> None:
        self._ocupado = ocupado
        self._atualizar_botoes()

    def _atualizar_botoes(self) -> None:
        livre = not self._ocupado
        self.botao_analisar.configure(state="normal" if livre else "disabled")
        self.botao_pasta.configure(state="normal" if livre else "disabled")
        self.botao_abrir_plano.configure(state="normal" if livre else "disabled")
        pode = livre and self.plano is not None
        self.botao_executar.configure(state="normal" if pode else "disabled")

    def _cliente_atual(self):
        modo, modelo = self.modo_motor.get(), self.modelo_ativo()
        if modo == "local" and not modelo:
            raise ValueError("Escolha um modelo instalado na aba 'Ollama local' (ou clique em Atualizar).")
        if modo == "cloud_api" and not self.chave_api.get().strip():
            raise ValueError("Preencha a chave de API na aba 'Nuvem: chave de API' (ou use 'entrar com conta').")
        return motor.construir_cliente(self.cfg, modo, modelo, self.chave_api.get())

    def _analisar(self) -> None:
        if self._ocupado:
            return
        alvo = Path(self.pasta.get())
        if not alvo.is_dir():
            messagebox.showerror("Pasta não encontrada", f"A pasta não existe:\n{alvo}")
            return
        try:
            cliente, mode, modelo = self._cliente_atual()
        except ValueError as e:
            messagebox.showwarning("Motor de IA", str(e))
            return
        self._limpar_plano("Analisando...")
        self._cancelar.clear()
        self._definir_ocupado(True)
        self.botao_cancelar.configure(state="normal")
        self.barra.configure(mode="indeterminate")
        self.barra.start(12)
        usar_cache = not self.reanalisar.get()
        self.cfg["scan"]["move_shortcuts"] = bool(self.organizar_atalhos.get())

        def dizer(msg: str) -> None:
            try:
                self.after(0, lambda: self.status.set(msg))
            except (tk.TclError, RuntimeError):
                pass

        def trabalho():
            try:
                plano, stats = fluxo.analisar(self.cfg, cliente, mode, modelo, alvo, self.data_dir,
                                              usar_cache=usar_cache, progress=dizer, cancelar=self._cancelar)
                arquivo = save_plan(plano, self.data_dir)
                return "ok", plano, stats, arquivo
            except fluxo.Cancelado:
                return "cancelado",
            except Exception as e:
                return "erro", f"{type(e).__name__}: {e}"

        self._em_thread(trabalho, self._fim_analise)

    def _cancelar_analise(self) -> None:
        self._cancelar.set()
        self.status.set("Cancelando... (o que já foi classificado fica guardado no cache)")

    def _fim_analise(self, res) -> None:
        self.barra.stop()
        self.barra.configure(mode="determinate", value=0)
        self.botao_cancelar.configure(state="disabled")
        self._definir_ocupado(False)
        tipo = res[0] if res and res[0] in ("ok", "cancelado", "erro") else "erro"
        if tipo == "ok":
            _, plano, stats, arquivo = res
            self._mostrar_plano(plano)
            self.status.set(f"Classificação: {fluxo.texto_estatisticas(stats)}.  Plano salvo em: {arquivo}")
        elif tipo == "cancelado":
            self.status.set("Análise cancelada. Nenhum arquivo foi alterado.")
        else:
            msg = res[1] if len(res) > 1 else "falha desconhecida"
            self.status.set(f"Erro na análise: {msg}")
            messagebox.showerror("Erro na análise", str(msg))

    def _abrir_plano(self) -> None:
        caminho = filedialog.askopenfilename(title="Abrir plano salvo", filetypes=[("Plano JSON", "*.json")],
                                             initialdir=str(Path(self.data_dir) / "plans"))
        if not caminho:
            return
        try:
            plano = plan_from_json(Path(caminho).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as e:
            messagebox.showerror("Plano inválido", str(e))
            return
        if Path(plano.target) != Path(self.pasta.get()):
            messagebox.showerror("Pasta diferente", f"O plano é para:\n{plano.target}\n\nmas a pasta atual é:\n"
                                 f"{self.pasta.get()}\n\nEscolha a mesma pasta e tente de novo.")
            return
        self._mostrar_plano(plano)
        self.status.set(f"Plano carregado de {caminho}. Confira antes de executar: os arquivos podem ter mudado.")

    def _montar_cartao_plano(self, corpo) -> None:
        externo, c = tema.cartao(corpo, "3) Confira o plano")
        externo.pack(fill="x", pady=(0, 12))
        ttk.Label(c, textvariable=self.resumo_plano, style="Cartao.TLabel", wraplength=740,
                  justify="left").pack(anchor="w")
        ttk.Label(c, style="Fraco.TLabel", wraplength=740, justify="left",
                  text="Clique na caixinha (ou dê duplo clique na linha) para marcar/desmarcar. Só o que estiver "
                       "marcado será movido. Itens protegidos vêm desmarcados; marque só se tiver certeza."
                  ).pack(anchor="w", pady=(4, 8))

        barra = ttk.Frame(c, style="Cartao.TFrame")
        barra.pack(fill="x", pady=(0, 6))
        ttk.Button(barra, text="Marcar todos", style="Normal.TButton",
                   command=lambda: self._marcar_todos(True)).pack(side="left")
        ttk.Button(barra, text="Desmarcar todos", style="Normal.TButton",
                   command=lambda: self._marcar_todos(False)).pack(side="left", padx=(6, 0))
        ttk.Button(barra, text="Expandir", style="Normal.TButton",
                   command=lambda: self._expandir(True)).pack(side="left", padx=(6, 0))
        ttk.Button(barra, text="Recolher", style="Normal.TButton",
                   command=lambda: self._expandir(False)).pack(side="left", padx=(6, 0))
        ttk.Button(barra, text="💾 Salvar plano...", style="Normal.TButton",
                   command=self._salvar_plano).pack(side="right")

        quadro = ttk.Frame(c, style="Cartao.TFrame")
        quadro.pack(fill="x")
        self.tabela = ttk.Treeview(quadro, columns=("sel", "conf", "destino", "desc"), show="tree headings",
                                   height=14, selectmode="browse")
        self.tabela.heading("#0", text="Nome")
        self.tabela.heading("sel", text="✓")
        self.tabela.heading("conf", text="Confiança")
        self.tabela.heading("destino", text="Destino")
        self.tabela.heading("desc", text="O que é")
        self.tabela.column("#0", width=250, minwidth=140)
        self.tabela.column("sel", width=44, minwidth=44, anchor="center", stretch=False)
        self.tabela.column("conf", width=80, minwidth=60, anchor="center", stretch=False)
        self.tabela.column("destino", width=170, minwidth=100)
        self.tabela.column("desc", width=260, minwidth=120)
        self.tabela.tag_configure("grupo", font=(tema.FONTE, 10, "bold"))
        self.tabela.tag_configure("fraco", foreground=tema.COR_TEXTO_FRACO)
        self.tabela.tag_configure("revisar", foreground=tema.COR_AVISO)
        self.tabela.tag_configure("protegido", foreground=tema.COR_ERRO)
        rol = ttk.Scrollbar(quadro, orient="vertical", command=self.tabela.yview)
        self.tabela.configure(yscrollcommand=rol.set)
        self.tabela.pack(side="left", fill="x", expand=True)
        rol.pack(side="right", fill="y")
        self.tabela.bind("<Button-1>", self._ao_clicar_tabela)
        self.tabela.bind("<Double-1>", self._ao_duplo_clique_tabela)
        self.tabela.bind("<space>", self._ao_espaco_tabela)

        ttk.Label(c, textvariable=self.contagem, style="Cartao.TLabel").pack(anchor="w", pady=(8, 0))
        ttk.Label(c, textvariable=self.avisos_plano, style="StatusAviso.TLabel", wraplength=740,
                  justify="left").pack(anchor="w", pady=(4, 0))

    def _limpar_plano(self, mensagem: str) -> None:
        self.plano = None
        self.marcas = {}
        self._entradas = {}
        self._grupos = {}
        self.tabela.delete(*self.tabela.get_children())
        self.resumo_plano.set("Nenhum plano ainda. Clique em 'Analisar'.")
        self.contagem.set("")
        self.avisos_plano.set("")
        self.resultado.set("")
        self.status.set(mensagem)
        self._atualizar_botoes()

    def _mostrar_plano(self, plano) -> None:
        self.tabela.delete(*self.tabela.get_children())
        self._entradas, self._grupos = {}, {}
        self.plano = plano
        self.marcas = fluxo.marcacao_inicial(plano)
        limiar = self.cfg["classification"]["confidence_threshold"]

        for i, (destino, ents) in enumerate(fluxo.agrupar(plano)):
            gid = f"g:{i}"
            self._grupos[gid] = []
            self.tabela.insert("", "end", iid=gid, open=True, tags=("grupo",),
                               text=f"📁 {destino}/   ({len(ents)})", values=(CAIXA_ON, "", "", ""))
            for e in ents:
                iid = f"e:{e.id}"
                self._entradas[iid] = e
                self._grupos[gid].append(iid)
                tags = ("revisar",) if (e.action == "revisar" or e.confidence < limiar) else ()
                origem = " (heurística)" if e.source == "heuristica" else ""
                self.tabela.insert(gid, "end", iid=iid, tags=tags,
                                   text=f"{e.name}{'/' if e.is_dir else ''}",
                                   values=(CAIXA_ON, f"{e.confidence:.2f}{origem}", e.dest_dir,
                                           e.description or e.reason))
        manter = fluxo.ficam(plano)
        if manter:
            gid = "g:ficam"
            self._grupos[gid] = []
            self.tabela.insert("", "end", iid=gid, open=False, tags=("grupo",),
                               text=f"🔒 Ficam onde estão   ({len(manter)})", values=(CAIXA_OFF, "", "", ""))
            for e in manter:
                iid = f"e:{e.id}"
                self._entradas[iid] = e
                marcavel = e.action == "protegido" and bool(e.dest_dir)
                if marcavel:
                    self._grupos[gid].append(iid)
                tipo = "PROTEGIDO" if e.action == "protegido" else "mantido"
                self.tabela.insert(gid, "end", iid=iid, tags=("protegido",) if e.action == "protegido" else ("fraco",),
                                   text=f"{e.name}{'/' if e.is_dir else ''}",
                                   values=(CAIXA_OFF if marcavel else SEM_CAIXA, f"{e.confidence:.2f}",
                                           e.dest_dir if marcavel else "", f"{tipo}: {e.note or e.reason}"))
            if not self._grupos[gid]:
                self.tabela.item(gid, values=(SEM_CAIXA, "", "", ""))
        if plano.duplicates:
            self.tabela.insert("", "end", iid="g:dup", open=False, tags=("grupo",),
                               text=f"👯 Possíveis duplicatas   ({len(plano.duplicates)})", values=(SEM_CAIXA, "", "", ""))
            for k, d in enumerate(plano.duplicates):
                self.tabela.insert("g:dup", "end", iid=f"d:{k}", tags=("fraco",), text=" == ".join(d["names"]),
                                   values=(SEM_CAIXA, "", "", f"{d['size']} bytes, sha256 {d['sha256'][:12]}… (nada será apagado)"))
        if plano.skipped:
            self.tabela.insert("", "end", iid="g:ign", open=False, tags=("grupo",),
                               text=f"⏭ Ignorados na varredura   ({len(plano.skipped)})", values=(SEM_CAIXA, "", "", ""))
            for k, s in enumerate(plano.skipped[:100]):
                self.tabela.insert("g:ign", "end", iid=f"i:{k}", tags=("fraco",), text=s["name"],
                                   values=(SEM_CAIXA, "", "", s["reason"]))
        self.resumo_plano.set(f"Alvo: {plano.target}   |   modelo: {plano.model}   |   {fluxo.resumo(plano)}")
        self.avisos_plano.set("\n".join("AVISO: " + w for w in plano.warnings))
        self.resultado.set("")
        for gid in self._grupos:
            self._atualizar_caixa_grupo(gid)
        self._atualizar_contagem()
        self._atualizar_botoes()

    def _glifo(self, iid: str) -> str:
        return CAIXA_ON if self.marcas.get(self._entradas[iid].id, False) else CAIXA_OFF

    def _atualizar_caixa_grupo(self, gid: str) -> None:
        filhos = self._grupos.get(gid, [])
        if not filhos:
            return
        estados = [self.marcas.get(self._entradas[i].id, False) for i in filhos]
        g = CAIXA_ON if all(estados) else (CAIXA_OFF if not any(estados) else CAIXA_PARCIAL)
        self.tabela.set(gid, "sel", g)

    def _alternar(self, iid: str) -> None:
        if iid in self._grupos:
            filhos = self._grupos[iid]
            if not filhos:
                return
            novo = not all(self.marcas.get(self._entradas[i].id, False) for i in filhos)
            for f in filhos:
                self.marcas[self._entradas[f].id] = novo
                self.tabela.set(f, "sel", CAIXA_ON if novo else CAIXA_OFF)
            self._atualizar_caixa_grupo(iid)
        elif iid in self._entradas and self._entradas[iid].id in self.marcas:
            e = self._entradas[iid]
            self.marcas[e.id] = not self.marcas[e.id]
            self.tabela.set(iid, "sel", self._glifo(iid))
            pai = self.tabela.parent(iid)
            if pai:
                self._atualizar_caixa_grupo(pai)
        else:
            return
        self._atualizar_contagem()

    def _marcar_todos(self, marcar: bool) -> None:
        for gid, filhos in self._grupos.items():
            if not filhos:
                continue
            for f in filhos:
                if marcar and self._entradas[f].action == "protegido":
                    continue
                self.marcas[self._entradas[f].id] = marcar
                self.tabela.set(f, "sel", CAIXA_ON if marcar else CAIXA_OFF)
            self._atualizar_caixa_grupo(gid)
        self._atualizar_contagem()

    def _expandir(self, abrir: bool) -> None:
        for gid in self.tabela.get_children(""):
            self.tabela.item(gid, open=abrir)

    def _atualizar_contagem(self) -> None:
        if self.plano is None:
            self.contagem.set("")
            return
        mov, prot = fluxo.contar_selecionados(self.plano, self.marcas)
        extra = f"  (+ {prot} protegido(s) que você marcou)" if prot else ""
        self.contagem.set(f"Marcados para mover: {mov} item(ns){extra}")

    def _ao_clicar_tabela(self, evento) -> None:
        if self.tabela.identify_region(evento.x, evento.y) != "cell" or self.tabela.identify_column(evento.x) != "#1":
            return
        iid = self.tabela.identify_row(evento.y)
        if iid:
            self._alternar(iid)

    def _ao_duplo_clique_tabela(self, evento) -> None:
        if self.tabela.identify_region(evento.x, evento.y) != "cell":
            return
        if self.tabela.identify_column(evento.x) == "#1":
            return
        iid = self.tabela.identify_row(evento.y)
        if iid:
            self._alternar(iid)

    def _ao_espaco_tabela(self, _evento) -> None:
        sel = self.tabela.selection()
        if sel:
            self._alternar(sel[0])

    def _salvar_plano(self) -> None:
        if self.plano is None:
            messagebox.showinfo("Salvar plano", "Analise uma pasta primeiro.")
            return
        caminho = filedialog.asksaveasfilename(title="Salvar plano", defaultextension=".json",
                                               filetypes=[("Plano JSON", "*.json")])
        if not caminho:
            return
        filtrado, _ = fluxo.aplicar_selecao(self.plano, self.marcas)
        try:
            Path(caminho).write_text(plan_to_json(filtrado), encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Não consegui salvar", str(e))
            return
        self.status.set(f"Plano (com a sua conferência) salvo em {caminho}")

    def _montar_cartao_executar(self, corpo) -> None:
        externo, c = tema.cartao(corpo, "4) Executar")
        externo.pack(fill="x")
        self.barra_exec = ttk.Progressbar(c, mode="determinate", style="Horizontal.TProgressbar")
        self.barra_exec.pack(fill="x", pady=(2, 8))
        ttk.Label(c, textvariable=self.resultado, style="Fraco.TLabel", wraplength=740, justify="left").pack(anchor="w")
        self.botao_executar = ttk.Button(c, text="▶  Executar organização", style="Acento.TButton",
                                         command=self._executar, state="disabled")
        self.botao_executar.pack(pady=(10, 2), fill="x")
        ttk.Label(c, style="Fraco.TLabel", wraplength=740, justify="left",
                  text="Nada é apagado: os arquivos só são movidos, tudo fica registrado e dá para desfazer "
                       "em 'Histórico / Desfazer'.").pack(anchor="w", pady=(6, 0))

    def _executar(self) -> None:
        if self._ocupado or self.plano is None:
            return
        mov, prot = fluxo.contar_selecionados(self.plano, self.marcas)
        if mov + prot == 0:
            messagebox.showinfo("Nada a mover", "Não há nenhum item marcado.")
            return
        simular = bool(self.somente_simular.get())
        if not simular:
            if not messagebox.askyesno("Confirmar", f"Mover {mov + prot} item(ns) dentro de:\n{self.plano.target}\n\n"
                                       "Nada será apagado e você poderá desfazer depois. Continuar?"):
                self.status.set("Cancelado. Nenhum arquivo foi alterado.")
                return
            if prot:
                nomes = [e.name for e in self.plano.entries
                         if e.action == "protegido" and self.marcas.get(e.id, False)][:10]
                if not messagebox.askyesno("Itens PROTEGIDOS",
                                           "Você marcou itens que o programa considera protegidos:\n\n  • "
                                           + "\n  • ".join(nomes) + "\n\nMover mesmo assim?"):
                    self.status.set("Cancelado. Nenhum arquivo foi alterado.")
                    return
        plano, marcas = self.plano, dict(self.marcas)
        self._definir_ocupado(True)
        self.barra_exec.configure(maximum=max(mov + prot, 1), value=0)
        self.resultado.set("[SIMULAÇÃO] Verificando os movimentos..." if simular else "Movendo arquivos...")

        def progresso(feitos: int, total: int, nome: str) -> None:
            try:
                self.after(0, lambda: (self.barra_exec.configure(value=feitos),
                                       self.resultado.set(f"{feitos}/{total}: {nome}")))
            except (tk.TclError, RuntimeError):
                pass

        def trabalho():
            try:
                return "ok", fluxo.executar(plano, self.data_dir, marcas, dry_run=simular, progress=progresso), simular
            except Exception as e:
                return "erro", f"{type(e).__name__}: {e}"

        self._em_thread(trabalho, self._fim_execucao)

    def _fim_execucao(self, res) -> None:
        self._definir_ocupado(False)
        if not res or res[0] != "ok":
            msg = res[1] if res and len(res) > 1 else "falha desconhecida"
            self.resultado.set(f"Erro na execução: {msg}")
            messagebox.showerror("Erro na execução", str(msg))
            return
        _, r, simulou = res
        self.barra_exec.configure(value=self.barra_exec["maximum"])
        detalhes = [f"   ! {n}: {e}" for n, e in (r["failed"] + r["skipped"])[:12]]
        if simulou:
            self.resultado.set(f"[SIMULAÇÃO] Nada foi movido. {len(r['moved'])} movimentações seriam feitas; "
                               f"{len(r['skipped'])} seriam ignoradas.\n" + "\n".join(detalhes))
            self.status.set("Simulação concluída. Desmarque 'Só simular' para executar de verdade.")
            return
        self.resultado.set(f"Concluído ({r['status']}): {len(r['moved'])} movidos, {len(r['failed'])} falhas, "
                           f"{len(r['skipped'])} ignorados. Para desfazer: 'Histórico / Desfazer'.\n" + "\n".join(detalhes))
        self.status.set("Organização concluída. Analise de novo para ver o que sobrou.")
        resultado = self.resultado.get()
        self._limpar_plano(self.status.get())
        self.resultado.set(resultado)

    def _abrir_historico(self) -> None:
        janela = tk.Toplevel(self)
        janela.title("Histórico e desfazer")
        janela.configure(bg=tema.COR_FUNDO)
        janela.geometry("860x520")
        janela.transient(self)
        corpo = ttk.Frame(janela, style="TFrame", padding=(16, 14))
        corpo.pack(fill="both", expand=True)
        ttk.Label(corpo, text="🕘 Histórico de organizações", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(corpo, style="Subtitulo.TLabel",
                  text="Cada execução fica registrada. Desfazer devolve os arquivos aos lugares originais."
                  ).pack(anchor="w", pady=(2, 10))

        tabela = ttk.Treeview(corpo, columns=("data", "movidos", "falhas", "status", "estado"),
                              show="headings", height=9, selectmode="browse")
        for col, texto, larg in (("data", "Quando", 190), ("movidos", "Movidos", 80), ("falhas", "Falhas", 70),
                                 ("status", "Status", 120), ("estado", "Situação", 120)):
            tabela.heading(col, text=texto)
            tabela.column(col, width=larg, anchor="w" if col == "data" else "center")
        tabela.pack(fill="x")
        saida = tk.Text(corpo, height=8, wrap="word", bg=tema.COR_CARTAO, fg=tema.COR_TEXTO, relief="flat",
                        highlightthickness=1, highlightbackground=tema.COR_BORDA, state="disabled")
        estado = {"busy": False}

        def escrever(texto: str) -> None:
            saida.configure(state="normal")
            saida.delete("1.0", "end")
            saida.insert("1.0", texto)
            saida.configure(state="disabled")

        def recarregar() -> None:
            tabela.delete(*tabela.get_children())
            runs = fluxo.historico(self.data_dir)
            for r in runs:
                tabela.insert("", "end", iid=r["run_id"], values=(
                    r["ts"].replace("T", " "), r["moved"], r["failed"], r["status"],
                    "desfeita" if r["undone"] else "ativa"))
            escrever("Nenhuma execução registrada." if not runs else "Selecione uma execução e use os botões abaixo.")

        def selecionada():
            sel = tabela.selection()
            if not sel:
                messagebox.showinfo("Desfazer", "Clique numa execução da lista primeiro.", parent=janela)
                return None
            run = next((r for r in fluxo.historico(self.data_dir) if r["run_id"] == sel[0]), None)
            if run and run["undone"]:
                messagebox.showinfo("Desfazer", "Essa execução já foi desfeita.", parent=janela)
                return None
            return run

        def desfazer(simular: bool) -> None:
            run = selecionada()
            if not run or estado["busy"]:
                return
            if not simular and not messagebox.askyesno(
                    "Desfazer", f"Restaurar os {run['moved']} arquivo(s) da execução {run['run_id']} "
                                "às posições originais?", parent=janela):
                return
            estado["busy"] = True
            escrever("Trabalhando...")

            def aplicar(res):
                estado["busy"] = False
                if isinstance(res, tuple):
                    escrever(str(res[1]))
                    return
                linhas = [f"{'[SIMULAÇÃO] ' if simular else ''}Restaurados: {len(res.get('restored', []))}   "
                          f"Falhas: {len(res.get('failed', []))}"]
                if res.get("error"):
                    linhas.append(res["error"])
                linhas += [f"- {n}" for n in res.get("notes", [])]
                linhas += [f"! {n}: {e}" for n, e in res.get("failed", [])]
                escrever("\n".join(linhas))
                if not simular:
                    recarregar_mantendo(linhas)

            self._em_thread(lambda: fluxo.desfazer(self.data_dir, run["run_id"], dry_run=simular), aplicar)

        def recarregar_mantendo(linhas) -> None:
            recarregar()
            escrever("\n".join(linhas))

        botoes = ttk.Frame(corpo, style="TFrame")
        botoes.pack(fill="x", pady=8)
        ttk.Button(botoes, text="🔄 Atualizar", style="Normal.TButton", command=recarregar).pack(side="left")
        ttk.Button(botoes, text="Simular desfazer", style="Normal.TButton",
                   command=lambda: desfazer(True)).pack(side="left", padx=(8, 0))
        ttk.Button(botoes, text="↩  Desfazer selecionada", style="Acento.TButton",
                   command=lambda: desfazer(False)).pack(side="left", padx=(8, 0))
        ttk.Button(botoes, text="Fechar", style="Normal.TButton", command=janela.destroy).pack(side="right")
        saida.pack(fill="both", expand=True)
        recarregar()


def main(config_path: str | None = None) -> int:
    App(config_path).mainloop()
    return 0
