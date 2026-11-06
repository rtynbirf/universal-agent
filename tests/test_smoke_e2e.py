"""Сквозные тесты: полный когнитивный цикл на mock-мозге, без сети."""

from __future__ import annotations

import unittest

import mock_llm_server
from universal_agent import AgentConfig, UniversalAgent

DEMO_SCRIPT = list(mock_llm_server.DEMO_SCRIPT)
REPAIR_SCRIPT = [
    "Дайте подумать... я пока не готов ответить структурой.",   # нарушение протокола
    '{"thought":"Осматриваюсь","action":{"name":"list_dir","params":{}}}',
    '{"thought":"Критерий достигнут","action":{"name":"finish","params":{"result":"Каталог пуст, задача тривиальна."}}}',
    '{"useful": false}',
]
BUDGET_SCRIPT = [
    '{"thought":"Осматриваюсь","action":{"name":"list_dir","params":{}}}',
    "Успел только осмотреться: каталог пуст. Осталось: написать отчёт.",
]


class TestEndToEnd(unittest.TestCase):
    def _agent(self, tmp, port, **overrides):
        cfg = AgentConfig(
            llm_base_url=f"http://127.0.0.1:{port}/v1",
            llm_model="mock",
            workspace_root=str(tmp / "workspace"),
            memory_db=str(tmp / "memory" / "agent.db"),
            knowledge_dir=str(tmp / "knowledge"),
            results_log=str(tmp / "results.log"),
            queue_path=str(tmp / "task_queue.txt"),
        )
        for key, value in overrides.items():
            setattr(cfg, key, value)
        return UniversalAgent(cfg)

    def test_full_cycle(self):
        tmp_holder = {}

        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp(prefix="cua-e2e-"))
        tmp_holder["tmp"] = tmp

        server = mock_llm_server.make_server(list(DEMO_SCRIPT))
        try:
            port = server.server_address[1]
            agent = self._agent(tmp, port)
            (tmp / "workspace" / "demo.txt").write_text("привет", encoding="utf-8")

            report = agent.solve("Изучи workspace, посчитай 2+2, запиши отчёт")
        finally:
            server.shutdown()
            server.server_close()

        # -- статус и трассировка --
        self.assertEqual(report["status"], "done")
        self.assertEqual(report["steps"], 5)
        self.assertIn("2+2", report["result"])

        # -- след в workspace --
        report_md = tmp / "workspace" / "report.md"
        self.assertTrue(report_md.exists())
        self.assertIn("Сумма: 4", report_md.read_text(encoding="utf-8"))

        # -- консолидация урока --
        lesson = tmp / "knowledge" / "survey-first.md"
        self.assertTrue(lesson.exists())
        self.assertIn("Урок", lesson.read_text(encoding="utf-8"))

        # -- самодокументация --
        self.assertIn("ИТОГ", (tmp / "results.log").read_text(encoding="utf-8"))

        # -- эпизодическая память: 1 задача + 4x(действие+наблюдение) + отчёт + урок --
        count = agent.memory.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(count, 11)

    def test_protocol_repair_channel(self):
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp(prefix="cua-repair-"))

        server = mock_llm_server.make_server(list(REPAIR_SCRIPT))
        try:
            agent = self._agent(tmp, server.server_address[1])
            report = agent.solve("Тривиальная проверка")
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(report["status"], "done")
        self.assertEqual(report["steps"], 3)  # сбой протокола не сломал цикл

    def test_budget_gives_honest_partial(self):
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp(prefix="cua-budget-"))

        server = mock_llm_server.make_server(list(BUDGET_SCRIPT))
        try:
            agent = self._agent(tmp, server.server_address[1], max_steps=1)
            report = agent.solve("Невыполнимая за один шаг задача")
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["steps"], 1)
        self.assertIn("осмотреться", report["result"])


if __name__ == "__main__":
    unittest.main()
