"""Tes TASKS 1.2 — core/store.py hanya-tambah (ADR-006), skema sama dengan data/ryo.db.

Semua penulisan di berkas ini HANYA ke DB sementara (`tmp_path`). `data/ryo.db` yang hidup
dibuka `mode=ro` saja: perekam sedang menulis ke sana dan snapshotnya tidak bisa direkam ulang.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import math
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.store import (
    EXPECTED_SCHEMA,
    FRESH_SCAN_LIMIT,
    FUTURE_SKEW_TOLERANCE_S,
    SQL_INSERT_SNAPSHOT,
    Snapshot,
    Store,
    StoreError,
    StoreReadOnlyError,
    StoreSchemaError,
    canonical_json,
    sha256_hex,
)

ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = ROOT / "data" / "ryo.db"

ENVELOPE = {
    "tool": "analyze_token",
    "status": "ok",
    "data_mode": "live",
    "as_of": "2026-09-01T03:51:43Z",
    "data": {"verdict": "accumulate", "price": 203.41, "note": "naik — 上昇"},
    "availability": {"token_profile": "unavailable"},
    "warnings": [],
}
ARGS = {"symbol": "SOL"}


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "t.db") as s:
        yield s


def _rows(path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    """Baca DB sementara langsung, tanpa lewat Store."""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _live_ro() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)


# --- kanonikalisasi ----------------------------------------------------------------------
def test_canonical_json_sorts_keys_and_omits_spaces() -> None:
    assert canonical_json({"b": 1, "a": [1, {"d": None, "c": 2}]}) == '{"a":[1,{"c":2,"d":null}],"b":1}'


def test_canonical_json_keeps_non_ascii_verbatim() -> None:
    assert canonical_json({"x": "点呼"}) == '{"x":"点呼"}'


def test_canonical_json_is_stable_across_key_order() -> None:
    assert sha256_hex({"a": 1, "b": 2}) == sha256_hex({"b": 2, "a": 1})


@pytest.mark.parametrize("bad", [float("nan"), math.inf, -math.inf])
def test_canonical_json_rejects_nan_and_infinity(bad: float) -> None:
    """NaN/Infinity bukan JSON sah; hash yang tidak stabil membuat replay berbohong."""
    with pytest.raises(StoreError):
        canonical_json({"x": bad})


def test_canonical_json_rejects_unserialisable_object() -> None:
    with pytest.raises(StoreError):
        canonical_json({"x": object()})


def test_sha256_hex_labels_lone_surrogate_instead_of_leaking_unicode_error() -> None:
    """`json.loads` menerima lone surrogate; UTF-8 tidak. Harus StoreError, bukan UnicodeEncodeError."""
    payload = json.loads('{"data":{"name":"\\ud800"}}')
    with pytest.raises(StoreError):
        sha256_hex(payload)


def test_insert_snapshot_labels_lone_surrogate(store: Store) -> None:
    payload = json.loads('{"status":"ok","data":{"name":"\\ud800"}}')
    with pytest.raises(StoreError):
        store.insert_snapshot("analyze_token", ARGS, payload)
    assert _rows(store.path, "SELECT COUNT(*) FROM snapshots")[0][0] == 0


def test_insert_snapshot_labels_recursion_error(store: Store) -> None:
    deep: dict = {}
    node = deep
    for _ in range(20_000):
        node["x"] = {}
        node = node["x"]
    with pytest.raises(StoreError):
        store.insert_snapshot("analyze_token", ARGS, {"status": "ok", "data": deep})


def test_canonical_matches_bootstrap_recorder_implementation() -> None:
    """Perilaku harus identik dengan `canonical`/`sha256_hex` di scripts/bootstrap_recorder.py.

    Berkas itu dihapus di TASKS 0.5; kesamaan hash tetap dijaga oleh tes terhadap baris nyata
    `data/ryo.db` yang tidak bergantung padanya.
    """
    bootstrap = ROOT / "scripts" / "bootstrap_recorder.py"
    if not bootstrap.exists():
        pytest.skip("scripts/bootstrap_recorder.py sudah dihapus (TASKS 0.5)")
    source = bootstrap.read_text(encoding="utf-8")
    namespace: dict = {"json": json, "hashlib": hashlib}
    start = source.index("def canonical(")
    end = source.index("def load_env(")
    exec(compile(source[start:end], "bootstrap", "exec"), namespace)  # noqa: S102
    for obj in ({}, ARGS, ENVELOPE, {"z": [1.5, -0.0, "点呼"], "a": None}):
        assert namespace["canonical"](obj) == canonical_json(obj)
        assert namespace["sha256_hex"](obj) == sha256_hex(obj)


# --- AC 1: hanya-tambah, tanpa dedup -----------------------------------------------------
def test_same_payload_twice_makes_two_rows_with_equal_hashes(store: Store) -> None:
    """ADR-006: duplikat DISENGAJA — itulah yang membuat grading punya `n`."""
    first = store.insert_snapshot("analyze_token", ARGS, ENVELOPE)
    second = store.insert_snapshot("analyze_token", ARGS, ENVELOPE)
    assert first != second

    rows = _rows(store.path, "SELECT id, args_hash, payload_hash FROM snapshots ORDER BY id")
    assert len(rows) == 2
    assert rows[0][1] == rows[1][1] == sha256_hex(ARGS)
    assert rows[0][2] == rows[1][2] == sha256_hex(ENVELOPE)


def test_insert_snapshot_copies_as_of_and_status_without_defaults(store: Store) -> None:
    store.insert_snapshot("analyze_token", ARGS, ENVELOPE)
    store.insert_snapshot("deep_analysis", {"symbol": "SOL"}, {"tool": "deep_analysis", "status": "partial"})
    rows = _rows(store.path, "SELECT tool, as_of, status FROM snapshots ORDER BY id")
    assert rows[0] == ("analyze_token", ENVELOPE["as_of"], "ok")
    # envelope tanpa as_of → NULL, bukan string kosong atau nol (ADR-004)
    assert rows[1] == ("deep_analysis", None, "partial")


def test_non_string_as_of_and_status_are_stored_as_null(store: Store) -> None:
    """Kolom indeks tidak boleh berisi repr Python; payload aslinya tetap utuh di payload_json."""
    envelope = {"status": {"nested": 1}, "as_of": ["2026-09-01"], "data": None}
    row_id = store.insert_snapshot("analyze_token", ARGS, envelope)
    assert _rows(store.path, "SELECT as_of, status FROM snapshots WHERE id = ?", (row_id,)) == [(None, None)]
    assert store.get_snapshots([row_id])[0].payload == envelope


def test_insert_snapshot_rejects_non_mapping_arguments(store: Store) -> None:
    with pytest.raises(StoreError):
        store.insert_snapshot("analyze_token", ARGS, ["bukan objek"])  # type: ignore[arg-type]
    with pytest.raises(StoreError):
        store.insert_snapshot("analyze_token", "bukan objek", ENVELOPE)  # type: ignore[arg-type]
    with pytest.raises(StoreError):
        store.insert_snapshot(123, ARGS, ENVELOPE)  # type: ignore[arg-type]


# --- hanya-tambah ditegakkan runtime, bukan oleh pemindaian teks -------------------------
MUTATING_SQL = [
    "UPDATE snapshots SET tool = 'x'",
    "DELETE FROM snapshots",
    "DROP TABLE snapshots",
    "DROP INDEX ix_snap_args",
    "ALTER TABLE snapshots ADD COLUMN evil TEXT",
    "ATTACH DATABASE ':memory:' AS other",
    "CREATE TABLE evil (a TEXT)",
    "CREATE TRIGGER trg AFTER INSERT ON snapshots BEGIN DELETE FROM snapshots; END",
    "VACUUM",
    "PRAGMA writable_schema = ON",
    "PRAGMA journal_mode = WAL",
]


@pytest.mark.parametrize("sql", MUTATING_SQL)
def test_sqlite_authorizer_blocks_mutating_sql_on_write_connection(store: Store, sql: str) -> None:
    """Penjaga sesungguhnya: SQLite menolak di jalur eksekusi, apa pun teks SQL-nya."""
    store.insert_snapshot("analyze_token", ARGS, ENVELOPE)
    with pytest.raises(sqlite3.DatabaseError):
        store._db.execute(sql)
    store._db.rollback()
    assert _rows(store.path, "SELECT tool FROM snapshots") == [("analyze_token",)]


def test_authorizer_blocks_sql_assembled_at_runtime(store: Store) -> None:
    """Kata terlarang yang dirakit saat runtime pun tidak lolos (pemindaian teks akan lolos)."""
    store.insert_snapshot("analyze_token", ARGS, ENVELOPE)
    verb = "".join(["de", "lete"]).upper()
    with pytest.raises(sqlite3.DatabaseError):
        store._db.execute(f"{verb} FROM snapshots")
    store._db.rollback()
    assert _rows(store.path, "SELECT COUNT(*) FROM snapshots")[0][0] == 1


def test_insert_helper_rejects_statements_outside_the_fixed_list(store: Store) -> None:
    """Lapis kedua: `REPLACE`/`INSERT OR REPLACE` dilaporkan SQLite sebagai aksi INSERT."""
    row_id = store.insert_snapshot("analyze_token", ARGS, ENVELOPE)
    replace_sql = (
        "INSERT OR REPLACE INTO snapshots"
        " (id, tool, args_json, args_hash, ts_utc, payload_json, payload_hash)"
        " VALUES (?,?,?,?,?,?,?)"
    )
    with pytest.raises(StoreError):
        store._insert(replace_sql, (row_id, "jahat", "{}", "h", "t", "{}", "h"))
    assert _rows(store.path, "SELECT tool FROM snapshots") == [("analyze_token",)]


def test_acknowledged_limit_replace_into_through_raw_db_handle_overwrites_a_row(
    store: Store,
) -> None:
    """BATAS YANG DIAKUI (docstring modul), bukan bug yang lolos: hanya-tambah dijamin di
    batas API `Store`, bukan pada koneksi.

    `store._db` adalah handle SQLite mentah yang sengaja tidak dilindungi allowlist
    pernyataan, dan authorizer menyaring aksi (bukan teks) sehingga `REPLACE INTO`
    dilaporkan sebagai aksi INSERT. Tes ini MENGHARAPKAN serangan itu berhasil: kalau kelak
    seseorang menutup celahnya, tes ini jatuh dan pembahasannya dipaksa terjadi. Yang
    menjaga sisanya tetap `MUTATING_SQL` di atas, yang menuntut penolakan.
    """
    row_id = store.insert_snapshot("analyze_token", ARGS, ENVELOPE)
    replace_sql = (
        "REPLACE INTO snapshots"
        " (id, tool, args_json, args_hash, ts_utc, payload_json, payload_hash)"
        " VALUES (?,?,?,?,?,?,?)"
    )
    store._db.execute(replace_sql, (row_id, "DIGANTI", "{}", "h", "t", "{}", "h"))
    store._db.commit()

    rows = _rows(store.path, "SELECT id, tool, payload_json FROM snapshots")
    assert rows == [(row_id, "DIGANTI", "{}")]  # baris asli hancur, jumlah baris tetap 1


def test_journal_mode_of_the_database_file_is_left_alone(store: Store) -> None:
    """Perekam sedang menulis ke DB hidup; membalik journal_mode ke WAL bersifat persisten."""
    store.insert_snapshot("analyze_token", ARGS, ENVELOPE)
    assert _rows(store.path, "PRAGMA journal_mode")[0][0].lower() == "delete"


def test_no_executable_string_literal_contains_a_mutating_verb() -> None:
    """Lapis ketiga (lemah, sengaja): literal SQL di sumber diperiksa lewat AST, bukan grep polos.

    Docstring dikecualikan — di sanalah `INSERT OR REPLACE` dan `writable_schema` memang perlu
    disebut untuk menjelaskan apa yang ditutup. Yang dilarang adalah string yang bisa dieksekusi.
    """
    source = (ROOT / "core" / "store.py").read_text(encoding="utf-8")
    assert re.search(r"\b(UPDATE|DELETE)\b", source, re.IGNORECASE) is None  # sama dengan gate.sh

    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            text = ast.get_docstring(node, clean=False)
            if text is not None:
                docstrings.add(text)
    mutating = re.compile(r"\b(UPDATE|DELETE|REPLACE|VACUUM|ATTACH|DETACH|TRUNCATE|writable_schema)\b", re.I)
    offenders = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
        and mutating.search(node.value)
    ]
    assert offenders == []
    assert source.count("executescript") == 1  # hanya di _create_schema, koneksi berumur pendek


# --- AC 2: latest_fresh --------------------------------------------------------------------
def test_latest_fresh_returns_none_when_every_row_is_older_than_limit(store: Store) -> None:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    for minutes in (30, 60, 120):
        store.insert_snapshot(
            "analyze_token", ARGS, ENVELOPE, ts_utc=(now - timedelta(minutes=minutes)).isoformat()
        )
    assert store.latest_fresh("analyze_token", sha256_hex(ARGS), 15, now=now) is None


def test_latest_fresh_returns_newest_row_within_limit(store: Store) -> None:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    ids = {}
    for minutes in (60, 10, 3):
        ids[minutes] = store.insert_snapshot(
            "analyze_token", ARGS, ENVELOPE, ts_utc=(now - timedelta(minutes=minutes)).isoformat()
        )
    fresh = store.latest_fresh("analyze_token", sha256_hex(ARGS), 15, now=now)
    assert fresh is not None and fresh.id == ids[3]
    assert fresh.payload == ENVELOPE


def test_latest_fresh_is_scoped_to_tool_and_args(store: Store) -> None:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    store.insert_snapshot("analyze_token", {"symbol": "BTC"}, ENVELOPE, ts_utc=now.isoformat())
    store.insert_snapshot("deep_analysis", ARGS, ENVELOPE, ts_utc=now.isoformat())
    assert store.latest_fresh("analyze_token", sha256_hex(ARGS), 15, now=now) is None


def test_latest_fresh_skips_unparsable_timestamp(store: Store) -> None:
    """`ts_utc` tak terbaca → dilewati (tidak segar), bukan dianggap baru."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    store.insert_snapshot("analyze_token", ARGS, ENVELOPE, ts_utc="entah-kapan")
    assert store.latest_fresh("analyze_token", sha256_hex(ARGS), 15, now=now) is None
    good = store.insert_snapshot(
        "analyze_token", ARGS, ENVELOPE, ts_utc=(now - timedelta(minutes=1)).isoformat()
    )
    fresh = store.latest_fresh("analyze_token", sha256_hex(ARGS), 15, now=now)
    assert fresh is not None and fresh.id == good


def test_latest_fresh_accepts_z_suffix_and_naive_now(store: Store) -> None:
    store.insert_snapshot("analyze_token", ARGS, ENVELOPE, ts_utc="2026-09-01T11:59:00Z")
    fresh = store.latest_fresh(
        "analyze_token",
        sha256_hex(ARGS),
        15,
        now=datetime(2026, 9, 1, 12, 0),  # noqa: DTZ001
    )
    assert fresh is not None


@pytest.mark.parametrize("bad", ["lima belas", None, float("nan")])
def test_latest_fresh_labels_bad_arguments(store: Store, bad: object) -> None:
    with pytest.raises(StoreError):
        store.latest_fresh("analyze_token", sha256_hex(ARGS), bad)  # type: ignore[arg-type]


# --- TASKS 2.0d: batas ATAS kesegaran (toleransi jam) ---------------------------------------
def test_latest_fresh_ignores_a_row_dated_ten_years_ahead(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """Baris +10 tahun tidak boleh menang `ORDER BY ts_utc DESC` atas baris sah."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    legit = store.insert_snapshot(
        "analyze_token", ARGS, ENVELOPE, ts_utc=(now - timedelta(minutes=5)).isoformat()
    )
    ahead = store.insert_snapshot(
        "analyze_token", ARGS, ENVELOPE, ts_utc=(now + timedelta(days=3653)).isoformat()
    )
    with caplog.at_level(logging.WARNING, logger="core.store"):
        fresh = store.latest_fresh("analyze_token", sha256_hex(ARGS), 15, now=now)
    assert fresh is not None
    assert fresh.id == legit
    warnings = [r for r in caplog.records if f"snapshot_id={ahead}" in r.getMessage()]
    assert len(warnings) == 1, caplog.text
    assert "di depan now" in warnings[0].getMessage()
    assert "315" in warnings[0].getMessage()  # selisih ~3,156e8 dtk ikut disebut


def test_latest_fresh_returns_none_when_the_only_row_is_in_the_future(store: Store) -> None:
    """Tidak ada bukti sah → None yang jujur, bukan baris masa depan."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    store.insert_snapshot(
        "analyze_token", ARGS, ENVELOPE, ts_utc=(now + timedelta(days=3653)).isoformat()
    )
    assert store.latest_fresh("analyze_token", sha256_hex(ARGS), 15, now=now) is None


@pytest.mark.parametrize(("ahead_s", "accepted"), [(119, True), (121, False)])
def test_latest_fresh_pins_the_future_skew_tolerance(
    store: Store, ahead_s: int, accepted: bool
) -> None:
    """Toleransi JAM, bukan kelonggaran kesegaran: 119 dtk lolos, 121 dtk ditolak."""
    assert FUTURE_SKEW_TOLERANCE_S == 120
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    made = store.insert_snapshot(
        "analyze_token", ARGS, ENVELOPE, ts_utc=(now + timedelta(seconds=ahead_s)).isoformat()
    )
    fresh = store.latest_fresh("analyze_token", sha256_hex(ARGS), 15, now=now)
    assert (fresh is not None and fresh.id == made) is accepted


def test_acknowledged_limit_future_rows_can_push_a_valid_row_out_of_the_scan(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """Batas yang diakui: >`FRESH_SCAN_LIMIT` baris masa depan menyembunyikan baris sah.

    Hasilnya tetap JUJUR (`None` = "tidak ada yang segar", bukan bukti palsu) dan berisik
    (satu warning per baris yang dilewati), tetapi baris sahnya memang tidak terlihat.
    """
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    legit = store.insert_snapshot(
        "analyze_token", ARGS, ENVELOPE, ts_utc=(now - timedelta(minutes=5)).isoformat()
    )
    for i in range(FRESH_SCAN_LIMIT + 1):
        store.insert_snapshot(
            "analyze_token", ARGS, ENVELOPE, ts_utc=(now + timedelta(days=3653 + i)).isoformat()
        )
    with caplog.at_level(logging.WARNING, logger="core.store"):
        assert store.latest_fresh("analyze_token", sha256_hex(ARGS), 15, now=now) is None
    assert len([r for r in caplog.records if "di depan now" in r.getMessage()]) == FRESH_SCAN_LIMIT
    assert store.get_snapshots([legit])[0].id == legit  # barisnya ada, hanya tidak terpindai


def test_latest_fresh_future_guard_needs_no_env_var() -> None:
    """Nilainya properti jam mesin, bukan tombol kebijakan (2.0d: tanpa env var baru).

    Konstantanya harus literal di sumber: env var yang bisa dinaikkan ke tak hingga akan
    membatalkan guardnya justru pada mesin yang jamnya rusak.
    """
    tree = ast.parse((ROOT / "core" / "store.py").read_text(encoding="utf-8"))
    assigned = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "FUTURE_SKEW_TOLERANCE_S" for t in node.targets
        )
    ]
    assert len(assigned) == 1
    assert isinstance(assigned[0], ast.Constant) and assigned[0].value == 120

    # Satu-satunya nama env di modul ini tetap DB_PATH (0.4); tidak ada tombol skew baru.
    env_consts = sorted(
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id.startswith("ENV_")
    )
    assert env_consts == ["ENV_DB_PATH"]


def test_latest_fresh_future_guard_is_a_noop_for_the_live_database() -> None:
    """0 baris `ts_utc > now` di data/ryo.db → guard tidak menggeser keputusan hari ini."""
    if not LIVE_DB.exists():
        pytest.skip("data/ryo.db tidak ada")
    conn = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        horizon = (datetime.now(UTC) + timedelta(seconds=FUTURE_SKEW_TOLERANCE_S)).isoformat()
        (ahead,) = conn.execute(
            "SELECT count(*) FROM snapshots WHERE ts_utc > ?", (horizon,)
        ).fetchone()
    finally:
        conn.close()
    assert ahead == 0


# --- recent_args (prasyarat follow-up perekam, TASKS 0.5) -----------------------------------
def _seed_ages(store: Store, ages_h: dict[str, float]) -> None:
    """Satu baris analyze_token per simbol dengan umur tertentu."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    for symbol, hours in ages_h.items():
        store.insert_snapshot(
            "analyze_token",
            {"symbol": symbol},
            ENVELOPE,
            ts_utc=(now - timedelta(hours=hours)).isoformat(),
        )


def test_recent_args_keeps_rows_inside_the_window_only(store: Store) -> None:
    _seed_ages(store, {"OLD25": 25.0, "OLD27": 27.0, "NEW": 1.0})
    got = store.recent_args("analyze_token", 26 * 60, now=datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    assert [a["symbol"] for a in got] == ["NEW", "OLD25"]  # terbaru dulu


def test_recent_args_deduplicates_identical_arguments(store: Store) -> None:
    _seed_ages(store, {"SOL": 1.0})
    store.insert_snapshot("analyze_token", {"symbol": "SOL"}, ENVELOPE)
    got = store.recent_args("analyze_token", 26 * 60, now=datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    assert got == [{"symbol": "SOL"}]


def test_recent_args_is_scoped_to_one_tool(store: Store) -> None:
    _seed_ages(store, {"SOL": 1.0})
    store.insert_snapshot("deep_analysis", {"symbol": "BTC", "include_perp": True}, ENVELOPE)
    got = store.recent_args("analyze_token", 26 * 60, now=datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    assert got == [{"symbol": "SOL"}]


def test_recent_args_skips_unparsable_timestamp(store: Store) -> None:
    store.insert_snapshot("analyze_token", ARGS, ENVELOPE, ts_utc="kemarin")
    assert store.recent_args("analyze_token", 26 * 60) == []


def test_recent_args_honours_the_row_limit(store: Store) -> None:
    _seed_ages(store, {"A": 1.0, "B": 2.0, "C": 3.0})
    got = store.recent_args("analyze_token", 26 * 60, now=datetime(2026, 9, 1, 12, 0, tzinfo=UTC), limit=2)
    assert [a["symbol"] for a in got] == ["A", "B"]


def test_recent_args_returns_empty_for_unknown_tool(store: Store) -> None:
    _seed_ages(store, {"SOL": 1.0})
    assert store.recent_args("market_overview", 26 * 60) == []


def test_recent_args_min_age_selects_only_old_enough_rows(store: Store) -> None:
    """`min_age_min` menjawab "sudah punya pembacaan LAMA?" — dasar pensiun follow-up 0.5."""
    _seed_ages(store, {"OLD25": 25.0, "OLD30": 30.0, "NEW": 1.0})
    got = store.recent_args(
        "analyze_token", 7 * 24 * 60, min_age_min=24 * 60, now=datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    )
    assert [a["symbol"] for a in got] == ["OLD25", "OLD30"]


def test_recent_args_empty_when_min_age_exceeds_max_age(store: Store) -> None:
    _seed_ages(store, {"SOL": 1.0})
    assert store.recent_args("analyze_token", 60, min_age_min=120) == []


def test_recent_args_keeps_future_timestamps_when_min_age_is_zero(store: Store) -> None:
    """Jam mesin mundur → baris "masa depan" tidak boleh hilang diam-diam dari jendela."""
    _seed_ages(store, {"AHEAD": -2.0})
    assert store.recent_args("analyze_token", 26 * 60, now=datetime(2026, 9, 1, 12, 0, tzinfo=UTC)) == [
        {"symbol": "AHEAD"}
    ]


def test_recent_args_warns_but_keeps_a_far_future_row(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """Pilihan yang dikunci 2.0d: di sini baris masa depan TETAP ikut, tapi tidak diam.

    Membuangnya di sini justru berbahaya: `recent_args` memberi tahu perekam simbol mana yang
    SUDAH direkam, jadi baris masa depan menghentikan perekaman simbol itu. Jam yang melompat
    tidak boleh membungkam pengumpulan data tanpa jejak.
    """
    _seed_ages(store, {"AHEAD": -24.0, "NOW": 1.0})
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    with caplog.at_level(logging.WARNING, logger="core.store"):
        got = store.recent_args("analyze_token", 26 * 60, now=now)
    assert [a["symbol"] for a in got] == ["AHEAD", "NOW"]  # perilaku TIDAK berubah
    ahead = [r for r in caplog.records if "di depan now" in r.getMessage()]
    assert len(ahead) == 1, caplog.text
    assert "tetap dihitung" in ahead[0].getMessage()


def test_recent_args_stays_quiet_inside_the_clock_tolerance(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """Skew jam biasa (p95 nyata ~1 dtk) tidak boleh membanjiri log."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    store.insert_snapshot(
        "analyze_token",
        {"symbol": "SOL"},
        ENVELOPE,
        ts_utc=(now + timedelta(seconds=FUTURE_SKEW_TOLERANCE_S - 1)).isoformat(),
    )
    with caplog.at_level(logging.WARNING, logger="core.store"):
        assert store.recent_args("analyze_token", 26 * 60, now=now) == [{"symbol": "SOL"}]
    assert "di depan now" not in caplog.text


def _followup_like(
    store: Store, now: datetime, *, drop_future_in_window: bool, drop_future_fresh: bool
) -> list[str]:
    """Uji pensiun perekam (`core/recorder.py::_followup_symbols`) dalam bentuk minimal.

    Dipakai untuk menjalankan TIGA kebijakan atas baris masa depan pada data yang sama; itulah
    yang membuat tabel A/B TASKS 2.0f bisa dipaku sel per sel di satu tempat.
    """
    in_window = store.recent_args(
        "analyze_token", 26 * 60, now=now, drop_future=drop_future_in_window
    )
    fresh = store.recent_args("analyze_token", 24 * 60, now=now, drop_future=drop_future_fresh)
    settled = store.recent_args("analyze_token", 26 * 60, min_age_min=24 * 60, now=now)
    done = {a["symbol"] for a in fresh} & {a["symbol"] for a in settled}
    return [a["symbol"] for a in in_window if a["symbol"] not in done]


#: Tiga kebijakan tabel TASKS 2.0f → (drop_future_in_window, drop_future_fresh).
_POLICIES = {
    "ikut semua": (False, False),
    "buang di fresh": (False, True),
    "buang di semua": (True, True),
}


@pytest.mark.parametrize(
    ("case", "policy", "expected"),
    [
        # A: baris 25 j (`settled`) + baris masa depan (`fresh`)
        ("A", "ikut semua", []),  # <- data follow-up HILANG: SOL pensiun sebelum waktunya
        ("A", "buang di fresh", ["SOL"]),
        ("A", "buang di semua", ["SOL"]),
        # B: HANYA baris masa depan
        ("B", "ikut semua", ["SOL"]),
        ("B", "buang di fresh", ["SOL"]),
        ("B", "buang di semua", []),  # <- rugi: simbolnya lenyap dari jendela follow-up
    ],
)
def test_recent_args_future_row_policy_table(
    store: Store, case: str, policy: str, expected: list[str]
) -> None:
    """Tabel reviewer TASKS 2.0f, keenam selnya dipaku: "buang di `fresh`" menang di A DAN B.

    Menggantikan pengakuan 2.0d `..._a_future_row_can_retire_a_symbol_from_follow_up`, yang
    MENGHARAPKAN `fresh & settled == {"SOL"}` (baris "A / ikut semua" di bawah, kini dipaku
    sebagai kerugian sebuah kebijakan yang TIDAK lagi dipakai perekam).
    """
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    if case == "A":
        _seed_ages(store, {"SOL": 25.0})
    store.insert_snapshot(
        "analyze_token", {"symbol": "SOL"}, ENVELOPE, ts_utc=(now + timedelta(hours=3)).isoformat()
    )
    in_window_flag, fresh_flag = _POLICIES[policy]
    got = _followup_like(
        store, now, drop_future_in_window=in_window_flag, drop_future_fresh=fresh_flag
    )
    assert got == expected


def test_recent_args_drop_future_empties_the_retirement_set(store: Store) -> None:
    """Kebalikan langsung dari pengakuan 2.0d: irisan pensiun kini KOSONG (TASKS 2.0f).

    Sub-kasus A reviewer: satu baris SOL berumur 25 jam (`settled`) + satu baris SOL masa depan.
    Dengan `drop_future=True` baris masa depan tidak lagi masuk `fresh`, jadi SOL tidak pensiun
    dan pasangan outcome T+24 jam masih bisa dilengkapi.
    """
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    _seed_ages(store, {"SOL": 25.0})
    store.insert_snapshot(
        "analyze_token", {"symbol": "SOL"}, ENVELOPE, ts_utc=(now + timedelta(hours=3)).isoformat()
    )
    fresh = {
        a["symbol"]
        for a in store.recent_args("analyze_token", 24 * 60, now=now, drop_future=True)
    }
    settled = {
        a["symbol"]
        for a in store.recent_args("analyze_token", 26 * 60, min_age_min=24 * 60, now=now)
    }
    assert fresh & settled == set()


def test_recent_args_drop_future_keeps_rows_inside_the_tolerance(store: Store) -> None:
    """Yang dibuang hanya yang melewati toleransi JAM, bukan setiap `ts_utc > now`."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    store.insert_snapshot(
        "analyze_token",
        {"symbol": "SOL"},
        ENVELOPE,
        ts_utc=(now + timedelta(seconds=FUTURE_SKEW_TOLERANCE_S - 1)).isoformat(),
    )
    got = store.recent_args("analyze_token", 26 * 60, now=now, drop_future=True)
    assert got == [{"symbol": "SOL"}]


def test_recent_args_drop_future_still_warns_once(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """Membuang baris tidak boleh menghilangkan jejaknya: satu warning per baris, tetap ada."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    _seed_ages(store, {"NOW": 1.0})
    store.insert_snapshot(
        "analyze_token", {"symbol": "AHEAD"}, ENVELOPE, ts_utc=(now + timedelta(hours=3)).isoformat()
    )
    with caplog.at_level(logging.WARNING, logger="core.store"):
        got = store.recent_args("analyze_token", 26 * 60, now=now, drop_future=True)
    assert [a["symbol"] for a in got] == ["NOW"]
    ahead = [r for r in caplog.records if "di depan now" in r.getMessage()]
    assert len(ahead) == 1, caplog.text
    assert "dilewati" in ahead[0].getMessage()


def test_recent_args_logs_a_parsed_timestamp_not_the_raw_column(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """`_parse_ts` melakukan strip(), jadi kolom mentah bisa menyuntik newline ke log."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    store.insert_snapshot(
        "analyze_token",
        {"symbol": "SOL"},
        ENVELOPE,
        ts_utc="\n\n" + (now + timedelta(hours=3)).isoformat() + "\n\n",
    )
    with caplog.at_level(logging.WARNING, logger="core.store"):
        store.recent_args("analyze_token", 26 * 60, now=now)
    ahead = [r for r in caplog.records if "di depan now" in r.getMessage()]
    assert len(ahead) == 1
    assert "\n" not in ahead[0].getMessage()


def test_latest_fresh_logs_a_parsed_timestamp_not_the_raw_column(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    store.insert_snapshot(
        "analyze_token", ARGS, ENVELOPE, ts_utc="\n\n" + (now + timedelta(days=3653)).isoformat()
    )
    with caplog.at_level(logging.WARNING, logger="core.store"):
        assert store.latest_fresh("analyze_token", sha256_hex(ARGS), 15, now=now) is None
    ahead = [r for r in caplog.records if "di depan now" in r.getMessage()]
    assert len(ahead) == 1
    assert "\n" not in ahead[0].getMessage()


@pytest.mark.parametrize("method", ["latest_fresh", "recent_args"])
def test_future_horizon_overflow_stays_a_store_error(store: Store, method: str) -> None:
    """`now` dekat datetime.max: klaim modul "hanya StoreError" berlaku juga untuk horizon."""
    brink = datetime.max.replace(tzinfo=UTC) - timedelta(seconds=1)
    with pytest.raises(StoreError):
        if method == "latest_fresh":
            store.latest_fresh("analyze_token", sha256_hex(ARGS), 15, now=brink)
        else:
            store.recent_args("analyze_token", 26 * 60, now=brink)


def test_recent_args_rejects_negative_min_age(store: Store) -> None:
    with pytest.raises(StoreError):
        store.recent_args("analyze_token", 60, min_age_min=-1)


def test_recent_args_warns_when_the_row_limit_is_reached(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """Batas LIMIT memotong baris paling basi; pemanggil berhak tahu horizonnya terpotong."""
    _seed_ages(store, {"A": 1.0, "B": 2.0})
    with caplog.at_level(logging.WARNING, logger="core.store"):
        store.recent_args("analyze_token", 26 * 60, limit=2)
    assert "batas 2 baris" in caplog.text


@pytest.mark.parametrize("bad", ["lama", None, float("nan")])
def test_recent_args_labels_bad_arguments(store: Store, bad: object) -> None:
    with pytest.raises(StoreError):
        store.recent_args("analyze_token", bad)  # type: ignore[arg-type]


# --- AC 3: get_snapshots -------------------------------------------------------------------
def test_get_snapshots_round_trips_payload_and_args(store: Store) -> None:
    """Prasyarat freeze() di TASKS 1.4: apa yang masuk = apa yang keluar."""
    ids = [
        store.insert_snapshot("analyze_token", ARGS, ENVELOPE),
        store.insert_snapshot("market_overview", {}, {"tool": "market_overview", "status": "ok"}),
    ]
    got = store.get_snapshots(reversed(ids))
    assert [s.id for s in got] == ids  # selalu urut id, apa pun urutan permintaan
    assert got[0].payload == ENVELOPE
    assert got[0].args == ARGS
    assert got[0].payload_hash == sha256_hex(ENVELOPE)
    assert got[1].payload == {"tool": "market_overview", "status": "ok"}


def test_get_snapshots_ignores_unknown_ids_and_empty_input(store: Store) -> None:
    kept = store.insert_snapshot("analyze_token", ARGS, ENVELOPE)
    assert [s.id for s in store.get_snapshots([kept, 9999])] == [kept]
    assert store.get_snapshots([]) == []


@pytest.mark.parametrize("bad_ids", [["1 OR 1=1"], [None], [{}], [2**63], [1.5], "12", 7])
def test_get_snapshots_labels_bad_ids(store: Store, bad_ids: object) -> None:
    with pytest.raises(StoreError):
        store.get_snapshots(bad_ids)  # type: ignore[arg-type]


def test_snapshot_row_is_immutable() -> None:
    snap = Snapshot.from_row((1, "t", "{}", "h", "2026-09-01T00:00:00+00:00", None, None, "{}", "h"))
    with pytest.raises(Exception):  # noqa: B017 — dataclass frozen
        snap.id = 2  # type: ignore[misc]


# --- AC 4: guard skema ---------------------------------------------------------------------
def test_guard_rejects_existing_table_with_different_columns(tmp_path: Path) -> None:
    path = tmp_path / "beda.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, tool TEXT, payload TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(StoreSchemaError) as excinfo:
        Store(path)
    message = str(excinfo.value)
    assert "snapshots" in message
    assert "payload_json" in message  # kolom yang hilang disebut namanya
    assert "payload" in message

    # dan tidak ada penulisan diam-diam: bentuk tabel lama tetap apa adanya, tetap kosong
    cols = {r[1] for r in _rows(path, "PRAGMA table_info(snapshots)")}
    assert cols == {"id", "tool", "payload"}
    assert _rows(path, "SELECT COUNT(*) FROM snapshots")[0][0] == 0


def test_guard_rejects_right_names_with_wrong_column_types(tmp_path: Path) -> None:
    """`id TEXT` menerima baris ber-id NULL yang tidak bisa diambil lagi → replay rusak diam-diam."""
    path = tmp_path / "tipe.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE snapshots (id TEXT, tool TEXT NOT NULL, args_json TEXT NOT NULL,"
        " args_hash TEXT NOT NULL, ts_utc TEXT NOT NULL, as_of TEXT, status TEXT,"
        " payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()
    with pytest.raises(StoreSchemaError) as excinfo:
        Store(path)
    assert "id" in str(excinfo.value)
    assert _rows(path, "SELECT COUNT(*) FROM snapshots")[0][0] == 0


def test_guard_rejects_dropped_not_null(tmp_path: Path) -> None:
    path = tmp_path / "notnull.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE trade_events (id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id INTEGER,"
        " symbol TEXT NOT NULL, direction TEXT NOT NULL, kind TEXT NOT NULL, price REAL NOT NULL,"
        " size_usd REAL NOT NULL, reason TEXT NOT NULL, ts_utc TEXT NOT NULL,"
        " simulated INTEGER NOT NULL DEFAULT 1)"
    )
    conn.commit()
    conn.close()
    with pytest.raises(StoreSchemaError):
        Store(path)


def test_guard_rejects_mismatch_in_read_only_mode_too(tmp_path: Path) -> None:
    path = tmp_path / "beda-ro.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE trade_events (id INTEGER PRIMARY KEY, symbol TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(StoreSchemaError):
        Store(path, read_only=True)


def test_schema_sql_produces_exactly_the_expected_shape(tmp_path: Path) -> None:
    """SCHEMA_SQL dan EXPECTED_SCHEMA tidak boleh berpisah jalan."""
    path = tmp_path / "bentuk.db"
    with Store(path):
        pass
    for table, expected in EXPECTED_SCHEMA.items():
        found = {
            (r[1], str(r[2]).upper(), int(r[3]), int(r[5]))
            for r in _rows(path, f"PRAGMA table_info({table})")
        }
        assert found == expected, table


def test_guard_accepts_freshly_created_schema(tmp_path: Path) -> None:
    path = tmp_path / "baru.db"
    with Store(path) as first:
        first.insert_snapshot("analyze_token", ARGS, ENVELOPE)
    with Store(path) as again:  # membuka ulang DB yang sudah ada harus lolos guard
        assert len(again.get_snapshots([1])) == 1


def test_partially_created_database_is_completed(tmp_path: Path) -> None:
    path = tmp_path / "separuh.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, tool TEXT NOT NULL,"
        " args_json TEXT NOT NULL, args_hash TEXT NOT NULL, ts_utc TEXT NOT NULL, as_of TEXT,"
        " status TEXT, payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL);"
    )
    conn.commit()
    conn.close()
    with Store(path) as s:
        assert s.tables == frozenset(EXPECTED_SCHEMA)
        s.insert_decision("SOL", "GO", [], [], "p", "d", {})


def test_open_read_only_missing_file_raises_store_error(tmp_path: Path) -> None:
    with pytest.raises(StoreError):
        Store(tmp_path / "tidak-ada.db", read_only=True)


# --- path & argumen konstruktor -------------------------------------------------------------
@pytest.mark.parametrize(
    "bad",
    ["file:/x/ryo.db?mode=rwc", "file:data/ryo.db", ":memory:", "file::memory:?cache=shared", "  "],
)
def test_uri_like_and_in_memory_paths_are_refused(bad: str) -> None:
    """Salah ketik DB_PATH tidak boleh diam-diam memulai DB kosong di direktori bernama `file:`."""
    with pytest.raises(StoreError):
        Store(bad)
    assert not Path("file:").exists()


def test_bad_busy_timeout_is_labelled_and_leaks_no_handle(tmp_path: Path) -> None:
    with pytest.raises(StoreError):
        Store(tmp_path / "bt.db", busy_timeout_ms="lima detik")  # type: ignore[arg-type]
    assert not (tmp_path / "bt.db").exists()  # ditolak sebelum berkas dibuat


def test_db_path_env_is_used_when_no_argument(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "sub" / "env.db"
    monkeypatch.setenv("DB_PATH", str(target))
    with Store() as s:
        assert s.path == target
    assert target.exists()


# --- mode baca-saja ------------------------------------------------------------------------
def test_read_only_store_refuses_write_paths(tmp_path: Path) -> None:
    path = tmp_path / "ro.db"
    with Store(path) as writer:
        writer.insert_snapshot("analyze_token", ARGS, ENVELOPE)
    with Store(path, read_only=True) as reader:
        assert reader.read_only is True
        with pytest.raises(StoreReadOnlyError):
            reader.insert_snapshot("analyze_token", ARGS, ENVELOPE)
        with pytest.raises(StoreReadOnlyError):
            reader.insert_decision("SOL", "GO", [], [1], "p", "d", {})
        with pytest.raises(StoreReadOnlyError):
            reader.insert_trade_event(1, "SOL", "long", "open", 1.0, 100.0, "plan")
        assert len(reader.get_snapshots([1])) == 1
    assert _rows(path, "SELECT COUNT(*) FROM snapshots")[0][0] == 1


def test_read_only_connection_is_enforced_by_sqlite_as_well(tmp_path: Path) -> None:
    """Lapis kedua: bukan hanya cek di Python — koneksinya sendiri mode=ro (tutup TOCTOU)."""
    path = tmp_path / "ro2.db"
    with Store(path) as writer:
        writer.insert_snapshot("analyze_token", ARGS, ENVELOPE)
    with Store(path, read_only=True) as reader, pytest.raises(sqlite3.Error):
        reader._db.execute(SQL_INSERT_SNAPSHOT, ("x", "{}", "h", "t", None, None, "{}", "h"))
    assert _rows(path, "SELECT COUNT(*) FROM snapshots")[0][0] == 1


def test_repr_shows_path_and_mode(tmp_path: Path) -> None:
    with Store(tmp_path / "r.db") as s:
        assert "mode='rw'" in repr(s)


# --- decisions & trade_events ---------------------------------------------------------------
def test_insert_decision_always_marks_simulated(store: Store) -> None:
    snap = store.insert_snapshot("analyze_token", ARGS, ENVELOPE)
    row_id = store.insert_decision(
        "SOL", "GO", [], [snap], "params-hash", "decision-hash", {"headline": "berangkat"}
    )
    rows = _rows(
        store.path,
        "SELECT symbol, decision, blocking_json, snapshot_ids_json, simulated FROM decisions WHERE id = ?",
        (row_id,),
    )
    assert rows == [("SOL", "GO", "[]", f"[{snap}]", 1)]


@pytest.mark.parametrize("bad", ["MAYBE", ["GO"], None, {"decision": "GO"}, 1])
def test_insert_decision_labels_bad_verdict(store: Store, bad: object) -> None:
    with pytest.raises(StoreError):
        store.insert_decision("SOL", bad, [], [], "p", "d", {})  # type: ignore[arg-type]


def test_insert_decision_labels_bad_collections(store: Store) -> None:
    with pytest.raises(StoreError):
        store.insert_decision("SOL", "GO", [], [None], "p", "d", {})  # type: ignore[list-item]
    with pytest.raises(StoreError):
        store.insert_decision("SOL", "GO", "bukan daftar", [], "p", "d", {})  # type: ignore[arg-type]
    with pytest.raises(StoreError):
        store.insert_decision("SOL", "GO", [], [], "p", "d", "bukan objek")  # type: ignore[arg-type]


def test_insert_trade_event_always_marks_simulated(store: Store) -> None:
    decision_id = store.insert_decision("SOL", "GO", [], [], "p", "d", {})
    open_id = store.insert_trade_event(decision_id, "SOL", "long", "open", 203.4, 100.0, "plan ATR")
    exit_id = store.insert_trade_event(decision_id, "SOL", "long", "exit", 210.0, 100.0, "target")
    rows = _rows(store.path, "SELECT id, kind, simulated FROM trade_events ORDER BY id")
    # posisi = lipatan event: menutup posisi berarti MENAMBAH baris, yang lama tidak disentuh
    assert rows == [(open_id, "open", 1), (exit_id, "exit", 1)]


@pytest.mark.parametrize(
    ("direction", "kind"),
    [("long", "close"), ("sideways", "open"), ("LONG", "open"), ("long", None), (["long"], "open")],
)
def test_insert_trade_event_rejects_unknown_vocabulary(store: Store, direction: object, kind: object) -> None:
    decision_id = store.insert_decision("SOL", "GO", [], [], "p", "d", {})
    with pytest.raises(StoreError):
        store.insert_trade_event(decision_id, "SOL", direction, kind, 1.0, 1.0, "x")  # type: ignore[arg-type]


@pytest.mark.parametrize("price", [float("nan"), math.inf, "mahal", None])
def test_insert_trade_event_rejects_non_finite_price(store: Store, price: object) -> None:
    decision_id = store.insert_decision("SOL", "GO", [], [], "p", "d", {})
    with pytest.raises(StoreError):
        store.insert_trade_event(decision_id, "SOL", "long", "open", price, 1.0, "x")  # type: ignore[arg-type]


def test_insert_trade_event_rejects_out_of_range_decision_id(store: Store) -> None:
    with pytest.raises(StoreError):
        store.insert_trade_event(2**63, "SOL", "long", "open", 1.0, 1.0, "x")


def test_no_write_path_can_set_simulated_to_zero() -> None:
    """`simulated` adalah literal di dalam pernyataan tetap, bukan parameter."""
    source = (ROOT / "core" / "store.py").read_text(encoding="utf-8")
    assert "simulated=" not in source.replace(" ", "")
    assert source.count('" VALUES (?,?,?,?,?,?,?,?,1)"') == 2


# --- data/ryo.db yang hidup (baca-saja; dilewati bila berkasnya tidak ada) -------------------
requires_live_db = pytest.mark.skipif(not LIVE_DB.exists(), reason="data/ryo.db belum ada di mesin ini")


@requires_live_db
def test_guard_passes_on_live_database_read_only() -> None:
    """Bukti skema kita = skema yang hidup. HANYA baca; perekam sedang menulis ke berkas ini."""
    with Store(LIVE_DB, read_only=True) as live:
        assert live.tables == frozenset(EXPECTED_SCHEMA)
        with pytest.raises(StoreReadOnlyError):
            live.insert_snapshot("analyze_token", ARGS, ENVELOPE)


@requires_live_db
def test_live_rows_reproduce_our_hashes() -> None:
    """Hash baris lama (ditulis bootstrap_recorder) dihitung ulang dengan fungsi kita → sama."""
    conn = _live_ro()
    try:
        rows = conn.execute(
            "SELECT id, args_json, args_hash, payload_json, payload_hash FROM snapshots ORDER BY id LIMIT 25"
        ).fetchall()
    finally:
        conn.close()
    assert rows, "data/ryo.db ada tetapi tabel snapshots kosong"
    for row_id, args_json, args_hash, payload_json, payload_hash in rows:
        args = json.loads(args_json)
        payload = json.loads(payload_json)
        assert canonical_json(args) == args_json, f"baris {row_id}: bentuk args berbeda"
        assert canonical_json(payload) == payload_json, f"baris {row_id}: bentuk payload berbeda"
        assert sha256_hex(args) == args_hash, f"baris {row_id}: args_hash berbeda"
        assert sha256_hex(payload) == payload_hash, f"baris {row_id}: payload_hash berbeda"


@requires_live_db
def test_live_rows_readable_through_get_snapshots() -> None:
    conn = _live_ro()
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM snapshots ORDER BY id LIMIT 3")]
    finally:
        conn.close()
    with Store(LIVE_DB, read_only=True) as live:
        got = live.get_snapshots(ids)
    assert [s.id for s in got] == ids
    assert all(isinstance(s.payload, dict) and "status" in s.payload for s in got)


# --- scripts/db_status.py tetap jalan atas DB yang KITA tulis --------------------------------
def test_db_status_script_reads_our_schema(tmp_path: Path) -> None:
    path = tmp_path / "status.db"
    with Store(path) as s:
        s.insert_snapshot("analyze_token", ARGS, ENVELOPE)
        decision_id = s.insert_decision("SOL", "GO", [], [1], "p", "d", {})
        s.insert_trade_event(decision_id, "SOL", "long", "open", 1.0, 1.0, "plan")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "db_status.py")],
        capture_output=True,
        text=True,
        env={"DB_PATH": str(path), "PATH": "/usr/bin:/bin"},
        cwd=str(ROOT),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "snapshots: 1 {'analyze_token': 1}" in proc.stdout
    assert "keputusan: 1" in proc.stdout
    assert "trade event: 1" in proc.stdout
