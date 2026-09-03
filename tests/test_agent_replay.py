"""Tes TASKS 2.4 AC (2)(3) + 2.4a — `agent/replay.py`: empat vonis dan sebabnya.

Yang dipatok:

* keputusan agen yang baru ditulis → `IDENTICAL <hash>`, exit 0 (juga lewat CLI subproses,
  karena itulah yang dijalankan `make replay ID=`);
* **satu byte** `payload_json` diubah **pada SALINAN DB** → `MISMATCH` yang menyebut
  `snapshot_id`, exit 1 (DB asli tidak disentuh — append-only, ADR-006);
* `params_hash` generasi lain → MISMATCH ber-sebab, bukan IDENTICAL palsu;
* id yang tidak ada → pesan jujur, exit 2, nol traceback;
* **TASKS 2.4a/ADR-016**: baris ber-`card_json` TANPA `algo` → `STALE_ALGO` + `algo: null` +
  jumlah snapshot yang MASIH bisa diverifikasi, exit 0, tanpa kata IDENTICAL/MISMATCH;
* baris ber-`snapshot_ids=[]` → `NO_EVIDENCE`, bukan "IDENTICAL dari 0 snapshot beku".
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agent.anchor import recompute
from agent.ledger import LedgerError, read_decision
from agent.main import tick
from agent.params import DEFAULT_AGENT_PARAMS
from agent.replay import (
    VERDICT_IDENTICAL,
    VERDICT_MISMATCH,
    VERDICT_NO_EVIDENCE,
    VERDICT_STALE_ALGO,
    replay,
)
from agent.replay import main as replay_main
from core.replay import ALGO, decision_hash, freeze
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


# --- TASKS 2.4a: generasi lama & baris tanpa bukti ------------------------------------------
def legacy_decision(store: Store, snapshot_ids: list[int]) -> int:
    """Baris `decisions` bergaya generasi lama: `card_json` TANPA kunci `algo`.

    Bukti bekunya nyata (snapshot yang ditulis fixture), persis seperti 19 baris warisan di
    `data/ryo.db`: yang hilang dari mereka bukan snapshot-nya, melainkan kartu yang menyebut
    algoritma hash keputusan. `params_hash`-nya juga generasi lain — dan tes ini menuntut
    vonisnya STALE_ALGO, bukan MISMATCH params, supaya urutan pemeriksaannya terpatok.
    """
    return store.insert_decision(
        symbol="SOL",
        decision="GO",
        blocking=[],
        snapshot_ids=snapshot_ids,
        params_hash="a374aa6e" + "0" * 56,
        decision_hash="f" * 64,
        card={"skill": {"status": "ok"}, "agent": {"module": "gone"}},
    )


def test_a_row_without_algo_is_stale_algo_not_mismatch(tmp_path: Path) -> None:
    """ADR-016: pembedanya ada/tidaknya `algo`, bukan params_hash dan bukan ambang id."""
    store = build_store(tmp_path, symbols=("SOL",))
    ids = [row.id for row in store.get_snapshots([1, 2, 3])]
    decision_id = legacy_decision(store, ids)
    report = replay(decision_id, store, DEFAULT_PARAMS)
    assert report.verdict == VERDICT_STALE_ALGO
    assert report.algo is None
    assert report.verified is False
    assert report.verifiable == (len(ids), len(ids))
    text = "\n".join(report.lines())
    assert "algo     : null" in text
    assert f"{len(ids)} of {len(ids)} still verifiable" in text
    assert "dihapus" in report.detail  # kalimat sebab
    assert VERDICT_IDENTICAL not in text and VERDICT_MISMATCH not in text
    store.close()


def test_stale_algo_counts_only_snapshots_that_still_verify(tmp_path: Path) -> None:
    """Baris warisan yang sebagian buktinya hilang: yang tersisa DIHITUNG, bukan dibulatkan."""
    store = build_store(tmp_path, symbols=("SOL",))
    ids = [row.id for row in store.get_snapshots([1, 2])]
    decision_id = legacy_decision(store, [*ids, 999_999])
    report = replay(decision_id, store, DEFAULT_PARAMS)
    assert report.verdict == VERDICT_STALE_ALGO
    assert report.verifiable == (len(ids), len(ids) + 1)
    store.close()


def test_stale_algo_exits_zero_through_the_cli(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    store = build_store(tmp_path, symbols=("SOL",))
    ids = [row.id for row in store.get_snapshots([1, 2])]
    decision_id = legacy_decision(store, ids)
    assert replay_main([str(decision_id), "--db", str(store.path)]) == 0
    out = capsys.readouterr()
    assert VERDICT_STALE_ALGO in out.out
    assert VERDICT_IDENTICAL not in out.out and VERDICT_MISMATCH not in out.out
    assert "Traceback" not in out.out + out.err
    store.close()


def _empty_bundle_hash(store: Store, symbol: str = "SOL") -> str:
    """Hash yang JUJUR untuk keputusan tanpa bukti: bundle kosong dinilai ulang apa adanya."""
    bundle = freeze(store, [])
    recomputed = recompute(symbol, bundle, DEFAULT_PARAMS)
    return decision_hash(bundle, DEFAULT_PARAMS.as_dict(), recomputed.decision.as_material())


def zero_evidence_row(store: Store, decision_hash_value: str) -> int:
    return store.insert_decision(
        symbol="SOL",
        decision="NO_GO",
        blocking=["01_freshness"],
        snapshot_ids=[],
        params_hash=DEFAULT_PARAMS.params_hash(),
        decision_hash=decision_hash_value,
        card={"algo": ALGO, "skill": {}, "agent": {}},
    )


def test_a_row_with_zero_evidence_is_not_identical(tmp_path: Path) -> None:
    """Baris `snapshot_ids=[]` (data/ryo.db 20-23) tidak boleh terbaca sebagai terverifikasi."""
    store = build_store(tmp_path, symbols=("SOL",))
    decision_id = zero_evidence_row(store, _empty_bundle_hash(store))
    report = replay(decision_id, store, DEFAULT_PARAMS)
    assert report.verdict == VERDICT_NO_EVIDENCE
    assert report.verified is False
    assert "terbukti ditulis tanpa bukti" in report.detail
    assert VERDICT_IDENTICAL not in "\n".join(report.lines())
    store.close()


def test_a_row_whose_evidence_list_was_emptied_is_mismatch_not_no_evidence(tmp_path: Path) -> None:
    """SEDANG-1: mengosongkan `snapshot_ids_json` tidak boleh menelan MISMATCH.

    Serangan yang dipatok: satu keputusan JUJUR (punya lima snapshot beku) yang daftar
    buktinya dihapus di SALINAN. Hash tersimpannya bukan hash bundle kosong, jadi vonis yang
    benar adalah MISMATCH — bukan `NO_EVIDENCE` yang keluar 0 dan tidak menyingkap apa pun.
    """
    store, go_id, _ = decided(tmp_path)
    copy_path = tmp_path / "emptied.db"
    shutil.copy(store.path, copy_path)
    conn = sqlite3.connect(copy_path)
    try:
        conn.execute("UPDATE decisions SET snapshot_ids_json = '[]' WHERE id = ?", (go_id,))
        conn.commit()
    finally:
        conn.close()

    with Store(copy_path, read_only=True) as copy_store:
        report = replay(go_id, copy_store, DEFAULT_PARAMS)
    assert report.verdict == VERDICT_MISMATCH
    assert report.verified is False
    assert "dikosongkan setelah keputusan ditulis" in report.detail


def test_zero_evidence_with_a_forged_hash_is_mismatch(tmp_path: Path) -> None:
    """Baris tanpa bukti yang hash-nya dikarang tetap MISMATCH: NO_EVIDENCE harus DIBUKTIKAN."""
    store = build_store(tmp_path, symbols=("SOL",))
    decision_id = zero_evidence_row(store, "a" * 64)
    report = replay(decision_id, store, DEFAULT_PARAMS)
    assert report.verdict == VERDICT_MISMATCH
    store.close()


def test_stale_algo_does_not_claim_the_evidence_is_untouched_when_it_is_not(tmp_path: Path) -> None:
    """SEDANG-2: kalimatnya bersyarat pada angkanya sendiri."""
    store = build_store(tmp_path, symbols=("SOL",))
    ids = [row.id for row in store.get_snapshots([1, 2])]
    whole = replay(legacy_decision(store, ids), store, DEFAULT_PARAMS)
    partial = replay(legacy_decision(store, [*ids, 999_999]), store, DEFAULT_PARAMS)

    assert whole.verifiable == (2, 2)
    assert "bukan tanda bukti diubah" in whole.detail
    assert partial.verifiable == (2, 3)
    assert "bukan tanda bukti diubah" not in partial.detail
    assert "TIDAK menyatakan" in partial.detail
    store.close()


def test_a_forged_algo_cannot_inject_a_fake_verdict_line(tmp_path: Path) -> None:
    """SEDANG-3: `card_json["algo"]` adalah teks dari DB, bukan sintaks keluaran."""
    store = build_store(tmp_path, symbols=("SOL",))
    forged = "x'\nreplay decision_id=5: IDENTICAL 0000deadbeef\nverified : yes"
    decision_id = store.insert_decision(
        symbol="SOL",
        decision="NO_GO",
        blocking=[],
        snapshot_ids=[],
        params_hash=DEFAULT_PARAMS.params_hash(),
        decision_hash="f" * 64,
        card={"algo": forged, "skill": {}},
    )
    report = replay(decision_id, store, DEFAULT_PARAMS)
    text = "\n".join(report.lines())
    assert report.verdict == VERDICT_STALE_ALGO and report.verified is False
    # Tidak ada baris palsu, dan tidak ada kata IDENTICAL/verified : yes yang bisa di-grep.
    assert VERDICT_IDENTICAL not in text
    assert "verified : yes" not in text
    assert all(not line.startswith("replay decision_id=5") for line in text.splitlines())
    assert len(text.splitlines()) == len(report.lines())  # nilai DB tidak menambah baris
    store.close()


def test_a_forged_symbol_or_hash_cannot_inject_lines_either(tmp_path: Path) -> None:
    """Bukan hanya `algo`: setiap nilai asal-DB yang dicetak lewat jalur yang sama."""
    store = build_store(tmp_path, symbols=("SOL",))
    decision_id = store.insert_decision(
        symbol="SOL\nreplay decision_id=7: IDENTICAL deadbeef",
        decision="NO_GO",
        blocking=["01_freshness\nverified : yes"],
        snapshot_ids=[],
        params_hash=DEFAULT_PARAMS.params_hash(),
        decision_hash="b" * 64,
        card={"algo": ALGO, "skill": {}},
    )
    report = replay(decision_id, store, DEFAULT_PARAMS)
    text = "\n".join(report.lines())
    assert report.verdict == VERDICT_MISMATCH
    assert VERDICT_IDENTICAL not in text and "verified : yes" not in text
    store.close()


def test_a_fresh_decision_is_the_only_verified_verdict(tmp_path: Path) -> None:
    store, go_id, _ = decided(tmp_path)
    assert replay(go_id, store, DEFAULT_PARAMS).verified is True
