"""Dua tanggal, simbol sama, keputusan berbeda — spec §7 langkah 6 (TASKS 4.7).

Spec §7 langkah 6: "Dua tanggal (fixture): simbol sama, item berbeda → keputusan berbeda, kartu
menjelaskan item mana yang berubah." Modul ini menjalankan dua fixture bertanggal lewat mesin yang
sudah ada (:mod:`agent.demo_fixture`, TASKS 3.4a) dan mencetak kartunya berdampingan.

**Bukti yang dipertaruhkan, dan kenapa dua simbol, bukan satu:**

* ``SOL`` menjawab AC 4.7 apa adanya — GO pada 2026-09-03, NO_GO pada 2026-09-04, dua
  ``decision_hash`` berbeda, dan perbedaannya bisa ditunjuk sampai baris item (``03_regime`` dan
  ``08_derivatives`` pindah hijau→merah). Params-nya SAMA (``params_hash`` beku ``3d39fb1b…``),
  jadi yang berubah hanya buktinya.
* ``DGAI`` menjawab syarat juri Fase 1: NO_GO yang penyebabnya bukti **HILANG**, bukan angka jelek.
  ``deep_analysis`` MENJAWAB untuk DGAI — envelope-nya dibekukan, ``status: partial`` — tetapi
  menyebut ``availability.trade_plan='unavailable'`` berikut peringatan "null values must not be
  treated as neutral or zero". Karena itu ``09_plan`` menjadi ``unspoken`` dan IKUT DISEBUT di
  ``blocking``; nilai default tidak pernah diisikan (ADR-013). Dari enam penahan pada 2026-09-03,
  lima ``unspoken`` dan satu-satunya yang merah (``02_completeness``) merah justru karena lubang itu.

Kontras SOL vs DGAI pada tanggal yang sama adalah intinya: pasar yang sama, tool yang sama, satu
simbol ditahan **angka**, satu simbol ditahan **jawaban yang tidak ada**, dan kartu menyebut yang
mana yang mana.

**Batas yang jujur.** Tidak ada satu pun simbol di ``data/ryo.db`` yang bisa memperagakan
GO → NO_GO-karena-hilang pada dirinya sendiri: hanya SOL/BTC/ETH/BNB yang pernah mencapai GO
(karena hanya mereka yang pernah muncul di ``compare_tokens``, bahan item 10), dan keempatnya tidak
pernah kehilangan satu pun seksi wajib. Satu-satunya simbol dengan seksi wajib yang hilang adalah
DGAI, dan DGAI karena itu tidak pernah bisa GO. Jadi pembalikan keputusan dan NO_GO-karena-hilang
dibawa oleh dua simbol yang berbeda dalam fixture yang sama — bukan disatukan dengan melonggarkan
apa pun.

**Kartu dibaca dari buku besar, bukan dihitung ulang untuk dicetak.** Setiap kartu di sini berasal
dari baris ``decisions.card_json`` yang ditulis tick — sumber yang sama dengan yang dibaca UI.
Mencetak dari hasil `preflight` kedua akan membuat kartu dan hash berasal dari dua perhitungan yang
berbeda, dan "hash ini milik kartu ini" berhenti berarti apa-apa.

Pemakaian::

    python -m agent.two_dates            # dua kartu berdampingan, per item, per tool
    python -m agent.two_dates --json     # bentuk mesin
    python -m agent.two_dates --symbol SOL

Keluarannya deterministik: jam beku dari fixture, DB tmp baru tiap run, tanpa jam dinding dan tanpa
path DB di dalam laporan. Tidak ada P&L di sini — rubric §8 tidak menilainya, dan kartu ini bicara
soal keputusan.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.demo_fixture import DemoFixture, DemoFixtureError, load_fixture, run_tick
from agent.ledger import LedgerError, read_decision
from agent.main import BANNER
from core.store import StoreError

#: Fixture bertanggal 4.7. Keduanya disalin dari `data/ryo.db` dengan
#: `python -m agent.demo_fixture --capture`; satu jendela siklus perekam utuh per berkas, jadi
#: tidak ada baris yang "dihilangkan" untuk membuat lubang.
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_EARLY = FIXTURES / "twodate-a-2026-09-03.json"
FIXTURE_LATE = FIXTURES / "twodate-b-2026-09-04.json"

#: Lebar kolom laporan. Dipatok supaya dua kartu bisa dibaca berdampingan di terminal 100 kolom.
_ID_W = 20
_TOOL_W = 32
_STATUS_W = 14


class TwoDatesError(Exception):
    """Pasangan fixture tidak bisa dijalankan atau kartunya tidak terbaca."""


@dataclass(frozen=True, slots=True)
class ItemView:
    """Satu baris panggilan seperti tertulis di kartu — termasuk tool yang menjawabnya."""

    id: str
    status: str
    tool: str
    field: str
    #: True bila envelope tool-nya BENAR-BENAR dibekukan untuk baris ini. Bedanya penting:
    #: `unspoken` dengan `answered=True` berarti "tool menjawab, field-nya kosong"; dengan
    #: `answered=False` berarti "tidak ada envelope sama sekali".
    answered: bool
    as_of: str
    reason: str


@dataclass(frozen=True, slots=True)
class CardView:
    """Kartu panggilan satu simbol pada satu tanggal, dibaca dari `decisions.card_json`."""

    symbol: str
    date: str
    now: str
    decision: str
    blocking: tuple[str, ...]
    decision_hash: str
    items: tuple[ItemView, ...]

    def item(self, item_id: str) -> ItemView:
        for item in self.items:
            if item.id == item_id:
                return item
        raise TwoDatesError(f"kartu {self.symbol}@{self.date} tidak punya baris {item_id!r}")

    def blocking_items(self) -> tuple[ItemView, ...]:
        return tuple(self.item(item_id) for item_id in self.blocking)

    def missing_causes(self) -> tuple[ItemView, ...]:
        """Penahan yang berstatus `unspoken` — penahan yang lahir dari bukti yang TIDAK ADA."""
        return tuple(item for item in self.blocking_items() if item.status == "unspoken")

    def tool_contribution(self) -> dict[str, tuple[int, int]]:
        """`{tool: (baris yang disumbang, berapa di antaranya BISA disebut)}` — urut tool.

        "Bisa disebut" = statusnya hijau atau merah. `unspoken` sengaja dihitung sebagai TIDAK
        disumbang: tool yang envelope-nya beku tetapi field-nya kosong tidak boleh terlihat
        seolah ia menyumbang baris panggilan yang penuh.
        """
        out: dict[str, tuple[int, int]] = {}
        for item in self.items:
            total, spoken = out.get(item.tool, (0, 0))
            out[item.tool] = (total + 1, spoken + (0 if item.status == "unspoken" else 1))
        return dict(sorted(out.items()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "date": self.date,
            "now": self.now,
            "decision": self.decision,
            "blocking": list(self.blocking),
            "decision_hash": self.decision_hash,
            "items": [
                {
                    "id": item.id,
                    "status": item.status,
                    "tool": item.tool,
                    "field": item.field,
                    "answered": item.answered,
                    "as_of": item.as_of,
                    "reason": item.reason,
                }
                for item in self.items
            ],
            "simulated": True,
        }


@dataclass(frozen=True, slots=True)
class ItemChange:
    """Satu baris panggilan yang berpindah status antara dua tanggal."""

    item_id: str
    before: ItemView
    after: ItemView


def _items_from_card(card: Mapping[str, Any], symbol: str) -> tuple[ItemView, ...]:
    """Baca kesepuluh baris panggilan dari `card_json`.

    Kartu penolakan (`data is None`, mis. bukti gagal diverifikasi) tetap menyebut kesepuluh
    barisnya di `availability` — dibaca dari sana supaya kartu tidak pernah kosong diam-diam.
    """
    skill = card.get("skill")
    if not isinstance(skill, Mapping):
        raise TwoDatesError(f"card_json {symbol} tidak memuat envelope skill")
    data = skill.get("data")
    if not isinstance(data, Mapping):
        availability = skill.get("availability")
        if not isinstance(availability, Mapping):
            raise TwoDatesError(
                f"kartu {symbol} tanpa data dan tanpa availability: tidak ada yang bisa dibaca"
            )
        return tuple(
            ItemView(
                str(item_id), str(status), "-", "-", False, "-", "envelope penolakan: tidak ada bukti beku"
            )
            for item_id, status in sorted(availability.items())
        )
    rows = data.get("items")
    if not isinstance(rows, list):
        raise TwoDatesError(f"kartu {symbol}: data.items bukan daftar")
    views: list[ItemView] = []
    for row in rows:
        source = row.get("source") if isinstance(row, Mapping) else None
        source = source if isinstance(source, Mapping) else {}
        views.append(
            ItemView(
                id=str(row.get("id", "?")),
                status=str(row.get("status", "?")),
                tool=str(source.get("tool") or "-"),
                field=str(source.get("field") or "-"),
                answered=source.get("snapshot_id") is not None,
                as_of=str(source.get("as_of") or "-"),
                reason=str(row.get("reason") or ""),
            )
        )
    return tuple(views)


def cards_for(fixture: DemoFixture, db_path: str | Path) -> dict[str, CardView]:
    """Satu tick atas fixture di `db_path`, lalu kartunya dibaca kembali dari buku besar."""
    report = run_tick(fixture, db_path)
    cards: dict[str, CardView] = {}
    date = fixture.now.date().isoformat()
    for outcome in report.outcomes:
        if outcome.decision_id is None:
            raise TwoDatesError(
                f"{outcome.symbol} tidak menghasilkan baris keputusan "
                f"({outcome.rejected or outcome.insufficient or 'tanpa alasan'})"
            )
        row = read_decision(db_path, outcome.decision_id)
        if row is None:
            raise TwoDatesError(
                f"decision_id {outcome.decision_id} ({outcome.symbol}) tidak ada di buku besar"
            )
        cards[outcome.symbol] = CardView(
            symbol=row.symbol,
            date=date,
            now=fixture.now.isoformat(),
            decision=row.decision,
            blocking=tuple(str(entry) for entry in row.blocking()),
            decision_hash=row.decision_hash,
            items=_items_from_card(row.card(), row.symbol),
        )
    return cards


def build_pair(
    early_path: str | Path = FIXTURE_EARLY,
    late_path: str | Path = FIXTURE_LATE,
    *,
    db_dir: str | Path | None = None,
) -> tuple[dict[str, CardView], dict[str, CardView]]:
    """Jalankan kedua fixture, masing-masing di DB BARU, dan kembalikan kartunya.

    `db_dir` hanya untuk tes penjaga: DB tujuan yang merupakan store bukti ditolak
    :func:`agent.demo_fixture.refuse_evidence_db` sebelum satu baris pun ditulis.
    """
    early = load_fixture(early_path)
    late = load_fixture(late_path)
    if early.symbols != late.symbols:
        raise TwoDatesError(
            f"dua fixture menyebut watchlist berbeda ({early.symbols} vs {late.symbols}): "
            "'simbol sama, tanggal berbeda' tidak bisa dibuktikan atas dua daftar yang berbeda"
        )
    if early.now.date() == late.now.date():
        raise TwoDatesError(f"dua fixture bertanggal sama ({early.now.date()}): ini bukan dua tanggal")
    if db_dir is not None:
        target = Path(db_dir)
        return cards_for(early, target / "twodate-a.db"), cards_for(late, target / "twodate-b.db")
    with tempfile.TemporaryDirectory(prefix="tenko-twodates-") as tmp:
        return cards_for(early, Path(tmp) / "a.db"), cards_for(late, Path(tmp) / "b.db")


def changed_items(before: CardView, after: CardView) -> list[ItemChange]:
    """Baris panggilan yang berpindah status antara dua tanggal, urut id item."""
    later = {item.id: item for item in after.items}
    return [
        ItemChange(item.id, item, later[item.id])
        for item in before.items
        if item.id in later and later[item.id].status != item.status
    ]


def _hash_note(early: CardView, late: CardView) -> str:
    return "berbeda" if early.decision_hash != late.decision_hash else "SAMA — tidak ada yang berubah"


def _cause_line(card: CardView) -> str:
    """Satu kalimat: penahannya berapa, berapa yang HILANG, berapa yang merah karena nilai."""
    if not card.blocking:
        return "tidak ada penahan; kesepuluh baris panggilan terjawab"
    missing = card.missing_causes()
    red = [item for item in card.blocking_items() if item.status == "red"]
    return (
        f"{len(card.blocking)} penahan: {len(missing)} tidak terjawab (bukti HILANG), "
        f"{len(red)} merah (nilai yang terbaca menahan)"
    )


def _render_symbol(symbol: str, early: CardView, late: CardView) -> list[str]:
    lines = [
        f"=== {symbol} ===",
        f"  A {early.date}  {early.decision:<6} hash {early.decision_hash}",
        f"     blocking: {', '.join(early.blocking) or '-'}",
        f"  B {late.date}  {late.decision:<6} hash {late.decision_hash}",
        f"     blocking: {', '.join(late.blocking) or '-'}",
        f"  keputusan: {early.decision} -> {late.decision}; decision_hash: {_hash_note(early, late)}",
        "",
        f"  {'item':<{_ID_W}}{'tool':<{_TOOL_W}}"
        f"{'A ' + early.date:<{_STATUS_W}}{'B ' + late.date:<{_STATUS_W}}",
    ]
    later = {item.id: item for item in late.items}
    for item in early.items:
        other = later.get(item.id)
        mark = "  <- berubah" if other is not None and other.status != item.status else ""
        lines.append(
            f"  {item.id:<{_ID_W}}{item.tool:<{_TOOL_W}}{item.status:<{_STATUS_W}}"
            f"{(other.status if other else '-'):<{_STATUS_W}}{mark}"
        )
    changes = changed_items(early, late)
    lines.append("")
    if changes:
        lines.append("  item yang berubah, dengan kalimatnya sendiri dari kedua tanggal:")
        for change in changes:
            lines.append(f"    {change.item_id}: {change.before.status} -> {change.after.status}")
            lines.append(
                f"      A {early.date} ({change.before.tool}.{change.before.field}): {change.before.reason}"
            )
            lines.append(
                f"      B {late.date} ({change.after.tool}.{change.after.field}): {change.after.reason}"
            )
    else:
        lines.append("  tidak ada baris panggilan yang berpindah status antara kedua tanggal")
    for label, card in (("A " + early.date, early), ("B " + late.date, late)):
        lines.append("")
        lines.append(f"  {label} {card.decision} — {_cause_line(card)}")
        for item in card.missing_causes():
            how = (
                "envelope-nya terbaca; barisnya tetap tidak bisa disebut"
                if item.answered
                else "tidak ada envelope yang dibekukan"
            )
            lines.append(f"    HILANG {item.id} <- {item.tool}.{item.field} ({how})")
            lines.append(f"           {item.reason}")
    return lines


def render(
    early: Mapping[str, CardView],
    late: Mapping[str, CardView],
    symbols: Sequence[str] | None = None,
) -> list[str]:
    """Laporan dua tanggal: tanpa jam dinding, tanpa path DB; `decision_hash` apa adanya."""
    wanted = list(symbols) if symbols else [symbol for symbol in early if symbol in late]
    lines = [
        BANNER,
        "two-date roll call (spec §7 langkah 6): simbol sama, tanggal berbeda, params sama",
    ]
    for symbol in wanted:
        if symbol not in early or symbol not in late:
            raise TwoDatesError(f"{symbol} tidak ada di kedua tanggal")
        lines.append("")
        lines.extend(_render_symbol(symbol, early[symbol], late[symbol]))
    lines.append("")
    lines.append("kontribusi tiap tool (baris panggilan yang BISA DISEBUT / baris yang bersumber tool itu):")
    for symbol in wanted:
        for label, card in (
            (f"A {early[symbol].date}", early[symbol]),
            (f"B {late[symbol].date}", late[symbol]),
        ):
            parts = [f"{tool} {spoken}/{total}" for tool, (total, spoken) in card.tool_contribution().items()]
            lines.append(f"  {symbol:<6} {label}  " + "  ".join(parts))
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent.two_dates",
        description="Dua fixture bertanggal, simbol sama: kartu berdampingan (SIMULATED, offline).",
    )
    parser.add_argument("--early", default=str(FIXTURE_EARLY), help="fixture tanggal pertama")
    parser.add_argument("--late", default=str(FIXTURE_LATE), help="fixture tanggal kedua")
    parser.add_argument(
        "--symbol", action="append", default=None, help="batasi ke simbol ini (boleh berulang)"
    )
    parser.add_argument("--json", action="store_true", help="keluaran bentuk mesin")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        early, late = build_pair(args.early, args.late)
        symbols = args.symbol or [symbol for symbol in early if symbol in late]
        if args.json:
            payload = {
                "simulated": True,
                "cards": {
                    symbol: {"early": early[symbol].as_dict(), "late": late[symbol].as_dict()}
                    for symbol in symbols
                },
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        print("\n".join(render(early, late, symbols)))
        return 0
    except (TwoDatesError, DemoFixtureError, LedgerError, StoreError) as exc:
        print(f"two-dates: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
