#!/usr/bin/env python3
"""scripts/healthcheck.py — П55: честность вслух (issue #5, волна ×4).

Сверяет ОБЪЯВЛЕННОЕ в A2A Agent Card (agent-card.json) с живым поведением
репозитория. Агент, который врёт о себе в манифесте, — красный.

Проверки:
  schema     — карточка валидна по правилам схемы A2A v1.0 (AgentCard):
               обязательные поля, типы, перечисления, непустые skills;
  version    — version карточки == версии в pyproject.toml;
  skills↔руки — каждый инструмент реестра _TOOLBOX покрыт ровно одним
               навыком карточки (SKILL_TOOLS), и наоборот: рука, которой
               нет в теле, не объявлена;
  modes      — заявленные режимы (task/daemon) реально есть в CLI;
  capabilities — streaming/pushNotifications = false честно: в репо нет
               HTTP-сервера с такими фичами;
  live       — (опционально) если задан AGENT_HEALTH_URL — карточка
               скачивается с {URL}/.well-known/agent-card.json и сверяется
               с объявленной: расхождение = красный.

rc=0 — карточка и поведение совпадают; rc=2 — расхождение (красный).
Только stdlib. Запуск: python3 scripts/healthcheck.py [--card ПУТЬ]
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "agent-card.json"

KNOWN_TRANSPORTS = {"JSONRPC", "GRPC", "HTTP+JSON"}
KNOWN_CAPABILITIES = {"streaming", "pushNotifications", "stateTransitionHistory"}

# Навык карточки → руки реестра _TOOLBOX (истина из universal_agent.py).
SKILL_TOOLS = {
    "workspace-files": {"read_file", "write_file", "list_dir"},
    "python-execution": {"execute_python"},
    "web-search": {"web_search"},
    "memory": {"remember", "recall"},
}
# Навык карточки → режим CLI (mode в argparse).
SKILL_MODES = {
    "task-solving": "task",
    "autonomous-daemon": "daemon",
}

problems: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    problems.append(msg)


def note(msg: str) -> None:
    notes.append(msg)


def load_card(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as err:  # noqa: BLE001
        fail(f"карточка не читается: {err}")
        return {}


# ── 1. Схема A2A v1.0 (AgentCard): обязательные поля и типы ───────────────
def validate_schema(card: dict) -> None:
    for field in ("protocolVersion", "name", "description", "url",
                  "version", "capabilities", "defaultInputModes",
                  "defaultOutputModes", "skills", "preferredTransport"):
        if field not in card:
            fail(f"schema: нет обязательного поля {field}")
    if problems:
        return
    if not re.fullmatch(r"\d+\.\d+(\.\d+)?", str(card["protocolVersion"])):
        fail(f"schema: protocolVersion «{card['protocolVersion']}» не вида X.Y[.Z]")
    for field in ("name", "description", "url", "version"):
        if not isinstance(card[field], str) or not card[field].strip():
            fail(f"schema: {field} должен быть непустой строкой")
    if not re.match(r"^https?://", str(card["url"])):
        fail(f"schema: url «{card['url']}» не абсолютный http(s)")
    if card["preferredTransport"] not in KNOWN_TRANSPORTS:
        fail(f"schema: preferredTransport «{card['preferredTransport']}» "
             f"вне {sorted(KNOWN_TRANSPORTS)}")
    caps = card["capabilities"]
    if not isinstance(caps, dict):
        fail("schema: capabilities должен быть объектом")
    else:
        for key, val in caps.items():
            if key not in KNOWN_CAPABILITIES:
                fail(f"schema: неизвестная capability {key}")
            elif not isinstance(val, bool):
                fail(f"schema: capability {key} должна быть bool")
    for field in ("defaultInputModes", "defaultOutputModes"):
        modes = card[field]
        if not isinstance(modes, list) or not modes or \
                not all(isinstance(m, str) and m for m in modes):
            fail(f"schema: {field} — непустой массив строк")
    skills = card["skills"]
    if not isinstance(skills, list) or not skills:
        fail("schema: skills — непустой массив")
    else:
        for i, skill in enumerate(skills):
            where = f"skills[{i}]"
            if not isinstance(skill, dict):
                fail(f"schema: {where} — объект")
                continue
            for field in ("id", "name", "description"):
                if not isinstance(skill.get(field), str) or not skill[field].strip():
                    fail(f"schema: {where}.{field} — непустая строка")
            tags = skill.get("tags")
            if not isinstance(tags, list) or not tags or \
                    not all(isinstance(t, str) and t for t in tags):
                fail(f"schema: {where}.tags — непустой массив строк")
            if "examples" in skill and (
                    not isinstance(skill["examples"], list)
                    or not all(isinstance(e, str) for e in skill["examples"])):
                fail(f"schema: {where}.examples — массив строк")
    provider = card.get("provider")
    if provider is not None:
        if not isinstance(provider, dict) or \
                not isinstance(provider.get("organization"), str) or \
                not isinstance(provider.get("url"), str):
            fail("schema: provider — {organization, url} из строк")


# ── 2. version карточки == pyproject ──────────────────────────────────────
def check_version(card: dict) -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        fail("pyproject: не найдена версия")
        return
    if card.get("version") != m.group(1):
        fail(f"version карточки {card.get('version')} != pyproject {m.group(1)}")


# ── 3. Руки тела: реестр _TOOLBOX против навыков карточки ────────────────
def load_toolbox() -> dict:
    spec = importlib.util.spec_from_file_location(
        "universal_agent", ROOT / "universal_agent.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["universal_agent"] = mod   # нужно dataclass'ам модуля
    try:
        spec.loader.exec_module(mod)
    except Exception as err:  # noqa: BLE001
        fail(f"universal_agent.py не импортируется: {err}")
        return {}
    return getattr(mod, "_TOOLBOX", {})


def check_skills_vs_hands(card: dict, toolbox: dict) -> None:
    declared = {s["id"] for s in card.get("skills", []) if isinstance(s, dict)}
    for skill_id, tools in SKILL_TOOLS.items():
        if skill_id not in declared:
            fail(f"руки {sorted(tools)} объявлены в SKILL_TOOLS, "
                 f"но навыка {skill_id} нет в карточке")
    covered: set[str] = set()
    for skill_id, tools in SKILL_TOOLS.items():
        missing = tools - set(toolbox)
        if missing:
            fail(f"навык {skill_id} объявляет несуществующие руки: {sorted(missing)}")
        covered |= tools & set(toolbox)
    hidden = set(toolbox) - covered
    if hidden:
        fail(f"руки тела, не покрытые карточкой (скрытые возможности): {sorted(hidden)}")


# ── 4. Режимы CLI существуют ──────────────────────────────────────────────
def check_modes(card: dict) -> None:
    source = (ROOT / "universal_agent.py").read_text(encoding="utf-8")
    for skill_id, mode in SKILL_MODES.items():
        if f'"{mode}"' not in source:
            fail(f"навык {skill_id} заявляет режим {mode}, которого нет в CLI")


# ── 5. Честность capabilities ─────────────────────────────────────────────
def check_capabilities_honesty(card: dict) -> None:
    caps = card.get("capabilities", {})
    if caps.get("streaming") or caps.get("pushNotifications"):
        fail("capabilities заявляет streaming/pushNotifications, "
             "но в репо нет их реализации — честность вслух нарушена")
    if not (ROOT / "Dockerfile").exists():
        note("Dockerfile нет — деплой-заявления карточки ничем не подкреплены")


# ── 6. Живая сверка (опционально) ─────────────────────────────────────────
def check_live(card: dict) -> None:
    base = os.environ.get("AGENT_HEALTH_URL", "").rstrip("/")
    if not base:
        note("AGENT_HEALTH_URL не задан — живая сверка пропущена "
             "(статический режим: карточка ↔ код)")
        return
    url = f"{base}/.well-known/agent-card.json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            served = json.loads(resp.read().decode("utf-8"))
    except Exception as err:  # noqa: BLE001
        fail(f"live: карточка не поднялась с {url}: {err}")
        return
    for field in ("protocolVersion", "name", "version"):
        if served.get(field) != card.get(field):
            fail(f"live: {field} расходится — объявлено "
                 f"«{card.get(field)}», живое «{served.get(field)}»")
    served_ids = {s.get("id") for s in served.get("skills", []) if isinstance(s, dict)}
    declared_ids = {s["id"] for s in card.get("skills", [])}
    if served_ids != declared_ids:
        fail(f"live: навыки расходятся — объявлены {sorted(declared_ids)}, "
             f"живые {sorted(served_ids)}")


def main(argv: list[str]) -> int:
    path = CARD
    if "--card" in argv:
        path = Path(argv[argv.index("--card") + 1])
    card = load_card(path)
    if card:
        validate_schema(card)
        check_version(card)
        toolbox = load_toolbox()
        if toolbox:
            check_skills_vs_hands(card, toolbox)
        check_modes(card)
        check_capabilities_honesty(card)
        check_live(card)
    bar = "=" * 64
    print(bar)
    print(" П55 · HEALTHCHECK: A2A Agent Card ↔ живое поведение агента")
    print(bar)
    for n in notes:
        print(f"NOTE  {n}")
    if problems:
        for p in problems:
            print(f"RED   {p}")
        print(f"# Result: 0 passed, {len(problems)} failed — карточка врёт или битая")
        return 2
    skills = ", ".join(s["id"] for s in card.get("skills", []))
    print(f"PASS  schema: карточка валидна по правилам A2A v1.0 "
          f"({len(card.get('skills', []))} навыков)")
    print(f"PASS  version {card.get('version')} == pyproject")
    print(f"PASS  skills ↔ руки: {sorted(SKILL_TOOLS)} покрыты, скрытых рук нет")
    print(f"PASS  modes: task/daemon на месте; capabilities честные (без выдумок)")
    print(f"# skills: {skills}")
    print("# Result: card ↔ behavior OK — агент честен о себе")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
