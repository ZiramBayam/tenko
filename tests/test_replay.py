"""Tes TASKS 1.4 — core/replay.py: bundle beku, hash keputusan, deteksi tamper.

Semua tes OFFLINE dan hanya menulis ke `tmp_path`; `data/ryo.db` tidak disentuh sama sekali
(perekam sedang menulis ke sana).
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from core.replay import (
    ALGO,
    Bundle,
    BundleEntry,
    ReplayError,
    ReplayMissingSnapshotError,
    ReplayTamperError,
    decision_hash,
    freeze,
    params_hash,
    verify_bundle,
)
from core.store import Store, StoreError, canonical_json

ROOT = Path(__file__).resolve().parent.parent

TS = "2026-09-01T03:51:43.123456+00:00"
ARGS = {"symbol": "SOL"}
ENVELOPE: dict[str, Any] = {
    "schema_version": "1.0",
    "tool": "analyze_token",
    "status": "ok",
    "data_mode": "live",
    "as_of": "2026-09-01T03:51:43Z",
    "data": {"verdict": "accumulate", "price": 203.41, "note": "上昇"},
    "availability": {"token_profile": "unavailable"},
    "warnings": [],
}
PARAMS = {"max_age_min": 30, "intent": "swing"}
DECISION = {
    "decision": "NO_GO",
    "blocking": ["liquidity", "freshness"],
    "item_status": {"liquidity": "red", "freshness": "red", "trend": "green", "funding": "unspoken"},
}


def make_store(path: Path, envelope: dict[str, Any] | None = None, *, filler: int = 0) -> Store:
    """Store baru berisi satu snapshot; `filler` baris didahulukan supaya id-nya bergeser."""
    store = Store(path)
    for i in range(filler):
        store.insert_snapshot("scan_tokens", {"n": i}, {"status": "ok"}, ts_utc=TS)
    store.insert_snapshot("analyze_token", ARGS, envelope if envelope is not None else ENVELOPE, ts_utc=TS)
    return store


def one_bundle(tmp_path: Path, name: str = "a.db", **kwargs: Any) -> Bundle:
    with make_store(tmp_path / name, **kwargs) as store:
        ids = [s.id for s in store.get_snapshots(range(1, 50))]
        return freeze(store, ids)


def raw_insert(path: Path, **cols: Any) -> int:
    """Tulis baris `snapshots` langsung, melewati Store — satu-satunya cara membuat baris rusak."""
    row = {
        "tool": "analyze_token",
        "args_json": canonical_json(ARGS),
        "args_hash": hashlib.sha256(canonical_json(ARGS).encode()).hexdigest(),
        "ts_utc": TS,
        "as_of": ENVELOPE["as_of"],
        "status": "ok",
        "payload_json": canonical_json(ENVELOPE),
        "payload_hash": hashlib.sha256(canonical_json(ENVELOPE).encode()).hexdigest(),
    }
    row.update(cols)
    conn = sqlite3.connect(str(path))
    try:
        cur = conn.execute(
            "INSERT INTO snapshots (tool, args_json, args_hash, ts_utc, as_of, status,"
            " payload_json, payload_hash) VALUES (?,?,?,?,?,?,?,?)",
            tuple(
                row[k]
                for k in (
                    "tool",
                    "args_json",
                    "args_hash",
                    "ts_utc",
                    "as_of",
                    "status",
                    "payload_json",
                    "payload_hash",
                )
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


# --- AC 1: dua panggilan atas bundle sama → hex identik --------------------------------------
def test_decision_hash_is_identical_across_calls(tmp_path: Path) -> None:
    bundle = one_bundle(tmp_path)
    first = decision_hash(bundle, PARAMS, DECISION)
    second = decision_hash(bundle, PARAMS, DECISION)
    assert first == second
    assert len(first) == 64 and int(first, 16) >= 0


def test_decision_hash_identical_for_two_freezes_of_the_same_store(tmp_path: Path) -> None:
    with make_store(tmp_path / "a.db") as store:
        a = freeze(store, [1])
        b = freeze(store, [1])
    assert a.bundle_hash == b.bundle_hash
    assert decision_hash(a, PARAMS, DECISION) == decision_hash(b, PARAMS, DECISION)


def test_hash_does_not_depend_on_key_order_of_params_or_item_status(tmp_path: Path) -> None:
    bundle = one_bundle(tmp_path)
    reordered_params = {"intent": "swing", "max_age_min": 30}
    reordered_status = dict(reversed(list(DECISION["item_status"].items())))
    other = {**DECISION, "item_status": reordered_status}
    assert params_hash(PARAMS) == params_hash(reordered_params)
    assert decision_hash(bundle, PARAMS, DECISION) == decision_hash(bundle, reordered_params, other)


# --- AC 2: satu byte payload berubah → hash berbeda -------------------------------------------
def test_one_byte_change_in_payload_changes_hash(tmp_path: Path) -> None:
    other = copy.deepcopy(ENVELOPE)
    other["data"]["price"] = 203.42  # 203.41 → 203.42: satu byte
    base = one_bundle(tmp_path, "a.db")
    changed = one_bundle(tmp_path, "b.db", envelope=other)
    assert len(base.entries[0].payload_json) == len(changed.entries[0].payload_json)
    assert base.bundle_hash != changed.bundle_hash
    assert decision_hash(base, PARAMS, DECISION) != decision_hash(changed, PARAMS, DECISION)


def test_ts_utc_and_status_are_part_of_the_hash(tmp_path: Path) -> None:
    base = one_bundle(tmp_path, "a.db")
    with Store(tmp_path / "b.db") as store:
        store.insert_snapshot("analyze_token", ARGS, ENVELOPE, ts_utc="2026-09-01T03:51:44.123456+00:00")
        later = freeze(store, [1])
    assert base.bundle_hash != later.bundle_hash


# --- AC 3: payload_hash tidak cocok isinya → ReplayTamperError menyebut snapshot_id ------------
def test_freeze_rejects_row_whose_payload_hash_does_not_match(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    with Store(path):
        pass
    snapshot_id = raw_insert(path, payload_hash="0" * 64)
    with Store(path, read_only=True) as store:
        with pytest.raises(ReplayTamperError) as exc:
            freeze(store, [snapshot_id])
    assert f"snapshot_id={snapshot_id}" in str(exc.value)
    assert "payload_hash" in str(exc.value)


def test_freeze_rejects_row_whose_payload_text_was_swapped(tmp_path: Path) -> None:
    """Kasus nyata tamper (a): isi diubah, hash lama ditinggalkan."""
    path = tmp_path / "t.db"
    with Store(path):
        pass
    tampered = canonical_json(ENVELOPE).replace("203.41", "999.99")
    snapshot_id = raw_insert(path, payload_json=tampered)
    with Store(path, read_only=True) as store:
        with pytest.raises(ReplayTamperError, match=f"snapshot_id={snapshot_id}"):
            freeze(store, [snapshot_id])


def test_freeze_rejects_row_whose_args_hash_does_not_match(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    with Store(path):
        pass
    snapshot_id = raw_insert(path, args_json='{"symbol":"BONK"}')
    with Store(path, read_only=True) as store:
        with pytest.raises(ReplayTamperError) as exc:
            freeze(store, [snapshot_id])
    assert f"snapshot_id={snapshot_id}" in str(exc.value)
    assert "args_hash" in str(exc.value)


def test_tamper_message_names_the_offending_row_not_the_first_one(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    with make_store(path) as store:
        good = store.get_snapshots([1])[0].id
    bad = raw_insert(path, payload_hash="1" * 64)
    with Store(path, read_only=True) as store:
        with pytest.raises(ReplayTamperError) as exc:
            freeze(store, [bad, good])
    assert f"snapshot_id={bad}" in str(exc.value)
    assert f"snapshot_id={good}" not in str(exc.value)


# --- AC 4: narasi tidak mengubah decision_hash (kontrak untuk TASKS 2.3) ----------------------
@pytest.mark.parametrize(
    "extra",
    [
        {"narration": "Likuiditas tipis; tidak berangkat."},
        {"summary": "NO_GO — 2 item merah"},
        {"card_json": {"rows": [{"item": "liquidity", "say": "tipis"}]}},
        {"narration": None, "summary": "", "card_json": {}, "ts_utc": "2026-09-01T04:00:00Z"},
    ],
)
def test_extra_keys_in_decision_do_not_change_the_hash(tmp_path: Path, extra: dict[str, Any]) -> None:
    bundle = one_bundle(tmp_path)
    assert decision_hash(bundle, PARAMS, DECISION) == decision_hash(bundle, PARAMS, {**DECISION, **extra})


def test_blocking_order_and_duplicates_do_not_change_the_hash(tmp_path: Path) -> None:
    bundle = one_bundle(tmp_path)
    shuffled = {**DECISION, "blocking": ["freshness", "liquidity", "freshness"]}
    assert decision_hash(bundle, PARAMS, DECISION) == decision_hash(bundle, PARAMS, shuffled)


def test_decision_verdict_blocking_and_item_status_do_change_the_hash(tmp_path: Path) -> None:
    bundle = one_bundle(tmp_path)
    base = decision_hash(bundle, PARAMS, DECISION)
    verdict = decision_hash(bundle, PARAMS, {**DECISION, "decision": "GO"})
    blocking = decision_hash(bundle, PARAMS, {**DECISION, "blocking": ["liquidity"]})
    status = decision_hash(
        bundle, PARAMS, {**DECISION, "item_status": {**DECISION["item_status"], "funding": "green"}}
    )
    assert len({base, verdict, blocking, status}) == 4


def test_params_change_the_hash(tmp_path: Path) -> None:
    bundle = one_bundle(tmp_path)
    other = {**PARAMS, "max_age_min": 31}
    assert params_hash(PARAMS) != params_hash(other)
    assert decision_hash(bundle, PARAMS, DECISION) != decision_hash(bundle, other, DECISION)


# --- determinisme lintas proses ---------------------------------------------------------------
def _hash_script(db: Path) -> str:
    return textwrap.dedent(
        f"""
        from core.replay import decision_hash, freeze, params_hash
        from core.store import Store
        with Store({str(db)!r}, read_only=True) as store:
            bundle = freeze(store, [1])
        print(bundle.bundle_hash)
        print(params_hash({PARAMS!r}))
        print(decision_hash(bundle, {PARAMS!r}, {DECISION!r}))
        """
    )


def test_hashes_are_identical_across_processes_with_different_hash_seeds(tmp_path: Path) -> None:
    db = tmp_path / "seed.db"
    with make_store(db):
        pass
    script = tmp_path / "run.py"
    script.write_text(_hash_script(db), encoding="utf-8")
    outputs = []
    for seed in ("0", "1", "12345"):
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(ROOT),
                "LC_ALL": "C.UTF-8",
                "PYTHONPATH": str(ROOT),
                "PYTHONHASHSEED": seed,
            },
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        outputs.append(proc.stdout)
    assert len(set(outputs)) == 1
    with make_store(tmp_path / "inproc.db") as store:
        bundle = freeze(store, [1])
    assert outputs[0].splitlines() == [
        bundle.bundle_hash,
        params_hash(PARAMS),
        decision_hash(bundle, PARAMS, DECISION),
    ]


# --- portabilitas: `id` tidak ikut di-hash ------------------------------------------------------
def test_same_rows_with_different_ids_give_the_same_decision_hash(tmp_path: Path) -> None:
    left = one_bundle(tmp_path, "left.db")
    right = one_bundle(tmp_path, "right.db", filler=7)
    assert left.snapshot_ids != right.snapshot_ids
    assert left.entries[0].snapshot_id == 1 and right.entries[-1].snapshot_id == 8
    assert left.bundle_hash != right.bundle_hash  # bundle kanan memuat 7 baris filler juga

    with Store(tmp_path / "right.db", read_only=True) as store:
        only_analyze = freeze(store, [8])
    assert only_analyze.snapshot_ids == (8,)
    assert left.snapshot_ids == (1,)
    assert only_analyze.bundle_hash == left.bundle_hash
    assert decision_hash(only_analyze, PARAMS, DECISION) == decision_hash(left, PARAMS, DECISION)


def test_bundle_material_shape_is_pinned(tmp_path: Path) -> None:
    """Bentuk material hash ditulis ulang di sini supaya perubahannya tidak bisa senyap."""
    bundle = one_bundle(tmp_path)
    entry = bundle.entries[0]
    material = {
        "algo": ALGO,
        "snapshots": [
            {
                "args_hash": entry.args_hash,
                "args_json": entry.args_json,
                "as_of": entry.as_of,
                "payload_hash": entry.payload_hash,
                "payload_json": entry.payload_json,
                "status": entry.status,
                "tool": entry.tool,
                "ts_utc": entry.ts_utc,
            }
        ],
    }
    text = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert '"id"' not in text and '"snapshot_id"' not in text
    assert bundle.bundle_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()

    decision_material = {
        "algo": ALGO,
        "bundle_hash": bundle.bundle_hash,
        "decision": {
            "blocking": ["freshness", "liquidity"],
            "decision": "NO_GO",
            "item_status": DECISION["item_status"],
        },
        "params_hash": params_hash(PARAMS),
    }
    expected = hashlib.sha256(
        json.dumps(decision_material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert decision_hash(bundle, PARAMS, DECISION) == expected


def test_params_hash_material_shape_is_pinned() -> None:
    text = json.dumps(
        {"algo": ALGO, "params": PARAMS}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert params_hash(PARAMS) == hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- verify_bundle -------------------------------------------------------------------------------
def test_verify_bundle_returns_the_hash_of_an_untouched_bundle(tmp_path: Path) -> None:
    bundle = one_bundle(tmp_path)
    assert verify_bundle(bundle) == bundle.bundle_hash


def test_verify_bundle_rejects_bundle_edited_after_freeze(tmp_path: Path) -> None:
    """Entri tetap konsisten sendiri; yang tidak cocok adalah bundle_hash → bukan snapshot_id."""
    bundle = one_bundle(tmp_path)
    swapped = canonical_json(ENVELOPE).replace("203.41", "999.99")
    entry = dataclasses.replace(
        bundle.entries[0],
        payload_json=swapped,
        payload_hash=hashlib.sha256(swapped.encode()).hexdigest(),
    )
    edited = dataclasses.replace(bundle, entries=(entry,))
    with pytest.raises(ReplayTamperError) as exc:
        verify_bundle(edited)
    assert "bundle_hash" in str(exc.value)
    assert "snapshot_id" not in str(exc.value)
    with pytest.raises(ReplayTamperError):
        decision_hash(edited, PARAMS, DECISION)


def test_verify_bundle_rejects_entry_that_no_longer_matches_its_own_hash(tmp_path: Path) -> None:
    bundle = one_bundle(tmp_path)
    entry = dataclasses.replace(bundle.entries[0], payload_json='{"status":"ok"}')
    with pytest.raises(ReplayTamperError, match=f"snapshot_id={entry.snapshot_id}"):
        verify_bundle(dataclasses.replace(bundle, entries=(entry,)))


def test_verify_bundle_rejects_reordered_or_duplicated_entries(tmp_path: Path) -> None:
    with make_store(tmp_path / "a.db", filler=1) as store:
        bundle = freeze(store, [1, 2])
    with pytest.raises(ReplayTamperError, match="urut id menaik"):
        verify_bundle(dataclasses.replace(bundle, entries=tuple(reversed(bundle.entries))))
    with pytest.raises(ReplayTamperError, match="urut id menaik"):
        verify_bundle(dataclasses.replace(bundle, entries=(bundle.entries[0], bundle.entries[0])))


def test_verify_bundle_reports_foreign_algo_as_error_not_tamper(tmp_path: Path) -> None:
    """TASKS 2.4 memakai ini untuk melaporkan STALE_ALGO, bukan menuduh tamper."""
    bundle = one_bundle(tmp_path)
    stale = dataclasses.replace(bundle, algo="tenko-decision-hash/0")
    with pytest.raises(ReplayError) as exc:
        verify_bundle(stale)
    assert not isinstance(exc.value, ReplayTamperError)
    assert "tenko-decision-hash/0" in str(exc.value) and ALGO in str(exc.value)


def test_bundle_entries_are_frozen(tmp_path: Path) -> None:
    bundle = one_bundle(tmp_path)
    assert all(isinstance(entry, BundleEntry) for entry in bundle.entries)
    with pytest.raises(dataclasses.FrozenInstanceError):
        bundle.entries[0].payload_json = "{}"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        bundle.bundle_hash = "0" * 64  # type: ignore[misc]


@pytest.mark.parametrize("bad", [None, "bundle", {"entries": []}, 7])
def test_verify_bundle_rejects_non_bundle(bad: Any) -> None:
    with pytest.raises(ReplayError, match="harus Bundle"):
        verify_bundle(bad)


def test_verify_bundle_rejects_entries_that_are_not_bundle_entries(tmp_path: Path) -> None:
    bundle = one_bundle(tmp_path)
    fake = {field: getattr(bundle.entries[0], field) for field in ("tool", "payload_json")}
    with pytest.raises(ReplayError, match="tuple BundleEntry"):
        verify_bundle(dataclasses.replace(bundle, entries=(fake,)))
    with pytest.raises(ReplayError, match="tuple BundleEntry"):
        verify_bundle(dataclasses.replace(bundle, entries=list(bundle.entries)))


# --- normalisasi snapshot_ids ---------------------------------------------------------------------
def test_shuffled_and_duplicated_ids_give_the_same_bundle(tmp_path: Path) -> None:
    with make_store(tmp_path / "a.db", filler=3) as store:
        ordered = freeze(store, [1, 2, 3, 4])
        shuffled = freeze(store, [4, 1, 3, 2, 4, 1])
    assert ordered.snapshot_ids == shuffled.snapshot_ids == (1, 2, 3, 4)
    assert ordered.bundle_hash == shuffled.bundle_hash
    assert decision_hash(ordered, PARAMS, DECISION) == decision_hash(shuffled, PARAMS, DECISION)


def test_missing_snapshot_id_is_named_and_fails_loudly(tmp_path: Path) -> None:
    with make_store(tmp_path / "a.db") as store:
        with pytest.raises(ReplayMissingSnapshotError) as exc:
            freeze(store, [1, 42])
    assert "42" in str(exc.value)


def test_empty_snapshot_ids_freeze_to_an_empty_but_valid_bundle(tmp_path: Path) -> None:
    """NO_GO ketika semua tool gagal memang tidak punya bukti; itu bukan error."""
    with make_store(tmp_path / "a.db") as store:
        bundle = freeze(store, [])
    assert bundle.entries == () and bundle.snapshot_ids == ()
    assert verify_bundle(bundle) == bundle.bundle_hash
    assert decision_hash(bundle, PARAMS, DECISION) != ""


@pytest.mark.parametrize("bad", ["1", b"1", 7, None])
def test_freeze_rejects_ids_that_are_not_a_list(tmp_path: Path, bad: Any) -> None:
    with make_store(tmp_path / "a.db") as store:
        with pytest.raises(ReplayError, match="snapshot_ids harus daftar"):
            freeze(store, bad)


@pytest.mark.parametrize("bad", [["1"], [1.5], [None], [True]])
def test_freeze_rejects_ids_that_are_not_integers(tmp_path: Path, bad: Any) -> None:
    with make_store(tmp_path / "a.db") as store:
        with pytest.raises(ReplayError, match="bilangan bulat"):
            freeze(store, bad)


# --- validasi decision/params ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("bad", "match"),
    [
        ({"blocking": [], "item_status": {}}, "kekurangan kunci"),
        ({"decision": "MAYBE", "blocking": [], "item_status": {}}, "GO"),
        ({"decision": "GO", "blocking": "liquidity", "item_status": {}}, "blocking harus daftar"),
        ({"decision": "GO", "blocking": [7], "item_status": {}}, "id item"),
        ({"decision": "GO", "blocking": [], "item_status": []}, "item_status harus objek"),
        ({"decision": "GO", "blocking": [], "item_status": {"a": "yellow"}}, "item_status"),
        ({"decision": "GO", "blocking": [], "item_status": {"a": None}}, "item_status"),
    ],
)
def test_decision_hash_rejects_malformed_decision(tmp_path: Path, bad: dict[str, Any], match: str) -> None:
    bundle = one_bundle(tmp_path)
    with pytest.raises(ReplayError, match=match):
        decision_hash(bundle, PARAMS, bad)


@pytest.mark.parametrize("bad", [None, "swing", [1, 2], 7])
def test_params_hash_rejects_non_mapping(bad: Any) -> None:
    with pytest.raises(ReplayError, match="params harus objek"):
        params_hash(bad)


def test_params_hash_rejects_values_that_cannot_be_canonicalised() -> None:
    with pytest.raises(ReplayError, match="tidak bisa dikanonikalisasi"):
        params_hash({"threshold": float("nan")})
    with pytest.raises(ReplayError, match="tidak bisa dikanonikalisasi"):
        params_hash({"store": object()})


# --- lone surrogate: jalur yang benar-benar ada (argv), bukan kolom DB ---------------------------
def test_lone_surrogate_in_params_and_decision_still_hashes(tmp_path: Path) -> None:
    """`sys.argv` memakai surrogateescape, jadi simbol dari CLI bisa membawa `\\udcff`."""
    bundle = one_bundle(tmp_path)
    weird = {"symbol": "SOL\udcff"}
    first = params_hash(weird)
    assert first == params_hash({"symbol": "SOL\udcff"})
    assert first != params_hash({"symbol": "SOL"})
    assert decision_hash(bundle, weird, DECISION) == decision_hash(bundle, dict(weird), DECISION)
    assert decision_hash(bundle, weird, DECISION) != decision_hash(bundle, PARAMS, DECISION)


def test_lone_surrogate_cannot_reach_a_snapshot_row_at_all(tmp_path: Path) -> None:
    """Karena itu `_sha256_text` toleran demi params/argv, bukan demi kolom DB."""
    with make_store(tmp_path / "a.db") as store:
        with pytest.raises(StoreError):
            store.insert_snapshot("analyze_token", ARGS, {**ENVELOPE, "note": "\ud800"})
    with pytest.raises(UnicodeEncodeError):
        raw_insert(tmp_path / "a.db", payload_json="\ud800")


# --- payload_json dipakai apa adanya, bukan hasil round-trip -------------------------------------
def test_non_canonical_payload_text_is_hashed_verbatim(tmp_path: Path) -> None:
    """Round-trip `1E+2` → `100.0` akan menggeser hash; teks dari DB harus dipakai apa adanya."""
    path = tmp_path / "t.db"
    with Store(path):
        pass
    text = '{"a":1E+2,"z":1,"b":{"k":1.0}}'
    snapshot_id = raw_insert(path, payload_json=text, payload_hash=hashlib.sha256(text.encode()).hexdigest())
    with Store(path, read_only=True) as store:
        bundle = freeze(store, [snapshot_id])
    assert bundle.entries[0].payload_json == text
    assert verify_bundle(bundle) == bundle.bundle_hash

    # Nilai JSON-nya SAMA, teksnya beda: kalau freeze me-round-trip payload, kedua hash ini
    # akan sama dan tamper tekstual jadi tak terlihat.
    roundtripped = canonical_json(json.loads(text))
    assert roundtripped != text
    other_id = raw_insert(
        path, payload_json=roundtripped, payload_hash=hashlib.sha256(roundtripped.encode()).hexdigest()
    )
    with Store(path, read_only=True) as store:
        other = freeze(store, [other_id])
    assert other.bundle_hash != bundle.bundle_hash


# --- arah dependency ------------------------------------------------------------------------------
def test_replay_does_not_import_layers_above_core() -> None:
    source = (ROOT / "core" / "replay.py").read_text(encoding="utf-8")
    for forbidden in ("preflight", "grading", "agent", "ui", "fastapi", "httpx"):
        assert f"import {forbidden}" not in source and f"from {forbidden}" not in source


# --- batas yang DIDOKUMENTASIKAN: konsistensi-diri, bukan asal-usul ---------------------------
def test_verify_bundle_accepts_a_handmade_bundle_that_never_touched_the_store() -> None:
    """Batas jujur (docstring modul): verify_bundle tidak membuktikan asal-usul bukti.

    Tes ini MEMATOK batas itu supaya siapa pun yang kelak membangun `Bundle` dari JSON tahu
    bahwa `freeze` adalah satu-satunya konstruktor yang sah.
    """
    payload = '{"data":{"price":999999.0},"status":"ok"}'
    entry = BundleEntry(
        snapshot_id=1,
        tool="analyze_token",
        args_json=canonical_json(ARGS),
        args_hash=hashlib.sha256(canonical_json(ARGS).encode()).hexdigest(),
        ts_utc=TS,
        as_of=ENVELOPE["as_of"],
        status="ok",
        payload_json=payload,
        payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
    )
    material = {
        "algo": ALGO,
        "snapshots": [
            {
                "args_hash": entry.args_hash,
                "args_json": entry.args_json,
                "as_of": entry.as_of,
                "payload_hash": entry.payload_hash,
                "payload_json": entry.payload_json,
                "status": entry.status,
                "tool": entry.tool,
                "ts_utc": entry.ts_utc,
            }
        ],
    }
    text = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    forged = Bundle(algo=ALGO, entries=(entry,), bundle_hash=hashlib.sha256(text.encode()).hexdigest())
    assert verify_bundle(forged) == forged.bundle_hash


def test_snapshot_id_is_not_protected_by_the_hash(tmp_path: Path) -> None:
    """`snapshot_id` = petunjuk pencarian, bukan bukti; 2.4 wajib mencocokkannya dengan DB."""
    bundle = one_bundle(tmp_path)
    relabelled = dataclasses.replace(
        bundle, entries=(dataclasses.replace(bundle.entries[0], snapshot_id=987),)
    )
    assert relabelled.snapshot_ids == (987,) and bundle.snapshot_ids == (1,)
    assert verify_bundle(relabelled) == bundle.bundle_hash
    assert decision_hash(relabelled, PARAMS, DECISION) == decision_hash(bundle, PARAMS, DECISION)


# --- tipe salah keluar sebagai ReplayError, bukan traceback (spec §7) --------------------------
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("payload_json", 123),
        ("args_json", None),
        ("tool", b"analyze_token"),
        ("ts_utc", 17),
        ("payload_hash", 0),
        ("as_of", 7),
        ("status", ["ok"]),
        ("snapshot_id", "1"),
        ("snapshot_id", True),
    ],
)
def test_bundle_entry_with_wrong_field_type_is_a_replay_error(tmp_path: Path, field: str, value: Any) -> None:
    bundle = one_bundle(tmp_path)
    broken = dataclasses.replace(bundle, entries=(dataclasses.replace(bundle.entries[0], **{field: value}),))
    with pytest.raises(ReplayError) as exc:
        verify_bundle(broken)
    assert not isinstance(exc.value, (AttributeError, TypeError))
    assert field in str(exc.value)


def test_none_is_allowed_for_the_two_nullable_columns(tmp_path: Path) -> None:
    """`as_of`/`status` memang nullable di skema: envelope tanpa keduanya harus tetap beku."""
    bare = {"data": {"price": 203.41}, "warnings": []}
    with Store(tmp_path / "n.db") as store:
        store.insert_snapshot("analyze_token", ARGS, bare, ts_utc=TS)
        bundle = freeze(store, [1])
    assert bundle.entries[0].as_of is None and bundle.entries[0].status is None
    assert verify_bundle(bundle) == bundle.bundle_hash
    assert decision_hash(bundle, PARAMS, DECISION) != ""


@pytest.mark.parametrize("bad", [{"status": "red"}, ["red"], {"a", "b"}, 1, None, b"red"])
def test_unhashable_item_status_value_is_a_replay_error_not_a_typeerror(tmp_path: Path, bad: Any) -> None:
    """2.1 sangat mungkin mengoper `ItemResult` mentah alih-alih `.status`."""
    bundle = one_bundle(tmp_path)
    decision = {**DECISION, "item_status": {"liquidity": bad}}
    with pytest.raises(ReplayError, match="item_status"):
        decision_hash(bundle, PARAMS, decision)


@pytest.mark.parametrize("bad", [{"decision": "GO"}, ["GO"], {"GO"}, None, 1])
def test_unhashable_verdict_is_a_replay_error_not_a_typeerror(tmp_path: Path, bad: Any) -> None:
    bundle = one_bundle(tmp_path)
    with pytest.raises(ReplayError, match="decision harus"):
        decision_hash(bundle, PARAMS, {**DECISION, "decision": bad})


# --- kunci non-str di params: JSON memaksanya jadi str, jadi ditolak di batas ------------------
@pytest.mark.parametrize(
    "bad",
    [
        {1: "a"},
        {True: "x"},
        {None: "a"},
        {2.5: "a"},
        {"nested": {1: "a"}},
        {"list": [{"deeper": {False: 1}}]},
    ],
)
def test_params_hash_rejects_non_string_keys(bad: dict[Any, Any]) -> None:
    with pytest.raises(ReplayError, match="kunci params"):
        params_hash(bad)


def test_non_string_keys_would_have_collided_with_their_string_form() -> None:
    """Alasan penolakan: `json.dumps` memaksa `1` → `"1"`, jadi dua params beda jadi satu hash."""
    assert json.dumps({1: "a"}, sort_keys=True) == json.dumps({"1": "a"}, sort_keys=True)
    with pytest.raises(ReplayError):
        params_hash({1: "a"})
    assert params_hash({"1": "a"}) != params_hash({"true": "x"})


def test_decision_hash_rejects_non_string_keys_in_params(tmp_path: Path) -> None:
    bundle = one_bundle(tmp_path)
    with pytest.raises(ReplayError, match="kunci params"):
        decision_hash(bundle, {"limits": {1: 2}}, DECISION)
