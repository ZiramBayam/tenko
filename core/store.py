"""Penyimpan snapshot/keputusan/trade event — SQLite, hanya-tambah (ADR-006).

Sifat modul ini (setiap klaim di bawah punya tes di ``tests/test_store.py``):

* **Hanya-tambah, ditegakkan di jalur eksekusi.** Yang menegakkannya BUKAN pemindaian teks
  sumber, melainkan tiga lapis runtime:

  1. :func:`sqlite3.Connection.set_authorizer` dengan **allowlist aksi** pada koneksi kerja.
     Hanya ``SQLITE_SELECT``/``SQLITE_READ``/``SQLITE_INSERT``/``SQLITE_TRANSACTION``/
     ``SQLITE_FUNCTION`` yang lolos; seluruh aksi pengubah & pembuang baris,
     ``SQLITE_DROP_*``, ``SQLITE_ALTER_TABLE``, ``SQLITE_ATTACH``, ``SQLITE_CREATE_*`` dan
     sisanya ditolak SQLite sendiri, apa pun SQL yang dikirim — termasuk SQL yang dirakit
     saat runtime. (Nama konstanta aksi itu sengaja tidak dieja di berkas ini: TASKS 1.2
     AC (4) menuntut pencarian verb pengubah/pembuang baris di berkas ini nihil. Daftar
     lengkap aksi yang ditolak dibuktikan di ``tests/test_store.py`` dengan SQL sungguhan.)
     ``PRAGMA`` disaring per nama (:data:`ALLOWED_PRAGMAS`), jadi ``writable_schema`` dan
     ``journal_mode`` juga tertutup.
  2. **Allowlist pernyataan**: :meth:`Store._insert` menolak SQL apa pun yang bukan salah
     satu dari tiga konstanta di :data:`INSERT_STATEMENTS`. Itu menutup satu-satunya celah
     yang lolos authorizer, yaitu ``INSERT OR REPLACE``/``REPLACE INTO`` (SQLite melaporkan
     keduanya sebagai aksi INSERT, padahal keduanya membuang baris lama).
  3. Pembuatan tabel dijalankan pada **koneksi terpisah berumur pendek** yang langsung
     ditutup (``_create_schema``), sehingga koneksi kerja tidak pernah punya izin DDL.

  Duplikat DISENGAJA: menulis payload yang sama dua kali menghasilkan dua baris dengan
  ``payload_hash``/``args_hash`` identik. Tanpa itu grading kehilangan ``n`` (berapa kali
  kondisi yang sama teramati) dan replay kehilangan urutan waktu.

* **Skema mengikuti ``data/ryo.db`` yang sudah hidup.** ``CREATE TABLE IF NOT EXISTS``
  TIDAK memigrasi tabel lama, jadi kolom yang meleset berarti tulis diam-diam ke bentuk
  yang salah. Karena itu :class:`Store` menjalankan guard saat start: setiap tabel yang
  SUDAH ada dibandingkan ``(nama, tipe, notnull, pk)`` kolomnya dengan
  :data:`EXPECTED_SCHEMA` (urutan kolom diabaikan — semua query menyebut kolom eksplisit),
  dan ketidakcocokan → :class:`StoreSchemaError`. Tipe ikut dibandingkan karena ``id TEXT``
  dengan nama kolom yang benar akan menerima baris ber-``id`` NULL yang tidak bisa diambil
  lagi lewat :meth:`Store.get_snapshots` — replay rusak diam-diam. Snapshot yang sudah
  terekam tidak bisa direkam ulang; itulah yang dijaga guard ini.

* **Kanonikalisasi sama dengan perekam.** :func:`canonical_json` / :func:`sha256_hex`
  berperilaku persis seperti ``canonical``/``sha256_hex`` di
  ``scripts/bootstrap_recorder.py`` (``sort_keys``, separator rapat, ``ensure_ascii=False``,
  ``allow_nan=False``), sehingga hash baris lama dan baris baru bisa dibandingkan. Ada tes
  yang menghitung ulang hash baris nyata dari ``data/ryo.db`` (dibuka baca-saja).

* **Kegagalan selalu eksplisit.** Semua jalur publik hanya melempar :class:`StoreError` dan
  turunannya: argumen aneh, payload yang tidak bisa dikanonikalisasi (NaN/Infinity, lone
  surrogate, bersarang terlalu dalam), maupun kegagalan SQLite. Satu envelope RYO yang aneh
  tidak boleh mematikan siklus perekam dengan exception yang tak terduga.

* **``simulated`` selalu 1.** Nilainya konstanta di dalam pernyataan ``INSERT``; tidak ada
  parameter, jadi tidak ada jalur yang bisa menulis 0 (docs/rules-compliance.md).

* **Mode baca-saja opsional.** ``Store(path, read_only=True)`` membuka koneksi SQLite URI
  ``mode=ro``: pembacaan tetap jalan, jalur tulis ditolak :class:`StoreReadOnlyError` DAN
  oleh SQLite sendiri. Dipakai UI, yang memang tidak pernah menulis apa pun.

Yang SENGAJA tidak ada di sini: agregasi, pembersihan, migrasi, dan cache. Membuang data
hanya lewat penghapusan berkas DB dari luar aplikasi (ADR-006).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("core.store")

ENV_DB_PATH = "DB_PATH"
DEFAULT_DB_PATH = "./data/ryo.db"

#: Nilai `kind` pada trade_events: posisi = lipatan event (spec §5).
TRADE_EVENT_KINDS: frozenset[str] = frozenset({"open", "exit"})
#: Arah practice trade; `deep_analysis.trade_plan` di semua sampel long.
TRADE_DIRECTIONS: frozenset[str] = frozenset({"long", "short"})
#: Keputusan preflight (spec §4).
DECISIONS: frozenset[str] = frozenset({"GO", "NO_GO"})

#: Berapa baris terbaru yang diperiksa `latest_fresh` sebelum menyerah. Urutan SQL memakai
#: perbandingan teks `ts_utc`; itu benar untuk ISO-8601 UTC yang ditulis modul ini dan oleh
#: perekam, dan batas ini menjaga biayanya tetap kecil meski satu (tool, args) punya ribuan baris.
FRESH_SCAN_LIMIT = 64

#: Batas baris yang dibaca `recent_args` (lihat metodenya). Perekam memanggil satu
#: `analyze_token` per simbol per siklus, jadi 8192 baris menutup jendela 26 jam bahkan pada
#: siklus 5 menit dengan 25 simbol (26 x 12 x 25 = 7800) tanpa memuat seluruh tabel.
RECENT_ARGS_LIMIT = 8192

_INT64_MAX = 2**63 - 1  # batas bind integer SQLite; di atas ini sqlite3 melempar OverflowError

# Skema PERSIS seperti data/ryo.db yang hidup (`.schema`, 1 Sep 2026). Dijalankan HANYA oleh
# _create_schema, pada koneksi terpisah yang langsung ditutup.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tool TEXT NOT NULL,
  args_json TEXT NOT NULL,
  args_hash TEXT NOT NULL,
  ts_utc TEXT NOT NULL,
  as_of TEXT,
  status TEXT,
  payload_json TEXT NOT NULL,
  payload_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snap_tool_ts ON snapshots(tool, ts_utc);
CREATE INDEX IF NOT EXISTS ix_snap_args ON snapshots(args_hash, ts_utc);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc TEXT NOT NULL,
  symbol TEXT NOT NULL,
  decision TEXT NOT NULL,
  blocking_json TEXT NOT NULL,
  snapshot_ids_json TEXT NOT NULL,
  params_hash TEXT NOT NULL,
  decision_hash TEXT NOT NULL,
  card_json TEXT NOT NULL,
  simulated INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS trade_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  direction TEXT NOT NULL,
  kind TEXT NOT NULL,
  price REAL NOT NULL,
  size_usd REAL NOT NULL,
  reason TEXT NOT NULL,
  ts_utc TEXT NOT NULL,
  simulated INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_events_decision ON trade_events(decision_id);
"""

#: Bentuk kolom yang diharapkan: (nama, tipe, notnull, pk) — dibaca dari `PRAGMA table_info`
#: pada data/ryo.db yang hidup. Urutan kolom sengaja diabaikan.
EXPECTED_SCHEMA: dict[str, frozenset[tuple[str, str, int, int]]] = {
    "snapshots": frozenset(
        {
            ("id", "INTEGER", 0, 1),
            ("tool", "TEXT", 1, 0),
            ("args_json", "TEXT", 1, 0),
            ("args_hash", "TEXT", 1, 0),
            ("ts_utc", "TEXT", 1, 0),
            ("as_of", "TEXT", 0, 0),
            ("status", "TEXT", 0, 0),
            ("payload_json", "TEXT", 1, 0),
            ("payload_hash", "TEXT", 1, 0),
        }
    ),
    "decisions": frozenset(
        {
            ("id", "INTEGER", 0, 1),
            ("ts_utc", "TEXT", 1, 0),
            ("symbol", "TEXT", 1, 0),
            ("decision", "TEXT", 1, 0),
            ("blocking_json", "TEXT", 1, 0),
            ("snapshot_ids_json", "TEXT", 1, 0),
            ("params_hash", "TEXT", 1, 0),
            ("decision_hash", "TEXT", 1, 0),
            ("card_json", "TEXT", 1, 0),
            ("simulated", "INTEGER", 1, 0),
        }
    ),
    "trade_events": frozenset(
        {
            ("id", "INTEGER", 0, 1),
            ("decision_id", "INTEGER", 1, 0),
            ("symbol", "TEXT", 1, 0),
            ("direction", "TEXT", 1, 0),
            ("kind", "TEXT", 1, 0),
            ("price", "REAL", 1, 0),
            ("size_usd", "REAL", 1, 0),
            ("reason", "TEXT", 1, 0),
            ("ts_utc", "TEXT", 1, 0),
            ("simulated", "INTEGER", 1, 0),
        }
    ),
}

_SNAPSHOT_COLUMNS = "id, tool, args_json, args_hash, ts_utc, as_of, status, payload_json, payload_hash"

#: Satu-satunya pernyataan tulis yang boleh dieksekusi koneksi kerja (lihat `Store._insert`).
#: `simulated` ditulis sebagai literal 1, bukan parameter.
SQL_INSERT_SNAPSHOT = (
    "INSERT INTO snapshots"
    " (tool, args_json, args_hash, ts_utc, as_of, status, payload_json, payload_hash)"
    " VALUES (?,?,?,?,?,?,?,?)"
)
SQL_INSERT_DECISION = (
    "INSERT INTO decisions"
    " (ts_utc, symbol, decision, blocking_json, snapshot_ids_json, params_hash,"
    "  decision_hash, card_json, simulated)"
    " VALUES (?,?,?,?,?,?,?,?,1)"
)
SQL_INSERT_TRADE_EVENT = (
    "INSERT INTO trade_events"
    " (decision_id, symbol, direction, kind, price, size_usd, reason, ts_utc, simulated)"
    " VALUES (?,?,?,?,?,?,?,?,1)"
)
INSERT_STATEMENTS: frozenset[str] = frozenset(
    {SQL_INSERT_SNAPSHOT, SQL_INSERT_DECISION, SQL_INSERT_TRADE_EVENT}
)

#: Aksi SQLite yang boleh dilakukan koneksi kerja. Semua aksi lain — termasuk seluruh
#: keluarga aksi pengubah/pembuang baris, DDL, dan ATTACH — ditolak SQLite sendiri.
ALLOWED_ACTIONS: frozenset[int] = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_FUNCTION,
    }
)
#: PRAGMA yang boleh dijalankan koneksi kerja (nama = argumen pertama authorizer).
#: `writable_schema` (jalan pintas ke skema) dan `journal_mode` (mengubah berkas DB yang
#: sedang ditulis perekam) sengaja TIDAK ada di sini.
ALLOWED_PRAGMAS: frozenset[str] = frozenset({"busy_timeout", "table_info"})


def _authorizer(
    action: int, arg1: str | None, arg2: str | None, dbname: str | None, source: str | None
) -> int:
    """Allowlist aksi SQLite untuk koneksi kerja; sisanya `SQLITE_DENY`.

    Dipasang lewat `set_authorizer`, jadi ia berlaku untuk SQL apa pun yang sampai ke
    koneksi ini — termasuk SQL yang dirakit saat runtime oleh kode masa depan.
    """
    if action == sqlite3.SQLITE_PRAGMA:
        if (arg1 or "").strip().lower() in ALLOWED_PRAGMAS:
            return sqlite3.SQLITE_OK
    elif action in ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    # Hanya kode angka + nama objek dari SQL kita sendiri yang dicatat; tanpa isi baris.
    log.warning("store: aksi SQLite %s pada %r ditolak (store hanya-tambah)", action, arg1)
    return sqlite3.SQLITE_DENY


# --- exception ---------------------------------------------------------------------------
class StoreError(Exception):
    """Akar semua kegagalan store."""


class StoreSchemaError(StoreError):
    """Skema DB yang ditemukan tidak cocok dengan yang diharapkan modul ini."""


class StoreReadOnlyError(StoreError):
    """Jalur tulis dipanggil pada store yang dibuka baca-saja."""


# --- kanonikalisasi ----------------------------------------------------------------------
def canonical_json(obj: Any) -> str:
    """JSON kanonik: kunci terurut, tanpa spasi, non-ASCII apa adanya, NaN/Infinity ditolak.

    Persis parameter yang dipakai ``scripts/bootstrap_recorder.py`` supaya baris lama dan
    baris baru punya bentuk teks — dan karenanya hash — yang bisa dibandingkan. Apa pun yang
    tidak bisa dikanonikalisasi menjadi :class:`StoreError`, bukan exception mentah.
    """
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        # NaN/Infinity, objek non-JSON, dan struktur bersarang ekstrem harus gagal keras:
        # hash yang tidak stabil membuat replay berbohong.
        raise StoreError(f"objek tidak bisa dikanonikalisasi: {type(exc).__name__}") from None


def sha256_hex(obj: Any) -> str:
    """sha256 heksadesimal atas bentuk kanonik `obj`."""
    text = canonical_json(obj)
    try:
        raw = text.encode("utf-8")
    except UnicodeEncodeError:
        # `json.loads` menerima lone surrogate (mis. "\ud800" di dalam payload RYO); UTF-8
        # tidak. Gagal berlabel di sini, bukan UnicodeEncodeError yang mematikan siklus.
        raise StoreError("teks kanonik memuat karakter yang tidak bisa di-encode UTF-8") from None
    return hashlib.sha256(raw).hexdigest()


def _utc_now_iso() -> str:
    """Waktu tulis: UTC ISO-8601 dengan offset eksplisit (bentuk yang sama dengan perekam)."""
    return datetime.now(UTC).isoformat()


def _parse_ts(raw: str | None) -> datetime | None:
    """Parse `ts_utc`; bentuk yang tidak terbaca → None (dianggap tidak segar, bukan segar)."""
    if not raw:
        return None
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


# --- validasi argumen (semua kegagalan → StoreError) ---------------------------------------
def _as_text(value: Any, label: str) -> str:
    """Kolom teks hanya menerima `str`; objek lain akan tersimpan sebagai repr Python."""
    if not isinstance(value, str):
        raise StoreError(f"{label} harus str, bukan {type(value).__name__}")
    return value


def _as_int(value: Any, label: str) -> int:
    if isinstance(value, float) and not value.is_integer():
        raise StoreError(f"{label} harus bilangan bulat, bukan pecahan")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise StoreError(f"{label} harus bilangan bulat, bukan {type(value).__name__}") from None
    if not -_INT64_MAX - 1 <= number <= _INT64_MAX:
        raise StoreError(f"{label} di luar jangkauan integer SQLite")
    return number


def _as_finite_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise StoreError(f"{label} harus angka, bukan {type(value).__name__}") from None
    if not math.isfinite(number):
        # SQLite menyimpan NaN sebagai NULL; angka yang tidak terhingga tidak pernah jadi
        # harga yang jujur (ADR-004).
        raise StoreError(f"{label} harus angka terhingga")
    return number


def _optional_text(value: Any) -> str | None:
    """Kolom indeks `as_of`/`status`: hanya string yang disalin; bentuk lain → NULL.

    Payload aslinya tetap utuh di `payload_json`, jadi tidak ada informasi yang hilang —
    yang dihindari adalah kolom indeks berisi repr Python dari objek (mis. "{'a': 1}").
    """
    return value if isinstance(value, str) else None


def _resolve_path(db_path: str | Path | None) -> Path:
    """Path berkas DB; bentuk yang menipu ditolak, bukan diterima diam-diam.

    `DB_PATH="file:/x/ryo.db?mode=rwc"` TIDAK dibuka sebagai URI oleh `sqlite3.connect`
    tanpa `uri=True`: tanpa penolakan ini ia akan membuat pohon direktori bernama `file:`
    dan memulai DB kosong — perekam menulis ke satu berkas, aplikasi membaca berkas lain,
    tanpa satu pun peringatan. DB in-memory juga ditolak: store harus tahan restart (ADR-006).
    """
    raw = db_path if db_path is not None else os.environ.get(ENV_DB_PATH, DEFAULT_DB_PATH)
    text = str(raw).strip()
    if not text:
        raise StoreError(f"{ENV_DB_PATH} kosong")
    lowered = text.lower()
    if lowered.startswith("file:") or "?mode=" in lowered or "?cache=" in lowered:
        raise StoreError(f"path DB berbentuk URI tidak didukung: {text!r} — beri path berkas biasa")
    if ":memory:" in lowered:
        raise StoreError("DB in-memory tidak didukung: store harus tahan restart (ADR-006)")
    return Path(text)


# --- baris -------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Snapshot:
    """Satu baris `snapshots` apa adanya; JSON diurai hanya saat diminta."""

    id: int
    tool: str
    args_json: str
    args_hash: str
    ts_utc: str
    as_of: str | None
    status: str | None
    payload_json: str
    payload_hash: str

    @property
    def args(self) -> Any:
        """Argumen tool hasil urai `args_json`."""
        return json.loads(self.args_json)

    @property
    def payload(self) -> Any:
        """Envelope RYO hasil urai `payload_json` — apa adanya, tanpa default (ADR-004)."""
        return json.loads(self.payload_json)

    @property
    def ts(self) -> datetime | None:
        """`ts_utc` sebagai datetime sadar-zona; None bila bentuknya tidak terbaca."""
        return _parse_ts(self.ts_utc)

    @classmethod
    def from_row(cls, row: Sequence[Any]) -> Snapshot:
        return cls(
            id=int(row[0]),
            tool=str(row[1]),
            args_json=str(row[2]),
            args_hash=str(row[3]),
            ts_utc=str(row[4]),
            as_of=None if row[5] is None else str(row[5]),
            status=None if row[6] is None else str(row[6]),
            payload_json=str(row[7]),
            payload_hash=str(row[8]),
        )


# --- store -------------------------------------------------------------------------------
class Store:
    """Koneksi SQLite hanya-tambah ke `snapshots` / `decisions` / `trade_events`.

    Aman dipakai dari beberapa thread: satu `threading.Lock` melindungi koneksi
    (`check_same_thread=False`), dan `busy_timeout` membuat proses lain yang sedang menulis
    (perekam) ditunggu, bukan langsung gagal "database is locked".
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        read_only: bool = False,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self.path = _resolve_path(db_path)
        self.read_only = bool(read_only)
        self._busy_timeout_ms = _as_int(busy_timeout_ms, "busy_timeout_ms")
        self._lock = threading.Lock()
        self._closed = False

        if self.read_only:
            uri = f"file:{self.path.as_posix()}?mode=ro"
            try:
                self._db = sqlite3.connect(uri, uri=True, check_same_thread=False)
            except sqlite3.Error as exc:
                raise StoreError(f"tidak bisa membuka {self.path} baca-saja: {exc}") from None
        else:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._db = sqlite3.connect(str(self.path), check_same_thread=False)
            except (OSError, sqlite3.Error) as exc:
                raise StoreError(f"tidak bisa membuka {self.path}: {exc}") from None

        try:
            # Authorizer dipasang SEBELUM pernyataan pertama: koneksi ini tidak pernah punya
            # izin di luar allowlist, termasuk saat guard berjalan.
            self._db.set_authorizer(_authorizer)
            self._db.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            # Guard dijalankan SEBELUM pembuatan tabel: bentuk lama yang tidak cocok harus
            # menghentikan proses, bukan diam-diam menerima tulisan berikutnya.
            self.tables = self._check_schema()
            if not self.read_only and self.tables != frozenset(EXPECTED_SCHEMA):
                self._create_schema()
                self.tables = self._check_schema()
        except StoreError:
            self._db.close()
            raise
        except sqlite3.Error as exc:
            self._db.close()
            raise StoreError(f"gagal menyiapkan {self.path}: {exc}") from None

    # -- housekeeping --
    def __repr__(self) -> str:
        mode = "ro" if self.read_only else "rw"
        return f"Store(path={str(self.path)!r}, mode={mode!r})"

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- skema --
    def _create_schema(self) -> None:
        """Buat tabel/indeks pada koneksi terpisah yang langsung ditutup.

        Koneksi kerja tidak boleh punya izin DDL (authorizer menolaknya), jadi pembuatan
        skema memang tidak bisa — dan tidak boleh — lewat sana.
        """
        try:
            ddl = sqlite3.connect(str(self.path))
        except sqlite3.Error as exc:
            raise StoreError(f"tidak bisa membuat skema di {self.path}: {exc}") from None
        try:
            ddl.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            ddl.executescript(SCHEMA_SQL)
            ddl.commit()
        except sqlite3.Error as exc:
            raise StoreError(f"gagal membuat skema di {self.path}: {exc}") from None
        finally:
            ddl.close()

    def _check_schema(self) -> frozenset[str]:
        """Bandingkan bentuk kolom setiap tabel yang SUDAH ada dengan `EXPECTED_SCHEMA`.

        Ketidakcocokan → :class:`StoreSchemaError` yang menyebut tabel, kolom yang hilang,
        dan kolom asing (nama + tipe + notnull + pk). Tabel yang belum ada bukan kesalahan:
        mode tulis membuatnya, mode baca-saja memperlakukannya kosong (perekam mungkin belum
        menulis).
        """
        present: set[str] = set()
        for table, expected in EXPECTED_SCHEMA.items():
            exists = self._db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?", ("table", table)
            ).fetchone()
            if exists is None:
                continue
            present.add(table)
            found = {
                (str(row[1]), str(row[2]).upper().strip(), int(row[3]), int(row[5]))
                # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
                for row in self._db.execute(f"PRAGMA table_info({table})")
            }
            if found == expected:
                continue
            missing = sorted(expected - found)
            extra = sorted(found - expected)
            raise StoreSchemaError(
                f"{self.path}: tabel '{table}' berbeda dari skema yang diharapkan"
                + (f"; kolom hilang: {missing}" if missing else "")
                + (f"; kolom asing: {extra}" if extra else "")
                + ". Jangan tulis ke DB ini; periksa berkasnya (data lama tidak bisa direkam ulang)."
            )
        return frozenset(present)

    # -- internal --
    def _require_writable(self) -> None:
        if self.read_only:
            raise StoreReadOnlyError(f"{self.path} dibuka baca-saja; jalur tulis ditolak")
        if self._closed:
            raise StoreError(f"{self.path} sudah ditutup")

    def _insert(self, sql: str, params: Sequence[Any]) -> int:
        """Jalankan salah satu pernyataan di `INSERT_STATEMENTS`, lalu commit.

        Commit per baris supaya `kill -9` di tengah siklus tidak memakan satu siklus penuh.
        SQL yang tidak persis sama dengan salah satu konstanta ditolak: itu yang menutup
        `INSERT OR REPLACE`/`REPLACE INTO`, satu-satunya bentuk perusak baris yang lolos
        authorizer (SQLite melaporkannya sebagai aksi INSERT).
        """
        if sql not in INSERT_STATEMENTS:
            raise StoreError("pernyataan tulis di luar daftar tetap store hanya-tambah")
        self._require_writable()
        with self._lock:
            try:
                cursor = self._db.execute(sql, tuple(params))
                self._db.commit()
            except sqlite3.Error as exc:
                raise StoreError(f"gagal menyimpan ke {self.path}: {type(exc).__name__}: {exc}") from None
            row_id = cursor.lastrowid
        if row_id is None:  # pragma: no cover — sqlite selalu mengisi lastrowid untuk INSERT
            raise StoreError("penulisan tidak mengembalikan id baris")
        return int(row_id)

    def _select(self, sql: str, params: Sequence[Any] = ()) -> list[Any]:
        if self._closed:
            raise StoreError(f"{self.path} sudah ditutup")
        with self._lock:
            try:
                return self._db.execute(sql, tuple(params)).fetchall()
            except sqlite3.Error as exc:
                raise StoreError(f"gagal membaca {self.path}: {type(exc).__name__}: {exc}") from None

    # -- tulis --
    def insert_snapshot(
        self,
        tool: str,
        args: Mapping[str, Any],
        envelope: Mapping[str, Any],
        *,
        ts_utc: str | None = None,
    ) -> int:
        """Simpan satu envelope RYO apa adanya; kembalikan id baris baru.

        `ts_utc` default = sekarang (UTC); parameter itu ada supaya tes bisa menyusun umur
        baris secara deterministik — jalur produksi tidak mengisinya.
        `as_of` dan `status` disalin dari envelope bila keduanya string (keduanya nullable di
        skema); envelope TIDAK dinormalisasi, tidak diberi default, dan isinya tidak
        divalidasi — itu tugas `core.ryo_client` (bentuk) dan `preflight` (makna). Memanggil
        ini dua kali dengan argumen yang sama menghasilkan dua baris; itu disengaja (ADR-006).
        """
        if not isinstance(envelope, Mapping):
            raise StoreError(f"envelope harus objek, bukan {type(envelope).__name__}")
        if not isinstance(args, Mapping):
            raise StoreError(f"args harus objek, bukan {type(args).__name__}")
        args_obj = dict(args)
        envelope_obj = dict(envelope)
        return self._insert(
            SQL_INSERT_SNAPSHOT,
            (
                _as_text(tool, "tool"),
                canonical_json(args_obj),
                sha256_hex(args_obj),
                _as_text(ts_utc, "ts_utc") if ts_utc is not None else _utc_now_iso(),
                _optional_text(envelope_obj.get("as_of")),
                _optional_text(envelope_obj.get("status")),
                canonical_json(envelope_obj),
                sha256_hex(envelope_obj),
            ),
        )

    def insert_decision(
        self,
        symbol: str,
        decision: str,
        blocking: Sequence[Any],
        snapshot_ids: Sequence[int],
        params_hash: str,
        decision_hash: str,
        card: Mapping[str, Any],
        *,
        ts_utc: str | None = None,
    ) -> int:
        """Simpan satu keputusan preflight + kartu panggilannya; kembalikan id baris.

        `simulated` ditulis sebagai konstanta 1 di dalam pernyataan tetap — tidak ada
        parameter yang bisa membuatnya 0.
        """
        if _as_text(decision, "decision") not in DECISIONS:
            raise StoreError(f"decision harus salah satu {sorted(DECISIONS)}, bukan {decision!r}")
        if not isinstance(card, Mapping):
            raise StoreError(f"card harus objek, bukan {type(card).__name__}")
        if isinstance(blocking, str) or not isinstance(blocking, Iterable):
            raise StoreError(f"blocking harus daftar, bukan {type(blocking).__name__}")
        if isinstance(snapshot_ids, str) or not isinstance(snapshot_ids, Iterable):
            raise StoreError(f"snapshot_ids harus daftar, bukan {type(snapshot_ids).__name__}")
        ids = [_as_int(i, "snapshot_ids") for i in snapshot_ids]
        return self._insert(
            SQL_INSERT_DECISION,
            (
                _as_text(ts_utc, "ts_utc") if ts_utc is not None else _utc_now_iso(),
                _as_text(symbol, "symbol"),
                decision,
                canonical_json(list(blocking)),
                canonical_json(ids),
                _as_text(params_hash, "params_hash"),
                _as_text(decision_hash, "decision_hash"),
                canonical_json(dict(card)),
            ),
        )

    def insert_trade_event(
        self,
        decision_id: int,
        symbol: str,
        direction: str,
        kind: str,
        price: float,
        size_usd: float,
        reason: str,
        *,
        ts_utc: str | None = None,
    ) -> int:
        """Simpan satu event practice trade (`open`/`exit`); kembalikan id baris.

        Tabel ini ADALAH buku besar yang dimaksud spec §5: posisi adalah lipatan atas
        event-nya, jadi "menutup posisi" berarti menambah baris `exit` — baris `open` yang
        sudah ada tidak pernah disentuh lagi. `simulated` konstan 1 (docs/rules-compliance.md).
        """
        if _as_text(kind, "kind") not in TRADE_EVENT_KINDS:
            raise StoreError(f"kind harus salah satu {sorted(TRADE_EVENT_KINDS)}, bukan {kind!r}")
        if _as_text(direction, "direction") not in TRADE_DIRECTIONS:
            raise StoreError(f"direction harus salah satu {sorted(TRADE_DIRECTIONS)}, bukan {direction!r}")
        return self._insert(
            SQL_INSERT_TRADE_EVENT,
            (
                _as_int(decision_id, "decision_id"),
                _as_text(symbol, "symbol"),
                direction,
                kind,
                _as_finite_float(price, "price"),
                _as_finite_float(size_usd, "size_usd"),
                _as_text(reason, "reason"),
                _as_text(ts_utc, "ts_utc") if ts_utc is not None else _utc_now_iso(),
            ),
        )

    # -- baca --
    def latest_fresh(
        self,
        tool: str,
        args_hash: str,
        max_age_min: float,
        *,
        now: datetime | None = None,
    ) -> Snapshot | None:
        """Snapshot terbaru untuk (tool, args_hash) yang umurnya ≤ `max_age_min` menit.

        Tidak ada yang cukup segar → None. Pemanggil (preflight, spec §5 "store dulu") yang
        memutuskan memanggil live; store tidak pernah mengembalikan baris basi seolah baru.
        Baris dengan `ts_utc` yang tidak terbaca dilewati (dan dicatat), bukan dianggap segar.
        """
        if "snapshots" not in self.tables:
            return None
        moment = now if now is not None else datetime.now(UTC)
        if not isinstance(moment, datetime):
            raise StoreError(f"now harus datetime, bukan {type(moment).__name__}")
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        try:
            cutoff = moment - timedelta(minutes=float(max_age_min))
        except (TypeError, ValueError, OverflowError):
            raise StoreError(f"max_age_min tidak sah: {type(max_age_min).__name__}") from None
        rows = self._select(
            f"SELECT {_SNAPSHOT_COLUMNS} FROM snapshots"
            " WHERE tool = ? AND args_hash = ? ORDER BY ts_utc DESC LIMIT ?",
            (_as_text(tool, "tool"), _as_text(args_hash, "args_hash"), FRESH_SCAN_LIMIT),
        )
        best: Snapshot | None = None
        best_ts: datetime | None = None
        for row in rows:
            snapshot = Snapshot.from_row(row)
            ts = snapshot.ts
            if ts is None:
                log.warning("snapshot %d: ts_utc tidak terbaca, dilewati", snapshot.id)
                continue
            if ts < cutoff:
                continue
            if best_ts is None or ts > best_ts:
                best, best_ts = snapshot, ts
        return best

    def recent_args(
        self,
        tool: str,
        max_age_min: float,
        *,
        min_age_min: float = 0.0,
        now: datetime | None = None,
        limit: int = RECENT_ARGS_LIMIT,
    ) -> list[dict[str, Any]]:
        """Argumen UNIK untuk `tool` dengan pembacaan berumur `[min_age_min, max_age_min]`
        menit, terbaru dulu.

        Dipakai `core.recorder` untuk dua pertanyaan yang berlawanan, tanpa menaruh SQL di
        luar modul ini: "simbol mana yang direkam dalam 26 jam terakhir" (`min_age_min=0`) dan
        "simbol mana yang SUDAH punya pembacaan berumur ≥24 jam" (`min_age_min=24*60`, yaitu
        yang kewajiban follow-up-nya selesai). Hanya kolom `args_json`/`ts_utc` yang dibaca
        (payload tidak ikut) supaya pemanggilan per siklus tetap murah.

        `min_age_min=0` berarti tanpa batas atas kesegaran: baris ber-`ts_utc` di masa depan
        (jam mesin mundur) tetap ikut, bukan hilang diam-diam.

        Baris dengan `ts_utc` tidak terbaca atau `args_json` yang bukan objek JSON DILEWATI
        dan dicatat — bukan dianggap segar, dan bukan pula mematikan siklus.
        """
        if "snapshots" not in self.tables:
            return []
        moment = now if now is not None else datetime.now(UTC)
        if not isinstance(moment, datetime):
            raise StoreError(f"now harus datetime, bukan {type(moment).__name__}")
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        try:
            cutoff = moment - timedelta(minutes=float(max_age_min))
            floor = float(min_age_min)
            newest = moment - timedelta(minutes=floor)
        except (TypeError, ValueError, OverflowError):
            raise StoreError(
                f"batas umur tidak sah: {type(max_age_min).__name__}/{type(min_age_min).__name__}"
            ) from None
        if floor < 0:
            raise StoreError(f"min_age_min tidak boleh negatif: {min_age_min}")
        row_limit = max(0, _as_int(limit, "limit"))
        rows = self._select(
            "SELECT args_json, ts_utc FROM snapshots WHERE tool = ? ORDER BY ts_utc DESC LIMIT ?",
            (_as_text(tool, "tool"), row_limit),
        )
        if len(rows) >= row_limit > 0:
            # Baris paling basi terpotong LIMIT: jawabannya masih benar untuk jendela terbaru,
            # tetapi pemanggil berhak tahu bahwa horizonnya tidak lagi dibatasi waktu.
            log.warning(
                "%s: recent_args mencapai batas %d baris; pembacaan lebih lama dari itu tidak dilihat",
                tool,
                row_limit,
            )
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for args_json, ts_utc in rows:
            ts = _parse_ts(ts_utc)
            if ts is None or ts < cutoff:
                continue
            if floor > 0 and ts > newest:
                continue
            if not isinstance(args_json, str) or args_json in seen:
                continue
            seen.add(args_json)
            try:
                args = json.loads(args_json)
            except ValueError:
                log.warning("%s: args_json tidak terbaca, dilewati", tool)
                continue
            if isinstance(args, dict):
                out.append(args)
        return out

    def get_snapshots(self, ids: Iterable[int]) -> list[Snapshot]:
        """Snapshot untuk `ids`, urut id menaik; id yang tidak ada dilewati diam-diam.

        Prasyarat `core.replay.freeze()`: payload yang keluar identik dengan yang masuk.
        """
        if isinstance(ids, str) or not isinstance(ids, Iterable):
            raise StoreError(f"ids harus daftar, bukan {type(ids).__name__}")
        wanted = [_as_int(i, "ids") for i in ids]
        if not wanted or "snapshots" not in self.tables:
            return []
        placeholders = ",".join("?" * len(wanted))
        rows = self._select(
            f"SELECT {_SNAPSHOT_COLUMNS} FROM snapshots WHERE id IN ({placeholders}) ORDER BY id",
            wanted,
        )
        return [Snapshot.from_row(row) for row in rows]
