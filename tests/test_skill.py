"""Tes TASKS 1.3 — kontrak `core/skill.py` (SkillResult/ItemResult + validator).

Acuan: docs/spec.md §3 (kontrak skill), ADR-013 (bentuk = envelope RYO), ADR-015 (usulan:
`schema_version="1.0"` mengikuti sampel nyata; `simulated` satu-satunya field tambahan),
docs/api-facts.md §A (envelope). Semua fakta bentuk dibaca dari `docs/samples/*.json`,
tidak di-hardcode.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from core import skill as skill_module
from core.skill import (
    SCHEMA_VERSION,
    ItemResult,
    ItemSource,
    SkillError,
    SkillResult,
    derive_data_mode,
    derive_status,
    load_skill,
    register_skill,
    skill_names,
)
from core.skill import _reset_registry as reset_registry

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "docs" / "samples"


def envelope(name: str) -> dict[str, Any]:
    """Envelope RYO nyata dari docs/samples (TASKS 0.3)."""
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))["result"]


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


# --- AC 1: bentuk top-level = envelope RYO + tepat satu field ----------------------------
def test_top_level_fields_are_ryo_envelope_plus_simulated() -> None:
    ryo_top_level = set(envelope("analyze_token"))
    ours = set(SkillResult.model_fields)
    assert ryo_top_level <= ours
    assert ours - ryo_top_level == {"simulated"}


def test_top_level_invariant_holds_for_all_six_samples() -> None:
    """Keenam sampel berbagi sepuluh field yang sama — invariannya bukan kebetulan satu file."""
    tools = [
        "market_overview",
        "scan_market",
        "analyze_token",
        "deep_analysis",
        "compare_tokens",
        "monitor_market_sentiment_shift",
    ]
    ours = set(SkillResult.model_fields)
    for tool in tools:
        assert ours - set(envelope(tool)) == {"simulated"}, tool


def test_schema_version_follows_live_samples() -> None:
    """ADR-015: `"1.0"` (sampel nyata), bukan `"1"` yang tertulis di spec §3."""
    assert SCHEMA_VERSION == envelope("analyze_token")["schema_version"] == "1.0"
    assert SkillResult.unavailable("preflight").schema_version == "1.0"


def test_field_order_mirrors_ryo_envelope() -> None:
    ryo_order = [k for k in envelope("analyze_token")]
    ours = [k for k in SkillResult.model_fields if k != "simulated"]
    assert ours == ryo_order


def test_simulated_is_always_true() -> None:
    """docs/rules-compliance.md: tidak boleh ada respons yang menyamar sebagai nyata."""
    assert SkillResult.unavailable("preflight").simulated is True
    with pytest.raises(ValidationError):
        SkillResult(tool="preflight", status="unavailable", simulated=False)


def test_extra_top_level_fields_are_rejected() -> None:
    """Penambahan field top-level butuh ADR sendiri (ADR-015)."""
    with pytest.raises(ValidationError):
        SkillResult(tool="preflight", status="unavailable", decision="GO")


# --- AC 2: satu tool partial → status partial, item terkait unspoken ---------------------
def _preflight_like(envelopes: list[dict[str, Any]], items: list[ItemResult]) -> SkillResult:
    return SkillResult.from_items(
        tool="preflight",
        request={"symbol": "SOL"},
        items=items,
        envelopes=envelopes,
    )


def test_partial_tool_yields_partial_result_with_unspoken_item() -> None:
    deep = envelope("deep_analysis")
    assert deep["status"] == "partial" and deep["availability"]["derivatives"] == "unavailable"
    items = [
        ItemResult(
            id="verdict_quick",
            status="green",
            value="accumulate",
            source=ItemSource(tool="analyze_token", field="data.verdict", snapshot_id=1),
            reason="verdict searah plan",
        ),
        ItemResult(
            id="derivatives",
            status="unspoken",
            source=ItemSource(tool="deep_analysis", field="data.derivatives", snapshot_id=2),
            reason="availability.derivatives=unavailable",
        ),
    ]
    result = _preflight_like([envelope("analyze_token"), deep], items)
    assert result.status == "partial"
    assert result.availability["derivatives"] == "unspoken"
    assert result.availability["verdict_quick"] == "available"
    assert result.data is not None
    assert [i["id"] for i in result.data["items"]] == ["verdict_quick", "derivatives"]
    assert result.data["items"][1]["value"] is None


def test_as_of_is_oldest_evidence_never_now() -> None:
    """Kesegaran dilaporkan konservatif: as_of tertua dari bukti, bukan jam pemanggilan."""
    result = _preflight_like(
        [envelope("analyze_token"), envelope("compare_tokens")],
        [ItemResult(id="a", status="green", value=1)],
    )
    assert result.as_of == datetime(2026, 9, 1, 3, 47, 14, tzinfo=UTC)


def test_ok_only_when_every_tool_is_ok() -> None:
    result = _preflight_like(
        [envelope("analyze_token"), envelope("market_overview")],
        [ItemResult(id="a", status="green", value=1)],
    )
    assert result.status == "ok"
    assert result.data_mode == "live"


# --- AC 3: tidak ada tool yang menjawab → unavailable & data None ------------------------
def test_no_tool_answers_gives_unavailable_and_no_data() -> None:
    result = SkillResult.from_items(
        tool="preflight",
        request={"symbol": "SOL"},
        items=[ItemResult(id="a", status="unspoken", reason="tool gagal")],
        envelopes=[],
        warnings=["all source tools failed"],
    )
    assert result.status == "unavailable"
    assert result.data is None
    assert result.as_of is None
    assert result.data_mode == "unknown"
    assert result.availability == {"a": "unspoken"}


def test_all_tools_unavailable_gives_unavailable() -> None:
    dead = dict(envelope("analyze_token"), status="unavailable", data=None)
    result = SkillResult.from_items(
        tool="preflight",
        request={"symbol": "SOL"},
        items=[ItemResult(id="a", status="unspoken", reason="tool unavailable")],
        envelopes=[dead],
    )
    assert result.status == "unavailable"
    assert result.data is None


def test_from_items_refuses_spoken_item_without_any_answer() -> None:
    """Kontradiksi = bug pemanggil, bukan data: gagal keras, jangan diam-diam dibuang."""
    with pytest.raises(SkillError):
        SkillResult.from_items(
            tool="preflight",
            request={},
            items=[ItemResult(id="a", status="green", value=1)],
            envelopes=[],
        )


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "unavailable"),
        (["ok"], "ok"),
        (["ok", "ok"], "ok"),
        (["ok", "partial"], "partial"),
        (["partial"], "partial"),
        (["ok", "unavailable"], "partial"),
        (["unavailable"], "unavailable"),
        (["unavailable", "unavailable"], "unavailable"),
    ],
)
def test_derive_status_matches_spec_rules(statuses: list[str], expected: str) -> None:
    assert derive_status(statuses) == expected


def test_derive_status_rejects_unknown_vocabulary() -> None:
    with pytest.raises(SkillError):
        derive_status(["degraded"])


@pytest.mark.parametrize(
    ("modes", "expected"),
    [
        ([], "unknown"),
        (["live"], "live"),
        (["live", "live"], "live"),
        (["live", "simulated"], "mixed"),
        (["live", None], "mixed"),
        ([None], "unknown"),
        (["nonsense"], "unknown"),
    ],
)
def test_derive_data_mode(modes: list[str | None], expected: str) -> None:
    assert derive_data_mode(modes) == expected


# --- AC 4: item unspoken tidak boleh membawa angka ---------------------------------------
@pytest.mark.parametrize("value", [1.0, 0, 0.0, "", False, {"rr": 1.8}, []])
def test_unspoken_item_may_not_carry_a_value(value: Any) -> None:
    with pytest.raises(ValidationError):
        ItemResult(id="derivatives", status="unspoken", value=value)


def test_unspoken_item_without_value_is_valid() -> None:
    item = ItemResult(id="derivatives", status="unspoken", reason="null di sumber")
    assert item.value is None


def test_spoken_item_may_carry_a_value() -> None:
    assert ItemResult(id="rr", status="green", value=1.8).value == 1.8


def test_unspoken_value_rejected_inside_data_items() -> None:
    """Jalur masuk lewat `data` mentah divalidasi sama ketatnya."""
    with pytest.raises(ValidationError):
        SkillResult(
            tool="preflight",
            status="partial",
            data={"items": [{"id": "derivatives", "status": "unspoken", "value": 0}]},
            availability={"derivatives": "unspoken"},
        )


# --- availability dua arah ---------------------------------------------------------------
def test_unspoken_item_must_be_marked_unspoken_in_availability() -> None:
    with pytest.raises(ValidationError):
        SkillResult(
            tool="preflight",
            status="partial",
            data={"items": [{"id": "derivatives", "status": "unspoken", "value": None}]},
            availability={"derivatives": "available"},
        )


def test_spoken_item_may_not_be_marked_unspoken_in_availability() -> None:
    with pytest.raises(ValidationError):
        SkillResult(
            tool="preflight",
            status="ok",
            data={"items": [{"id": "rr", "status": "green", "value": 1.8}]},
            availability={"rr": "unspoken"},
        )


def test_every_item_needs_an_availability_entry() -> None:
    with pytest.raises(ValidationError):
        SkillResult(
            tool="preflight",
            status="ok",
            data={"items": [{"id": "rr", "status": "green", "value": 1.8}]},
            availability={},
        )


def test_availability_may_also_describe_tool_sections() -> None:
    """RYO memakai availability untuk seksi (mis. `token_profile`); itu tidak dilarang."""
    result = SkillResult(
        tool="preflight",
        status="partial",
        data={"items": [{"id": "rr", "status": "green", "value": 1.8}]},
        availability={"rr": "available", "derivatives": "unavailable"},
    )
    assert result.availability["derivatives"] == "unavailable"


def test_duplicate_item_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillResult(
            tool="preflight",
            status="ok",
            data={
                "items": [
                    {"id": "rr", "status": "green", "value": 1.8},
                    {"id": "rr", "status": "red", "value": 0.4},
                ]
            },
            availability={"rr": "available"},
        )


def test_items_must_be_a_list_of_objects() -> None:
    with pytest.raises(ValidationError):
        SkillResult(tool="preflight", status="ok", data={"items": "rr"}, availability={})


# --- status unavailable ⇒ data None ------------------------------------------------------
def test_unavailable_with_data_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillResult(tool="preflight", status="unavailable", data={"items": []})


def test_unavailable_helper_is_honest() -> None:
    result = SkillResult.unavailable(
        "preflight",
        request={"symbol": "SOL"},
        warnings=["skill 'preflight' not registered"],
    )
    assert (result.status, result.data, result.as_of) == ("unavailable", None, None)
    assert result.data_mode == "unknown"
    assert result.warnings == ["skill 'preflight' not registered"]
    assert set(result.summary) == {"headline", "key_points"}
    assert result.summary["key_points"] == []


# --- waktu -------------------------------------------------------------------------------
def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillResult(tool="preflight", status="unavailable", as_of=datetime(2026, 9, 1, 3, 47, 14))


def test_as_of_serializes_like_ryo() -> None:
    ryo_as_of = envelope("analyze_token")["as_of"]
    result = SkillResult(tool="preflight", status="unavailable", as_of=ryo_as_of)
    assert result.model_dump(mode="json")["as_of"] == ryo_as_of


def test_non_utc_offset_is_normalised_to_utc() -> None:
    jst = timezone(timedelta(hours=9))
    stamp = datetime(2026, 9, 1, 12, 47, 14, tzinfo=jst)
    result = SkillResult(tool="preflight", status="unavailable", as_of=stamp)
    assert result.model_dump(mode="json")["as_of"] == "2026-09-01T03:47:14Z"


def test_item_source_as_of_serializes_like_ryo() -> None:
    item = ItemResult(
        id="rr",
        status="green",
        value=1.8,
        source=ItemSource(
            tool="deep_analysis",
            field="data.trade_plan",
            snapshot_id=7,
            as_of="2026-09-01T03:47:44Z",
        ),
    )
    assert item.model_dump(mode="json")["source"]["as_of"] == "2026-09-01T03:47:44Z"


# --- serialisasi -------------------------------------------------------------------------
def test_dump_is_json_serialisable_and_keeps_ten_ryo_fields() -> None:
    result = _preflight_like(
        [envelope("analyze_token")],
        [ItemResult(id="a", status="green", value=1)],
    )
    dumped = result.model_dump(mode="json")
    json.dumps(dumped)  # tidak boleh melempar
    assert set(envelope("analyze_token")) <= set(dumped)
    assert dumped["simulated"] is True


# --- registry ----------------------------------------------------------------------------
def test_registry_round_trip() -> None:
    def fake(symbol: str) -> SkillResult:
        return SkillResult.unavailable("fake", request={"symbol": symbol})

    register_skill("fake", fake)
    assert skill_names() == ("fake",)
    assert load_skill("fake") is fake
    assert load_skill("preflight") is None


def test_registry_refuses_duplicate_names() -> None:
    def fake(symbol: str) -> SkillResult:  # pragma: no cover - tidak dipanggil
        raise AssertionError

    register_skill("fake", fake)
    with pytest.raises(SkillError):
        register_skill("fake", fake)


def test_load_skill_imports_declared_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registry diisi oleh paket pemiliknya (TASKS 2.2), bukan oleh core yang mengimpor preflight."""
    monkeypatch.setitem(
        skill_module.SKILL_MODULES,
        "demo",
        "tests.helpers_skill_demo",
    )
    fn = load_skill("demo")
    assert fn is not None
    assert fn("SOL").tool == "demo"


def test_load_skill_reports_broken_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        skill_module.SKILL_MODULES,
        "demo",
        "tests.tidak_ada_modul_ini",
    )
    with pytest.raises(SkillError):
        load_skill("demo")


# --- ronde perbaikan 1.3: model beku & nilai yang tidak bisa dilaporkan jujur -------------
@pytest.mark.parametrize("model_kwargs", [{"status": "ok"}, {"tool": "lain"}, {"simulated": False}])
def test_skill_result_is_frozen(model_kwargs: dict[str, Any]) -> None:
    """Envelope tidak boleh ditambal setelah lolos validator (mis. `simulated=False`)."""
    result = SkillResult.unavailable("preflight")
    field, value = next(iter(model_kwargs.items()))
    with pytest.raises(ValidationError):
        setattr(result, field, value)


def test_item_result_is_frozen() -> None:
    item = ItemResult(id="rr", status="unspoken", reason="null di sumber")
    with pytest.raises(ValidationError):
        item.value = 1.8
    with pytest.raises(ValidationError):
        item.status = "green"


def test_item_source_is_frozen() -> None:
    source = ItemSource(tool="deep_analysis", field="data.trade_plan")
    with pytest.raises(ValidationError):
        source.snapshot_id = 7


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        {"rr": float("nan")},
        [1.0, float("inf")],
        {"plan": {"targets": [float("nan")]}},
    ],
)
def test_non_finite_value_is_refused_not_silently_nulled(value: Any) -> None:
    """pydantic `mode="json"` mengubah NaN jadi `null`: item hijau bernilai null = bohong."""
    with pytest.raises(ValidationError):
        ItemResult(id="rr", status="green", value=value)


def test_non_finite_inside_data_is_refused() -> None:
    with pytest.raises(ValidationError):
        SkillResult(
            tool="preflight",
            status="ok",
            data={"items": [{"id": "rr", "status": "green", "value": 1.8}], "plan": {"stop": float("nan")}},
            availability={"rr": "available"},
        )


def test_circular_data_is_refused() -> None:
    loop: dict[str, Any] = {"items": [{"id": "rr", "status": "green", "value": 1.8}]}
    loop["self"] = loop
    with pytest.raises(ValidationError):
        SkillResult(tool="preflight", status="ok", data=loop, availability={"rr": "available"})


def test_ryo_envelope_values_stay_representable() -> None:
    """Sanity: nilai dari sampel nyata tidak tersandung cek di atas."""
    item = ItemResult(id="verdict", status="green", value=envelope("analyze_token")["data"])
    assert item.value is not None


# --- simetri status ⇄ data ---------------------------------------------------------------
@pytest.mark.parametrize("status", ["ok", "partial"])
@pytest.mark.parametrize("data", [None, {}, {"items": []}])
def test_spoken_status_without_evidence_is_refused(status: str, data: Any) -> None:
    """ "ok tanpa satu pun bukti" adalah bentuk kebohongan yang paling mudah dibuat."""
    with pytest.raises(ValidationError):
        SkillResult(tool="preflight", status=status, data=data, availability={})


# --- presisi waktu (ADR-015 poin 5) ------------------------------------------------------
def test_as_of_is_truncated_to_seconds_like_ryo() -> None:
    """`snapshots.ts_utc` menyimpan mikrodetik; RYO tidak pernah memakainya."""
    stamp = datetime(2026, 9, 1, 3, 47, 12, 123456, tzinfo=UTC)
    result = SkillResult(tool="preflight", status="unavailable", as_of=stamp)
    assert result.model_dump(mode="json")["as_of"] == "2026-09-01T03:47:12Z"
    item = ItemResult(
        id="rr",
        status="green",
        value=1.8,
        source=ItemSource(tool="deep_analysis", field="data.trade_plan", as_of=stamp),
    )
    assert item.model_dump(mode="json")["source"]["as_of"] == "2026-09-01T03:47:12Z"


def test_sample_as_of_round_trips_unchanged() -> None:
    for tool in ("analyze_token", "deep_analysis", "compare_tokens"):
        raw = envelope(tool)["as_of"]
        result = SkillResult(tool="preflight", status="unavailable", as_of=raw)
        assert result.model_dump(mode="json")["as_of"] == raw
