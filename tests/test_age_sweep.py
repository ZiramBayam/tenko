"""Tes TASKS 0.20 — kanari `scripts/age_sweep.py`.

Kanari yang tidak pernah diuji merah adalah kanari yang tidak menjaga apa pun: seluruh gunanya
adalah menyala ketika `as_of` RYO mulai tertinggal dari `ts_utc`. Karena itu berkas ini menyusun
DB buatan di `tmp_path` dengan selisih yang dipilih sendiri, dan menuntut exit code-nya berubah.

`data/ryo.db` yang hidup TIDAK disentuh: perekam sedang menulis ke sana, dan snapshotnya tidak
bisa dibuat ulang.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.store import Store
from scripts.age_sweep import FIXTURE_IDS, MAX_LAG_HOURS, main, sweep


def _envelope(as_of: datetime) -> dict:
    return {
        "schema_version": "1.0",
        "tool": "analyze_token",
        "status": "ok",
        "data_mode": "live",
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
        "request": {"symbol": "SOL"},
        "data": {"verdict": "neutral"},
        "summary": {"headline": "x", "key_points": []},
        "availability": {"market_data": "available"},
        "warnings": [],
    }


def _db(tmp_path: Path, lags_hours: list[float]) -> Path:
    """DB dengan satu baris per selisih yang diminta (`ts_utc − as_of` = lag)."""
    path = tmp_path / "sweep.db"
    recorded = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    with Store(path) as store:
        for lag in lags_hours:
            store.insert_snapshot(
                "analyze_token",
                {"symbol": "SOL"},
                _envelope(recorded - timedelta(hours=lag)),
                ts_utc=recorded.isoformat(),
            )
    return path


def test_a_healthy_store_is_green(tmp_path: Path) -> None:
    assert main([str(_db(tmp_path, [0.0, 0.001, -0.0005]))]) == 0


def test_a_lagging_store_turns_the_canary_red(tmp_path: Path) -> None:
    """Inti 0.20: gejala yang item 1 TIDAK bisa lihat harus terlihat di sini."""
    assert main([str(_db(tmp_path, [0.0, MAX_LAG_HOURS + 1.0]))]) == 1


def test_the_canary_is_red_for_a_clock_that_runs_far_ahead_too(tmp_path: Path) -> None:
    """Selisih NEGATIF besar juga tidak wajar — `as_of` jauh di masa depan."""
    assert main([str(_db(tmp_path, [0.0, -(MAX_LAG_HOURS + 1.0)]))]) == 1


def test_the_threshold_is_the_edge_not_a_suggestion(tmp_path: Path) -> None:
    assert main([str(_db(tmp_path, [MAX_LAG_HOURS - 0.01]))]) == 0
    assert main([str(_db(tmp_path, [MAX_LAG_HOURS + 0.01]))]) == 1


def test_rows_without_as_of_are_counted_not_treated_as_zero(tmp_path: Path) -> None:
    """null ≠ default: baris tanpa `as_of` dilaporkan, tidak diam-diam dihitung selisih 0."""
    path = _db(tmp_path, [0.0])
    connection = sqlite3.connect(path)
    try:
        envelope = _envelope(datetime(2026, 9, 5, 12, 0, tzinfo=UTC))
        del envelope["as_of"]
        connection.execute(
            "INSERT INTO snapshots (tool, args_json, args_hash, ts_utc, as_of, status,"
            " payload_json, payload_hash) VALUES (?,?,?,?,?,?,?,?)",
            ("analyze_token", "{}", "h", "2026-09-05T12:00:00+00:00", None, "ok", json.dumps(envelope), "h2"),
        )
        connection.commit()
    finally:
        connection.close()
    lags, _, missing = sweep(path)
    assert missing == 1
    assert len(lags) == 1  # baris tanpa as_of TIDAK ikut menjadi 0,0


def test_the_contaminating_fixture_rows_are_excluded(tmp_path: Path) -> None:
    """Kalau 47 baris fixture (TASKS 0.19) ikut, kanari merah selamanya oleh sesuatu yang
    sudah didiagnosis — dan kanari yang selalu merah akan diabaikan."""
    assert 1597 in FIXTURE_IDS and 1643 in FIXTURE_IDS
    assert 1596 not in FIXTURE_IDS and 1644 not in FIXTURE_IDS


def test_a_missing_database_says_so_instead_of_reporting_zero(tmp_path: Path) -> None:
    assert main([str(tmp_path / "tidak-ada.db")]) == 2


def test_the_canary_never_writes_to_the_database(tmp_path: Path) -> None:
    """`mode=ro`, seperti `scripts/db_status.py`: nol byte ditulis, tanpa `-journal`/`-wal`."""
    path = _db(tmp_path, [0.0])
    before = {p.name for p in tmp_path.iterdir()}
    digest = path.read_bytes()
    assert main([str(path)]) == 0
    assert path.read_bytes() == digest
    assert {p.name for p in tmp_path.iterdir()} == before


@pytest.mark.skipif(
    not (Path(__file__).parent.parent / "data" / "ryo.db").exists(), reason="DB hidup tidak ada"
)
def test_the_live_store_is_green_today() -> None:
    """Bukti bahwa penerimaan batas 2.2e masih berdasar HARI INI, bukan hanya saat ditulis."""
    assert main([]) == 0
