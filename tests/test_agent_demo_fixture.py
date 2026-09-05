"""Fixture demo bertanggal untuk spec §7 langkah 3 (TASKS 3.4a).

Yang dibuktikan berkas ini, satu tes per AC:

* AC(1) bukti DISALIN — fixture membawa envelope-nya sendiri dan satu tick berhasil dari
  direktori kerja yang tidak punya `data/ryo.db` sama sekali;
* AC(2) satu tick menghasilkan ≥1 GO dan ≥1 NO_GO ber-`blocking` yang tidak kosong;
* AC(3) dua run identik baris demi baris, `decision_hash` termasuk (dan sama dengan hash yang
  tercatat saat penyalinan — kalau penilaian bergeser, tes ini yang bicara lebih dulu);
* AC(4) fixture ditolak masuk ke store bukti, lewat penjaga TASKS 0.19 yang sudah ada.

Tidak ada tes di sini yang menulis ke `data/ryo.db`; satu-satunya yang menyentuhnya adalah
pemeriksaan bahwa jalur itu DITOLAK — dan penolakannya terjadi sebelum store dibuka.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent.demo_fixture import (
    DEMO_FIXTURE_PATH,
    DemoFixtureError,
    load_fixture,
    materialize,
    report_lines,
    run_tick,
)
from agent.main import tick
from agent.params import DEFAULT_AGENT_PARAMS
from core.store import DEFAULT_DB_PATH, Store

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ROOT / "data"


@pytest.fixture(scope="module")
def demo():
    return load_fixture(DEMO_FIXTURE_PATH)


def test_fixture_carries_its_own_evidence(demo) -> None:
    """AC(1): bukti ada DI DALAM berkas fixture, bukan ditunjuk ke store hidup."""
    assert demo.snapshots, "fixture tanpa snapshot tidak membekukan apa pun"
    for snapshot in demo.snapshots:
        assert snapshot.envelope.get("status"), f"{snapshot.tool}: envelope tanpa status"
        assert snapshot.ts_utc, f"{snapshot.tool}: ts_utc kosong — ia ikut di-hash"
    assert demo.source_ids, "provenance id sumber wajib tercatat"


def test_tick_runs_without_the_live_store(tmp_path, monkeypatch, demo) -> None:
    """AC(1): dari cwd tanpa `data/ryo.db` dan `DB_PATH` yang tidak ada, tick tetap jalan."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tidak-ada.db"))
    assert not (tmp_path / "data" / "ryo.db").exists()
    report = run_tick(demo, tmp_path / "demo.db")
    assert report.n_go + report.n_no_go == len(demo.symbols)


def test_one_tick_gives_at_least_one_go_and_one_blocked_no_go(tmp_path, demo) -> None:
    """AC(2): syarat spec §7 langkah 3, apa adanya."""
    report = run_tick(demo, tmp_path / "demo.db")
    assert report.n_rejected == 0, [outcome.rejected for outcome in report.outcomes]
    assert report.n_insufficient == 0, [outcome.insufficient for outcome in report.outcomes]
    assert report.n_go >= 1, "spec §7 langkah 3 menuntut ≥1 GO"
    blocked = [outcome for outcome in report.outcomes if outcome.decision == "NO_GO" and outcome.blocking]
    assert blocked, "spec §7 langkah 3 menuntut ≥1 NO_GO DENGAN blocking"
    for outcome in report.outcomes:
        if outcome.decision == "GO":
            assert outcome.plan is not None, f"{outcome.symbol}: GO tanpa practice trade"
            assert outcome.trade_event_id is not None


def test_two_runs_are_identical(tmp_path, demo) -> None:
    """AC(3): dua run, dua DB baru, laporan ternormalisasi sama baris demi baris."""
    first = report_lines(demo, run_tick(demo, tmp_path / "a.db"))
    second = report_lines(demo, run_tick(demo, tmp_path / "b.db"))
    assert first == second
    assert any("decision_hash=" in line for line in first), "hash tidak boleh dinormalisasi hilang"


def test_decisions_match_what_was_recorded_at_capture(tmp_path, demo) -> None:
    """AC(3): keputusan, blocking, DAN `decision_hash` sama dengan saat fixture disalin.

    Kalau tes ini merah, yang bergeser adalah PENILAIAN (item/params), bukan fixture-nya:
    fixture beku, jadi hash yang berubah berarti keputusan lama tidak lagi bisa di-replay.
    """
    report = run_tick(demo, tmp_path / "demo.db")
    observed = {
        outcome.symbol: {
            "decision": outcome.decision,
            "blocking": list(outcome.blocking),
            "decision_hash": outcome.decision_hash,
        }
        for outcome in report.outcomes
    }
    assert observed == {symbol: dict(entry) for symbol, entry in demo.recorded.items()}
    assert demo.params_hash == DEFAULT_AGENT_PARAMS.decision_params_hash()


def test_frozen_clock_is_what_makes_the_fixture_readable(tmp_path, demo) -> None:
    """Jam beku bukan kenyamanan: tanpa `now=` fixture terbaca basi dan nol baris ditulis."""
    age_min = (datetime.now(UTC) - demo.now).total_seconds() / 60
    if age_min <= DEFAULT_AGENT_PARAMS.preflight.max_age_min:
        pytest.skip(f"fixture masih segar terhadap jam dinding ({age_min:.0f} mnt)")
    materialize(demo, tmp_path / "demo.db")
    with Store(tmp_path / "demo.db") as store:
        report = tick(store, demo.symbols, DEFAULT_AGENT_PARAMS)
    assert report.n_insufficient == len(demo.symbols)
    assert report.n_go == 0


def test_refuses_the_repo_evidence_store(demo) -> None:
    """AC(4): `data/ryo.db` ditolak dengan namanya sendiri."""
    with pytest.raises(DemoFixtureError) as excinfo:
        materialize(demo, DEFAULT_DB_PATH)
    assert "store bukti" in str(excinfo.value)


def test_refuses_a_new_file_inside_the_evidence_dir(demo) -> None:
    """AC(4): ditolak SEBELUM store dibuka — tidak ada DB kosong yang tertinggal."""
    target = EVIDENCE_DIR / "tenko-demo-fixture-tidak-boleh-ada.db"
    assert not target.exists()
    with pytest.raises(DemoFixtureError):
        materialize(demo, target)
    assert not target.exists(), "penolakan tidak boleh meninggalkan berkas"


def test_refuses_a_db_that_already_holds_decisions(tmp_path, demo) -> None:
    """AC(4): DB yang sudah memegang buku besar keputusan adalah store bukti juga."""
    target = tmp_path / "demo.db"
    run_tick(demo, target)
    with pytest.raises(DemoFixtureError) as excinfo:
        materialize(demo, target)
    assert "buku besar keputusan" in str(excinfo.value)


def test_load_rejects_a_malformed_fixture(tmp_path, demo) -> None:
    """Bentuk yang tidak lengkap = error, bukan default diam-diam."""
    raw = json.loads(DEMO_FIXTURE_PATH.read_text(encoding="utf-8"))
    raw["fixture_version"] = 999
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DemoFixtureError):
        load_fixture(broken)
    raw["fixture_version"] = 1
    raw["snapshots"] = []
    broken.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DemoFixtureError):
        load_fixture(broken)
