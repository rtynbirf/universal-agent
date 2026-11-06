## Summary

<!-- Что и зачем. Один абзац без маркетинга. -->

## Type

- [ ] `feat` — новая функциональность
- [ ] `fix` — исправление
- [ ] `docs` — только документация
- [ ] `chore` — сборка/CI/релиз-инженерия
- [ ] `refactor` — без изменения поведения
- [ ] `test` — только тесты

## Breaking changes

None

## Verification

Локальный прогон на HEAD ветки:

```bash
python3 scripts/check.py
# Result: N passed, 0 failed (of N checks)
# tests: 36/36 (mock-brain, no network)
```

- [ ] `make verify` exit 0
- [ ] `CHANGELOG.md` обновлён в `[Unreleased]` (Keep a Changelog)
- [ ] Commit message = Conventional Commits + `Signed-off-by` (DCO, `git commit -s`)
- [ ] Нет секретов в diff (hygiene PASS)

## Checklist

- [ ] PR title = commit message (semantic-pull-request)
- [ ] Self-review пройден; язык: документация — русский, коммиты — английский
- [ ] Архитектурные изменения описаны в Summary (смена мозга/протокола/памяти)
