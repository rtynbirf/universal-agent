"""Тесты рук: файловые операции и песочница исполнения."""

from __future__ import annotations

import os
import unittest

from _helpers import build_agent


class TestFileHands(unittest.TestCase):
    def setUp(self):
        self.agent, self.tmp = build_agent()
        self.root = self.tmp / "workspace"

    def test_write_read_roundtrip(self):
        out = self.agent.dispatch("write_file", {"path": "a/b.txt", "content": "данные"})
        self.assertIn("записано", out)
        back = self.agent.dispatch("read_file", {"path": "a/b.txt"})
        self.assertEqual(back, "данные")

    def test_list_dir_shows_files(self):
        (self.root / "видимый.txt").write_text("x", encoding="utf-8")
        listing = self.agent.dispatch("list_dir", {})
        self.assertIn("видимый.txt", listing)

    def test_list_dir_empty(self):
        listing = self.agent.dispatch("list_dir", {})
        self.assertIn("(каталог пуст)", listing)


class TestSandbox(unittest.TestCase):
    def setUp(self):
        self.agent, _ = build_agent()
        self.agent.cfg.exec_net_isolate = False  # стабильность тестовой среды

    def test_stdout_and_exit_code(self):
        out = self.agent.dispatch("execute_python", {"code": "print(2+2)"})
        self.assertIn("4", out)
        self.assertIn("exit=0", out)

    def test_syntax_error_surfaces_as_observation(self):
        out = self.agent.dispatch("execute_python", {"code": "def сломано("})
        self.assertIn("exit=1", out)
        self.assertIn("stderr", out)

    def test_timeout_kills_process(self):
        out = self.agent.dispatch(
            "execute_python", {"code": "import time; time.sleep(5)", "timeout_sec": 1}
        )
        self.assertIn("TIMEOUT", out)

    def test_memory_limit_enforced(self):
        self.agent.cfg.exec_mem_mb = 64
        out = self.agent.dispatch(
            "execute_python",
            {"code": "x = bytearray(512 * 1024 * 1024); print(len(x))"},
        )
        self.assertNotIn("exit=0", out)  # malloc убит лимитом адресного пространства

    def test_cwd_is_workspace(self):
        with open(os.path.join(self.agent.cfg.workspace_root, "маркер.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("здесь")
        out = self.agent.dispatch(
            "execute_python",
            {"code": "print(open('маркер.txt', encoding='utf-8').read())"},
        )
        self.assertIn("здесь", out)


if __name__ == "__main__":
    unittest.main()
