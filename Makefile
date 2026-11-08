PY ?= python3

.PHONY: verify test lint manifest health run daemon clean help

help:
	@echo "verify   — канон: check.py (компиляция + тесты + гигиена + манифест)"
	@echo "test     — unittest-набор подробно"
	@echo "lint     — ruff check ."
	@echo "manifest — пересобрать release/manifest_sha256.txt"
	@echo "health   — П55: A2A Agent Card ↔ живое поведение (честность вслух)"
	@echo "run      — make run TASK=\"текст задачи\""
	@echo "daemon   — автономный режим жизни"

verify:
	$(PY) scripts/check.py

test:
	$(PY) -m unittest discover -s tests -v

lint:
	ruff check .

manifest:
	@mkdir -p release
	sha256sum universal_agent.py README.md CHANGELOG.md LICENSE CONTRIBUTING.md SECURITY.md pyproject.toml Makefile Dockerfile agent-card.json scripts/healthcheck.py > release/manifest_sha256.txt
	@echo "release/manifest_sha256.txt обновлён"

health:
	$(PY) scripts/healthcheck.py

run:
	$(PY) universal_agent.py task --task "$(TASK)"

daemon:
	$(PY) universal_agent.py daemon

clean:
	rm -rf __pycache__ tests/__pycache__ .ruff_cache
