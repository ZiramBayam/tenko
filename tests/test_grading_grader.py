"""Tes `grading/grader.py` (TASKS 3.3) — reputasi item dari outcome nyata.

Yang dijaga, berurut kepentingan:

1. **Duplikasi bukti tidak boleh menjadi `n`.** Tiga tick agen dalam satu detik atas snapshot
   yang sama x empat simbol yang berbagi satu baris market-wide = 12 baris `decisions`, tetapi
   4 episode dan 1 peristiwa. Kalau tes ini merah, angka yang keluar berbohong ke juri.
2. **Gerbang 30 berlaku, dan alatnya berubah jadi bermakna sendiri saat datanya cukup** —
   dua tes berpasangan: 5 peristiwa -> "belum cukup data"; 30 peristiwa -> CI tercetak.
3. **Nol tulisan ke DB.** md5 sebelum/sesudah identik, tidak ada `-wal`/`-journal`, dan
   `grade()` menolak koneksi yang bisa menulis.
4. **Idempoten**: dua panggilan atas `--until` yang sama memberi teks yang byte-identik.
5. **Nol pengaruh ke jalur keputusan (ADR-003)**: `params_hash` tetap `3d39fb1b…`, dan tidak
   ada modul di `preflight/`/`agent/`/`core/` yang mengimpor `grading`.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from core.store import Store
from grading.grader import GradingError, grade, main, render
from grading.stats import MIN_N
from preflight.params import DEFAULT_PARAMS, params_hash

ROOT = Path(__file__).resolve().parent.parent
FROZEN = params_hash(DEFAULT_PARAMS)
START = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
UNTIL = datetime(2026, 9, 10, 0, 0, 0, tzinfo=UTC)
SYMBOLS = ("SOL", "BTC", "ETH", "BNB")


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`Store()` tanpa argumen mendarat di tmp_path — DB hidup tidak pernah disentuh."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "grading.db"))


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324 - sidik jari berkas, bukan kripto


def card(
    *,
    entry: float,
    stop: float,
    target: float,
    plan_snapshot: int,
    market_snapshot: int,
    stance: str = "neutral",
    regime: str = "neutral",
) -> dict[str, Any]:
    """Kartu berbentuk sama dengan yang ditulis `agent/` hari ini (id item berupa string)."""
    return {
        "algo": "tenko-decision-hash/1",
        "skill": {
            "data": {
                "decision": "NO_GO",
                "items": [
                    {
                        "id": "03_regime",
                        "status": "green",
                        "value": {"regime": regime},
                        "source": {"snapshot_id": market_snapshot, "tool": "market_overview"},
                    },
                    {
                        "id": "07_verdict_deep",
                        "status": "green",
                        "value": {"stance": stance},
                        "source": {"snapshot_id": plan_snapshot, "tool": "deep_analysis"},
                    },
                    {
                        "id": "08_derivatives",
                        "status": "red",
                        "value": {"funding_crowding_state": "long_crowding"},
                        "source": {"snapshot_id": market_snapshot, "tool": "monitor_market_sentiment_shift"},
                    },
                    {
                        "id": "09_plan",
                        "status": "green",
                        "value": {
                            "direction": "long",
                            "entry": entry,
                            "stop": stop,
                            "targets": [target],
                            "risk_reward": [1.0],
                        },
                        "source": {"snapshot_id": plan_snapshot, "tool": "deep_analysis"},
                    },
                ],
            }
        },
    }


def write_prices(store: Store, symbol: str, prices: list[tuple[datetime, float]]) -> None:
    for moment, price in prices:
        stamp = moment.replace(microsecond=0).isoformat()
        store.insert_snapshot(
            "analyze_token",
            {"symbol": symbol},
            {"as_of": stamp.replace("+00:00", "Z"), "data": {"market": {"price_usd": price}}},
            ts_utc=stamp,
        )


def build_db(
    tmp_path: Path,
    *,
    cycles: int = 1,
    repeats: int = 3,
    symbols: tuple[str, ...] = SYMBOLS,
    step_hours: int = 48,
    price_path: list[float] | None = None,
    name: str = "grading.db",
) -> Path:
    """`cycles` siklus; tiap siklus SATU snapshot market-wide dipakai keempat simbol, dan tiap
    keputusan ditulis `repeats` kali (tick agen yang menembak berkali-kali atas bukti yang sama).

    Itu bentuk `data/ryo.db` nyata: id 28–39 adalah tiga tick dalam rentang satu detik.
    """
    path = tmp_path / name
    path_prices = price_path if price_path is not None else [100.0, 100.5, 101.0]
    with Store(path) as store:
        for cycle in range(cycles):
            decided_at = START + timedelta(hours=step_hours * cycle)
            market_snapshot = 1000 + cycle  # satu baris market-wide, dibaca semua simbol
            for index, symbol in enumerate(symbols):
                plan_snapshot = 2000 + cycle * 10 + index
                write_prices(
                    store,
                    symbol,
                    [
                        (decided_at + timedelta(hours=hour + 1), price)
                        for hour, price in enumerate(path_prices)
                    ]
                    + [(decided_at + timedelta(hours=30), path_prices[-1])],
                )
                payload = card(
                    entry=100.0,
                    stop=90.0,
                    target=110.0,
                    plan_snapshot=plan_snapshot,
                    market_snapshot=market_snapshot,
                )
                for repeat in range(repeats):
                    store.insert_decision(
                        symbol,
                        "NO_GO",
                        ["08_derivatives"],
                        [market_snapshot, plan_snapshot],
                        FROZEN,
                        f"hash-{cycle}-{index}-{repeat}",
                        payload,
                        ts_utc=(decided_at + timedelta(milliseconds=repeat)).isoformat(),
                    )
    return path


def report_of(path: Path, *, until: datetime = UNTIL) -> Any:
    with Store(path, read_only=True) as store:
        return grade(store, until=until)


# --- unit amatan --------------------------------------------------------------------------
def test_repeated_ticks_and_shared_market_row_do_not_inflate_n(tmp_path: Path) -> None:
    """12 baris `decisions` -> 4 episode -> 1 peristiwa. Ini alasan modul ini ada."""
    path = build_db(tmp_path, cycles=1, repeats=3)
    report = report_of(path)
    assert report.decisions_total == 12
    assert report.decisions_graded == 12
    tally = report.tallies["08_derivatives"]
    assert len(tally.episodes) == 4  # satu per simbol, bukan satu per baris
    rate = tally.rate
    assert rate.n == 4
    assert rate.events == 1  # satu snapshot market-wide untuk keempat simbol
    assert rate.n_effective == 1
    assert rate.enough is False
    # dan tiap episode membawa SEMUA id keputusan yang melahirkannya (tidak ada yang hilang)
    assert sorted(len(episode.decision_ids) for episode in tally.episodes) == [3, 3, 3, 3]


def test_distinct_cycles_are_distinct_events(tmp_path: Path) -> None:
    path = build_db(tmp_path, cycles=5, repeats=2)
    rate = report_of(path).tallies["08_derivatives"].rate
    assert rate.n == 20
    assert rate.events == 5
    assert rate.n_effective == 5


# --- gerbang 30 ---------------------------------------------------------------------------
def test_small_sample_says_belum_cukup_data(tmp_path: Path) -> None:
    path = build_db(tmp_path, cycles=5, repeats=2)
    text = render(report_of(path))
    assert "belum cukup data" in text
    assert "CI95" not in text


def test_tool_becomes_meaningful_by_itself_once_events_reach_the_threshold(tmp_path: Path) -> None:
    """Pasangan tes di atas: dengan 30 peristiwa yang benar-benar berbeda, CI tercetak.

    Angka di `data/ryo.db` hari ini jauh di bawah ini; yang dijaga tes ini adalah bahwa alatnya
    berubah jadi bermakna SENDIRI saat perekam menambah data — tanpa ada ambang yang diturunkan.
    """
    path = build_db(tmp_path, cycles=MIN_N, repeats=1, symbols=("SOL",), step_hours=48)
    far = START + timedelta(hours=48 * (MIN_N + 1))
    rate = report_of(path, until=far).tallies["08_derivatives"].rate
    assert rate.n == MIN_N
    assert rate.events == MIN_N
    assert rate.enough is True
    assert rate.interval is not None
    text = render(report_of(path, until=far))
    assert "CI95" in text
    assert f"n_efektif={MIN_N}" in text or f"{MIN_N:>10}" in text


def test_stop_touched_counts_as_a_loss_prevented(tmp_path: Path) -> None:
    path = build_db(tmp_path, cycles=MIN_N, repeats=1, symbols=("SOL",), price_path=[100.0, 89.0, 88.0])
    far = START + timedelta(hours=48 * (MIN_N + 1))
    rate = report_of(path, until=far).tallies["08_derivatives"].rate
    assert rate.k == MIN_N
    assert rate.n == MIN_N
    assert rate.point == 1.0


def test_target_touched_means_the_veto_was_wrong_and_is_not_hidden(tmp_path: Path) -> None:
    path = build_db(tmp_path, cycles=3, repeats=1, symbols=("SOL",), price_path=[100.0, 111.0, 112.0])
    report = report_of(path)
    assert report.tallies["08_derivatives"].rate.k == 0
    assert report.nogo_outcomes["target_first"] == 3
    assert "target_first=3" in render(report)


# --- yang dibuang, dengan cacahnya ---------------------------------------------------------
def test_other_params_generations_are_dropped_and_counted(tmp_path: Path) -> None:
    path = build_db(tmp_path, cycles=1, repeats=1, symbols=("SOL",))
    with Store(path) as store:
        store.insert_decision(
            "SOL",
            "NO_GO",
            ["08_derivatives"],
            [1, 2],
            "a" * 64,
            "hash-lain",
            card(entry=100.0, stop=90.0, target=110.0, plan_snapshot=9, market_snapshot=8),
            ts_utc=START.isoformat(),
        )
    report = report_of(path)
    assert report.decisions_graded == 1
    assert report.dropped["params_hash bukan generasi beku"] == 1
    assert report.dropped_params["aaaaaaaaaaaaaaaa"] == 1
    assert "generasi params yang dibuang" in render(report)


def test_decision_younger_than_the_horizon_is_dropped(tmp_path: Path) -> None:
    path = build_db(tmp_path, cycles=1, repeats=1, symbols=("SOL",))
    report = report_of(path, until=START + timedelta(hours=12))
    assert report.decisions_graded == 0
    assert report.dropped["belum berumur 24 j pada --until"] == 1


def test_card_without_a_plan_is_dropped_not_defaulted(tmp_path: Path) -> None:
    path = tmp_path / "noplan.db"
    with Store(path) as store:
        store.insert_decision(
            "SOL",
            "NO_GO",
            ["08_derivatives"],
            [1],
            FROZEN,
            "h",
            {"skill": {"data": {"items": []}}},
            ts_utc=START.isoformat(),
        )
    report = report_of(path)
    assert report.decisions_graded == 0
    assert report.dropped["kartu tanpa plan lengkap (item 09)"] == 1
    assert report.tallies["08_derivatives"].episodes == []


def test_missing_prices_never_become_not_a_loss(tmp_path: Path) -> None:
    """Ketiadaan harga dibuang dari `n` dan disebut, bukan dilipat jadi 'tidak rugi'."""
    path = tmp_path / "noprice.db"
    with Store(path) as store:
        store.insert_decision(
            "SOL",
            "NO_GO",
            ["08_derivatives"],
            [1, 2],
            FROZEN,
            "h",
            card(entry=100.0, stop=90.0, target=110.0, plan_snapshot=2, market_snapshot=1),
            ts_utc=START.isoformat(),
        )
    report = report_of(path)
    assert report.decisions_graded == 1
    tally = report.tallies["08_derivatives"]
    assert len(tally.episodes) == 1
    assert tally.rate.n == 0
    assert tally.rate.point is None
    assert tally.dropped["no_prices"] == 1
    assert "belum ada amatan" in render(report)


def test_unknown_blocking_id_is_counted_not_silently_tallied(tmp_path: Path) -> None:
    path = tmp_path / "aneh.db"
    with Store(path) as store:
        write_prices(store, "SOL", [(START + timedelta(hours=hour), 100.0) for hour in (1, 30)])
        store.insert_decision(
            "SOL",
            "NO_GO",
            ["99_tidak_ada", 7],
            [1, 2],
            FROZEN,
            "h",
            card(entry=100.0, stop=90.0, target=110.0, plan_snapshot=2, market_snapshot=1),
            ts_utc=START.isoformat(),
        )
    report = report_of(path)
    assert report.dropped["id item di blocking tidak dikenal"] == 2
    assert all(not tally.episodes for tally in report.tallies.values())


# --- Bunryo ---------------------------------------------------------------------------------
def test_bunryo_cells_are_class_by_regime_and_stay_silent_below_the_threshold(tmp_path: Path) -> None:
    path = build_db(tmp_path, cycles=2, repeats=1, symbols=("SOL", "BTC"))
    report = report_of(path)
    assert set(report.bunryo) == {("neutral", "neutral")}
    cell = report.bunryo[("neutral", "neutral")]
    assert cell.rate.n == 4
    text = render(report)
    assert "TIDAK ada takaran yang dikeluarkan" in text
    assert f"ambang n>={MIN_N}" in text


def test_bunryo_class_is_unspoken_when_the_card_has_no_stance(tmp_path: Path) -> None:
    """null tidak pernah jadi kelas default: ia jadi kelas `unspoken` yang terlihat."""
    path = tmp_path / "bunryo.db"
    payload = card(entry=100.0, stop=90.0, target=110.0, plan_snapshot=2, market_snapshot=1)
    for item in payload["skill"]["data"]["items"]:
        if item["id"] == "07_verdict_deep":
            item["value"] = {"stance": None}
    with Store(path) as store:
        write_prices(store, "SOL", [(START + timedelta(hours=hour), 100.0) for hour in (1, 30)])
        store.insert_decision("SOL", "NO_GO", [], [1, 2], FROZEN, "h", payload, ts_utc=START.isoformat())
    assert set(report_of(path).bunryo) == {("unspoken", "neutral")}


# --- baca-saja, idempoten, tidak memutus ----------------------------------------------------
def test_grade_refuses_a_writable_connection(tmp_path: Path) -> None:
    path = build_db(tmp_path, cycles=1, repeats=1, symbols=("SOL",))
    with Store(path) as writable, pytest.raises(GradingError, match="read_only"):
        grade(writable, until=UNTIL)


def test_no_writes_at_all(tmp_path: Path) -> None:
    path = build_db(tmp_path, cycles=2, repeats=2)
    before = md5(path)
    render(report_of(path))
    assert md5(path) == before
    assert not list(tmp_path.glob("*.db-wal"))
    assert not list(tmp_path.glob("*.db-journal"))


def test_two_runs_are_byte_identical(tmp_path: Path) -> None:
    """Idempoten: `--until` yang sama + DB yang sama -> teks yang sama persis."""
    path = build_db(tmp_path, cycles=3, repeats=2)
    assert render(report_of(path)) == render(report_of(path))


def test_report_never_prints_a_rate_without_n(tmp_path: Path) -> None:
    """CLAUDE.md: tidak ada angka agregat tanpa `n`. Setiap baris item membawa n + peristiwa."""
    path = build_db(tmp_path, cycles=2, repeats=2)
    text = render(report_of(path))
    for line in text.splitlines():
        if "CI95" in line or "belum cukup data" in line:
            assert "n=" in line or "n_efektif" in line or "peristiwa" in line, line


def test_params_hash_is_untouched_by_grading() -> None:
    """TASKS 5.0: params BEKU. Mengimpor `grading` tidak boleh menggesernya satu bit pun."""
    assert params_hash(DEFAULT_PARAMS) == "3d39fb1bd1a03b7f5269f53fec347d34645d2b402ff1c2de47e53f19d69616b7"


def test_decision_path_never_imports_grading() -> None:
    """ADR-003: alat ukur tidak boleh bisa memutus. Arah impornya satu arah, dan dijaga di sini."""
    offenders = []
    for folder in ("preflight", "agent", "core", "ui"):
        for module in sorted((ROOT / folder).rglob("*.py")):
            tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                if any(name == "grading" or name.startswith("grading.") for name in names):
                    offenders.append(str(module.relative_to(ROOT)))
    assert offenders == []


def test_cli_runs_read_only_and_reports(tmp_path: Path) -> None:
    path = build_db(tmp_path, cycles=2, repeats=2)
    before = md5(path)
    assert main(["--db", str(path), "--until", "2026-09-10T00:00:00Z"]) == 0
    assert md5(path) == before


def test_cli_rejects_bad_arguments(tmp_path: Path) -> None:
    path = build_db(tmp_path, cycles=1, repeats=1, symbols=("SOL",))
    assert main(["--db", str(path), "--until", "kemarin"]) == 2
    assert main(["--db", str(path), "--horizon-hours", "0"]) == 2


def test_module_entrypoint_exits_cleanly(tmp_path: Path) -> None:
    path = build_db(tmp_path, cycles=1, repeats=1, symbols=("SOL",))
    done = subprocess.run(  # argv tetap, tanpa shell
        [sys.executable, "-m", "grading.grader", "--db", str(path), "--until", "2026-09-10T00:00:00Z"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "SIMULATED" in done.stdout
    assert json.dumps(FROZEN)[1:-1] in done.stdout
