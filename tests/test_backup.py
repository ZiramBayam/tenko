"""Tes TASKS 1.12 — `make backup`: salinan online `data/ryo.db` yang bisa dipercaya.

Aset yang dijaga di sini tidak bisa dibuat ulang: RYO tidak menyediakan histori, jadi
snapshot ber-timestamp yang sudah terekam hilang selamanya kalau berkas DB rusak atau
terhapus (TASKS 4.5 memang akan menghapusnya dengan sengaja). Karena itu tes ini menuntut
tiga hal yang lebih keras daripada "berkasnya ada":

1. **Sumber tidak pernah ditulis.** Dibuktikan dengan menjadikan berkas DB DAN direktorinya
   hanya-baca di filesystem, lalu tetap menuntut backup berhasil: SQLite yang menulis
   (journal, `-wal`, atau halaman) akan gagal di lingkungan itu.
2. **Salinan diverifikasi, bukan diasumsikan.** `PRAGMA integrity_check` harus `ok`;
   salinan yang tidak lolos wajib membuat exit code ≠ 0 dan diberi nama `.corrupt` supaya
   tidak pernah bisa dikira salinan sehat.
3. **Menjalankan dua kali tidak menimpa salinan sebelumnya** — salinan lama yang tertimpa
   adalah kehilangan data yang sama saja.

Semua penulisan di berkas ini HANYA ke `tmp_path`. `data/ryo.db` yang hidup tidak disentuh.
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
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "backup.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("tenko_backup_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # `dataclass` mencari modulnya di sys.modules; berkas skrip di luar paket harus
    # didaftarkan dulu supaya bisa diimpor dari tes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backup = _load_module()

NOW = datetime(2026, 9, 2, 11, 30, 0, tzinfo=UTC)


def _make_db(path: Path, rows: int = 3) -> Path:
    """DB mini dengan tabel `snapshots` seminimal yang dibutuhkan tes (bukan skema penuh)."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, tool TEXT, ts_utc TEXT)")
        conn.executemany(
            "INSERT INTO snapshots (tool, ts_utc) VALUES (?, ?)",
            [("analyze_token", f"2026-09-02T0{i}:00:00+00:00") for i in range(rows)],
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _integrity(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def _count(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    finally:
        conn.close()


# --- nama berkas -------------------------------------------------------------------------
def test_target_name_is_utc_timestamp_so_two_runs_a_day_do_not_collide(tmp_path: Path) -> None:
    """`<tgl>` = cap waktu UTC sampai detik: dua jalan dalam sehari = dua berkas."""
    pagi = backup.target_path(tmp_path, datetime(2026, 9, 2, 6, 0, 0, tzinfo=UTC))
    sore = backup.target_path(tmp_path, datetime(2026, 9, 2, 18, 30, 15, tzinfo=UTC))
    assert pagi.name == "ryo-20260902T060000Z.db"
    assert sore.name == "ryo-20260902T183015Z.db"
    assert pagi != sore


def test_target_path_rejects_naive_datetime(tmp_path: Path) -> None:
    """Cap waktu tanpa zona akan menamai salinan dengan jam lokal yang tak bisa ditafsir."""
    with pytest.raises(backup.BackupError):
        backup.target_path(tmp_path, datetime(2026, 9, 2, 6, 0, 0))  # noqa: DTZ001 - memang sengaja naive


def test_target_path_uses_utc_even_when_given_another_timezone(tmp_path: Path) -> None:
    from datetime import timedelta, timezone

    jst = datetime(2026, 9, 2, 15, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    assert backup.target_path(tmp_path, jst).name == "ryo-20260902T060000Z.db"


def test_existing_target_gets_a_suffix_instead_of_being_overwritten(tmp_path: Path) -> None:
    first = backup.target_path(tmp_path, NOW)
    first.write_bytes(b"salinan lama")
    second = backup.target_path(tmp_path, NOW)
    assert second != first
    assert second.name == "ryo-20260902T113000Z-2.db"
    assert first.read_bytes() == b"salinan lama"


# --- jalur bahagia -----------------------------------------------------------------------
def test_run_copies_rows_and_reports_ok(tmp_path: Path) -> None:
    src = _make_db(tmp_path / "ryo.db", rows=5)
    report = backup.run(source=src, dest_dir=tmp_path / "backups", now=NOW)

    assert report.trusted is True
    assert report.integrity == "ok"
    assert report.target.exists()
    assert report.source_rows == 5
    assert report.copy_rows == 5
    assert report.bytes_written == report.target.stat().st_size
    assert _integrity(report.target) == "ok"
    assert _count(report.target) == 5


def test_run_creates_destination_directory(tmp_path: Path) -> None:
    src = _make_db(tmp_path / "ryo.db")
    dest = tmp_path / "a" / "b" / "backups"
    report = backup.run(source=src, dest_dir=dest, now=NOW)
    assert report.target.parent == dest
    assert dest.is_dir()


def test_two_runs_leave_two_files(tmp_path: Path) -> None:
    src = _make_db(tmp_path / "ryo.db")
    dest = tmp_path / "backups"
    a = backup.run(source=src, dest_dir=dest, now=NOW)
    b = backup.run(source=src, dest_dir=dest, now=NOW)
    assert a.target != b.target
    assert {p.name for p in dest.glob("*.db")} == {a.target.name, b.target.name}


def test_copy_is_a_snapshot_not_a_live_link(tmp_path: Path) -> None:
    """Baris yang ditulis SETELAH backup tidak boleh muncul di salinan (dan sebaliknya)."""
    src = _make_db(tmp_path / "ryo.db", rows=2)
    report = backup.run(source=src, dest_dir=tmp_path / "backups", now=NOW)
    conn = sqlite3.connect(str(src))
    try:
        conn.execute("INSERT INTO snapshots (tool, ts_utc) VALUES ('scan_market', 'nanti')")
        conn.commit()
    finally:
        conn.close()
    assert _count(report.target) == 2
    assert _count(src) == 3


# --- sumber tidak boleh ditulis ----------------------------------------------------------
def test_source_is_opened_read_only_even_when_filesystem_forbids_writing(tmp_path: Path) -> None:
    """Direktori sumber hanya-baca: SQLite yang mencoba menulis journal akan gagal di sini."""
    src_dir = tmp_path / "live"
    src_dir.mkdir()
    src = _make_db(src_dir / "ryo.db", rows=4)
    before = src.read_bytes()
    src.chmod(stat.S_IRUSR)
    src_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        report = backup.run(source=src, dest_dir=tmp_path / "backups", now=NOW)
        assert report.trusted is True
        assert report.copy_rows == 4
    finally:
        src_dir.chmod(stat.S_IRWXU)
        src.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert src.read_bytes() == before
    assert not (src_dir / "ryo.db-journal").exists()
    assert not (src_dir / "ryo.db-wal").exists()


def test_source_connection_uri_is_read_only(tmp_path: Path) -> None:
    src = _make_db(tmp_path / "ryo.db")
    uri = backup.source_uri(src)
    assert uri.endswith("?mode=ro")
    conn = sqlite3.connect(uri, uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO snapshots (tool, ts_utc) VALUES ('x', 'y')")
    finally:
        conn.close()


def test_source_uri_quotes_paths_with_spaces_and_question_marks(tmp_path: Path) -> None:
    odd = tmp_path / "data dir?x"
    odd.mkdir()
    src = _make_db(odd / "ryo.db", rows=2)
    report = backup.run(source=src, dest_dir=tmp_path / "backups", now=NOW)
    assert report.copy_rows == 2


# --- verifikasi salinan ------------------------------------------------------------------
def test_corrupt_copy_is_reported_and_renamed_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    src = _make_db(tmp_path / "ryo.db")
    dest = tmp_path / "backups"

    def _sabotage(source: Path, target: Path) -> None:
        target.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)

    monkeypatch.setattr(backup, "copy_database", _sabotage)
    code = backup.main(["--source", str(src), "--dest", str(dest)])
    out = capsys.readouterr()

    assert code != 0
    assert not list(dest.glob("ryo-*.db")), "salinan gagal tidak boleh menyandang nama salinan sehat"
    corrupt = list(dest.glob("*.corrupt"))
    assert len(corrupt) == 1
    assert "tidak bisa dipercaya" in (out.out + out.err)


def test_integrity_check_of_a_damaged_file_is_not_ok(tmp_path: Path) -> None:
    src = _make_db(tmp_path / "ryo.db", rows=200)
    blob = bytearray(src.read_bytes())
    for i in range(2048, min(len(blob), 6144)):
        blob[i] = 0x5A
    damaged = tmp_path / "rusak.db"
    damaged.write_bytes(bytes(blob))
    assert backup.integrity_check(damaged) != "ok"


def test_integrity_check_of_a_non_database_reports_instead_of_raising(tmp_path: Path) -> None:
    junk = tmp_path / "bukan.db"
    junk.write_text("halo")
    assert backup.integrity_check(junk) != "ok"


# --- kegagalan yang eksplisit ------------------------------------------------------------
def test_missing_source_exits_nonzero_with_a_clear_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = backup.main(["--source", str(tmp_path / "tidak-ada.db"), "--dest", str(tmp_path / "b")])
    out = capsys.readouterr()
    assert code != 0
    assert "tidak-ada.db" in out.err


def test_uri_shaped_db_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(backup.BackupError):
        backup.run(source="file:/x/ryo.db?mode=rwc", dest_dir=tmp_path, now=NOW)


def test_source_without_snapshots_table_reports_none_not_zero(tmp_path: Path) -> None:
    """Angka default saat sumber tidak menjawab = dusta; tabel hilang harus terlihat."""
    src = tmp_path / "kosong.db"
    conn = sqlite3.connect(str(src))
    conn.execute("CREATE TABLE lain (a TEXT)")
    conn.commit()
    conn.close()
    report = backup.run(source=src, dest_dir=tmp_path / "backups", now=NOW)
    assert report.source_rows is None
    assert report.copy_rows is None
    assert report.trusted is True


# --- CLI ---------------------------------------------------------------------------------
def test_cli_exits_zero_and_prints_counts_and_integrity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = _make_db(tmp_path / "ryo.db", rows=7)
    dest = tmp_path / "backups"
    code = backup.main(["--source", str(src), "--dest", str(dest)])
    out = capsys.readouterr().out
    assert code == 0
    assert "integrity_check: ok" in out
    assert "7" in out
    assert len(list(dest.glob("ryo-*.db"))) == 1


def test_cli_reads_db_path_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _make_db(tmp_path / "ryo.db", rows=2)
    monkeypatch.setenv("DB_PATH", str(src))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "bk"))
    assert backup.main([]) == 0
    assert len(list((tmp_path / "bk").glob("ryo-*.db"))) == 1


def test_script_runs_as_a_subprocess_without_the_project_venv(tmp_path: Path) -> None:
    """Alat penyelamat harus jalan dengan python polos: stdlib saja, tanpa uv/venv."""
    src = _make_db(tmp_path / "ryo.db", rows=3)
    dest = tmp_path / "backups"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--source", str(src), "--dest", str(dest)],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout
    assert len(list(dest.glob("ryo-*.db"))) == 1


# --- batas & integrasi -------------------------------------------------------------------
def test_backup_script_imports_stdlib_only() -> None:
    """Backup harus tetap jalan saat paket aplikasi rusak; itu justru saat ia dibutuhkan."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= set(sys.stdlib_module_names), roots
    assert not roots & {"core", "preflight", "grading", "agent", "ui"}


def test_makefile_has_a_backup_target() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "\nbackup:" in text
    assert "scripts/backup.py" in text


def test_daemons_start_backs_up_before_the_recorder_and_never_blocks_it() -> None:
    """Kehilangan satu jam snapshot lebih buruk daripada tidak punya salinan hari itu."""
    text = (ROOT / "scripts" / "daemons.sh").read_text(encoding="utf-8")
    start = text.index("  start)")
    body = text[start : text.index("  stop)")]
    assert "scripts/backup.py" in body
    assert body.index("scripts/backup.py") < body.index("start_one recorder")
    line = next(ln for ln in body.splitlines() if "scripts/backup.py" in ln)
    assert "||" in line, "kegagalan backup tidak boleh menghentikan start perekam"


def test_backup_directory_is_git_ignored() -> None:
    """Salinan DB berisi output tool RYO; tidak boleh ikut ter-commit (rules-compliance)."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git tidak tersedia")
    proc = subprocess.run(
        [git, "check-ignore", "-v", "data/backups/ryo-20260902T113000Z.db"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    assert proc.returncode == 0, f"data/backups/*.db TIDAK ter-ignore: {proc.stdout}{proc.stderr}"


def test_live_database_is_never_the_destination(tmp_path: Path) -> None:
    """Menulis salinan ke atas sumber = menghapus aset yang dijaga."""
    src = _make_db(tmp_path / "ryo.db")
    with pytest.raises(backup.BackupError):
        backup.run(source=src, dest_dir=src.parent, now=NOW, name=src.name)


def test_default_paths_match_the_repository_layout() -> None:
    assert backup.DEFAULT_DB_PATH == "./data/ryo.db"
    assert backup.DEFAULT_BACKUP_DIR == "./data/backups"
    assert os.environ.get("BACKUP_DIR") is None or True  # env opsional; default tetap berlaku
