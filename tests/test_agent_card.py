# -*- coding: utf-8 -*-
"""П55 · тесты A2A Agent Card и healthcheck (issue #5).

Карточка валидна, версия сходит с pyproject, healthcheck зелёный;
расхождение карточки с поведением = красный (rc=2).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "agent-card.json"
HEALTHCHECK = ROOT / "scripts" / "healthcheck.py"


class TestAgentCard(unittest.TestCase):
    def test_card_is_valid_json_with_skills(self):
        card = json.loads(CARD.read_text(encoding="utf-8"))
        self.assertEqual(card["protocolVersion"], "1.0")
        self.assertTrue(card["name"] and card["description"])
        self.assertTrue(card["skills"], "навыки карточки не должны быть пусты")

    def test_card_version_matches_pyproject(self):
        card = json.loads(CARD.read_text(encoding="utf-8"))
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1)
        self.assertEqual(card["version"], version)

    def test_healthcheck_green(self):
        proc = subprocess.run(
            [sys.executable, str(HEALTHCHECK)],
            capture_output=True, text=True, cwd=str(ROOT), timeout=120,
        )
        self.assertEqual(proc.returncode, 0,
                         f"healthcheck красный:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("card ↔ behavior OK", proc.stdout)

    def test_card_behavior_mismatch_is_red(self):
        card = json.loads(CARD.read_text(encoding="utf-8"))
        card["version"] = "0.0.0"                      # ложь о версии
        card["skills"].append({                        # ложь о несуществующей руке
            "id": "teleportation",
            "name": "Телепортация",
            "description": "Заявлена рука, которой в теле нет.",
            "tags": ["tool", "fiction"],
        })
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "agent-card.json"
            fake.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(HEALTHCHECK), "--card", str(fake)],
                capture_output=True, text=True, cwd=str(ROOT), timeout=120,
            )
        self.assertEqual(proc.returncode, 2, "расхождение карточки и поведения должно быть красным")


if __name__ == "__main__":
    unittest.main()
