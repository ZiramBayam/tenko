.PHONY: doctor test lint hooks record daemons skill agent-tick replay grade ui demo autopilot
doctor:
	@echo "python : $$(python3 --version 2>/dev/null || echo MISSING)  (harus 3.12.x, lihat docs/versions.md)"
	@echo "uv     : $$(uv --version 2>/dev/null || echo MISSING)"
	@echo "sqlite : $$(python3 -c 'import sqlite3;print(sqlite3.sqlite_version)' 2>/dev/null || echo MISSING)"
	@echo "env    : $$( [ -f .env ] && echo '.env ada' || echo '.env BELUM ada (salin .env.example)')"
	@echo "sources: $$(grep -c 'enabled: true' sources.yaml) sumber aktif (lihat sources.yaml)"
test:
	uv run pytest -q
lint:
	uv run ruff check . && uv run ruff format --check .
record:            ## perekam di depan (Ctrl+C untuk berhenti)
	uv run python -m core.recorder
hooks:             ## aktifkan git hook commit-msg (Conventional Commits)
	git config core.hooksPath .githooks && echo "hooks aktif: .githooks/commit-msg"
daemons:           ## make daemons ARGS=start|status|stop — perekam+agen lepas dari sesi
	bash scripts/daemons.sh $${ARGS:-status}
skill:             ## make skill ARGS="preflight SOL"  |  ARGS="preflight SOL hold"
	uv run python -m core.skill_cli $(ARGS)
agent-tick:        ## satu siklus agen buku-ganda (SIMULASI)
	uv run python -m agent.main --once
replay:            ## make replay ID=<trade_id> → decision_hash harus identik
	uv run python -m agent.replay $(ID)
grade:             ## (opsional) grading outcome keputusan → reputasi item
	uv run python -m grading.grader
ui:
	uv run uvicorn ui.app:app --port $${UI_PORT:-8000}
demo:              ## naskah docs/spec.md §7 dari nol (fixture offline) — TASKS 4.4
	@echo "TASKS 4.4 belum dikerjakan: implementasikan scripts/demo.sh sesuai docs/spec.md §7"
autopilot:
	bash scripts/autopilot.sh $${N:-3}
