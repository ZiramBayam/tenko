"""Pembacaan buku besar (`decisions`, `trade_events`) — koneksi BACA-SAJA (TASKS 2.4).

`core/store.py` adalah satu-satunya jalan MENULIS (append-only, ADR-006) dan ia sengaja hanya
menyediakan pembaca untuk `snapshots`: `latest_fresh`, `recent_args`, `get_snapshots`. Tidak
ada pembaca `decisions`/`trade_events` di sana, sedangkan agen membutuhkannya untuk dua hal
yang tidak bisa dikerjakan tanpa membaca: melipat posisi terbuka (`trade_events`) dan memutar
ulang satu keputusan (`decisions`).

Karena `core/` bukan milik task ini, pembacaannya hidup di sini — dengan satu syarat yang
membuatnya tetap aman untuk ADR-006: **koneksinya benar-benar tidak bisa menulis**, bukan
berjanji tidak menulis. Semua penulisan agen tetap lewat `core.store.Store`. Kalau kelak
`core/store.py` menumbuhkan pembaca sendiri, modul ini diganti pemanggilannya — bukan
ditambah jalur tulis.

**Bagaimana syarat itu ditegakkan, dan mengapa bentuk sebelumnya BOHONG** (temuan TINGGI
security-reviewer, 4 Sep 2026, dibuktikan dengan tulisan yang benar-benar berhasil): URI
sebelumnya dirakit dengan `f"file:{path}?mode=ro"`. `sqlite3` mengurai teks setelah `file:`
sebagai URI, sehingga path yang MEMUAT `?`/`#`/`&` — dan itu nama berkas yang SAH di POSIX —
menyuntikkan parameternya sendiri: berkas bernama `evil.db?mode=rwc&x=` dibuka `rwc` dan
`INSERT` berhasil lewat koneksi yang modul ini sebut "baca-saja". Sejak 4 Sep 2026 path
di-`urllib.parse.quote` (`?`, `#`, `&`, `%` menjadi persen-escape yang diurai balik SQLite
menjadi NAMA BERKAS, bukan sintaks), dan sebagai lapis kedua tiap koneksi menjalankan
`PRAGMA query_only = ON`. Keduanya dipatok tes penyerang di `tests/test_agent_ledger.py`.

**Posisi = lipatan atas event, bukan baris yang diperbarui.** `trade_events` menyimpan `open`
lalu `exit`; menutup posisi berarti MENAMBAH baris `exit`. Posisi terbuka = `decision_id` yang
punya `open` tanpa `exit`. Baris `open` tidak pernah disentuh lagi.

**Generasi lama.** 9 baris `trade_events` warisan di `data/ryo.db` menunjuk `decision_id`
generasi sebelumnya (`params_hash` berbeda). Penyaringannya adalah TASKS 2.4b dan TIDAK
dikerjakan di sini; yang disediakan modul ini adalah `params_hash` tiap keputusan
(:attr:`DecisionRow.params_hash`) supaya pemanggil bisa menyaringnya tanpa menebak.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from core.store import StoreError

#: Kolom yang dibaca — dieja penuh, tidak pernah `SELECT *` (skema bisa tumbuh).
DECISION_COLUMNS = (
    "id, ts_utc, symbol, decision, blocking_json, snapshot_ids_json, params_hash,"
    " decision_hash, card_json, simulated"
)
EVENT_COLUMNS = "id, decision_id, symbol, direction, kind, price, size_usd, reason, ts_utc, simulated"


class LedgerError(StoreError):
    """Buku besar tidak terbaca. Turunan `StoreError`: pemanggil menangani satu keluarga saja."""


@dataclass(frozen=True, slots=True)
class DecisionRow:
    """Satu baris `decisions` apa adanya; JSON tetap TEKS sampai pemanggil memintanya."""

    id: int
    ts_utc: str
    symbol: str
    decision: str
    blocking_json: str
    snapshot_ids_json: str
    params_hash: str
    decision_hash: str
    card_json: str
    simulated: int

    def blocking(self) -> list[Any]:
        return _loads_list(self.blocking_json, "blocking_json")

    def snapshot_ids(self) -> list[int]:
        ids = _loads_list(self.snapshot_ids_json, "snapshot_ids_json")
        out: list[int] = []
        for value in ids:
            if isinstance(value, bool) or not isinstance(value, int):
                raise LedgerError(f"decisions.snapshot_ids_json memuat {value!r}, bukan id baris")
            out.append(value)
        return out

    def card(self) -> dict[str, Any]:
        try:
            card = json.loads(self.card_json)
        except ValueError:
            raise LedgerError(f"decisions.card_json baris {self.id} bukan JSON") from None
        if not isinstance(card, dict):
            raise LedgerError(f"decisions.card_json baris {self.id} bukan objek")
        return card


@dataclass(frozen=True, slots=True)
class EventRow:
    """Satu baris `trade_events`."""

    id: int
    decision_id: int
    symbol: str
    direction: str
    kind: str
    price: float
    size_usd: float
    reason: str
    ts_utc: str
    simulated: int


@dataclass(frozen=True, slots=True)
class Position:
    """Posisi practice yang masih terbuka: baris `open` yang belum punya pasangan `exit`."""

    decision_id: int
    symbol: str
    direction: str
    entry_price: float
    size_usd: float
    opened_at: str


def _loads_list(text: str, label: str) -> list[Any]:
    try:
        value = json.loads(text)
    except ValueError:
        raise LedgerError(f"{label} bukan JSON yang terbaca") from None
    if not isinstance(value, list):
        raise LedgerError(f"{label} harus daftar, bukan {type(value).__name__}")
    return value


@contextmanager
def _connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Koneksi BACA-SAJA ke berkas DB `db_path`.

    Path-nya WAJIB dioper pemanggil, dan pemanggilnya selalu memberi `store.path` — resolusi
    `DB_PATH` hidup di satu tempat saja (`core.store`), sehingga agen tidak bisa membaca berkas
    yang berbeda dari berkas yang ia tulisi.
    """
    path = Path(db_path)
    # `quote`: path adalah NAMA BERKAS, bukan sintaks URI. Tanpa ini, `?`/`#`/`&` di dalam nama
    # berkas menjadi parameter URI dan `mode=ro` bisa ditimpa (lihat docstring modul).
    uri = "file:" + quote(path.as_posix(), safe="/") + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise LedgerError(f"tidak bisa membuka {path} baca-saja: {type(exc).__name__}") from None
    try:
        # Lapis kedua, murah: walau URI-nya kelak salah rakit lagi, koneksi ini tetap menolak
        # setiap pernyataan yang mengubah berkas.
        conn.execute("PRAGMA query_only = ON")
        yield conn
    except sqlite3.Error as exc:
        raise LedgerError(f"gagal membaca {path}: {type(exc).__name__}: {exc}") from None
    finally:
        conn.close()


def read_decision(db_path: str | Path, decision_id: int) -> DecisionRow | None:
    """Satu baris `decisions`, atau None bila id itu tidak ada."""
    if isinstance(decision_id, bool) or not isinstance(decision_id, int):
        raise LedgerError(f"decision_id harus bilangan bulat, bukan {type(decision_id).__name__}")
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT {DECISION_COLUMNS} FROM decisions WHERE id = ?",  # noqa: S608 - kolom dari konstanta modul
            (decision_id,),
        ).fetchone()
    return None if row is None else DecisionRow(*row)


def recent_decisions(db_path: str | Path, limit: int = 20) -> list[DecisionRow]:
    """Keputusan terbaru dulu — dipakai ringkasan tick dan UI."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {DECISION_COLUMNS} FROM decisions ORDER BY id DESC LIMIT ?",  # noqa: S608 - kolom dari konstanta modul
            (max(0, int(limit)),),
        ).fetchall()
    return [DecisionRow(*row) for row in rows]


def events_for(db_path: str | Path, decision_id: int) -> list[EventRow]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {EVENT_COLUMNS} FROM trade_events WHERE decision_id = ? ORDER BY id",  # noqa: S608 - kolom dari konstanta modul
            (decision_id,),
        ).fetchall()
    return [EventRow(*row) for row in rows]


def open_positions(db_path: str | Path) -> list[Position]:
    """Lipatan `trade_events`: `open` yang belum punya `exit`, urut `decision_id`.

    Baris `open` kedua untuk satu `decision_id` (tidak seharusnya terjadi; tabel append-only
    tidak bisa mencegahnya) dilipat menjadi SATU posisi — yang pertama. Menjumlahkannya akan
    mengarang ukuran yang tidak pernah dibuka.
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {EVENT_COLUMNS} FROM trade_events ORDER BY id"  # noqa: S608 - kolom dari konstanta modul
        ).fetchall()
    opened: dict[int, Position] = {}
    closed: set[int] = set()
    for row in rows:
        event = EventRow(*row)
        if event.kind == "open":
            opened.setdefault(
                event.decision_id,
                Position(
                    decision_id=event.decision_id,
                    symbol=event.symbol,
                    direction=event.direction,
                    entry_price=event.price,
                    size_usd=event.size_usd,
                    opened_at=event.ts_utc,
                ),
            )
        elif event.kind == "exit":
            closed.add(event.decision_id)
    return [pos for decision_id, pos in sorted(opened.items()) if decision_id not in closed]
