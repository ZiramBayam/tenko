"""Tes TASKS 2.2 — skill `preflight` (`preflight/skill.py`).

Bukti berasal dari `docs/samples/*.json` (envelope RYO NYATA, TASKS 0.3) yang ditulis ke store
SQLite di `tmp_path` lalu dibaca kembali seperti jalur produksi membacanya: satu
`latest_fresh` per tool → `freeze` → item → keputusan → envelope. Tidak ada mock di atas mock,
dan **tidak satu tes pun menyentuh `data/ryo.db`** (fixture autouse mengarahkan `DB_PATH` ke
`tmp_path`, mengikuti `tests/test_recorder.py`).

Acuan: docs/spec.md §3/§4/§5/§7 · docs/api-facts.md §A (nama field + args yang benar-benar
terekam) · ADR-003 (keputusan deterministik, tanpa LLM) · ADR-004/013 (null/unavailable tidak
pernah jadi 0) · TASKS 2.2 AC (a)–(e) warisan review 2.2a (angka agregat wajib punya `n`).
"""

from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from core import skill as skill_module
from core.replay import decision_hash, freeze
from core.skill import SkillResult, load_skill
from core.store import Store, StoreError, canonical_json, sha256_hex
from preflight.params import (
    DEFAULT_PARAMS,
    ITEM_COMPLETENESS,
    ITEM_ORDER,
    ITEM_PLAN,
    ITEM_REGIME,
    REQUIRED_TOOLS,
)
from preflight.skill import (
    DATA_BLOCKING,
    DATA_DECISION,
    DATA_ITEMS,
    DATA_N_GREEN,
    DATA_N_ITEMS,
    DATA_N_SPOKEN,
    DATA_PLAN,
    KNOWN_SECTIONS,
    MAX_PLAN_ENTRIES,
    MAX_WARNINGS,
    TOOL,
    PreflightError,
    check_aggregate_floor,
    count_items,
    normalize_symbol,
    preflight,
    tool_args,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "docs" / "samples"

MARKET_OVERVIEW = "market_overview"
MONITOR = "monitor_market_sentiment_shift"
ANALYZE = "analyze_token"
DEEP = "deep_analysis"
COMPARE = "compare_tokens"
TOOLS = (MARKET_OVERVIEW, MONITOR, ANALYZE, DEEP, COMPARE)

#: Jam resolusi tetap: kesegaran store diukur terhadap jam ini, bukan jam mesin (AC3).
NOW = datetime(2026, 9, 1, 4, 0, 0, tzinfo=UTC)

_CACHE: dict[str, dict[str, Any]] = {}


def sample(tool: str) -> dict[str, Any]:
    """Envelope RYO nyata (`response["result"]`) dari docs/samples."""
    if tool not in _CACHE:
        _CACHE[tool] = json.loads((SAMPLES / f"{tool}.json").read_text(encoding="utf-8"))["result"]
    return json.loads(json.dumps(_CACHE[tool]))


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`Store()` tanpa argumen pun mendarat di tmp_path — DB hidup tidak pernah disentuh."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "preflight.db"))


@pytest.fixture(autouse=True)
def _registered() -> None:
    """Registry global dikosongkan tes lain (`_reset_registry`); pasang kembali bila hilang."""
    if load_skill(TOOL) is None:  # pragma: no cover - tergantung urutan berkas tes
        skill_module.register_skill(TOOL, preflight)


def build_store(
    tmp_path: Path,
    *,
    mutate: Callable[[str, dict[str, Any]], None] | None = None,
    tools: tuple[str, ...] = TOOLS,
    symbol: str = "SOL",
    intent: str = "swing",
    age_min: float = 2.0,
    name: str = "store.db",
    at: datetime = NOW,
) -> Store:
    """Store berisi satu baris per tool, berumur `age_min` menit terhadap `at` (default `NOW`).

    Tes in-process memakai jam tetap :data:`NOW`; tes subproses WAJIB memakai
    `at=datetime.now(UTC)` karena proses CLI memakai jam dinding — dengan `NOW` seluruh baris
    terbaca basi dan yang diuji berubah diam-diam menjadi jalur "tidak ada bukti".
    """
    store = Store(tmp_path / name)
    args = tool_args(symbol, intent, DEFAULT_PARAMS)
    ts = (at - timedelta(minutes=age_min)).isoformat()
    for tool in tools:
        envelope = sample(tool)
        if mutate is not None:
            mutate(tool, envelope)
        store.insert_snapshot(tool, args[tool], envelope, ts_utc=ts)
    return store


def run(store: Store, symbol: str = "SOL", intent: str = "swing", **kwargs: Any) -> SkillResult:
    return preflight(symbol, intent, store=store, now=NOW, **kwargs)


def items_by_id(result: SkillResult) -> dict[str, Any]:
    return {item.id: item for item in result.items}


class CountingStore:
    """Pembungkus store yang MENCATAT tiap pembacaan (kuota: satu per tool per keputusan)."""

    def __init__(self, inner: Store) -> None:
        self.inner = inner
        self.reads: list[str] = []
        self.snapshot_calls = 0

    def latest_fresh(self, tool: str, *args: Any, **kwargs: Any) -> Any:
        self.reads.append(tool)
        return self.inner.latest_fresh(tool, *args, **kwargs)

    def get_snapshots(self, ids: Any) -> Any:
        self.snapshot_calls += 1
        return self.inner.get_snapshots(ids)


class RefusingStore:
    """Store yang meledak bila dibaca — untuk membuktikan jalur "tidak membaca apa pun"."""

    def latest_fresh(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("store tidak boleh dibaca pada jalur ini")

    def get_snapshots(self, ids: Any) -> Any:
        raise AssertionError("store tidak boleh dibaca pada jalur ini")


# --- kerangka: registry, CLI, bentuk envelope ---------------------------------------------
def test_skill_is_registered_under_its_own_name() -> None:
    assert load_skill(TOOL) is preflight


def test_skill_modules_points_at_the_owning_module() -> None:
    """Peta modul diisi paket pemiliknya (ADR-002: core tidak boleh mengimpor preflight)."""
    assert skill_module.SKILL_MODULES[TOOL] == "preflight.skill"


def run_cli(argv: list[str], db_path: Path, *, preimport: bool = True) -> subprocess.CompletedProcess:
    """Jalankan CLI di proses terpisah. `preimport` = paket pemilik skill diimpor lebih dulu."""
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(ROOT),
        "LC_ALL": "C.UTF-8",
        "DB_PATH": str(db_path),
    }
    if preimport:
        code = "import preflight.skill\nfrom core import skill_cli\nraise SystemExit(skill_cli.main())"
        cmd = [sys.executable, "-c", code, *argv]
    else:
        cmd = [sys.executable, "-m", "core.skill_cli", *argv]
    return subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, timeout=120)


def test_cli_produces_a_valid_envelope_end_to_end(tmp_path: Path) -> None:
    """AC1: `make skill ARGS="preflight SOL"` → satu envelope sah, exit 0, tanpa traceback."""
    store = build_store(tmp_path, age_min=0.5, at=datetime.now(UTC))
    store.close()
    done = run_cli(["preflight", "SOL"], tmp_path / "store.db")
    assert done.returncode == 0
    assert b"Traceback" not in done.stderr
    envelope = json.loads(done.stdout)
    # Divalidasi ulang dengan model yang sama yang dipakai produksi: bukan sekadar "JSON".
    checked = SkillResult.model_validate(envelope)
    assert checked.tool == TOOL
    assert checked.simulated is True
    assert envelope["summary"][DATA_DECISION] in {"GO", "NO_GO"}
    # bukan sekadar "envelope sah": bukti benar-benar terbaca dari store lewat jalur CLI
    assert checked.data is not None
    assert checked.data[DATA_N_ITEMS] == len(ITEM_ORDER)
    assert checked.data["snapshot_ids"] == [1, 2, 3, 4, 5]


def test_cli_output_is_byte_identical_across_runs(tmp_path: Path) -> None:
    """AC3 di tingkat proses: dua run atas store yang sama → stdout byte-identik."""
    store = build_store(tmp_path, age_min=0.5, at=datetime.now(UTC))
    store.close()
    first = run_cli(["preflight", "SOL"], tmp_path / "store.db")
    second = run_cli(["preflight", "SOL"], tmp_path / "store.db")
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout


def test_cli_accepts_the_intent_as_a_second_argument(tmp_path: Path) -> None:
    store = build_store(tmp_path, age_min=0.5, at=datetime.now(UTC))
    store.close()
    done = run_cli(["preflight", "SOL", "swing"], tmp_path / "store.db")
    assert done.returncode == 0
    assert json.loads(done.stdout)["request"]["intent"] == "swing"


def test_bare_cli_finds_the_skill_through_the_module_map(tmp_path: Path) -> None:
    """Jalur `make skill` apa adanya: `python -m core.skill_cli`, TANPA paket pemilik diimpor.

    `core/skill.py` memetakan `preflight -> preflight.skill` (diisi core-engineer), dan
    `load_skill` mengimpornya secara malas. Tes ini yang menjaga jalur itu: kalau entri peta
    hilang, envelope masih sah tetapi `data`-nya `None` dan `warnings` berbunyi "not
    registered" — demo §7 langkah 1 mati tanpa satu tes pun merah kalau tidak dipatok di sini.
    """
    store = build_store(tmp_path, age_min=0.5, at=datetime.now(UTC))
    store.close()
    done = run_cli(["preflight", "SOL"], tmp_path / "store.db", preimport=False)
    assert done.returncode == 0
    envelope = SkillResult.model_validate(json.loads(done.stdout))
    assert envelope.simulated is True
    assert envelope.tool == TOOL
    assert envelope.data is not None
    assert envelope.data[DATA_DECISION] in {"GO", "NO_GO"}


def test_top_level_is_the_ryo_envelope_plus_simulated(tmp_path: Path) -> None:
    result = run(build_store(tmp_path))
    ryo_top_level = set(sample(ANALYZE))
    assert set(result.model_dump()) - ryo_top_level == {"simulated"}
    assert result.schema_version == "1.0"


def test_envelope_survives_the_final_revalidation_the_cli_does(tmp_path: Path) -> None:
    result = run(build_store(tmp_path))
    again = SkillResult.model_validate(result.model_dump())
    assert canonical_json(again.model_dump(mode="json")) == canonical_json(result.model_dump(mode="json"))


# --- jalur bukti lengkap -------------------------------------------------------------------
def test_full_evidence_yields_go_with_every_item_spoken(tmp_path: Path) -> None:
    result = run(build_store(tmp_path))
    assert result.status == "partial"  # deep_analysis sampel nyata memang `partial`
    assert result.data is not None
    assert result.data[DATA_DECISION] == "GO"
    assert result.data[DATA_BLOCKING] == []
    assert [item.id for item in result.items] == list(ITEM_ORDER)
    assert result.data[DATA_N_SPOKEN] == result.data[DATA_N_ITEMS] == 10


def test_availability_names_every_item_and_nothing_else(tmp_path: Path) -> None:
    result = run(build_store(tmp_path))
    assert set(result.availability) == set(ITEM_ORDER)


def test_snapshot_ids_are_the_rows_that_were_frozen(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    result = run(store)
    assert result.data is not None
    assert result.data["snapshot_ids"] == [1, 2, 3, 4, 5]
    assert result.data["store_reads"]["n_snapshots_frozen"] == 5
    assert result.data["store_reads"]["n_tools_answered"] == 5
    assert result.data["store_reads"]["n_tools_required"] == len(REQUIRED_TOOLS)


def test_decision_hash_can_be_recomputed_from_the_store(tmp_path: Path) -> None:
    """Hash di envelope = hash yang dihitung ulang dari baris store + params + keputusan."""
    store = build_store(tmp_path)
    result = run(store)
    assert result.data is not None
    bundle = freeze(store, result.data["snapshot_ids"])
    material = {
        "decision": result.data[DATA_DECISION],
        "blocking": result.data[DATA_BLOCKING],
        "item_status": {item.id: item.status for item in result.items},
    }
    assert result.data["decision_hash"] == decision_hash(bundle, DEFAULT_PARAMS.as_dict(), material)
    assert result.data["params_hash"] == DEFAULT_PARAMS.params_hash()


def test_plan_is_a_passthrough_of_item_nine(tmp_path: Path) -> None:
    """`plan` = `value` item 9 apa adanya + sumbernya. Tanpa aritmetika (itu TASKS 2.4)."""
    result = run(build_store(tmp_path))
    assert result.data is not None
    plan = result.data[DATA_PLAN]
    item = items_by_id(result)[ITEM_PLAN]
    assert plan["item_id"] == ITEM_PLAN
    assert plan["item_status"] == item.status
    assert plan["value"] == item.value
    assert plan["source"]["tool"] == DEEP
    assert plan["source"]["field"] == "data.trade_plan"
    assert plan["source"]["as_of"] == sample(DEEP)["as_of"]
    assert set(plan) == {"item_id", "item_status", "source", "value"}


def test_plan_is_absent_when_item_nine_is_unspoken(tmp_path: Path) -> None:
    def drop_plan(tool: str, envelope: dict[str, Any]) -> None:
        if tool == DEEP:
            envelope["availability"]["trade_plan"] = "unavailable"
            envelope["data"].pop("trade_plan", None)

    result = run(build_store(tmp_path, mutate=drop_plan))
    assert result.data is not None
    assert DATA_PLAN not in result.data
    assert items_by_id(result)[ITEM_PLAN].status == "unspoken"


# --- AC3: determinisme ----------------------------------------------------------------------
def test_two_runs_over_the_same_snapshots_give_the_same_hash(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    first, second = run(store), run(store)
    assert first.data is not None and second.data is not None
    assert first.data["decision_hash"] == second.data["decision_hash"]


def test_two_runs_produce_byte_identical_envelopes(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    first = canonical_json(run(store).model_dump(mode="json"))
    second = canonical_json(run(store).model_dump(mode="json"))
    assert first == second


def test_symbol_case_and_padding_do_not_change_the_decision(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    plain, padded = run(store, "SOL"), run(store, "  sol  ")
    assert plain.data is not None and padded.data is not None
    assert plain.data["decision_hash"] == padded.data["decision_hash"]
    assert padded.request["symbol"] == "SOL"


def test_different_evidence_gives_a_different_hash(tmp_path: Path) -> None:
    def flip(tool: str, envelope: dict[str, Any]) -> None:
        if tool == MARKET_OVERVIEW:
            envelope["data"]["regime"] = "risk_off"

    baseline = run(build_store(tmp_path, name="a.db"))
    changed = run(build_store(tmp_path, mutate=flip, name="b.db"))
    assert baseline.data is not None and changed.data is not None
    assert baseline.data["decision_hash"] != changed.data["decision_hash"]
    assert changed.data[DATA_DECISION] == "NO_GO"


# --- kuota: satu pembacaan per tool, nol panggilan live -------------------------------------
def test_each_tool_is_read_exactly_once(tmp_path: Path) -> None:
    spy = CountingStore(build_store(tmp_path))
    run(spy)  # type: ignore[arg-type]
    assert sorted(spy.reads) == sorted(REQUIRED_TOOLS)
    assert len(spy.reads) == len(set(spy.reads)) == 5


def test_evidence_is_frozen_in_one_pass_before_any_item_is_judged(tmp_path: Path) -> None:
    """`freeze` sekali; tidak ada pembacaan ulang di tengah penilaian item."""
    spy = CountingStore(build_store(tmp_path))
    run(spy)  # type: ignore[arg-type]
    assert spy.snapshot_calls == 1


def test_module_never_imports_a_live_client() -> None:
    """Store-first: modul ini tidak boleh punya jalan menuju HTTP sama sekali."""
    tree = ast.parse((ROOT / "preflight" / "skill.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not {name for name in imported if name.split(".")[0] in {"httpx", "requests", "socket"}}
    assert "core.ryo_client" not in imported
    assert "core.recorder" not in imported


def test_default_store_is_opened_read_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tidak menulis baris `decisions` (TASKS 2.4) bukan janji — koneksinya tidak bisa."""
    seen: dict[str, Any] = {}
    real = Store

    def spy(*args: Any, **kwargs: Any) -> Store:
        seen.update(kwargs)
        return real(*args, **kwargs)

    build_store(tmp_path, name="preflight.db").close()  # DB_PATH menunjuk berkas ini
    monkeypatch.setattr("preflight.skill.Store", spy)
    result = preflight("SOL", now=NOW)
    assert seen == {"read_only": True}
    assert result.data is not None
    assert result.data[DATA_DECISION] == "GO"


def test_unknown_intent_reads_nothing_at_all() -> None:
    result = preflight("SOL", "hold", store=RefusingStore(), now=NOW)  # type: ignore[arg-type]
    assert result.status == "unavailable"
    assert result.data is None
    assert result.summary[DATA_DECISION] == "NO_GO"
    assert any("intent" in warning for warning in result.warnings)


def test_unusable_symbol_reads_nothing_at_all() -> None:
    result = preflight("SOL,BTC", store=RefusingStore(), now=NOW)  # type: ignore[arg-type]
    assert result.status == "unavailable"
    assert result.data is None
    assert any("symbol" in warning for warning in result.warnings)


def test_intent_does_not_move_any_threshold(tmp_path: Path) -> None:
    """`intent` hanya masuk `request` + args pencarian; ambang yang menilai tetap params."""
    result = run(build_store(tmp_path))
    assert result.request["intent"] == "swing"
    assert result.data is not None
    assert result.data["params_hash"] == DEFAULT_PARAMS.params_hash()


# --- argumen tool = baris yang benar-benar terekam ------------------------------------------
def test_tool_args_match_the_recorded_rows() -> None:
    """Argumen pencarian = bentuk PERSIS `core/recorder.py` + `docs/samples` (api-facts §A).

    **Jangan "memperbaiki" dua baris pertama menjadi `sample(...)["request"]`.** `request` di
    envelope adalah argumen TERNORMALISASI yang dikembalikan server, sedangkan yang dicari di
    store adalah argumen yang DIKIRIM (`snapshots.args_json`). Keduanya berbeda untuk dua tool
    ini: `monitor_market_sentiment_shift` dikirim `{}` menjawab `request: {"time_window": "7d"}`
    di 26/26 baris `data/ryo.db` (api-facts §A "Argumen yang DIKIRIM ≠ `request` yang
    DIKEMBALIKAN", diverifikasi 3 Sep 2026). Menyamakannya dengan `request` berarti mematok
    bentuk args yang TIDAK PERNAH ada di store, dan `latest_fresh` tidak akan pernah cocok lagi.
    Penyamaan dengan `request` hanya sah untuk tiga tool di bawahnya.
    """
    args = tool_args("SOL", "swing", DEFAULT_PARAMS)
    assert args[MARKET_OVERVIEW] == {}
    assert args[MONITOR] == {}
    assert args[MONITOR] != sample(MONITOR)["request"]  # justru itu intinya
    assert args[ANALYZE] == sample(ANALYZE)["request"]
    assert args[DEEP] == sample(DEEP)["request"]
    assert args[COMPARE] == sample(COMPARE)["request"]
    # `args_hash` baris `market_overview` di data/ryo.db (26 baris, sapuan 2 Sep 2026).
    assert sha256_hex(args[MARKET_OVERVIEW]) == (
        "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    )


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("SOL", "SOL, BNB, ETH"),
        ("BTC", "BTC, BNB, ETH"),
        ("ETH", "ETH, BNB"),
        ("BNB", "BNB, ETH"),
    ],
)
def test_compare_symbols_never_repeat_a_token(symbol: str, expected: str) -> None:
    """Dua dari empat simbol inti ADALAH pembanding: `params.peers = ("BNB", "ETH")`.

    Tanpa dedupe, `preflight("BNB")` meminta perbandingan `"BNB, BNB, ETH"` — satu token
    dibandingkan dengan dirinya sendiri. Alasan dedupe SEMANTIK + rentang 2-4 `catalog.json`,
    **bukan** kecocokan dengan baris perekam: bentuk ber-dedupe pun 0 baris di `data/ryo.db`
    hari ini (perekam belum memanggil `compare_tokens` — TASKS 2.0c). Perilaku RYO terhadap
    simbol berulang BELUM DIVERIFIKASI (0 amatan), jadi tes ini tidak mengklaim API menolaknya.
    Urutan dipertahankan: `args_hash` menghitung STRING-nya, bukan himpunannya.
    """
    args = tool_args(symbol, "swing", DEFAULT_PARAMS)[COMPARE]
    assert args["symbols"] == expected
    listed = args["symbols"].split(", ")
    assert listed[0] == symbol  # yang ditanya selalu di depan
    assert len(listed) == len(set(listed))
    # docs/samples/catalog.json: `compare_tokens.symbols` = "2-4 simbol, dipisah koma/spasi"
    assert 2 <= len(listed) <= 4


def test_compare_symbols_for_a_peer_symbol_still_find_their_rows(tmp_path: Path) -> None:
    """Penulis dan pencari baris sepakat: store diisi dengan args ber-dedupe, lalu ditemukan.

    **Batas tes ini (ditandai api-verifier):** store-nya dibangun dari `tool_args` yang sama,
    jadi ia SIRKULAR terhadap DB nyata — ia membuktikan kedua sisi kode sepakat, BUKAN bahwa
    baris `compare_tokens` untuk `BNB` ada di `data/ryo.db` (hari ini tidak ada satu pun;
    TASKS 2.0c yang membuatnya).
    """
    store = build_store(tmp_path, symbol="BNB")
    result = run(store, "BNB")
    assert result.data is not None
    assert result.data["store_reads"]["tools"][COMPARE]["used_as_evidence"] is True
    assert result.data["store_reads"]["n_tools_answered"] == 5


def test_symbol_normalisation_matches_the_recorder() -> None:
    """Salinan yang dipatok (pelajaran TASKS 1.5b): pencarian harus sepakat dengan penulis."""
    from core.recorder import _normalize_symbol as recorder_normalize

    for raw in ("SOL", "sol", "  sol  ", "", "   ", "牛来", "a" * 65, "SO L", "SOL‮", 42, None):
        assert normalize_symbol(raw) == recorder_normalize(raw), raw


def test_symbol_with_a_comma_is_refused_here_only() -> None:
    """Aturan TAMBAHAN modul ini: koma memisah daftar `compare_tokens.symbols`."""
    from core.recorder import _normalize_symbol as recorder_normalize

    assert recorder_normalize("SOL,BTC") == "SOL,BTC"
    assert normalize_symbol("SOL,BTC") is None


# --- AC2: kegagalan tool --------------------------------------------------------------------
def test_all_tools_unavailable_is_no_go_without_exception(tmp_path: Path) -> None:
    """AC2: fixture `status: unavailable` → NO_GO, `data=None`, nol exception."""

    def kill(tool: str, envelope: dict[str, Any]) -> None:
        envelope["status"] = "unavailable"
        envelope["data"] = None

    result = run(build_store(tmp_path, mutate=kill))
    assert result.status == "unavailable"
    assert result.data is None
    assert result.summary[DATA_DECISION] == "NO_GO"
    assert result.summary[DATA_BLOCKING]
    assert set(result.availability) == set(ITEM_ORDER)
    assert set(result.availability.values()) == {"unspoken"}
    assert any("no required tool answered" in warning for warning in result.warnings)


def test_empty_store_is_no_go_without_exception(tmp_path: Path) -> None:
    result = run(build_store(tmp_path, tools=()))
    assert result.status == "unavailable"
    assert result.data is None
    assert result.summary[DATA_DECISION] == "NO_GO"


def test_stale_evidence_is_not_used_at_all(tmp_path: Path) -> None:
    """Batas kesegaran store: baris lebih tua dari `max_age_min` tidak ikut dibekukan."""
    result = run(build_store(tmp_path, age_min=DEFAULT_PARAMS.max_age_min + 1))
    assert result.status == "unavailable"
    assert result.data is None


def test_envelope_that_disclaims_itself_is_not_used_as_evidence(tmp_path: Path) -> None:
    """`status: unavailable` TAPI `data` terisi: angkanya tidak boleh menggerakkan keputusan."""

    def lie(tool: str, envelope: dict[str, Any]) -> None:
        envelope["status"] = "unavailable"  # `data` sengaja dibiarkan utuh

    result = run(build_store(tmp_path, mutate=lie))
    assert result.status == "unavailable"
    assert result.data is None
    assert result.summary[DATA_DECISION] == "NO_GO"
    assert any("not used as evidence" in warning for warning in result.warnings)


def test_one_required_tool_unavailable_still_names_the_hole(tmp_path: Path) -> None:
    """Satu tool wajib mati, sisanya menjawab → `partial`, item 2 MERAH, `data` tetap ada."""

    def kill_deep(tool: str, envelope: dict[str, Any]) -> None:
        if tool == DEEP:
            envelope["status"] = "unavailable"
            envelope["data"] = None

    result = run(build_store(tmp_path, mutate=kill_deep))
    assert result.status == "partial"
    assert result.data is not None
    assert items_by_id(result)[ITEM_COMPLETENESS].status == "red"
    assert ITEM_COMPLETENESS in result.data[DATA_BLOCKING]
    assert result.data[DATA_DECISION] == "NO_GO"
    assert result.data["store_reads"]["tools"][DEEP]["status"] == "unavailable"


def test_missing_tools_make_the_envelope_partial_not_ok(tmp_path: Path) -> None:
    """Satu tool menjawab, empat tidak punya baris sama sekali → `partial`, bukan `ok`.

    `derive_status` hanya melihat envelope yang dioper; kalau yang tidak menjawab tidak ikut
    disebut, satu tool yang `ok` akan menyamar sebagai panggilan yang lengkap.
    """
    result = run(build_store(tmp_path, tools=(MARKET_OVERVIEW,)))
    assert result.status == "partial"
    assert result.data is not None
    assert result.data["store_reads"]["n_tools_answered"] == 1
    assert items_by_id(result)[ITEM_COMPLETENESS].status == "red"
    assert result.data[DATA_DECISION] == "NO_GO"


def test_store_failure_is_an_envelope_not_a_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: Any, **kwargs: Any) -> Store:
        raise StoreError("DB tidak bisa dibuka")

    monkeypatch.setattr("preflight.skill.Store", explode)
    result = preflight("SOL", now=NOW)
    assert result.status == "unavailable"
    assert result.summary[DATA_DECISION] == "NO_GO"
    assert any("store could not be read" in warning for warning in result.warnings)


def test_tampered_snapshot_row_refuses_and_names_the_snapshot(tmp_path: Path) -> None:
    """Bukti yang tidak cocok dengan hash-nya sendiri = penolakan, bukan keputusan."""
    store = build_store(tmp_path)
    store.close()
    raw = sqlite3.connect(tmp_path / "store.db")
    raw.execute("UPDATE snapshots SET payload_json = ? WHERE id = 1", ('{"status":"ok"}',))
    raw.commit()
    raw.close()
    with Store(tmp_path / "store.db", read_only=True) as reopened:
        result = run(reopened)
    assert result.status == "unavailable"
    assert result.data is None
    assert any("snapshot_id=1" in warning for warning in result.warnings)


# --- AC (a)-(e): penjaga angka agregat ------------------------------------------------------
def mixed_result(tmp_path: Path) -> SkillResult:
    """Envelope dengan campuran hijau/merah/unspoken — bahan uji penjaga `n`."""

    def mutate(tool: str, envelope: dict[str, Any]) -> None:
        if tool == MARKET_OVERVIEW:
            envelope["data"]["regime"] = "risk_off"  # merah
        if tool == COMPARE:
            envelope["data"]["factors"] = []  # unspoken

    return run(build_store(tmp_path, mutate=mutate))


def test_n_spoken_counts_only_items_actually_spoken(tmp_path: Path) -> None:
    """AC (a): `n` dari item ber-status non-`unspoken` yang BENAR-BENAR masuk envelope."""
    result = mixed_result(tmp_path)
    assert result.data is not None
    entries = result.data[DATA_ITEMS]
    spoken = [entry for entry in entries if entry["status"] != "unspoken"]
    assert result.data[DATA_N_SPOKEN] == len(spoken)
    assert result.data[DATA_N_ITEMS] == len(entries)
    assert 0 < result.data[DATA_N_SPOKEN] < result.data[DATA_N_ITEMS]
    assert result.data[DATA_N_SPOKEN] != len(REQUIRED_TOOLS)


def test_every_aggregate_number_sits_next_to_its_n(tmp_path: Path) -> None:
    result = mixed_result(tmp_path)
    assert result.data is not None
    counts = count_items(result.items)
    assert {key: result.data[key] for key in counts.as_dict()} == counts.as_dict()
    assert counts.n_green + counts.n_red + counts.n_unspoken == counts.n_items


@pytest.mark.parametrize("inflated", [11, 99, -1])
def test_n_spoken_cannot_exceed_the_items_it_counts(tmp_path: Path, inflated: int) -> None:
    """AC (c): `n` yang tidak didukung `data["items"]` ditolak mekanis, bukan dipercaya."""
    result = run(build_store(tmp_path))
    assert result.data is not None
    result.data[DATA_N_SPOKEN] = inflated  # penambalan setelah validasi (celah 1.3 yang nyata)
    with pytest.raises(PreflightError, match="n_spoken"):
        check_aggregate_floor(result)


def test_inflated_green_count_is_rejected(tmp_path: Path) -> None:
    result = run(build_store(tmp_path))
    assert result.data is not None
    result.data[DATA_N_GREEN] = result.data[DATA_N_GREEN] + 1
    with pytest.raises(PreflightError, match="n_green"):
        check_aggregate_floor(result)


def zero_spoken_store(tmp_path: Path) -> Store:
    """Bukti yang terbaca tetapi tidak satu item pun bisa tersebut (n_spoken == 0)."""

    def mutate(tool: str, envelope: dict[str, Any]) -> None:
        envelope["data"] = None
        if tool == MARKET_OVERVIEW:
            envelope.pop("as_of", None)  # item 1 tidak punya jam untuk disebut
        else:
            envelope["status"] = "weird"  # item 2 unspoken (kosakata di luar api-facts §A)

    return build_store(tmp_path, mutate=mutate)


def test_zero_spoken_items_refuses_plan_and_score(tmp_path: Path) -> None:
    """AC (b): nol bukti = penolakan ber-alasan, bukan skor."""
    result = run(zero_spoken_store(tmp_path))
    assert result.data is not None  # ada tool yang menjawab, jadi ini BUKAN jalur `unavailable`
    assert result.status == "partial"
    assert result.data[DATA_N_SPOKEN] == 0
    assert DATA_PLAN not in result.data
    assert result.data[DATA_DECISION] == "NO_GO"
    assert result.data[DATA_BLOCKING]
    assert "refusal, not a score" in result.data["reason"]


def test_zero_spoken_items_with_a_plan_is_rejected(tmp_path: Path) -> None:
    result = run(zero_spoken_store(tmp_path))
    assert result.data is not None
    result.data[DATA_PLAN] = {"item_id": ITEM_PLAN, "item_status": "green", "source": {}, "value": {}}
    with pytest.raises(PreflightError, match="n_spoken=0 tetapi envelope membawa plan"):
        check_aggregate_floor(result)


def test_zero_spoken_items_may_not_be_a_go(tmp_path: Path) -> None:
    result = run(zero_spoken_store(tmp_path))
    assert result.data is not None
    result.data[DATA_DECISION] = "GO"
    result.summary[DATA_DECISION] = "GO"
    with pytest.raises(PreflightError, match="NO_GO"):
        check_aggregate_floor(result)


def test_data_without_items_key_is_not_a_shortcut() -> None:
    """AC (d): `data` tanpa kunci `items` lolos SELURUH invarian `core/skill.py` (TASKS 2.2a).

    Bentuk itu sengaja tetap diterima di lapisan kontrak; yang menolaknya adalah lapisan ini.
    """
    smuggled = SkillResult(
        tool=TOOL,
        status="ok",
        data={DATA_DECISION: "GO", DATA_N_SPOKEN: 10, DATA_N_ITEMS: 10},
        availability={},
        summary={DATA_DECISION: "GO"},
    )
    assert smuggled.items == []  # kontrak 1.3 tidak protes
    with pytest.raises(PreflightError, match="wajib punya kunci 'items'"):
        check_aggregate_floor(smuggled)


def test_empty_items_list_is_rejected_by_the_contract_layer() -> None:
    """Sisi lain asimetri: `data={"items": []}` sudah ditolak `core/skill.py` sendiri."""
    with pytest.raises(Exception, match="items kosong"):
        SkillResult(tool=TOOL, status="ok", data={DATA_ITEMS: []}, availability={})


def test_ghost_availability_key_is_rejected(tmp_path: Path) -> None:
    """AC (e): kunci yang bukan id item dinilai maupun nama seksi yang dikenal."""
    result = run(build_store(tmp_path))
    result.availability["11_bunryo"] = "available"
    with pytest.raises(PreflightError, match="11_bunryo"):
        check_aggregate_floor(result)


def test_known_section_name_stays_allowed_in_availability(tmp_path: Path) -> None:
    """Seksi tool yang dikenal (mis. `trade_plan`) boleh: itu kosakata `availability` RYO."""
    result = run(build_store(tmp_path))
    assert "trade_plan" in KNOWN_SECTIONS
    result.availability["trade_plan"] = "available"
    check_aggregate_floor(result)


def test_plan_without_a_spoken_item_nine_is_rejected(tmp_path: Path) -> None:
    def drop_plan(tool: str, envelope: dict[str, Any]) -> None:
        if tool == DEEP:
            envelope["availability"]["trade_plan"] = "unavailable"
            envelope["data"].pop("trade_plan", None)

    result = run(build_store(tmp_path, mutate=drop_plan))
    assert result.data is not None
    result.data[DATA_PLAN] = {"item_id": ITEM_PLAN, "item_status": "green", "source": {}, "value": {}}
    with pytest.raises(PreflightError, match=ITEM_PLAN):
        check_aggregate_floor(result)


def test_summary_and_data_must_agree_on_the_decision(tmp_path: Path) -> None:
    result = run(build_store(tmp_path))
    assert result.data is not None
    result.data[DATA_DECISION] = "NO_GO"
    with pytest.raises(PreflightError, match="summary"):
        check_aggregate_floor(result)


def test_blocking_may_only_name_items_that_were_judged(tmp_path: Path) -> None:
    result = run(build_store(tmp_path))
    assert result.data is not None
    result.data[DATA_BLOCKING] = ["99_hantu"]
    with pytest.raises(PreflightError, match="99_hantu"):
        check_aggregate_floor(result)


def test_refusal_envelope_must_say_no_go() -> None:
    result = SkillResult.unavailable(TOOL)
    with pytest.raises(PreflightError, match="NO_GO"):
        check_aggregate_floor(result)


# --- warning & kartu -------------------------------------------------------------------------
def test_warnings_are_capped_but_the_total_is_named(tmp_path: Path) -> None:
    """Konvensi repo: batasi + sebutkan jumlah aslinya; jangan membuang diam-diam."""

    def noisy(tool: str, envelope: dict[str, Any]) -> None:
        if tool == COMPARE:
            for index, token in enumerate(envelope["data"]["tokens"]):
                token["verdict"] = f"kosakata_asing_{index}"

    result = run(build_store(tmp_path, mutate=noisy))
    assert len(result.warnings) <= MAX_WARNINGS + 1
    if len(result.warnings) > MAX_WARNINGS:
        assert "in total" in result.warnings[-1]


def test_key_points_name_the_blocking_items(tmp_path: Path) -> None:
    def flip(tool: str, envelope: dict[str, Any]) -> None:
        if tool == MARKET_OVERVIEW:
            envelope["data"]["regime"] = "risk_off"

    result = run(build_store(tmp_path, mutate=flip))
    assert result.data is not None
    blocking = result.data[DATA_BLOCKING]
    assert blocking
    points = result.summary["key_points"]
    assert all(any(point.startswith(item_id) for point in points) for item_id in blocking)


def test_headline_names_both_the_verdict_and_the_n(tmp_path: Path) -> None:
    result = run(build_store(tmp_path))
    headline = result.summary["headline"]
    assert "GO" in headline
    assert "10 of 10" in headline


# --- lima serangan security-reviewer atas envelope preflight ASLI (TINGGI-1, 3 Sep 2026) ---
# Semuanya menambal `result.data`/`result.summary` SETELAH validasi — celah nyata yang diakui
# `core/skill.py` (model beku, isi `data` tidak). Versi pertama `check_aggregate_floor` LOLOS
# kelimanya karena ia hanya memeriksa konsistensi angka, bukan menghitung ulang vonisnya.
def test_attack_1_go_while_an_item_is_still_red(tmp_path: Path) -> None:
    """`decision` dipalsukan jadi GO (summary diselaraskan) padahal 03_regime masih merah."""
    result = mixed_result(tmp_path)
    assert result.data is not None
    assert items_by_id(result)[ITEM_REGIME].status == "red"
    result.data[DATA_DECISION] = "GO"
    result.summary[DATA_DECISION] = "GO"
    with pytest.raises(PreflightError, match="tidak konsisten dengan item envelope ini"):
        check_aggregate_floor(result)


def test_attack_2_blocking_emptied_while_a_red_item_remains(tmp_path: Path) -> None:
    """`blocking` dikosongkan di `data` DAN `summary`: penahan disembunyikan, bukan hilang."""
    result = mixed_result(tmp_path)
    assert result.data is not None
    assert result.data[DATA_BLOCKING]
    result.data[DATA_BLOCKING] = []
    result.summary[DATA_BLOCKING] = []
    result.data[DATA_DECISION] = "GO"
    result.summary[DATA_DECISION] = "GO"
    with pytest.raises(PreflightError, match="bukan himpunan penyebab veto"):
        check_aggregate_floor(result)


def test_attack_3_red_item_deleted_and_every_count_recomputed(tmp_path: Path) -> None:
    """Item merah DIHAPUS lalu seluruh `n_*` dihitung ulang agar konsisten (10 baris → 9).

    Serangan ini lolos `SkillResult.model_validate` — validasi ulang yang dipakai
    `core/skill_cli.py` — karena kontrak 1.3 tidak pernah menuntut sepuluh baris ADA.
    """
    result = mixed_result(tmp_path)
    assert result.data is not None
    data = result.data
    data[DATA_ITEMS] = [entry for entry in data[DATA_ITEMS] if entry["id"] != ITEM_REGIME]
    data[DATA_N_ITEMS] -= 1
    data["n_red"] -= 1
    data[DATA_N_SPOKEN] -= 1
    data[DATA_BLOCKING] = [i for i in data[DATA_BLOCKING] if i != ITEM_REGIME]
    result.summary[DATA_BLOCKING] = data[DATA_BLOCKING]
    # kontrak envelope (1.3) memang tidak menangkapnya — itu sebabnya lantai ini ada
    SkillResult.model_validate(result.model_dump())
    with pytest.raises(PreflightError, match="TEPAT sepuluh baris panggilan"):
        check_aggregate_floor(result)


def test_attack_4_invented_item_escapes_the_zero_evidence_branch(tmp_path: Path) -> None:
    """Item karangan `11_bunryo` disisipkan supaya `n_spoken` bukan nol → `plan` boleh masuk."""
    result = run(zero_spoken_store(tmp_path))
    assert result.data is not None
    assert result.data[DATA_N_SPOKEN] == 0
    result.data[DATA_ITEMS].append(
        {"id": "11_bunryo", "status": "green", "value": 1.0, "source": None, "reason": "karangan"}
    )
    result.availability["11_bunryo"] = "available"
    result.data[DATA_N_ITEMS] += 1
    result.data[DATA_N_GREEN] += 1
    result.data[DATA_N_SPOKEN] += 1
    result.data[DATA_PLAN] = {"item_id": ITEM_PLAN, "item_status": "green", "source": {}, "value": {}}
    with pytest.raises(PreflightError, match="TEPAT sepuluh baris panggilan"):
        check_aggregate_floor(result)


def test_attack_5_plan_value_swapped_for_other_numbers(tmp_path: Path) -> None:
    """`plan` diganti angka lain + `source` hantu, padahal modul mengklaim PASSTHROUGH."""
    result = run(build_store(tmp_path))
    assert result.data is not None
    result.data[DATA_PLAN] = {
        "item_id": ITEM_PLAN,
        "item_status": "green",
        "source": {"tool": DEEP, "field": "data.trade_plan", "snapshot_id": 9999, "as_of": None},
        "value": {"entry": 1.0, "stop": 0.01, "targets": [999999], "risk_reward": [99]},
    }
    with pytest.raises(PreflightError, match="tidak sama dengan passthrough"):
        check_aggregate_floor(result)


def test_plan_removed_entirely_is_also_rejected(tmp_path: Path) -> None:
    """Arah sebaliknya: item 9 tersebut tetapi `plan` dihapus → kartu dan data berbeda."""
    result = run(build_store(tmp_path))
    assert result.data is not None
    del result.data[DATA_PLAN]
    with pytest.raises(PreflightError, match="tidak membawa plan"):
        check_aggregate_floor(result)


def test_availability_missing_an_item_is_rejected(tmp_path: Path) -> None:
    """Arah kedua AC (e): baris panggilan yang lenyap dari `availability` juga hantu."""
    result = run(build_store(tmp_path))
    del result.availability[ITEM_REGIME]
    with pytest.raises(PreflightError, match="tidak menyebut item yang dinilai"):
        check_aggregate_floor(result)


def test_floor_refuses_to_judge_with_params_that_did_not_judge(tmp_path: Path) -> None:
    """Aturan `check_decision_floor`: ambang yang memeriksa = ambang yang menilai."""
    result = run(build_store(tmp_path))
    other = DEFAULT_PARAMS.with_overrides(min_rr=99.0)
    with pytest.raises(PreflightError, match="params_hash"):
        check_aggregate_floor(result, other)


def test_summary_blocking_must_mirror_data_blocking(tmp_path: Path) -> None:
    result = mixed_result(tmp_path)
    assert result.data is not None
    result.summary[DATA_BLOCKING] = []
    with pytest.raises(PreflightError, match="summary.blocking"):
        check_aggregate_floor(result)


# --- SEDANG: daftar tak terbatas & teks masukan di `data` ------------------------------------
def test_plan_lists_are_capped_with_their_totals_named(tmp_path: Path) -> None:
    """`targets` 50.000 entri tidak boleh disalin utuh ke `plan` (payload >1 MB ke stdout)."""

    def flood(tool: str, envelope: dict[str, Any]) -> None:
        if tool == DEEP:
            plan = envelope["data"]["trade_plan"]
            plan["targets"] = [110.0 + index for index in range(50_000)]

    result = run(build_store(tmp_path, mutate=flood))
    assert result.data is not None
    value = result.data[DATA_PLAN]["value"]
    assert len(value["targets"]) == MAX_PLAN_ENTRIES
    assert value["targets_total"] == 50_000  # dibatasi, TIDAK dibuang diam-diam
    check_aggregate_floor(result)  # passthrough tetap terikat setelah pemotongan
    # Batas yang diakui & dipatok: `value` item 9 sendiri masih membawa daftar penuh; yang
    # memotongnya adalah `preflight/items.py` (TASKS 2.1), bukan lapisan ini.
    assert len(items_by_id(result)[ITEM_PLAN].value["targets"]) == 50_000


def test_symbol_in_data_is_escaped_like_the_one_in_request(tmp_path: Path) -> None:
    """`data` tidak dibersihkan model; simbol adalah satu-satunya teks masukan di dalamnya.

    `normalize_symbol` menolak Cc/Cf/Cs (dan koma) tetapi MELOLOSKAN Cn/Co — sengaja, supaya ia
    tetap sepakat dengan `core/recorder.py` yang menulis barisnya. Konsekuensinya: yang menjaga
    kartu adalah `clean_text`, dan ia harus dipakai di `data` juga — kalau tidak,
    `request["symbol"]` (dibersihkan validator) dan `data["symbol"]` menyebut simbol berbeda.
    """
    weird = "SOL" + chr(0xE0000)  # Cn (unassigned)
    assert normalize_symbol(weird) == weird
    result = run(build_store(tmp_path, symbol=weird), weird)
    assert result.data is not None
    escaped = "SOL" + chr(92) + "ue0000"
    assert result.data["symbol"] == result.request["symbol"] == escaped
    assert chr(0xE0000) not in canonical_json(result.model_dump(mode="json"))


def test_symbol_in_data_matches_request_on_the_evidence_path(tmp_path: Path) -> None:
    result = run(build_store(tmp_path))
    assert result.data is not None
    assert result.data["symbol"] == result.request["symbol"] == "SOL"


# --- RENDAH: penolakan dini tetap menjawab "kenapa" ------------------------------------------
@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (("SOL,BTC", "swing"), "symbol"),
        (("SOL", "hold"), "intent"),
    ],
)
def test_early_refusal_names_its_reason_on_the_card(args: tuple[str, str], needle: str) -> None:
    """`blocking` kosong di sini karena belum ada item yang dinilai — sebabnya tetap terbaca."""
    result = preflight(*args, store=RefusingStore(), now=NOW)  # type: ignore[arg-type]
    assert result.summary[DATA_BLOCKING] == []
    assert result.summary["key_points"]
    assert needle in result.summary["key_points"][0]


# --- pengakuan yang DIEKSEKUSI, lalu DITUTUP: batas kepercayaan data["items"] --------------
# Sampai TASKS 2.4c-min, dua tes di bawah ini bernama `test_acknowledged_gap_*` dan
# MENGHARAPKAN serangan 6 & 8 LOLOS (pola 2.2a `test_acknowledged_gap_ok_without_items_key_...`
# dan 1.6 `REPLACE INTO`). Celahnya kini DITUTUP oleh `agent.anchor.check_against_evidence`,
# jadi tesnya dibalik di commit yang sama — pembalikannya harus terlihat, bukan senyap.
#
# Pembagian kerjanya tetap seperti yang ditulis docstring `preflight/skill.py`: lantai di modul
# ini menjawab "envelope konsisten dengan itemnya sendiri" dan karena itu MASIH meloloskan
# kedua serangan; yang menjawab "item ini adalah apa yang dikatakan bukti beku" adalah
# penjangkaran di `agent/`, satu-satunya lapisan yang memegang bundle. Kedua sisi itu
# di-assert di sini supaya batasnya tidak bisa bergeser diam-diam ke arah mana pun.
# Tes penyerang lengkapnya: `tests/test_agent_anchor.py`.
def test_flipped_item_statuses_pass_the_floor_but_are_rejected_by_the_anchor(
    tmp_path: Path,
) -> None:
    """Serangan 6/7: lantai ini ikut patuh; penjangkaran bukti menolaknya."""
    store = build_store(
        tmp_path,
        mutate=lambda tool, envelope: (
            envelope["data"].__setitem__("regime", "risk_off")
            if tool == MARKET_OVERVIEW
            else envelope["data"].__setitem__("factors", [])
            if tool == COMPARE
            else None
        ),
    )
    result = run(store)
    assert result.data is not None
    assert (result.data[DATA_DECISION], result.data[DATA_BLOCKING]) == (
        "NO_GO",
        [ITEM_REGIME, "10_relative"],
    )
    data = result.data
    for entry in data[DATA_ITEMS]:
        if entry["id"] in {ITEM_REGIME, "10_relative"}:
            entry["status"] = "green"
            result.availability[entry["id"]] = "available"
    data.update({DATA_N_GREEN: 10, "n_red": 0, "n_unspoken": 0, DATA_N_SPOKEN: 10})
    data[DATA_DECISION] = "GO"
    data[DATA_BLOCKING] = []
    result.summary.update({DATA_DECISION: "GO", DATA_BLOCKING: [], "headline": "SOL GO: 10 of 10"})

    # LOLOS di lantai ini — batasnya memang "konsisten dengan itemnya sendiri".
    check_aggregate_floor(result)
    # Bukti beku di store TIDAK ikut berubah: kebohongan hanya ada di envelope.
    bundle = freeze(store, data["snapshot_ids"])
    frozen = store.get_snapshots(data["snapshot_ids"])
    assert any(json.loads(row.payload_json)["data"].get("regime") == "risk_off" for row in frozen)
    # Serangan 7: `decision_hash` dihitung dari bundle ASLI + item_status PALSU tetap SAH
    # menurut `core/replay.py` — itulah yang membuat serangan ini berbahaya.
    forged = decision_hash(
        bundle,
        DEFAULT_PARAMS.as_dict(),
        {
            "decision": "GO",
            "blocking": [],
            "item_status": {item.id: item.status for item in result.items},
        },
    )
    assert len(forged) == 64
    assert forged != data["decision_hash"]  # beda dari hash jujur, tetapi sama-sama sah

    # DITUTUP (TASKS 2.4c-min): penjangkaran menilai ulang item dari `payload_json` beku dan
    # menolak envelope ini, menyebut id item DAN snapshot_id-nya. Impor lokal — ADR-002
    # melarang `preflight/` mengimpor `agent/`; tes boleh, kodenya tidak.
    from agent.anchor import EvidenceMismatchError, check_against_evidence

    with pytest.raises(EvidenceMismatchError) as excinfo:
        check_against_evidence(result, bundle, DEFAULT_PARAMS)
    assert ITEM_REGIME in str(excinfo.value) and "snapshot_id=" in str(excinfo.value)


def test_plan_falsified_on_both_sides_passes_the_floor_but_not_the_anchor(tmp_path: Path) -> None:
    """Serangan 8/12: `plan` == passthrough tidak menolong bila ITEM-nya ikut dipalsukan.

    Lantai ini tetap lolos (kedua sisi berasal dari item palsu yang sama); penjangkaran ke
    bukti beku menolaknya (TASKS 2.4c-min).
    """
    store = build_store(tmp_path)
    result = run(store)
    assert result.data is not None
    forged_value = {
        **items_by_id(result)[ITEM_PLAN].value,
        "entry": 1.0,
        "stop": 0.0001,
        "targets": [999999.0],
        "risk_reward": [99.0, 99.0],
    }
    for entry in result.data[DATA_ITEMS]:
        if entry["id"] == ITEM_PLAN:
            entry["value"] = forged_value
    result.data[DATA_PLAN]["value"] = forged_value

    check_aggregate_floor(result)  # LOLOS: kedua sisi perbandingan berasal dari item yang sama
    assert result.data[DATA_PLAN]["value"]["entry"] == 1.0

    # DITUTUP (TASKS 2.4c-min): angka plan dibandingkan dengan `deep_analysis.data.trade_plan`
    # di dalam bundle, bukan dengan salinan dirinya sendiri.
    from agent.anchor import EvidenceMismatchError, check_against_evidence

    with pytest.raises(EvidenceMismatchError, match=ITEM_PLAN):
        check_against_evidence(result, freeze(store, result.data["snapshot_ids"]), DEFAULT_PARAMS)


# --- SEDANG/RENDAH ronde kedua: pembukuan intra-envelope ------------------------------------
def test_data_symbol_must_equal_request_symbol(tmp_path: Path) -> None:
    result = run(build_store(tmp_path))
    assert result.data is not None
    result.data["symbol"] = "BTC"
    with pytest.raises(PreflightError, match="satu envelope, satu simbol"):
        check_aggregate_floor(result)


@pytest.mark.parametrize("key", ["decision_hash", "params_hash", "reason"])
def test_bookkeeping_keys_may_not_vanish(tmp_path: Path, key: str) -> None:
    result = run(build_store(tmp_path))
    assert result.data is not None
    del result.data[key]
    with pytest.raises(PreflightError, match=key):
        check_aggregate_floor(result)


def test_snapshot_ids_must_agree_with_the_store_read_count(tmp_path: Path) -> None:
    """Dua pembukuan yang menyebut jumlah bukti berbeda di envelope yang sama."""
    result = run(build_store(tmp_path))
    assert result.data is not None
    result.data["store_reads"]["n_snapshots_frozen"] = 0
    with pytest.raises(PreflightError, match="dua pembukuan, dua jumlah"):
        check_aggregate_floor(result)


@pytest.mark.parametrize("value", ["1,2,3", [True], [1, "2"], {"1": 2}])
def test_snapshot_ids_must_be_a_list_of_plain_ints(tmp_path: Path, value: Any) -> None:
    """Cocokkan pesan CABANGNYA, bukan sekadar kata "snapshot_ids".

    Tetangganya (urut-unik, `n_snapshots_frozen`) kebetulan ikut menolak sebagian bentuk ini,
    jadi regex yang longgar akan tetap hijau meski penjaga TIPE dimatikan — persis kegagalan
    "lolos karena alasan yang salah" yang sudah dua kali ditemukan di berkas ini.
    """
    result = run(build_store(tmp_path))
    assert result.data is not None
    result.data["snapshot_ids"] = value
    with pytest.raises(PreflightError, match="harus daftar id baris"):
        check_aggregate_floor(result)


@pytest.mark.parametrize("value", [[5, 4, 3, 2, 1], [1, 1, 2, 3, 4, 5]])
def test_snapshot_ids_must_be_sorted_without_duplicates(tmp_path: Path, value: Any) -> None:
    result = run(build_store(tmp_path))
    assert result.data is not None
    result.data["snapshot_ids"] = value
    with pytest.raises(PreflightError, match="urut menaik tanpa duplikat"):
        check_aggregate_floor(result)


def test_item_source_may_not_point_outside_the_frozen_evidence(tmp_path: Path) -> None:
    """Serangan 12: `source.snapshot_id` hantu = tunjukan ke bukti yang tidak ada di envelope."""
    result = run(build_store(tmp_path))
    assert result.data is not None
    for entry in result.data[DATA_ITEMS]:
        if entry["id"] == ITEM_PLAN:
            entry["source"]["snapshot_id"] = 424242
    result.data[DATA_PLAN]["source"]["snapshot_id"] = 424242
    with pytest.raises(PreflightError, match="424242"):
        check_aggregate_floor(result)


def test_availability_value_must_match_the_item_status(tmp_path: Path) -> None:
    """Serangan 13: item hijau ditampilkan `unspoken` di kartu (atau sebaliknya)."""
    result = run(build_store(tmp_path))
    result.availability[ITEM_REGIME] = "unspoken"
    with pytest.raises(PreflightError, match="tidak cocok dengan status item"):
        check_aggregate_floor(result)


def test_store_reads_must_name_every_required_tool(tmp_path: Path) -> None:
    result = run(build_store(tmp_path))
    assert result.data is not None
    del result.data["store_reads"]["tools"][DEEP]
    with pytest.raises(PreflightError, match="store_reads.tools"):
        check_aggregate_floor(result)


def test_unparseable_items_raise_preflight_error_not_validation_error(tmp_path: Path) -> None:
    """Kontrak `Raises:` menyebut satu kelas; pemanggil hilir menulis `except PreflightError`."""
    result = run(build_store(tmp_path))
    assert result.data is not None
    result.data[DATA_ITEMS][0]["status"] = "kuning"
    with pytest.raises(PreflightError, match="tidak bisa diurai"):
        check_aggregate_floor(result)


@pytest.mark.parametrize(
    ("patch", "needle"),
    [
        ({"headline": "SOL GO: berangkat"}, "berbunyi GO"),
        ({DATA_BLOCKING: ["99_hantu"]}, "99_hantu"),
        ({"key_points": [{"dana": "sudah dikirim"}]}, "key_points"),
        ({"headline": 42}, "headline"),
    ],
)
def test_refusal_summary_may_not_invent_anything(patch: dict[str, Any], needle: str) -> None:
    """Serangan 15: cabang penolakan justru kanal "kenapa" — ia diperiksa, bukan dipercaya."""
    result = preflight("SOL", "hold", store=RefusingStore(), now=NOW)  # type: ignore[arg-type]
    assert result.data is None
    result.summary.update(patch)
    with pytest.raises(PreflightError, match=needle):
        check_aggregate_floor(result)


def test_go_headline_stays_allowed(tmp_path: Path) -> None:
    """Pagar "tidak boleh berbunyi GO" hanya berlaku untuk NO_GO — kartu GO tetap sah."""
    result = run(build_store(tmp_path))
    assert result.data is not None
    assert result.data[DATA_DECISION] == "GO"
    assert "GO" in result.summary["headline"]
    check_aggregate_floor(result)
