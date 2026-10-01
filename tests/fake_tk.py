from __future__ import annotations

import sys
import types


class TclError(Exception):
    pass


class _Var:
    def __init__(self, master=None, value=None, **k):
        self._v = value

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


class StringVar(_Var):
    def __init__(self, master=None, value="", **k):
        super().__init__(master, value)


class BooleanVar(_Var):
    def __init__(self, master=None, value=False, **k):
        super().__init__(master, value)


class IntVar(_Var):
    def __init__(self, master=None, value=0, **k):
        super().__init__(master, value)


class Widget:
    def __init__(self, master=None, **opts):
        self.master = master
        self.opts = dict(opts)
        self.bindings = {}
        self.children_w = []
        self.destroyed = False
        if master is not None and hasattr(master, "children_w"):
            master.children_w.append(self)

    def configure(self, cnf=None, **k):
        if isinstance(cnf, dict):
            k = {**cnf, **k}
        self.opts.update(k)

    config = configure

    def cget(self, key):
        return self.opts.get(key)

    def __getitem__(self, key):
        return self.opts.get(key)

    def __setitem__(self, key, value):
        self.opts[key] = value

    def pack(self, *a, **k): pass
    def grid(self, *a, **k): pass
    def place(self, *a, **k): pass
    def pack_forget(self): pass
    def bind(self, seq, fn=None, add=None): self.bindings[seq] = fn
    def bind_all(self, seq, fn=None, add=None): self.bindings[seq] = fn
    def columnconfigure(self, *a, **k): pass
    def rowconfigure(self, *a, **k): pass
    def focus_set(self): pass
    def destroy(self): self.destroyed = True
    def winfo_toplevel(self): return self
    def winfo_screenwidth(self): return 1920
    def winfo_screenheight(self): return 1080
    def update_idletasks(self): pass
    def update(self): pass
    def start(self, *a): self.opts["_rodando"] = True
    def stop(self): self.opts["_rodando"] = False
    def select(self, *a): return self.opts.get("_sel", 0) if not a else self.opts.__setitem__("_sel", a[0])
    def index(self, x): return x if isinstance(x, int) else self.opts.get("_sel", 0)
    def add(self, *a, **k): pass
    def yview(self, *a): pass
    def yview_scroll(self, *a): pass
    def set(self, *a, **k): pass
    def create_window(self, *a, **k): return 1
    def create_rectangle(self, *a, **k): return 1
    def delete(self, *a): pass
    def bbox(self, *a): return (0, 0, 0, 0)
    def itemconfig(self, *a, **k): pass
    def heading(self, *a, **k): pass
    def column(self, *a, **k): pass
    def tag_configure(self, *a, **k): pass
    def insert(self, *a, **k): pass
    def theme_use(self, *a): pass
    def map(self, *a, **k): pass
    def option_add(self, *a): pass
    def title(self, *a): pass
    def geometry(self, *a): pass
    def minsize(self, *a): pass
    def transient(self, *a): pass
    def mainloop(self): pass


class Tk(Widget):
    def __init__(self):
        super().__init__(None)
        self.agendado = []

    def after(self, ms, fn=None, *args):
        if fn is None:
            return None
        if ms == 0:
            fn(*args)
        else:
            self.agendado.append((ms, fn))
        return "after"


class Toplevel(Widget):
    def after(self, ms, fn=None, *a):
        if ms == 0 and fn:
            fn(*a)


class Canvas(Widget): pass
class Frame(Widget): pass
class Text(Widget):
    def __init__(self, master=None, **o):
        super().__init__(master, **o)
        self.texto = ""

    def delete(self, *a): self.texto = ""
    def insert(self, idx, txt, *a): self.texto += txt


class Style(Widget):
    def configure(self, *a, **k): pass


class Treeview(Widget):

    def __init__(self, master=None, **o):
        super().__init__(master, **o)
        self.cols = list(o.get("columns", ()))
        self.itens = {}
        self.ordem = {"": []}
        self._sel = ()

    def insert(self, parent, index, iid=None, text="", values=(), tags=(), open=False, **k):
        iid = iid or f"I{len(self.itens)}"
        self.itens[iid] = {"text": text, "values": list(values), "tags": tags, "open": open, "parent": parent}
        self.ordem.setdefault(parent, []).append(iid)
        self.ordem.setdefault(iid, [])
        return iid

    def delete(self, *iids):
        for iid in iids:
            self._apagar(iid)

    def _apagar(self, iid):
        for f in list(self.ordem.get(iid, [])):
            self._apagar(f)
        it = self.itens.pop(iid, None)
        if it is not None:
            self.ordem[it["parent"]].remove(iid)
        self.ordem.pop(iid, None)

    def get_children(self, item=""):
        return tuple(self.ordem.get(item, []))

    def parent(self, iid):
        return self.itens[iid]["parent"]

    def item(self, iid, option=None, **k):
        if k:
            self.itens[iid].update({a: b for a, b in k.items() if a in ("text", "values", "tags", "open")})
            return None
        return self.itens[iid][option] if option else dict(self.itens[iid])

    def set(self, iid, column, value=None):
        i = self.cols.index(column)
        if value is None:
            return self.itens[iid]["values"][i]
        self.itens[iid]["values"][i] = value

    def selection(self):
        return self._sel

    def selection_set(self, iid):
        self._sel = (iid,)

    def identify_region(self, x, y): return "cell"
    def identify_column(self, x): return "#1"
    def identify_row(self, y): return ""


class _Msg:
    def __init__(self):
        self.respostas_sim_nao = True
        self.chamadas = []

    def _reg(self, nome, *a, **k):
        self.chamadas.append((nome, a))

    def showinfo(self, *a, **k): self._reg("info", *a)
    def showwarning(self, *a, **k): self._reg("warning", *a)
    def showerror(self, *a, **k): self._reg("error", *a)

    def askyesno(self, *a, **k):
        self._reg("askyesno", *a)
        return self.respostas_sim_nao


class _Files:
    def __init__(self):
        self.dir = ""
        self.open = ""
        self.save = ""

    def askdirectory(self, **k): return self.dir
    def askopenfilename(self, **k): return self.open
    def asksaveasfilename(self, **k): return self.save


_SALVOS = {}


def install():
    for nome in ("tkinter", "tkinter.ttk", "tkinter.messagebox", "tkinter.filedialog"):
        _SALVOS[nome] = sys.modules.get(nome)
    tk = types.ModuleType("tkinter")
    ttk = types.ModuleType("tkinter.ttk")
    msg, files = _Msg(), _Files()
    for n in ("Tk", "Toplevel", "Canvas", "Frame", "Text", "StringVar", "BooleanVar", "IntVar", "TclError", "Widget"):
        setattr(tk, n, globals()[n])
    for n in ("Frame", "Label", "Button", "Entry", "Checkbutton", "Notebook", "Combobox", "Progressbar",
              "Scrollbar", "Spinbox"):
        setattr(ttk, n, type(n, (Widget,), {}))
    ttk.Treeview = Treeview
    ttk.Style = Style
    tk.ttk, tk.messagebox, tk.filedialog = ttk, msg, files
    tk.Misc = Widget
    sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk, "tkinter.messagebox": msg, "tkinter.filedialog": files})
    return msg, files


def uninstall():
    for nome, antigo in _SALVOS.items():
        if antigo is None:
            sys.modules.pop(nome, None)
        else:
            sys.modules[nome] = antigo
    _SALVOS.clear()
    for nome in [n for n in sys.modules if n.startswith("desktop_organizer.gui")]:
        sys.modules.pop(nome, None)
