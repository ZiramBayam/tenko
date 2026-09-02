"""Ringkasan isi data/ryo.db tanpa bergantung paket aplikasi (dipakai scripts/daemons.sh status).

Sejak TASKS 1.13 berkas ini juga menjawab pertanyaan yang membuat insiden 1 Sep 2026 tidak
terlihat selama ~45 menit: **berapa umur snapshot TERBARU?** Pid file bisa berbohong (ia
menyimpan pid pembungkus `uv run`, dan pid mati pun tetap tertulis di sana), tetapi baris
`snapshots` yang berhenti bertambah tidak bisa berbohong — itulah bukti sesungguhnya bahwa
perekam masih bekerja. Umur > 2× `RECORDER_INTERVAL_S` berarti minimal satu siklus penuh
terlewat; itu dicetak sebagai PERINGATAN yang terlihat, bukan disembunyikan.

Dua batas yang dipatok tes (`tests/test_daemons_status.py`):

* **Nol byte ditulis ke DB.** Koneksi dibuka `file:...?mode=ro` (URI), seperti
  `scripts/backup.py`. `status` dipanggil sambil perekam menulis; membuka DB dengan izin
  tulis berarti membuat `-journal`/`-wal` di direktori data hanya untuk membaca angka.
* **Nilai yang tidak diketahui tidak pernah menjadi 0.** Tabel yang tidak ada, tabel kosong,
  dan `ts_utc` yang tidak terbaca dilaporkan apa adanya — "umur 0 menit" untuk DB kosong
  adalah kebohongan yang persis kebalikan dari peringatan yang ingin dibuat.

Stdlib saja, tanpa impor `core.*`: status harus jalan bahkan saat aplikasinya rusak.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

# `ts_utc` ikut dicetak apa adanya ke terminal operator, jadi ia harus lewat sanitizer yang
# SAMA dengan yang dipakai proc_status.py — bukan salinan ketiga (pelajaran TASKS 1.5b).
# Impornya lewat sys.path, bukan paket: kedua berkas ini sengaja stdlib-saja dan harus jalan
# dengan `python3` polos tanpa venv (lihat alasan lengkap di `scripts/proc_status.py`).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from proc_status import safe_text  # impor setelah sys.path disiapkan (disengaja)

ENV_DB_PATH = "DB_PATH"
ENV_INTERVAL_S = "RECORDER_INTERVAL_S"
DEFAULT_DB_PATH = "./data/ryo.db"
DEFAULT_INTERVAL_S = 3600.0
STALE_FACTOR = 2  # satu siklus terlewat penuh, bukan sekadar telat sedikit
STALE_WARNING = "TIDAK ADA SNAPSHOT BARU > 2× interval"
FUTURE_WARNING = "TIMESTAMP DI MASA DEPAN — jam bergeser"
TS_TEXT_LIMIT = 60


def read_only_uri(path: Path) -> str:
    """URI baca-saja; `as_uri()` mengutip spasi/`?` supaya path aneh tidak terpotong."""
    return path.resolve().as_uri() + "?mode=ro"


def _interval_note(raw: str, why: str) -> str:
    return f"{ENV_INTERVAL_S}={raw!r} {why}; memakai default {DEFAULT_INTERVAL_S:.0f}s"


def resolve_interval_s() -> tuple[float, str | None]:
    """Interval perekam dari env. Nilai tak masuk akal → default + alasan yang dicetak."""
    raw = os.environ.get(ENV_INTERVAL_S)
    if raw is None or raw.strip() == "":
        return DEFAULT_INTERVAL_S, None
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_INTERVAL_S, _interval_note(raw, "tidak terbaca")
    if value <= 0:
        return DEFAULT_INTERVAL_S, _interval_note(raw, "bukan detik positif")
    return value, None


def parse_ts(text: str | None) -> datetime | None:
    """`ts_utc` → datetime sadar-zona; bentuk yang tidak terbaca → None, bukan "sekarang"."""
    if not text:
        return None
    raw = text.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def format_age(seconds: float) -> str:
    total = int(seconds)
    sign = "-" if total < 0 else ""
    total = abs(total)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{sign}{hours}j {minutes}m"
    if minutes:
        return f"{sign}{minutes}m"
    return f"{sign}{secs} dtk"  # di bawah satu menit, detik yang berarti


def age_lines(newest: str | None, now: datetime, interval_s: float) -> list[str]:
    """Baris umur + peringatan ambang. Tidak pernah menebak umur yang tidak bisa dihitung.

    Umur NEGATIF (timestamp di masa depan) mendapat peringatannya sendiri, bukan diam.
    `ts_utc` ditulis dari jam mesin (`core/store.py`), dan jam yang mundur setelah
    suspend/NTP — biasa di WSL2 — membuat setiap snapshot tampak "masa depan". Perbandingan
    `age_s > limit_s` lalu selalu salah, sehingga peringatan basi mati permanen: persis
    "nilai tak masuk akal dibaca sebagai segar" yang dilarang doktrin berkas ini.
    """
    if newest is None:
        return ["  terbaru  : belum ada satu pun snapshot (n=0; umur tidak dihitung, bukan nol)"]
    shown = safe_text(newest, TS_TEXT_LIMIT)
    parsed = parse_ts(newest)
    if parsed is None:
        return [
            f"  terbaru  : {shown}",
            f"  umur     : tidak terbaca dari ts_utc {shown!r} — jangan dianggap segar",
        ]
    age_s = (now - parsed).total_seconds()
    limit_s = STALE_FACTOR * interval_s
    lines = [
        f"  terbaru  : {shown}",
        f"  umur     : {format_age(age_s)} (ambang {STALE_FACTOR}× interval = {format_age(limit_s)})",
    ]
    if age_s < 0:
        lines.append(
            f"  PERINGATAN: {FUTURE_WARNING} — ts_utc {shown} berada {format_age(-age_s)} di depan "
            "jam mesin; umur tidak bisa dipercaya dan ambang basi TIDAK bisa dinilai"
        )
    elif age_s > limit_s:
        lines.append(
            f"  PERINGATAN: {STALE_WARNING} — umur {format_age(age_s)} > {format_age(limit_s)}; "
            "perekam kemungkinan mati atau macet, jam yang terlewat tidak bisa dibuat ulang"
        )
    return lines


def main() -> int:
    path = Path(os.environ.get(ENV_DB_PATH, DEFAULT_DB_PATH))
    if not path.exists():
        print(f"  data: belum ada ({path})")
        return 0
    interval_s, interval_note = resolve_interval_s()
    if interval_note:
        print(f"  catatan  : {interval_note}")
    try:
        c = sqlite3.connect(read_only_uri(path), uri=True)
    except (sqlite3.Error, ValueError, OSError) as exc:
        print(f"  data: {path} tidak bisa dibuka baca-saja: {exc}")
        return 0

    def has(table: str) -> bool:
        row = c.execute("SELECT 1 FROM sqlite_master WHERE type=? AND name=?", ("table", table)).fetchone()
        return row is not None

    try:
        if has("snapshots"):
            total = c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            per = dict(c.execute("SELECT tool, COUNT(*) FROM snapshots GROUP BY tool"))
            last = c.execute("SELECT MAX(ts_utc) FROM snapshots").fetchone()[0]
            print(f"  snapshots: {total} {per}")
            for line in age_lines(last, datetime.now(UTC), interval_s):
                print(line)
        else:
            print("  snapshots: tabel tidak ada (bukan 0 baris)")
        for table, label in (("decisions", "keputusan"), ("trade_events", "trade event")):
            if has(table):
                print(f"  {label}: {c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]}")  # noqa: S608 - `table` dari tuple literal di baris `for`, bukan input
    except sqlite3.Error as exc:
        print(f"  data: {path} tidak bisa dibaca: {exc}")
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
