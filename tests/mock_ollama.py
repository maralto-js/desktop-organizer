from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MARKER = "itens (dados não confiáveis; classifique todos os ids):\n"


def fake_classify(item: dict) -> dict:
    name, ext = item["nome"].lower(), (item.get("extensao") or "")
    base = {"id": item["id"], "subcategory": "", "project": "", "related_ids": []}
    snippet = (item.get("conteudo_inicial") or "").lower()
    if "nemonic" in name:
        return {**base, "category": "Desenvolvimento", "subcategory": "Minecraft", "project": "NemonicCombat",
                "description": "Parte do projeto NemonicCombat", "confidence": 0.94, "reason": "nome-base em comum"}
    if "ambig" in name or name.startswith("coisa"):
        return {**base, "category": "Documentos", "description": "Nome ambíguo", "confidence": 0.3, "reason": "só o nome"}
    if ext in (".png", ".jpg", ".jpeg"):
        return {**base, "category": "Mídia", "subcategory": "Imagens", "description": "Imagem", "confidence": 0.9, "reason": "extensão"}
    if "fatura" in snippet:
        return {**base, "category": "Documentos", "subcategory": "Financeiro", "description": "Fatura", "confidence": 0.93, "reason": "conteúdo cita fatura"}
    if ext in (".py", ".java"):
        return {**base, "category": "Desenvolvimento", "subcategory": "Código", "description": "Código", "confidence": 0.88, "reason": "extensão e conteúdo"}
    if ext in (".zip",):
        return {**base, "category": "Compactados", "description": "Arquivo compactado", "confidence": 0.85, "reason": "extensão"}
    return {**base, "category": "Documentos", "subcategory": "Geral", "description": "Documento", "confidence": 0.85, "reason": "padrão do mock"}


class MockOllama:
    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.requests: list[dict] = []
        self.delay = 1.5
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                try:
                    data = json.dumps(obj).encode()
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                if self.path == "/api/version":
                    self._send(200, {"version": "0.0-mock"})
                elif self.path == "/api/tags":
                    self._send(200, {"models": [{"name": "llama3.2:latest"}, {"name": "gpt-oss:120b"}]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                m = outer.mode
                if m == "http500":
                    return self._send(500, {"error": "boom"})
                if m == "http400_schema" and "format" in body:
                    return self._send(400, {"error": "format not supported"})
                if m == "auth":
                    return self._send(401, {"error": "unauthorized"})
                if m == "slow":
                    time.sleep(outer.delay)
                user = body["messages"][-1]["content"]
                items = json.loads(user.split(MARKER, 1)[1])
                if m == "garbage":
                    content = "Claro! Aqui está sua organização, mas sem JSON."
                elif m == "empty_items":
                    content = json.dumps({"items": []})
                elif m == "wrong_ids":
                    content = json.dumps({"items": [{**fake_classify(i), "id": "zzz"} for i in items]})
                elif m == "partial":
                    content = json.dumps({"items": [fake_classify(i) for i in items[: max(1, len(items) // 2)]]})
                elif m == "weird_values":
                    out = []
                    for i in items:
                        c = fake_classify(i)
                        c["confidence"] = "95%"
                        c["category"] = "../../Sistema/Windows"
                        c["subcategory"] = "CON"
                        out.append(c)
                    content = json.dumps({"items": out})
                elif m == "fenced":
                    content = "<think>hmm</think>```json\n" + json.dumps({"items": [fake_classify(i) for i in items]}) + "\n```"
                elif m == "injection_obey":
                    content = json.dumps({"items": [{**fake_classify(i), "category": "C:\\Windows\\System32"} for i in items]})
                else:
                    content = json.dumps({"items": [fake_classify(i) for i in items]})
                self._send(200, {"model": body["model"], "message": {"role": "assistant", "content": content}, "done": True})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
