#!/usr/bin/env python3
"""Salinan online `data/ryo.db` → `data/backups/ryo-<UTC>.db` (TASKS 1.12).

Kenapa ada: snapshot pasar ber-timestamp di `data/ryo.db` adalah satu-satunya aset proyek
ini yang TIDAK bisa dibuat ulang — RYO tidak menyediakan histori, jadi jam yang hilang
hilang selamanya. Temuan (j) sudah membuktikan tulisan mentah lewat `Store._db`
(`REPLACE INTO`) bisa menghancurkan baris tanpa jejak, dan TASKS 4.5 memang akan menghapus
berkas DB dengan sengaja. Perintah ini dijalankan sebelum operasi berisiko dan di setiap
`scripts/daemons.sh start`.

Tiga keputusan teknisnya, semuanya punya tes di `tests/test_backup.py`:

* **`sqlite3.Connection.backup()`, bukan `cp`/`shutil.copy`.** Perekam sedang menulis ke DB
  itu (siklus per jam); menyalin berkas SQLite yang hidup di level filesystem bisa
  menghasilkan salinan robek — halaman setengah tertulis, journal tidak ikut — yang baru
  ketahuan saat salinan itu dibutuhkan. Online backup API stdlib memang dirancang untuk
  berjalan bersamaan dengan penulis lain. `pages=-1` (satu langkah) dipilih supaya salinan
  konsisten pada satu titik waktu dan backup tidak pernah di-restart oleh penulis di
  tengah jalan; DB ini kecil (satuan MB), jadi kunci baca yang ditahan hanya milidetik.
* **Sumber dibuka baca-saja** (`file:...?mode=ro`, `uri=True`). Tidak ada satu pun jalur di
  berkas ini yang membuka sumber dengan izin tulis.
* **Salinan diverifikasi, bukan diasumsikan.** `PRAGMA integrity_check` dijalankan pada
  salinan; kalau hasilnya bukan `ok`, berkas diberi akhiran `.corrupt` dan exit code ≠ 0.
  Salinan rusak yang menyamar sebagai salinan sehat lebih berbahaya daripada tidak punya
  salinan sama sekali, karena ia menghentikan orang dari membuat salinan lain.

Stdlib saja, tanpa impor paket aplikasi (`core/`, `agent/`, ...): alat penyelamat harus
tetap jalan justru ketika aplikasinya rusak atau venv-nya belum ada. Karena itu ia hidup di
`scripts/` — ini perkakas operasional, bukan bagian kontrak skill atau jalur keputusan.

Pakai:
    python3 scripts/backup.py                          # DB_PATH → ./data/backups/
    python3 scripts/backup.py --source X --dest DIR
Exit: 0 salinan tepercaya · 1 salinan gagal verifikasi · 2 sumber/argumen bermasalah.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ENV_DB_PATH = "DB_PATH"
ENV_BACKUP_DIR = "BACKUP_DIR"
DEFAULT_DB_PATH = "./data/ryo.db"
DEFAULT_BACKUP_DIR = "./data/backups"

NAME_PREFIX = "ryo-"
NAME_SUFFIX = ".db"
CORRUPT_SUFFIX = ".corrupt"
# Cap waktu UTC sampai detik, bukan tanggal saja: menjalankan backup dua kali sehari
# (mis. sebelum tes destruktif) tidak boleh menimpa salinan pagi. Formatnya juga terurut
# secara leksikografis (kecuali salinan `-N` pada detik yang sama), jadi `ls` kurang lebih
# urutan waktu. UTC karena seluruh proyek menyimpan UTC.
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

COPY_ATTEMPTS = 3  # perekam bisa memegang kunci tulis sesaat; coba lagi, jangan menyerah
COPY_RETRY_SLEEP_S = 2.0
BUSY_TIMEOUT_S = 30.0


class BackupError(Exception):
    """Kegagalan backup yang sudah dikenali; pesannya untuk dibaca operator."""


@dataclass(frozen=True)
class BackupReport:
    """Hasil satu backup. `trusted=False` berarti salinan TIDAK boleh dipakai."""

    source: Path
    target: Path
    integrity: str
    source_rows: int | None
    copy_rows: int | None
    bytes_written: int
    trusted: bool


# --- path & nama -------------------------------------------------------------------------
def resolve_source(source: str | Path | None = None) -> Path:
    """Path sumber; bentuk URI ditolak seperti di `core.store` (bukan diterima diam-diam)."""
    raw = source if source is not None else os.environ.get(ENV_DB_PATH, DEFAULT_DB_PATH)
    text = str(raw).strip()
    if not text:
        raise BackupError(f"{ENV_DB_PATH} kosong")
    lowered = text.lower()
    if lowered.startswith("file:") or "?mode=" in lowered or "?cache=" in lowered:
        raise BackupError(f"path DB berbentuk URI tidak didukung: {text!r} — beri path berkas biasa")
    if ":memory:" in lowered:
        raise BackupError("DB in-memory tidak punya apa pun untuk disalin")
    return Path(text)


def resolve_dest_dir(dest: str | Path | None = None) -> Path:
    raw = dest if dest is not None else os.environ.get(ENV_BACKUP_DIR, DEFAULT_BACKUP_DIR)
    text = str(raw).strip()
    if not text:
        raise BackupError(f"{ENV_BACKUP_DIR} kosong")
    return Path(text)


def stamp(now: datetime) -> str:
    if now.tzinfo is None:
        raise BackupError("cap waktu backup harus punya zona waktu (UTC); yang naive tak bisa ditafsir")
    return now.astimezone(UTC).strftime(STAMP_FORMAT)


def target_path(dest_dir: str | Path, now: datetime, name: str | None = None) -> Path:
    """Berkas tujuan yang BELUM ada. Salinan lama tidak pernah ditimpa, hanya diberi urutan."""
    directory = Path(dest_dir)
    if name is not None:
        return directory / name
    base = f"{NAME_PREFIX}{stamp(now)}"
    candidate = directory / f"{base}{NAME_SUFFIX}"
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = directory / f"{base}-{counter}{NAME_SUFFIX}"
        if counter > 999:  # pagar paranoid, bukan ambang bisnis
            raise BackupError(f"terlalu banyak salinan dengan cap waktu {base}")
    return candidate


def source_uri(path: str | Path) -> str:
    """URI baca-saja. `as_uri()` mengutip spasi/`?` supaya path aneh tidak terpotong."""
    return Path(path).resolve().as_uri() + "?mode=ro"


# --- SQLite ------------------------------------------------------------------------------
def _connect_read_only(path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(source_uri(path), uri=True, timeout=BUSY_TIMEOUT_S)


def count_snapshots(path: str | Path) -> int | None:
    """Jumlah baris `snapshots`, atau None bila tabelnya tidak ada — bukan 0.

    0 adalah angka; "tidak ada tabelnya" adalah keadaan lain. Menyamakan keduanya persis
    kesalahan yang dilarang di seluruh proyek ini (null tidak pernah menjadi 0).
    """
    conn = _connect_read_only(path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type=? AND name=?", ("table", "snapshots")
        ).fetchone()
        if row is None:
            return None
        return int(conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
    except sqlite3.Error as exc:
        raise BackupError(f"gagal menghitung baris snapshots di {path}: {exc}") from exc
    finally:
        conn.close()


def integrity_check(path: str | Path) -> str:
    """Hasil `PRAGMA integrity_check` sebagai teks; kegagalan membaca pun jadi teks, bukan crash."""
    try:
        conn = _connect_read_only(path)
    except (sqlite3.Error, ValueError, OSError) as exc:
        return f"tidak bisa dibuka: {exc}"
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as exc:
        return f"tidak bisa diperiksa: {exc}"
    finally:
        conn.close()
    return "; ".join(str(r[0]) for r in rows) if rows else "kosong"


def copy_database(source: Path, target: Path) -> None:
    """Online backup: sumber baca-saja, satu langkah (`pages=-1`) agar salinan konsisten."""
    src = _connect_read_only(source)
    try:
        dst = sqlite3.connect(str(target), timeout=BUSY_TIMEOUT_S)
        try:
            src.backup(dst, pages=-1)
        finally:
            dst.close()
    finally:
        src.close()


# --- orkestrasi --------------------------------------------------------------------------
def run(
    *,
    source: str | Path | None = None,
    dest_dir: str | Path | None = None,
    now: datetime | None = None,
    name: str | None = None,
) -> BackupReport:
    src = resolve_source(source)
    if not src.is_file():
        raise BackupError(f"sumber tidak ada: {src}")
    directory = resolve_dest_dir(dest_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = target_path(directory, now or datetime.now(UTC), name=name)
    if target.resolve() == src.resolve():
        raise BackupError(f"tujuan sama dengan sumber ({src}) — itu akan menghapus aset yang dijaga")

    last: sqlite3.Error | None = None
    for attempt in range(1, COPY_ATTEMPTS + 1):
        try:
            copy_database(src, target)
            break
        except sqlite3.Error as exc:  # perekam memegang kunci tulis → coba lagi, jangan gagal
            last = exc
            target.unlink(missing_ok=True)
            if attempt < COPY_ATTEMPTS:
                time.sleep(COPY_RETRY_SLEEP_S)
    else:
        raise BackupError(f"gagal menyalin {src} setelah {COPY_ATTEMPTS} percobaan: {last}")

    integrity = integrity_check(target)
    copy_rows = count_snapshots(target) if integrity == "ok" else None
    source_rows = count_snapshots(src)
    size = target.stat().st_size
    if integrity != "ok":
        broken = target.with_name(target.name + CORRUPT_SUFFIX)
        target.rename(broken)
        target = broken
    return BackupReport(
        source=src,
        target=target,
        integrity=integrity,
        source_rows=source_rows,
        copy_rows=copy_rows,
        bytes_written=size,
        trusted=integrity == "ok",
    )


def _format(report: BackupReport) -> list[str]:
    lines = [
        f"backup: sumber  {report.source} ({report.source.stat().st_size} B, dibuka mode=ro)",
        f"backup: salinan {report.target} ({report.bytes_written} B)",
        f"backup: integrity_check: {report.integrity}",
    ]
    if report.source_rows is None:
        lines.append("backup: snapshots: tabel tidak ada di sumber (tidak diasumsikan 0)")
    else:
        copy_rows = (
            "tidak dihitung (salinan gagal verifikasi)" if report.copy_rows is None else report.copy_rows
        )
        lines.append(f"backup: snapshots sumber={report.source_rows} salinan={copy_rows}")
        if report.copy_rows is not None and report.copy_rows != report.source_rows:
            delta = report.source_rows - report.copy_rows
            lines.append(
                f"backup: beda {delta} baris — normal: perekam menulis saat backup berjalan; "
                "salinan adalah snapshot pada satu titik waktu"
            )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Salinan online data/ryo.db yang diverifikasi.")
    parser.add_argument("--source", default=None, help=f"path DB sumber (default: env {ENV_DB_PATH})")
    parser.add_argument("--dest", default=None, help=f"direktori tujuan (default: env {ENV_BACKUP_DIR})")
    args = parser.parse_args(argv)

    try:
        report = run(source=args.source, dest_dir=args.dest)
    except BackupError as exc:
        print(f"backup GAGAL: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"backup GAGAL: {exc}", file=sys.stderr)
        return 2

    for line in _format(report):
        print(line)
    if not report.trusted:
        print(
            f"backup GAGAL: salinan {report.target} tidak bisa dipercaya "
            f"(integrity_check: {report.integrity}); jangan pakai berkas ini, ulangi backup",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
