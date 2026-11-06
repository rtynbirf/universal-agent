"""Тесты контейнерной песочницы (фаза II «Крепость»): фейковый docker CLI.

Проверяем не сам docker, а дисциплину агента: с какими флагами он зовёт
docker, как интерпретирует результат и что делает при таймауте и откате.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from _helpers import build_agent

import universal_agent as ua

FAKE_DOCKER_SRC = Path(__file__).resolve().parent / "fake_docker.py"


class DockerShimCase(unittest.TestCase):
    """База: фейковый docker на PATH, лог argv, агент с exec_docker=True."""

    def setUp(self):
        self.bin_dir = Path(tempfile.mkdtemp(prefix="cua-docker-bin-"))
        self.log = self.bin_dir / "argv.log"
        launcher = self.bin_dir / "docker"
        launcher.write_text(
            "#!/usr/bin/env python3\n"
            "import runpy\n"
            f"runpy.run_path(r'{FAKE_DOCKER_SRC}', run_name='__main__')\n",
            encoding="utf-8",
        )
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self._old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.bin_dir}{os.pathsep}{self._old_path}"
        os.environ["FAKE_DOCKER_LOG"] = str(self.log)
        ua._DOCKER_READY = True                    # healthcheck пройден «заранее»
        self.agent, _ = build_agent(exec_docker=True)
        self.ws = Path(self.agent.cfg.workspace_root)

    def tearDown(self):
        os.environ["PATH"] = self._old_path
        os.environ.pop("FAKE_DOCKER_LOG", None)
        ua._DOCKER_READY = None

    def _argv_calls(self) -> list:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in
                self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _run_call(self) -> list:
        return next(c for c in self._argv_calls() if c[:1] == ["run"])


class TestContainerHardening(DockerShimCase):
    def test_container_command_is_hardened(self):
        self.agent.dispatch("execute_python", {"code": "print(2+2)"})
        run = self._run_call()
        self.assertEqual(run[run.index("--network") + 1], "none")
        self.assertEqual(run[run.index("--memory") + 1], "512m")
        self.assertEqual(run[run.index("--memory-swap") + 1], "512m")   # swap-побег закрыт
        self.assertEqual(run[run.index("--pids-limit") + 1], "64")
        self.assertEqual(run[run.index("--cap-drop") + 1], "ALL")
        self.assertIn("--read-only", run)
        self.assertIn("no-new-privileges", run)
        self.assertEqual(run[run.index("--user") + 1], f"{os.getuid()}:{os.getgid()}")
        self.assertEqual(run[run.index("--cpus") + 1], "1.0")
        volume = run[run.index("--volume") + 1]
        self.assertTrue(volume.endswith(":/workspace"))
        self.assertIn(str(self.ws.resolve()), volume)
        self.assertEqual(run[run.index("--workdir") + 1], "/workspace")
        # хвост: <образ> python -B -c <код>
        self.assertEqual(run[-4:-1], ["python", "-B", "-c"])
        self.assertEqual(run[-1], "print(2+2)")

    def test_container_image_from_config(self):
        self.agent.cfg.exec_docker_image = "python:3.11-slim"
        self.agent.dispatch("execute_python", {"code": "print(1)"})
        run = self._run_call()
        self.assertIn("python:3.11-slim", run)

    def test_env_overrides_reach_container(self):
        self.agent.cfg.exec_docker_cpus = 0.5
        self.agent.cfg.exec_docker_pids = 32
        self.agent.cfg.exec_mem_mb = 256
        self.agent.dispatch("execute_python", {"code": "print(1)"})
        run = self._run_call()
        self.assertEqual(run[run.index("--cpus") + 1], "0.5")
        self.assertEqual(run[run.index("--pids-limit") + 1], "32")
        self.assertEqual(run[run.index("--memory") + 1], "256m")


class TestContainerBehaviour(DockerShimCase):
    def test_output_and_exit_code(self):
        out = self.agent.dispatch("execute_python", {"code": "print(2+2)"})
        self.assertIn("4", out)
        self.assertIn("exit=0", out)
        self.assertIn("docker ua-sbx-", out)

    def test_stderr_and_nonzero_exit_surfaces(self):
        out = self.agent.dispatch(
            "execute_python",
            {"code": "import sys; sys.stderr.write('беда'); sys.exit(3)"},
        )
        self.assertIn("exit=3", out)
        self.assertIn("беда", out)

    def test_workspace_visible_and_writable(self):
        (self.ws / "маркер.txt").write_text("видно", encoding="utf-8")
        out = self.agent.dispatch(
            "execute_python",
            {"code": "print(open('маркер.txt', encoding='utf-8').read())"},
        )
        self.assertIn("видно", out)
        self.agent.dispatch(
            "execute_python",
            {"code": "open('внутри.txt', 'w', encoding='utf-8').write('изнутри')"},
        )
        self.assertEqual((self.ws / "внутри.txt").read_text(encoding="utf-8"), "изнутри")

    def test_timeout_kills_container(self):
        os.environ["FAKE_DOCKER_RUN_SLEEP"] = "8"
        try:
            out = self.agent.dispatch(
                "execute_python", {"code": "FAKE_SLEEP  # висим", "timeout_sec": 1}
            )
        finally:
            os.environ.pop("FAKE_DOCKER_RUN_SLEEP", None)
        self.assertIn("TIMEOUT", out)
        kills = [c for c in self._argv_calls() if c[:1] == ["kill"]]
        self.assertEqual(len(kills), 1)
        self.assertTrue(kills[0][1].startswith("ua-sbx-"))


class TestDockerDispatch(unittest.TestCase):
    """Маршрутизация: доступность, откат, выключенный режим, конфиг из env."""

    def setUp(self):
        self.bin_dir = Path(tempfile.mkdtemp(prefix="cua-docker-bin-"))
        self.log = self.bin_dir / "argv.log"

    def tearDown(self):
        ua._DOCKER_READY = None
        os.environ.pop("FAKE_DOCKER_LOG", None)

    def _install_shim(self) -> None:
        launcher = self.bin_dir / "docker"
        launcher.write_text(
            "#!/usr/bin/env python3\n"
            "import runpy\n"
            f"runpy.run_path(r'{FAKE_DOCKER_SRC}', run_name='__main__')\n",
            encoding="utf-8",
        )
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def test_probe_positive_with_healthy_docker(self):
        self._install_shim()
        os.environ["FAKE_DOCKER_LOG"] = str(self.log)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.bin_dir}{os.pathsep}{old_path}"
        try:
            self.assertTrue(ua._docker_available())
        finally:
            os.environ["PATH"] = old_path
        self.assertTrue(self.log.exists())             # был вызов docker version

    def test_probe_negative_without_docker(self):
        empty = self.bin_dir / "empty"
        empty.mkdir()
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(empty)                # docker-а нет — будет FileNotFoundError
        try:
            self.assertFalse(ua._docker_available())
        finally:
            os.environ["PATH"] = old_path

    def test_fallback_to_local_limits_when_docker_broken(self):
        agent, _ = build_agent(exec_docker=True)
        ua._DOCKER_READY = False                       # healthcheck «упал»
        out = agent.dispatch("execute_python", {"code": "print(2+2)"})
        self.assertIn("docker недоступен", out)
        self.assertIn("exit=0", out)

    def test_docker_disabled_by_default(self):
        self._install_shim()
        os.environ["FAKE_DOCKER_LOG"] = str(self.log)
        agent, _ = build_agent()                       # exec_docker=False
        out = agent.dispatch("execute_python", {"code": "print(2+2)"})
        self.assertIn("exit=0", out)
        self.assertNotIn("docker", out)
        self.assertFalse(self.log.exists())            # фейковый docker ни разу не позван

    def test_config_from_env(self):
        saved = {k: os.environ.get(k) for k in
                 ("UA_EXEC_DOCKER", "UA_EXEC_DOCKER_IMAGE",
                  "UA_EXEC_DOCKER_CPUS", "UA_EXEC_DOCKER_PIDS", "UA_EXEC_MEM_MB")}
        try:
            os.environ["UA_EXEC_DOCKER"] = "1"
            os.environ["UA_EXEC_DOCKER_IMAGE"] = "python:3.11-slim"
            os.environ["UA_EXEC_DOCKER_CPUS"] = "0.5"
            os.environ["UA_EXEC_DOCKER_PIDS"] = "32"
            os.environ["UA_EXEC_MEM_MB"] = "256"
            cfg = ua.AgentConfig.from_env()
            self.assertTrue(cfg.exec_docker)
            self.assertEqual(cfg.exec_docker_image, "python:3.11-slim")
            self.assertEqual(cfg.exec_docker_cpus, 0.5)
            self.assertEqual(cfg.exec_docker_pids, 32)
            self.assertEqual(cfg.exec_mem_mb, 256)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
