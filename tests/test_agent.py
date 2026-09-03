"""Tes TASKS 2.4 + 2.0b-lite — agen practice-trade (`agent/main.py`, `agent/trade.py`).

AC yang dipatok di sini:

* 2.4 (1) tiga tick → ≥1 GO dan ≥1 NO_GO; (4) tiap baris ledger `simulated=1`;
* 2.4 exit `target`/`stop`/`timeout` dari harga PEREKAM, bukan dari panggilan tool;
* 2.0b-lite (1) `agent/` tidak pernah mengonstruksi klien live — dibuktikan dengan
  `socket.socket.connect` yang diblokir DAN dengan pemindaian impor;
* 2.0b-lite (2) bukti tidak segar → item `unspoken` → NO_GO ber-`reason` jujur, dan frasa
  "kuota dipegang perekam" tidak ada di mana pun di `agent/`;
* 2.0b-lite (3) docstring `agent/main.py` menyebut penyimpangan + ADR-018 sebagai USULAN.

Bukti = `docs/samples/*.json` di store `tmp_path` (`tests/helpers_agent.py`); `data/ryo.db`
tidak pernah disentuh (fixture autouse mengarahkan `DB_PATH`).
"""

from __future__ import annotations

import ast
import json
import socket
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent import main as agent_main
from agent import params as agent_params
from agent.ledger import events_for, open_positions, read_decision, recent_decisions
from agent.params import DEFAULT_AGENT_PARAMS, AgentParams
from agent.trade import (
    EXIT_STOP,
    EXIT_TARGET,
    EXIT_TIMEOUT,
    PRICE_FIELD,
    PracticePlan,
    exit_reason,
    latest_price,
    overdue_min,
    plan_from_items,
    settle_open_positions,
)
from agent.watchlist import MAX_SYMBOLS, watchlist
from core.store import Store
from preflight.params import DEFAULT_PARAMS, ITEM_COMPLETENESS, ITEM_ORDER, ITEM_PLAN
from tests.helpers_agent import ANALYZE, MARKET_OVERVIEW, build_store, mixed_confluence, price_snapshot

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / "agent"


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`Store()` tanpa argumen pun mendarat di tmp_path — DB hidup tidak pernah disentuh."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "agent-default.db"))


def rows(store: Store, sql: str) -> list[tuple]:
    """Baca baris apa adanya lewat koneksi baca-saja terpisah (tes, bukan jalur produksi)."""
    conn = sqlite3.connect(f"file:{store.path}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# --- AC 2.4 (1): tiga tick, minimal satu GO dan satu NO_GO ---------------------------------
def test_three_ticks_produce_at_least_one_go_and_one_no_go(tmp_path: Path) -> None:
    store = build_store(tmp_path, mutate=mixed_confluence("BTC"))
    verdicts: list[str] = []
    for _ in range(3):
        report = agent_main.tick(store, ("SOL", "BTC"), DEFAULT_AGENT_PARAMS)
        verdicts.extend(outcome.decision for outcome in report.outcomes)
    assert verdicts.count("GO") >= 1
    assert verdicts.count("NO_GO") >= 1
    # Append-only: tiga tick × dua simbol = enam baris keputusan, tidak ada yang ditimpa.
    assert len(recent_decisions(store.path, limit=50)) == 6


def test_go_writes_a_decision_row_and_one_open_trade(tmp_path: Path) -> None:
    store = build_store(tmp_path, symbols=("SOL",))
    report = agent_main.tick(store, ("SOL",), DEFAULT_AGENT_PARAMS)
    outcome = report.outcomes[0]
    assert (outcome.decision, outcome.rejected) == ("GO", "")

    row = read_decision(store.path, outcome.decision_id)
    assert row is not None
    assert row.symbol == "SOL"
    assert row.decision == "GO"
    assert row.blocking() == []
    assert len(row.snapshot_ids()) == 5
    assert row.params_hash == DEFAULT_PARAMS.params_hash()
    assert row.decision_hash == outcome.decision_hash
    assert row.simulated == 1

    events = events_for(store.path, outcome.decision_id)
    assert [event.kind for event in events] == ["open"]
    assert events[0].direction == "long"
    assert events[0].size_usd == DEFAULT_AGENT_PARAMS.size_usd
    assert events[0].price == outcome.plan.entry
    assert events[0].simulated == 1


def test_no_go_writes_a_declined_row_with_blocking_and_no_trade(tmp_path: Path) -> None:
    store = build_store(tmp_path, symbols=("BTC",), mutate=mixed_confluence("BTC"))
    report = agent_main.tick(store, ("BTC",), DEFAULT_AGENT_PARAMS)
    outcome = report.outcomes[0]
    assert outcome.decision == "NO_GO"
    assert "06_confluence" in outcome.blocking
    assert outcome.plan is None

    row = read_decision(store.path, outcome.decision_id)
    assert row is not None and row.decision == "NO_GO"
    assert row.blocking() == list(outcome.blocking)
    assert events_for(store.path, outcome.decision_id) == []


# --- AC 2.4 (4): simulated=1 di SETIAP baris ledger ----------------------------------------
def test_every_ledger_row_is_marked_simulated(tmp_path: Path) -> None:
    store = build_store(tmp_path, mutate=mixed_confluence("BTC"))
    agent_main.tick(store, ("SOL", "BTC"), DEFAULT_AGENT_PARAMS)
    decisions = rows(store, "SELECT simulated FROM decisions")
    events = rows(store, "SELECT simulated FROM trade_events")
    assert decisions and events
    assert {value for (value,) in decisions} == {1}
    assert {value for (value,) in events} == {1}


def test_card_json_carries_the_plan_and_the_hash_algo(tmp_path: Path) -> None:
    store = build_store(tmp_path, symbols=("SOL",))
    report = agent_main.tick(store, ("SOL",), DEFAULT_AGENT_PARAMS)
    card = read_decision(store.path, report.outcomes[0].decision_id).card()
    assert card["algo"] == "tenko-decision-hash/1"  # kait STALE_ALGO (ADR-016 / TASKS 2.4a)
    assert card["agent"]["sizing"] == "fixed_notional"
    assert card["agent"]["simulated"] is True
    assert card["agent"]["plan"]["size_usd"] == DEFAULT_AGENT_PARAMS.size_usd
    assert [item["id"] for item in card["skill"]["data"]["items"]] == list(ITEM_ORDER)


# --- exit: target / stop / timeout, semuanya dari harga perekam ----------------------------
def _open_one(tmp_path: Path) -> tuple[Store, int, PracticePlan]:
    store = build_store(tmp_path, symbols=("SOL",))
    report = agent_main.tick(store, ("SOL",), DEFAULT_AGENT_PARAMS)
    outcome = report.outcomes[0]
    assert outcome.plan is not None
    return store, outcome.decision_id, outcome.plan


def test_exit_on_target_uses_the_recorder_price(tmp_path: Path) -> None:
    store, decision_id, plan = _open_one(tmp_path)
    snapshot_id = price_snapshot(store, "SOL", plan.target + 1.0)
    settlements = settle_open_positions(store, DEFAULT_AGENT_PARAMS)
    assert [(s.closed, s.reason) for s in settlements] == [(True, EXIT_TARGET)]
    events = events_for(store.path, decision_id)
    assert [event.kind for event in events] == ["open", "exit"]
    assert events[1].price == plan.target + 1.0
    assert events[1].reason.startswith(EXIT_TARGET)
    assert f"snapshot_id={snapshot_id}" in events[1].reason
    assert PRICE_FIELD in events[1].reason
    assert open_positions(store.path) == []


def test_exit_on_stop(tmp_path: Path) -> None:
    store, decision_id, plan = _open_one(tmp_path)
    price_snapshot(store, "SOL", plan.stop - 1.0)
    settlements = settle_open_positions(store, DEFAULT_AGENT_PARAMS)
    assert [(s.closed, s.reason) for s in settlements] == [(True, EXIT_STOP)]
    assert events_for(store.path, decision_id)[1].reason.startswith(EXIT_STOP)


def test_exit_on_timeout_when_price_sits_between_stop_and_target(tmp_path: Path) -> None:
    store, decision_id, plan = _open_one(tmp_path)
    later = datetime.now(UTC) + timedelta(minutes=DEFAULT_AGENT_PARAMS.timeout_min + 1)
    # Harga SEGAR terhadap `later`: batas waktu diuji, bukan kesegaran harga.
    price_snapshot(store, "SOL", plan.entry, at=later)
    settlements = settle_open_positions(store, DEFAULT_AGENT_PARAMS, now=later)
    assert [(s.closed, s.reason) for s in settlements] == [(True, EXIT_TIMEOUT)]
    assert events_for(store.path, decision_id)[1].reason.startswith(EXIT_TIMEOUT)


def test_timeout_is_shorter_than_the_widest_observed_price_gap(tmp_path: Path) -> None:
    """Angka batas waktu dipatok berikut sebabnya (api-verifier 4 Sep 2026).

    docs/api-facts.md §A "Harga — `analyze_token.data.market.price_usd` — DIVERIFIKASI
    4 Sep 2026": celah harga maksimum yang teramati **1455 mnt (24,3 j)**, sementara jendela
    outcome spec §6 adalah 1440 mnt. Nilai lama 1440 bertabrakan tepat dengan celah itu —
    satu celah bisa menelan seluruh umur posisi. 720 mnt menaruh jatuh tempo di TENGAH jendela
    §6. Kalau angka ini digeser, alasannya harus digeser juga: itulah gunanya tes ini.
    """
    assert DEFAULT_AGENT_PARAMS.timeout_min == 720
    assert DEFAULT_AGENT_PARAMS.timeout_min < 1440  # jatuh tempo di dalam jendela outcome §6
    constant_doc = Path(agent_params.__file__).read_text(encoding="utf-8")
    assert "1455" in constant_doc and "929/929" in constant_doc  # angka, bukan kesan
    assert "docs/api-facts.md" in constant_doc  # sumbernya disebut, bukan ingatan
    assert "REAL NOT NULL" in constant_doc  # cabang "tutup tanpa harga" ditolak, dengan sebab


@pytest.mark.parametrize(
    ("minutes_since_open", "expected"),
    [(0, None), (719, None), (720, 0.0), (1020, 300.0)],
)
def test_overdue_min_reports_how_late_a_position_is(minutes_since_open: int, expected: float | None) -> None:
    opened = datetime(2026, 9, 4, tzinfo=UTC)
    now = opened + timedelta(minutes=minutes_since_open)
    assert overdue_min(opened.isoformat(), now=now, timeout_min=720) == expected


def test_a_price_gap_that_swallows_the_deadline_is_reported_then_booked_late(tmp_path: Path) -> None:
    """Kasus nyata dari data: perekam diam melewati jatuh tempo.

    Posisi TIDAK ditutup dengan harga karangan; ia tetap terbuka dengan sebab yang menyebut
    berapa menit jatuh temponya sudah lewat, lalu ditutup `timeout` begitu harga perekam
    muncul — dan keterlambatannya ikut tertulis di baris `exit`.
    """
    store, decision_id, plan = _open_one(tmp_path)
    # 1455 menit = celah terlebar yang pernah teramati (ETH/BNB, api-facts §A).
    inside_gap = datetime.now(UTC) + timedelta(minutes=1455)
    settlements = settle_open_positions(store, DEFAULT_AGENT_PARAMS, now=inside_gap)
    assert settlements[0].closed is False
    assert "jatuh tempo sudah lewat" in settlements[0].reason
    assert [event.kind for event in events_for(store.path, decision_id)] == ["open"]

    # Perekam kembali menulis; harga di antara stop dan target → `timeout`, tercatat terlambat.
    price_snapshot(store, "SOL", plan.entry, at=inside_gap)
    closed = settle_open_positions(store, DEFAULT_AGENT_PARAMS, now=inside_gap)
    assert [(s.closed, s.reason) for s in closed] == [(True, EXIT_TIMEOUT)]
    exit_row = events_for(store.path, decision_id)[1]
    assert exit_row.reason.startswith(EXIT_TIMEOUT)
    assert f"past the {DEFAULT_AGENT_PARAMS.timeout_min} min limit" in exit_row.reason
    assert "735 min past" in exit_row.reason  # 1455 - 720, angka bukan kesan


def test_a_forged_card_json_plan_cannot_close_a_position(tmp_path: Path) -> None:
    """TINGGI-2 security-reviewer (4 Sep 2026) — DITUTUP: penutupan tidak membaca `card_json`.

    Serangan aslinya: satu `UPDATE decisions SET card_json` (tanpa menyentuh satu hash pun)
    menggeser `stop`/`target`, `make replay` TETAP IDENTICAL, dan tick berikutnya membukukan
    `exit reason='target'` pada harga yang mustahil. Sejak perbaikan, plan diturunkan ULANG
    dari snapshot beku (`agent.trade.plan_from_frozen_evidence`) dan `card_json` hanya bahan
    kartu, jadi serangan yang sama tidak menggerakkan satu baris pun.
    """
    store, decision_id, plan = _open_one(tmp_path)
    forged = read_decision(store.path, decision_id).card()
    forged["agent"]["plan"].update({"entry": 3.0, "stop": 0.001, "target": 3.0})
    conn = sqlite3.connect(str(store.path))
    try:
        conn.execute("UPDATE decisions SET card_json = ? WHERE id = ?", (json.dumps(forged), decision_id))
        conn.commit()
    finally:
        conn.close()

    # Harga normal: dengan plan PALSU ia "kena target" (>= 3.0); dengan plan asli ia di tengah.
    price_snapshot(store, "SOL", plan.entry)
    settlements = settle_open_positions(store, DEFAULT_AGENT_PARAMS)
    assert settlements[0].closed is False
    assert "masih di antara stop" in settlements[0].reason
    assert [event.kind for event in events_for(store.path, decision_id)] == ["open"]

    # Batas yang TETAP diakui: `card_json` memang tidak dilindungi hash — replay tetap
    # IDENTICAL setelah UPDATE di atas. Yang berubah adalah kolom itu tidak lagi memutuskan.
    from agent.replay import VERDICT_IDENTICAL, replay

    assert replay(decision_id, store, DEFAULT_PARAMS).verdict == VERDICT_IDENTICAL


def test_a_tampered_snapshot_stops_a_position_from_being_closed(tmp_path: Path) -> None:
    """Penutupan memakai bukti yang DIJAGA HASH: payload berubah → posisi tidak ditutup."""
    store, decision_id, plan = _open_one(tmp_path)
    frozen = read_decision(store.path, decision_id).snapshot_ids()
    conn = sqlite3.connect(str(store.path))
    try:
        rows = conn.execute(
            "SELECT id, payload_json FROM snapshots WHERE tool = 'deep_analysis' ORDER BY id"
        ).fetchall()
        row = next(entry for entry in rows if entry[0] in frozen)
        conn.execute(
            "UPDATE snapshots SET payload_json = ? WHERE id = ?",
            (row[1].replace("103.8", "104.8", 1), row[0]),
        )
        conn.commit()
    finally:
        conn.close()

    price_snapshot(store, "SOL", plan.target + 1.0)
    settlements = settle_open_positions(store, DEFAULT_AGENT_PARAMS)
    assert settlements[0].closed is False
    assert "tidak bisa diturunkan ulang dari bukti beku" in settlements[0].reason
    assert f"snapshot_id={row[0]}" in settlements[0].reason
    assert [event.kind for event in events_for(store.path, decision_id)] == ["open"]


def test_position_stays_open_between_stop_and_target(tmp_path: Path) -> None:
    store, decision_id, plan = _open_one(tmp_path)
    price_snapshot(store, "SOL", plan.entry)
    settlements = settle_open_positions(store, DEFAULT_AGENT_PARAMS)
    assert settlements[0].closed is False
    assert [event.kind for event in events_for(store.path, decision_id)] == ["open"]
    assert [pos.decision_id for pos in open_positions(store.path)] == [decision_id]


def test_stale_price_never_closes_a_position(tmp_path: Path) -> None:
    """Harga basi bukan harga: posisi tetap terbuka dan alasannya disebut."""
    store, decision_id, plan = _open_one(tmp_path)
    price_snapshot(store, "SOL", plan.target + 1.0)
    # Jam digeser jauh ke depan: SETIAP baris harga di store kini lebih tua dari batasnya,
    # termasuk yang di atas target. Batas waktu pun tidak menutup posisi tanpa harga.
    much_later = datetime.now(UTC) + timedelta(minutes=DEFAULT_PARAMS.max_age_min * 10)
    settlements = settle_open_positions(store, DEFAULT_AGENT_PARAMS, now=much_later)
    assert settlements[0].closed is False
    assert str(DEFAULT_PARAMS.max_age_min) in settlements[0].reason
    assert [event.kind for event in events_for(store.path, decision_id)] == ["open"]


def test_latest_price_ignores_a_snapshot_whose_market_data_is_unavailable(tmp_path: Path) -> None:
    store = build_store(tmp_path, symbols=("SOL",))
    conn_rows = rows(store, "SELECT COUNT(*) FROM snapshots")
    assert conn_rows[0][0] > 0
    price = latest_price(store, "SOL", DEFAULT_AGENT_PARAMS)
    assert price is not None and price.price > 0
    assert latest_price(store, "NOSUCH", DEFAULT_AGENT_PARAMS) is None


@pytest.mark.parametrize(
    ("direction", "price", "expected"),
    [
        ("long", 120.0, EXIT_TARGET),
        ("long", 90.0, EXIT_STOP),
        ("long", 100.0, None),
        ("short", 90.0, EXIT_TARGET),
        ("short", 120.0, EXIT_STOP),
    ],
)
def test_exit_reason_is_a_pure_function(direction: str, price: float, expected: str | None) -> None:
    plan = PracticePlan(
        symbol="SOL",
        direction=direction,
        entry=100.0,
        stop=95.0 if direction == "long" else 105.0,
        target=110.0 if direction == "long" else 95.0,
        size_usd=1000.0,
    )
    from agent.trade import PriceRead

    read = PriceRead(price=price, snapshot_id=1, as_of=None, ts_utc="2026-09-03T00:00:00+00:00")
    now = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)
    assert exit_reason(plan, read, now.isoformat(), now=now, timeout_min=1440) == expected


def test_stop_is_checked_before_target_when_one_price_is_outside_both(tmp_path: Path) -> None:
    """Satu harga tidak memberi lintasan; yang merugikan dibaca lebih dulu (docstring modul)."""
    from agent.trade import PriceRead

    plan = PracticePlan("SOL", "long", 100.0, 95.0, 110.0, 1000.0)
    read = PriceRead(price=1000.0, snapshot_id=1, as_of=None, ts_utc="")
    now = datetime(2026, 9, 3, tzinfo=UTC)
    assert exit_reason(plan, read, now.isoformat(), now=now, timeout_min=1440) == EXIT_TARGET
    # Harga di bawah stop DAN di atas target mustahil untuk long; kasus nyata "di luar
    # keduanya" adalah plan short — dan di sana stop yang menang.
    short = PracticePlan("SOL", "short", 100.0, 105.0, 95.0, 1000.0)
    assert exit_reason(short, read, now.isoformat(), now=now, timeout_min=1440) == EXIT_STOP


def test_no_pnl_is_computed_or_printed(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Rubric §8 tidak menilai P&L; keluaran menyebut keputusan & fakta exit, bukan untung."""
    store, _decision_id, plan = _open_one(tmp_path)
    price_snapshot(store, "SOL", plan.target + 1.0)
    report = agent_main.tick(store, ("SOL",), DEFAULT_AGENT_PARAMS)
    agent_main._print_report(report, store, ("SOL",), DEFAULT_AGENT_PARAMS)
    printed = capsys.readouterr().out
    # Baris `db:` sengaja dilewati: ia memuat path tmp_path, yang kebetulan memuat nama tes.
    body = "\n".join(line for line in printed.splitlines() if not line.startswith("db: ")).lower()
    for word in ("pnl", "p&l", "profit", "loss", "return %", "equity"):
        assert word not in body
    assert "simulated" in body
    assert "closed (target)" in body


# --- 2.0b-lite (1): nol panggilan live -----------------------------------------------------
def test_agent_never_opens_a_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`socket.connect` diblokir: satu tick tetap selesai dan tidak ada request satu pun."""
    attempts: list[object] = []

    def refuse(self: socket.socket, address: object) -> None:
        attempts.append(address)
        raise AssertionError(f"agen mencoba koneksi jaringan ke {address!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    store = build_store(tmp_path, mutate=mixed_confluence("BTC"))
    report = agent_main.tick(store, ("SOL", "BTC"), DEFAULT_AGENT_PARAMS)
    assert attempts == []
    assert report.n_go + report.n_no_go == 2


def test_agent_package_never_imports_the_live_client() -> None:
    """Pemindaian impor: `agent/` tidak menyebut `core.ryo_client` maupun `core.livelock`."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(AGENT_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
        bad = [name for name in names if name in {"core.ryo_client", "core.livelock", "httpx", "requests"}]
        if bad:
            offenders[path.name] = bad
    assert offenders == {}


def test_agent_cli_refuses_the_withdrawn_live_flag(capsys: pytest.CaptureFixture) -> None:
    assert agent_main.main(["--once", "--client", "live"]) == agent_main.EXIT_UNUSABLE
    err = capsys.readouterr().err
    assert "--client" in err and "ADR-018" in err


# --- 2.0b-lite (2): bukti tidak segar → unspoken → NO_GO ber-alasan jujur -------------------
def test_partly_stale_evidence_becomes_unspoken_items_and_an_honest_no_go(tmp_path: Path) -> None:
    """Sebagian tool punya bukti segar, sebagian tidak → item `unspoken` → NO_GO ber-alasan.

    Inilah bentuk 2.0b-lite AC (2) yang MASIH menulis baris: ada bukti beku yang bisa
    di-replay. Alasannya menyebut store, bukan kuota siapa pun.
    """
    store = build_store(tmp_path, symbols=("SOL",), tools=(MARKET_OVERVIEW, ANALYZE))
    report = agent_main.tick(store, ("SOL",), DEFAULT_AGENT_PARAMS)
    outcome = report.outcomes[0]
    assert outcome.decision == "NO_GO"
    assert ITEM_COMPLETENESS in outcome.blocking
    assert outcome.plan is None

    row = read_decision(store.path, outcome.decision_id)
    assert row.snapshot_ids()  # ada bukti beku: barisnya bisa di-replay
    skill = row.card()["skill"]
    statuses = {entry["id"]: entry["status"] for entry in skill["data"]["items"]}
    assert statuses[ITEM_PLAN] == "unspoken"  # deep_analysis tidak menjawab
    text = json.dumps(skill)
    assert f"no snapshot within {DEFAULT_PARAMS.max_age_min} min in the store" in text
    assert "kuota" not in text.lower()


def test_evidence_older_than_the_limit_writes_no_row_at_all(tmp_path: Path) -> None:
    """Nol bukti beku → `insufficient evidence`, TIDAK ada baris, kode keluar tetap 0.

    Temuan SEDANG security-reviewer 4 Sep 2026: baris ber-`snapshot_ids=[]` membuat
    `make replay` berkata "IDENTICAL … dari 0 snapshot beku" — vonis yang terlihat seperti
    verifikasi padahal tidak memverifikasi apa pun (bukti nyatanya `data/ryo.db` baris 20-23).
    """
    store = build_store(tmp_path, symbols=("SOL",), age_min=DEFAULT_PARAMS.max_age_min + 60)
    report = agent_main.tick(store, ("SOL",), DEFAULT_AGENT_PARAMS)
    outcome = report.outcomes[0]
    assert (outcome.decision, outcome.decision_id, outcome.rejected) == ("-", None, "")
    assert f"tidak ada snapshot dalam {DEFAULT_PARAMS.max_age_min} menit" in outcome.insufficient
    assert "kuota" not in outcome.insufficient.lower()
    assert report.n_insufficient == 1 and report.n_rejected == 0
    assert recent_decisions(store.path) == []
    assert open_positions(store.path) == []


def test_a_tick_without_evidence_still_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ "Tidak ada bukti" adalah keadaan jujur, bukan kegagalan penjaga (2.0b-lite AC 1)."""
    store = build_store(tmp_path, symbols=("SOL",), age_min=DEFAULT_PARAMS.max_age_min + 60)
    monkeypatch.setenv("DB_PATH", str(store.path))
    assert agent_main.main(["--once", "--symbols", "SOL"]) == agent_main.EXIT_OK


def test_the_agent_never_creates_a_database(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """`--db` salah ketik → pesan jujur + exit 2, BUKAN DB kosong baru yang lalu diisi."""
    missing = tmp_path / "salah-ketik.db"
    assert agent_main.main(["--once", "--db", str(missing)]) == agent_main.EXIT_UNUSABLE
    assert not missing.exists()
    err = capsys.readouterr().err
    assert "tidak membuat DB baru" in err and "make record" in err


def _runtime_strings(path: Path) -> list[str]:
    """Semua string literal KECUALI docstring modul/kelas/fungsi.

    Docstring memang menyebut frasa lama untuk MENOLAKNYA; yang dilarang adalah frasa itu
    muncul di teks yang bisa sampai ke pengguna.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def test_no_user_facing_string_blames_the_recorder_for_holding_a_quota() -> None:
    """Frasa 2.0b lama ("kuota dipegang perekam") tidak boleh sampai ke pengguna."""
    for path in sorted(AGENT_DIR.glob("*.py")):
        for text in _runtime_strings(path):
            assert "kuota" not in text.lower(), f"{path.name}: {text!r}"


# --- 2.0b-lite (3): pengakuan penyimpangan ada di docstring --------------------------------
def test_main_docstring_states_the_deviation_and_points_at_adr_018() -> None:
    doc = agent_main.__doc__ or ""
    assert "spec §5" in doc
    assert "ADR-018" in doc
    assert "USULAN" in doc and "menunggu persetujuan manusia" in doc
    assert "store-first" in doc


def test_livelock_is_left_in_place_without_a_production_caller() -> None:
    """`core.livelock.try_acquire` tetap ada (disengaja) tetapi tidak dipanggil `agent/`."""
    from core import livelock

    assert callable(livelock.try_acquire)
    for path in sorted(AGENT_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        assert "try_acquire" not in called, path.name
        assert "acquire_single_instance_lock" not in called, path.name


# --- determinisme & watchlist --------------------------------------------------------------
def test_two_ticks_over_the_same_evidence_give_the_same_decision_hash(tmp_path: Path) -> None:
    store = build_store(tmp_path, symbols=("SOL",))
    first = agent_main.tick(store, ("SOL",), DEFAULT_AGENT_PARAMS).outcomes[0]
    second = agent_main.tick(store, ("SOL",), DEFAULT_AGENT_PARAMS).outcomes[0]
    assert first.decision_hash == second.decision_hash
    assert first.decision_id != second.decision_id  # append-only: dua baris, satu hash


def test_no_randomness_in_the_agent_package() -> None:
    for path in sorted(AGENT_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name != "random" for alias in node.names), path.name
            if isinstance(node, ast.ImportFrom):
                assert node.module != "random", path.name


def test_watchlist_defaults_to_the_four_core_symbols() -> None:
    assert watchlist(None, env={}).symbols == ("SOL", "BTC", "ETH", "BNB")


def test_watchlist_reads_the_argument_then_the_env() -> None:
    assert watchlist("sol, eth").symbols == ("SOL", "ETH")
    assert watchlist(None, env={"WATCHLIST": "btc;bnb"}).symbols == ("BTC", "BNB")
    assert watchlist(None, env={"AGENT_WATCHLIST": "SOL"}).source == "env:AGENT_WATCHLIST"


def test_watchlist_drops_unusable_entries_and_says_so() -> None:
    result = watchlist("SOL, , sol, A B, X,Y")
    assert result.symbols == ("SOL", "X", "Y")
    assert result.dropped == ("A B",)


def test_watchlist_caps_the_number_of_symbols() -> None:
    result = watchlist(",".join(f"S{i}" for i in range(MAX_SYMBOLS + 3)))
    assert len(result.symbols) == MAX_SYMBOLS
    assert result.truncated == 3


def test_agent_params_reject_unusable_sizes() -> None:
    for kwargs in ({"size_usd": 0}, {"size_usd": -1.0}, {"timeout_min": 0}, {"timeout_min": 1.5}):
        with pytest.raises(ValueError):
            AgentParams(**kwargs)


def test_execution_params_do_not_move_the_decision_params_hash() -> None:
    """Mengganti ukuran posisi tidak boleh membuat keputusan lama tampak MISMATCH."""
    other = AgentParams(size_usd=25.0)
    assert other.decision_params_hash() == DEFAULT_AGENT_PARAMS.decision_params_hash()
    assert other.exec_params_hash() != DEFAULT_AGENT_PARAMS.exec_params_hash()


def test_plan_from_items_refuses_when_item_nine_is_unspoken(tmp_path: Path) -> None:
    from agent.anchor import recompute
    from core.replay import freeze

    store = build_store(tmp_path, symbols=("SOL",), tools=("market_overview",))
    bundle = freeze(store, [1])
    recomputed = recompute("SOL", bundle, DEFAULT_PARAMS)
    with pytest.raises(Exception, match="09_plan"):
        plan_from_items("SOL", recomputed.items, DEFAULT_AGENT_PARAMS)
