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
tinjauan keamanan, 4 Sep 2026, dibuktikan dengan tulisan yang benar-benar berhasil): URI
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

**Generasi lama (TASKS 2.4b, ADR-016).** 9 baris `trade_events` warisan di `data/ryo.db`
menunjuk `decision_id` yang ditulis implementasi yang dihapus 1 Sep 2026. Lipatan posisi
(:func:`fold_positions`) hanya melipat generasi BERJALAN dan mengembalikan yang diabaikan
sebagai data, bukan membuangnya diam-diam: pemanggil wajib mencetak :meth:`PositionFold.notes`.

**Pembeda generasinya adalah ada/tidaknya `algo` di `card_json`** (ADR-016 butir 2), bukan
`params_hash` dan bukan ambang id. Alasannya bisa diuji: `params_hash` SENGAJA berubah tiap
kali ambang preflight bergeser (mis. ADR-017), jadi menyaring dengannya akan menandai keputusan
jujur hari ini sebagai warisan besok; sedangkan `algo` menamai FORMAT hash keputusan dan hanya
berubah lewat kenaikan versi format yang disengaja. Baris tanpa kunci `algo` = ditulis kode
yang sudah tidak ada. Tidak ada kolom baru, tidak ada migrasi, tidak ada "abaikan id < 20".
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from core.replay import ALGO
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

    def algo(self) -> str | None:
        """Nama algoritma hash keputusan yang tercatat di `card_json`, atau None.

        None berarti "baris ini tidak menyebut algoritmanya" — generasi sebelum kait
        STALE_ALGO ada. Fungsi ini sengaja TIDAK melempar untuk `card_json` yang rusak atau
        bukan objek: kartu yang tidak terbaca juga tidak menyebut algoritma apa pun, dan
        jawabannya sama-sama "bukan generasi berjalan". Yang melempar tetap :meth:`card`.
        """
        try:
            card = json.loads(self.card_json)
        except (TypeError, ValueError):
            return None
        if not isinstance(card, dict):
            return None
        value = card.get("algo")
        return value if isinstance(value, str) else None

    def is_current_generation(self) -> bool:
        """True bila `card_json` menyebut algoritma hash yang berlaku sekarang (ADR-016)."""
        return self.algo() == ALGO

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


#: Batas kolom `INTEGER` SQLite. Id di luar rentang ini bukan "tidak ada", melainkan nilai yang
#: TIDAK BISA ditanyakan ke SQLite sama sekali — `sqlite3` melempar `OverflowError` sebelum
#: kueri dijalankan (TASKS 3.1b).
SQLITE_INT_MAX = 2**63 - 1
SQLITE_INT_MIN = -(2**63)


def read_decision(db_path: str | Path, decision_id: int) -> DecisionRow | None:
    """Satu baris `decisions`, atau None bila id itu tidak ada.

    Id yang bukan bilangan bulat DAN id di luar rentang `INTEGER` SQLite sama-sama menjadi
    :class:`LedgerError`. Sebelum TASKS 3.1b hanya yang pertama yang dijaga, sehingga janji
    docstring ini ("int atau `LedgerError`") tidak benar untuk sebagian int: `read_decision(2**63)`
    melempar `OverflowError` mentah dari `sqlite3`, dan `python -m agent.replay 9223372036854775808`
    memberi traceback sementara `agent.replay abc` dijawab satu kalimat rapi. Ditemukan
    tinjauan keamanan 3.1 — batas rentang di route HTTP sudah cukup untuk UI, tetapi pemanggil
    KEDUA (CLI replay) sudah ada hari ini.
    """
    if isinstance(decision_id, bool) or not isinstance(decision_id, int):
        raise LedgerError(f"decision_id harus bilangan bulat, bukan {type(decision_id).__name__}")
    if not SQLITE_INT_MIN <= decision_id <= SQLITE_INT_MAX:
        raise LedgerError(
            f"decision_id {decision_id} di luar rentang INTEGER SQLite "
            f"[{SQLITE_INT_MIN}, {SQLITE_INT_MAX}]: id sebesar itu tidak bisa ditanyakan, "
            "bukan sekadar tidak ada"
        )
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


def all_events(db_path: str | Path) -> list[EventRow]:
    """Semua `trade_events` urut id — bahan mentah lipatan posisi."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {EVENT_COLUMNS} FROM trade_events ORDER BY id"  # noqa: S608 - kolom dari konstanta modul
        ).fetchall()
    return [EventRow(*row) for row in rows]


def algo_by_decision(db_path: str | Path, decision_ids: Sequence[int]) -> dict[int, str | None]:
    """`{decision_id: algo}` untuk id yang diminta; id yang barisnya TIDAK ADA tidak muncul.

    Bedanya penting: `None` berarti "barisnya ada, tetapi tidak menyebut algoritma" (generasi
    lama), sedangkan kunci yang hilang berarti "event menunjuk keputusan yang tidak ada".
    """
    try:
        # `trade_events.decision_id` bertipe longgar di SQLite (tanpa STRICT), jadi nilai yang
        # bukan angka MUNGKIN ada di kolom itu. Melempar `ValueError` mentah dari sini membuat
        # tick meledak alih-alih melaporkan (temuan RENDAH tinjauan keamanan, 4 Sep 2026).
        wanted = sorted({int(i) for i in decision_ids})
    except (TypeError, ValueError) as exc:
        raise LedgerError(f"trade_events.decision_id memuat nilai yang bukan id baris: {exc}") from None
    if not wanted:
        return {}
    placeholders = ",".join("?" for _ in wanted)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {DECISION_COLUMNS} FROM decisions WHERE id IN ({placeholders})",  # noqa: S608 - kolom & placeholder dari konstanta/hitungan
            wanted,
        ).fetchall()
    return {row[0]: DecisionRow(*row).algo() for row in rows}


@dataclass(frozen=True, slots=True)
class PositionFold:
    """Hasil lipatan `trade_events`: yang dihitung, DAN yang sengaja tidak dihitung.

    Yang diabaikan tidak dibuang: ia dikembalikan sebagai data supaya pemanggil bisa
    menuliskannya (TASKS 2.4b — "diabaikan secara TERTULIS, bukan diam-diam").
    """

    positions: tuple[Position, ...] = ()
    #: Event yang keputusannya bukan generasi berjalan (`card_json` tanpa `algo` yang berlaku).
    stale_events: tuple[EventRow, ...] = ()
    #: Event yang `decision_id`-nya tidak punya baris `decisions` sama sekali.
    orphan_events: tuple[EventRow, ...] = ()

    def notes(self) -> list[str]:
        """Kalimat yang WAJIB dicetak/dicatat pemanggil bila ada yang diabaikan."""
        out: list[str] = []
        if self.stale_events:
            ids = sorted({event.decision_id for event in self.stale_events})
            out.append(
                f"{len(self.stale_events)} trade_events generasi sebelumnya diabaikan "
                f"(STALE_ALGO); decision_id: {', '.join(str(i) for i in ids)} — "
                "kartunya tidak menyebut algoritma hash yang berlaku, jadi posisinya tidak "
                "bisa diturunkan ulang dari bukti beku dan tidak dihitung sebagai posisi terbuka"
            )
        if self.orphan_events:
            ids = sorted({event.decision_id for event in self.orphan_events})
            out.append(
                f"{len(self.orphan_events)} trade_events diabaikan karena decision_id-nya tidak "
                f"ada di ledger: {', '.join(str(i) for i in ids)}"
            )
        return out


def fold_positions(
    events: Sequence[EventRow], algo_of: Mapping[int, str | None], *, algo: str = ALGO
) -> PositionFold:
    """Lipat event menjadi posisi terbuka — HANYA untuk keputusan generasi berjalan.

    Fungsi murni (tanpa I/O) supaya bisa diuji dengan fixture campuran warisan + baru.
    Posisi terbuka = `decision_id` yang punya `open` tanpa `exit`. Baris `open` kedua untuk
    satu `decision_id` (tidak seharusnya terjadi; tabel append-only tidak bisa mencegahnya)
    dilipat menjadi SATU posisi — yang pertama. Menjumlahkannya akan mengarang ukuran yang
    tidak pernah dibuka.

    `algo_of` memetakan `decision_id` ke nilai `card_json["algo"]`-nya; kunci yang hilang =
    tidak ada baris keputusan. Keduanya tidak dilipat, masing-masing dengan sebab tersendiri.
    """
    opened: dict[int, Position] = {}
    closed: set[int] = set()
    stale: list[EventRow] = []
    orphan: list[EventRow] = []
    for event in events:
        if event.decision_id not in algo_of:
            orphan.append(event)
            continue
        if algo_of[event.decision_id] != algo:
            stale.append(event)
            continue
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
    positions = tuple(pos for decision_id, pos in sorted(opened.items()) if decision_id not in closed)
    return PositionFold(positions=positions, stale_events=tuple(stale), orphan_events=tuple(orphan))


def read_positions(db_path: str | Path) -> PositionFold:
    """Lipatan posisi atas DB `db_path`, berikut daftar event yang sengaja tidak dilipat."""
    events = all_events(db_path)
    algo_of = algo_by_decision(db_path, [event.decision_id for event in events])
    return fold_positions(events, algo_of)


def open_positions(db_path: str | Path) -> list[Position]:
    """Posisi terbuka generasi BERJALAN, urut `decision_id`.

    Sengaja membuang keterangan "apa yang diabaikan": pemanggil yang perlu menuliskannya
    memakai :func:`read_positions` (TASKS 2.4b). Yang dikembalikan di sini tidak pernah
    memuat posisi warisan — itu yang membuat hitungan posisi terbuka tidak berbohong.
    """
    return list(read_positions(db_path).positions)
