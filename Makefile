.PHONY: doctor test lint hooks record daemons backup skill agent-tick replay two-dates grade ui demo autopilot frontend
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
frontend:          ## bangun ulang ui/static/app.js + app.css dari frontend/ (BUTUH Node; ADR-020)
	@command -v npm >/dev/null 2>&1 || { \
	  echo "make frontend: npm tidak ada. Node/npm adalah alat BUILD saja: make ui, make test, dan make demo TIDAK memerlukannya karena ui/static/app.js dan ui/static/app.css dilacak git (ADR-020 butir 2). Target ini hanya perlu dijalankan kalau kamu MENGUBAH frontend/." >&2; \
	  exit 1; }
	npm ci                       # ci, bukan install: lockfile yang menentukan, bukan rentang versi
	npx tsc --noEmit             # type-check; esbuild sendiri tidak memeriksa tipe
	npx esbuild frontend/src/main.ts --bundle --format=iife --target=es2020 --charset=utf8 --legal-comments=none --outfile=ui/static/app.js
	npx esbuild frontend/styles/app.css --bundle --charset=utf8 --legal-comments=none --outfile=ui/static/app.css
	@echo "ui/static/ dibangun ulang. JANGAN lupa: git add ui/static/ — artefaknya dilacak."
	@echo "SHA-256 artefak (paku ulang di tests/test_ui.py: BUNDLE_SHA256 / STYLESHEET_SHA256):"
	@sha256sum ui/static/app.js ui/static/app.css
ui:                ## nol Node: melayani ui/static/ yang sudah dilacak git
	uv run uvicorn ui.app:app --port $${UI_PORT:-8000}
demo:              ## naskah docs/spec.md §7 langkah 1-6 (fixture offline) — TASKS 3.4
	@bash scripts/demo.sh $${ARGS:-}   # @ : stdout harus naskahnya saja (dua run wajib byte-identik)
autopilot:         ## make autopilot N=3 — skrip lokal, sengaja tidak dilacak git
	@[ -f scripts/autopilot.sh ] || { \
	  echo "make autopilot: scripts/autopilot.sh tidak ada. Skrip itu mesin kerja internal dan sengaja tidak dilacak git (.git/info/exclude), jadi clone bersih tidak memilikinya. Bukan bagian aplikasi: tidak ada yang perlu diperbaiki untuk menjalankan Tenko — pakai make test / make demo / make ui." >&2; \
	  exit 1; }
	bash scripts/autopilot.sh $${N:-3}
