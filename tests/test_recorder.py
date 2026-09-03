"""Tes TASKS 0.5 — core/recorder.py.

Semua tes OFFLINE dan hanya menulis ke `tmp_path`:

* klien = :class:`core.ryo_client.FixtureClient` (docs/samples) atau pembungkusnya —
  tidak ada jaringan, tidak ada kuota RYO yang terpakai;
* `DB_PATH` di-monkeypatch ke `tmp_path` untuk SETIAP tes (fixture autouse), sehingga tidak
  ada jalur yang bisa menyentuh DB hidup — snapshotnya tidak bisa direkam ulang;
* `load_dotenv` dimatikan di semua tes supaya `main()` tidak pernah membaca berkas `.env`
  milik pemilik repo; fungsinya diuji terpisah dengan berkas buatan di `tmp_path`;
* tidak ada `time.sleep` sungguhan: `sleep_fn`/`time_fn` selalu disuntik.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from core.recorder import (
    CORE_SYMBOLS,
    FOLLOWUP_DONE_AFTER_H,
    FOLLOWUP_MAX_AGE_H,
    MAX_COMPARES_PER_CYCLE,
    MAX_INTERVAL_S,
    CycleReport,
    Recorder,
    RecorderConfig,
    RecorderConfigError,
    RecorderLockError,
    _configure_logging,
    _normalize_symbol,
    acquire_single_instance_lock,
    build_client,
    compare_args,
    load_dotenv,
    main,
)
from core.ryo_client import BaseRyoClient, FixtureClient, RyoError, RyoNetworkError, RyoProtocolError
from core.sources import SourceNotApprovedError
from core.store import Store, StoreError, sha256_hex

ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = ROOT / "data" / "ryo.db"
LOUD_KEY = "KEYVALUEMUSTNOTLEAK123"

SCAN_TOP = ["USELESS", "ARB", "PONS", "CASHCAT", "BONK"]  # docs/samples/scan_market.json


# --- perkakas ----------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tidak satu pun tes boleh mewarisi DB_PATH/kredensial nyata dari shell atau berkas env."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "recorder.db"))
    monkeypatch.setenv("RECORDER_LOCK_PATH", str(tmp_path / "recorder.lock"))
    for name in (
        "RYO_MCP_URL",
        "RYO_MCP_KEY",
        "WATCHLIST",
        "RECORDER_MAX_SYMBOLS",
        "RECORDER_INTERVAL_S",
        "RECORDER_MAX_FOLLOWUPS",
        "RECORDER_MAX_FAILED_CYCLES",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("core.recorder.load_dotenv", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _restore_logging():
    """`main()` menyetel level logger global; kembalikan seperti semula agar berkas tes lain aman."""
    names = ("httpx", "httpcore", "hpack", "anthropic")
    saved = [(logging.getLogger(n), logging.getLogger(n).level) for n in ("", *names)]
    yield
    for logger, level in saved:
        logger.setLevel(level)


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "recorder.db") as s:
        yield s


class FakeClock:
    """Jam + tidur palsu; tidur hanya memajukan waktu."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class RecordingClient(BaseRyoClient):
    """FixtureClient + kemampuan menyuntik kegagalan per tool (ADR-004)."""

    def __init__(self, fail: dict[str, BaseException] | None = None) -> None:
        self._inner = FixtureClient()
        self._fail = fail or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((tool, dict(args or {})))
        failure = self._fail.get(tool)
        if failure is not None:
            raise failure
        return self._inner.call(tool, args)

    def health(self) -> dict[str, Any]:
        return self._inner.health()

    def whoami(self) -> dict[str, Any]:
        return self._inner.whoami()

    def catalog(self) -> list[dict[str, Any]]:
        return self._inner.catalog()

    def _safe(self, text: str) -> str:
        """Double ini tidak memegang kredensial; kebijakannya ditulis eksplisit karena
        `BaseRyoClient._safe` abstrak (klien baru tidak boleh diam-diam tidak meredaksi)."""
        return text

    def tools_called(self, tool: str) -> list[dict[str, Any]]:
        return [args for name, args in self.calls if name == tool]

    def symbols_called(self, tool: str) -> list[str]:
        return [args.get("symbol") for args in self.tools_called(tool)]


def make_recorder(store: Store, client: BaseRyoClient | None = None, **config: Any) -> Recorder:
    config.setdefault("watchlist", ("SOL", "BTC"))
    config.setdefault("max_symbols", 3)
    return Recorder(client or RecordingClient(), store, RecorderConfig(**config))


def rows(path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def seed_analyze(store: Store, symbol: str, hours_ago: float) -> int:
    """Baris `analyze_token` buatan dengan umur tertentu (jalur `ts_utc` khusus tes)."""
    ts = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
    envelope = {
        "schema_version": "1.0",
        "tool": "analyze_token",
        "status": "ok",
        "data_mode": "live",
        "as_of": "2026-09-01T03:47:12Z",
        "request": {"symbol": symbol},
        "data": {"verdict": "neutral"},
        "summary": {"headline": "x", "key_points": []},
        "availability": {"market_data": "available"},
        "warnings": [],
    }
    return store.insert_snapshot("analyze_token", {"symbol": symbol}, envelope, ts_utc=ts)


# --- konfigurasi -------------------------------------------------------------------------
def test_config_defaults() -> None:
    config = RecorderConfig.from_env({})
    assert config.watchlist == CORE_SYMBOLS
    assert config.max_symbols == 20
    assert config.interval_s == 3600.0
    assert config.followup_max_age_h == FOLLOWUP_MAX_AGE_H


def test_config_reads_watchlist_uppercased_and_deduplicated() -> None:
    config = RecorderConfig.from_env({"WATCHLIST": " sol , BTC ,sol, ,eth"})
    assert config.watchlist == ("SOL", "BTC", "ETH")


@pytest.mark.parametrize(
    "bad",
    [
        {"RECORDER_MAX_SYMBOLS": "nol"},
        {"RECORDER_MAX_SYMBOLS": "0"},
        {"RECORDER_INTERVAL_S": "-1"},
        {"RECORDER_INTERVAL_S": "sejam"},
        {"WATCHLIST": " , , "},
    ],
)
def test_config_refuses_to_guess_bad_values(bad: dict[str, str]) -> None:
    """Konfigurasi salah = berhenti dengan pesan, bukan diam-diam memakai default."""
    with pytest.raises(RecorderConfigError):
        RecorderConfig.from_env(bad)


# --- satu siklus penuh ---------------------------------------------------------------------
def test_full_cycle_records_every_call(store: Store, tmp_path: Path) -> None:
    client = RecordingClient()
    report = make_recorder(store, client).run_cycle()

    # 2 market-wide + 1 scan + 3 simbol x (analyze + deep) + 2 banding watchlist = 11 panggilan.
    expected = {
        "market_overview": 1,
        "monitor_market_sentiment_shift": 1,
        "scan_market": 1,
        "analyze_token": 3,
        "deep_analysis": 3,
        "compare_tokens": 2,
    }
    assert report.recorded == expected
    assert report.n_recorded == 11
    assert report.failed == {}
    assert len(report.snapshot_ids) == len(set(report.snapshot_ids)) == 11
    counts = dict(rows(tmp_path / "recorder.db", "SELECT tool, COUNT(*) FROM snapshots GROUP BY tool"))
    assert counts == expected


def test_cycle_uses_core_symbols_then_scan_candidates(store: Store) -> None:
    client = RecordingClient()
    report = make_recorder(store, client).run_cycle()
    assert report.symbols == ("SOL", "BTC", SCAN_TOP[0])
    assert client.symbols_called("analyze_token") == ["SOL", "BTC", SCAN_TOP[0]]
    assert client.symbols_called("deep_analysis") == ["SOL", "BTC", SCAN_TOP[0]]


def test_stored_args_match_the_arguments_actually_sent(store: Store, tmp_path: Path) -> None:
    """`args_json`/`args_hash` = permintaan yang benar-benar dikirim (bentuk sama dgn baris lama)."""
    client = RecordingClient()
    make_recorder(store, client, watchlist=("SOL",), max_symbols=1).run_cycle()

    stored = {
        tool: json.loads(args_json)
        for tool, args_json in rows(tmp_path / "recorder.db", "SELECT tool, args_json FROM snapshots")
    }
    assert stored == {
        "market_overview": {},
        "monitor_market_sentiment_shift": {},
        "scan_market": {"top_n": 1},
        "analyze_token": {"symbol": "SOL"},
        "deep_analysis": {"symbol": "SOL", "include_perp": True},
        "compare_tokens": {"symbols": "SOL, BNB, ETH", "intent": "swing"},
    }
    assert dict(client.calls) == stored  # yang dikirim == yang disimpan

    hashes = dict(rows(tmp_path / "recorder.db", "SELECT tool, args_hash FROM snapshots"))
    assert hashes["analyze_token"] == sha256_hex({"symbol": "SOL"})
    assert hashes["deep_analysis"] == sha256_hex({"include_perp": True, "symbol": "SOL"})
    assert hashes["market_overview"] == sha256_hex({})


def test_scan_top_n_leaves_room_for_core_symbols(store: Store) -> None:
    client = RecordingClient()
    make_recorder(store, client, watchlist=("SOL", "BTC", "ETH", "BNB"), max_symbols=25).run_cycle()
    assert client.tools_called("scan_market") == [{"top_n": 21}]  # sama dgn baris nyata perekam


def test_scan_top_n_never_drops_below_one(store: Store) -> None:
    client = RecordingClient()
    make_recorder(store, client, watchlist=("SOL", "BTC", "ETH"), max_symbols=2).run_cycle()
    assert client.tools_called("scan_market") == [{"top_n": 1}]


def test_watchlist_truncated_to_max_symbols(store: Store) -> None:
    client = RecordingClient()
    watchlist = ("SOL", "BTC", "ETH", "BNB", "ARB", "OP")
    report = make_recorder(store, client, watchlist=watchlist, max_symbols=3).run_cycle()
    assert report.symbols == ("SOL", "BTC", "ETH")
    assert client.symbols_called("deep_analysis") == ["SOL", "BTC", "ETH"]


def test_candidates_never_duplicate_core_symbols(store: Store) -> None:
    client = RecordingClient()
    report = make_recorder(store, client, watchlist=(SCAN_TOP[0],), max_symbols=3).run_cycle()
    assert report.symbols == (SCAN_TOP[0], SCAN_TOP[1], SCAN_TOP[2])
    assert len(set(report.symbols)) == 3


# --- compare_tokens (TASKS 2.0c) -----------------------------------------------------------
def test_compare_tokens_is_recorded_for_watchlist_symbols_only(store: Store) -> None:
    """Kandidat `scan_market` TIDAK dibandingkan: ~42 dtk per panggilan, dan item 10 hanya
    membutuhkan baris segar untuk simbol inti."""
    client = RecordingClient()
    report = make_recorder(store, client).run_cycle()

    assert report.symbols == ("SOL", "BTC", SCAN_TOP[0])
    assert report.compares == ("SOL", "BTC")
    assert client.tools_called("compare_tokens") == [
        {"symbols": "SOL, BNB, ETH", "intent": "swing"},
        {"symbols": "BTC, BNB, ETH", "intent": "swing"},
    ]


def test_compare_tokens_is_capped_by_the_cycle_budget(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    """Anggaran mengikat, DAN pemotongannya menyebut simbol yang kehilangan cakupan.

    Temuan SEDANG security-reviewer (TASKS 2.0c): `WATCHLIST` yang diperlebar lewat env
    membuat simbol ke-5 dst. tidak pernah punya baris `compare_tokens`, sehingga item 10
    (WAJIB) `unspoken` selamanya. Degradasinya jujur; sebabnya harus terlihat.
    """
    client = RecordingClient()
    watchlist = ("SOL", "BTC", "ETH", "BNB", "ARB", "OP")
    with caplog.at_level(logging.WARNING, logger="core.recorder"):
        report = make_recorder(store, client, watchlist=watchlist, max_symbols=6).run_cycle()

    assert len(report.compares) == MAX_COMPARES_PER_CYCLE == 4
    assert report.compares == ("SOL", "BTC", "ETH", "BNB")
    assert len(client.tools_called("compare_tokens")) == 4

    # Dua simbol terakhir disebut NAMANYA — di CycleReport dan di WARNING.
    assert report.compares_dropped == ("ARB", "OP")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "compare_tokens" in r.message]
    assert len(warnings) == 1
    text = warnings[0].getMessage()
    assert "ARB, OP" in text
    assert "item 10" in text and "unspoken" in text


def test_compare_budget_that_does_not_bite_is_silent(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    """Watchlist 4 simbol (produksi hari ini) → tidak ada yang dipotong, tidak ada WARNING."""
    client = RecordingClient()
    with caplog.at_level(logging.WARNING, logger="core.recorder"):
        report = make_recorder(store, client, watchlist=CORE_SYMBOLS, max_symbols=6).run_cycle()

    assert report.compares == CORE_SYMBOLS
    assert report.compares_dropped == ()
    assert "compare_tokens" not in caplog.text


def test_dropped_compare_symbols_are_named_in_the_cycle_log(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """Ringkasan satu baris per siklus juga menyebutnya, supaya `daemons.sh status` cukup."""
    clock = FakeClock()
    recorder = make_recorder(
        store, RecordingClient(), watchlist=("SOL", "BTC", "ETH", "BNB", "ARB", "OP"), max_symbols=6
    )
    with caplog.at_level(logging.INFO, logger="core.recorder"):
        recorder.run(max_cycles=1, sleep_fn=clock.sleep, time_fn=clock.time)

    line = [r.getMessage() for r in caplog.records if "siklus selesai" in r.getMessage()][-1]
    assert "banding=4 (tanpa banding: ARB, OP)" in line


def test_compare_args_never_compares_a_symbol_with_itself() -> None:
    """`BNB` ada di `COMPARE_PEERS`; dedupe mempertahankan urutan (`args_hash` = teksnya)."""
    assert compare_args("BNB") == {"symbols": "BNB, ETH", "intent": "swing"}
    assert compare_args("ETH") == {"symbols": "ETH, BNB", "intent": "swing"}
    assert compare_args("SOL") == {"symbols": "SOL, BNB, ETH", "intent": "swing"}


@pytest.mark.parametrize("symbol", CORE_SYMBOLS)
def test_recorder_compare_args_match_what_preflight_looks_up(symbol: str) -> None:
    """Kesamaan args perekam vs pembaca store dijaga TES, bukan impor (ADR-002:
    `core/` tidak boleh mengimpor `preflight/`).

    Tes ini menggigit: mengubah `preflight.params.Params.peers` (atau `COMPARE_PEERS` di
    perekam) membuatnya gagal — dan kegagalan itu adalah peringatan bahwa setiap baris
    `compare_tokens` yang direkam sejak sekarang tidak akan pernah ditemukan item 10.
    """
    from preflight.skill import tool_args

    assert compare_args(symbol) == tool_args(symbol, "swing")["compare_tokens"]


def test_recorded_compare_args_are_found_by_the_store_lookup_shape(store: Store, tmp_path: Path) -> None:
    """Bukan sekadar dict yang sama: `args_hash` baris yang DIREKAM == hash args pencarian."""
    from preflight.skill import tool_args

    client = RecordingClient()
    make_recorder(store, client, watchlist=("SOL",), max_symbols=1).run_cycle()
    stored = dict(
        rows(
            tmp_path / "recorder.db",
            "SELECT tool, args_hash FROM snapshots WHERE tool = 'compare_tokens'",
        )
    )
    assert stored["compare_tokens"] == sha256_hex(tool_args("SOL", "swing")["compare_tokens"])


def test_compare_tokens_failure_does_not_stop_the_cycle(store: Store) -> None:
    client = RecordingClient({"compare_tokens": RyoNetworkError("compare_tokens: jaringan gagal")})
    report = make_recorder(store, client).run_cycle()
    assert report.failed == {"compare_tokens": 2}
    assert report.recorded.get("deep_analysis") == 3
    assert "compare_tokens" not in report.recorded


# --- kegagalan per komponen (ADR-004) ------------------------------------------------------
@pytest.mark.parametrize("broken", ["market_overview", "monitor_market_sentiment_shift", "deep_analysis"])
def test_one_failing_tool_does_not_stop_the_cycle(store: Store, broken: str) -> None:
    client = RecordingClient({broken: RyoNetworkError(f"{broken}: jaringan gagal")})
    report = make_recorder(store, client).run_cycle()

    assert report.failed[broken] > 0
    assert broken not in report.recorded
    for tool in ("market_overview", "monitor_market_sentiment_shift", "scan_market", "analyze_token"):
        if tool != broken:
            assert report.recorded.get(tool, 0) > 0
    assert report.symbols == ("SOL", "BTC", SCAN_TOP[0])  # siklus tetap selesai


def test_failed_scan_still_records_core_symbols(store: Store) -> None:
    client = RecordingClient({"scan_market": RyoNetworkError("scan_market: jaringan gagal")})
    report = make_recorder(store, client).run_cycle()
    assert report.symbols == ("SOL", "BTC")  # tanpa kandidat — bukan simbol karangan
    assert report.recorded["analyze_token"] == 2
    assert report.failed == {"scan_market": 1}


def test_protocol_error_is_logged_loudly_and_cycle_continues(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """Status di luar enum = 100% data tool itu hilang; harus ERROR, tidak boleh senyap."""
    client = RecordingClient(
        {"deep_analysis": RyoProtocolError("deep_analysis: status di luar enum api-facts §A: 'degraded'")}
    )
    with caplog.at_level(logging.DEBUG, logger="core.recorder"):
        report = make_recorder(store, client).run_cycle()

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "RyoProtocolError harus tercatat, bukan senyap"
    text = " ".join(r.getMessage() for r in errors)
    assert "api-facts" in text and "deep_analysis" in text and "degraded" in text
    assert report.recorded["analyze_token"] == 3


def test_store_failure_does_not_stop_the_cycle(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    real = store.insert_snapshot

    def flaky(tool: str, args: Any, envelope: Any, **kwargs: Any) -> int:
        if tool == "market_overview":
            raise StoreError("gagal menyimpan (simulasi)")
        return real(tool, args, envelope, **kwargs)

    monkeypatch.setattr(store, "insert_snapshot", flaky)
    report = make_recorder(store).run_cycle()
    assert report.failed == {"market_overview": 1}
    assert report.recorded["deep_analysis"] == 3


def test_unexpected_client_exception_is_contained_per_component(store: Store) -> None:
    """Kontrak klien menjanjikan RyoError; bug di luar itu pun tidak menjatuhkan tool lain."""
    client = RecordingClient({"market_overview": RuntimeError("bug klien")})
    report = make_recorder(store, client).run_cycle()
    assert report.failed == {"market_overview": 1}
    assert report.n_recorded == 10


def test_malformed_scan_envelope_yields_no_candidates(store: Store) -> None:
    class WeirdScan(RecordingClient):
        def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
            envelope = super().call(tool, args)
            if tool == "scan_market":
                envelope = dict(envelope)
                envelope["data"] = {"candidates": "bukan daftar"}
            return envelope

    report = make_recorder(store, WeirdScan()).run_cycle()
    assert report.symbols == ("SOL", "BTC")
    assert report.recorded["scan_market"] == 1  # envelope aneh tetap direkam apa adanya


# --- follow-up ≤26 jam ---------------------------------------------------------------------
def test_followup_includes_25h_and_excludes_27h(store: Store) -> None:
    seed_analyze(store, "OLD25", 25.0)
    seed_analyze(store, "OLD27", 27.0)
    client = RecordingClient()
    report = make_recorder(store, client, watchlist=("SOL",), max_symbols=1).run_cycle()

    assert report.symbols == ("SOL",)
    assert report.followups == ("OLD25",)
    assert client.symbols_called("analyze_token") == ["SOL", "OLD25"]
    assert client.symbols_called("deep_analysis") == ["SOL"]  # follow-up hanya analyze_token


def test_followup_never_calls_a_watchlist_symbol_twice(store: Store) -> None:
    seed_analyze(store, "SOL", 3.0)
    seed_analyze(store, "BTC", 20.0)
    client = RecordingClient()
    report = make_recorder(store, client, watchlist=("SOL", "BTC"), max_symbols=2).run_cycle()

    assert report.followups == ()
    assert client.symbols_called("analyze_token") == ["SOL", "BTC"]


def test_followup_is_ordered_stalest_first(store: Store) -> None:
    """Simbol yang pembacaan terakhirnya paling lama = paling mendesak untuk outcome T+24j."""
    seed_analyze(store, "FRESH", 2.0)
    seed_analyze(store, "STALE", 25.0)
    seed_analyze(store, "MIDDLE", 12.0)
    report = make_recorder(store, RecordingClient(), watchlist=("SOL",), max_symbols=1).run_cycle()
    assert report.followups == ("STALE", "MIDDLE", "FRESH")


def test_followup_retires_symbol_whose_span_already_covers_24h(store: Store) -> None:
    """Pembacaan 27 j + 2 j = rentang 25 j → outcome T+24 j sudah bisa dinilai, simbol pensiun.

    Ini yang memutus ratchet: tanpa pensiun, tiap follow-up menulis baris baru sehingga
    simbolnya selalu masih di jendela 26 jam pada siklus berikutnya (perekam sementara:
    29 → 42 panggilan analyze_token per siklus, 32x HTTP 429 dalam sehari).
    """
    seed_analyze(store, "BOTH", 27.0)
    seed_analyze(store, "BOTH", 2.0)
    seed_analyze(store, "STALE", 25.0)
    report = make_recorder(store, RecordingClient(), watchlist=("SOL",), max_symbols=1).run_cycle()
    assert report.followups == ("STALE",)


def test_followup_keeps_a_lone_25h_reading(store: Store) -> None:
    """Satu baris berumur 25 j = rentang 0: justru simbol paling mendesak, bukan pensiun."""
    seed_analyze(store, "OLD25", 25.0)
    report = make_recorder(store, RecordingClient(), watchlist=("SOL",), max_symbols=1).run_cycle()
    assert report.followups == ("OLD25",)


def test_followup_budget_keeps_the_most_urgent(store: Store) -> None:
    for hours in (25.0, 20.0, 15.0, 5.0):
        seed_analyze(store, f"S{hours:.0f}", hours)
    client = RecordingClient()
    report = make_recorder(store, client, watchlist=("SOL",), max_symbols=1, max_followups=2).run_cycle()
    assert report.followups == ("S25", "S20")  # dipotong dari ekor, yang terlama didahulukan


def test_followup_budget_is_logged_when_it_bites(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    for hours in (25.0, 20.0):
        seed_analyze(store, f"S{hours:.0f}", hours)
    with caplog.at_level(logging.WARNING, logger="core.recorder"):
        recorder = make_recorder(store, RecordingClient(), watchlist=("SOL",), max_symbols=1, max_followups=1)
        recorder.run_cycle()
    assert "RECORDER_MAX_FOLLOWUPS" in caplog.text


def test_followup_reaches_a_plateau_over_many_cycles(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bukti anti-ratchet: 40 siklus dengan kandidat selalu baru → panggilan/siklus berhenti tumbuh.

    Jam DB dan jam perekam digeser bersama 1 jam per siklus, jadi 40 jam simulasi selesai
    dalam hitungan detik dan seluruhnya offline.
    """

    class SimClock:
        def __init__(self) -> None:
            self.now = datetime(2026, 9, 1, tzinfo=UTC)

        def utc(self) -> datetime:
            return self.now

        def iso(self) -> str:
            return self.now.isoformat()

    class RotatingScan(RecordingClient):
        """Tiap siklus memperkenalkan satu simbol yang belum pernah terlihat."""

        def __init__(self) -> None:
            super().__init__()
            self.cycle = 0

        def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
            envelope = super().call(tool, args)
            if tool == "scan_market":
                envelope = dict(envelope)
                data = dict(envelope["data"])
                data["candidates"] = [{"symbol": f"C{self.cycle:03d}"}]
                envelope["data"] = data
                self.cycle += 1
            return envelope

    clock = SimClock()
    monkeypatch.setattr("core.store._utc_now_iso", clock.iso)
    client = RotatingScan()
    recorder = Recorder(
        client,
        store,
        RecorderConfig(watchlist=("SOL",), max_symbols=2, max_followups=1000),
        now_fn=clock.utc,
    )

    per_cycle: list[int] = []
    for _ in range(40):
        before = len(client.tools_called("analyze_token"))
        recorder.run_cycle()
        per_cycle.append(len(client.tools_called("analyze_token")) - before)
        clock.now += timedelta(hours=1)

    assert per_cycle[5] < per_cycle[20], "24 jam pertama memang menumpuk kewajiban"
    plateau = per_cycle[26:]
    assert len(dict.fromkeys(plateau)) == 1, f"panggilan/siklus masih berubah: {plateau}"
    # 1 watchlist + 1 kandidat baru + kewajiban 23 jam terakhir; jauh dari universe (~91 listing)
    assert plateau[0] <= int(FOLLOWUP_DONE_AFTER_H) + 2
    assert max(per_cycle) == plateau[0]


def test_followup_ignores_other_tools(store: Store) -> None:
    """Hanya `analyze_token` yang jadi dasar follow-up (deep_analysis tidak dipanggil ulang)."""
    envelope = {"tool": "deep_analysis", "status": "ok", "data": {}}
    store.insert_snapshot("deep_analysis", {"symbol": "DEEPONLY", "include_perp": True}, envelope)
    report = make_recorder(store, RecordingClient(), watchlist=("SOL",), max_symbols=1).run_cycle()
    assert report.followups == ()


def test_followup_survives_store_read_failure(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise StoreError("baca gagal")

    monkeypatch.setattr(store, "recent_args", broken)
    report = make_recorder(store, RecordingClient()).run_cycle()
    assert report.followups == ()
    assert report.n_recorded == 11


# --- loop --------------------------------------------------------------------------------
def test_loop_survives_a_cycle_that_raises(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    recorder = make_recorder(store)
    attempts: list[int] = []
    real_cycle = recorder.run_cycle

    def flaky() -> CycleReport:
        attempts.append(len(attempts))
        if len(attempts) <= 2:
            raise RuntimeError("bug siklus")
        return real_cycle()

    recorder.run_cycle = flaky  # type: ignore[method-assign]
    clock = FakeClock()
    with caplog.at_level(logging.ERROR, logger="core.recorder"):
        summary = recorder.run(max_cycles=3, sleep_fn=clock.sleep, time_fn=clock.time)

    assert len(attempts) == 3  # dua siklus gagal TIDAK menghentikan loop
    assert summary.recorded == 11  # siklus ketiga tetap merekam
    assert not summary.aborted
    assert len(clock.sleeps) == 2  # tidur di antara siklus, bukan setelah yang terakhir


def test_loop_sleeps_the_remainder_of_the_interval(store: Store) -> None:
    class SlowClient(RecordingClient):
        def __init__(self, clock: FakeClock) -> None:
            super().__init__()
            self._clock = clock

        def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
            self._clock.t += 10.0  # setiap panggilan "makan" 10 detik
            return super().call(tool, args)

    clock = FakeClock()
    recorder = make_recorder(store, SlowClient(clock), interval_s=600.0)
    recorder.run(max_cycles=2, sleep_fn=clock.sleep, time_fn=clock.time)
    assert clock.sleeps == [600.0 - 110.0]  # 11 panggilan x 10 dtk


def test_loop_never_sleeps_below_one_second(store: Store) -> None:
    class SlowClient(RecordingClient):
        def __init__(self, clock: FakeClock) -> None:
            super().__init__()
            self._clock = clock

        def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
            self._clock.t += 100.0
            return super().call(tool, args)

    clock = FakeClock()
    recorder = make_recorder(store, SlowClient(clock), interval_s=60.0)
    recorder.run(max_cycles=2, sleep_fn=clock.sleep, time_fn=clock.time)
    assert clock.sleeps == [1.0]


def test_run_gives_up_after_consecutive_total_failures(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """Berkas DB hilang/tak bisa ditulis = gagal selamanya sambil membakar kuota → berhenti berisik."""

    class AllDead(RecordingClient):
        def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
            raise RyoNetworkError(f"{tool}: host mati")

    clock = FakeClock()
    recorder = make_recorder(store, AllDead(), max_failed_cycles=3)
    with caplog.at_level(logging.ERROR, logger="core.recorder"):
        summary = recorder.run(max_cycles=100, sleep_fn=clock.sleep, time_fn=clock.time)

    assert summary.aborted is True
    assert summary.cycles == 3  # berhenti pada siklus gagal ketiga, bukan berputar 100x
    assert summary.recorded == 0
    assert "berturut-turut" in caplog.text


def test_run_gives_up_when_the_store_stops_accepting_writes(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skenario TASKS 4.5: DB dihapus di tengah jalan → tulisan gagal terus → keluar ≠ 0."""

    def broken(*args: Any, **kwargs: Any) -> int:
        raise StoreError("attempt to write a readonly database")

    monkeypatch.setattr(store, "insert_snapshot", broken)
    clock = FakeClock()
    summary = make_recorder(store, max_failed_cycles=2).run(
        max_cycles=50, sleep_fn=clock.sleep, time_fn=clock.time
    )
    assert summary.aborted is True
    assert summary.cycles == 2


def test_failure_streak_resets_after_a_good_cycle(store: Store) -> None:
    """Dua siklus gagal lalu satu berhasil TIDAK boleh dihitung sebagai beruntun."""
    recorder = make_recorder(store, max_failed_cycles=3)
    attempts: list[int] = []
    real_cycle = recorder.run_cycle

    def flaky() -> CycleReport:
        attempts.append(len(attempts))
        if len(attempts) in (1, 2, 4, 5):
            raise RuntimeError("bug siklus")
        return real_cycle()

    recorder.run_cycle = flaky  # type: ignore[method-assign]
    clock = FakeClock()
    summary = recorder.run(max_cycles=5, sleep_fn=clock.sleep, time_fn=clock.time)
    assert summary.aborted is False  # streak direset oleh siklus ke-3
    assert summary.cycles == 5
    assert summary.recorded == 11


def test_loop_stops_when_sleep_fails(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    """`sleep` yang gagal tidak boleh berubah menjadi loop tanpa jeda (kuota habis dalam menit)."""

    def broken_sleep(seconds: float) -> None:
        raise OverflowError("timestamp too large to convert to C _PyTime_t")

    with caplog.at_level(logging.ERROR, logger="core.recorder"):
        summary = make_recorder(store).run(sleep_fn=broken_sleep, time_fn=FakeClock().time)
    assert summary.aborted is True
    assert summary.cycles == 1
    assert "berputar tanpa jeda" in caplog.text


# --- instance tunggal ----------------------------------------------------------------------
def test_second_instance_cannot_take_the_lock(tmp_path: Path) -> None:
    """Dua perekam atas satu kunci API = 2x60 panggilan/menit; instance kedua harus menyerah."""
    lock_path = tmp_path / "recorder.lock"
    first = acquire_single_instance_lock(lock_path)
    try:
        with pytest.raises(RecorderLockError):
            acquire_single_instance_lock(lock_path)
    finally:
        first.close()
    # setelah yang pertama menutup, instance berikutnya boleh masuk
    acquire_single_instance_lock(lock_path).close()


def test_lock_file_records_the_python_pid(tmp_path: Path) -> None:
    """Berkas kunci menyimpan pid proses Python (daemons.sh menyimpan pid pembungkus uv)."""
    lock_path = tmp_path / "recorder.lock"
    handle = acquire_single_instance_lock(lock_path)
    try:
        assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())
    finally:
        handle.close()


def test_main_refuses_to_start_when_another_recorder_holds_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    lock_path = tmp_path / "busy.lock"
    monkeypatch.setenv("RECORDER_LOCK_PATH", str(lock_path))
    other = acquire_single_instance_lock(lock_path)
    try:
        with caplog.at_level(logging.ERROR, logger="core.recorder"):
            assert main(["--once", "--client", "fixture"]) == 2
    finally:
        other.close()
    assert "perekam lain" in caplog.text


def test_main_releases_the_lock_when_it_finishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = tmp_path / "released.lock"
    monkeypatch.setenv("RECORDER_LOCK_PATH", str(lock_path))
    assert main(["--once", "--client", "fixture"]) == 0
    acquire_single_instance_lock(lock_path).close()  # tidak boleh melempar


# --- konfigurasi yang bisa gagal senyap ------------------------------------------------------
def test_interval_has_an_upper_bound() -> None:
    """`RECORDER_INTERVAL_S=1e9` = tidur 31 tahun dengan pid hidup dan status "JALAN"."""
    with pytest.raises(RecorderConfigError):
        RecorderConfig.from_env({"RECORDER_INTERVAL_S": "1e9"})
    with pytest.raises(RecorderConfigError):
        RecorderConfig.from_env({"RECORDER_INTERVAL_S": "1e300"})
    assert RecorderConfig.from_env({"RECORDER_INTERVAL_S": str(MAX_INTERVAL_S)}).interval_s == MAX_INTERVAL_S


def test_cli_interval_has_the_same_upper_bound(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="core.recorder"):
        assert main(["--once", "--client", "fixture", "--interval", "1e300"]) == 2
    assert "--interval" in caplog.text


@pytest.mark.parametrize(
    "bad",
    [
        {"RECORDER_MAX_FOLLOWUPS": "0"},
        {"RECORDER_MAX_FOLLOWUPS": "banyak"},
        {"RECORDER_MAX_FAILED_CYCLES": "0"},
    ],
)
def test_new_knobs_refuse_bad_values(bad: dict[str, str]) -> None:
    with pytest.raises(RecorderConfigError):
        RecorderConfig.from_env(bad)


# --- saringan simbol -------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        "SO\u202eL",  # U+202E membalik arah teks di kartu panggilan UI
        "SOL\x00",
        "SO\x07L",
        "SOL\ufeff",
        "SO\ud800L",  # surrogate: tidak bisa di-encode UTF-8 oleh canonical_json
        "SOL BTC",
        "",
        "X" * 65,
        None,
        123,
    ],
)
def test_symbol_filter_rejects_unusable_values(raw: Any) -> None:
    assert _normalize_symbol(raw) is None


@pytest.mark.parametrize("raw", ["sol", " BTC ", "\u725b\u6765", "1000PEPE", "USELESS"])
def test_symbol_filter_keeps_real_symbols(raw: str) -> None:
    """`\u725b\u6765` adalah simbol RYO nyata: penyaringan TIDAK boleh membatasi ke ASCII."""
    assert _normalize_symbol(raw) == raw.strip().upper()


def test_scan_candidate_with_control_characters_is_skipped(store: Store) -> None:
    class Sneaky(RecordingClient):
        def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
            envelope = super().call(tool, args)
            if tool == "scan_market":
                envelope = dict(envelope)
                data = dict(envelope["data"])
                data["candidates"] = [{"symbol": "EVIL\u202e"}, {"symbol": "GOOD"}]
                envelope["data"] = data
            return envelope

    client = Sneaky()
    report = make_recorder(store, client, watchlist=("SOL",), max_symbols=2).run_cycle()
    assert report.symbols == ("SOL", "GOOD")
    assert all("\u202e" not in (a.get("symbol") or "") for a in client.tools_called("analyze_token"))


# --- logger pihak ketiga ---------------------------------------------------------------------
def test_debug_logging_does_not_unmute_http_libraries() -> None:
    """LOG_LEVEL=DEBUG tidak boleh membuat httpx/httpcore mencetak header respons mentah."""
    _configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    for noisy in ("httpx", "httpcore", "hpack", "anthropic"):
        assert logging.getLogger(noisy).level == logging.INFO
        assert not logging.getLogger(noisy).isEnabledFor(logging.DEBUG)


# --- restart-safe & CLI --------------------------------------------------------------------
def test_once_twice_over_the_same_db_adds_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Restart-safe: tidak ada state selain DB; jalankan dua kali → baris bertambah."""
    db = tmp_path / "restart.db"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("WATCHLIST", "SOL,BTC")
    monkeypatch.setenv("RECORDER_MAX_SYMBOLS", "3")

    assert main(["--once", "--client", "fixture"]) == 0
    first = rows(db, "SELECT COUNT(*) FROM snapshots")[0][0]
    assert main(["--once", "--client", "fixture"]) == 0
    second = rows(db, "SELECT COUNT(*) FROM snapshots")[0][0]

    assert first == 11
    # Siklus kedua = 11 panggilan yang sama; tidak ada dedup (ADR-006) dan tidak ada follow-up
    # karena ketiga simbol siklus pertama masih ada di watchlist siklus kedua.
    assert second == 22


def test_once_records_followups_after_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "restart2.db"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("WATCHLIST", "SOL")
    monkeypatch.setenv("RECORDER_MAX_SYMBOLS", "1")
    with Store(db) as seeded:
        seed_analyze(seeded, "OLD25", 25.0)
        seed_analyze(seeded, "OLD27", 27.0)

    assert main(["--once", "--client", "fixture"]) == 0
    symbols = [
        json.loads(a)["symbol"]
        for (a,) in rows(db, "SELECT args_json FROM snapshots WHERE tool='analyze_token' AND id > 2")
    ]
    assert symbols == ["SOL", "OLD25"]


def test_main_reports_config_failure_without_traceback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("RECORDER_MAX_SYMBOLS", "banyak")
    with caplog.at_level(logging.ERROR, logger="core.recorder"):
        assert main(["--once", "--client", "fixture"]) == 2
    assert "RECORDER_MAX_SYMBOLS" in caplog.text


def test_main_reports_missing_credentials_without_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="core.recorder"):
        assert main(["--once"]) == 2
    assert "RYO_MCP_URL" in caplog.text


def test_main_reports_ungated_source_without_traceback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """ADR-005: sumber belum approved → pesan jujur, exit 2, bukan traceback."""
    monkeypatch.setenv("RYO_MCP_URL", "https://example.invalid/api/mcp")
    monkeypatch.setenv("RYO_MCP_KEY", LOUD_KEY)

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise SourceNotApprovedError("ryo_tools: approved bukan true")

    monkeypatch.setattr("core.ryo_client.require_source", refuse)
    with caplog.at_level(logging.ERROR, logger="core.recorder"):
        assert main(["--once"]) == 2
    assert "ryo_tools" in caplog.text
    assert LOUD_KEY not in caplog.text


def test_main_returns_one_when_nothing_was_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Host RYO mati (TASKS 4.5): exit 1 yang jujur, tanpa traceback."""

    class AllDead(RecordingClient):
        def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
            raise RyoNetworkError(f"{tool}: host mati")

    monkeypatch.setattr("core.recorder.build_client", lambda kind="live": AllDead())
    assert main(["--once"]) == 1


def test_build_client_fixture_needs_no_credentials() -> None:
    assert isinstance(build_client("fixture"), FixtureClient)


def test_build_client_rejects_unknown_kind() -> None:
    with pytest.raises(RecorderConfigError):
        build_client("mock")


# --- kredensial --------------------------------------------------------------------------
def test_no_credentials_in_logs_during_a_normal_cycle(
    store: Store, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("RYO_MCP_KEY", LOUD_KEY)
    monkeypatch.setenv("LLM_API_KEY", LOUD_KEY)
    with caplog.at_level(logging.DEBUG):
        make_recorder(store).run_cycle()
    assert LOUD_KEY not in caplog.text


def test_unexpected_exception_message_never_reaches_the_log(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """Exception pihak ketiga bisa membawa kredensial → hanya nama tipenya yang dicatat."""
    client = RecordingClient({"analyze_token": RuntimeError(f"Bearer {LOUD_KEY}")})
    with caplog.at_level(logging.DEBUG):
        report = make_recorder(store, client).run_cycle()
    assert LOUD_KEY not in caplog.text
    assert "RuntimeError" in caplog.text
    assert report.failed["analyze_token"] == 3


def test_cycle_level_failure_logs_type_only(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    recorder = make_recorder(store)

    def boom() -> CycleReport:
        raise RuntimeError(f"Bearer {LOUD_KEY}")

    recorder.run_cycle = boom  # type: ignore[method-assign]
    clock = FakeClock()
    with caplog.at_level(logging.DEBUG):
        recorder.run(max_cycles=1, sleep_fn=clock.sleep, time_fn=clock.time)
    assert LOUD_KEY not in caplog.text
    assert "RuntimeError" in caplog.text


def test_recorder_repr_has_no_credentials(store: Store) -> None:
    text = repr(make_recorder(store))
    assert LOUD_KEY not in text
    assert "Bearer" not in text


def test_ryo_error_message_is_logged_verbatim(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    """Pesan RyoError sudah diredaksi di klien, jadi boleh dicatat utuh (untuk debugging)."""
    client = RecordingClient({"scan_market": RyoError("scan_market: HTTP 503")})
    with caplog.at_level(logging.WARNING, logger="core.recorder"):
        make_recorder(store, client).run_cycle()
    assert "HTTP 503" in caplog.text


def test_load_dotenv_never_overrides_real_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RYO_MCP_KEY", "dari-environment")
    monkeypatch.delenv("WATCHLIST", raising=False)
    written = tmp_path / "dotenv"
    written.write_text(
        "# komentar\nexport RYO_MCP_KEY=dari-berkas\nWATCHLIST=SOL,BTC   # inline\nRECORDER_MAX_SYMBOLS=7\n",
        encoding="utf-8",
    )
    load_dotenv(written)
    assert os.environ["RYO_MCP_KEY"] == "dari-environment"  # environment menang
    assert os.environ["WATCHLIST"] == "SOL,BTC"  # komentar sebaris dibuang
    assert os.environ["RECORDER_MAX_SYMBOLS"] == "7"


def test_load_dotenv_keeps_hash_inside_a_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`#` tanpa spasi di depan BUKAN komentar: memotong kredensial diam-diam lebih berbahaya."""
    monkeypatch.delenv("RECORDER_TEST_VALUE", raising=False)
    written = tmp_path / "dotenv"
    written.write_text('RECORDER_TEST_VALUE="abc#def"\n', encoding="utf-8")
    load_dotenv(written)
    assert os.environ["RECORDER_TEST_VALUE"] == "abc#def"


def test_load_dotenv_strips_a_utf8_bom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Editor Windows menambah BOM; tanpa penanganan, variabel PERTAMA hilang dengan senyap."""
    monkeypatch.delenv("RECORDER_TEST_FIRST", raising=False)
    written = tmp_path / "dotenv"
    written.write_bytes("\ufeffRECORDER_TEST_FIRST=ada\n".encode())
    load_dotenv(written)
    assert os.environ["RECORDER_TEST_FIRST"] == "ada"


def test_load_dotenv_takes_the_last_duplicate_like_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`set -a; . ./.env` memakai baris TERAKHIR; make record harus memakai nilai yang sama."""
    monkeypatch.delenv("RECORDER_TEST_DUP", raising=False)
    written = tmp_path / "dotenv"
    written.write_text("RECORDER_TEST_DUP=pertama\nRECORDER_TEST_DUP=terakhir\n", encoding="utf-8")
    load_dotenv(written)
    assert os.environ["RECORDER_TEST_DUP"] == "terakhir"


def test_load_dotenv_keeps_a_hash_inside_quotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RECORDER_TEST_QUOTED", raising=False)
    written = tmp_path / "dotenv"
    written.write_text('RECORDER_TEST_QUOTED="abc #def"   # komentar\n', encoding="utf-8")
    load_dotenv(written)
    assert os.environ["RECORDER_TEST_QUOTED"] == "abc #def"


def test_load_dotenv_skips_a_value_with_nul(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("RECORDER_TEST_NUL", raising=False)
    monkeypatch.delenv("RECORDER_TEST_AFTER", raising=False)
    written = tmp_path / "dotenv"
    written.write_text("RECORDER_TEST_NUL=a\x00b\nRECORDER_TEST_AFTER=ok\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="core.recorder"):
        load_dotenv(written)
    assert "RECORDER_TEST_NUL" not in os.environ
    assert os.environ["RECORDER_TEST_AFTER"] == "ok"  # satu baris rusak tidak membatalkan sisanya
    assert "NUL" in caplog.text


def test_load_dotenv_skips_malformed_names(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    written = tmp_path / "dotenv"
    written.write_text("2BAD=x\nBAD NAME=y\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="core.recorder"):
        load_dotenv(written)
    assert "2BAD" not in os.environ
    assert "nama variabel tidak sah" in caplog.text


def test_load_dotenv_tolerates_missing_file(tmp_path: Path) -> None:
    load_dotenv(tmp_path / "tidak-ada")  # tidak boleh melempar


# --- TASKS 1.10: "kredensial dicabut" harus bisa diuji ----------------------------------------
def test_empty_credentials_are_not_refilled_from_dotenv(tmp_path: Path) -> None:
    """Nama yang ADA tetapi kosong dihitung "sudah diset" — `.env` tidak boleh menimpanya.

    Tanpa ini, tes chaos yang mengosongkan kredensial akan diam-diam memanggil RYO sungguhan.
    """
    written = tmp_path / "dotenv"
    written.write_text("RYO_MCP_KEY=nilai-dari-berkas\n", encoding="utf-8")
    os.environ["RYO_MCP_KEY"] = ""
    try:
        load_dotenv(written)
        assert os.environ["RYO_MCP_KEY"] == ""
    finally:
        os.environ.pop("RYO_MCP_KEY", None)


def test_removed_credentials_are_refilled_unless_no_dotenv(tmp_path: Path) -> None:
    """Batas yang diakui: `unset` BUKAN cara mencabut kredensial — `.env` mengisinya lagi.

    hackathon-judge menemukannya dengan cara mahal (percobaan "cabut kredensial"-nya tetap
    memanggil API dan menyumbang HTTP 429). Tes ini mengunci kenyataan itu supaya siapa pun
    yang menulis tes chaos berikutnya memakai `--no-dotenv` atau mengosongkan nilainya.
    """
    written = tmp_path / "dotenv"
    written.write_text("RYO_MCP_KEY=nilai-dari-berkas\n", encoding="utf-8")
    os.environ.pop("RYO_MCP_KEY", None)
    try:
        load_dotenv(written)
        assert os.environ["RYO_MCP_KEY"] == "nilai-dari-berkas"
    finally:
        os.environ.pop("RYO_MCP_KEY", None)


def test_revoked_credentials_stop_the_recorder_without_touching_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kredensial kosong -> exit 2, dan NOL koneksi keluar (bukan sekadar exit != 0)."""
    attempts: list[object] = []

    def blocked(*args: object, **kwargs: object) -> None:
        attempts.append(args)
        raise AssertionError("perekam mencoba memanggil jaringan tanpa kredensial")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setenv("RYO_MCP_KEY", "")
    monkeypatch.setenv("RYO_MCP_URL", "")
    monkeypatch.setenv("RECORDER_LOCK_PATH", str(tmp_path / "lock"))

    assert main(["--once", "--no-dotenv"]) == 2
    assert attempts == []


# --- jaring pengaman: DB hidup tidak boleh tersentuh -----------------------------------------
def test_default_db_path_is_isolated_to_tmp(tmp_path: Path) -> None:
    """Oracle fixture autouse: `Store()` tanpa argumen pun mendarat di tmp_path, bukan di DB hidup."""
    with Store() as isolated:
        assert isolated.path == tmp_path / "recorder.db"
        assert isolated.path.resolve() != LIVE_DB.resolve()
