"""TASKS 4.7 — dua tanggal, simbol sama, keputusan berbeda; satu kartu ditahan bukti yang HILANG.

Yang dijaga di sini bukan "ada dua berkas fixture", melainkan tiga klaim yang dipakai juri:

1. **Keputusan berbeda, hash berbeda.** SOL GO pada 2026-09-03 dan NO_GO pada 2026-09-04, dengan
   `decision_hash` yang berbeda. Kalau keduanya sama, tidak ada yang bisa disebut "berubah".
2. **Penjelasan per item.** Perbedaan itu bisa ditunjuk sampai baris item: item mana yang pindah
   status, dengan kalimat alasan dari KEDUA tanggal, dan tool mana yang menyumbang baris itu.
3. **Satu kartu NO_GO karena bukti HILANG, bukan angka jelek** (syarat juri Fase 1). DGAI ditahan
   karena `deep_analysis` MENJAWAB tetapi menyebut `availability.trade_plan='unavailable'`, jadi
   `09_plan` `unspoken` dan ikut disebut di `blocking`. Tesnya menelusuri rantai itu sampai ke
   envelope beku di fixture, supaya klaimnya tidak bisa lolos dengan kalimat kartu saja.

Nilai `decision_hash` dipatok apa adanya. Kalau params/aturan item berubah, tes ini gagal — itu
memang gunanya: kartu demo tidak boleh diam-diam berubah arti.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.demo_fixture import DemoFixtureError, load_fixture
from agent.two_dates import (
    FIXTURE_EARLY,
    FIXTURE_LATE,
    CardView,
    ItemView,
    build_pair,
    changed_items,
    main,
    render,
)

ROOT = Path(__file__).resolve().parents[1]

#: Hash yang teramati saat fixture disalin (2026-09-05). Empat kartu, empat hash berbeda.
EXPECTED = {
    ("SOL", "early"): ("GO", (), "a5e0c4a74e4d2593cfe92ea551db2f569cb9f9f4afde42c300b0e645b75f6eea"),
    ("SOL", "late"): (
        "NO_GO",
        ("03_regime", "08_derivatives"),
        "4d3b08a8a955e82e525530869f7e72ccb7cff0c79b2177605729f811be2824ed",
    ),
    ("DGAI", "early"): (
        "NO_GO",
        ("02_completeness", "03_regime", "05_verdict_quick", "07_verdict_deep", "09_plan", "10_relative"),
        "a2f9c60722b9932981615716902cf55caf561c166661d606ea7622beb67ad67e",
    ),
    ("DGAI", "late"): (
        "NO_GO",
        (
            "02_completeness",
            "03_regime",
            "05_verdict_quick",
            "07_verdict_deep",
            "08_derivatives",
            "09_plan",
            "10_relative",
        ),
        "76066c1943b8bb38002e9d401eab461b4b773321017a030a1463a6b6680765dc",
    ),
}


@pytest.fixture(scope="module")
def pair() -> tuple[dict[str, CardView], dict[str, CardView]]:
    return build_pair()


def test_fixtures_are_two_dates_of_the_same_symbols() -> None:
    early = load_fixture(FIXTURE_EARLY)
    late = load_fixture(FIXTURE_LATE)
    assert early.symbols == late.symbols == ("SOL", "DGAI")
    assert early.now.date().isoformat() == "2026-09-03"
    assert late.now.date().isoformat() == "2026-09-04"
    # `params_hash` beku (TASKS 5.0): perbedaan kartu harus lahir dari BUKTI, bukan dari ambang.
    assert early.params_hash == late.params_hash


def test_same_symbol_two_dates_flip_the_decision_and_the_hash(
    pair: tuple[dict[str, CardView], dict[str, CardView]],
) -> None:
    early, late = pair
    assert early["SOL"].decision == "GO"
    assert late["SOL"].decision == "NO_GO"
    assert early["SOL"].decision_hash != late["SOL"].decision_hash
    # Keempat kartu punya hash yang berbeda satu sama lain — tidak ada dua kartu yang tertukar.
    hashes = {card.decision_hash for card in (*early.values(), *late.values())}
    assert len(hashes) == 4


@pytest.mark.parametrize(("symbol", "when"), sorted(EXPECTED))
def test_cards_match_what_was_recorded_at_capture(
    pair: tuple[dict[str, CardView], dict[str, CardView]], symbol: str, when: str
) -> None:
    cards = pair[0] if when == "early" else pair[1]
    card = cards[symbol]
    assert (card.decision, card.blocking, card.decision_hash) == EXPECTED[(symbol, when)]


def test_changed_items_name_the_items_and_carry_both_reasons(
    pair: tuple[dict[str, CardView], dict[str, CardView]],
) -> None:
    early, late = pair
    changes = changed_items(early["SOL"], late["SOL"])
    moved = {change.item_id: (change.before.status, change.after.status) for change in changes}
    assert moved["03_regime"] == ("green", "red")
    assert moved["08_derivatives"] == ("green", "red")
    for change in changes:
        # "Penjelasan per item" berarti kalimat dari KEDUA tanggal, bukan hanya yang terakhir.
        assert change.before.reason.strip()
        assert change.after.reason.strip()
        assert change.before.tool and change.after.tool
    # Item yang TIDAK berubah tidak boleh muncul sebagai perubahan.
    assert "01_freshness" not in moved


def test_no_go_from_missing_evidence_names_the_unanswered_item(
    pair: tuple[dict[str, CardView], dict[str, CardView]],
) -> None:
    """Syarat juri: penyebab NO_GO adalah data HILANG, dan `blocking` menyebut item itu."""
    card = pair[0]["DGAI"]
    assert card.decision == "NO_GO"
    assert "09_plan" in card.blocking

    plan_item = card.item("09_plan")
    assert plan_item.status == "unspoken"
    assert plan_item.tool == "deep_analysis"
    assert plan_item.field == "data.trade_plan"

    # Tidak satu pun penahan yang merah karena NILAI yang terbaca: yang merah hanya
    # `02_completeness`, dan ia merah justru karena lubangnya.
    reds = [item.id for item in card.blocking_items() if item.status == "red"]
    assert reds == ["02_completeness"]
    assert "not usable" in card.item("02_completeness").reason
    assert len(card.missing_causes()) == 5


def test_missing_plan_is_traced_to_the_frozen_envelope() -> None:
    """Bukan sekadar kalimat kartu: envelope beku sendiri menyebut `trade_plan` tidak tersedia."""
    fixture = load_fixture(FIXTURE_EARLY)
    deep = [
        snapshot
        for snapshot in fixture.snapshots
        if snapshot.tool == "deep_analysis" and snapshot.args.get("symbol") == "DGAI"
    ]
    assert len(deep) == 1
    envelope = deep[0].envelope
    # Tool-nya MENJAWAB (`status` ada, bukan envelope kosong) tetapi seksinya tidak terisi.
    assert envelope["status"] == "partial"
    assert envelope["availability"]["trade_plan"] == "unavailable"
    assert any("null values must not be treated as neutral or zero" in w for w in envelope["warnings"])


def test_answered_but_empty_is_distinguished_from_never_answered(
    pair: tuple[dict[str, CardView], dict[str, CardView]],
) -> None:
    """Dua rupa "hilang" tidak boleh terbaca sama di kartu."""
    card = pair[0]["DGAI"]
    assert card.item("09_plan").answered is True  # envelope-nya dibekukan, field-nya kosong
    assert card.item("10_relative").answered is False  # tidak ada envelope compare_tokens sama sekali


def test_every_item_names_the_tool_that_answered_it(
    pair: tuple[dict[str, CardView], dict[str, CardView]],
) -> None:
    """ "Kontribusi tiap tool": tiap baris panggilan menyebut tool sumbernya."""
    for cards in pair:
        for card in cards.values():
            assert len(card.items) == 10
            assert all(item.tool for item in card.items)
            assert {item.tool for item in card.items} >= {
                "market_overview",
                "monitor_market_sentiment_shift",
                "analyze_token",
                "deep_analysis",
                "compare_tokens",
            }


def test_render_is_identical_across_runs_and_shows_both_hashes() -> None:
    first = render(*build_pair())
    second = render(*build_pair())
    assert first == second
    for _, _, digest in EXPECTED.values():
        assert any(digest in line for line in first)


def test_render_keeps_the_simulated_banner_and_shows_no_pnl() -> None:
    lines = render(*build_pair())
    assert any("SIMULATED" in line for line in lines)
    text = "\n".join(lines).lower()
    for forbidden in ("pnl", "p&l", "profit", "return %", "equity"):
        assert forbidden not in text


def test_cli_prints_the_pair_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "SOL" in out and "DGAI" in out
    assert "09_plan" in out


def test_cli_json_is_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["simulated"] is True
    assert payload["cards"]["SOL"]["early"]["decision"] == "GO"
    assert payload["cards"]["SOL"]["late"]["decision"] == "NO_GO"


def test_refuses_to_run_against_the_evidence_store() -> None:
    """Fixture bukan bukti: DB tujuan yang merupakan store perekam ditolak (penjaga TASKS 0.19)."""
    with pytest.raises(DemoFixtureError):
        build_pair(db_dir=ROOT / "data")


def test_cli_reports_a_broken_fixture_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{}", encoding="utf-8")
    assert main(["--early", str(broken)]) == 2
    assert "two-dates:" in capsys.readouterr().err


def test_item_view_is_read_only() -> None:
    item = ItemView("01_freshness", "green", "market_overview", "as_of", True, "x", "y")
    with pytest.raises(AttributeError):
        item.status = "red"  # type: ignore[misc]


def test_tool_contribution_counts_only_lines_that_can_be_spoken(
    pair: tuple[dict[str, CardView], dict[str, CardView]],
) -> None:
    """Tool yang envelope-nya beku tetapi field-nya kosong tidak boleh terlihat menyumbang penuh."""
    sol = pair[0]["SOL"].tool_contribution()
    dgai = pair[0]["DGAI"].tool_contribution()
    # SOL: kelima tool menyumbang baris yang benar-benar tersebut.
    assert sol["deep_analysis"] == (3, 3)
    assert sol["compare_tokens"] == (1, 1)
    # DGAI: `deep_analysis` menyumbang tiga baris, hanya satu yang bisa disebut; `compare_tokens`
    # tidak menjawab sama sekali.
    assert dgai["deep_analysis"] == (3, 1)
    assert dgai["compare_tokens"] == (1, 0)
