"""Tes `agent/ledger.py` — koneksi buku besar HARUS benar-benar tidak bisa menulis.

Berkas ini adalah pembayaran dua temuan security-reviewer 4 Sep 2026 yang keduanya dibuktikan
dengan tulisan/penutupan yang BERHASIL, bukan dengan dugaan:

* **TINGGI-3** — `f"file:{path}?mode=ro"` merakit URI dengan interpolasi tanpa escape, sehingga
  nama berkas yang sah tetapi memuat `?`/`#`/`&` menyuntikkan parameternya sendiri dan
  `mode=ro` bisa ditimpa. `INSERT` benar-benar berhasil lewat koneksi yang modul itu sebut
  baca-saja — dan itu membatalkan satu-satunya alasan SQL boleh hidup di luar `core/store.py`.
* **TINGGI-2** — `decisions.card_json` tidak dilindungi hash apa pun, tetapi dulu dialah yang
  menentukan `entry`/`stop`/`target` saat posisi ditutup.

Bagian terakhir berkas ini adalah TASKS 2.4b: lipatan `trade_events` hanya melipat generasi
BERJALAN (`card_json` menyebut `algo` yang berlaku, ADR-016), dan apa yang tidak dilipat
dikembalikan sebagai data supaya pemanggil menuliskannya — bukan menyusut diam-diam.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent.ledger import (
    SQLITE_INT_MAX,
    SQLITE_INT_MIN,
    DecisionRow,
    EventRow,
    LedgerError,
    _connect,
    algo_by_decision,
    fold_positions,
    read_decision,
)
from core.replay import ALGO

SCHEMA = (
    "CREATE TABLE decisions (id INTEGER PRIMARY KEY, ts_utc TEXT, symbol TEXT, decision TEXT,"
    " blocking_json TEXT, snapshot_ids_json TEXT, params_hash TEXT, decision_hash TEXT,"
    " card_json TEXT, simulated INTEGER)"
)


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ledger-default.db"))


def _seed(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("name", ["evil.db?mode=rwc&x=", "note#1.db", "a%2fb.db", "plain.db"])
def test_a_ledger_connection_can_never_write_whatever_the_filename_is(tmp_path: Path, name: str) -> None:
    """Nama berkas adalah NAMA BERKAS, bukan sintaks URI."""
    path = tmp_path / name
    _seed(path)
    with _connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
        with pytest.raises(sqlite3.Error):
            conn.execute(
                "INSERT INTO decisions (id, ts_utc, symbol, decision, blocking_json,"
                " snapshot_ids_json, params_hash, decision_hash, card_json, simulated)"
                " VALUES (1,'x','SOL','GO','[]','[]','h','h','{}',0)"
            )
    # Berkas yang benar-benar dibaca adalah berkas yang kami tunjuk (bukan tetangganya).
    assert path.exists()


def test_query_only_is_a_second_layer(tmp_path: Path) -> None:
    path = tmp_path / "plain2.db"
    _seed(path)
    with _connect(path) as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1


def test_a_missing_file_is_an_honest_error(tmp_path: Path) -> None:
    with pytest.raises(LedgerError, match="baca-saja"), _connect(tmp_path / "nope.db") as conn:
        conn.execute("SELECT 1")


# --- TASKS 2.4b: lipatan posisi hanya melipat generasi berjalan ------------------------------
def _event(event_id: int, decision_id: int, kind: str, symbol: str = "SOL") -> EventRow:
    return EventRow(
        id=event_id,
        decision_id=decision_id,
        symbol=symbol,
        direction="long",
        kind=kind,
        price=100.0 + event_id,
        size_usd=100.0,
        reason="fixture",
        ts_utc=f"2026-09-0{1 + event_id % 3}T00:00:00+00:00",
        simulated=1,
    )


def test_the_fold_returns_only_positions_of_the_running_generation() -> None:
    """Fixture campuran warisan + baru: yang dilipat HANYA yang baru (AC 2.4b-1)."""
    events = [
        _event(1, 1, "open"),  # warisan: kartunya tanpa `algo`
        _event(2, 3, "open", "ETH"),  # warisan
        _event(3, 24, "open"),  # generasi berjalan
        _event(4, 25, "open", "BTC"),  # generasi berjalan, lalu ditutup
        _event(5, 25, "exit", "BTC"),
    ]
    algo_of = {1: None, 3: None, 24: ALGO, 25: ALGO}
    fold = fold_positions(events, algo_of)

    assert [pos.decision_id for pos in fold.positions] == [24]
    assert [event.id for event in fold.stale_events] == [1, 2]
    assert fold.orphan_events == ()
    note = fold.notes()[0]
    assert note.startswith("2 trade_events generasi sebelumnya diabaikan (STALE_ALGO)")
    assert "1, 3" in note


def test_the_fold_never_hides_what_it_skipped() -> None:
    """Diabaikan = TERTULIS. Tanpa catatan, lipatan yang menyusut adalah kebohongan diam."""
    fold = fold_positions([_event(1, 1, "open")], {1: None})
    assert fold.positions == ()
    assert fold.notes() and "STALE_ALGO" in fold.notes()[0]


def test_an_event_pointing_at_a_missing_decision_is_named_separately() -> None:
    fold = fold_positions([_event(1, 77, "open")], {})
    assert fold.positions == ()
    assert [event.id for event in fold.orphan_events] == [1]
    assert "tidak ada di ledger" in fold.notes()[0]


def test_generation_is_decided_by_algo_not_by_params_hash_or_id() -> None:
    """ADR-016 butir 2: params_hash boleh bergeser (ADR-017) tanpa mengubah generasi."""
    current = DecisionRow(
        id=24,
        ts_utc="2026-09-03T00:00:00+00:00",
        symbol="SOL",
        decision="GO",
        blocking_json="[]",
        snapshot_ids_json="[1]",
        params_hash="params-yang-akan-berubah-besok",
        decision_hash="h",
        card_json=json.dumps({"algo": ALGO, "skill": {}}),
        simulated=1,
    )
    legacy = DecisionRow(
        id=13,
        ts_utc="2026-09-01T00:00:00+00:00",
        symbol="BNB",
        decision="NO_GO",
        blocking_json="[5,6,10]",
        snapshot_ids_json="[81,83]",
        params_hash="params-generasi-lama",
        decision_hash="h",
        card_json=json.dumps({"skill": {}}),
        simulated=1,
    )
    assert current.algo() == ALGO and current.is_current_generation()
    assert legacy.algo() is None and not legacy.is_current_generation()


@pytest.mark.parametrize("card_json", ["", "[1,2]", "{", '{"algo": 7}'])
def test_an_unreadable_card_is_simply_not_the_running_generation(card_json: str) -> None:
    """`algo()` tidak melempar: kartu rusak juga tidak menyebut algoritma apa pun."""
    row = DecisionRow(
        id=1,
        ts_utc="t",
        symbol="SOL",
        decision="GO",
        blocking_json="[]",
        snapshot_ids_json="[]",
        params_hash="p",
        decision_hash="h",
        card_json=card_json,
        simulated=1,
    )
    assert row.algo() is None
    assert not row.is_current_generation()


def test_a_non_numeric_decision_id_is_a_ledger_error_not_a_raw_valueerror(tmp_path: Path) -> None:
    """RENDAH (security-reviewer 4 Sep 2026): tick harus MELAPORKAN, bukan meledak.

    `trade_events.decision_id` tidak dijaga tipe oleh SQLite (tabel tanpa STRICT), jadi nilai
    yang bukan angka mungkin ada di kolom itu.
    """
    path = tmp_path / "loose.db"
    _seed(path)
    with pytest.raises(LedgerError, match="bukan id baris"):
        algo_by_decision(path, ["bukan-angka"])


@pytest.mark.parametrize("out_of_range", [2**63, -(2**63) - 1, 10**40, -(10**40)])
def test_an_id_outside_the_sqlite_integer_range_is_a_ledger_error(tmp_path: Path, out_of_range: int) -> None:
    """Janji docstring "int atau `LedgerError`" harus benar untuk SETIAP int (TASKS 3.1b).

    Sebelum ini, `read_decision(2**63)` melempar `OverflowError` mentah dari `sqlite3` — id
    sebesar itu tidak bisa ditanyakan, bukan sekadar tidak ada. Batas rentang di route HTTP
    (`ui/app.py`) sudah cukup untuk UI, tetapi pemanggil KEDUA sudah ada hari ini:
    `python -m agent.replay 9223372036854775808` memberi traceback sementara `agent.replay abc`
    dijawab satu kalimat rapi (temuan security-reviewer 3.1).
    """
    path = tmp_path / "range.db"
    _seed(path)
    with pytest.raises(LedgerError, match="rentang INTEGER SQLite"):
        read_decision(path, out_of_range)


@pytest.mark.parametrize("edge", [SQLITE_INT_MAX, SQLITE_INT_MIN])
def test_the_edges_of_the_range_are_answered_not_refused(tmp_path: Path, edge: int) -> None:
    """Tepinya masih id yang SAH untuk ditanyakan — jawabannya "tidak ada", bukan galat."""
    path = tmp_path / "edge.db"
    _seed(path)
    assert read_decision(path, edge) is None
