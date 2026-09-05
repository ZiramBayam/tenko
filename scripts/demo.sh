#!/usr/bin/env bash
# Naskah demo docs/spec.md §7 langkah 1-6 dari fixture offline (TASKS 3.4).
# Pakai: make demo   ·   make demo ARGS="--step2-fixture path.json"   ·   bash scripts/demo.sh
# Langkah 2 memakai fixture kegagalan TASKS 2.2c secara BAWAAN (lihat di bawah).
#
# Kontrak yang dijaga tes (tests/test_agent_demo.py):
#   * `make demo > a; make demo > b; diff a b` KOSONG — seluruh naskah deterministik.
#   * `data/ryo.db` tidak dibuka sama sekali: tiap run membangun DB SQLite baru di direktori
#     sementara dari fixture JSON, dan `agent/demo.py` memasang audit hook yang MENGUKUR itu.
#
# Berkas ini sengaja setipis mungkin. Semua yang bisa berbeda antar-run hidup di Python, di mana
# ia bisa dites; shell yang menghitung atau memformat berarti dua tempat yang harus sama-sama
# benar. `exec` supaya kode keluar dan sinyal milik proses Python, bukan milik shell pembungkus.
#
# `.env` TIDAK dimuat, tidak seperti scripts/daemons.sh, dan itu disengaja: §7 langkah 4 adalah
# "matikan LLM (env kosong) -> narration_unavailable". Kredensial yang ikut masuk ke proses akan
# membuat langkah itu memperagakan keadaan yang berbeda dari yang tertulis di naskah — dan
# menambah satu jalan lagi bagi kredensial untuk sampai ke keluaran yang di-screenshot.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

# §7 langkah 2 menuntut item 02 MERAH, dan itu hanya lahir bila SATU tool wajib menjawab
# `unavailable` sementara yang lain menjawab. Fixture berbentuk itu sekarang ada (TASKS 2.2c),
# jadi `make demo` memakainya SECARA BAWAAN — bukan hanya bila dilewatkan tangan. Fixture
# kegagalan yang KEDUA (semua tool padam) sengaja tidak dipakai di sini: ia menghasilkan
# envelope `unavailable` + item 02 `unspoken`, kalimat lain, bukan naskah langkah 2.
# Penelepon yang menyebut --step2-fixture sendiri tetap menang.
step2=(--step2-fixture "preflight/fixtures/outage-one-tool-2026-09-05.json")
for arg in "$@"; do
  case "$arg" in
    --step2-fixture | --step2-fixture=*) step2=() ;;
  esac
done

exec uv run python -m agent.demo ${step2[@]+"${step2[@]}"} "$@"
