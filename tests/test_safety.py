"""Тесты безопасности: границы workspace, дисциплина dispatch."""

from __future__ import annotations

import unittest

from _helpers import build_agent
from universal_agent import _safe_path


class TestSafePath(unittest.TestCase):
    def setUp(self):
        self.agent, self.tmp = build_agent()

    def test_inside_workspace_ok(self):
        path = _safe_path("sub/dir/file.txt")
        self.assertTrue(str(path).startswith(str(self.tmp / "workspace")))

    def test_traversal_rejected(self):
        with self.assertRaises(PermissionError):
            _safe_path("../escape.txt")

    def test_absolute_escape_rejected(self):
        with self.assertRaises(PermissionError):
            _safe_path("/etc/passwd")


class TestDispatchDiscipline(unittest.TestCase):
    def setUp(self):
        self.agent, _ = build_agent()

    def test_unknown_tool_is_observation_not_crash(self):
        result = self.agent.dispatch("телепортация", {})
        self.assertIn("неизвестный инструмент", result)
        self.assertIn("телепортация", result)

    def test_missing_params_reported(self):
        result = self.agent.dispatch("read_file", {})
        self.assertIn("не переданы параметры", result)
        self.assertIn("path", result)

    def test_tool_error_is_observation_not_crash(self):
        result = self.agent.dispatch("read_file", {"path": 12345})
        self.assertTrue(result.startswith("ОШИБКА ИНСТРУМЕНТА"))

    def test_extra_params_ignored(self):
        from pathlib import Path
        self.agent.dispatch("write_file", {"path": "ok.txt", "content": "x", "лишнее": 1})
        self.assertTrue((Path(self.agent.cfg.workspace_root) / "ok.txt").exists())


if __name__ == "__main__":
    unittest.main()
