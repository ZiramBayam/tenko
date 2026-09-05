"""Tes untuk `scripts/chaos.sh` + `scripts/chaos_support.py` (TASKS 4.5).

Yang diuji di sini BUKAN "apakah chaos lulus" — itu dijawab dengan menjalankan skripnya dan
menempel keluarannya di `docs/reports/chaos-<tgl>.md`. Yang diuji adalah **pagar keselamatannya**,
karena chaos test adalah satu-satunya skrip di repo ini yang tugasnya membunuh proses dan
menghapus basis data, dan di mesin yang sama hidup perekam produksi serta `data/ryo.db` yang
berisi ribuan baris bukti yang tidak bisa dibuat ulang (ADR-006).

Pagar yang punya tes di bawah:

1. `chaos_kill` menolak pid yang tidak di-spawn skrip itu sendiri, dan menolak pid yang bukan
   anak LANGSUNG shell-nya. Dua-duanya harus menolak sebelum satu sinyal pun terkirim.
2. Tidak ada pembunuhan berbasis pola (`pkill`, `killall`, `kill $(pgrep ...)`). Pola itulah
   yang akan mengenai perekam produksi; ia dilarang secara statis, bukan lewat kedisiplinan.
3. `db-copy` menolak menulis ke dalam `data/`, dan tidak menyentuh sumbernya.
4. Kelima jam yang TASKS 0.23 laporkan ditanam sebagai konstanta, sehingga probe "jendela tanpa
   bukti" tetap menanyakan pertanyaan yang sama walau isi DB berubah.

Pid 1 dipakai sebagai kenari di tes penolakan: bila pagarnya bocor, `kill` terhadap pid 1 tetap
gagal dengan EPERM untuk pengguna biasa — tes yang salah tidak boleh merusak apa pun.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHAOS_SH = ROOT / "scripts" / "chaos.sh"
SUPPORT = ROOT / "scripts" / "chaos_support.py"
REAL_DB = ROOT / "data" / "ryo.db"


def run_lib(snippet: str) -> subprocess.CompletedProcess[str]:
    """Jalankan potongan bash SESUDAH `scripts/chaos.sh` di-source sebagai pustaka.

    `CHAOS_LIB_ONLY=1` membuat skrip berhenti tepat sesudah definisi fungsi: nol skenario
    berjalan, jadi tes ini tidak pernah menyentuh proses atau berkas produksi.
    """
    return subprocess.run(
        ["/bin/bash", "-c", f'source "{CHAOS_SH}"\n{snippet}'],
        capture_output=True,
        text=True,
        # Environment dibuat dari nol: `chaos.sh` membaca `TMPDIR`, `RECORDER_LOCK_PATH`, dan
        # kawan-kawannya, dan tes tidak boleh berperilaku lain hanya karena shell pemanggilnya
        # kebetulan punya variabel itu.
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "CHAOS_LIB_ONLY": "1"},
        timeout=60,
        check=False,
    )


def run_support(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SUPPORT), *args], capture_output=True, text=True, timeout=120, check=False
    )


# --- pagar 1: siapa yang boleh dibunuh ------------------------------------------------------
def test_chaos_kill_refuses_a_pid_it_did_not_spawn() -> None:
    """Pid asing ditolak SEBELUM sinyal apa pun — ini pagar yang melindungi perekam produksi."""
    done = run_lib("chaos_kill TERM 1")
    assert done.returncode == 3, done.stderr
    assert "menolak kill" in done.stderr
    assert "tidak di-spawn skrip ini" in done.stderr


def test_chaos_kill_refuses_a_pid_that_is_not_its_own_child() -> None:
    """Terdaftar di `SPAWNED` saja tidak cukup: ppid-nya harus PERSIS shell chaos.

    Daftar bisa tercemar (mis. pid dipakai ulang setelah proses kita mati); cek ppid adalah
    pagar yang tidak bisa ditipu oleh daftar yang salah.
    """
    done = run_lib("SPAWNED+=(1)\nchaos_kill TERM 1")
    assert done.returncode == 3, done.stderr
    assert "bukan anak langsung shell ini" in done.stderr


def test_chaos_kill_accepts_a_process_it_spawned_itself() -> None:
    """Pagar yang menolak SEMUANYA tidak menguji apa pun — skenario `kill -9` harus tetap bisa."""
    done = run_lib(
        'chaos_spawn sleep 30\nkill -0 "$LAST_PID" && echo HIDUP\n'
        'chaos_kill 9 "$LAST_PID"\nsleep 0.4\n'
        'kill -0 "$LAST_PID" 2>/dev/null && echo MASIH-HIDUP || echo MATI'
    )
    assert "HIDUP" in done.stdout
    assert "MATI" in done.stdout
    assert "MASIH-HIDUP" not in done.stdout


def test_chaos_kill_refuses_the_pid_recorded_in_logs_pidfiles() -> None:
    """Lapis kedua: apa pun yang tercatat di `logs/*.pid` ditolak dengan alasannya sendiri."""
    pidfile = ROOT / "logs" / "recorder.pid"
    if not pidfile.exists():
        pytest.skip("logs/recorder.pid tidak ada di mesin ini")
    pid = pidfile.read_text(encoding="utf-8").strip()
    # Pid itu SENGAJA dimasukkan ke SPAWNED supaya pagar pertama lewat dan pagar daftar-hitam
    # yang diuji. Tidak ada sinyal terkirim: `fatal` keluar lebih dulu.
    done = run_lib(f"SPAWNED+=({pid})\nchaos_kill TERM {pid}")
    assert done.returncode == 3, done.stderr
    assert "proses produksi" in done.stderr


# --- pagar 2: nol pembunuhan berbasis pola --------------------------------------------------
def _code_lines(path: Path) -> list[tuple[int, str]]:
    """Baris yang benar-benar DIJALANKAN: komentar dibuang.

    Larangan di bawah harus berlaku untuk kode, bukan untuk prosa yang MENJELASKAN larangannya —
    kalau tidak, satu-satunya cara lulus adalah berhenti mendokumentasikan alasannya.
    """
    out = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append((lineno, stripped))
    return out


@pytest.mark.parametrize("forbidden", ["pkill", "killall", "pgrep", "kill -9 $(", "kill %"])
def test_chaos_script_never_kills_by_pattern(forbidden: str) -> None:
    """`pkill -f recorder` akan mengenai perekam produksi. Dilarang secara statis."""
    for lineno, line in _code_lines(CHAOS_SH):
        assert forbidden not in line, f"{CHAOS_SH}:{lineno} membunuh berbasis pola: {line}"


def test_chaos_script_never_removes_or_moves_the_real_db() -> None:
    """Tidak satu pun `rm`/`mv`/redirect yang menyebut `data/ryo.db` atau `$REAL_DB`."""
    for lineno, stripped in _code_lines(CHAOS_SH):
        if "REAL_DB" not in stripped and "data/ryo.db" not in stripped:
            continue
        for verb in ("rm ", "mv ", "truncate", "> $REAL_DB", '> "$REAL_DB"'):
            assert verb not in stripped, f"{CHAOS_SH}:{lineno} menyentuh DB produksi: {stripped}"


def test_chaos_script_declares_the_evidence_invariant() -> None:
    """Skrip harus MENUNTUT baris `snapshots` tidak menyusut, bukan sekadar melaporkannya."""
    text = CHAOS_SH.read_text(encoding="utf-8")
    assert "DB_AFTER_ROWS" in text
    assert "menyusut" in text
    assert "record bukti_utuh GAGAL" in text


# --- pagar 3: salinan DB ---------------------------------------------------------------------
def test_db_copy_refuses_a_destination_inside_data(tmp_path: Path) -> None:
    """Tujuan di dalam `data/` ditolak: di situlah bukti yang tidak bisa dibuat ulang tinggal."""
    src = tmp_path / "src.db"
    sqlite3.connect(src).close()
    dst = ROOT / "data" / "chaos-should-never-exist.db"
    done = run_support("db-copy", "--src", str(src), "--dst", str(dst))
    assert done.returncode != 0
    assert "menolak" in done.stderr
    assert not dst.exists()


def test_db_copy_leaves_the_source_byte_identical(tmp_path: Path) -> None:
    """Salinan online tidak boleh menulis apa pun ke sumbernya — termasuk `-wal`/`-shm` baru."""
    src = tmp_path / "src.db"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    before = (src.read_bytes(), src.stat().st_mtime_ns)

    dst = tmp_path / "out" / "copy.db"
    done = run_support("db-copy", "--src", str(src), "--dst", str(dst))
    assert done.returncode == 0, done.stderr
    assert dst.exists()
    assert (src.read_bytes(), src.stat().st_mtime_ns) == before
    assert sqlite3.connect(dst).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def test_db_check_reports_missing_file_without_raising(tmp_path: Path) -> None:
    """Skenario "DB dihapus" memanggil ini SESUDAH penghapusan: ia harus menjawab, bukan meledak."""
    done = run_support("db-check", "--db", str(tmp_path / "hilang.db"), "--table", "snapshots")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["exists"] is False


def test_db_check_reports_sidecar_files_left_behind_by_a_crash(tmp_path: Path) -> None:
    """`kill -9` meninggalkan `-journal`/`-wal`. Ia harus DILAPORKAN, bukan diam-diam diabaikan.

    Chaos run 5 Sep 2026 hampir menyimpulkan "DB rusak" dari jurnal panas yang sebenarnya normal;
    pembaca laporan hanya bisa menilai itu kalau keberadaan sidecar-nya ikut tercetak.
    """
    src = tmp_path / "crash.db"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE snapshots (a INTEGER)")
    conn.commit()
    conn.close()
    (tmp_path / "crash.db-journal").write_bytes(b"")

    done = run_support("db-check", "--db", str(src), "--table", "snapshots")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["sidecars"] == ["-journal"]


def test_db_check_without_recover_writes_nothing(tmp_path: Path) -> None:
    """Mode default dipakai untuk mem-poll DB yang penulisnya HIDUP: ia tidak boleh menulis."""
    src = tmp_path / "poll.db"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE snapshots (a INTEGER)")
    conn.commit()
    conn.close()
    before = (src.read_bytes(), src.stat().st_mtime_ns)

    done = run_support("db-check", "--db", str(src), "--table", "snapshots")
    assert json.loads(done.stdout)["recover"] is False
    assert (src.read_bytes(), src.stat().st_mtime_ns) == before


def test_the_kill9_verdict_is_taken_from_a_recovering_connection() -> None:
    """Vonis integritas sesudah SIGKILL WAJIB memakai `--recover`.

    Tanpa itu, `mode=ro` gagal dengan `attempt to write a readonly database` pada jurnal panas
    dan chaos akan melaporkan kerusakan yang tidak ada — persis yang terjadi 5 Sep 2026 sebelum
    ini diperbaiki. Tes ini menahan perbaikannya di tempat.
    """
    text = CHAOS_SH.read_text(encoding="utf-8")
    start = text.index("2. kill -9 perekam mid-siklus")
    end = text.index("3. HTTP 429")
    body = text[start:end]
    assert 'db-check --recover --db "$KDB"' in body
    assert "jurnal" in body.lower()


# --- pagar 4: probe TASKS 0.23 ---------------------------------------------------------------
def test_the_named_instants_are_exactly_the_ones_task_0_23_reported() -> None:
    """Kelima jam itu adalah reproduksi yang dilaporkan; menghitungnya ulang akan kehilangannya."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from chaos_support import TASK_0_23_INSTANTS

    assert TASK_0_23_INSTANTS == (
        "2026-09-02T12:30",
        "2026-09-02T17:00",
        "2026-09-03T19:30",
        "2026-09-04T02:00",
        "2026-09-04T21:30",
    )


def test_preflight_never_raises_on_the_five_reported_instants() -> None:
    """AC 4.5 "nol traceback": `preflight()` harus MENJAWAB envelope, bukan melempar.

    TASKS 0.23 melaporkan `TypeError: 'NoneType' object is not subscriptable` di kelima jam ini.
    `core/skill_cli.py` hanya menerjemahkan `PreflightError`, jadi exception jenis lain lolos
    sebagai traceback mentah. Tes ini menahan pintunya walau bug-nya sudah tidak reproduksi.
    """
    if not REAL_DB.exists():
        pytest.skip("data/ryo.db tidak ada di mesin ini")
    from datetime import UTC, datetime

    from core.store import Store
    from preflight.skill import preflight

    sys.path.insert(0, str(ROOT / "scripts"))
    from chaos_support import TASK_0_23_INSTANTS

    # BACA-SAJA: tes tidak pernah menulis ke bukti produksi.
    store = Store(str(REAL_DB), read_only=True)
    try:
        for text in TASK_0_23_INSTANTS:
            now = datetime.fromisoformat(text).replace(tzinfo=UTC)
            result = preflight("SOL", "swing", store=store, now=now)
            assert result.status in {"ok", "partial", "unavailable"}, (text, result.status)
    finally:
        store.close()
