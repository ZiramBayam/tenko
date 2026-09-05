"""Tes `grading/stats.py` (TASKS 3.3) — Wilson tulis tangan + `n` yang jujur.

Yang dijaga, berurut kepentingan:

1. **Gerbang 30 tidak bisa dilewati diam-diam.** `n_effective < MIN_N` -> `interval is None`,
   dan kalimatnya berbunyi "belum cukup data" beserta angkanya.
2. **Duplikasi bukti MELEBARKAN selang, bukan mempersempitnya.** Ini invarian yang membuat
   modul ini ada: 24/120 dari 30 peristiwa harus lebih tidak yakin daripada 24/120 dari 120.
3. **Cacah yang tidak mungkin dilempar, bukan dibulatkan.** `n=0`, `k>n`, `events>n`, `bool`.
4. Nilai Wilson dicek terhadap angka yang dihitung tangan dari rumusnya, bukan terhadap
   implementasi lain (tidak ada implementasi lain — itu intinya).
"""

from __future__ import annotations

import math

import pytest

from grading.stats import (
    MIN_N,
    Z_95,
    GradingStatsError,
    observed_rate,
    wilson_interval,
)


def reference(k: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Rumus Wilson ditulis ulang bebas dari implementasinya (oracle independen)."""
    p = k / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return center - half, center + half


@pytest.mark.parametrize(("k", "n"), [(0, 30), (1, 30), (15, 30), (29, 30), (30, 30), (7, 100)])
def test_wilson_matches_reference(k: int, n: int) -> None:
    interval = wilson_interval(k, n)
    low, high = reference(k, n)
    assert interval.low == pytest.approx(max(0.0, low), abs=1e-12)
    assert interval.high == pytest.approx(min(1.0, high), abs=1e-12)


def test_wilson_stays_inside_unit_interval() -> None:
    """k=0 dan k=n adalah tepi tempat aproksimasi normal paling gampang keluar [0,1]."""
    for k, n in ((0, 30), (30, 30), (0, 31), (31, 31)):
        interval = wilson_interval(k, n)
        assert 0.0 <= interval.low <= interval.high <= 1.0


@pytest.mark.parametrize(
    ("k", "n"),
    [(0, 0), (1, 0), (2, 1), (-1, 5), (5, -1)],
)
def test_wilson_rejects_impossible_counts(k: int, n: int) -> None:
    with pytest.raises(GradingStatsError):
        wilson_interval(k, n)


def test_wilson_rejects_bool_and_bad_z() -> None:
    """`True` bukan 1: bool yang lolos akan menjadi cacah yang tidak pernah ada."""
    with pytest.raises(GradingStatsError):
        wilson_interval(True, 30)
    with pytest.raises(GradingStatsError):
        wilson_interval(1, 30, z=0)
    with pytest.raises(GradingStatsError):
        wilson_interval(1, 30, z=float("nan"))


# --- observed_rate: `n` yang jujur --------------------------------------------------------
def test_below_threshold_gives_no_interval_and_says_so() -> None:
    rate = observed_rate(0, 5, 2)
    assert rate.n_effective == 2
    assert rate.enough is False
    assert rate.interval is None
    sentence = rate.sentence("08_derivatives")
    assert "belum cukup data" in sentence
    assert "0 dari 5" in sentence
    assert "peristiwa bukti berbeda=2" in sentence
    assert "CI" not in sentence


def test_exactly_at_threshold_speaks() -> None:
    """MIN_N adalah ambang tertutup: n_efektif == 30 sudah cukup, 29 belum."""
    assert observed_rate(10, MIN_N, MIN_N).enough is True
    assert observed_rate(10, MIN_N - 1, MIN_N - 1).enough is False
    assert observed_rate(10, MIN_N - 1, MIN_N - 1).interval is None


def test_duplicated_evidence_widens_the_interval() -> None:
    """Invarian inti: bukti yang dihitung berulang tidak boleh membuat kita lebih yakin."""
    honest = observed_rate(24, 120, 120)
    duplicated = observed_rate(24, 120, 30)
    assert honest.point == duplicated.point == 0.2
    assert honest.interval is not None
    assert duplicated.interval is not None
    honest_width = honest.interval.high - honest.interval.low
    duplicated_width = duplicated.interval.high - duplicated.interval.low
    assert duplicated_width > honest_width
    # dan pengempisan menjaga proporsinya, bukan menggeser jawabannya
    assert duplicated.k_effective == 6
    assert duplicated.n_effective == 30


def test_point_estimate_is_never_deflated() -> None:
    rate = observed_rate(24, 120, 30)
    assert rate.k == 24
    assert rate.n == 120
    assert rate.point == 0.2


def test_n_zero_is_not_a_rate() -> None:
    """0/0 bukan 0 (CLAUDE.md): tidak ada titik estimasi, tidak ada selang."""
    rate = observed_rate(0, 0, 0)
    assert rate.point is None
    assert rate.interval is None
    assert rate.enough is False
    assert "belum ada amatan" in rate.sentence("09_plan")


def test_more_events_than_observations_is_a_bug_not_a_widening() -> None:
    with pytest.raises(GradingStatsError, match="melebihi"):
        observed_rate(1, 3, 4)


@pytest.mark.parametrize(("k", "n", "events"), [(4, 3, 3), (-1, 3, 3), (1, 3, -1)])
def test_observed_rate_rejects_impossible_counts(k: int, n: int, events: int) -> None:
    with pytest.raises(GradingStatsError):
        observed_rate(k, n, events)


def test_half_up_rounding_is_stable_across_items() -> None:
    """`round()` Python membulatkan setengah ke genap; dua item dengan rasio sama harus sama."""
    # k*n_eff/n = 0.5 persis: `round()` akan memberi 0, floor(x+0.5) memberi 1.
    assert observed_rate(1, 4, 2).k_effective == 1
    assert observed_rate(3, 4, 2).k_effective == 2
    # dan tidak pernah melebihi n_eff meski pembulatan ke atas
    assert observed_rate(3, 3, 2).k_effective == 2


def test_as_dict_always_carries_n_and_events() -> None:
    """Tidak ada angka agregat tanpa `n` (CLAUDE.md) — termasuk di bentuk mesin."""
    payload = observed_rate(0, 5, 2).as_dict()
    assert payload["n"] == 5
    assert payload["events"] == 2
    assert payload["n_effective"] == 2
    assert payload["interval"] is None
    assert payload["enough"] is False
