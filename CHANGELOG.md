# Changelog

Все заметные изменения проекта документируются в этом файле.
Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версионирование — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

## [2.3.0] — 2026-11-07

### Added

- Контейнерная песочница исполнения (фаза II «Крепость»): `UA_EXEC_DOCKER=1`
  запускает код `execute_python` в одноразовом контейнере — `--network none`,
  cgroup-лимиты RAM/CPU/PID (с закрытым swap-побегом), `--read-only` корень ФС,
  `--cap-drop ALL`, `no-new-privileges`, не-root пользователь, workspace смонтирован
  внутрь, по таймауту контейнер снимается `docker kill`. Конфиг:
  `UA_EXEC_DOCKER_IMAGE` / `_CPUS` / `_PIDS`.
- Автоматический откат на локальную RLIMIT-песочницу с пометкой в наблюдении,
  если docker недоступен — агент не останавливается.
- Тесты контейнерной песочницы на фейковом docker CLI (жёсткость флагов,
  видимость/записываемость workspace, снятие по таймауту, откат, конфиг из env):
  набор вырос с 36 до 48 тестов.

### Changed

- `execute_python`: документация отражает оба режима; README — раздел
  «Контейнерная песочница», дорожная карта помечает первый пункт фазы II выполненным.

## [2.2.0] — 2026-11-07

### Added

- Release-комплект по канону POLIMAT: `Makefile` (verify/test/manifest/run/daemon), `scripts/check.py` (компиляция + unittest + гигиена секретов + SHA-256-манифест), `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md`, PR-шаблон.
- CI (`ci.yml`): тестовая матрица Python 3.10–3.12, hygiene-прогон, ruff, semantic-pull-request.
- `Dockerfile`: изолированный non-root рантайм агента (python:3.12-slim), volume-органы workspace/memory/knowledge.
- `pyproject.toml` (PEP 621): метаданные, точка входа `universal-agent`, конфиг ruff.

### Changed

- История git пересобрана в канон: единый squash-коммит `feat(agent)` с DCO + аннотированный тег `v2.2.0`.

### Security

- Гигиена токенов в CI: автоскан текстовых файлов на паттерны PAT/ключей (`scripts/check.py`); доступ к workspace жёстко ограничен `_safe_path`.

## [2.1.0] — 2026-11-07

### Added

- ЦУА v2.1: ядро `LLMCore` (модель-агностик, OpenAI-совместимый протокол), JSON-протокол действий с ремонтным каналом, четыре контура памяти (рабочая, эпизодическая SQLite, семантическая FTS5 с LIKE-фолбэком, процедурная дистилляция уроков), песочница исполнения (RLIMIT_CPU/AS, kill группы процессов, опциональный `unshare -n`), демон воли с изоляцией сбоев и экспоненциальным backoff.
- Mock-мозг (`mock_llm_server.py`) и тестовый набор: 36 тестов, включая три сквозных сценария без сети.
