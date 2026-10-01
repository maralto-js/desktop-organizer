from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from ..ollama_client import OllamaClient, OllamaError, OllamaUnavailable

MODOS = ("local", "cloud_login", "cloud_api")

MODELOS_CLOUD_EXEMPLO = [
    "gpt-oss:20b-cloud", "gpt-oss:120b-cloud", "qwen3.5:cloud", "deepseek-v4-flash:cloud",
    "kimi-k2.6:cloud", "minimax-m3:cloud", "glm-5.2:cloud",
]

_PADRAO_URL_SIGNIN = re.compile(r"https://ollama\.com/connect\S*")


def tamanho_humano(n_bytes) -> str:
    if not n_bytes:
        return "?"
    n = float(n_bytes)
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unidade == "TB":
            return f"{int(n)} B" if unidade == "B" else f"{n:.1f} {unidade}"
        n /= 1024
    return "?"


def separar_nome_modelo(texto: str) -> str:
    return (texto or "").split("  (")[0].strip()


def listar_modelos_locais(local_url: str, timeout: int = 3):
    cli = OllamaClient(local_url, "", timeout=timeout, retries=0)
    try:
        dados = cli._request("GET", "/api/tags")
    except OllamaError as e:
        return False, f"Não consegui falar com o Ollama em {local_url}: {e}"
    modelos = [{"nome": m.get("name", "?"), "tamanho": tamanho_humano(m.get("size"))}
               for m in (dados.get("models") or []) if isinstance(m, dict)]
    modelos.sort(key=lambda m: m["nome"])
    return True, modelos


def listar_modelos_cloud_api(chave: str, cloud_url: str = "https://ollama.com", timeout: int = 8):
    if not chave or not chave.strip():
        return False, "Preencha a chave de API primeiro."
    cli = OllamaClient(cloud_url, "", api_key=chave.strip(), timeout=timeout, retries=0)
    try:
        dados = cli._request("GET", "/api/tags")
    except OllamaUnavailable as e:
        return False, f"Chave inválida ou sem permissão: {e}"
    except OllamaError as e:
        return False, f"Não consegui falar com a ollama.com: {e}"
    nomes = sorted(m.get("name", "?") for m in (dados.get("models") or []) if isinstance(m, dict))
    if not nomes:
        return False, "A chave funcionou, mas a lista veio vazia. Use o catálogo no site."
    return True, nomes


def cli_disponivel() -> bool:
    return shutil.which("ollama") is not None


def _rodar_cli(args: list[str], timeout: int):
    return subprocess.run(["ollama", *args], capture_output=True, text=True, timeout=timeout)


def rodar_signin(timeout: int = 180):
    if not cli_disponivel():
        return False, ("Não encontrei o comando 'ollama' no PATH. Reinstale o Ollama marcando a opção "
                       "de adicionar ao PATH, ou reinicie o computador se acabou de instalar."), None
    try:
        r = _rodar_cli(["signin"], timeout)
        saida = ((r.stdout or "") + (r.stderr or "")).strip()
        achou = _PADRAO_URL_SIGNIN.search(saida)
        return r.returncode == 0, saida or "(sem saída do comando)", achou.group(0) if achou else None
    except subprocess.TimeoutExpired:
        return False, (f"'ollama signin' não terminou em {timeout}s. Se o navegador abriu pedindo login, "
                       "conclua lá e use 'Verificar conexão': o login pode ter funcionado mesmo assim."), None
    except OSError as e:
        return False, f"Erro ao rodar 'ollama signin': {e}", None


def rodar_signout(timeout: int = 30):
    if not cli_disponivel():
        return False, "Não encontrei o comando 'ollama' no PATH."
    try:
        r = _rodar_cli(["signout"], timeout)
        saida = ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode == 0, saida or "(sem saída do comando)"
    except subprocess.TimeoutExpired:
        return False, f"'ollama signout' não terminou em {timeout}s."
    except OSError as e:
        return False, f"Erro ao rodar 'ollama signout': {e}"


def verificar_login_cloud(local_url: str, modelo: str, timeout: int = 25):
    modelo = (modelo or "").strip() or MODELOS_CLOUD_EXEMPLO[0]
    cli = OllamaClient(local_url, modelo, timeout=timeout, retries=0)
    payload = {"model": modelo, "messages": [{"role": "user", "content": "oi"}],
               "stream": False, "options": {"num_predict": 1}}
    try:
        resp = cli._request("POST", "/api/chat", payload)
    except OllamaError as e:
        return False, str(e)
    if isinstance(resp, dict) and resp.get("error"):
        return False, str(resp["error"])[:300]
    return True, "A conta Ollama Cloud está autenticada e funcionando."


URL_USO_NUVEM = "https://ollama.com/api/usage"


def usado_para_percentual(valor: float, unidade: str = "auto") -> float:
    if unidade == "fracao":
        p = valor * 100
    elif unidade == "percentual":
        p = valor
    else:
        p = valor * 100 if valor <= 1.0 else valor
    return max(0.0, min(100.0, p))


def consultar_uso_cloud(chave: str, unidade: str = "auto", timeout: int = 8, url: str = URL_USO_NUVEM):
    if not chave or not chave.strip():
        return False, "Preencha a chave de API para consultar a cota."
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {chave.strip()}",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, f"Chave de API inválida ou sem permissão (HTTP {e.code})."
        if e.code == 404:
            return False, ("A Ollama parece ter mudado ou removido o endereço de consulta de cota "
                           "(HTTP 404). Veja o uso em ollama.com/settings.")
        return False, f"Erro ao consultar a cota (HTTP {e.code})."
    except urllib.error.URLError as e:
        return False, f"Não consegui falar com a ollama.com: {e.reason}"
    except (OSError, ValueError) as e:
        return False, f"Resposta inesperada ao consultar a cota: {e}"
    try:
        s = float(dados["limits"]["session"]["usage"])
        w = float(dados["limits"]["weekly"]["usage"])
    except (KeyError, TypeError, ValueError):
        return False, "A resposta da cota veio num formato desconhecido. Veja o uso em ollama.com/settings."
    return True, {"sessao": usado_para_percentual(s, unidade),
                  "semana": usado_para_percentual(w, unidade), "bruto": (s, w)}


def construir_cliente(cfg: dict, modo: str, modelo: str, chave: str = "") -> tuple[OllamaClient, str, str]:
    if modo not in MODOS:
        raise ValueError(f"modo desconhecido: {modo}")
    o = cfg["ollama"]
    cloud_direta = modo == "cloud_api"
    url = o["cloud_url"] if cloud_direta else o["local_url"]
    schema_ok = o["use_json_schema"] and not cloud_direta and not modelo.endswith(("-cloud", ":cloud"))
    cliente = OllamaClient(url, modelo, chave.strip() if cloud_direta else "", o["timeout_seconds"],
                           o["retries"], o["temperature"], o["num_ctx"], use_schema=schema_ok)
    nuvem = cloud_direta or modo == "cloud_login" or modelo.endswith(("-cloud", ":cloud"))
    return cliente, "cloud" if nuvem else "local", modelo


def carregar_preferencias(data_dir: Path) -> dict:
    padrao = {"modo_motor": "local", "modelo_local": "", "modelo_cloud": MODELOS_CLOUD_EXEMPLO[0],
              "lembrar_chave": False, "chave_api": ""}
    p = Path(data_dir) / "gui_settings.json"
    try:
        salvo = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao
    if isinstance(salvo, dict):
        for k in padrao:
            if k in salvo and isinstance(salvo[k], type(padrao[k])):
                padrao[k] = salvo[k]
    if padrao["modo_motor"] not in MODOS:
        padrao["modo_motor"] = "local"
    return padrao


def salvar_preferencias(data_dir: Path, prefs: dict) -> None:
    dados = dict(prefs)
    if not dados.get("lembrar_chave"):
        dados["chave_api"] = ""
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "gui_settings.json.tmp"
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(d / "gui_settings.json")
