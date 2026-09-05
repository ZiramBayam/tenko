"""Tes `grading/outcomes.py` (TASKS 3.3) — jawaban spec §6 per keputusan.

Yang dijaga, berurut kepentingan:

1. **Jendela yang belum selesai bukan "tidak kena stop".** Perekam yang berhenti sebelum
   horizon -> `window_not_covered`, dan `would_have_lost` -> None, bukan False.
2. **Sisi yang tersentuh lebih dulu menang**, dan sampel PADA detik keputusan tidak ikut
   (batas kiri terbuka: harga itu sudah dilihat keputusan tersebut).
3. **Plan yang tidak lengkap / arahnya terbalik -> None**, tidak pernah ditambal default.
4. **Baca-saja adalah sifat FUNGSI**, bukan sifat `main()`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from core.store import Store
from grading.outcomes import (
    NO_PRICES,
    OPEN_AT_HORIZON,
    STOP_FIRST,
    TARGET_FIRST,
    WINDOW_NOT_COVERED,
    GradingDataError,
    Plan,
    PricePoint,
    plan_from_card,
    price_series,
    resolve_outcome,
    series_args_hash,
)

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)
LONG = Plan(direction="long", entry=100.0, stop=90.0, target=110.0, snapshot_id=7, targets=(110.0, 120.0))
SHORT = Plan(direction="short", entry=100.0, stop=110.0, target=90.0, snapshot_id=7, targets=(90.0,))


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`Store()` tanpa argumen mendarat di tmp_path — DB hidup tidak pernah disentuh."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "grading.db"))


def points(*pairs: tuple[float, float]) -> list[PricePoint]:
    """(jam setelah NOW, harga) -> deret PricePoint."""
    return [
        PricePoint(ts=NOW + timedelta(hours=hours), price=price, snapshot_id=index + 1)
        for index, (hours, price) in enumerate(pairs)
    ]


def card_with_plan(value: Any, *, snapshot_id: int | None = 7) -> dict[str, Any]:
    return {
        "skill": {
            "data": {
                "items": [
                    {"id": "01_freshness", "status": "green", "value": 1},
                    {
                        "id": "09_plan",
                        "status": "green",
                        "value": value,
                        "source": {"snapshot_id": snapshot_id, "tool": "deep_analysis"},
                    },
                ]
            }
        }
    }


GOOD_PLAN = {
    "direction": "long",
    "entry": 100.0,
    "stop": 90.0,
    "targets": [110.0, 120.0],
    "risk_reward": [1.0, 2.0],
}


# --- plan_from_card -----------------------------------------------------------------------
def test_plan_read_from_card_not_from_todays_snapshot() -> None:
    plan = plan_from_card(card_with_plan(GOOD_PLAN))
    assert plan == Plan(
        direction="long", entry=100.0, stop=90.0, target=110.0, snapshot_id=7, targets=(110.0, 120.0)
    )


@pytest.mark.parametrize(
    "broken",
    [
        {**GOOD_PLAN, "entry": None},
        {**GOOD_PLAN, "stop": None},
        {**GOOD_PLAN, "targets": []},
        {**GOOD_PLAN, "targets": [None]},
        {**GOOD_PLAN, "direction": "sideways"},
        {**GOOD_PLAN, "direction": None},
        {**GOOD_PLAN, "stop": 105.0},  # stop di atas entry untuk long -> arah terbalik
        {**GOOD_PLAN, "targets": [95.0]},  # target di bawah entry untuk long
        {**GOOD_PLAN, "entry": True},  # bool bukan harga
        {**GOOD_PLAN, "entry": float("inf")},
    ],
)
def test_incomplete_or_inverted_plan_is_none_never_defaulted(broken: dict[str, Any]) -> None:
    assert plan_from_card(card_with_plan(broken)) is None


def test_old_generation_card_has_no_plan() -> None:
    """Kartu generasi lama memakai id item berupa angka; itu bukan plan yang bisa dinilai."""
    old = {"data": {"items": [{"id": 9, "value": GOOD_PLAN}]}}
    assert plan_from_card(old) is None
    assert plan_from_card({"skill": {"data": {"items": []}}}) is None
    assert plan_from_card({"skill": {"data": {}}}) is None
    assert plan_from_card("bukan kartu") is None


def test_plan_without_source_snapshot_still_readable() -> None:
    plan = plan_from_card(card_with_plan(GOOD_PLAN, snapshot_id=None))
    assert plan is not None
    assert plan.snapshot_id is None


# --- resolve_outcome ----------------------------------------------------------------------
def test_stop_touched_first_is_a_loss_avoided() -> None:
    outcome = resolve_outcome(LONG, points((1, 99.0), (2, 89.0), (3, 111.0), (30, 100.0)), NOW)
    assert outcome.status == STOP_FIRST
    assert outcome.would_have_lost is True
    assert outcome.touched_price == 89.0
    assert outcome.touched_snapshot_id == 2


def test_target_touched_first_means_the_veto_held_back_a_winner() -> None:
    outcome = resolve_outcome(LONG, points((1, 111.0), (2, 89.0), (30, 100.0)), NOW)
    assert outcome.status == TARGET_FIRST
    assert outcome.would_have_lost is False


def test_untouched_but_complete_window_is_an_answer_not_a_hole() -> None:
    outcome = resolve_outcome(LONG, points((1, 101.0), (12, 99.0), (25, 100.0)), NOW)
    assert outcome.status == OPEN_AT_HORIZON
    assert outcome.resolved is True
    assert outcome.would_have_lost is False
    assert outcome.samples == 2  # sampel jam ke-25 di luar jendela, tetapi membuktikan cakupan


def test_recorder_stopped_before_horizon_is_unresolved_not_a_win() -> None:
    """Jebakan utama: tanpa syarat cakupan, keputusan 2 jam lalu terbaca 'tidak kena stop'."""
    outcome = resolve_outcome(LONG, points((1, 101.0), (2, 99.0)), NOW)
    assert outcome.status == WINDOW_NOT_COVERED
    assert outcome.resolved is False
    assert outcome.would_have_lost is None


def test_no_samples_at_all_is_no_prices() -> None:
    outcome = resolve_outcome(LONG, [], NOW)
    assert outcome.status == NO_PRICES
    assert outcome.would_have_lost is None
    assert outcome.samples == 0


def test_price_at_the_decision_second_is_not_the_future() -> None:
    """Batas kiri terbuka: harga pada detik keputusan sudah dilihat keputusan itu.

    Ia karena itu tidak boleh menyentuh stop, dan jendela tanpa sampel lain adalah
    `no_prices` — ketiadaan data, bukan "tidak kena stop".
    """
    at_decision = [PricePoint(ts=NOW, price=89.0, snapshot_id=1), *points((25, 100.0))]
    outcome = resolve_outcome(LONG, at_decision, NOW)
    assert outcome.status == NO_PRICES
    assert outcome.resolved is False
    assert outcome.would_have_lost is None
    assert outcome.samples == 0


def test_sample_exactly_at_horizon_counts() -> None:
    outcome = resolve_outcome(LONG, points((24, 89.0)), NOW)
    assert outcome.status == STOP_FIRST


def test_short_plan_reverses_both_sides() -> None:
    assert resolve_outcome(SHORT, points((1, 111.0), (25, 100.0)), NOW).status == STOP_FIRST
    assert resolve_outcome(SHORT, points((1, 89.0), (25, 100.0)), NOW).status == TARGET_FIRST


def test_gap_between_samples_is_reported_because_touches_hide_there() -> None:
    outcome = resolve_outcome(LONG, points((1, 101.0), (20, 101.0), (25, 100.0)), NOW)
    assert outcome.max_gap_min == pytest.approx(19 * 60.0)


def test_outcome_dict_is_labelled_simulated() -> None:
    payload = resolve_outcome(LONG, points((1, 89.0), (25, 100.0)), NOW).as_dict()
    assert payload["simulated"] is True
    assert payload["would_have_lost"] is True
    assert payload["horizon_at"] == "2026-09-05T12:00:00Z"


# --- price_series -------------------------------------------------------------------------
def build_price_db(tmp_path: Path, prices: list[tuple[datetime, Any]], *, symbol: str = "SOL") -> Path:
    path = tmp_path / "prices.db"
    with Store(path) as store:
        for moment, price in prices:
            stamp = moment.replace(microsecond=0).isoformat()
            envelope = {
                "as_of": stamp.replace("+00:00", "Z"),
                "status": "ok",
                "data": {"market": {"price_usd": price}},
            }
            store.insert_snapshot("analyze_token", {"symbol": symbol}, envelope, ts_utc=stamp)
    return path


def test_series_args_hash_matches_recorder_shape() -> None:
    """Seri dipilih dengan args_hash yang sama dengan yang ditulis perekam."""
    canonical = json.dumps({"symbol": "SOL"}, sort_keys=True, separators=(",", ":"))
    assert series_args_hash("SOL") == hashlib.sha256(canonical.encode()).hexdigest()


def test_price_series_reads_ascending_and_counts_unusable(tmp_path: Path) -> None:
    path = build_price_db(
        tmp_path,
        [
            (NOW + timedelta(hours=2), 102.0),
            (NOW + timedelta(hours=1), 101.0),
            (NOW + timedelta(hours=3), None),  # null tidak pernah jadi 0
            (NOW + timedelta(hours=4), "103"),  # teks bukan harga
            (NOW + timedelta(hours=5), -1.0),  # harga tidak positif
            (NOW + timedelta(hours=6), True),  # bool bukan harga
        ],
    )
    with Store(path, read_only=True) as store:
        series, unusable = price_series(store, "SOL")
    assert [point.price for point in series] == [101.0, 102.0]
    assert unusable == 4


def test_price_series_ignores_other_symbols(tmp_path: Path) -> None:
    path = build_price_db(tmp_path, [(NOW, 100.0)], symbol="SOL")
    with Store(path) as store:
        store.insert_snapshot(
            "analyze_token",
            {"symbol": "BTC"},
            {"as_of": "2026-09-04T12:00:00Z", "data": {"market": {"price_usd": 80000.0}}},
            ts_utc=NOW.isoformat(),
        )
    with Store(path, read_only=True) as store:
        assert [point.price for point in price_series(store, "SOL")[0]] == [100.0]
        assert [point.price for point in price_series(store, "BTC")[0]] == [80000.0]


def test_price_series_refuses_a_writable_connection(tmp_path: Path) -> None:
    """Baca-saja adalah sifat fungsi: `Store._select` meneruskan SQL apa pun ke koneksinya."""
    path = build_price_db(tmp_path, [(NOW, 100.0)])
    with Store(path) as writable, pytest.raises(GradingDataError, match="read_only"):
        price_series(writable, "SOL")


def test_price_series_survives_rows_older_than_fresh_scan_limit(tmp_path: Path) -> None:
    """Jebakan FRESH_SCAN_LIMIT=64: jalur harga TIDAK lewat `latest_fresh`, jadi baris ke-100
    dari belakang tetap terbaca. Kalau tes ini merah, laporan diam-diam kehilangan sejarah."""
    prices = [(NOW + timedelta(minutes=10 * index), 100.0 + index) for index in range(200)]
    path = build_price_db(tmp_path, prices)
    with Store(path, read_only=True) as store:
        series, _ = price_series(store, "SOL")
    assert len(series) == 200
    assert series[0].price == 100.0
