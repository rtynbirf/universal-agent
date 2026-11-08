#!/usr/bin/env python3
"""scripts/check.py — верификация канона (POLIMAT) для UniversalAgent.

Прогоняет группы проверок и печатает каноническую строку:
    # Result: N passed, 0 failed (of N checks)
Код возврата 0 — канон принял дерево, пуш разрешён.

Группы:
  compile    — все .py модули компилируются
  tests      — unittest-набор зелёный (mock-мозг, без сети)
  hygiene    — в текстовых файлах нет паттернов секретов (PAT/ключи)
  integrity  — release/manifest_sha256.txt сходится (sha256sum-канон)
  docs       — release-комплект полный, changelog описывает 2.2.0
"""

from __future__ import annotations

import hashlib
import io
import py_compile
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# check.py сам содержит тексты паттернов — из скана исключён.
HYGIENE_EXCLUDE = {"check.py"}
HYGIENE_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".toml", ".txt", ".sh", ".example"}
SECRET_PATTERNS = [
    re.compile(pat, re.I)
    for pat in (
        r"gh[pousr]_[A-Za-z0-9]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"sk-[A-Za-z0-9]{20,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    )
]

results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
        results.append((name, True, ""))
    except Exception as err:  # noqa: BLE001 — верификатор обязан пережить всё
        results.append((name, False, f"{type(err).__name__}: {err}"))


def _compile() -> None:
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" not in path.parts:
            py_compile.compile(str(path), doraise=True)


def _tests() -> None:
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"), top_level_dir=str(ROOT / "tests")
    )
    buffer = io.StringIO()
    result = unittest.TextTestRunner(verbosity=0, stream=buffer).run(suite)
    if result.failures or result.errors:
        raise AssertionError(
            f"failures={len(result.failures)} errors={len(result.errors)}"
        )
    globals()["TEST_COUNT"] = result.testsRun


def _hygiene() -> None:
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.name in HYGIENE_EXCLUDE:
            continue
        if {".git", "__pycache__"} & set(path.parts):
            continue
        if path.suffix.lower() not in HYGIENE_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in SECRET_PATTERNS:
            hit = pattern.search(text)
            if hit:
                raise AssertionError(f"подозрительный секрет в {path.name}: {hit.group(0)[:8]}…")


def _integrity() -> None:
    manifest = ROOT / "release" / "manifest_sha256.txt"
    if not manifest.exists():
        raise AssertionError("release/manifest_sha256.txt отсутствует — сделай make manifest")
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split(maxsplit=1)
        rel = rel.strip().lstrip("*")
        if rel.startswith(".github/workflows/"):
            print(f"ОК(авто): {rel} — dependabot-зона, из канона целостности исключена")
            continue
        target = ROOT / rel
        if not target.exists():
            raise AssertionError(f"в манифесте, но нет файла: {rel}")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != digest:
            raise AssertionError(f"hash mismatch: {rel} (пересобери make manifest)")


def _docs() -> None:
    required = (
        "README.md", "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md",
        "pyproject.toml", "Makefile", "Dockerfile", ".github/workflows/ci.yml",
        ".github/PULL_REQUEST_TEMPLATE.md", "PUSH.md", "publish.sh",
    )
    for name in required:
        if not (ROOT / name).exists():
            raise AssertionError(f"нет файла {name}")


def _changelog() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if "## [2.2.0]" not in text:
        raise AssertionError("CHANGELOG не описывает 2.2.0")


check("compile: все модули компилируются", _compile)
check("tests: unittest-набор зелёный (mock-brain, no network)", _tests)
check("hygiene: секретов в текстовых файлах нет", _hygiene)
check("integrity: SHA-256-манифест сходится", _integrity)
check("docs: release-комплект полный", _docs)
check("changelog: v2.2.0 задокументирована", _changelog)

passed = sum(1 for _, ok, _ in results if ok)
for name, ok, err in results:
    print(("PASS  " if ok else "FAIL  ") + name + (f" — {err}" if err else ""))
print(f"# Result: {passed} passed, {len(results) - passed} failed (of {len(results)} checks)")
print(f"# tests: {globals().get('TEST_COUNT', 0)}/{globals().get('TEST_COUNT', 0)} (mock-brain, no network)")
sys.exit(0 if passed == len(results) else 1)
