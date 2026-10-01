from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


class OllamaError(Exception):
    pass


class OllamaUnavailable(OllamaError):
    pass


class OllamaTimeout(OllamaError):
    pass


class OllamaBadResponse(OllamaError):
    pass


class OllamaBadRequest(OllamaError):
    pass


def extract_json(text: str):
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
    try:
        return json.loads(t)
    except ValueError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = t.find(open_c), t.rfind(close_c)
        if i != -1 and j > i:
            try:
                return json.loads(t[i : j + 1])
            except ValueError:
                continue
    raise OllamaBadResponse("a resposta do modelo não contém JSON válido")


def _is_loopback(url: str) -> bool:
    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


class OllamaClient:
    def __init__(self, base_url, model, api_key="", timeout=180, retries=2, temperature=0,
                 num_ctx=None, use_schema=True, sleep=time.sleep):
        self.base = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.use_schema = use_schema
        self.sleep = sleep
        handlers = [urllib.request.ProxyHandler({})] if _is_loopback(self.base) else []
        self.opener = urllib.request.build_opener(*handlers)

    def _request(self, method, path, payload=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        last: Exception = OllamaError("falha desconhecida")
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
            try:
                with self.opener.open(req, timeout=self.timeout) as r:
                    body = r.read()
                try:
                    return json.loads(body)
                except ValueError:
                    raise OllamaBadResponse("a resposta HTTP não é JSON")
            except urllib.error.HTTPError as e:
                text = e.read()[:400].decode("utf-8", "replace")
                if e.code in (401, 403):
                    raise OllamaUnavailable(f"autenticação recusada (HTTP {e.code}); verifique a chave de API")
                if e.code == 404:
                    raise OllamaUnavailable(f"modelo ou endpoint não encontrado (HTTP 404): {text}")
                if e.code == 400:
                    raise OllamaBadRequest(f"HTTP 400: {text}")
                last = OllamaError(f"HTTP {e.code}: {text}")
                if e.code != 429 and e.code < 500:
                    raise last
            except (socket.timeout, TimeoutError):
                last = OllamaTimeout(f"tempo esgotado após {self.timeout}s")
            except urllib.error.URLError as e:
                if isinstance(e.reason, (socket.timeout, TimeoutError)):
                    last = OllamaTimeout(f"tempo esgotado após {self.timeout}s")
                else:
                    raise OllamaUnavailable(f"não foi possível conectar a {self.base}: {e.reason}")
            except OllamaBadResponse:
                raise
            except OSError as e:
                last = OllamaError(f"erro de rede: {e}")
            if attempt < self.retries:
                self.sleep(min(2 ** attempt, 8))
        raise last

    def version(self) -> str:
        return str(self._request("GET", "/api/version").get("version", "?"))

    def list_models(self) -> list[str]:
        data = self._request("GET", "/api/tags")
        return [m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)]

    def chat_json(self, system: str, user: str, schema: dict | None = None):
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        if self.num_ctx:
            payload["options"]["num_ctx"] = self.num_ctx
        if schema and self.use_schema:
            payload["format"] = schema
        try:
            resp = self._request("POST", "/api/chat", payload)
        except OllamaBadRequest:
            if "format" not in payload:
                raise
            payload.pop("format")
            self.use_schema = False
            resp = self._request("POST", "/api/chat", payload)
        if not isinstance(resp, dict):
            raise OllamaBadResponse("formato de resposta inesperado")
        if resp.get("error"):
            raise OllamaError(str(resp["error"])[:300])
        content = (resp.get("message") or {}).get("content")
        if not content or not str(content).strip():
            raise OllamaBadResponse("resposta vazia do modelo")
        return extract_json(str(content))
