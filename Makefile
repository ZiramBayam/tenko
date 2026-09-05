.PHONY: doctor test lint hooks record daemons backup skill agent-tick replay two-dates grade ui demo autopilot
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
backup:            ## salinan online data/ryo.db -> data/backups/ryo-<UTC>.db, lalu integrity_check
	python3 scripts/backup.py   # stdlib saja: harus jalan walau venv/paket aplikasi rusak
age-sweep:         ## kanari kesegaran: sebaran `ts_utc - as_of` (TASKS 0.20)
	python3 scripts/age_sweep.py   # stdlib saja + mode=ro, seperti scripts/db_status.py
skill:             ## make skill ARGS="preflight SOL"  |  ARGS="preflight SOL swing"
	@uv run python -m core.skill_cli $(ARGS)   # @ : stdout harus JSON saja (spec §7 langkah 1)
agent-tick:        ## satu siklus agen buku-ganda (SIMULASI)
	uv run python -m agent.main --once
replay:            ## make replay ID=<trade_id> → decision_hash harus identik
	uv run python -m agent.replay $(ID)
two-dates:         ## spec §7 langkah 6 — dua tanggal, simbol sama, kartu berdampingan (TASKS 4.7)
	uv run python -m agent.two_dates   # fixture offline; DB tmp baru tiap run, data/ryo.db tidak disentuh
grade:             ## (opsional) grading outcome keputusan → reputasi item
	uv run python -m grading.grader
ui:
	uv run uvicorn ui.app:app --port $${UI_PORT:-8000}
demo:              ## naskah docs/spec.md §7 dari nol (fixture offline) — TASKS 3.4
	@if [ -x scripts/demo.sh ]; then bash scripts/demo.sh; else \
	  echo "make demo: TASKS 3.4 belum dikerjakan — scripts/demo.sh belum ada." >&2; \
	  echo "Gagal keras dengan sengaja: target yang exit 0 tanpa menjalankan apa pun" >&2; \
	  echo "melaporkan sukses palsu ke shell, CI, dan juri (temuan juri Fase 1)." >&2; \
	  exit 1; fi
autopilot:
	bash scripts/autopilot.sh $${N:-3}
