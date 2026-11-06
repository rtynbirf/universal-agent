#!/usr/bin/env python3
"""Фальшивый docker CLI для юнит-тестов контейнерной песочницы.

Эмулирует подмножество CLI, достаточное песочнице агента:
  version --format <tpl>                -> печатает версию, exit 0 (демон жив);
  run <флаги> <образ> python -B -c <код>
      - логирует полный argv в $FAKE_DOCKER_LOG (строка JSON на вызов);
      - уважает --workdir + --volume: переходит в смонтированный каталог,
        чтобы код видел те же файлы, что видит настоящий контейнер;
      - исполняет код настоящим python (эмуляция контейнера);
      - маркер FAKE_SLEEP в коде: висит FAKE_DOCKER_RUN_SLEEP секунд —
        клиент будет снят таймаутом агента;
  kill <контейнер>                      -> exit 0 (контейнер снят).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def _log(argv: list) -> None:
    log = os.environ.get("FAKE_DOCKER_LOG")
    if not log:
        return
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(argv, ensure_ascii=False) + "\n")


def _volumes(args: list) -> dict:
    """Карта «путь в контейнере -> локальный путь» из флагов --volume."""
    mounts = {}
    for i, arg in enumerate(args):
        if arg == "--volume" and i + 1 < len(args):
            local, _, container = args[i + 1].partition(":")
            mounts[container] = local
    return mounts


def main() -> int:
    argv = sys.argv[1:]
    _log(argv)
    if not argv:
        return 1
    if argv[0] == "version":
        print("24.0.0")
        return 0
    if argv[0] == "kill":
        return 0
    if argv[0] == "run":
        args = argv[1:]
        mounts = _volumes(args)
        workdir = "/workspace"
        for i, arg in enumerate(args):
            if arg == "--workdir" and i + 1 < len(args):
                workdir = args[i + 1]
        # хвост команды: <образ> python -B -c <код> — код идёт после последнего "-c"
        ci = len(args) - args[::-1].index("-c")
        code = args[ci]
        local_dir = mounts.get(workdir)
        if local_dir:
            os.chdir(local_dir)
        if "FAKE_SLEEP" in code:
            time.sleep(float(os.environ.get("FAKE_DOCKER_RUN_SLEEP", "30")))
            return 0
        done = subprocess.run(
            [sys.executable, "-B", "-c", code],
            capture_output=True, text=True, timeout=60,
        )
        sys.stdout.write(done.stdout)
        sys.stderr.write(done.stderr)
        return done.returncode
    return 1


if __name__ == "__main__":
    sys.exit(main())
