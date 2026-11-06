"""Общий помощник тестов: агент во временных каталогах, без сети."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PARENT = Path(__file__).resolve().parents[1]
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from universal_agent import AgentConfig, UniversalAgent  # noqa: E402


def build_agent(**overrides) -> tuple[UniversalAgent, Path]:
    """Собрать ЦУА во временном каталоге. Адрес мозга — заглушка:
    юнит-тесты не должны вызывать generate() ни разу."""
    tmp = Path(tempfile.mkdtemp(prefix="cua-test-"))
    cfg = AgentConfig(
        llm_base_url="http://127.0.0.1:9/v1",
        llm_model="mock",
        workspace_root=str(tmp / "workspace"),
        memory_db=str(tmp / "memory" / "agent.db"),
        knowledge_dir=str(tmp / "knowledge"),
        results_log=str(tmp / "results.log"),
        queue_path=str(tmp / "task_queue.txt"),
        daemon_poll_sec=1,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    agent = UniversalAgent(cfg)
    return agent, tmp
