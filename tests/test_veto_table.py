"""Tes alat ukur `preflight/veto_table.py` (TASKS 2.1b).

Yang dijaga di sini, berurut kepentingan:

1. **Alat ukur tidak boleh menulis.** Md5 DB sebelum/sesudah identik, tidak ada berkas
   `-wal`/`-journal`, dan koneksinya sendiri menolak `INSERT`.
2. **Jebakan `FRESH_SCAN_LIMIT` nyata dan tertangkap.** Satu tes membuktikan artefaknya ADA
   (jangkar tua → bukti `unspoken` padahal barisnya ada dan segar), dan alat ini membuang
   jangkar seperti itu alih-alih mencacahnya sebagai sifat item.
3. **Kalimat "belum pernah menahan apa pun" lahir dari data**, bukan dari daftar yang ditulis
   di muka: item yang sekali saja merah/unspoken langsung hilang dari kalimat itu.
4. **Cacah skew** ikut jalur produksi (`gather` → `latest_fresh`), jadi perubahan kata di
   `core/store.py` memerahkan tes, bukan membuat angkanya diam-diam nol.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from core.skill import TRUNCATED_MARK, ItemResult, ItemSource
from core.store import FRESH_SCAN_LIMIT, Store, sha256_hex
from preflight import veto_table
from preflight.items import ITEMS, Evidence, evaluate_all, results_of
from preflight.params import DEFAULT_PARAMS
from preflight.skill import gather, tool_args
from preflight.veto_table import (
    CORE_SYMBOLS,
    ItemTally,
    Sweep,
    VetoTableError,
    _rows,
    cause_key,
    main,
    render,
    scan_depth,
    sweep,
)
from tests.helpers_agent import align_inner_clocks

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "docs" / "samples"
TOOLS = (
    "market_overview",
    "monitor_market_sentiment_shift",
    "analyze_token",
    "deep_analysis",
    "compare_tokens",
)
NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)

_CACHE: dict[str, dict[str, Any]] = {}


def sample(tool: str) -> dict[str, Any]:
    if tool not in _CACHE:
        _CACHE[tool] = json.loads((SAMPLES / f"{tool}.json").read_text(encoding="utf-8"))["result"]
    return json.loads(json.dumps(_CACHE[tool]))


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`Store()` tanpa argumen mendarat di tmp_path — DB hidup tidak pernah disentuh."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "veto.db"))


def build_store(
    tmp_path: Path,
    *,
    cycles: int = 3,
    step_min: float = 60.0,
    symbol: str = "SOL",
    tools: tuple[str, ...] = TOOLS,
    mutate: Callable[[str, dict[str, Any], int], None] | None = None,
    name: str = "veto.db",
    at: datetime = NOW,
) -> Path:
    """`cycles` siklus perekam palsu, satu baris per tool per siklus, jarak `step_min` menit.

    Siklus 0 adalah yang terbaru. Setiap baris `as_of`-nya diselaraskan dengan `ts_utc`-nya
    (skew nol) supaya `latest_fresh` tidak membuangnya — kecuali bila `mutate` sengaja
    merusaknya.
    """
    path = tmp_path / name
    args = tool_args(symbol, "swing", DEFAULT_PARAMS)
    with Store(path) as store:
        # Ditulis dari yang TERTUA ke yang terbaru supaya `id` naik bersama `ts_utc`, seperti
        # perekam sungguhan — kalau tidak, `max(id) <= --until` mengukur hal lain.
        for cycle in reversed(range(cycles)):
            moment = at - timedelta(minutes=step_min * cycle)
            ts = moment.replace(microsecond=0).isoformat()
            stamp = ts.replace("+00:00", "Z")
            for tool in tools:
                envelope = sample(tool)
                envelope["as_of"] = stamp
                align_inner_clocks(envelope, stamp)
                if mutate is not None:
                    mutate(tool, envelope, cycle)
                store.insert_snapshot(tool, args[tool], envelope, ts_utc=ts)
    return path


def run_sweep(path: Path, *, until: datetime = NOW, symbols: tuple[str, ...] = ("SOL",)) -> Sweep:
    with Store(path, read_only=True) as store:
        return sweep(store, until=until, symbols=symbols)


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324 - sidik jari berkas, bukan kripto


# --- batas modul --------------------------------------------------------------------------
def test_core_symbols_match_the_recorder() -> None:
    """Salinan `CORE_SYMBOLS` tidak boleh melenceng dari yang benar-benar direkam."""
    from core.recorder import CORE_SYMBOLS as RECORDER_SYMBOLS

    assert CORE_SYMBOLS == RECORDER_SYMBOLS


def test_module_never_imports_a_live_client() -> None:
    """Alat ukur membaca store; ia tidak boleh punya jalan menuju HTTP sama sekali."""
    tree = ast.parse((ROOT / "preflight" / "veto_table.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not {name for name in imported if name.split(".")[0] in {"httpx", "requests", "socket"}}
    assert "core.ryo_client" not in imported
    assert "core.recorder" not in imported


# --- nol tulis ----------------------------------------------------------------------------
def test_sweep_leaves_the_database_byte_identical(tmp_path: Path) -> None:
    """AC(4): md5 sebelum == sesudah, dan tidak ada berkas jurnal yang tertinggal."""
    path = build_store(tmp_path)
    before = md5(path)
    result = run_sweep(path)
    assert result.anchors_used == 3
    assert md5(path) == before
    assert not list(tmp_path.glob("*-wal"))
    assert not list(tmp_path.glob("*-journal"))
    assert not list(tmp_path.glob("*-shm"))


def test_the_connection_the_sweep_uses_refuses_to_write(tmp_path: Path) -> None:
    """Bukan janji: koneksinya sendiri (`mode=ro` + `PRAGMA query_only`) menolak INSERT."""
    path = build_store(tmp_path, cycles=1)
    with Store(path, read_only=True) as store:
        assert store.read_only
        with pytest.raises(Exception, match="readonly|read-only|attempt to write"):
            store._db.execute("INSERT INTO snapshots (tool) VALUES ('x')")


def test_cli_opens_the_store_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    real = Store

    def spy(*args: Any, **kwargs: Any) -> Store:
        seen.update(kwargs)
        return real(*args, **kwargs)

    path = build_store(tmp_path, cycles=1)
    monkeypatch.setattr(veto_table, "Store", spy)
    assert main(["--db", str(path), "--until", NOW.isoformat(), "--symbols", "SOL"]) == 0
    assert seen == {"read_only": True}


# --- jebakan FRESH_SCAN_LIMIT -------------------------------------------------------------
def test_the_scan_limit_artefact_is_real(tmp_path: Path) -> None:
    """Kontrol positif: TANPA penjaga, jangkar tua terbaca `unspoken` walau buktinya ada.

    Jangkar tertua di store ini punya barisnya sendiri, segar dan utuh — tetapi
    ``latest_fresh`` hanya memindai 64 baris teratas menurut `ts_utc`, dan semuanya lebih baru
    dari jangkar itu. Tes ini memaku artefaknya supaya penjaga di alat ukur tidak pernah
    dianggap paranoia.
    """
    cycles = FRESH_SCAN_LIMIT + 6
    path = build_store(tmp_path, cycles=cycles, step_min=30.0)
    oldest = NOW - timedelta(minutes=30.0 * (cycles - 1))
    with Store(path, read_only=True) as store:
        gathered = gather(store, "SOL", "swing", DEFAULT_PARAMS, now=oldest)
        # Baris untuk jangkar ini ADA di store dan umurnya 0 menit …
        rows = store._select(
            "SELECT COUNT(*) FROM snapshots WHERE tool = ? AND args_hash = ? AND ts_utc = ?",
            ("analyze_token", sha256_hex({"symbol": "SOL"}), oldest.replace(microsecond=0).isoformat()),
        )
        assert rows[0][0] == 1
        # … tetapi ia tidak pernah dilihat: yang keluar adalah bundel kosong.
        assert gathered.envelopes == {}
        evidence = Evidence.from_envelopes("SOL", gathered.envelopes, params=DEFAULT_PARAMS)
        statuses = {item.id: item.status for item in results_of(evaluate_all(evidence))}
        assert statuses["05_verdict_quick"] == "unspoken"


def test_scan_limited_anchors_are_dropped_and_counted_not_scored(tmp_path: Path) -> None:
    """AC: artefak di atas tidak boleh masuk tabel; ia dibuang, dicacah, dan disebut."""
    cycles = FRESH_SCAN_LIMIT + 6
    path = build_store(tmp_path, cycles=cycles, step_min=30.0)
    result = run_sweep(path)
    assert result.anchors_scan_limited > 0
    assert result.anchors_used + result.anchors_scan_limited == cycles
    assert result.tallies["05_verdict_quick"].n == result.anchors_used
    assert result.deepest_scan <= FRESH_SCAN_LIMIT
    text = render(result)
    assert f"{result.anchors_scan_limited} dibuang batas pindai" in text
    assert f"terdalam {result.deepest_scan} dari batas {FRESH_SCAN_LIMIT}" in text


def test_scan_depth_counts_future_window_and_unreadable_rows() -> None:
    anchor = NOW
    stamps = [
        NOW + timedelta(minutes=10),  # lebih baru dari horizon: makan slot pindai
        NOW - timedelta(minutes=5),  # kandidat di dalam jendela
        NOW - timedelta(minutes=600),  # di luar jendela: tidak dihitung
    ]
    assert scan_depth(stamps, 0, anchor, DEFAULT_PARAMS.max_age_min) == 2
    assert scan_depth(stamps, 3, anchor, DEFAULT_PARAMS.max_age_min) == 5


# --- determinisme & --until ---------------------------------------------------------------
def test_two_sweeps_with_the_same_until_render_identical_text(tmp_path: Path) -> None:
    path = build_store(tmp_path, cycles=4)
    first = render(run_sweep(path))
    second = render(run_sweep(path))
    assert first == second


def test_until_bounds_the_anchors(tmp_path: Path) -> None:
    path = build_store(tmp_path, cycles=4, step_min=60.0)
    full = run_sweep(path)
    clipped = run_sweep(path, until=NOW - timedelta(minutes=90))
    assert full.anchors_used == 4
    assert clipped.anchors_used == 2
    assert clipped.tallies["05_verdict_quick"].n == 2
    assert clipped.max_id_until is not None
    assert clipped.max_id_until < (full.max_id_until or 0)


def test_cli_prints_the_table_and_exits_zero(tmp_path: Path) -> None:
    path = build_store(tmp_path, cycles=2)
    proc = subprocess.run(
        [sys.executable, "-m", "preflight.veto_table", "--db", str(path), "--until", NOW.isoformat()],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    assert "item                    n  green   red  unspoken" in proc.stdout
    assert "01_freshness" in proc.stdout
    assert DEFAULT_PARAMS.params_hash() in proc.stdout


def test_cli_rejects_an_unreadable_until(tmp_path: Path) -> None:
    path = build_store(tmp_path, cycles=1)
    assert main(["--db", str(path), "--until", "kemarin"]) == 2


# --- cacah skew ---------------------------------------------------------------------------
def test_rows_whose_ts_utc_fights_their_as_of_are_counted_not_hidden(tmp_path: Path) -> None:
    """Baris ber-skew (bentuk fixture TASKS 0.19) dibuang jalur produksi; alat ini mencacahnya."""

    def break_clock(tool: str, envelope: dict[str, Any], cycle: int) -> None:
        if tool == "market_overview" and cycle == 0:
            envelope["as_of"] = "2026-09-01T03:47:12Z"

    path = build_store(tmp_path, cycles=1, mutate=break_clock)
    result = run_sweep(path)
    assert len(result.skew_skipped_ids) == 1
    assert result.skew_skipped_hits == 1
    assert "1 snapshot berbeda" in render(result)
    # Konsekuensinya jujur: item yang membaca tool itu jadi unspoken, bukan hijau.
    assert result.tallies["03_regime"].status["unspoken"] == 1


# --- kalimat AC(3) lahir dari data ---------------------------------------------------------
def _tallied(counts: dict[str, tuple[int, int, int]]) -> Sweep:
    result = Sweep(db_path=Path("x.db"), until=NOW, symbols=("SOL",), params=DEFAULT_PARAMS)
    result.tallies = {item.id: ItemTally(item_id=item.id) for item in ITEMS}
    for item_id, (green, red, unspoken) in counts.items():
        tally = result.tallies[item_id]
        tally.status["green"] = green
        tally.status["unspoken"] = unspoken
        for index in range(red):
            tally.add(
                ItemResult(
                    id=item_id,
                    status="red",
                    value={"why": "because"},
                    source=ItemSource(tool="t", field="f", snapshot_id=index + 1),
                ),
                f"2026-09-0{index % 5 + 1}T00:00:00Z",
            )
    return result


def test_the_never_held_anything_back_sentence_is_generated_from_the_counts() -> None:
    counts = {item.id: (12, 0, 0) for item in ITEMS}
    counts["06_confluence"] = (11, 1, 0)
    counts["10_relative"] = (11, 0, 1)
    text = render(_tallied(counts))
    assert "01_freshness: belum pernah menahan apa pun dalam n=12 amatan" in text
    # Sekali merah / sekali diam sudah cukup untuk kehilangan kalimat itu.
    assert "06_confluence: belum pernah" not in text
    assert "10_relative: belum pernah" not in text
    assert "10_relative: 0x merah dalam n=12 amatan, tetapi 1x tidak tersebut" in text


def test_no_item_is_named_as_silent_when_every_item_has_spoken_up() -> None:
    counts = {item.id: (10, 2, 0) for item in ITEMS}
    text = render(_tallied(counts))
    assert "tidak ada: setiap item pernah merah atau pernah unspoken" in text
    assert "belum pernah menahan apa pun dalam" not in text


def test_end_to_end_counts_add_up_to_n(tmp_path: Path) -> None:
    path = build_store(tmp_path, cycles=3)
    result = run_sweep(path)
    for tally in result.tallies.values():
        assert tally.n == result.anchors_used
        assert tally.status["green"] + tally.status["red"] + tally.status["unspoken"] == tally.n


# --- sidik jari nilai penyebab -------------------------------------------------------------
def test_cause_key_keeps_raw_values_and_bounds_giant_lists() -> None:
    assert cause_key(None) == "null"
    assert cause_key({}) == "{}"
    assert cause_key({"state": "MIXED", "score": 0.5}) == "score=0.5, state=MIXED"
    assert cause_key({"gates": ["momentum", "market_activity"]}) == "gates=[momentum|market_activity]"
    # Item 9 pernah membawa 50.000 target: dicacah, tidak dicetak.
    assert cause_key({"targets": list(range(50_000))}) == "targets=n=50000"
    assert cause_key({"deep": {"a": {"b": {"c": 1}}}}) == "deep.a.b.c=…"
    assert cause_key({"text": "x" * 80}).endswith(TRUNCATED_MARK)


def test_cause_key_escapes_control_characters_in_values_and_keys() -> None:
    """Versi pertama tes ini MENGUNCI perilaku yang salah (nilai mentah lolos apa adanya).

    `ItemResult.value` tidak dibersihkan siapa pun (`core/skill.py` hanya menjaga `id` dan
    `reason`), jadi escape ANSI di dalamnya sampai ke stdout dan bisa menimpa baris tabel.
    Yang benar: setiap string DAN setiap kunci lewat `clean_text`.
    """
    key = cause_key({"data_date": "\x1b[10A\x1b[1Gpalsu"})
    assert "\x1b" not in key
    assert "\\u001b" in key
    key = cause_key({"\x1b[2Kkunci": "nilai"})
    assert "\x1b" not in key
    assert "\\u001b" in key
    # U+202E (pembalik arah teks) juga tidak boleh lolos ke terminal.
    assert "\u202e" not in cause_key({"symbol": "BTC\u202e"})


ANSI_FORGERY = "\x1b[10A\x1b[1G06_confluence      226    226     0         0  —  (tidak pernah merah)\x1b[K"


def _crowded_with_payload(payload: str) -> Callable[[str, dict[str, Any], int], None]:
    """Mutator: memerahkan item 8 DAN menaruh payload di `funding.data_date` (jalur nyata)."""

    def mutate(tool: str, envelope: dict[str, Any], cycle: int) -> None:
        if tool != "monitor_market_sentiment_shift":
            return
        evidence = envelope["data"]["evidence"]
        evidence["funding"]["crowding_state"] = "long_crowding"
        evidence["liquidation"]["pressure_state"] = "extreme"
        evidence["funding"]["data_date"] = payload

    return mutate


def test_a_tool_payload_cannot_forge_a_row_of_this_table(tmp_path: Path) -> None:
    """Serangan KRITIS security-reviewer 2.1b, ditulis ulang sebagai tes.

    Escape ANSI di `data_date` memindahkan kursor ke atas dan menimpa baris tabel yang alat ini
    sendiri hitung. Setelah `clean_text`, ia menjadi teks yang inert DAN terlihat — dan cacah
    item 6 di tabel tetap milik alat ukur, bukan milik payload.
    """
    path = build_store(tmp_path, cycles=2, mutate=_crowded_with_payload(ANSI_FORGERY))
    result = run_sweep(path)
    assert result.tallies["08_derivatives"].status["red"] == 2  # jalurnya benar-benar hidup
    text = render(result)
    assert "\x1b" not in text
    assert "\\u001b[10A" in text  # payload-nya TERLIHAT, bukan dibuang diam-diam
    # Baris tabel item 6 tetap yang dihitung alat ukur (0 merah di fixture ini, bukan klaim palsu).
    rows = [line for line in text.splitlines() if line.startswith("06_confluence")]
    assert len(rows) == 1
    assert "tidak pernah merah" in rows[0]
    assert rows[0].split()[1] == str(result.anchors_used)


def test_the_data_date_that_carries_the_payload_is_cleaned_at_the_item_too(tmp_path: Path) -> None:
    """Ditutup di sumbernya juga: `value` item 8 sendiri tidak boleh membawa ESC mentah.

    `ItemResult.value` ikut ke `SkillResult.data` dan `decisions.card_json`, jadi membersihkan
    di alat ukur saja berarti memindahkan masalah, bukan menutupnya (temuan TINGGI).
    """
    path = build_store(tmp_path, cycles=1, mutate=_crowded_with_payload(ANSI_FORGERY))
    with Store(path, read_only=True) as store:
        gathered = gather(store, "SOL", "swing", DEFAULT_PARAMS, now=NOW)
        evidence = Evidence.from_envelopes(
            "SOL", gathered.envelopes, params=DEFAULT_PARAMS, snapshot_ids=gathered.snapshot_ids
        )
        item = next(i for i in results_of(evaluate_all(evidence)) if i.id == "08_derivatives")
    assert item.status == "red"
    spoken = item.value["funding_data_date"]
    assert "\x1b" not in spoken  # ESC mentah tidak pernah masuk `value`
    assert "\\u001b" in spoken  # ia disebut, dalam bentuk yang tidak bisa menggerakkan kursor
    assert "\x1b" not in json.dumps(item.value)


def test_argv_text_printed_in_the_header_is_cleaned(tmp_path: Path) -> None:
    path = build_store(tmp_path, cycles=1)
    result = run_sweep(path, symbols=("SOL", "BTC\u202e"))
    text = render(result)
    assert "\u202e" not in text
    assert "\\u202e" in text


# --- alat ukur tidak boleh memegang koneksi yang bisa menulis ------------------------------
def test_sweep_refuses_a_writable_store(tmp_path: Path) -> None:
    """`Store._select` mengeksekusi SQL apa pun; jadi koneksinya sendiri yang harus baca-saja."""
    path = build_store(tmp_path, cycles=1)
    with Store(path) as writable, pytest.raises(VetoTableError, match="read_only"):
        sweep(writable, until=NOW, symbols=("SOL",))


def test_the_sweep_only_ever_runs_select(tmp_path: Path) -> None:
    path = build_store(tmp_path, cycles=1)
    with Store(path, read_only=True) as store:
        assert _rows(store, "SELECT COUNT(*) FROM snapshots")[0][0] > 0
        with pytest.raises(VetoTableError, match="hanya menjalankan SELECT"):
            _rows(store, "INSERT INTO snapshots (tool) VALUES ('x')")


# --- independensi amatan --------------------------------------------------------------------
def test_red_counts_are_reported_next_to_the_distinct_evidence_behind_them(tmp_path: Path) -> None:
    """Dua simbol, satu envelope market-wide: 2 merah TAPI hanya 1 snapshot sumber.

    Ini yang membuat `n` tidak terdengar lebih kuat daripada buktinya (temuan TINGGI).
    """
    for symbol in ("SOL", "BTC"):
        build_store(
            tmp_path,
            cycles=1,
            symbol=symbol,
            name="shared.db",
            mutate=lambda tool, envelope, cycle: (
                envelope["data"].__setitem__("regime", "risk_off") if tool == "market_overview" else None
            ),
        )
    result = run_sweep(tmp_path / "shared.db", symbols=("SOL", "BTC"))
    tally = result.tallies["03_regime"]
    assert tally.status["red"] == 2
    assert len(tally.red_sources) == 1
    text = render(result)
    assert "1/2" in text
    assert "market_overview → SATU args_hash untuk 2 simbol" in text
    assert "TIDAK independen" in text


def test_cause_distribution_counts_identical_values_together(tmp_path: Path) -> None:
    """Dua amatan dengan nilai penyebab yang sama = satu baris distribusi ber-cacah 2."""

    def force_red(tool: str, envelope: dict[str, Any], cycle: int) -> None:
        if tool == "market_overview":
            envelope["data"]["regime"] = "risk_off"

    path = build_store(tmp_path, cycles=2, mutate=force_red)
    result = run_sweep(path)
    tally = result.tallies["03_regime"]
    assert tally.status["red"] == 2
    assert len(tally.red_causes) == 1
    assert next(iter(tally.red_causes.values())) == 2
    assert "regime=risk_off" in next(iter(tally.red_causes))
