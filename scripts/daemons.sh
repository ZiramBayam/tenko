#!/usr/bin/env bash
# Perekam & agen sebagai proses lepas (setsid) — tetap hidup walau sesi Claude ditutup.
# Pakai: bash scripts/daemons.sh start|status|stop
set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs data
[ -f .env ] && { set -a; . ./.env; set +a; }

start_one() {  # nama, perintah...
  local name="$1"; shift
  if [ -f "logs/$name.pid" ] && kill -0 "$(cat "logs/$name.pid")" 2>/dev/null; then
    echo "$name sudah jalan (pid $(cat "logs/$name.pid"))"; return 0
  fi
  setsid nohup "$@" >> "logs/$name.log" 2>&1 < /dev/null &
  echo $! > "logs/$name.pid"
  sleep 1
  kill -0 "$(cat "logs/$name.pid")" 2>/dev/null && echo "$name start (pid $(cat "logs/$name.pid"))" || { echo "$name GAGAL start — lihat logs/$name.log"; return 1; }
}

case "${1:-status}" in
  start)
    export RECORDER_MAX_SYMBOLS="${RECORDER_MAX_SYMBOLS:-20}" RECORDER_INTERVAL_S="${RECORDER_INTERVAL_S:-3600}"
    # Salinan DB SEBELUM perekam menyala (TASKS 1.12). `||` disengaja: kehilangan satu jam
    # snapshot (tidak bisa dibuat ulang) lebih buruk daripada tidak punya salinan hari ini,
    # jadi backup yang gagal hanya memperingatkan dan TIDAK menghentikan start perekam.
    python3 scripts/backup.py || echo "PERINGATAN: backup gagal (lihat pesan di atas) - perekam tetap dinyalakan, jalankan 'make backup' setelah ini"
    # Perekam asli dipakai begitu TASKS 0.5 selesai; sebelum itu perekam sementara
    # menjaga agar tidak ada lubang di data (outcome T+24j tidak bisa dibuat ulang).
    if [ -f core/recorder.py ]; then
      start_one recorder uv run python -m core.recorder
    elif [ -f scripts/bootstrap_recorder.py ]; then
      echo "core/recorder.py belum ada -> memakai scripts/bootstrap_recorder.py (sementara)"
      start_one recorder uv run python scripts/bootstrap_recorder.py
    else
      echo "recorder: tidak ada implementasi yang bisa dijalankan"
    fi
    if [ -f agent/main.py ]; then
      start_one agent uv run python -m agent.main --client live --interval "${AGENT_INTERVAL_S:-3600}" \
        --symbols "${WATCHLIST:-SOL,BTC,ETH,BNB}"
    else
      echo "agent: belum dibangun (TASKS 2.4), dilewati"
    fi
    ;;
  stop)
    for n in recorder agent; do
      if [ -f "logs/$n.pid" ]; then kill "$(cat "logs/$n.pid")" 2>/dev/null && echo "$n stop"; rm -f "logs/$n.pid"; fi
    done
    ;;
  status)
    for n in recorder agent; do
      if [ -f "logs/$n.pid" ] && kill -0 "$(cat "logs/$n.pid")" 2>/dev/null; then
        echo "$n: JALAN (pid $(cat "logs/$n.pid")) — $(tail -1 "logs/$n.log" 2>/dev/null | cut -c1-90)"
      else
        echo "$n: mati"
      fi
    done
    python3 scripts/db_status.py 2>/dev/null || true
    ;;
  *) echo "pakai: bash scripts/daemons.sh start|status|stop"; exit 2;;
esac
