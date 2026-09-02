"""Tes TASKS 1.13 — `daemons.sh status` harus MENDETEKSI perekam mati.

Insiden 1 Sep 2026 23:39: perekam live (pid 160582/160586) dibunuh dari luar pukul 22:55 di
tengah siklus, nol ERROR di log, dan `status` tetap melaporkan "JALAN" karena ia hanya
membaca `logs/recorder.pid`. Selama ~45 menit tidak ada yang tahu; jumlah snapshot beku di
689. Data pasar ber-timestamp tidak bisa dibuat ulang, jadi status yang berbohong = jam yang
hilang selamanya.

Tiga hal yang dijaga tes ini lebih keras daripada "ada tulisannya":

1. **Tidak ada sinyal.** `status` hanya MENYEBUT pid. Dibuktikan dengan proses uji yang harus
   MASIH HIDUP setelah status berjalan, dan dengan pembacaan sumber (`os.kill`, `signal`,
   `subprocess`, `kill`/`pkill` di shell tidak boleh ada di jalur status).
2. **`logs/recorder.lock` tidak pernah dihapus.** `flock` itu advisory pada *inode*: menghapus
   berkas saat pemegangnya hidup membuat instance berikutnya membuka inode baru dan mendapat
   kunci sendiri → dua perekam atas satu kunci API = 2×60 panggilan/menit. Tes menuntut
   berkas DAN `st_ino`-nya utuh, termasuk saat kuncinya sedang dipegang proses lain.
3. **Nol byte tertulis ke DB.** DB dan direktorinya dibuat hanya-baca di filesystem; status
   tetap harus berhasil (SQLite yang menulis journal akan gagal di lingkungan itu).

Semua penulisan di berkas ini HANYA ke `tmp_path`. `data/ryo.db`, `logs/recorder.pid`, dan
`logs/recorder.lock` yang hidup tidak pernah disentuh — termasuk oleh tes integrasi shell,
yang menyalin `scripts/` ke tmp_path supaya `cd "$(dirname "$0")/.."` menunjuk sandbox.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "proc_status.py"
DB_STATUS = ROOT / "scripts" / "db_status.py"
DAEMONS = ROOT / "scripts" / "daemons.sh"


def _load_module():
    spec = importlib.util.spec_from_file_location("tenko_proc_status_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ps = _load_module()


# --- helper ------------------------------------------------------------------------------
def _dead_pid() -> int:
    """Pid yang PASTI sudah tidak ada: proses yang selesai DAN sudah dipanen (bukan zombie)."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _fake_proc(root: Path, pid: int, argv: list[str], state: str = "S") -> None:
    """Entri /proc palsu. `argv` boleh KOSONG — itu yang dilakukan kernel thread."""
    entry = root / str(pid)
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))
    (entry / "stat").write_text(f"{pid} (python3) {state} 1 1 1", encoding="utf-8")


def _stub_recorder(tmp_path: Path, extra: list[str]) -> subprocess.Popen:
    """Proses nyata ber-cmdline `python -m core.recorder ...` tanpa menyentuh apa pun.

    Modul aslinya sengaja TIDAK dipakai: yang diuji adalah pencocokan cmdline, dan perekam
    asli akan mencoba mengambil `logs/recorder.lock` produksi.
    """
    pkg = tmp_path / "core"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "recorder.py").write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, "-m", "core.recorder", *extra],
        cwd=str(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_alive(proc: subprocess.Popen, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if Path(f"/proc/{proc.pid}/cmdline").exists():
            return
        time.sleep(0.05)
    raise AssertionError("proses uji tidak pernah muncul di /proc")


def _lock_holder(path: Path) -> subprocess.Popen:
    code = (
        "import fcntl,sys,time\n"
        f"h=open({str(path)!r},'a+')\n"
        "fcntl.flock(h.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "sys.stdout.write('ready\\n'); sys.stdout.flush()\n"
        "time.sleep(120)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "ready"
    return proc


# --- 1. pid mati vs hidup ------------------------------------------------------------------
def test_pid_alive_true_for_self() -> None:
    assert ps.pid_is_alive(os.getpid()) is True


def test_pid_alive_false_for_finished_process() -> None:
    assert ps.pid_is_alive(_dead_pid()) is False


def test_pid_alive_false_for_zombie() -> None:
    """Proses zombie masih punya `/proc/<pid>`; ia tidak merekam apa pun, jadi bukan "JALAN"."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            text = Path(f"/proc/{proc.pid}/stat").read_text(encoding="utf-8", errors="replace")
            if ") Z" in text:
                break
            time.sleep(0.02)
        else:
            pytest.skip("proses uji tidak sempat menjadi zombie di mesin ini")
        assert ps.pid_is_alive(proc.pid) is False
    finally:
        proc.wait()


@pytest.mark.parametrize("bad", ["", "   ", "abc", "-1", "0", "999999999999999999999"])
def test_read_pid_file_rejects_garbage(tmp_path: Path, bad: str) -> None:
    path = tmp_path / "recorder.pid"
    path.write_text(bad, encoding="utf-8")
    assert ps.read_pid_file(path) is None


def test_read_pid_file_reads_number_with_newline(tmp_path: Path) -> None:
    path = tmp_path / "recorder.pid"
    path.write_text("4863\n", encoding="utf-8")
    assert ps.read_pid_file(path) == 4863


# --- AC 1: "MATI (pid file basi)" + pid file dihapus ---------------------------------------
def test_stale_pid_file_reported_and_removed(tmp_path: Path) -> None:
    pid_file = tmp_path / "recorder.pid"
    pid_file.write_text(f"{_dead_pid()}\n", encoding="utf-8")
    (tmp_path / "recorder.log").write_text("baris terakhir\n", encoding="utf-8")

    lines, alive = ps.check_daemon("recorder", pid_file, tmp_path / "recorder.log")

    assert alive is None
    joined = "\n".join(lines)
    assert "MATI (pid file basi)" in joined
    assert not pid_file.exists(), "pid file basi harus dihapus"


def test_garbage_pid_file_reported_and_removed(tmp_path: Path) -> None:
    pid_file = tmp_path / "recorder.pid"
    pid_file.write_text("bukan angka\n", encoding="utf-8")
    lines, alive = ps.check_daemon("recorder", pid_file, tmp_path / "recorder.log")
    assert alive is None
    assert "MATI (pid file basi)" in "\n".join(lines)
    assert not pid_file.exists()


def test_live_pid_file_is_kept_and_tail_shown(tmp_path: Path) -> None:
    proc = _stub_recorder(tmp_path, [])
    try:
        _wait_alive(proc)
        pid_file = tmp_path / "recorder.pid"
        pid_file.write_text(f"{proc.pid}\n", encoding="utf-8")
        log = tmp_path / "recorder.log"
        log.write_text("lama\nbaris terakhir yang penting\n", encoding="utf-8")

        lines, alive = ps.check_daemon("recorder", pid_file, log)

        joined = "\n".join(lines)
        assert alive == proc.pid
        assert "JALAN" in joined
        assert "baris terakhir yang penting" in joined
        assert pid_file.exists(), "pid file milik proses hidup tidak boleh dihapus"
    finally:
        proc.kill()
        proc.wait()


# --- [TINGGI] pid daur-ulang: hidup TETAPI bukan daemon kita --------------------------------
def test_recycled_pid_is_not_reported_running(tmp_path: Path) -> None:
    """Proses lain yang mewarisi pid perekam tidak boleh membuat status berkata JALAN.

    Kalau ia berkata JALAN, `start_one` di daemons.sh akan menyimpulkan "sudah jalan" dan
    perekam tidak pernah menyala lagi — insiden 1 Sep, dengan status yang membenarkannya.
    """
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        pid_file = tmp_path / "recorder.pid"
        pid_file.write_text(f"{other.pid}\n", encoding="utf-8")

        lines, alive = ps.check_daemon("recorder", pid_file, tmp_path / "recorder.log")

        joined = "\n".join(lines)
        assert alive is None
        assert "JALAN" not in joined
        assert ps.MISMATCH_PID in joined
        assert "BUKAN core.recorder" in joined
        assert str(other.pid) in joined, "pid harus disebut supaya operator bisa menanganinya"
        assert other.poll() is None, "status mengirim sinyal ke pid asing"
    finally:
        other.kill()
        other.wait()


# --- tiga cabang pid file, dikunci terpisah -------------------------------------------------
def test_branch_dead_pid_file_is_removed(tmp_path: Path) -> None:
    pid_file = tmp_path / "recorder.pid"
    pid_file.write_text(f"{_dead_pid()}\n", encoding="utf-8")
    lines, alive = ps.check_daemon("recorder", pid_file, tmp_path / "recorder.log")
    assert alive is None
    assert ps.STALE_PID in "\n".join(lines)
    assert not pid_file.exists(), "pid MATI: berkasnya memang harus dibersihkan (AC 1)"


def test_branch_live_matching_pid_file_is_kept(tmp_path: Path) -> None:
    proc = _stub_recorder(tmp_path, [])
    try:
        _wait_alive(proc)
        pid_file = tmp_path / "recorder.pid"
        pid_file.write_text(f"{proc.pid}\n", encoding="utf-8")
        lines, alive = ps.check_daemon("recorder", pid_file, tmp_path / "recorder.log")
        assert alive == proc.pid
        assert "JALAN" in "\n".join(lines)
        assert pid_file.exists()
    finally:
        proc.kill()
        proc.wait()


def test_branch_live_mismatching_pid_file_is_kept_for_the_operator(tmp_path: Path) -> None:
    """Hidup tetapi bukan daemon kita: BUKAN JALAN, tetapi pid file DIPERTAHANKAN.

    `daemons.sh stop` bersyarat `[ -f logs/<name>.pid ]`. Menghapus berkas milik proses yang
    masih hidup membuat proses itu tidak bisa lagi dihentikan lewat `make daemons ARGS=stop`,
    sekaligus tak terlihat di baris YATIM (matcher-nya sama) — status mencabut kendali
    operator, jauh melampaui AC 1 yang hanya meminta membersihkan pid file yang pid-nya MATI.
    """
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        pid_file = tmp_path / "recorder.pid"
        pid_file.write_text(f"{other.pid}\n", encoding="utf-8")
        lines, alive = ps.check_daemon("recorder", pid_file, tmp_path / "recorder.log")
        joined = "\n".join(lines)

        assert alive is None
        assert "JALAN" not in joined
        assert pid_file.exists(), "pid HIDUP: berkasnya harus dipertahankan agar stop tetap bisa"
        assert pid_file.read_text(encoding="utf-8").strip() == str(other.pid)
        assert "SENGAJA tidak dihapus" in joined
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()


def test_orphan_scan_limit_is_written_down() -> None:
    """Matcher yatim = matcher identitas; bentuk tak dikenal tidak muncul sebagai YATIM."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "pemindaian yatim memakai matcher yang SAMA" in text


def test_agent_pid_must_run_agent_main(tmp_path: Path) -> None:
    """Identitas dicek per daemon: pid perekam di `agent.pid` bukan agen yang hidup."""
    proc_root = tmp_path / "proc"
    logs = tmp_path / "logs"
    logs.mkdir()
    _fake_proc(proc_root, 500, ["uv", "run", "python", "-m", "core.recorder"])
    _fake_proc(proc_root, 501, ["uv", "run", "python", "-m", "agent.main", "--client", "live"])

    (logs / "agent.pid").write_text("500\n", encoding="utf-8")
    lines, alive = ps.check_daemon("agent", logs / "agent.pid", logs / "agent.log", proc_root)
    assert alive is None and "BUKAN agent.main" in "\n".join(lines)

    (logs / "agent.pid").write_text("501\n", encoding="utf-8")
    lines, alive = ps.check_daemon("agent", logs / "agent.pid", logs / "agent.log", proc_root)
    assert alive == 501 and "JALAN (pid 501)" in "\n".join(lines)


def test_unknown_daemon_name_says_identity_unverified(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    logs = tmp_path / "logs"
    logs.mkdir()
    _fake_proc(proc_root, 600, ["/usr/bin/anything", "--flag"])
    (logs / "ui.pid").write_text("600\n", encoding="utf-8")
    lines, alive = ps.check_daemon("ui", logs / "ui.pid", logs / "ui.log", proc_root)
    joined = "\n".join(lines)
    assert alive == 600
    assert "JALAN" in joined and "TIDAK diverifikasi" in joined


def test_recorder_script_path_form_is_recognised(tmp_path: Path) -> None:
    """`python /repo/core/recorder.py` sah dan tidak boleh dianggap pid daur-ulang."""
    proc_root = tmp_path / "proc"
    logs = tmp_path / "logs"
    logs.mkdir()
    _fake_proc(proc_root, 700, ["/usr/bin/python3", "/home/x/tenko/core/recorder.py"])
    (logs / "recorder.pid").write_text("700\n", encoding="utf-8")
    lines, alive = ps.check_daemon("recorder", logs / "recorder.pid", logs / "x.log", proc_root)
    assert alive == 700 and "JALAN" in "\n".join(lines)


# --- [SEDANG] cmdline kosong (kernel thread) tidak boleh mematikan keluaran ------------------
def test_empty_cmdline_does_not_crash_check_daemon(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    logs = tmp_path / "logs"
    logs.mkdir()
    _fake_proc(proc_root, 800, [])  # kernel thread: cmdline benar-benar kosong
    (logs / "recorder.pid").write_text("800\n", encoding="utf-8")
    lines, alive = ps.check_daemon("recorder", logs / "recorder.pid", logs / "x.log", proc_root)
    joined = "\n".join(lines)
    assert alive is None
    assert "JALAN" not in joined
    assert ps.MISMATCH_PID in joined
    # pid 800 masih hidup di /proc palsu, jadi berkasnya dipertahankan (kendali operator)
    assert (logs / "recorder.pid").exists()


def test_empty_cmdline_anywhere_does_not_kill_the_whole_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Satu `[kworker]` tidak boleh menghapus baris kunci dan pemindaian yatim."""
    proc_root = tmp_path / "proc"
    logs = tmp_path / "logs"
    logs.mkdir()
    _fake_proc(proc_root, 2, [])
    _fake_proc(proc_root, 900, ["uv", "run", "python", "-m", "core.recorder"])
    (logs / "recorder.pid").write_text("2\n", encoding="utf-8")
    lock = logs / "recorder.lock"
    lock.touch()

    code = ps.main(
        ["--dir", str(logs), "--name", "recorder", "--proc-root", str(proc_root), "--lock", str(lock)]
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "kunci:" in out, "baris kunci hilang — IndexError memakan seluruh keluaran"
    assert "YATIM pid 900" in out


def test_missing_pid_file_is_not_an_error(tmp_path: Path) -> None:
    lines, alive = ps.check_daemon("agent", tmp_path / "agent.pid", tmp_path / "agent.log")
    assert alive is None
    assert "mati" in "\n".join(lines).lower()


# --- AC 1: lock TIDAK PERNAH dihapus -------------------------------------------------------
def test_lock_held_by_other_process_is_reported_and_untouched(tmp_path: Path) -> None:
    lock = tmp_path / "recorder.lock"
    lock.write_text("999\n", encoding="utf-8")
    before = lock.stat()
    holder = _lock_holder(lock)
    try:
        state = ps.lock_state(lock)
        line = ps.lock_line(lock)
    finally:
        holder.kill()
        holder.wait()

    assert state == ps.LOCK_HELD
    assert "dipegang" in line
    assert lock.exists(), "berkas kunci tidak boleh dihapus"
    after = lock.stat()
    assert after.st_ino == before.st_ino, "inode kunci berubah — flock advisory jadi tidak berarti"


def test_lock_without_holder_is_reported_stale_but_kept(tmp_path: Path) -> None:
    lock = tmp_path / "recorder.lock"
    lock.write_text("4863\n", encoding="utf-8")
    before = lock.stat()

    assert ps.lock_state(lock) == ps.LOCK_FREE
    line = ps.lock_line(lock)

    assert "basi" in line
    assert lock.exists()
    assert lock.stat().st_ino == before.st_ino
    assert lock.read_text(encoding="utf-8") == "4863\n", "isi kunci tidak boleh diubah"


def test_lock_probe_releases_immediately(tmp_path: Path) -> None:
    """Probe tidak boleh menahan kunci: perekam yang start setelah status harus tetap bisa."""
    lock = tmp_path / "recorder.lock"
    lock.touch()
    assert ps.lock_state(lock) == ps.LOCK_FREE
    holder = _lock_holder(lock)  # gagal (timeout/exception) bila probe masih memegang
    try:
        assert ps.lock_state(lock) == ps.LOCK_HELD
    finally:
        holder.kill()
        holder.wait()


def test_missing_lock_is_not_created(tmp_path: Path) -> None:
    lock = tmp_path / "recorder.lock"
    assert ps.lock_state(lock) == ps.LOCK_ABSENT
    assert not lock.exists(), "status tidak boleh membuat berkas kunci"


# --- AC 3: pencocokan cmdline --------------------------------------------------------------
@pytest.mark.parametrize(
    "argv",
    [
        ["python", "-m", "core.recorder"],
        ["/home/x/tenko/.venv/bin/python3", "-m", "core.recorder"],
        ["uv", "run", "python", "-m", "core.recorder"],
        ["python3.12", "-mcore.recorder"],
        ["python3", "-m", "core.recorder", "--once", "--client", "fixture"],
        ["/usr/bin/python3", "/home/x/tenko/core/recorder.py", "--once"],
    ],
)
def test_looks_like_recorder_true(argv: list[str]) -> None:
    assert ps.looks_like_recorder(argv) is True


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["/bin/bash", "-c", "for p in /proc/*; do grep core.recorder $p/cmdline; done"],
        ["grep", "-rn", "core.recorder", "."],
        ["vim", "core/recorder.py"],
        ["nano", "/home/x/tenko/core/recorder.py"],
        ["python3", "scripts/proc_status.py", "--dir", "logs"],
        ["python3", "-m", "pytest", "tests/test_recorder.py"],
        ["python3", "-m", "core.recorder_helper"],
        ["python3", "-m", "core.skill_cli", "core.recorder"],
        ["tail", "-f", "logs/recorder.log"],
    ],
)
def test_looks_like_recorder_false(argv: list[str]) -> None:
    assert ps.looks_like_recorder(argv) is False


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["python3", "-m", "core.recorder"], "live"),
        (["python3", "-m", "core.recorder", "--client", "fixture"], "fixture"),
        (["python3", "-m", "core.recorder", "--client=fixture"], "fixture"),
        (["python3", "-m", "core.recorder", "--client", "live"], "live"),
    ],
)
def test_client_kind(argv: list[str], expected: str) -> None:
    assert ps.client_kind(argv) == expected


def test_scan_uses_fake_proc_and_skips_non_recorders(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    _fake_proc(proc_root, 111, ["uv", "run", "python", "-m", "core.recorder"])
    _fake_proc(proc_root, 222, [sys.executable, "-m", "core.recorder", "--client", "fixture"])
    _fake_proc(proc_root, 333, ["grep", "-rn", "core.recorder", "."])
    (proc_root / "self").mkdir()
    (proc_root / "meminfo").write_text("bukan proses", encoding="utf-8")

    found = ps.scan_recorders(proc_root=proc_root)
    assert [p.pid for p in found] == [111, 222]
    assert [p.client for p in found] == ["live", "fixture"]

    found_excluded = ps.scan_recorders(proc_root=proc_root, exclude={111})
    assert [p.pid for p in found_excluded] == [222]


# --- AC 3: yatim nyata, nol sinyal ----------------------------------------------------------
@pytest.mark.parametrize(
    ("extra", "expected_client"),
    [([], "live"), (["--client", "fixture"], "fixture")],
)
def test_orphan_recorder_named_and_left_alive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str], expected_client: str
) -> None:
    proc = _stub_recorder(tmp_path, extra)
    try:
        _wait_alive(proc)
        code = ps.main(["--dir", str(tmp_path / "logs"), "--name", "recorder"])
        out = capsys.readouterr().out
        assert code == 0
        assert f"YATIM pid {proc.pid}" in out
        assert expected_client in out
        assert proc.poll() is None, "status mengirim sinyal — proses uji mati"
    finally:
        proc.kill()
        proc.wait()


def test_recorder_in_pid_file_is_not_called_orphan(tmp_path: Path, capsys) -> None:
    proc = _stub_recorder(tmp_path, ["--client", "fixture"])
    try:
        _wait_alive(proc)
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "recorder.pid").write_text(f"{proc.pid}\n", encoding="utf-8")
        ps.main(["--dir", str(logs), "--name", "recorder"])
        out = capsys.readouterr().out
        assert "JALAN" in out
        # Hanya pid INI yang diperiksa: /proc nyata bisa memuat perekam produksi yang sah,
        # dan tes tidak boleh menuntut mesin uji bersih dari proses lain.
        assert f"YATIM pid {proc.pid}" not in out
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()


# --- [SEDANG] teks asal-luar tidak boleh menyembunyikan peringatan --------------------------
def test_safe_text_escapes_control_and_bidi() -> None:
    assert ps.safe_text("a\x1b[8mb", 40) == "a\\u001b[8mb"
    assert ps.safe_text("a\nb", 40) == "a\\u000ab"
    assert ps.safe_text("a\x00b", 40) == "a\\u0000b"
    assert ps.safe_text("a\u202eb", 40) == "a\\u202eb"
    assert ps.safe_text("biasa saja", 40) == "biasa saja"


def test_safe_text_truncates_visibly() -> None:
    out = ps.safe_text("x" * 100, 10)
    assert out.startswith("x" * 10) and out.endswith(ps.TRUNCATED_MARK)


def test_log_tail_cannot_conceal_the_lines_after_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ESC di log = seluruh keluaran sesudahnya tak terbaca; PERINGATAN ikut hilang."""
    proc_root = tmp_path / "proc"
    logs = tmp_path / "logs"
    logs.mkdir()
    _fake_proc(proc_root, 910, ["uv", "run", "python", "-m", "core.recorder"])
    (logs / "recorder.pid").write_text("910\n", encoding="utf-8")
    (logs / "recorder.log").write_text("siklus ok\x1b[8m\x1b[30;40m\n", encoding="utf-8")

    ps.main(["--dir", str(logs), "--name", "recorder", "--proc-root", str(proc_root)])
    out = capsys.readouterr().out

    assert "\x1b" not in out
    assert "\\u001b" in out


def test_orphan_cmdline_cannot_forge_extra_lines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    proc_root = tmp_path / "proc"
    logs = tmp_path / "logs"
    logs.mkdir()
    _fake_proc(
        proc_root,
        920,
        ["python3", "-m", "core.recorder", "--db", "x\n  snapshots: 999999 (palsu)"],
    )
    ps.main(["--dir", str(logs), "--name", "recorder", "--proc-root", str(proc_root)])
    out = capsys.readouterr().out
    assert "\n  snapshots: 999999" not in out
    assert "YATIM pid 920" in out


def test_pid_file_content_is_escaped_before_printing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "recorder.pid").write_text("\x1b[8m rahasia visual", encoding="utf-8")
    ps.main(["--dir", str(logs), "--name", "recorder"])
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert ps.STALE_PID in out


def test_db_status_escapes_timestamp_text(tmp_path: Path) -> None:
    db = tmp_path / "ryo.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, tool TEXT, ts_utc TEXT)")
    conn.execute("INSERT INTO snapshots (tool, ts_utc) VALUES ('get_price', ?)", ("2026\x1b[8m",))
    conn.commit()
    conn.close()
    done = _run_db_status(db)
    assert done.returncode == 0, done.stderr
    assert "\x1b" not in done.stdout
    assert "tidak terbaca" in done.stdout


# --- [SEDANG] timestamp masa depan tidak boleh membungkam peringatan ------------------------
def test_db_status_warns_on_future_timestamp(tmp_path: Path) -> None:
    db = tmp_path / "ryo.db"
    _db_with_age(db, age=-timedelta(days=3))
    done = _run_db_status(db)
    assert done.returncode == 0, done.stderr
    assert "TIMESTAMP DI MASA DEPAN" in done.stdout
    assert "TIDAK ADA SNAPSHOT BARU" not in done.stdout, "ambang basi tidak bisa dinilai di sini"


def test_db_status_shares_one_sanitizer_with_proc_status() -> None:
    """Satu definisi, bukan salinan kedua (pelajaran TASKS 1.5b)."""
    text = DB_STATUS.read_text(encoding="utf-8")
    assert "from proc_status import safe_text" in text
    assert "def safe_text" not in text


# --- batas yang diakui: pid file menyimpan pembungkus `uv run` -------------------------------
def test_low_findings_are_written_down() -> None:
    """Lima batas RENDAH yang sengaja DIBIARKAN harus tercatat, terutama FIFO yang menggantung."""
    text = SCRIPT.read_text(encoding="utf-8")
    for fragment in ("FIFO", "MENGGANTUNG", "YATIM hantu", "splitlines", "BLOB"):
        assert fragment in text, fragment


def test_wrapper_limit_is_written_down() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "uv run" in text
    assert "pembungkus" in text
    assert "Batas yang diakui" in text


def test_wrapper_pid_gets_an_explicit_caveat_line(tmp_path: Path, capsys) -> None:
    proc_root = tmp_path / "proc"
    logs = tmp_path / "logs"
    logs.mkdir()
    pid = 4863
    _fake_proc(proc_root, pid, ["uv", "run", "python", "-m", "core.recorder"])
    _fake_proc(proc_root, 4867, ["/x/.venv/bin/python3", "-m", "core.recorder"])
    (logs / "recorder.pid").write_text(f"{pid}\n", encoding="utf-8")

    ps.main(["--dir", str(logs), "--name", "recorder", "--proc-root", str(proc_root)])
    out = capsys.readouterr().out

    assert f"JALAN (pid {pid})" in out
    assert "YATIM pid 4867" in out
    assert "pembungkus" in out, "batas pembungkus harus terlihat di output, bukan hanya di docstring"


# --- nol sinyal: dibaca dari sumber ---------------------------------------------------------
def test_proc_status_never_signals() -> None:
    """PENYARING KASAR, bukan jaminan.

    Tes ini membaca sumber dan menolak `kill`/`killpg` serta impor `signal`/`subprocess`. Ia
    TIDAK menutup `ctypes.CDLL(None).syscall(62, pid, 15)`, `getattr(os, "ki" + "ll")`,
    `__import__("signal")`, atau `os.system` — semuanya lolos. Jaminan yang sebenarnya ada
    pada dua tes PERILAKU (`test_orphan_recorder_named_and_left_alive` dan
    `test_recycled_pid_is_reported_stale_not_running`): proses uji nyata harus MASIH HIDUP
    setelah status berjalan. Tes lemah yang jujur lebih baik daripada klaim yang lebih kuat
    dari kenyataannya.
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    assert "kill" not in names
    assert "killpg" not in names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert {a.name.split(".")[0] for a in node.names}.isdisjoint({"signal", "subprocess"})
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in {"signal", "subprocess"}


def test_proc_status_imports_stdlib_only() -> None:
    """Alat diagnostik harus jalan dengan python polos — tanpa uv/venv, tanpa `core.*`."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= set(sys.stdlib_module_names), roots


def test_proc_status_runs_with_bare_python() -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        cwd=str(Path(SCRIPT).parent),
        env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
    )
    assert done.returncode == 0, done.stderr


# --- AC 2: umur snapshot & ambang (scripts/db_status.py) -------------------------------------
def _db_with_age(path: Path, *, age: timedelta | None, rows: int = 3) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, tool TEXT, ts_utc TEXT)")
        if age is not None:
            newest = datetime.now(UTC) - age
            for i in range(rows):
                ts = (newest - timedelta(minutes=i)).isoformat()
                conn.execute("INSERT INTO snapshots (tool, ts_utc) VALUES (?, ?)", ("get_price", ts))
        conn.commit()
    finally:
        conn.close()


def _run_db_status(db: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DB_PATH"] = str(db)
    env.pop("RECORDER_INTERVAL_S", None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(DB_STATUS)], capture_output=True, text=True, env=env)


def test_db_status_reports_age_without_warning_when_fresh(tmp_path: Path) -> None:
    db = tmp_path / "ryo.db"
    _db_with_age(db, age=timedelta(minutes=12))
    done = _run_db_status(db)
    assert done.returncode == 0, done.stderr
    assert "umur" in done.stdout
    assert "TIDAK ADA SNAPSHOT BARU" not in done.stdout


def test_db_status_warns_when_older_than_two_intervals(tmp_path: Path) -> None:
    db = tmp_path / "ryo.db"
    _db_with_age(db, age=timedelta(hours=5))
    done = _run_db_status(db)
    assert done.returncode == 0, done.stderr
    assert "TIDAK ADA SNAPSHOT BARU > 2× interval" in done.stdout


def test_db_status_threshold_follows_recorder_interval_env(tmp_path: Path) -> None:
    db = tmp_path / "ryo.db"
    _db_with_age(db, age=timedelta(hours=5))
    quiet = _run_db_status(db, {"RECORDER_INTERVAL_S": "10800"})  # 2× = 6 j → belum lewat
    loud = _run_db_status(db, {"RECORDER_INTERVAL_S": "1800"})  # 2× = 1 j → lewat
    assert "TIDAK ADA SNAPSHOT BARU" not in quiet.stdout
    assert "TIDAK ADA SNAPSHOT BARU > 2× interval" in loud.stdout
    assert (quiet.returncode, loud.returncode) == (0, 0)


def test_db_status_bad_interval_env_falls_back_and_says_so(tmp_path: Path) -> None:
    db = tmp_path / "ryo.db"
    _db_with_age(db, age=timedelta(minutes=5))
    done = _run_db_status(db, {"RECORDER_INTERVAL_S": "bukan angka"})
    assert done.returncode == 0, done.stderr
    assert "RECORDER_INTERVAL_S" in done.stdout


def test_db_status_empty_table_has_no_age_and_no_fake_zero(tmp_path: Path) -> None:
    db = tmp_path / "ryo.db"
    _db_with_age(db, age=None)
    done = _run_db_status(db)
    assert done.returncode == 0, done.stderr
    assert "snapshots: 0" in done.stdout
    assert "umur 0" not in done.stdout


def test_db_status_unparsable_timestamp_is_not_treated_as_fresh(tmp_path: Path) -> None:
    db = tmp_path / "ryo.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, tool TEXT, ts_utc TEXT)")
    conn.execute("INSERT INTO snapshots (tool, ts_utc) VALUES ('get_price', 'kemarin sore')")
    conn.commit()
    conn.close()
    done = _run_db_status(db)
    assert done.returncode == 0, done.stderr
    assert "tidak terbaca" in done.stdout


def test_db_status_writes_nothing_to_the_database(tmp_path: Path) -> None:
    """DB + direktorinya hanya-baca: SQLite yang membuka mode tulis akan gagal di sini."""
    db = tmp_path / "ryo.db"
    _db_with_age(db, age=timedelta(minutes=3))
    before = db.stat().st_mtime_ns
    db.chmod(stat.S_IRUSR)
    tmp_path.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        done = _run_db_status(db)
    finally:
        tmp_path.chmod(stat.S_IRWXU)
        db.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert done.returncode == 0, done.stderr + done.stdout
    assert "snapshots: 3" in done.stdout
    assert db.stat().st_mtime_ns == before
    assert not (tmp_path / "ryo.db-journal").exists()
    assert not (tmp_path / "ryo.db-wal").exists()


def test_db_status_opens_read_only_uri() -> None:
    text = DB_STATUS.read_text(encoding="utf-8")
    assert "mode=ro" in text and "uri=True" in text


def test_db_status_imports_stdlib_only() -> None:
    """Stdlib + satu modul saudara di `scripts/` — tidak pernah `core.*` atau paket pihak ketiga.

    `proc_status` diizinkan karena ia berada di direktori yang sama dan juga stdlib-saja;
    itu harga untuk memiliki SATU sanitizer, bukan dua yang saling melenceng.
    """
    tree = ast.parse(DB_STATUS.read_text(encoding="utf-8"))
    allowed = set(sys.stdlib_module_names) | {"proc_status"}
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= allowed, roots


# --- integrasi: scripts/daemons.sh status di sandbox -----------------------------------------
def _sandbox(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    for name in ("daemons.sh", "proc_status.py", "db_status.py", "backup.py"):
        shutil.copy2(ROOT / "scripts" / name, root / "scripts" / name)
    (root / "logs").mkdir()
    (root / "data").mkdir()
    return root


def _run_daemons_status(root: Path, env_extra: dict[str, str] | None = None):
    env = dict(os.environ)
    env["DB_PATH"] = str(root / "data" / "ryo.db")
    env.pop("RECORDER_INTERVAL_S", None)
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(root / "scripts" / "daemons.sh"), "status"],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
    )


def test_daemons_status_exit_zero_on_empty_sandbox(tmp_path: Path) -> None:
    done = _run_daemons_status(_sandbox(tmp_path))
    assert done.returncode == 0, done.stderr
    assert "recorder:" in done.stdout and "agent:" in done.stdout


def test_daemons_status_reports_and_removes_stale_pid_file(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)
    pid_file = root / "logs" / "recorder.pid"
    pid_file.write_text(f"{_dead_pid()}\n", encoding="utf-8")

    done = _run_daemons_status(root)

    assert done.returncode == 0, done.stderr
    assert "MATI (pid file basi)" in done.stdout
    assert not pid_file.exists()


def test_daemons_status_reports_recycled_pid_without_touching_the_file(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        pid_file = root / "logs" / "recorder.pid"
        pid_file.write_text(f"{other.pid}\n", encoding="utf-8")
        done = _run_daemons_status(root)
        assert done.returncode == 0, done.stderr
        assert "TIDAK DIKENALI (pid hidup, bukan daemon ini)" in done.stdout
        assert "BUKAN core.recorder" in done.stdout
        assert not any(line.startswith("recorder: JALAN") for line in done.stdout.splitlines())
        assert pid_file.exists(), "pid hidup: `daemons.sh stop` harus tetap punya berkasnya"
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()


def test_daemons_status_keeps_lock_file_and_inode(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)
    lock = root / "logs" / "recorder.lock"
    lock.write_text("1\n", encoding="utf-8")
    before = lock.stat().st_ino
    holder = _lock_holder(lock)
    try:
        done = _run_daemons_status(root, {"RECORDER_LOCK_PATH": str(lock)})
    finally:
        holder.kill()
        holder.wait()

    assert done.returncode == 0, done.stderr
    assert "dipegang" in done.stdout
    assert lock.exists()
    assert lock.stat().st_ino == before


def test_daemons_status_warns_on_stale_snapshots(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)
    _db_with_age(root / "data" / "ryo.db", age=timedelta(hours=6))
    done = _run_daemons_status(root)
    assert done.returncode == 0, done.stderr
    assert "TIDAK ADA SNAPSHOT BARU > 2× interval" in done.stdout, done.stdout


def _status_block() -> str:
    """Blok `status)` tanpa baris komentar — yang dinilai perilaku, bukan prosa."""
    text = DAEMONS.read_text(encoding="utf-8")
    block = text.split("  status)", 1)[1].split(";;", 1)[0]
    return "\n".join(line for line in block.splitlines() if not line.strip().startswith("#"))


def test_daemons_status_block_sends_no_signal_and_never_removes_lock() -> None:
    block = _status_block()
    for forbidden in ("kill", "pkill", "killall"):
        assert forbidden not in block, f"blok status memuat {forbidden}"
    assert "rm " not in block, "blok status tidak boleh menghapus berkas sendiri"
    assert "proc_status.py" in block, "pembersihan pid basi hidup di satu tempat yang punya tes"


def test_daemons_status_does_not_swallow_new_errors() -> None:
    """`2>/dev/null` pada db_status menelan kesalahan baru; ia harus sudah dibuang."""
    assert "2>/dev/null" not in _status_block()
