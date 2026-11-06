"""
mock_llm_server.py — тестовый двойник мозга (OpenAI-совместимый).
Отдаёт заранее заготовленные реплики из скрипта по очереди, позволяя
прогонять ЦУА end-to-end без реальной LLM и без сети.

Запуск как демо-сервера:
    python mock_llm_server.py            # встроенный демо-сценарий, порт 8077
    python mock_llm_server.py replies.jsonl   # по одной реплике-строке в файле

Внутри тестов: make_server(replies) -> ThreadingHTTPServer (порт 0 = случайный).
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEMO_SCRIPT = [
    '{"thought":"Осматриваюсь в workspace","action":{"name":"list_dir","params":{}}}',
    '{"thought":"Читаю демо-файл","action":{"name":"read_file","params":{"path":"demo.txt"}}}',
    '{"thought":"Считаю сумму на живом коде","action":{"name":"execute_python","params":{"code":"print(2+2)"}}}',
    '{"thought":"Фиксирую отчёт","action":{"name":"write_file","params":{"path":"report.md","content":"# Отчёт\\nСумма: 4"}}}',
    '{"thought":"Критерий готовности достигнут","action":{"name":"finish","params":{"result":"Изучил workspace, проверил исполнение кода (2+2=4), отчёт записан в report.md."}}}',
    '{"useful": true, "lesson": "Сначала осмотри каталог, потом действуй", "filename": "survey-first"}',
]

EXHAUSTED = ('{"thought":"скрипт двойника исчерпан",'
             '"action":{"name":"finish","params":{"result":"mock exhausted"}}}')


class MockLLMHandler(BaseHTTPRequestHandler):
    """Отвечает на любой POST /.../chat/completions следующей репликой скрипта."""

    script: list[str] = []
    lock = threading.Lock()

    def do_POST(self) -> None:  # noqa: N802 — требование BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        with MockLLMHandler.lock:
            reply = MockLLMHandler.script.pop(0) if MockLLMHandler.script else EXHAUSTED
        body = json.dumps({
            "id": "chatcmpl-mock", "object": "chat.completion", "created": 0,
            "model": "mock-brain",
            "choices": [{
                "index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": reply},
            }],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # тишина в тестовых логах
        pass


def make_server(replies: list[str]) -> ThreadingHTTPServer:
    """Поднять mock-мозг на случайном порту (127.0.0.1). Возвращает сервер:
    порт — srv.server_address[1]; остановка — srv.shutdown()+server_close()."""
    MockLLMHandler.script = list(replies)
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockLLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as fh:
            script = [line.rstrip("\n") for line in fh if line.strip()]
    else:
        script = list(DEMO_SCRIPT)
    MockLLMHandler.script = script
    server = ThreadingHTTPServer(("127.0.0.1", 8077), MockLLMHandler)
    print(f"mock-мозг на http://127.0.0.1:{server.server_address[1]}/v1 "
          f"({len(script)} реплик). Ctrl+C — выход.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
