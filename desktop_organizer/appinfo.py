from __future__ import annotations

import mmap
import re
import struct
from pathlib import Path

SHORTCUT_EXT = (".lnk", ".url", ".desktop")
EXECUTABLE_EXT = (".exe", ".msi")
MAX_PE_BYTES = 400 * 1024 * 1024

KNOWLEDGE = [
    ("Jogos", ["steam", "steamapps", "riot", "riot games", "league of legends", "valorant", "epic games",
               "epicgames", "fortnite", "roblox", "minecraft", "hytale", "ea app", "origin", "ubisoft",
               "uplay", "battle.net", "blizzard", "gog galaxy", "easyanticheat", "anticheat", "anti-cheat",
               "gamers club", "rockstar", "xbox", "playstation", "pcsx2", "ppsspp", "dolphin", "retroarch",
               "emulator", "emulador", "boosteroid", "geforce now", "genshin", "hoyoverse", "osu", "gaming",
               "game", "games", "jogo", "jogos", "launcher de jogos", "prism launcher", "curseforge"]),
    ("Navegadores", ["chrome", "brave", "firefox", "msedge", "microsoft edge", "opera", "vivaldi", "browser"]),
    ("Comunicação", ["discord", "whatsapp", "telegram", "slack", "microsoft teams", "zoom", "skype",
                     "signal", "stoat", "revolt", "thunderbird", "outlook"]),
    ("Desenvolvimento", ["visual studio", "vscode", "intellij", "jetbrains", "pycharm", "webstorm", "clion",
                         "android studio", "eclipse", "github", "git", "filezilla", "putty", "winscp",
                         "postman", "docker", "node.js", "nodejs", "python", "ollama", "blockbench",
                         "sublime", "notepad++", "terminal", "powershell", "cmd"]),
    ("Mídia", ["spotify", "obs studio", "obs", "davinci", "resolve", "vlc", "stremio", "premiere",
               "after effects", "audacity", "handbrake", "kdenlive", "foobar", "itunes", "plex", "photoshop",
               "gimp", "krita", "blender", "lightroom"]),
    ("Utilitários", ["kaspersky", "antivirus", "malwarebytes", "winrar", "7-zip", "7zip", "uninstaller",
                     "revo", "ccleaner", "driver", "nvidia", "amd", "razer", "logitech", "corsair",
                     "google drive", "onedrive", "dropbox", "passwordmanager", "password manager",
                     "recycle", "reciclagem", "gamers club anti"]),
    ("Produtividade", ["obsidian", "notion", "word", "excel", "powerpoint", "libreoffice", "onenote",
                       "evernote", "trello", "todoist"]),
]
_KB = [(sub, [re.compile(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])") for k in kws])
       for sub, kws in KNOWLEDGE]

_USER_PATH = re.compile(r"(?i)\b[a-z]:[\\/]+users[\\/]+[^\\/]+")
_HOME_PATH = re.compile(r"/(?:home|Users)/[^/\s]+")


def anonimizar_caminho(texto: str) -> str:
    texto = _USER_PATH.sub("%USERPROFILE%", texto or "")
    return _HOME_PATH.sub("~", texto)


def _limpo(valor: str, limite: int = 160) -> str:
    valor = "".join(ch for ch in (valor or "") if ch.isprintable()).strip()
    return anonimizar_caminho(valor)[:limite]


def adivinhar_tipo(*textos: str) -> str:
    alvo = " ".join(t for t in textos if t).casefold().replace("\\", " ").replace("/", " ").replace("_", " ")
    for sub, padroes in _KB:
        if any(p.search(alvo) for p in padroes):
            return sub
    return ""


def _cstr(buf: bytes, off: int, unicode_: bool) -> str:
    if off <= 0 or off >= len(buf):
        return ""
    if unicode_:
        fim = off
        while fim + 1 < len(buf) and buf[fim:fim + 2] != b"\0\0":
            fim += 2
        return buf[off:fim].decode("utf-16-le", "replace")
    fim = buf.find(b"\0", off)
    return buf[off:fim if fim >= 0 else len(buf)].decode("cp1252", "replace")


def ler_lnk(dados: bytes) -> dict:
    if len(dados) < 76 or struct.unpack_from("<I", dados, 0)[0] != 0x4C:
        raise ValueError("não é um atalho .lnk")
    flags = struct.unpack_from("<I", dados, 20)[0]
    tem_idlist, tem_info, tem_nome, tem_rel, tem_pasta, tem_args, tem_icone = (bool(flags & (1 << i)) for i in range(7))
    unicode_ = bool(flags & (1 << 7))
    pos = 76
    if tem_idlist:
        pos += 2 + struct.unpack_from("<H", dados, pos)[0]
    out: dict = {}
    if tem_info:
        base = pos
        tam, cab, info_flags, _vol, off_local, _rede, off_sufixo = struct.unpack_from("<7I", dados, base)
        local = sufixo = ""
        if info_flags & 1:
            if cab >= 0x24:
                off_local_u, off_sufixo_u = struct.unpack_from("<2I", dados, base + 28)
                local = _cstr(dados, base + off_local_u, True) if off_local_u else ""
                sufixo = _cstr(dados, base + off_sufixo_u, True) if off_sufixo_u else ""
            local = local or _cstr(dados, base + off_local, False)
            sufixo = sufixo or _cstr(dados, base + off_sufixo, False)
        if local or sufixo:
            out["alvo"] = local + sufixo
        pos += tam

    def texto() -> str:
        nonlocal pos
        n = struct.unpack_from("<H", dados, pos)[0]
        pos += 2
        if unicode_:
            s = dados[pos:pos + 2 * n].decode("utf-16-le", "replace")
            pos += 2 * n
        else:
            s = dados[pos:pos + n].decode("cp1252", "replace")
            pos += n
        return s

    for presente, chave in ((tem_nome, "nome"), (tem_rel, "relativo"), (tem_pasta, "pasta"),
                            (tem_args, None), (tem_icone, "icone")):
        if presente:
            valor = texto()
            if chave:
                out[chave] = valor
    return out


def _ini(texto: str) -> dict:
    d: dict = {}
    for linha in texto.splitlines():
        if "=" in linha and not linha.lstrip().startswith(("#", ";", "[")):
            k, v = linha.split("=", 1)
            d.setdefault(k.strip().lower(), v.strip())
    return d


def ler_url(texto: str) -> dict:
    d = _ini(texto)
    url = d.get("url", "")
    sem_query = re.split(r"[?#]", url, 1)[0]
    return {"endereco": sem_query, "icone": d.get("iconfile", "")}


def ler_desktop(texto: str) -> dict:
    d = _ini(texto)
    exec_ = d.get("exec", "").split()
    return {"nome": d.get("name", ""), "descricao": d.get("comment", ""), "categorias": d.get("categories", ""),
            "alvo": exec_[0] if exec_ else ""}


_CHAVES_PE = {"ProductName": "produto", "FileDescription": "descricao", "CompanyName": "empresa"}


def ler_versao_pe(caminho: str) -> dict:
    p = Path(caminho)
    tam = p.stat().st_size
    if tam == 0 or tam > MAX_PE_BYTES:
        return {}
    out: dict = {}
    with open(p, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        if mm[:2] != b"MZ":
            return {}
        for chave, rotulo in _CHAVES_PE.items():
            padrao = chave.encode("utf-16-le") + b"\0\0"
            achado = mm.find(padrao)
            if achado < 0:
                continue
            ini = achado + len(padrao)
            while ini + 1 < tam and mm[ini:ini + 2] == b"\0\0" and ini - achado < len(padrao) + 8:
                ini += 2
            bruto = mm[ini:ini + 400]
            fim = 0
            while fim + 1 < len(bruto) and bruto[fim:fim + 2] != b"\0\0":
                fim += 2
            valor = bruto[:fim].decode("utf-16-le", "replace")
            if valor and all(c.isprintable() for c in valor) and len(valor) < 150:
                out[rotulo] = valor
    return out


def contexto_do_item(caminho: str, nome: str, ext: str) -> dict:
    try:
        bruto: dict = {}
        if ext == ".lnk":
            bruto = ler_lnk(Path(caminho).read_bytes()[:65536])
        elif ext == ".url":
            bruto = ler_url(Path(caminho).read_text(encoding="utf-8", errors="replace")[:4096])
        elif ext == ".desktop":
            bruto = ler_desktop(Path(caminho).read_text(encoding="utf-8", errors="replace")[:8192])
        elif ext in EXECUTABLE_EXT:
            bruto = ler_versao_pe(caminho)
        else:
            return {}
    except (OSError, ValueError, struct.error, UnicodeError):
        return {}
    meta = {}
    destino = bruto.get("alvo") or bruto.get("endereco") or bruto.get("relativo") or ""
    if destino:
        meta["alvo_do_atalho"] = _limpo(destino)
    for origem, rotulo in (("produto", "produto"), ("empresa", "empresa"), ("descricao", "descricao_do_programa"),
                           ("nome", "nome_no_atalho"), ("categorias", "categorias_desktop")):
        if bruto.get(origem):
            meta[rotulo] = _limpo(str(bruto[origem]))
    if bruto.get("icone"):
        meta["icone_do_atalho"] = _limpo(bruto["icone"])
    tipo = adivinhar_tipo(Path(nome).stem, meta.get("alvo_do_atalho", ""), meta.get("produto", ""),
                          meta.get("empresa", ""), meta.get("descricao_do_programa", ""),
                          meta.get("nome_no_atalho", ""), meta.get("icone_do_atalho", ""),
                          meta.get("categorias_desktop", ""))
    if tipo:
        meta["tipo_provavel_app"] = tipo
    return meta
