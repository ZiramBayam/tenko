"""Tes TASKS 2.4c-min — penjangkaran item ke bukti beku (`agent/anchor.py`).

Berkas ini adalah **pembalikan yang terlihat** atas dua celah yang diakui TASKS 2.2: serangan 6
(status item dibalik + `n_*`/`decision`/`blocking`/`summary` diselaraskan + `decision_hash`
dihitung ulang sehingga COCOK) dan serangan 8 (`plan` dipalsukan di kedua sisi). Keduanya masih
LOLOS `check_aggregate_floor` — itu memang batas fungsi tersebut — dan keduanya kini DITOLAK
`check_against_evidence`. Tes yang dulu mengharapkannya lolos ada di
`tests/test_preflight_skill.py` dan diperbarui pada commit yang sama.

AC yang dipatok:

1. status tiap item dihitung ulang dari payload beku SEBELUM baris `decisions` ditulis;
   ketidakcocokan → tolak, sebut id item + `snapshot_id`;
2. `item.source.snapshot_id` wajib elemen `data["snapshot_ids"]` DAN ada di bundle;
3. serangan 6 ditolak.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.anchor import (
    EvidenceMismatchError,
    check_against_evidence,
    envelopes_from_bundle,
    recompute,
)
from agent.main import tick
from agent.params import DEFAULT_AGENT_PARAMS
from core.replay import decision_hash, freeze
from core.skill import SkillResult
from core.store import Store
from preflight.params import DEFAULT_PARAMS, ITEM_ORDER, ITEM_PLAN, ITEM_REGIME
from preflight.skill import check_aggregate_floor, preflight
from tests.helpers_agent import DEEP, MARKET_OVERVIEW, build_store

RELATIVE = "10_relative"


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "anchor-default.db"))


def run(store: Store, symbol: str = "SOL") -> tuple[SkillResult, object]:
    """Envelope + bundle yang MEMBEKUKAN buktinya, persis seperti agen merakitnya."""
    result = preflight(symbol, "swing", store=store, params=DEFAULT_PARAMS)
    assert result.data is not None
    bundle = freeze(store, result.data["snapshot_ids"])
    return result, bundle


def red_regime(tool: str, symbol: str, envelope: dict) -> None:
    """`market_overview.data.regime = "risk_off"` + `compare_tokens.data.factors = []`.

    Mutasi yang SAMA dengan serangan 6 tinjauan keamanan: dua item merah yang lalu dipalsukan
    menjadi hijau di envelope.
    """
    if tool == MARKET_OVERVIEW:
        envelope["data"]["regime"] = "risk_off"
    elif tool == "compare_tokens":
        envelope["data"]["factors"] = []


# --- jalur jujur ---------------------------------------------------------------------------
def test_an_honest_envelope_is_anchored_without_complaint(tmp_path: Path) -> None:
    store = build_store(tmp_path, symbols=("SOL",))
    result, bundle = run(store)
    recomputed = check_against_evidence(result, bundle, DEFAULT_PARAMS)
    assert [item.id for item in recomputed.items] == list(ITEM_ORDER)
    assert recomputed.decision.decision == result.data["decision"]


def test_recomputation_reproduces_preflight_items(tmp_path: Path) -> None:
    """Patokan anti-divergensi: penyaringan envelope di `anchor` = penyaringan di `gather`.

    `envelopes_from_bundle` menyalin aturan `preflight.skill.gather`. Kalau salah satunya
    bergeser, tes inilah yang memberi tahu — bukan replay yang MISMATCH di depan juri.
    """
    store = build_store(tmp_path, symbols=("SOL",))
    result, bundle = run(store)
    envelopes, ids = envelopes_from_bundle(bundle)
    assert set(envelopes) == set(ids)
    recomputed = recompute("SOL", bundle, DEFAULT_PARAMS)
    assert [item.model_dump(mode="json") for item in recomputed.items] == result.data["items"]


def test_recomputed_decision_hash_matches_the_envelope(tmp_path: Path) -> None:
    store = build_store(tmp_path, symbols=("SOL",))
    result, bundle = run(store)
    recomputed = check_against_evidence(result, bundle, DEFAULT_PARAMS)
    computed = decision_hash(bundle, DEFAULT_PARAMS.as_dict(), recomputed.decision.as_material())
    assert computed == result.data["decision_hash"]


# --- serangan 6: dua item merah dipalsukan hijau --------------------------------------------
def _forge_attack_six(result: SkillResult) -> None:
    """Balik dua item merah menjadi hijau dan selaraskan SEMUA pembukuannya."""
    data = result.data
    assert data is not None
    for entry in data["items"]:
        if entry["id"] in {ITEM_REGIME, RELATIVE}:
            entry["status"] = "green"
            result.availability[entry["id"]] = "available"
    data.update({"n_green": 10, "n_red": 0, "n_unspoken": 0, "n_spoken": 10})
    data["decision"] = "GO"
    data["blocking"] = []
    result.summary.update({"decision": "GO", "blocking": [], "headline": "SOL GO: 10 of 10"})


def test_attack_six_is_rejected_by_the_anchor(tmp_path: Path) -> None:
    """Serangan 6 tinjauan keamanan 2.2 — DITOLAK di sini (dulu: lolos, diakui di 2.2)."""
    store = build_store(tmp_path, symbols=("SOL",), mutate=red_regime)
    result, bundle = run(store)
    assert (result.data["decision"], result.data["blocking"]) == ("NO_GO", [ITEM_REGIME, RELATIVE])
    _forge_attack_six(result)

    # Lantai lama TETAP lolos: batasnya memang "konsisten dengan itemnya sendiri".
    check_aggregate_floor(result, DEFAULT_PARAMS)

    with pytest.raises(EvidenceMismatchError) as excinfo:
        check_against_evidence(result, bundle, DEFAULT_PARAMS)
    message = str(excinfo.value)
    assert ITEM_REGIME in message
    assert "snapshot_id=" in message  # AC: sebut id item DAN snapshot_id-nya
    assert "'green'" in message and "'red'" in message
    # Bukti beku tidak ikut berubah: kebohongan hanya ada di envelope.
    frozen = store.get_snapshots(result.data["snapshot_ids"])
    assert any(json.loads(row.payload_json)["data"].get("regime") == "risk_off" for row in frozen)


def test_attack_six_with_a_recomputed_matching_hash_is_still_rejected(tmp_path: Path) -> None:
    """Bagian yang membuat serangan 6 berbahaya: hash palsunya COCOK. Tetap ditolak."""
    store = build_store(tmp_path, symbols=("SOL",), mutate=red_regime)
    result, bundle = run(store)
    _forge_attack_six(result)
    forged_hash = decision_hash(
        bundle,
        DEFAULT_PARAMS.as_dict(),
        {
            "decision": "GO",
            "blocking": [],
            "item_status": {item.id: item.status for item in result.items},
        },
    )
    result.data["decision_hash"] = forged_hash
    # `decision_hash` yang dihitung dari bundle ASLI + item PALSU tetap sah menurut core/replay.
    assert len(forged_hash) == 64
    with pytest.raises(EvidenceMismatchError, match=ITEM_REGIME):
        check_against_evidence(result, bundle, DEFAULT_PARAMS)


def test_the_agent_writes_no_row_when_the_anchor_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC (1): penjangkaran terjadi SEBELUM baris `decisions` ditulis."""
    store = build_store(tmp_path, symbols=("SOL",), mutate=red_regime)

    def forging_preflight(symbol: str, intent: str = "swing", **kwargs: object) -> SkillResult:
        result = preflight(symbol, intent, **kwargs)
        _forge_attack_six(result)
        return result

    monkeypatch.setattr("agent.main.preflight", forging_preflight)
    report = tick(store, ("SOL",), DEFAULT_AGENT_PARAMS)
    assert report.n_rejected == 1
    assert report.n_go == 0
    assert ITEM_REGIME in report.outcomes[0].rejected
    # Tidak ada baris keputusan maupun trade yang ditulis untuk simbol yang ditolak.
    from agent.ledger import open_positions, recent_decisions

    assert recent_decisions(store.path) == []
    assert open_positions(store.path) == []


# --- serangan 8: `plan` dipalsukan di KEDUA sisi --------------------------------------------
def test_attack_eight_is_rejected_by_the_anchor(tmp_path: Path) -> None:
    store = build_store(tmp_path, symbols=("SOL",))
    result, bundle = run(store)
    forged_value = {
        **next(item.value for item in result.items if item.id == ITEM_PLAN),
        "entry": 1.0,
        "stop": 0.0001,
        "targets": [999999.0],
        "risk_reward": [99.0, 99.0],
    }
    for entry in result.data["items"]:
        if entry["id"] == ITEM_PLAN:
            entry["value"] = forged_value
    result.data["plan"]["value"] = forged_value

    check_aggregate_floor(result, DEFAULT_PARAMS)  # lolos: kedua sisi dari item yang sama
    with pytest.raises(EvidenceMismatchError) as excinfo:
        check_against_evidence(result, bundle, DEFAULT_PARAMS)
    assert ITEM_PLAN in str(excinfo.value)
    assert "value=" in str(excinfo.value)


def test_a_forged_reason_is_rejected_too(tmp_path: Path) -> None:
    """Kartu dibaca manusia: `reason` yang tidak berasal dari bukti juga kebohongan."""
    store = build_store(tmp_path, symbols=("SOL",))
    result, bundle = run(store)
    for entry in result.data["items"]:
        if entry["id"] == ITEM_REGIME:
            entry["reason"] = "regime risk_on strongly supports this long plan"
    with pytest.raises(EvidenceMismatchError, match="reason="):
        check_against_evidence(result, bundle, DEFAULT_PARAMS)


# --- AC (2): `source.snapshot_id` harus ada di dua tempat ------------------------------------
def test_a_ghost_source_snapshot_id_is_rejected(tmp_path: Path) -> None:
    store = build_store(tmp_path, symbols=("SOL",))
    result, bundle = run(store)
    for entry in result.data["items"]:
        if entry["id"] == ITEM_PLAN:
            entry["source"]["snapshot_id"] = 424242
    with pytest.raises(EvidenceMismatchError, match="snapshot_ids"):
        check_against_evidence(result, bundle, DEFAULT_PARAMS)


@pytest.mark.parametrize("bogus", ["2166", b"2166", 2166, {"id": 2166}, None])
def test_snapshot_ids_that_is_not_a_list_is_rejected(tmp_path: Path, bogus: object) -> None:
    """Cabang bentuk `snapshot_ids`, yang sebelumnya ada di kode tetapi tidak dipatok tes.

    Ditemukan lewat mutation testing 5 Sep 2026: melumpuhkan `raise` di cabang ini membiarkan
    114 tes tetap hijau. Bahayanya khusus untuk `str`/`bytes` — keduanya `Sequence`, jadi tanpa
    penjagaan eksplisit `"2166"` akan diiterasi sebagai karakter dan menghasilkan himpunan id
    KOSONG, yang lalu dibandingkan dengan bundle dan bisa lolos untuk bundle kosong.
    """
    store = build_store(tmp_path, symbols=("SOL",))
    result, bundle = run(store)
    result.data["snapshot_ids"] = bogus
    with pytest.raises(EvidenceMismatchError, match="daftar id baris"):
        check_against_evidence(result, bundle, DEFAULT_PARAMS)


def test_snapshot_ids_must_be_exactly_the_frozen_evidence(tmp_path: Path) -> None:
    store = build_store(tmp_path, symbols=("SOL",))
    result, bundle = run(store)
    result.data["snapshot_ids"] = [*result.data["snapshot_ids"], 999]
    with pytest.raises(EvidenceMismatchError, match="bukti yang dibekukan"):
        check_against_evidence(result, bundle, DEFAULT_PARAMS)


def test_a_bundle_with_other_evidence_is_rejected(tmp_path: Path) -> None:
    """Bundle yang bukan bukti keputusan ini (satu baris dibuang) → ditolak, bukan dinilai."""
    store = build_store(tmp_path, symbols=("SOL",))
    result, bundle = run(store)
    smaller = freeze(store, list(bundle.snapshot_ids)[:-1])
    with pytest.raises(EvidenceMismatchError):
        check_against_evidence(result, smaller, DEFAULT_PARAMS)


def test_params_that_did_not_judge_may_not_anchor(tmp_path: Path) -> None:
    store = build_store(tmp_path, symbols=("SOL",))
    result, bundle = run(store)
    other = DEFAULT_PARAMS.with_overrides(min_rr=99.0)
    with pytest.raises(EvidenceMismatchError, match="params_hash"):
        check_against_evidence(result, bundle, other)


def test_two_rows_for_one_tool_in_a_bundle_are_refused(tmp_path: Path) -> None:
    """Preflight membekukan satu baris per tool; dua baris = bundle yang bukan miliknya."""
    store = build_store(tmp_path, symbols=("SOL",))
    result, bundle = run(store)
    deep_id = next(entry.snapshot_id for entry in bundle.entries if entry.tool == DEEP)
    duplicate = store.insert_snapshot(
        DEEP,
        {"symbol": "SOL", "include_perp": True},
        json.loads(next(e.payload_json for e in bundle.entries if e.snapshot_id == deep_id)),
    )
    wider = freeze(store, [*bundle.snapshot_ids, duplicate])
    with pytest.raises(EvidenceMismatchError, match="dua baris"):
        envelopes_from_bundle(wider)
    # Lewat pintu depan, cek `snapshot_ids` yang menjaga urutan lebih dulu — pesannya beda,
    # penolakannya sama. Keduanya dipatok supaya tidak ada yang hilang saat urutan berubah.
    with pytest.raises(EvidenceMismatchError, match="bukti yang dibekukan"):
        check_against_evidence(result, wider, DEFAULT_PARAMS)


def test_a_refusal_envelope_has_nothing_to_anchor(tmp_path: Path) -> None:
    """`data=None` tidak membawa item bervonis; penjangkaran diam, bukan meledak."""
    store = build_store(tmp_path, symbols=("SOL",), age_min=DEFAULT_PARAMS.max_age_min + 60)
    result = preflight("SOL", "swing", store=store, params=DEFAULT_PARAMS)
    assert result.data is None
    recomputed = check_against_evidence(result, freeze(store, []), DEFAULT_PARAMS)
    assert recomputed.items == ()


# --- TINGGI-4 (tinjauan keamanan 4 Sep 2026): simbol envelope harus simbol yang diputuskan ---
def test_an_envelope_for_another_symbol_is_rejected(tmp_path: Path) -> None:
    """Bukti ETH tidak boleh dipakai untuk keputusan berlabel SOL.

    Envelope ETH konsisten dengan buktinya sendiri, jadi SEMUA penjaga lain lolos: lantai
    agregat, penilaian ulang item, bahkan `decision_hash`. Yang membedakannya hanya ikatan
    simbol yang diminta pemanggil.
    """
    store = build_store(tmp_path, symbols=("SOL", "ETH"))
    result, bundle = run(store, "ETH")
    assert result.data["symbol"] == "ETH"
    # Tanpa `symbol=`: lolos — envelope ini memang jujur tentang dirinya sendiri.
    check_against_evidence(result, bundle, DEFAULT_PARAMS)
    with pytest.raises(EvidenceMismatchError, match="menjawab 'ETH', bukan 'SOL'"):
        check_against_evidence(result, bundle, DEFAULT_PARAMS, symbol="SOL")


def test_the_agent_binds_the_watchlist_symbol_to_the_envelope(tmp_path: Path) -> None:
    """Jalur agen: envelope ETH yang disodorkan untuk SOL → 0 baris, 0 posisi."""
    store = build_store(tmp_path, symbols=("SOL", "ETH"))

    def wrong_symbol_preflight(symbol: str, intent: str = "swing", **kwargs: object) -> SkillResult:
        return preflight("ETH", intent, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("agent.main.preflight", wrong_symbol_preflight)
    try:
        report = tick(store, ("SOL",), DEFAULT_AGENT_PARAMS)
    finally:
        monkeypatch.undo()
    from agent.ledger import open_positions, recent_decisions

    assert report.n_rejected == 1
    assert "menjawab 'ETH', bukan 'SOL'" in report.outcomes[0].rejected
    assert recent_decisions(store.path) == []
    assert open_positions(store.path) == []
