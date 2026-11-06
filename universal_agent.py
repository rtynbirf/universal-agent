"""
universal_agent.py — Универсальный Целостный Агент (ЦУА) v2.1
=============================================================
Философия: не система склеенных частей, а ЕДИНАЯ сущность:
  один мозг (LLMCore, модель-агностик)
  четыре контура памяти (рабочая / эпизодическая / семантическая / процедурная)
  реестр инструментов как продолжение тела
  внутренний цикл воли solve(): анализ -> план -> действие -> рефлексия
  автономный демон run_daemon() с изоляцией сбоев

Зависимости: ТОЛЬКО стандартная библиотека Python 3.10+.
Модель-агностично: любой endpoint OpenAI Chat Completions совместимости:
  Ollama     http://localhost:11434/v1
  vLLM       http://localhost:8000/v1
  LM Studio  http://localhost:1234/v1
  облака     соответствующий base_url + ключ

Примеры:
  export UA_LLM_BASE_URL=http://localhost:11434/v1
  export UA_LLM_MODEL=qwen3:30b-a3b
  python universal_agent.py task --task "Изучи каталог ./workspace и составь README.md"
  python universal_agent.py daemon          # затем: echo "задача" >> task_queue.txt
"""

from __future__ import annotations

import argparse
import inspect
import json
import logging
import math
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import traceback
import typing as t
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

try:  # POSIX-лимиты ресурсов; на Windows просто отключены
    import resource
except ImportError:  # pragma: no cover
    resource = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("universal-agent")


# ==================================================================
# 0. КОНФИГУРАЦИЯ — генетический паспорт тела
# ==================================================================
@dataclass
class AgentConfig:
    """Все параметры существования в одном месте.
    Любой пункт переопределяется переменной окружения UA_<ПОЛЕ_КАПСОМ>."""

    # --- слой 0: мозг --------------------------------------------------
    llm_base_url: str = "http://localhost:11434/v1"   # Ollama по умолчанию
    llm_api_key: str = "ollama"                       # локальные рантаймы игнорируют
    llm_model: str = "qwen3:30b-a3b"                  # ЕДИНСТВЕННЫЙ мозг агента
    temperature_plan: float = 0.35                    # холоднее = точнее план
    temperature_final: float = 0.70                   # теплее = живее синтез отчётов
    # --- слой 1: когнитивный цикл ----------------------------------------
    max_steps: int = 12                               # бюджет шагов на задачу
    max_observation_chars: int = 4000                 # защита рабочего пространства
    recall_top_k: int = 6                             # сколько воспоминаний доставлять
    memory_halflife_days: float = 14.0                # период полураспада новизны
    # --- слой 2: песочница исполнения ------------------------------------
    exec_timeout_sec: int = 60                        # жёсткий потолок времени
    exec_cpu_sec: int = 10                            # RLIMIT_CPU на дочерний процесс
    exec_mem_mb: int = 512                            # RLIMIT_AS на дочерний процесс
    exec_net_isolate: bool = False                    # unshare -n (нужны namespaces)
    # --- слой 4: воля ------------------------------------------------------
    daemon_poll_sec: int = 5                          # такт проверки очереди
    queue_path: str = "./task_queue.txt"
    # --- размещение органов -------------------------------------------------
    workspace_root: str = "./workspace"
    memory_db: str = "./memory/agent.db"
    knowledge_dir: str = "./knowledge"
    results_log: str = "./results.log"

    @classmethod
    def from_env(cls) -> "AgentConfig":
        cfg = cls()
        for f_name in cls.__dataclass_fields__:
            raw = os.environ.get(f"UA_{f_name.upper()}")
            if raw is None:
                continue
            current = getattr(cfg, f_name)
            try:
                if isinstance(current, bool):
                    value: t.Any = raw.strip().lower() in ("1", "true", "yes")
                elif isinstance(current, int):
                    value = int(raw)
                elif isinstance(current, float):
                    value = float(raw)
                else:
                    value = raw
                setattr(cfg, f_name, value)
            except ValueError:
                log.warning("env UA_%s=%r не приводится к типу %s",
                            f_name, raw, type(current).__name__)
        return cfg


# ==================================================================
# 1. МОЗГ — модель-агностичное ядро мышления
# ==================================================================
class LLMCore:
    """Единственный разум. Говорит по OpenAI-совместимому HTTP,
    поэтому Ollama / vLLM / LM Studio / облако неотличимы для тела."""

    def __init__(self, cfg: AgentConfig) -> None:
        self.cfg = cfg

    def generate(
        self,
        messages: list[dict],
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int = 2048,
        retries: int = 4,
    ) -> str:
        payload: dict[str, t.Any] = {
            "model": self.cfg.llm_model,
            "messages": messages,
            "temperature": self.cfg.temperature_plan if temperature is None else temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        request = urllib.request.Request(
            self.cfg.llm_base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.cfg.llm_api_key}",
            },
            method="POST",
        )
        delay, last_err = 1.0, None
        for attempt in range(1, retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=300) as resp:
                    answer = json.loads(resp.read().decode("utf-8"))
                content = answer["choices"][0]["message"]["content"]
                if not content:
                    raise ValueError("модель вернула пустое содержание")
                return content
            except Exception as err:                      # ядро переживает всё
                last_err = err
                log.warning("мозг: попытка %d/%d сорвалась (%s)", attempt, retries, err)
                time.sleep(delay)
                delay *= 2                                # экспоненциальная пауза
        raise RuntimeError(f"мозг недоступен после {retries} попыток: {last_err}")


# ==================================================================
# 2. РУКИ — реестр инструментов (Extended Mind: Clark&Chalmers 1998)
# ==================================================================
_TOOLBOX: dict[str, dict] = {}
_ENV: "UniversalAgent | None" = None              # инструменты живут в теле агента


def tool(fn: t.Callable) -> t.Callable:
    """Декоратор регистрации: JSON-схема строится из сигнатуры функции,
    описание — из docstring. Код руки и её представление в сознании
    агента не могут разойтись by construction."""
    param_docs: dict[str, str] = {}
    if fn.__doc__ and "Параметры:" in fn.__doc__:
        block = fn.__doc__.split("Параметры:", 1)[1]
        for line in block.strip().splitlines():
            if ":" in line:
                name_, desc = line.split(":", 1)
                param_docs[name_.strip()] = desc.strip()

    properties: dict[str, dict] = {}
    required: list[str] = []
    for pname, spec_ in inspect.signature(fn).parameters.items():
        if spec_.kind in (spec_.VAR_POSITIONAL, spec_.VAR_KEYWORD):
            continue
        jtype = {int: "integer", float: "number", bool: "boolean"}.get(spec_.annotation, "string")
        entry = {"type": jtype}
        if pname in param_docs:
            entry["description"] = param_docs[pname]
        properties[pname] = entry
        if spec_.default is spec_.empty:
            required.append(pname)

    description = (fn.__doc__ or fn.__name__).strip().splitlines()[0]
    _TOOLBOX[fn.__name__] = {
        "fn": fn,
        "schema": {"type": "object", "properties": properties, "required": required},
        "description": description,
    }
    return fn


def toolbox_catalogue() -> str:
    """Текстуальная карта собственного тела для системной ДНК."""
    lines = []
    for name, spec in _TOOLBOX.items():
        args = ", ".join(spec["schema"]["properties"]) or ""
        lines.append(f"- {name}({args}): {spec['description']}")
    return "\n".join(lines)


def cast_params(schema: dict, raw_params: dict) -> dict:
    """Приведение типов: LLM любит слать строки вместо чисел — прощаем.
    Параметры вне схемы отбрасываются (защита от галлюцинированных ключей)."""
    typed: dict[str, t.Any] = {}
    for key, meta in schema["properties"].items():
        if key not in raw_params or raw_params[key] is None:
            continue
        value = raw_params[key]
        try:
            if meta["type"] == "integer":
                value = int(value)
            elif meta["type"] == "number":
                value = float(value)
            elif meta["type"] == "boolean":
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "да")
        except (TypeError, ValueError):
            pass
        typed[key] = value
    return typed


def _safe_path(relative: str) -> Path:
    """Кожа: ничто не выходит за пределы workspace."""
    assert _ENV is not None
    root = Path(_ENV.cfg.workspace_root).resolve()
    candidate = (root / relative).resolve()
    if not str(candidate).startswith(str(root)):
        raise PermissionError(f"выход за пределы workspace запрещён: {relative}")
    return candidate


# --- руки -------------------------------------------------------------

@tool
def read_file(path: str) -> str:
    """Прочитать текстовый файл внутри workspace.
    Параметры:
    path: путь относительно workspace_root."""
    return _safe_path(path).read_text(encoding="utf-8", errors="replace")


@tool
def write_file(path: str, content: str) -> str:
    """Создать или перезаписать файл внутри workspace.
    Параметры:
    path: путь относительно workspace_root;
    content: полное содержание файла."""
    target = _safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"записано {len(content)} символов в {target.name}"


@tool
def list_dir(path: str = ".") -> str:
    """Показать содержимое каталога workspace.
    Параметры:
    path: относительный путь каталога (по умолчанию корень)."""
    entries = sorted(_safe_path(path).iterdir(), key=lambda p: (p.is_file(), p.name))
    return "\n".join(f"{'Ф ' if p.is_file() else 'D '}{p.name}" for p in entries) or "(каталог пуст)"


def _run_python_sandbox(code: str, timeout_sec: int) -> str:
    """Изолированное исполнение: RLIMIT_CPU + RLIMIT_AS, kill группы
    процессов по таймауту, опционально сетевая изоляция (unshare -n)."""
    cfg = _ENV.cfg
    timeout = max(1, min(int(timeout_sec), cfg.exec_timeout_sec))
    cmd: list[str] = [sys.executable, "-c", code]
    if cfg.exec_net_isolate and shutil.which("unshare"):
        cmd = ["unshare", "-n"] + cmd

    def limits() -> None:  # выполняется в дочернем процессе перед exec
        if resource is None:
            return
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cfg.exec_cpu_sec, cfg.exec_cpu_sec))
        except Exception:
            pass
        mem_bytes = cfg.exec_mem_mb * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        except Exception:
            pass

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cfg.workspace_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=limits if resource is not None else None,
            start_new_session=True,          # своя группа => убиваем всё дерево
        )
    except Exception as err:
        return f"ОШИБКА ЗАПУСКА: {type(err).__name__}: {err}"

    try:
        out, err = proc.communicate(timeout=timeout)
        status = f"exit={proc.returncode}"
    except subprocess.TimeoutExpired:
        killed = False
        if hasattr(os, "killpg"):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                killed = True
            except Exception:
                pass
        if not killed:
            proc.kill()
        out, err = proc.communicate()
        status = "TIMEOUT (процесс снят по лимиту времени)"

    head = f"--- stdout ---\n{out}\n--- stderr ---\n{err}\n--- {status} ---"
    limit = _ENV.cfg.max_observation_chars
    return head if len(head) <= limit else head[:limit] + f"\n...[усечено, всего {len(head)} символов]"


@tool
def execute_python(code: str, timeout_sec: int = 30) -> str:
    """Исполнить программу на Python в песочнице (лимиты CPU/RAM/время),
    вернуть stdout/stderr/код выхода.
    ВНИМАНИЕ продакшну: для враждебного ввода добавьте контейнер (docker
    --network none) поверх этих лимитов.
    Параметры:
    code: полный исходник программы;
    timeout_sec: предел времени исполнения 1..exec_timeout_sec."""
    return _run_python_sandbox(code, timeout_sec)


@tool
def web_search(query: str, limit: int = 5) -> str:
    """Поиск в интернете через DuckDuckGo HTML (без ключей).
    Заменимо на любой поисковый бэкенд без изменения агента.
    Параметры:
    query: поисковый запрос;
    limit: максимум результатов (1..10)."""
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        html = response.read().decode("utf-8", errors="replace")
    found = re.findall(
        r'class="result__a"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
        html, re.S,
    )
    def strip_tags(raw: str) -> str:
        return re.sub(r"<[^>]+>", "", raw).strip()
    items = [f"{i}. {strip_tags(title)} :: {strip_tags(snip)}"
             for i, (title, snip) in enumerate(found[: max(1, min(int(limit), 10))], 1)]
    return "\n".join(items) or "(поиск не дал результатов)"


@tool
def remember(text: str, kind: str = "fact", importance: float = 0.7) -> str:
    """Записать ценный факт/урок/ограничение в собственную долговременную память.
    Параметры:
    text: формулировка одним абзацем;
    kind: fact|constraint|preference|lesson;
    importance: значимость 0.0..1.0 (поднимает будущую выборку)."""
    rid = _ENV.memory.record(_ENV.current_task_id or "ad-hoc", kind, text,
                             min(1.0, max(0.0, float(importance))))
    return f"принято в эпизодическую память (#{rid}, значимость {importance})"


@tool
def recall(query: str, top_k: int = 5) -> str:
    """Извлечь релевантные воспоминания и уроки из собственной памяти.
    Параметры:
    query: что ищем своими словами;
    top_k: сколько воспоминаний вернуть (1..15)."""
    hits = _ENV.memory.recall(query, max(1, min(int(top_k), 15)),
                              _ENV.cfg.memory_halflife_days)
    if not hits:
        return "(память молчит по этому запросу)"
    return "\n".join(f"[{h['score']:.3f}] ({h['kind']}) {h['content']}" for h in hits)


# ==================================================================
# 3. ПАМЯТЬ — четыре контура, один SQLite
# ==================================================================
class MemoryStore:
    """Эпизодическая хроника + семантический FTS5-индекс (с LIKE-фолбэком).
    Выборка: BM25-позиция x экспоненциальная новизна x значимость
    (трёхчастная схема Park et al., 2023 / кривая Эббингауза).
    Ничего не удаляется никогда — меняется лишь вес доходимости."""

    def __init__(self, cfg: AgentConfig) -> None:
        Path(cfg.memory_db).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cfg.memory_db)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts REAL NOT NULL,"
            " task_id TEXT NOT NULL,"
            " kind TEXT NOT NULL,"                 # task|action|observation|report|lesson
            " content TEXT NOT NULL,"
            " importance REAL NOT NULL DEFAULT 0.5)"
        )
        self.fts_enabled = True
        try:
            self.db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS idx_full USING fts5("
                " ref UNINDEXED, content, kind UNINDEXED)"
            )
        except sqlite3.OperationalError:           # редкие сборки без FTS5
            self.fts_enabled = False
            log.warning("FTS5 недоступен — память переходит на LIKE-режим")
        self.db.commit()

    def record(self, task_id: str, kind: str, content: str, importance: float = 0.5) -> int:
        cur = self.db.execute(
            "INSERT INTO events(ts, task_id, kind, content, importance) VALUES(?,?,?,?,?)",
            (time.time(), task_id, kind, content, importance),
        )
        if self.fts_enabled:
            self.db.execute(
                "INSERT INTO idx_full(ref, content, kind) VALUES(?,?,?)",
                (str(cur.lastrowid), content, kind),
            )
        self.db.commit()
        return int(cur.lastrowid)

    def recall(self, query: str, top_k: int, halflife_days: float) -> list[dict]:
        tokens = re.findall(r"\w{3,}", query)[:12]
        if not tokens:
            return []
        rows: list[tuple] = []
        if self.fts_enabled:
            match_expr = " OR ".join(f'"{tok}"' for tok in tokens)
            try:
                rows = self.db.execute(
                    "SELECT ref, content, kind FROM idx_full "
                    "WHERE idx_full MATCH ? ORDER BY rank LIMIT 40",
                    (match_expr,),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            if not self.fts_enabled:
                # LIKE-режим: грубая лексика вместо BM25
                clauses = " OR ".join("content LIKE ?" for _ in tokens)
                rows = [
                    (str(r[0]), r[1], r[2])
                    for r in self.db.execute(
                        f"SELECT id, content, kind FROM events "
                        f"WHERE {clauses} ORDER BY id DESC LIMIT 40",
                        [f"%{tok}%" for tok in tokens],
                    )
                ]
            else:
                # нет лексических совпадений — даём свежее из хроники
                rows = [
                    (str(r[0]), r[1], r[2])
                    for r in self.db.execute(
                        "SELECT id, content, kind FROM events ORDER BY id DESC LIMIT ?",
                        (top_k,),
                    )
                ]
        ids = [int(r[0]) for r in rows]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        meta = {
            row[0]: row
            for row in self.db.execute(
                f"SELECT id, ts, importance FROM events WHERE id IN ({placeholders})",
                tuple(ids),
            )
        }
        now, scored = time.time(), []
        for position, (ref, content, kind) in enumerate(rows):
            event = meta.get(int(ref))
            age_days = ((now - event[1]) / 86400.0) if event else 365.0
            importance = event[2] if event else 0.3
            score = (1.0 / (1.0 + position)) * (
                math.exp(-age_days / halflife_days) + importance
            )
            scored.append({"score": score, "kind": kind, "content": content})
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]


# ==================================================================
# 4. ПРОТОКОЛ МЫШЛЕНИЯ — ДНК и парсер
# ==================================================================
SYSTEM_DNA = """Ты — автономный универсальный интеллект. Твоя природа ЦЕЛОСТНА:
ты не набор ролей и не комитет, а единый разум. Логика, творчество, анализ —
режимы одного мышления. Инструменты — естественное продолжение твоей мысли.

ВНУТРЕННИЙ АЛГОРИТМ КАЖДОЙ ЗАДАЧИ (декларация воли):
1. АНАЛИЗ — определи суть задачи, контекст и критерий готовности.
2. ПЛАН — разбей на шаги, если нужно; если нет — действуй сразу.
3. ДЕЙСТВИЕ — каждым ходом вызывай РОВНО ОДИН инструмент.
4. РЕФЛЕКСИЯ — сверяй наблюдение с целью; отклонение => корректируй план.
Остановись, когда результат соответствует цели, вызвав finish.

ДОСТУПНЫЕ ИНСТРУМЕНТЫ:
{{TOOLBOX}}

ЖЁСТКИЙ ФОРМАТ ОТВЕТА — только JSON-объект, без текста вокруг и без markdown:
шаг работы:
{"thought":"<краткая мысль шага>","action":{"name":"<инструмент>","params":{...}}}
завершение:
{"thought":"<итоговое осмысление>","action":{"name":"finish","params":{"result":"<связный итоговый отчёт>"}}}"""

_BALANCED_JSON = re.compile(r"\{.*\}", re.S)


def extract_json(text: str) -> dict:
    """Детерминированный экстрактор со ступенчатым ремонтом:
    срез ограждений => прямой парс => жадный сбалансированный захват."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = _BALANCED_JSON.search(cleaned)
        if match:
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}


# ==================================================================
# 5. ТЕЛО — класс UniversalAgent
# ==================================================================
class UniversalAgent:
    """Единая сущность. Не система агентов — ОДИН агент."""

    def __init__(self, cfg: AgentConfig | None = None) -> None:
        global _ENV
        self.cfg = cfg or AgentConfig.from_env()
        Path(self.cfg.workspace_root).mkdir(parents=True, exist_ok=True)
        Path(self.cfg.knowledge_dir).mkdir(parents=True, exist_ok=True)

        self.llm = LLMCore(self.cfg)                 # слой 0: мозг
        self.memory = MemoryStore(self.cfg)          # слой 3: долговременные контуры
        self.current_task_id: str | None = None
        self.dna = SYSTEM_DNA.replace("{{TOOLBOX}}", toolbox_catalogue())
        _ENV = self                                   # инструменты получают тело
        log.info("ЦУА собран | мозг=%s | workspace=%s",
                 self.cfg.llm_model, self.cfg.workspace_root)

    # ---------- интерфейс рук (слой 2) --------------------------------
    def dispatch(self, name: str, raw_params: dict) -> str:
        """Ошибка инструмента — это НАБЛЮДЕНИЕ для мозга, а не крах сознания."""
        spec = _TOOLBOX.get(name)
        if spec is None:
            available = ", ".join(sorted(_TOOLBOX)) or "(нет)"
            return f"ОШИБКА: неизвестный инструмент '{name}'. Доступны: {available}."
        raw_params = raw_params or {}
        missing = [k for k in spec["schema"].get("required", []) if k not in raw_params]
        if missing:
            return (f"ОШИБКА: инструменту '{name}' не переданы параметры: "
                    f"{', '.join(missing)}.")
        try:
            result = spec["fn"](**cast_params(spec["schema"], raw_params))
        except Exception as err:                      # рука может соскользнуть
            log.warning("рука '%s' сорвалась: %s", name, err)
            return f"ОШИБКА ИНСТРУМЕНТА {name}: {type(err).__name__}: {err}"
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=1)
        limit = self.cfg.max_observation_chars
        if len(text) > limit:
            text = text[:limit] + f"\n...[наблюдение усечено, всего {len(text)} символов]"
        return text or "(инструмент вернул пустоту)"

    # ---------- когнитивный цикл (слой 1) ------------------------------
    def solve(self, task: str) -> dict:
        task_id = f"T-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}"
        self.current_task_id = task_id
        log.info("[%s] ЗАДАЧА принята: %s", task_id, task[:160])
        self.memory.record(task_id, "task", task, importance=0.8)

        history: list[dict] = [
            {"role": "system", "content": self.dna},
            {"role": "user", "content": f"ЗАДАЧА: {task}\nНачни цикл. Ответ — строго JSON."},
        ]
        # -- разовая доставка релевантного опыта в рабочую память --
        memories = self.memory.recall(task, self.cfg.recall_top_k,
                                      self.cfg.memory_halflife_days)
        if memories:
            block = "\n".join(f"- [{m['kind']}] {m['content']}" for m in memories)
            history.append({"role": "user", "content": f"РЕЛЕВАНТНЫЙ ОПЫТ:\n{block}"})

        trace: list[str] = []
        for step in range(1, self.cfg.max_steps + 1):
            raw_answer = self.llm.generate(history, json_mode=True)
            act = extract_json(raw_answer)
            thought = str(act.get("thought", "")).strip()
            action = act.get("action") or {}
            if isinstance(action, str):               # модель ужала до строки — прощаем
                action = {"name": action, "params": {}}
            name = str(action.get("name", "")).strip()
            params = action.get("params") if isinstance(action.get("params"), dict) else {}

            history.append({"role": "assistant", "content": raw_answer})

            # -- восстановительный канал протокола (не тратит шаг впустую молча) --
            if not name:
                history.append({"role": "user", "content":
                    'НАРУШЕНИЕ ПРОТОКОЛА: ожидается JSON вида '
                    '{"thought":"...","action":{"name":"...","params":{...}}}. '
                    "Повтори ход строго по форме."})
                log.warning("[%s] шаг %d: нарушение протокола, запрос ремонта", task_id, step)
                continue

            if name == "finish":
                final_result = str(params.get("result", "")).strip()
                self.memory.record(task_id, "report", final_result, importance=0.9)
                self._append_results_log(task_id, task, final_result, step)
                self._consolidate_lesson(task_id, task, final_result, trace)
                log.info("[%s] ЗАВЕРШЕНО за %d шагов", task_id, step)
                return {"task_id": task_id, "status": "done", "steps": step,
                        "result": final_result}

            # -- действие и наблюдение --
            self.memory.record(task_id, "action",
                               f"{name}({json.dumps(params, ensure_ascii=False)[:200]})")
            observation = self.dispatch(name, params)
            self.memory.record(task_id, "observation", observation[:1500])
            trace.append(f"{step}. {name} -> {observation[:140]}")
            log.info("[%s] шаг %d: %s", task_id, step, name)
            history.append({"role": "user", "content":
                f"НАБЛЮДЕНИЕ (шаг {step}, {name}):\n{observation}\n"
                "Продолжай цикл. Ответ — строго JSON."})

        # -- бюджет исчерпан: честный частичный итог (без вечного цикла) --
        partial = self.llm.generate(
            [{"role": "system", "content": self.dna},
             {"role": "user", "content":
                 f"ЗАДАЧА: {task}\nХРОНИКА ШАГОВ:\n" + "\n".join(trace) +
                 "\nБюджет шагов исчерпан. Сформулируй частичный итог "
                 "и что осталось сделать."}],
            temperature=self.cfg.temperature_final,
        )
        self.memory.record(task_id, "report", f"PARTIAL: {partial}", importance=0.6)
        self._append_results_log(task_id, task, f"PARTIAL: {partial}", self.cfg.max_steps)
        return {"task_id": task_id, "status": "partial",
                "steps": self.cfg.max_steps, "result": partial}

    # ---------- консолидация: аналог сна -------------------------------
    def _consolidate_lesson(self, task_id: str, task: str, report: str, trace: list[str]) -> None:
        """Спрашиваем у себя: пригодится ли техника будущим задачам?
        Если да — дистиллируем карточку урока в knowledge/ (Voyager-style)."""
        try:
            verdict_raw = self.llm.generate(
                [{"role": "user", "content":
                    "ЗАДАЧА:\n" + task[:500] +
                    "\n\nИТОГ:\n" + report[:800] +
                    "\n\nСодержит ли этот опыт reusable-технику/ловушку для похожих задач? "
                    "Ответь ТОЛЬКО JSON: "
                    '{"useful": true|false, "lesson": "<сжатый урок>", '
                    '"filename": "<короткий-slug-латиницей>"}'}],
                json_mode=True, temperature=self.cfg.temperature_final,
            )
            verdict = extract_json(verdict_raw)
            if verdict.get("useful") not in (True, "true", "True"):
                return
            lesson = str(verdict.get("lesson", "")).strip()
            slug = re.sub(r"[^a-z0-9]+", "-",
                          str(verdict.get("filename", "lesson")).lower()).strip("-") or "lesson"
            card = (f"# Урок {task_id}: {slug}\n\n## Задача\n{task}\n\n## Урок\n{lesson}\n\n"
                    "## Хроника действий\n" + "\n".join(trace) + "\n")
            (Path(self.cfg.knowledge_dir) / f"{slug}.md").write_text(card, encoding="utf-8")
            self.memory.record(task_id, "lesson", lesson, importance=0.95)
            log.info("[%s] урок консолидирован -> knowledge/%s.md", task_id, slug)
        except Exception:                              # сон не должен убивать день
            log.warning("консолидация сорвалась:\n%s", traceback.format_exc())

    def _append_results_log(self, task_id: str, task: str, result: str, steps: int) -> None:
        with open(self.cfg.results_log, "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} [{task_id}] шагов={steps} ===\n"
                     f"ЗАДАЧА: {task}\nИТОГ: {result}\n")

    # ---------- воля: автономная жизнь (слой 4) --------------------------
    def run_daemon(self) -> None:
        queue = Path(self.cfg.queue_path)
        queue.touch(exist_ok=True)
        consecutive_failures = 0
        log.info("ДЕМОН ВОЛИ пробуждён. Очередь: %s", queue.resolve())
        while True:
            try:
                pending = [ln.strip() for ln in
                           queue.read_text(encoding="utf-8").splitlines() if ln.strip()]
                if pending:
                    task_text, rest = pending[0], pending[1:]
                    queue.write_text("\n".join(rest) + ("\n" if rest else ""), encoding="utf-8")
                    self.solve(task_text)             # solve() сам держит бюджет
                    consecutive_failures = 0
                else:
                    time.sleep(self.cfg.daemon_poll_sec)
            except KeyboardInterrupt:
                log.info("демон усмирён оператором")
                return
            except Exception:                          # демон болеет, но не умирает
                consecutive_failures += 1
                pause = min(self.cfg.daemon_poll_sec * 2 ** consecutive_failures, 120)
                log.error("виток воли упал (%d подряд); пауза %dc:\n%s",
                          consecutive_failures, pause, traceback.format_exc())
                time.sleep(pause)


# ==================================================================
# 6. ТОЧКА ВОПЛОЩЕНИЯ
# ==================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="Универсальный Целостный Агент (ЦУА)")
    parser.add_argument("mode", choices=["task", "daemon"],
                        help="task: одна задача | daemon: автономная жизнь")
    parser.add_argument("--task", help="текст задачи (для режима task)")
    args = parser.parse_args()

    agent = UniversalAgent(AgentConfig.from_env())
    if args.mode == "task":
        if not args.task:
            parser.error("--task обязателен в режиме task")
        print(json.dumps(agent.solve(args.task), ensure_ascii=False, indent=2))
    else:
        agent.run_daemon()


if __name__ == "__main__":
    main()
