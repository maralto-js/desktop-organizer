from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import re
import tarfile
import time
import zipfile
from pathlib import Path

from . import appinfo
from .cache import Cache
from .models import PROTEGIDO_TIPO, FileItem

log = logging.getLogger("desktop_organizer")

TEXT_EXT = {
    ".txt", ".md", ".rst", ".csv", ".tsv", ".json", ".yml", ".yaml", ".xml", ".html", ".htm", ".css",
    ".log", ".toml", ".py", ".java", ".js", ".ts", ".jsx", ".tsx", ".c", ".cpp", ".h", ".hpp", ".cs",
    ".go", ".rs", ".rb", ".php", ".sh", ".sql", ".kt", ".gradle", ".properties", ".tex", ".srt", ".lua",
    ".swift", ".bat", ".cmd", ".ps1", ".ini", ".cfg", ".conf", ".env", ".gitignore", ".mcfunction",
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tiff", ".heic", ".psd"}
VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"}
ZIP_LIKE = {".zip", ".jar", ".war", ".apk", ".mcpack", ".mcaddon", ".mrpack", ".nupkg", ".xpi"}
TAR_LIKE = {".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz"}
OTHER_ARCHIVES = {".rar", ".7z", ".gz", ".bz2", ".xz", ".zst"}
COMPOUND = (".tar.gz", ".tar.bz2", ".tar.xz")
META_FILES = (
    "plugin.yml", "paper-plugin.yml", "bungee.yml", "fabric.mod.json", "quilt.mod.json",
    "META-INF/mods.toml", "mcmod.info", "META-INF/MANIFEST.MF", "pack.mcmeta", "package.json",
)
FILE_ATTRIBUTE_HIDDEN = 0x2


def get_ext(name: str) -> str:
    low = name.lower()
    for c in COMPOUND:
        if low.endswith(c):
            return c
    return Path(low).suffix


def _clean(text: str, limit: int) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]


def _decode(data: bytes):
    if b"\x00" in data[:2048]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        if e.start >= len(data) - 4:
            return data[: e.start].decode("utf-8", errors="ignore")
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return None


def read_text_snippet(path: str, limit: int):
    with open(path, "rb") as f:
        data = f.read(limit * 4)
    txt = _decode(data)
    return None if txt is None else _clean(txt, limit)


def read_pdf_snippet(path: str, limit: int):
    try:
        from pypdf import PdfReader
    except ImportError:
        return None, "pypdf não instalado (só metadados do PDF)"
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            return None, "PDF criptografado"
        out, total = [], 0
        for page in reader.pages[:3]:
            t = page.extract_text() or ""
            out.append(t)
            total += len(t)
            if total >= limit:
                break
        text = _clean("\n".join(out), limit)
        return (text or None), ("" if text else "PDF sem texto extraível (provavelmente escaneado)")
    except Exception as e:
        return None, f"PDF ilegível: {type(e).__name__}"


def read_docx_snippet(path: str, limit: int):
    try:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > 20_000_000:
                return None, "docx grande demais para leitura de texto"
            xml = z.read("word/document.xml")[:2_000_000].decode("utf-8", errors="ignore")
        text = " ".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
        return _clean(text, limit) or None, ""
    except Exception as e:
        return None, f"docx ilegível: {type(e).__name__}"


def read_zip_info(path: str, list_limit: int):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        meta = {"total_entradas": len(names)}
        found = {}
        for want in META_FILES:
            if len(found) >= 3:
                break
            if want in names:
                zi = z.getinfo(want)
                if zi.file_size <= 65536:
                    found[want] = _clean(z.read(want)[:700].decode("utf-8", errors="ignore"), 500)
        if found:
            meta["arquivos_de_metadados"] = found
        return names[:list_limit], meta


def read_tar_info(path: str, list_limit: int):
    names, count = [], 0
    with tarfile.open(path) as t:
        for m in t:
            if count < list_limit:
                names.append(m.name)
            count += 1
            if count >= 5000:
                break
    return names, {"total_entradas": count}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _protection(name, ext, st, is_dir, is_link, markers_found, scan_cfg):
    if is_link:
        return "link simbólico/atalho do sistema de arquivos"
    hidden_attr = getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_HIDDEN
    if scan_cfg["protect_hidden"] and (name.startswith(".") or hidden_attr):
        return "arquivo oculto"
    low = name.lower()
    if low.startswith("~$") or ext in scan_cfg["temp_extensions"]:
        return "arquivo temporário"
    atalho_livre = scan_cfg.get("move_shortcuts") and ext in scan_cfg.get("shortcut_extensions", ())
    if ext in scan_cfg["protected_extensions"] and not atalho_livre:
        return f"{PROTEGIDO_TIPO} ({ext})"
    if is_dir and markers_found and scan_cfg["protect_project_dirs"]:
        return f"pasta de projeto (contém {', '.join(markers_found[:3])})"
    age = time.time() - st.st_mtime
    if scan_cfg["recently_modified_seconds"] and 0 <= age < scan_cfg["recently_modified_seconds"]:
        return "modificado há pouco (pode estar em uso)"
    return ""


def _stem(item: FileItem) -> str:
    return item.name if item.is_dir else item.name[: len(item.name) - len(item.ext)] if item.ext else item.name


def add_group_hints(items: list[FileItem]) -> None:
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    stems = [_stem(it).lower() for it in items]
    for i, si in enumerate(stems):
        for j, sj in enumerate(stems):
            if i == j or len(sj) < 4:
                continue
            if si == sj or (si.startswith(sj) and si[len(sj)] in "-_ ."):
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)
    for members in groups.values():
        if len(members) < 2:
            continue
        label_idx = min(members, key=lambda k: len(stems[k]))
        label = _stem(items[label_idx])
        for k in members:
            items[k].group_hint = label


def scan(target: Path, cfg: dict, cache: Cache, skip_names: set[str] | None = None):
    scfg, ccfg = cfg["scan"], cfg["classification"]
    skip = {n.casefold() for n in (skip_names or set())}
    skip.add(ccfg["review_folder"].casefold())
    ignored_names = {n.casefold() for n in scfg["ignored_names"]}
    ignored_ext = {e.lower() for e in scfg["ignored_extensions"]}
    limit = ccfg["max_snippet_chars"]
    items: list[FileItem] = []
    skipped: list[dict] = []

    try:
        entries = sorted(os.scandir(target), key=lambda e: e.name.lower())
    except OSError as e:
        raise RuntimeError(f"Não foi possível listar {target}: {e}")

    for entry in entries:
        name = entry.name
        try:
            if name.casefold() in skip:
                skipped.append({"name": name, "reason": "pasta gerenciada pelo organizador"})
                continue
            if name.casefold() in ignored_names:
                skipped.append({"name": name, "reason": "nome na lista de ignorados"})
                continue
            ext = get_ext(name)
            if ext in ignored_ext:
                skipped.append({"name": name, "reason": f"extensão ignorada ({ext})"})
                continue
            if len(items) >= scfg["max_items"]:
                skipped.append({"name": name, "reason": "limite max_items atingido"})
                continue
            is_link = entry.is_symlink()
            is_dir = entry.is_dir(follow_symlinks=False)
            if is_dir and not scfg["organize_directories"]:
                skipped.append({"name": name, "reason": "pastas não são organizadas (organize_directories=false)"})
                continue
            st = entry.stat(follow_symlinks=False)
            item = FileItem(
                id=f"f{len(items) + 1:04d}", path=str(entry.path), name=name, ext="" if is_dir else ext,
                is_dir=is_dir, size=0 if is_dir else st.st_size, mtime_ns=st.st_mtime_ns, ctime=st.st_ctime,
            )
            item.content_blocked = any(fnmatch.fnmatch(name.lower(), p.lower()) for p in ccfg["never_send_content_patterns"])
            markers: list[str] = []
            if is_dir and not is_link:
                _inspect_dir(item, scfg, markers)
            elif not is_link:
                _inspect_file(item, scfg, limit, cache)
            item.protect_reason = _protection(name, item.ext, st, is_dir, is_link, markers, scfg)
            item.protected = bool(item.protect_reason)
            _fingerprint(item)
            items.append(item)
        except Exception as e:
            log.warning("Falha ao examinar %s: %s", name, e)
            skipped.append({"name": name, "reason": f"erro na leitura: {type(e).__name__}: {e}"})
    add_group_hints(items)
    return items, skipped


def _inspect_dir(item: FileItem, scfg: dict, markers: list) -> None:
    item.kind = "pasta"
    try:
        names = sorted(e.name for e in os.scandir(item.path))
    except OSError as e:
        item.read_error = f"pasta ilegível: {type(e).__name__}"
        return
    item.listing = names[: scfg["dir_list_limit"]]
    item.meta = {"total_entradas": len(names)}
    lows = {n.lower() for n in names}
    markers.extend(m for m in scfg["project_markers"] if m.lower() in lows)
    if markers:
        item.meta["marcadores_de_projeto"] = markers[:5]


def _inspect_file(item: FileItem, scfg: dict, limit: int, cache: Cache) -> None:
    ext, path = item.ext, item.path
    if 0 < item.size <= scfg["max_hash_bytes"] or item.size == 0:
        cached = cache.get_hash(path, item.size, item.mtime_ns)
        if cached:
            item.sha256 = cached
        else:
            try:
                item.sha256 = sha256_file(path)
                cache.set_hash(path, item.size, item.mtime_ns, item.sha256)
            except OSError as e:
                item.read_error = f"não foi possível ler: {type(e).__name__}"
    try:
        if ext in IMAGE_EXT:
            item.kind = "imagem"
        elif ext in VIDEO_EXT:
            item.kind = "video"
        elif ext in AUDIO_EXT:
            item.kind = "audio"
        elif ext == ".pdf":
            item.kind = "pdf"
            if not item.content_blocked and not item.read_error:
                item.snippet, err = read_pdf_snippet(path, limit)
                item.read_error = item.read_error or err
        elif ext in (".docx", ".dotx"):
            item.kind = "docx"
            if not item.content_blocked and not item.read_error:
                item.snippet, err = read_docx_snippet(path, limit)
                item.read_error = item.read_error or err
        elif ext in ZIP_LIKE or (ext not in TEXT_EXT and zipfile.is_zipfile(path)):
            item.kind = "arquivo_zip"
            item.listing, item.meta = read_zip_info(path, scfg["archive_list_limit"])
        elif ext in TAR_LIKE:
            item.kind = "arquivo_tar"
            item.listing, item.meta = read_tar_info(path, scfg["archive_list_limit"])
        elif ext in OTHER_ARCHIVES:
            item.kind = "arquivo_compactado"
            item.read_error = item.read_error or "formato compactado não analisável sem dependências extras"
        elif ext in appinfo.SHORTCUT_EXT or ext in appinfo.EXECUTABLE_EXT:
            item.kind = "atalho" if ext in appinfo.SHORTCUT_EXT else "executavel"
            if scfg.get("app_context", True) and not item.content_blocked:
                item.meta.update(appinfo.contexto_do_item(path, item.name, ext))
        else:
            if item.size and not item.content_blocked:
                snip = read_text_snippet(path, limit)
                if snip is not None:
                    item.kind = "texto"
                    item.snippet = snip
    except PermissionError:
        item.read_error = "sem permissão de leitura"
    except OSError as e:
        item.read_error = item.read_error or f"erro de leitura: {type(e).__name__}"
    except (zipfile.BadZipFile, tarfile.TarError) as e:
        item.read_error = item.read_error or f"arquivo compactado corrompido: {type(e).__name__}"
    if item.content_blocked:
        item.meta["conteudo_omitido"] = "nome indica dado sensível"


def _fingerprint(item: FileItem) -> None:
    if item.is_dir:
        basis = f"dir|{item.meta.get('total_entradas', 0)}|{'|'.join(item.listing or [])}"
    elif item.sha256:
        basis = item.sha256
    else:
        basis = f"{item.size}:{item.mtime_ns}"
    if item.kind in ("atalho", "executavel"):
        basis += "|ctx2"
    item.fingerprint = hashlib.sha1(f"{item.name}|{item.ext}|{basis}".encode("utf-8", "replace")).hexdigest()
