#!/usr/bin/env bash
# Chaos test Tenko (TASKS 4.5) — enam kegagalan yang juri akan coba, dijalankan sungguhan.
#
# Yang dibuktikan: nol traceback tak tertangani, status yang jujur (bukan nol/GO palsu), dan
# backoff yang benar-benar TERTULIS di log. Laporan naratifnya: docs/reports/chaos-<tgl>.md.
#
# PAKAI:  bash scripts/chaos.sh [--only <nama>] [--report <berkas.md>]
#         CHAOS_KEEP=1 menahan artefak sementara (default: dihapus saat keluar)
# Nama skenario: host_down · recorder_kill9 · rate_limit_429 · db_deleted_ui · agent_tick_live
#                · credentials_removed · evidence_gap
#
# ==========================================================================================
# KESELAMATAN — dibaca sebelum menyunting satu baris pun
# ==========================================================================================
# Di mesin ini ada perekam PRODUKSI yang hidup dan `data/ryo.db` berisi ribuan baris bukti yang
# TIDAK BISA dibuat ulang (ADR-006). Skrip ini karena itu memegang empat aturan keras:
#
#  1. Ia hanya membunuh proses yang ia SPAWN SENDIRI. `chaos_kill` menolak pid mana pun yang
#     bukan anak langsung shell ini (`/proc/<pid>/stat` field ppid == $$) DAN tidak terdaftar
#     di `SPAWNED`. Tidak ada `pkill`, tidak ada pencocokan pola nama proses — pola itulah yang
#     akan mengenai perekam produksi.
#  2. Ia tidak pernah menulis, memindahkan, atau menghapus `data/ryo.db`. Setiap skenario yang
#     butuh basis data memakai SALINAN di direktori sementara (`chaos_support.py db-copy`,
#     yang membuka sumbernya `mode=ro`). Cacah baris sumber dicatat sebelum & sesudah dan
#     dituntut TIDAK MENYUSUT.
#  3. Ia tidak pernah memindahkan `.env` yang asli. Skenario "kredensial dicabut" membangun
#     pohon sandbox berisi salinan `core/` + `sources.yaml` + `.env` SINTETIS (kunci palsu,
#     URL ke server fixture lokal); yang dipindahkan adalah `.env` sandbox itu.
#  4. Ia tidak pernah memanggil RYO sungguhan. Semua jalur "live" diarahkan ke 127.0.0.1.
#  5. Ia tidak meninggalkan bukti RYO di luar repo. `$CHAOS_DIR` (berisi salinan penuh
#     `data/ryo.db` untuk skenario 4/5/7) DIHAPUS di `cleanup()`; `CHAOS_KEEP=1` menahannya
#     untuk forensik. Tabel hasil selamat lewat stdout dan `--report`, yang ditulis di luar
#     `$CHAOS_DIR`.
#
# `scripts/daemons.sh` tetap pelapor, bukan supervisor (batas TASKS 1.13): skrip ini tidak
# memanggilnya untuk start/stop apa pun.
# ==========================================================================================

set -uo pipefail
# `-e` SENGAJA tidak dipakai: chaos harus menjalankan keenam skenario walaupun satu gagal —
# skrip yang berhenti di kegagalan pertama menyembunyikan lima kegagalan berikutnya.

# `BASH_SOURCE`, bukan `$0`: skrip ini juga di-SOURCE oleh `tests/test_chaos.py`
# (CHAOS_LIB_ONLY=1), dan di sana `$0` adalah `bash` sehingga ROOT akan salah.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
PY="$ROOT/.venv/bin/python"
SUPPORT="$ROOT/scripts/chaos_support.py"
REAL_DB="$ROOT/data/ryo.db"
SAMPLE="$ROOT/docs/samples/market_overview.json"

ONLY=""
REPORT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="${2:-}"; shift 2;;
    --report) REPORT="${2:-}"; shift 2;;
    -h|--help) sed -n '2,11p' "$0"; exit 0;;
    *) echo "argumen tidak dikenal: $1" >&2; exit 2;;
  esac
done

[ -x "$PY" ] || { echo "FATAL: $PY tidak ada — jalankan 'uv sync' dulu" >&2; exit 2; }

CHAOS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tenko-chaos-XXXXXX")" || exit 2
SANDBOX="$CHAOS_DIR/sandbox"
declare -a SPAWNED=()
PASS=0; FAIL=0; SKIP=0
SUMMARY="$CHAOS_DIR/summary.md"
: > "$SUMMARY"

say()  { printf '%s\n' "$*"; }
head2() { printf '\n=== %s ===\n' "$*"; }
fatal() { printf 'FATAL(keselamatan): %s\n' "$*" >&2; exit 3; }

# --- pagar pembunuhan proses --------------------------------------------------------------
# Pid perekam/agen produksi dicatat SEKALI di awal sebagai daftar-hitam eksplisit. Pagar
# sebenarnya adalah cek "anak langsung" di bawah; daftar ini hanya lapis kedua yang murah.
FORBIDDEN_PIDS=""
for pidfile in "$ROOT/logs/recorder.pid" "$ROOT/logs/agent.pid"; do
  [ -f "$pidfile" ] && FORBIDDEN_PIDS="$FORBIDDEN_PIDS $(cat "$pidfile" 2>/dev/null)"
done

_is_own_child() {  # pid — benar bila ppid-nya PERSIS shell ini
  local pid="$1" ppid
  [ -r "/proc/$pid/stat" ] || return 1
  # comm bisa memuat spasi dan kurung; potong sampai ')' TERAKHIR lalu ambil field ke-2 (ppid)
  ppid="$(sed -e 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $2}')"
  [ "$ppid" = "$$" ]
}

# Hasilnya ditaruh di $LAST_PID, TIDAK dicetak: `pid=$(chaos_spawn ...)` akan berjalan di
# subshell sehingga `SPAWNED` di induk tetap kosong dan pid-nya bukan anak langsung `$$` —
# `chaos_kill` lalu menolak membunuh proses milik chaos sendiri (terjadi, lalu diperbaiki).
LAST_PID=""
chaos_spawn() {  # perintah... — jalankan di latar, catat pid ke $LAST_PID
  "$@" >>"$CHAOS_DIR/spawn.log" 2>&1 &
  LAST_PID=$!
  SPAWNED+=("$LAST_PID")
}

chaos_kill() {  # sinyal pid
  local sig="$1" pid="$2" known=0 p
  for p in "${SPAWNED[@]:-}"; do [ "$p" = "$pid" ] && known=1; done
  [ "$known" = "1" ] || fatal "menolak kill: pid $pid tidak di-spawn skrip ini"
  for p in $FORBIDDEN_PIDS; do
    [ "$p" = "$pid" ] && fatal "menolak kill: pid $pid ada di logs/*.pid (proses produksi)"
  done
  _is_own_child "$pid" || fatal "menolak kill: pid $pid bukan anak langsung shell ini"
  kill "-$sig" "$pid" 2>/dev/null
}

cleanup() {
  local pid
  for pid in "${SPAWNED[@]:-}"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && _is_own_child "$pid"; then
      kill -TERM "$pid" 2>/dev/null
    fi
  done
  wait 2>/dev/null
  say ""
  # Skenario 4/5/7 menaruh SALINAN PENUH `data/ryo.db` di sini — ribuan envelope RYO, ~9 MB
  # masing-masing. `docs/rules-compliance.md`: output tool RYO hanya untuk di dalam submission,
  # jadi ia tidak boleh menumpuk di luar repo sesudah skrip selesai. Default = bersihkan;
  # `CHAOS_KEEP=1` menahannya untuk forensik saat ada skenario yang GAGAL.
  if [ "${CHAOS_KEEP:-0}" = "1" ]; then
    say "artefak chaos DITAHAN (CHAOS_KEEP=1): $CHAOS_DIR"
  else
    case "$CHAOS_DIR" in
      */tenko-chaos-*) rm -rf -- "$CHAOS_DIR"; say "artefak chaos dihapus: $CHAOS_DIR (CHAOS_KEEP=1 untuk menahannya)";;
      *) say "artefak chaos TIDAK dihapus (nama di luar pola tenko-chaos-*): $CHAOS_DIR";;
    esac
  fi
}
trap cleanup EXIT

# --- pencatat hasil ------------------------------------------------------------------------
NOTES=""
note() { NOTES="$NOTES  - $*"$'\n'; say "    · $*"; }
record() {  # nama status detail
  local name="$1" status="$2" detail="$3"
  printf '| `%s` | %s | %s |\n' "$name" "$status" "$detail" >> "$SUMMARY"
  case "$status" in
    LULUS) PASS=$((PASS + 1));;
    GAGAL) FAIL=$((FAIL + 1));;
    *) SKIP=$((SKIP + 1));;
  esac
  say "  -> $name: $status — $detail"
}

wanted() { [ -z "$ONLY" ] || [ "$ONLY" = "$1" ]; }

# Traceback = kegagalan AC. Dicari di berkas apa pun yang dihasilkan skenario.
has_traceback() { grep -qF 'Traceback (most recent call last)' "$1" 2>/dev/null; }

db_rows() {  # db tabel -> cacah baris, atau kosong
  "$PY" "$SUPPORT" db-check --db "$1" --table "$2" 2>/dev/null \
    | "$PY" -c "import json,sys;print(json.load(sys.stdin).get('rows_$2',''))" 2>/dev/null
}

wait_for_file() {  # berkas detik
  local i=0
  while [ ! -s "$1" ]; do
    i=$((i + 1)); [ "$i" -gt "$(( ${2} * 20 ))" ] && return 1
    sleep 0.05
  done
  return 0
}

# `CHAOS_LIB_ONLY=1 source scripts/chaos.sh` berhenti DI SINI: fungsi-fungsi di atas terpasang,
# nol skenario berjalan. Ada supaya `tests/test_chaos.py` bisa menguji pagar keselamatan
# (`chaos_kill`) secara langsung — pagar yang tidak punya tes adalah pagar yang belum diuji,
# dan pagar inilah yang berdiri di antara skrip ini dan perekam produksi.
if [ "${CHAOS_LIB_ONLY:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

# ==========================================================================================
# Cacah bukti SEBELUM apa pun dijalankan. Invarian yang dituntut di akhir: berkasnya masih
# ada dan barisnya TIDAK BERKURANG. `md5` sengaja TIDAK dijadikan syarat lulus — perekam
# produksi menambah baris tiap jam, jadi md5 yang berubah adalah tanda sehat, bukan tanda
# rusak; yang dilaporkan adalah angkanya, dan pembacanya bisa menilai sendiri.
# ==========================================================================================
DB_BEFORE_ROWS="$(db_rows "$REAL_DB" snapshots)"
DB_BEFORE_MD5="$(md5sum "$REAL_DB" 2>/dev/null | awk '{print $1}')"
DB_BEFORE_SIZE="$(stat -c %s "$REAL_DB" 2>/dev/null)"
say "bukti sebelum chaos: data/ryo.db snapshots=$DB_BEFORE_ROWS size=$DB_BEFORE_SIZE md5=$DB_BEFORE_MD5"
say "pid produksi yang TIDAK BOLEH disentuh:${FORBIDDEN_PIDS:- (tidak ada logs/*.pid)}"
say "sandbox chaos: $CHAOS_DIR"

FAKE_KEY='chaos-fixture-key-bukan-kredensial-nyata'

# ==========================================================================================
# 1. Host RYO mati
# ==========================================================================================
if wanted host_down; then
  head2 "1/7 host_down — RYO tidak menjawab sama sekali"
  NOTES=""
  PORT_DEAD="$("$PY" "$SUPPORT" free-port)"
  LOG="$CHAOS_DIR/host_down.log"
  # Port yang TIDAK didengarkan siapa pun -> connection refused. Bukan RYO sungguhan.
  RYO_MCP_URL="https://127.0.0.1:$PORT_DEAD/api/mcp" \
  RYO_MCP_KEY="$FAKE_KEY" \
  RECORDER_LOCK_PATH="$CHAOS_DIR/host_down.lock" \
  WATCHLIST=SOL RECORDER_MAX_SYMBOLS=1 RECORDER_MAX_FOLLOWUPS=1 \
    timeout 240 "$PY" -m core.recorder --once --no-dotenv --client live \
      --db "$CHAOS_DIR/host_down.db" >"$LOG" 2>&1
  RC=$?
  note "perekam exit=$RC (diharapkan bukan 0: siklus tanpa satu pun snapshot)"
  BACKOFF_LINES="$(grep -c 'backoff' "$LOG")"
  note "baris backoff di log: $BACKOFF_LINES"
  note "contoh: $(grep -m1 'backoff' "$LOG" | cut -c1-120)"

  # Jalur BACA harus tetap menjawab jujur walau host mati: preflight store-first.
  SKILL_OUT="$CHAOS_DIR/host_down_skill.json"
  DB_PATH="$CHAOS_DIR/host_down.db" RYO_MCP_URL="https://127.0.0.1:$PORT_DEAD/api/mcp" \
  RYO_MCP_KEY="$FAKE_KEY" \
    timeout 120 "$PY" -m core.skill_cli preflight SOL swing \
      >"$SKILL_OUT" 2>"$CHAOS_DIR/host_down_skill.err"
  SKILL_RC=$?
  SKILL_STATUS="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('status'))" \
    "$SKILL_OUT" 2>/dev/null)"
  note "skill_cli exit=$SKILL_RC status=$SKILL_STATUS (exit 0 + status jujur = kontrak §3)"

  OK=1
  [ "$RC" = "0" ] && { OK=0; note "CACAT: perekam melaporkan sukses padahal host mati"; }
  [ "${BACKOFF_LINES:-0}" -lt 1 ] && { OK=0; note "CACAT: nol baris backoff"; }
  has_traceback "$LOG" && { OK=0; note "CACAT: traceback di log perekam"; }
  has_traceback "$CHAOS_DIR/host_down_skill.err" && { OK=0; note "CACAT: traceback di skill_cli"; }
  [ "$SKILL_RC" = "0" ] || { OK=0; note "CACAT: skill_cli exit != 0"; }
  case "$SKILL_STATUS" in ok|partial|unavailable) ;; *) OK=0; note "CACAT: status skill tidak dikenal";; esac
  # Satu-satunya skenario yang menjalankan `--client live`: kalau ada jalur yang membocorkan
  # Bearer key ke log, ia bocor DI SINI. Kuncinya palsu, asersinya tidak.
  grep -qiF "$FAKE_KEY" "$LOG" "$SKILL_OUT" "$CHAOS_DIR/host_down_skill.err" \
    && { OK=0; note "CACAT: kunci muncul di log/keluaran perekam atau skill_cli"; }
  [ "$OK" = "1" ] \
    && record host_down LULUS "perekam exit $RC, $BACKOFF_LINES baris backoff, skill_cli exit 0 status \`$SKILL_STATUS\`, nol traceback, nol kunci di keluaran" \
    || record host_down GAGAL "lihat $LOG"
  printf '%s' "$NOTES" >> "$CHAOS_DIR/notes.md"
fi

# ==========================================================================================
# 2. kill -9 perekam mid-siklus, lalu restart
# ==========================================================================================
if wanted recorder_kill9; then
  head2 "2/7 recorder_kill9 — SIGKILL di tengah siklus, lalu start lagi"
  NOTES=""
  KDB="$CHAOS_DIR/kill9.db"
  # Perekam MILIK CHAOS: klien fixture (nol panggilan jaringan), DB sementara, kunci sendiri.
  # `--client fixture` bahkan menolak menulis ke data/ryo.db lewat `fixture_refusal`.
  chaos_spawn env RECORDER_LOCK_PATH="$CHAOS_DIR/kill9.lock" WATCHLIST=SOL,BTC,ETH,BNB \
      RECORDER_MAX_SYMBOLS=8 RECORDER_INTERVAL_S=3600 \
      "$PY" -m core.recorder --no-dotenv --client fixture --db "$KDB"
  KPID="$LAST_PID"
  note "perekam chaos di-spawn pid=$KPID (bukan perekam produksi:${FORBIDDEN_PIDS:- -})"

  ROWS_AT_KILL=""
  for _ in $(seq 1 400); do
    ROWS_AT_KILL="$(db_rows "$KDB" snapshots)"
    [ -n "$ROWS_AT_KILL" ] && [ "$ROWS_AT_KILL" -ge 1 ] 2>/dev/null && break
    sleep 0.05
  done
  chaos_kill 9 "$KPID"
  wait "$KPID" 2>/dev/null
  KRC=$?
  note "SIGKILL dikirim saat snapshots=$ROWS_AT_KILL; status tunggu=$KRC (137 = dibunuh SIGKILL)"

  # Jurnal PANAS: `kill -9` di tengah transaksi meninggalkan `<db>-journal`. Ini keadaan yang
  # normal sesudah crash, BUKAN kerusakan — tetapi ia mengubah apa yang bisa dijawab pembaca
  # baca-saja, jadi dicatat eksplisit sebelum apa pun menyentuh berkasnya.
  SIDE=""
  for ext in -journal -wal -shm; do [ -f "$KDB$ext" ] && SIDE="$SIDE $ext"; done
  note "berkas sampingan sesudah SIGKILL:${SIDE:- (tidak ada)}"

  # Restart PERTAMA — sebelum siapa pun memulihkan jurnal. Ini keadaan yang PERSIS ditemui
  # operator yang menyalakan ulang perekam sesudah crash. Yang dituntut di sini bukan sukses,
  # melainkan KEJUJURAN: exit code yang berarti, pesan yang menyebut sebabnya, nol traceback.
  RECORDER_LOCK_PATH="$CHAOS_DIR/kill9.lock" WATCHLIST=SOL,BTC,ETH,BNB RECORDER_MAX_SYMBOLS=8 \
    timeout 240 "$PY" -m core.recorder --once --no-dotenv --client fixture --db "$KDB" \
      >"$CHAOS_DIR/kill9_restart_a.log" 2>&1
  RRC_A=$?
  note "restart A (jurnal masih panas): exit=$RRC_A — $(grep -m1 -o 'menolak menulis payload fixture[^.]*\.' "$CHAOS_DIR/kill9_restart_a.log" | cut -c1-120)"

  # Pemulihan: koneksi BACA-TULIS memutar balik jurnal, persis yang dilakukan proses mana pun
  # yang membuka DB itu sesudah crash (termasuk `--client live`, yang tidak punya penjaga
  # fixture di depannya). Baru sesudah ini `integrity_check` bisa menjawab.
  CHECK1="$("$PY" "$SUPPORT" db-check --recover --db "$KDB" --table snapshots)"
  note "sesudah pemulihan: $CHECK1"
  ROWS1="$(db_rows "$KDB" snapshots)"
  INTEG="$("$PY" -c "import json,sys;print(json.load(sys.stdin).get('integrity'))" <<<"$CHECK1")"

  RECORDER_LOCK_PATH="$CHAOS_DIR/kill9.lock" WATCHLIST=SOL,BTC,ETH,BNB RECORDER_MAX_SYMBOLS=8 \
    timeout 240 "$PY" -m core.recorder --once --no-dotenv --client fixture --db "$KDB" \
      >"$CHAOS_DIR/kill9_restart_b.log" 2>&1
  RRC_B=$?
  ROWS2="$(db_rows "$KDB" snapshots)"
  note "restart B (sesudah pemulihan): exit=$RRC_B, snapshots $ROWS1 -> $ROWS2, integrity_check=$INTEG"

  OK=1
  [ "$INTEG" = "ok" ] || { OK=0; note "CACAT: integrity_check bukan 'ok' sesudah pemulihan"; }
  [ "${ROWS1:-0}" -ge 1 ] || { OK=0; note "CACAT: nol snapshot bertahan — commit per snapshot tidak terbukti"; }
  [ "${ROWS2:-0}" -gt "${ROWS1:-0}" ] || { OK=0; note "CACAT: restart tidak menambah snapshot"; }
  [ "$RRC_B" = "0" ] || { OK=0; note "CACAT: restart B exit $RRC_B"; }
  # Restart A boleh menolak (0 = jurnal tidak panas; 2 = menolak dengan alasan). Yang TIDAK
  # boleh: kode keluar lain, atau traceback.
  case "$RRC_A" in 0|2) ;; *) OK=0; note "CACAT: restart A exit $RRC_A di luar {0,2}";; esac
  has_traceback "$CHAOS_DIR/kill9_restart_a.log" && { OK=0; note "CACAT: traceback saat restart A"; }
  has_traceback "$CHAOS_DIR/kill9_restart_b.log" && { OK=0; note "CACAT: traceback saat restart B"; }
  [ "$OK" = "1" ] \
    && record recorder_kill9 LULUS "SIGKILL saat $ROWS_AT_KILL baris; sidecar${SIDE:- -}; restart A exit $RRC_A (jujur); pemulihan integrity ok; restart B $ROWS1 -> $ROWS2 baris" \
    || record recorder_kill9 GAGAL "lihat $CHAOS_DIR/kill9_restart_b.log"
  printf '%s' "$NOTES" >> "$CHAOS_DIR/notes.md"
fi

# ==========================================================================================
# 3. HTTP 429 + Retry-After dari server fixture
# ==========================================================================================
if wanted rate_limit_429; then
  head2 "3/7 rate_limit_429 — 429 + Retry-After: 2 dari server fixture HTTPS lokal"
  NOTES=""
  CERT="$CHAOS_DIR/chaos-cert.pem"; KEYF="$CHAOS_DIR/chaos-key.pem"
  # `RyoClient` MENOLAK base_url non-https (Bearer tidak pernah polos), jadi server fixture
  # harus TLS. Sertifikat self-signed umur 1 hari, SAN IP:127.0.0.1, hanya dipercaya di sini.
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=127.0.0.1" \
    -addext "subjectAltName=IP:127.0.0.1" -keyout "$KEYF" -out "$CERT" \
    >"$CHAOS_DIR/openssl.log" 2>&1
  if [ ! -s "$CERT" ]; then
    record rate_limit_429 DILEWATI "openssl gagal membuat sertifikat — lihat $CHAOS_DIR/openssl.log"
  else
    PF="$CHAOS_DIR/tls.port"; CF="$CHAOS_DIR/tls.count"
    chaos_spawn "$PY" "$SUPPORT" tls-server --port-file "$PF" --count-file "$CF" \
        --cert "$CERT" --key "$KEYF" --sample "$SAMPLE" --n-429 2 --retry-after 2
    SPID="$LAST_PID"
    if wait_for_file "$PF" 10; then
      TPORT="$(cat "$PF")"
      note "server fixture HTTPS di 127.0.0.1:$TPORT (pid $SPID): 2 request pertama -> 429 Retry-After: 2"
      CHAOS_RYO_KEY="$FAKE_KEY" timeout 120 "$PY" "$SUPPORT" ryo-call \
        --url "https://127.0.0.1:$TPORT" --ca "$CERT" \
        --out "$CHAOS_DIR/ryo_call.json" --log "$CHAOS_DIR/ryo_call.log" --max-retries 3
      OUTCOME="$("$PY" -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get('outcome'),d.get('status'),d.get('elapsed_s'))" "$CHAOS_DIR/ryo_call.json" 2>/dev/null)"
      ELAPSED="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['elapsed_s'])" "$CHAOS_DIR/ryo_call.json" 2>/dev/null)"
      SEEN="$(cat "$CF" 2>/dev/null)"
      B429="$(grep -c 'HTTP 429, backoff' "$CHAOS_DIR/ryo_call.log")"
      note "hasil panggilan: $OUTCOME ; request diterima server: $SEEN ; baris 'HTTP 429, backoff': $B429"
      note "contoh log: $(grep -m1 'HTTP 429, backoff' "$CHAOS_DIR/ryo_call.log" | cut -c1-120)"
      HONOURED="$("$PY" -c "import sys;print('1' if float(sys.argv[1]) >= 3.5 else '0')" "${ELAPSED:-0}" 2>/dev/null)"
      OK=1
      case "$OUTCOME" in "ok ok "*) ;; *) OK=0; note "CACAT: panggilan tidak berakhir sukses";; esac
      [ "${SEEN:-0}" = "3" ] || { OK=0; note "CACAT: server melihat $SEEN request, diharapkan 3 (2x429 + 1x200)"; }
      [ "${B429:-0}" -ge 2 ] || { OK=0; note "CACAT: backoff 429 tidak tercatat di log"; }
      [ "$HONOURED" = "1" ] || { OK=0; note "CACAT: total tunggu ${ELAPSED}s < 2x Retry-After — header diabaikan"; }
      grep -qiF "$FAKE_KEY" "$CHAOS_DIR/ryo_call.log" "$CHAOS_DIR/ryo_call.json" \
        && { OK=0; note "CACAT: kunci muncul di log/keluaran"; }
      [ "$OK" = "1" ] \
        && record rate_limit_429 LULUS "2x429 lalu 200; menunggu ${ELAPSED}s (>= 2x Retry-After 2s); $B429 baris backoff; nol kunci di log" \
        || record rate_limit_429 GAGAL "lihat $CHAOS_DIR/ryo_call.log"
    else
      record rate_limit_429 GAGAL "server fixture tidak pernah menulis port — lihat $CHAOS_DIR/spawn.log"
    fi
    chaos_kill TERM "$SPID"
  fi
  printf '%s' "$NOTES" >> "$CHAOS_DIR/notes.md"
fi

# ==========================================================================================
# 4. DB dihapus saat UI hidup
# ==========================================================================================
if wanted db_deleted_ui; then
  head2 "4/7 db_deleted_ui — berkas SQLite dihapus di bawah kaki UI yang sedang melayani"
  NOTES=""
  UIDB="$CHAOS_DIR/ui.db"
  # SALINAN, bukan data/ryo.db. `db-copy` menolak menulis ke dalam data/ dan membuka sumber ro.
  "$PY" "$SUPPORT" db-copy --src "$REAL_DB" --dst "$UIDB" >"$CHAOS_DIR/uidb.log" 2>&1
  if [ ! -s "$UIDB" ]; then
    record db_deleted_ui DILEWATI "salinan DB gagal — lihat $CHAOS_DIR/uidb.log"
  else
    UPORT="$("$PY" "$SUPPORT" free-port)"
    ULOG="$CHAOS_DIR/uvicorn.log"
    chaos_spawn env DB_PATH="$UIDB" "$PY" -m uvicorn ui.app:app --host 127.0.0.1 --port "$UPORT"
    UPID="$LAST_PID"
    UP=0
    for _ in $(seq 1 100); do
      curl -fsS -m 3 "http://127.0.0.1:$UPORT/api/health" -o "$CHAOS_DIR/ui_health_ok.json" 2>/dev/null && { UP=1; break; }
      sleep 0.2
    done
    if [ "$UP" != "1" ]; then
      record db_deleted_ui DILEWATI "UI tidak pernah siap di port $UPORT — lihat $CHAOS_DIR/spawn.log"
    else
      note "UI hidup (pid $UPID) di 127.0.0.1:$UPORT dengan DB_PATH=$UIDB"
      B_DEC="$(curl -s -m 5 -o "$CHAOS_DIR/ui_dec_before.json" -w '%{http_code}' "http://127.0.0.1:$UPORT/api/decisions")"
      note "sebelum penghapusan: GET /api/decisions -> HTTP $B_DEC"
      rm -f "$UIDB" "$UIDB-wal" "$UIDB-shm"
      note "berkas $UIDB DIHAPUS (salinan; data/ryo.db tidak disentuh)"
      A_HEALTH="$(curl -s -m 5 -o "$CHAOS_DIR/ui_health_after.json" -w '%{http_code}' "http://127.0.0.1:$UPORT/api/health")"
      A_DEC="$(curl -s -m 5 -o "$CHAOS_DIR/ui_dec_after.json" -w '%{http_code}' "http://127.0.0.1:$UPORT/api/decisions")"
      # `/` hanyalah indeks rute (tidak menyentuh DB); LAYAR yang bisa berbohong hijau ada di
      # `/ui` dan `/ui/decision/{id}` — itulah yang harus dituntut jujur saat DB hilang.
      A_ROOT="$(curl -s -m 5 -o "$CHAOS_DIR/ui_screen_after.html" -w '%{http_code}' "http://127.0.0.1:$UPORT/ui")"
      A_CARD="$(curl -s -m 5 -o "$CHAOS_DIR/ui_card_after.html" -w '%{http_code}' "http://127.0.0.1:$UPORT/ui/decision/1")"
      A_WATCH="$(curl -s -m 5 -o "$CHAOS_DIR/ui_watch_after.json" -w '%{http_code}' "http://127.0.0.1:$UPORT/api/watchlist")"
      note "sesudah: /api/health=$A_HEALTH /api/decisions=$A_DEC /ui=$A_ROOT /ui/decision/1=$A_CARD /api/watchlist=$A_WATCH"
      note "badan /api/decisions sesudah: $(cut -c1-200 "$CHAOS_DIR/ui_dec_after.json" 2>/dev/null)"
      note "teks layar /ui sesudah: $(sed -e 's/<[^>]*>/ /g' "$CHAOS_DIR/ui_screen_after.html" | tr -s ' \n' ' ' | cut -c1-200)"
      ALIVE=0; kill -0 "$UPID" 2>/dev/null && ALIVE=1
      note "proses UI masih hidup sesudah penghapusan: $ALIVE"
      SIM="$(grep -c '"simulated":true' "$CHAOS_DIR/ui_dec_after.json" 2>/dev/null)"
      OK=1
      [ "$ALIVE" = "1" ] || { OK=0; note "CACAT: UI mati saat DB hilang"; }
      has_traceback "$ULOG" && { OK=0; note "CACAT: traceback di log uvicorn"; }
      has_traceback "$CHAOS_DIR/spawn.log" && { OK=0; note "CACAT: traceback di keluaran UI"; }
      for code in "$A_HEALTH" "$A_DEC" "$A_ROOT" "$A_CARD" "$A_WATCH"; do
        case "$code" in 2*|4*|5*) ;; *) OK=0; note "CACAT: jawaban bukan HTTP yang sah ($code)";; esac
      done
      grep -qF 'Internal Server Error' "$CHAOS_DIR/ui_dec_after.json" && { OK=0; note "CACAT: 500 tanpa penjelasan"; }
      # Layar yang masih menyanjung "TERVERIFIKASI"/"GO" tanpa satu baris pun DB adalah
      # kegagalan yang paling mahal di rubric: angka yang tampak sah padahal sumbernya hilang.
      grep -qiE 'terverifikasi|verified' "$CHAOS_DIR/ui_screen_after.html" \
        && { OK=0; note "CACAT: layar /ui masih mengklaim terverifikasi tanpa DB"; }
      [ "$OK" = "1" ] \
        && record db_deleted_ui LULUS "DB hilang: health=$A_HEALTH decisions=$A_DEC /ui=$A_ROOT /ui/decision/1=$A_CARD watchlist=$A_WATCH; proses tetap hidup; nol traceback; penanda simulated=$SIM" \
        || record db_deleted_ui GAGAL "lihat $CHAOS_DIR/spawn.log"
      chaos_kill TERM "$UPID"
    fi
  fi
  printf '%s' "$NOTES" >> "$CHAOS_DIR/notes.md"
fi

# ==========================================================================================
# 5. Perekam produksi HIDUP + make agent-tick (tambahan juri Fase 1)
# ==========================================================================================
if wanted agent_tick_live; then
  head2 "5/7 agent_tick_live — agen di-tick sementara perekam produksi berjalan"
  NOTES=""
  RECLOG="$ROOT/logs/recorder.log"
  R_ALIVE=0
  for p in $FORBIDDEN_PIDS; do kill -0 "$p" 2>/dev/null && R_ALIVE=1; done
  B429_BEFORE="$(grep -c 'HTTP 429' "$RECLOG" 2>/dev/null)"
  note "perekam produksi hidup: $R_ALIVE (pid:${FORBIDDEN_PIDS:- -}) — TIDAK disentuh sama sekali"
  note "cacah 'HTTP 429' di logs/recorder.log sebelum tick: ${B429_BEFORE:-n/a}"

  ADB="$CHAOS_DIR/agent.db"
  "$PY" "$SUPPORT" db-copy --src "$REAL_DB" --dst "$ADB" >"$CHAOS_DIR/agentdb.log" 2>&1
  if [ ! -s "$ADB" ]; then
    record agent_tick_live DILEWATI "salinan DB gagal — lihat $CHAOS_DIR/agentdb.log"
  else
    DEC_BEFORE="$(db_rows "$ADB" decisions)"
    T0="$(date +%s)"
    # Kredensial SENGAJA dihapus dari environment: agen 2.0b-lite adalah store-first murni,
    # jadi bila ia tetap menghasilkan keputusan tanpa kunci, itu bukti langsung nol panggilan
    # live — dan karenanya nol 429 baru yang bisa dibebankan kepadanya.
    env -u RYO_MCP_KEY -u RYO_MCP_URL \
      timeout 120 "$PY" -m agent.main --once --db "$ADB" --symbols SOL,BTC,ETH,BNB \
        >"$CHAOS_DIR/agent_tick.log" 2>&1
    ARC=$?
    T1="$(date +%s)"
    DUR=$((T1 - T0))
    DEC_AFTER="$(db_rows "$ADB" decisions)"
    B429_AFTER="$(grep -c 'HTTP 429' "$RECLOG" 2>/dev/null)"
    D429=$(( ${B429_AFTER:-0} - ${B429_BEFORE:-0} ))
    note "agen exit=$ARC durasi=${DUR}s decisions $DEC_BEFORE -> $DEC_AFTER"
    note "429 baru di log perekam selama jendela tick: $D429"
    OK=1
    [ "$DUR" -lt 60 ] || { OK=0; note "CACAT: tick memakan ${DUR}s (>= 60 dtk = stall)"; }
    [ "${DEC_AFTER:-0}" -gt "${DEC_BEFORE:-0}" ] || { OK=0; note "CACAT: tidak ada keputusan yang dibukukan"; }
    [ "$D429" -eq 0 ] || { OK=0; note "CACAT: $D429 HTTP 429 baru muncul selama jendela tick"; }
    has_traceback "$CHAOS_DIR/agent_tick.log" && { OK=0; note "CACAT: traceback di tick agen"; }
    [ "$OK" = "1" ] \
      && record agent_tick_live LULUS "tanpa kredensial: exit $ARC, ${DUR}s (<60), decisions $DEC_BEFORE -> $DEC_AFTER, 429 baru = $D429" \
      || record agent_tick_live GAGAL "lihat $CHAOS_DIR/agent_tick.log"
  fi
  printf '%s' "$NOTES" >> "$CHAOS_DIR/notes.md"
fi

# ==========================================================================================
# 6. Kredensial dicabut dengan MEMINDAHKAN .env (TASKS 1.10: `unset` tidak sah)
# ==========================================================================================
if wanted credentials_removed; then
  head2 "6/7 credentials_removed — .env DIPINDAHKAN (bukan unset), di pohon sandbox"
  NOTES=""
  ENV_BEFORE="$(stat -c '%s %Y' "$ROOT/.env" 2>/dev/null || echo 'tidak ada')"
  mkdir -p "$SANDBOX"
  cp -r "$ROOT/core" "$SANDBOX/core"
  cp "$ROOT/sources.yaml" "$SANDBOX/sources.yaml"
  # `.env` SINTETIS: kunci palsu + URL ke penghitung TCP lokal. Berkas `.env` ASLI tidak
  # pernah dibaca, disalin, atau dipindahkan oleh skenario ini.
  PF6="$CHAOS_DIR/tcp.port"; CF6="$CHAOS_DIR/tcp.count"
  chaos_spawn "$PY" "$SUPPORT" tcp-counter --port-file "$PF6" --count-file "$CF6"
  TPID="$LAST_PID"
  if ! wait_for_file "$PF6" 10; then
    record credentials_removed GAGAL "penghitung TCP tidak pernah menulis port"
  else
    CPORT="$(cat "$PF6")"
    {
      printf 'RYO_MCP_URL=https://127.0.0.1:%s/api/mcp\n' "$CPORT"
      printf 'RYO_MCP_KEY=%s\n' "$FAKE_KEY"
      printf 'WATCHLIST=SOL\nRECORDER_MAX_SYMBOLS=1\nRECORDER_MAX_FOLLOWUPS=1\n'
    } > "$SANDBOX/.env"

    run_sandbox() {  # berkas-log -> exit code
      ( cd "$SANDBOX" && env -u RYO_MCP_KEY -u RYO_MCP_URL RECORDER_LOCK_PATH="$CHAOS_DIR/sandbox.lock" \
          PYTHONPATH="$SANDBOX" timeout 240 "$PY" -m core.recorder --once --client live \
            --db "$CHAOS_DIR/sandbox.db" ) >"$1" 2>&1
    }

    run_sandbox "$CHAOS_DIR/creds_present.log"; RC_A=$?
    CONN_A="$(cat "$CF6" 2>/dev/null)"
    note "A) .env sandbox ADA: exit=$RC_A, koneksi yang dilihat server: ${CONN_A:-0}"

    mv "$SANDBOX/.env" "$SANDBOX/.env.dipindahkan"
    note "B) $SANDBOX/.env DIPINDAHKAN ke .env.dipindahkan (bukan \`unset\` — TASKS 1.10)"
    run_sandbox "$CHAOS_DIR/creds_moved.log"; RC_B=$?
    CONN_B="$(cat "$CF6" 2>/dev/null)"
    NEW_CONN=$(( ${CONN_B:-0} - ${CONN_A:-0} ))
    note "B) exit=$RC_B, koneksi BARU sesudah .env dipindahkan: $NEW_CONN"
    note "B) pesan: $(grep -m1 -i 'kosong\|tidak bisa dibentuk\|RYO_MCP' "$CHAOS_DIR/creds_moved.log" | cut -c1-140)"

    ENV_AFTER="$(stat -c '%s %Y' "$ROOT/.env" 2>/dev/null || echo 'tidak ada')"
    note ".env ASLI sebelum/sesudah: [$ENV_BEFORE] / [$ENV_AFTER]"

    OK=1
    [ "${CONN_A:-0}" -ge 1 ] || { OK=0; note "CACAT: dengan .env pun perekam tidak menghubungi apa pun — skenario tidak membuktikan apa-apa"; }
    [ "$NEW_CONN" -eq 0 ] || { OK=0; note "CACAT: $NEW_CONN koneksi TETAP terjadi tanpa .env"; }
    [ "$RC_B" = "2" ] || { OK=0; note "CACAT: exit $RC_B, diharapkan 2 (tidak bisa mulai)"; }
    [ "$ENV_BEFORE" = "$ENV_AFTER" ] || { OK=0; note "CACAT: .env ASLI berubah — ini bug KESELAMATAN skrip chaos"; }
    has_traceback "$CHAOS_DIR/creds_moved.log" && { OK=0; note "CACAT: traceback saat kredensial hilang"; }
    grep -qiF "$FAKE_KEY" "$CHAOS_DIR/creds_present.log" "$CHAOS_DIR/creds_moved.log" \
      && { OK=0; note "CACAT: kunci muncul di log perekam"; }
    [ "$OK" = "1" ] \
      && record credentials_removed LULUS "dengan .env: ${CONN_A} koneksi; .env dipindahkan: exit $RC_B, $NEW_CONN koneksi baru, nol traceback, .env asli utuh" \
      || record credentials_removed GAGAL "lihat $CHAOS_DIR/creds_moved.log"
    chaos_kill TERM "$TPID"
  fi
  printf '%s' "$NOTES" >> "$CHAOS_DIR/notes.md"
fi

# ==========================================================================================
# 7. Jendela tanpa bukti — apakah preflight() MENJAWAB atau MELEMPAR? (TASKS 0.23)
# ==========================================================================================
# Ini bukan kegagalan infrastruktur melainkan kegagalan KONTRAK, dan ia mendarat di AC yang
# sama: `core/skill_cli.py` hanya menerjemahkan `PreflightError` menjadi envelope, jadi
# exception jenis lain (0.23 melaporkan `TypeError: 'NoneType' object is not subscriptable`)
# keluar sebagai traceback mentah. Kelima jam yang dilaporkan ditanam sebagai konstanta di
# `chaos_support.py`; sisanya sapuan mundur dari sekarang.
if wanted evidence_gap; then
  head2 "7/7 evidence_gap — preflight() di jam-jam tanpa bukti (TASKS 0.23)"
  NOTES=""
  PDB="$CHAOS_DIR/preflight.db"
  "$PY" "$SUPPORT" db-copy --src "$REAL_DB" --dst "$PDB" >"$CHAOS_DIR/pdb.log" 2>&1
  if [ ! -s "$PDB" ]; then
    record evidence_gap DILEWATI "salinan DB gagal — lihat $CHAOS_DIR/pdb.log"
  else
    ANCHOR="$(date -u +%Y-%m-%dT%H:00)"
    timeout 900 "$PY" "$SUPPORT" preflight-sweep --db "$PDB" --out "$CHAOS_DIR/sweep.json" \
      --anchor "$ANCHOR" --symbols SOL,BTC,ETH,BNB --hours-back 24 --step-min 30 \
      >"$CHAOS_DIR/sweep.log" 2>&1
    SRC=$?
    CALLS="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['calls'])" "$CHAOS_DIR/sweep.json" 2>/dev/null)"
    RAISES="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['raises'])" "$CHAOS_DIR/sweep.json" 2>/dev/null)"
    STATS="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['statuses'])" "$CHAOS_DIR/sweep.json" 2>/dev/null)"
    note "sapuan exit=$SRC, jangkar $ANCHOR, panggilan=$CALLS, MELEMPAR=$RAISES"
    note "sebaran status: $STATS"
    [ "${RAISES:-1}" -gt 0 ] && note "contoh lemparan: $("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['first_raises'])" "$CHAOS_DIR/sweep.json" 2>/dev/null | cut -c1-240)"
    OK=1
    [ "$SRC" = "0" ] || { OK=0; note "CACAT: sapuan sendiri gagal (exit $SRC)"; }
    [ "${RAISES:-1}" -eq 0 ] || { OK=0; note "CACAT: $RAISES panggilan melempar, bukan menjawab envelope — TASKS 0.23"; }
    has_traceback "$CHAOS_DIR/sweep.log" && { OK=0; note "CACAT: traceback di sapuan"; }
    [ "$OK" = "1" ] \
      && record evidence_gap LULUS "$CALLS panggilan (5 jam bernama 0.23 + sapuan 24 j), nol melempar, sebaran $STATS" \
      || record evidence_gap GAGAL "lihat $CHAOS_DIR/sweep.json"
  fi
  printf '%s' "$NOTES" >> "$CHAOS_DIR/notes.md"
fi

# ==========================================================================================
# Invarian bukti + ringkasan
# ==========================================================================================
head2 "invarian bukti"
DB_AFTER_ROWS="$(db_rows "$REAL_DB" snapshots)"
DB_AFTER_MD5="$(md5sum "$REAL_DB" 2>/dev/null | awk '{print $1}')"
DB_AFTER_SIZE="$(stat -c %s "$REAL_DB" 2>/dev/null)"
say "data/ryo.db snapshots: $DB_BEFORE_ROWS -> $DB_AFTER_ROWS"
say "data/ryo.db size     : $DB_BEFORE_SIZE -> $DB_AFTER_SIZE"
say "data/ryo.db md5      : $DB_BEFORE_MD5 -> $DB_AFTER_MD5"
say "(md5 boleh berubah: perekam produksi menambah baris. Yang TIDAK boleh: baris menyusut.)"
DB_OK=1
[ -f "$REAL_DB" ] || { DB_OK=0; say "CACAT BERAT: data/ryo.db hilang"; }
[ "${DB_AFTER_ROWS:-0}" -ge "${DB_BEFORE_ROWS:-0}" ] || { DB_OK=0; say "CACAT BERAT: baris snapshots menyusut"; }
if [ "$DB_OK" = "1" ]; then
  record bukti_utuh LULUS "data/ryo.db ada, snapshots $DB_BEFORE_ROWS -> $DB_AFTER_ROWS (tidak menyusut)"
else
  record bukti_utuh GAGAL "data/ryo.db rusak/menyusut — SELIDIKI SEBELUM APA PUN"
fi

head2 "ringkasan"
printf '| skenario | hasil | bukti |\n|---|---|---|\n%s\n' "$(cat "$SUMMARY")"
say "LULUS=$PASS GAGAL=$FAIL DILEWATI=$SKIP"
if [ -n "$REPORT" ]; then
  {
    printf '| skenario | hasil | bukti |\n|---|---|---|\n'
    cat "$SUMMARY"
    printf '\nLULUS=%s GAGAL=%s DILEWATI=%s — dihasilkan `bash scripts/chaos.sh`\n' "$PASS" "$FAIL" "$SKIP"
  } > "$REPORT"
  say "tabel ditulis ke $REPORT"
fi
[ "$FAIL" -eq 0 ] || exit 1
exit 0
