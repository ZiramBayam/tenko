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
    HEADLINE_LIMIT,
    KEY_LIMIT,
    REASON_LIMIT,
    SCHEMA_VERSION,
    TEXT_LIMIT,
    TOOL_LIMIT,
    TRUNCATED_MARK,
    WARNING_LIMIT,
    ItemResult,
    ItemSource,
    SkillError,
    SkillResult,
    clean_text,
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


def test_acknowledged_gap_ok_without_items_key_passes_every_item_invariant() -> None:
    """BATAS YANG DIAKUI (TASKS 2.2a): `data` tanpa kunci `items` lolos SEMUA invarian item.

    Invarian 1/2 (`unspoken` tanpa value, `availability` dua arah) hanya berlaku untuk
    `data["items"][*]`; tanpa kunci itu, `_parsed_items` mengembalikan None dan validator
    berhenti lebih awal. Jadi angka agregat yang duduk di luar `items` — persis bentuk yang
    dirakit TASKS 2.2 (`plan`, `decision`) — TIDAK punya penjaga `n` di lapisan ini.

    Tes ini MEMATOK celah itu, bukan menyetujuinya sebagai selamanya: menutupnya adalah
    pekerjaan 2.2 (yang wajib menyediakan `n`-nya sendiri) dan mengubah desain "struktural"
    TASKS 1.3 harus terlihat sebagai kegagalan tes ini, bukan berubah senyap.
    """
    result = SkillResult(tool="t", status="ok", data={"foo": 1}, availability={})

    assert result.status == "ok"
    assert result.items == []  # nol item: tidak ada yang bisa dijaga invarian item
    assert result.availability == {}  # dan tidak ada entri availability yang dituntut
    assert result.data == {"foo": 1}  # angka agregat lewat apa adanya, tanpa `n`


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
        SkillResult(tool="preflight", status="unavailable", as_of=datetime(2026, 9, 1, 3, 47, 14))  # noqa: DTZ001 - naif ADALAH input yang diuji


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
    # Nama yang tidak ada di registry MAUPUN `SKILL_MODULES`: sejak TASKS 2.2 "preflight"
    # terdaftar lewat impor malas, jadi memakainya di sini hanya akan menguji urutan impor.
    assert load_skill("no_such_skill") is None


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


# --- TASKS 1.5(c): panjang & charset teks yang ikut ke envelope --------------------------
def test_tool_is_truncated_to_a_reportable_length() -> None:
    """Argumen 100 kB tidak boleh menjadi field `tool` 100 kB (temuan juri fase 1 no. 6)."""
    result = SkillResult(tool="a" * 100_000, status="unavailable")
    assert len(result.tool) <= TOOL_LIMIT + len("...(truncated)")
    assert result.tool.endswith("...(truncated)")
    assert len(json.dumps(result.model_dump(mode="json"))) < 4096


def test_headline_is_truncated_to_a_reportable_length() -> None:
    result = SkillResult(
        tool="preflight",
        status="unavailable",
        summary={"headline": "h" * 100_000, "key_points": []},
    )
    assert len(result.summary["headline"]) <= HEADLINE_LIMIT + len("...(truncated)")


def test_unavailable_constructor_truncates_both_tool_and_headline() -> None:
    """Jalur yang dipakai CLI: headline dirakit DARI `tool`, jadi keduanya harus dipotong."""
    result = SkillResult.unavailable("a" * 100_000)
    assert len(result.tool) <= TOOL_LIMIT + len("...(truncated)")
    assert len(result.summary["headline"]) <= HEADLINE_LIMIT + len("...(truncated)")


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("pre\x1b[2Jflight", "\\u001b"),
        ("pre\r\nflight", "\\u000d"),
        ("pre‮flight", "\\u202e"),
        ("pre\ud800flight", "\\ud800"),
    ],
)
def test_control_characters_in_tool_are_escaped_not_dropped(raw: str, escaped: str) -> None:
    """Escape yang TERLIHAT, bukan penghapusan diam-diam: pembaca berhak tahu ada yang aneh.

    Lone surrogate ikut karena `model_dump(mode="json")` melemparkan `UnicodeEncodeError`
    untuk itu — satu nama skill aneh akan menggagalkan perakitan SELURUH envelope.
    """
    result = SkillResult(tool=raw, status="unavailable")
    assert escaped in result.tool
    assert raw[3] not in result.tool
    json.dumps(result.model_dump(mode="json"))


def test_control_characters_in_headline_are_escaped() -> None:
    result = SkillResult(
        tool="preflight",
        status="unavailable",
        summary={"headline": "GO\x1b[31m‮", "key_points": []},
    )
    assert "\x1b" not in result.summary["headline"]
    assert "\\u202e" in result.summary["headline"]


def test_non_string_headline_is_left_alone() -> None:
    """Hanya teks yang dibersihkan; bentuk lain bukan urusan sanitasi ini."""
    result = SkillResult(tool="preflight", status="unavailable", summary={"headline": None})
    assert result.summary["headline"] is None


def test_clean_text_is_idempotent() -> None:
    """Dipanggil dua kali (CLI lalu validator) tidak boleh mengubah hasil."""
    once = clean_text("pre\x1b[2Jflight" + "z" * 500, TOOL_LIMIT)
    assert clean_text(once, TOOL_LIMIT) == once


# --- TASKS 1.5(c) ronde 2: batas berlaku di MODEL, bukan hanya di CLI --------------------
# Temuan security-reviewer: `clean_text` semula hanya dipasang di `tool`/`summary.headline`,
# sedangkan `request`/`warnings`/`availability` dibiarkan. Skill 2.2 merakit envelope-nya
# SENDIRI, jadi pembatasan sisi CLI dibuang begitu skill mengembalikan envelope — envelope
# stdout terukur 300.289 byte dengan pola `tests/helpers_skill_live.py`.
def test_request_values_are_bounded_at_the_model() -> None:
    result = SkillResult.unavailable("preflight", request={"symbol": "a" * 100_000})
    assert len(result.request["symbol"]) <= TEXT_LIMIT + len(TRUNCATED_MARK)


def test_request_keys_are_bounded_at_the_model() -> None:
    result = SkillResult.unavailable("preflight", request={"k" * 100_000: "SOL"})
    assert max(len(key) for key in result.request) <= KEY_LIMIT + len(TRUNCATED_MARK)


def test_nested_request_values_are_bounded() -> None:
    """`request` skill bisa bersarang (mis. daftar simbol); satu level saja tidak cukup."""
    result = SkillResult.unavailable(
        "preflight", request={"peers": ["b" * 100_000], "opts": {"note": "c" * 100_000}}
    )
    assert len(result.request["peers"][0]) <= TEXT_LIMIT + len(TRUNCATED_MARK)
    assert len(result.request["opts"]["note"]) <= TEXT_LIMIT + len(TRUNCATED_MARK)


def test_warnings_are_bounded_at_the_model() -> None:
    result = SkillResult.unavailable("preflight", warnings=["d" * 100_000])
    assert len(result.warnings[0]) <= WARNING_LIMIT + len(TRUNCATED_MARK)


def test_summary_key_points_are_bounded() -> None:
    result = SkillResult.unavailable("preflight")
    patched = SkillResult.model_validate(
        {**result.model_dump(), "summary": {"headline": "h", "key_points": ["e" * 100_000]}}
    )
    assert len(patched.summary["key_points"][0]) <= TEXT_LIMIT + len(TRUNCATED_MARK)


def test_availability_keys_are_bounded_and_still_match_item_ids() -> None:
    """Kunci `availability` dan `ItemResult.id` dibersihkan dengan batas yang SAMA.

    Kalau tidak, invarian dua arah (`availability[item.id]`) akan pecah untuk id panjang.
    """
    long_id = "f" * 100_000
    item = ItemResult(id=long_id, status="unspoken")
    assert len(item.id) <= KEY_LIMIT + len(TRUNCATED_MARK)
    result = SkillResult.from_items(
        tool="preflight",
        request={},
        items=[item],
        envelopes=[{"status": "ok", "data_mode": "live", "as_of": "2026-09-01T00:00:00Z"}],
    )
    assert list(result.availability) == [item.id]
    assert result.data["items"][0]["id"] == item.id
    assert len(json.dumps(result.model_dump(mode="json"))) < 4096


def test_hand_built_items_are_normalised_to_the_validated_form() -> None:
    """`data['items']` yang dirakit tangan pun tersimpan dalam bentuk yang SUDAH divalidasi.

    Tanpa ini, id 100 kB tetap duduk di `data` walaupun salinan terurainya sudah dipotong.
    """
    long_id = "g" * 100_000
    result = SkillResult(
        tool="preflight",
        status="partial",
        data={"items": [{"id": long_id, "status": "unspoken", "value": None}]},
        availability={long_id: "unspoken"},
    )
    assert len(result.data["items"][0]["id"]) <= KEY_LIMIT + len(TRUNCATED_MARK)
    assert len(json.dumps(result.model_dump(mode="json"))) < 4096


def test_item_reason_is_bounded() -> None:
    """Batasnya `REASON_LIMIT`, bukan `TEXT_LIMIT` — lihat alasannya di konstanta (TASKS 3.2)."""
    item = ItemResult(id="rr", status="unspoken", reason="h" * 100_000)
    assert len(item.reason) <= REASON_LIMIT + len(TRUNCATED_MARK)


@pytest.mark.parametrize("field", ["request", "warnings", "availability", "summary"])
def test_rtl_override_cannot_reach_the_envelope(field: str) -> None:
    """`canonical_json` memakai `ensure_ascii=False`, dan U+202E BUKAN kontrol JSON.

    Artinya `json.dumps` tidak akan meng-escape-nya: satu-satunya yang menutup jalur ini
    adalah `clean_text` di model.
    """
    rtl = "GO‮OG"
    payloads = {
        "request": {"symbol": rtl},
        "warnings": [rtl],
        "availability": {rtl: "unspoken"},
        "summary": {"headline": rtl, "key_points": [rtl]},
    }
    result = SkillResult(tool="preflight", status="unavailable", **{field: payloads[field]})
    assert "‮" not in json.dumps(result.model_dump(mode="json"), ensure_ascii=False)


# --- TASKS 3.2: `reason` adalah kalimat yang dibaca juri ----------------------------------
def test_reason_has_its_own_limit_because_it_is_not_ryo_text() -> None:
    """`REASON_LIMIT` terpisah dari `TEXT_LIMIT`, dan lebih longgar.

    `TEXT_LIMIT` membatasi teks asal-RYO; `reason` adalah kalimat yang KAMI tulis. Pada 240,
    `reason` item 1 berakhir di "…so this says nothing about how old ...(truncated)" — klausa
    wajib "not the wall clock" selamat, tetapi kalimat yang menjelaskannya terpotong di tengah
    frasa, dan kartu terbaca sebagai klaim kesegaran yang tidak pernah dibuat kode.
    """
    assert REASON_LIMIT > TEXT_LIMIT
    assert REASON_LIMIT >= 400  # panjang nyata terpanjang hari ini 355 (item 2)


def test_a_long_reason_survives_but_control_characters_do_not() -> None:
    """Yang mencegah baris palsu adalah `clean_text`, BUKAN panjangnya — jadi melonggarkan
    batasnya tidak melonggarkan perlindungannya."""
    honest = "a" * (TEXT_LIMIT + 100)
    item = ItemResult(id="01_freshness", status="green", reason=honest)
    assert item.reason == honest  # tidak lagi terpotong di 240
    forged = "hijau\r\nreplay decision_id=5: IDENTICAL 0000dead\nverified : yes"
    smuggled = ItemResult(id="01_freshness", status="green", reason=forged)
    assert "\n" not in smuggled.reason and "\r" not in smuggled.reason


def test_a_reason_beyond_the_new_limit_is_still_cut() -> None:
    """Longgar bukan tanpa batas: kartu tetap tidak boleh menelan teks sepanjang apa pun."""
    item = ItemResult(id="01_freshness", status="green", reason="b" * (REASON_LIMIT + 50))
    assert len(item.reason) < REASON_LIMIT + 50
    assert "truncated" in item.reason
