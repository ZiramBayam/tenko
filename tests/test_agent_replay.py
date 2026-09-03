"""Tes TASKS 2.4 AC (2)(3) — `agent/replay.py`: IDENTICAL, MISMATCH, dan sebabnya.

Yang dipatok:

* keputusan agen yang baru ditulis → `IDENTICAL <hash>`, exit 0 (juga lewat CLI subproses,
  karena itulah yang dijalankan `make replay ID=`);
* **satu byte** `payload_json` diubah **pada SALINAN DB** → `MISMATCH` yang menyebut
  `snapshot_id`, exit 1 (DB asli tidak disentuh — append-only, ADR-006);
* `params_hash` generasi lain → MISMATCH ber-sebab, bukan IDENTICAL palsu;
* id yang tidak ada → pesan jujur, exit 2, nol traceback.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agent.ledger import LedgerError, read_decision
from agent.main import tick
from agent.params import DEFAULT_AGENT_PARAMS
from agent.replay import VERDICT_IDENTICAL, VERDICT_MISMATCH, replay
from agent.replay import main as replay_main
from core.store import Store
from preflight.params import DEFAULT_PARAMS
from tests.helpers_agent import build_store, mixed_confluence

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "replay-default.db"))


def decided(tmp_path: Path) -> tuple[Store, int, int]:
    """Satu tick dua simbol → (store, id keputusan GO, id keputusan NO_GO)."""
    store = build_store(tmp_path, symbols=("SOL", "BTC"), mutate=mixed_confluence("BTC"))
    report = tick(store, ("SOL", "BTC"), DEFAULT_AGENT_PARAMS)
    by_verdict = {outcome.decision: outcome.decision_id for outcome in report.outcomes}
    assert set(by_verdict) == {"GO", "NO_GO"}
    return store, by_verdict["GO"], by_verdict["NO_GO"]


def test_a_fresh_decision_replays_identical(tmp_path: Path) -> None:
    store, go_id, no_go_id = decided(tmp_path)
    for decision_id in (go_id, no_go_id):
        report = replay(decision_id, store, DEFAULT_PARAMS)
        assert report.verdict == VERDICT_IDENTICAL
        assert report.computed_hash == report.stored_hash
        assert len(report.stored_hash) == 64
        assert VERDICT_IDENTICAL in report.lines()[0]


def test_replay_reads_a_copy_of_the_database_identically(tmp_path: Path) -> None:
    """Salinan berkas = keputusan yang sama; id baris lokal tidak ikut di-hash."""
    store, go_id, _ = decided(tmp_path)
    copy_path = tmp_path / "copy.db"
    shutil.copy(store.path, copy_path)
    with Store(copy_path, read_only=True) as copy_store:
        assert replay(go_id, copy_store, DEFAULT_PARAMS).verdict == VERDICT_IDENTICAL


def test_one_tampered_byte_in_the_copy_gives_mismatch_naming_the_snapshot(tmp_path: Path) -> None:
    store, go_id, _ = decided(tmp_path)
    copy_path = tmp_path / "tampered.db"
    shutil.copy(store.path, copy_path)

    snapshot_id = replay(go_id, store, DEFAULT_PARAMS)
    assert snapshot_id.verdict == VERDICT_IDENTICAL  # asli tetap identik

    frozen = read_decision(store.path, go_id).snapshot_ids()
    conn = sqlite3.connect(copy_path)
    try:
        # SQL tetap konstan; penyaringan id dilakukan di Python (ruff S608).
        candidates = conn.execute(
            "SELECT id, payload_json FROM snapshots WHERE tool = 'deep_analysis' ORDER BY id"
        ).fetchall()
        row = next(entry for entry in candidates if entry[0] in frozen)
        # SATU byte: satu digit angka di dalam payload diganti.
        tampered = row[1].replace("103.8", "104.8", 1)
        assert tampered != row[1] and len(tampered) == len(row[1])
        conn.execute("UPDATE snapshots SET payload_json = ? WHERE id = ?", (tampered, row[0]))
        conn.commit()
    finally:
        conn.close()

    with Store(copy_path, read_only=True) as copy_store:
        report = replay(go_id, copy_store, DEFAULT_PARAMS)
    assert report.verdict == VERDICT_MISMATCH
    assert f"snapshot_id={row[0]}" in report.detail
    assert "payload_hash" in report.detail


def test_a_decision_from_another_params_generation_is_mismatch_with_a_reason(tmp_path: Path) -> None:
    store, go_id, _ = decided(tmp_path)
    other = DEFAULT_PARAMS.with_overrides(min_rr=99.0)
    report = replay(go_id, store, other)
    assert report.verdict == VERDICT_MISMATCH
    assert "params_hash" in report.detail
    assert other.params_hash() in report.detail


def test_a_missing_row_is_an_honest_error_not_a_verdict(tmp_path: Path) -> None:
    store, _go_id, _ = decided(tmp_path)
    with pytest.raises(LedgerError, match="tidak ada keputusan"):
        replay(9999, store, DEFAULT_PARAMS)


# --- CLI: inilah yang dijalankan `make replay ID=` -----------------------------------------
def test_cli_prints_identical_and_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    store, go_id, _ = decided(tmp_path)
    assert replay_main([str(go_id), "--db", str(store.path)]) == 0
    out = capsys.readouterr().out
    assert VERDICT_IDENTICAL in out and "MISMATCH" not in out


def test_cli_rejects_a_non_numeric_id(capsys: pytest.CaptureFixture) -> None:
    assert replay_main(["SOL"]) == 2
    assert "bilangan bulat" in capsys.readouterr().err


def test_cli_reports_a_missing_row_without_a_traceback(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    store, _go_id, _ = decided(tmp_path)
    assert replay_main(["4242", "--db", str(store.path)]) == 2
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err + captured.out
    assert "tidak ada keputusan" in captured.err


def test_cli_in_a_subprocess_matches_make_replay(tmp_path: Path) -> None:
    """Jalur proses penuh (`python -m agent.replay <id>`), env minimal seperti `make replay`."""
    store, go_id, _ = decided(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "agent.replay", str(go_id)],
        cwd=ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(ROOT),
            "LC_ALL": "C.UTF-8",
            "DB_PATH": str(store.path),
            "PYTHONPATH": str(ROOT),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert VERDICT_IDENTICAL in proc.stdout
    assert "Traceback" not in proc.stderr
