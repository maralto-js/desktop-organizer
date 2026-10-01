from __future__ import annotations

import tkinter as tk
from tkinter import ttk

COR_FUNDO = "#F4F6F8"
COR_CARTAO = "#FFFFFF"
COR_TEXTO = "#1F2937"
COR_TEXTO_FRACO = "#6B7280"
COR_BORDA = "#E2E5E9"
COR_ACENTO = "#2E7D32"
COR_ACENTO_HOVER = "#276A2A"
COR_OK = "#2E7D32"
COR_ERRO = "#C0392B"
COR_AVISO = "#B7791F"

FONTE = "Segoe UI"


def preparar_estilo(raiz: tk.Misc) -> None:
    estilo = ttk.Style(raiz)
    try:
        estilo.theme_use("clam")
    except tk.TclError:
        pass
    base = (FONTE, 10)
    raiz.option_add("*Font", base)

    estilo.configure("TFrame", background=COR_FUNDO)
    estilo.configure("Cartao.TFrame", background=COR_CARTAO, relief="flat")
    estilo.configure("TLabel", background=COR_FUNDO, foreground=COR_TEXTO, font=base)
    estilo.configure("Cartao.TLabel", background=COR_CARTAO, foreground=COR_TEXTO, font=base)
    estilo.configure("Titulo.TLabel", background=COR_FUNDO, foreground=COR_TEXTO, font=(FONTE, 17, "bold"))
    estilo.configure("Subtitulo.TLabel", background=COR_FUNDO, foreground=COR_TEXTO_FRACO, font=base)
    estilo.configure("SecaoTitulo.TLabel", background=COR_CARTAO, foreground=COR_TEXTO, font=(FONTE, 11, "bold"))
    estilo.configure("Fraco.TLabel", background=COR_CARTAO, foreground=COR_TEXTO_FRACO)
    estilo.configure("FracoFundo.TLabel", background=COR_FUNDO, foreground=COR_TEXTO_FRACO)
    estilo.configure("StatusOk.TLabel", background=COR_CARTAO, foreground=COR_OK, font=(FONTE, 10, "bold"))
    estilo.configure("StatusErro.TLabel", background=COR_CARTAO, foreground=COR_ERRO, font=(FONTE, 10, "bold"))
    estilo.configure("StatusAviso.TLabel", background=COR_CARTAO, foreground=COR_AVISO, font=(FONTE, 10, "bold"))

    estilo.configure("Acento.TButton", font=(FONTE, 11, "bold"), foreground="white",
                     background=COR_ACENTO, padding=(14, 10), borderwidth=0)
    estilo.map("Acento.TButton", background=[("active", COR_ACENTO_HOVER), ("disabled", "#9CA3AF")])
    estilo.configure("Normal.TButton", font=base, padding=(10, 6))

    estilo.configure("TNotebook", background=COR_FUNDO, borderwidth=0, tabmargins=(0, 6, 0, 0))
    estilo.configure("TNotebook.Tab", padding=(14, 8), font=(FONTE, 10, "bold"),
                     background=COR_BORDA, foreground=COR_TEXTO_FRACO)
    estilo.map("TNotebook.Tab", background=[("selected", COR_CARTAO)], foreground=[("selected", COR_TEXTO)])

    estilo.configure("Treeview", background=COR_CARTAO, fieldbackground=COR_CARTAO, foreground=COR_TEXTO,
                     rowheight=24, borderwidth=0, font=base)
    estilo.configure("Treeview.Heading", background=COR_BORDA, foreground=COR_TEXTO, font=(FONTE, 10, "bold"),
                     relief="flat", padding=(6, 4))
    estilo.map("Treeview", background=[("selected", "#DCEFDD")], foreground=[("selected", COR_TEXTO)])
    estilo.configure("TCombobox", padding=4)
    estilo.configure("Horizontal.TProgressbar", background=COR_ACENTO, troughcolor=COR_BORDA)
    estilo.configure("Vertical.TScrollbar", background=COR_BORDA, troughcolor=COR_FUNDO,
                     arrowsize=14, width=14, borderwidth=0)
    estilo.map("Vertical.TScrollbar", background=[("active", COR_TEXTO_FRACO)])


def cartao(pai: tk.Misc, titulo: str | None = None) -> tuple[ttk.Frame, ttk.Frame]:
    externo = ttk.Frame(pai, style="TFrame")
    borda = tk.Frame(externo, bg=COR_BORDA)
    borda.pack(fill="both", expand=True)
    interno = ttk.Frame(borda, style="Cartao.TFrame")
    interno.pack(fill="both", expand=True, padx=1, pady=1)
    conteudo = ttk.Frame(interno, style="Cartao.TFrame")
    conteudo.pack(fill="both", expand=True, padx=16, pady=14)
    if titulo:
        ttk.Label(conteudo, text=titulo, style="SecaoTitulo.TLabel").pack(anchor="w", pady=(0, 10))
    return externo, conteudo


def cor_da_barra(restante_pct: float | None) -> str:
    if restante_pct is None:
        return "#374151"
    if restante_pct > 50:
        return "#4CAF50"
    if restante_pct > 20:
        return "#F5B301"
    return "#E5484D"


def blocos_cheios(restante_pct: float | None, total: int = 20) -> int:
    if restante_pct is None:
        return 0
    pct = max(0.0, min(100.0, float(restante_pct)))
    cheios = int(round(pct / 100.0 * total))
    if pct > 0:
        cheios = max(1, cheios)
    return cheios


class BarraRetro(tk.Canvas):

    BLOCOS = 20

    def __init__(self, pai: tk.Misc, largura: int = 320, altura: int = 22):
        super().__init__(pai, width=largura, height=altura, bg="#1F2937",
                         highlightthickness=2, highlightbackground="#111827", bd=0)
        self._largura = largura
        self._altura = altura
        self.definir(None)

    def definir(self, restante_pct: float | None) -> None:
        self.delete("all")
        n = self.BLOCOS
        margem, vao = 3, 2
        largura_bloco = (self._largura - 2 * margem - vao * (n - 1)) / n
        cheios = blocos_cheios(restante_pct, n)
        cor = cor_da_barra(restante_pct)
        for i in range(n):
            x0 = margem + i * (largura_bloco + vao)
            self.create_rectangle(x0, margem, x0 + largura_bloco, self._altura - margem,
                                  fill=cor if i < cheios else "#374151", outline="")
