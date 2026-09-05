"""Outcome satu keputusan berumur >=24 j, dibaca dari harga PEREKAM — TASKS 3.3, spec §6.

Spec §6 apa adanya: *"Untuk keputusan berumur >= 24 j: GO -> target tersentuh sebelum stop?;
NO_GO -> apakah plan yang ditolak akan kena stop?"* Modul ini menjawab pertanyaan itu dan tidak
lebih. Ia tidak menghitung P&L, tidak menandai untung/rugi dalam dolar, dan tidak punya konsep
"posisi": yang ada hanya satu pertanyaan biner per keputusan, dengan horizon yang selalu
tercetak di sebelah jawabannya.

Dari mana harganya
------------------
Satu-satunya seri harga di repo ini adalah ``analyze_token.data.market.price_usd`` yang direkam
``core.recorder`` (nol panggilan RYO dari modul ini; ia hanya membaca `data/ryo.db` baca-saja).
Seri itu dipilih dengan ``args_hash = sha256(canonical({"symbol": <simbol>}))`` — bentuk argumen
yang dipakai perekam — supaya baris simbol lain tidak pernah tercampur.

Empat batas yang membuat angka modul ini bisa dibantah (baca sebelum mengutipnya)
--------------------------------------------------------------------------------
* **Buta intrabar.** Perekam mengambil ~1 sampel/jam/simbol. Sentuhan stop atau target yang
  terjadi DI ANTARA dua sampel tidak terlihat. Arah biasnya satu: outcome condong ke
  ``open_at_horizon``, jadi "veto ini menyelamatkan sebuah kerugian" akan **kurang** dilaporkan,
  bukan dilebih-lebihkan. Jarak sampel terbesar di dalam jendela ikut dilaporkan
  (:attr:`Outcome.max_gap_min`) supaya besarnya kebutaan itu terlihat angkanya.
* **Harga = titik, bukan OHLC.** ``price_usd`` adalah harga saat sampel diambil; tidak ada
  high/low. Satu sampel karena itu hanya bisa menyentuh SATU sisi (stop < entry < target untuk
  long), jadi tidak ada aturan tie-break yang disembunyikan di sini.
* **Jendela yang belum selesai bukan "tidak kena stop".** Bila perekam berhenti sebelum
  `keputusan + horizon`, jawabannya ``window_not_covered`` dan amatan itu **dibuang dari n**,
  bukan dihitung sebagai "tidak rugi". Cakupan diuji dengan bertanya apakah seri simbol itu
  punya sampel PADA ATAU SETELAH horizon — bukan dengan toleransi longgar yang bisa disetel.
* **Ini simulasi.** Tidak ada dana, tidak ada eksekusi, tidak ada slippage/biaya. `entry` plan
  dianggap terisi persis pada saat keputusan; bila kenyataannya tidak terisi, seluruh outcome
  ini hipotetis — dan untuk NO_GO ia memang selalu hipotetis (trade-nya tidak pernah dibuka).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from core.store import Store, sha256_hex

__all__ = [
    "HORIZON_HOURS",
    "PRICE_FIELD",
    "PRICE_TOOL",
    "GradingDataError",
    "Outcome",
    "OutcomeStatus",
    "Plan",
    "PricePoint",
    "parse_ts",
    "plan_from_card",
    "price_series",
    "resolve_outcome",
    "series_args_hash",
]


class GradingDataError(Exception):
    """Bacaan store gagal atau dipakai dengan koneksi yang bisa menulis."""


#: Horizon spec §6. Konstanta modul ini sendiri; ia TIDAK ikut ke `preflight.params` dan karena
#: itu tidak bisa menggeser `params_hash` (yang wajib tetap `3d39fb1b…`, TASKS 5.0).
HORIZON_HOURS = 24

PRICE_TOOL = "analyze_token"
PRICE_FIELD = "data.market.price_usd"

#: Nilai `Outcome.status`. `open_at_horizon` = jendela LENGKAP dan tidak ada sisi yang tersentuh;
#: sisanya adalah ketiadaan data, dan ketiadaan data tidak pernah menjadi 0 (CLAUDE.md).
OutcomeStatus = str
STOP_FIRST: OutcomeStatus = "stop_first"
TARGET_FIRST: OutcomeStatus = "target_first"
OPEN_AT_HORIZON: OutcomeStatus = "open_at_horizon"
NO_PRICES: OutcomeStatus = "no_prices"
WINDOW_NOT_COVERED: OutcomeStatus = "window_not_covered"

#: Status yang boleh masuk `n`. `open_at_horizon` IKUT: spec §6 bertanya "apakah plan ini kena
#: stop [dalam horizon]", dan "tidak, ia masih terbuka" adalah jawaban yang teramati, bukan
#: data yang hilang. Membuangnya justru menciptakan bias seleksi ke arah jendela yang bergerak
#: cepat. Yang dibuang hanyalah dua status ketiadaan data di bawah.
RESOLVED_STATUSES: frozenset[str] = frozenset({STOP_FIRST, TARGET_FIRST, OPEN_AT_HORIZON})
#: Status ketiadaan data — dicacah terpisah, tidak pernah dilipat jadi "tidak rugi".
UNRESOLVED_STATUSES: frozenset[str] = frozenset({NO_PRICES, WINDOW_NOT_COVERED})


def parse_ts(raw: Any) -> datetime | None:
    """`ts_utc`/`as_of` kolom → datetime UTC; yang tidak terbaca jadi None (dicacah, bukan ditebak)."""
    if not isinstance(raw, str):
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def series_args_hash(symbol: str) -> str:
    """`args_hash` seri harga satu simbol — bentuk argumen yang dipakai perekam."""
    return sha256_hex({"symbol": symbol})


@dataclass(frozen=True, slots=True)
class PricePoint:
    ts: datetime
    price: float
    snapshot_id: int


@dataclass(frozen=True, slots=True)
class Plan:
    """`deep_analysis.data.trade_plan` seperti yang DIPAKAI keputusan itu, dari kartunya.

    Diambil dari `decisions.card_json` (item ``09_plan``), bukan dari snapshot hari ini: yang
    dinilai adalah plan yang benar-benar dihadapi keputusan itu. Membaca ulang snapshot akan
    menilai keputusan lama dengan angka baru.
    """

    direction: str
    entry: float
    stop: float
    target: float
    snapshot_id: int | None
    targets: tuple[float, ...] = ()


def _finite(value: Any) -> float | None:
    """float berhingga, atau None. `bool` ditolak: `True` bukan harga 1.0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _dig(payload: Any, path: Sequence[str]) -> Any:
    for key in path:
        if not isinstance(payload, Mapping):
            return None
        payload = payload.get(key)
    return payload


def plan_from_card(card: Any) -> Plan | None:
    """Plan dari `card_json` satu keputusan, atau None bila kartunya tidak memuatnya.

    None untuk SEMUA bentuk yang tidak lengkap — termasuk kartu generasi lama (id item berupa
    angka, sebelum TASKS 2.x) dan kartu ber-`items` kosong. Menambal yang hilang dengan default
    akan membuat keputusan tanpa plan tampak punya outcome.
    """
    skill = card.get("skill") if isinstance(card, Mapping) and "skill" in card else card
    items = _dig(skill, ("data", "items"))
    if not isinstance(items, list):
        return None
    value: Any = None
    for item in items:
        if isinstance(item, Mapping) and item.get("id") == "09_plan":
            value = item.get("value")
            source = item.get("source")
            snapshot_id = source.get("snapshot_id") if isinstance(source, Mapping) else None
            break
    else:
        return None
    if not isinstance(value, Mapping):
        return None
    entry = _finite(value.get("entry"))
    stop = _finite(value.get("stop"))
    raw_targets = value.get("targets")
    targets = (
        tuple(price for price in (_finite(one) for one in raw_targets) if price is not None)
        if isinstance(raw_targets, list)
        else ()
    )
    direction = value.get("direction")
    if entry is None or stop is None or not targets or not isinstance(direction, str):
        return None
    if direction not in ("long", "short"):
        return None
    # Plan yang stop/target-nya di sisi yang salah bukan plan: menilainya akan memberi outcome
    # yang bentuknya benar tetapi artinya terbalik.
    if direction == "long" and not (stop < entry < targets[0]):
        return None
    if direction == "short" and not (targets[0] < entry < stop):
        return None
    return Plan(
        direction=direction,
        entry=entry,
        stop=stop,
        target=targets[0],
        snapshot_id=(
            snapshot_id if isinstance(snapshot_id, int) and not isinstance(snapshot_id, bool) else None
        ),
        targets=targets,
    )


def price_series(store: Store, symbol: str) -> tuple[list[PricePoint], int]:
    """SELURUH seri harga satu simbol (menaik) + cacah baris yang tak terpakai.

    Dibaca sekali per simbol lalu dipakai ulang untuk setiap keputusan simbol itu: satu SELECT
    per simbol, bukan satu per keputusan.

    **Tidak lewat `Store.latest_fresh`, dan itu disengaja.**
    :data:`core.store.FRESH_SCAN_LIMIT` memindai hanya 64 baris terbaru per (tool, args_hash)
    tanpa batas SQL atas `ts_utc`; jalur harga di sini butuh SELURUH jendela ke belakang, jadi
    memakainya akan membuat keputusan yang lebih tua dari 64 siklus tampak tanpa harga —
    "bukti hilang" yang 100 % artefak batas pindai. Fungsi ini karena itu memakai `SELECT`
    sendiri yang dipagari (`SELECT` saja, koneksi `read_only`), dan konstanta itu TIDAK dinaikkan.

    Raises:
        GradingDataError: `store` bisa menulis. Alat ukur tidak boleh memegang koneksi yang
            bisa mengubah DB hidup (temuan SEDANG security-reviewer 2.1b): `Store._select`
            meneruskan SQL apa pun ke koneksinya.
    """
    if not getattr(store, "read_only", False):
        raise GradingDataError(
            "price_series() menuntut Store(read_only=True): grading mengukur, ia tidak menulis"
        )
    sql = "SELECT id, ts_utc, payload_json FROM snapshots WHERE tool = ? AND args_hash = ?"
    try:
        rows = store._select(sql, (PRICE_TOOL, series_args_hash(symbol)))
    except Exception as exc:  # noqa: BLE001 - StoreError & sqlite3.Error dibungkus satu tipe
        raise GradingDataError(f"gagal membaca seri harga {symbol!r}: {type(exc).__name__}") from None

    points: list[PricePoint] = []
    unusable = 0
    for snapshot_id, raw_ts, payload_json in rows:
        moment = parse_ts(raw_ts)
        if moment is None:
            unusable += 1
            continue
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            unusable += 1
            continue
        price = _finite(_dig(payload, ("data", "market", "price_usd")))
        if price is None or price <= 0:
            unusable += 1
            continue
        points.append(PricePoint(ts=moment, price=price, snapshot_id=int(snapshot_id)))
    points.sort(key=lambda point: (point.ts, point.snapshot_id))
    return points, unusable


@dataclass(frozen=True, slots=True)
class Outcome:
    """Jawaban spec §6 untuk satu keputusan + seluruh angka yang membuatnya bisa dibantah."""

    status: OutcomeStatus
    decided_at: datetime
    horizon_at: datetime
    samples: int
    max_gap_min: float | None
    touched_at: datetime | None = None
    touched_price: float | None = None
    touched_snapshot_id: int | None = None
    note: str = ""

    @property
    def resolved(self) -> bool:
        return self.status in RESOLVED_STATUSES

    @property
    def would_have_lost(self) -> bool | None:
        """Apakah plan ini akan kena stop dalam horizon? None bila tidak terjawab.

        None bukan False. Itu perbedaan antara "tidak, harganya tidak pernah ke sana" dan
        "kami tidak tahu karena harganya tidak terekam".
        """
        if not self.resolved:
            return None
        return self.status == STOP_FIRST

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "decided_at": self.decided_at.isoformat().replace("+00:00", "Z"),
            "horizon_at": self.horizon_at.isoformat().replace("+00:00", "Z"),
            "samples": self.samples,
            "max_gap_min": self.max_gap_min,
            "touched_at": (
                None if self.touched_at is None else self.touched_at.isoformat().replace("+00:00", "Z")
            ),
            "touched_price": self.touched_price,
            "touched_snapshot_id": self.touched_snapshot_id,
            "would_have_lost": self.would_have_lost,
            "note": self.note,
            "simulated": True,
        }


def resolve_outcome(
    plan: Plan,
    points: Sequence[PricePoint],
    decided_at: datetime,
    *,
    horizon_hours: int = HORIZON_HOURS,
) -> Outcome:
    """Sisi mana yang tersentuh lebih dulu dalam `(decided_at, decided_at + horizon]`.

    Sampel PADA `decided_at` sendiri tidak ikut (batas kiri terbuka): harga pada detik keputusan
    adalah harga yang sudah dilihat keputusan itu, bukan masa depannya.
    """
    horizon_at = decided_at + timedelta(hours=horizon_hours)
    window = [point for point in points if decided_at < point.ts <= horizon_at]
    # Cakupan: perekam harus punya sampel PADA ATAU SETELAH horizon. Tanpa syarat ini, keputusan
    # yang dibuat 2 jam lalu akan terbaca "tidak kena stop dalam 24 jam".
    covered = any(point.ts >= horizon_at for point in points)

    gaps: list[float] = []
    previous = decided_at
    for point in window:
        gaps.append((point.ts - previous).total_seconds() / 60.0)
        previous = point.ts
    if covered:
        gaps.append((horizon_at - previous).total_seconds() / 60.0)
    max_gap = round(max(gaps), 1) if gaps else None

    for point in window:
        if plan.direction == "long":
            hit_stop = point.price <= plan.stop
            hit_target = point.price >= plan.target
        else:
            hit_stop = point.price >= plan.stop
            hit_target = point.price <= plan.target
        if hit_stop or hit_target:
            # Satu harga titik hanya bisa berada di satu sisi (dijamin plan_from_card:
            # stop < entry < target untuk long, terbalik untuk short).
            return Outcome(
                status=STOP_FIRST if hit_stop else TARGET_FIRST,
                decided_at=decided_at,
                horizon_at=horizon_at,
                samples=len(window),
                max_gap_min=max_gap,
                touched_at=point.ts,
                touched_price=point.price,
                touched_snapshot_id=point.snapshot_id,
                note=(
                    f"harga {point.price:g} vs stop {plan.stop:g} / target {plan.target:g} ({plan.direction})"
                ),
            )

    if not window:
        status = NO_PRICES
        note = "tidak ada satu pun sampel harga di dalam jendela"
    elif not covered:
        status = WINDOW_NOT_COVERED
        note = "perekam berhenti sebelum horizon: jendelanya belum selesai, bukan 'tidak kena stop'"
    else:
        status = OPEN_AT_HORIZON
        note = f"{len(window)} sampel, tidak ada yang menyentuh stop maupun target"
    if status == NO_PRICES and not covered:
        note = "tidak ada sampel di dalam jendela DAN perekam berhenti sebelum horizon"
    return Outcome(
        status=status,
        decided_at=decided_at,
        horizon_at=horizon_at,
        samples=len(window),
        max_gap_min=max_gap,
        note=note,
    )


@dataclass(slots=True)
class SeriesCache:
    """Seri harga per simbol, dibaca sekali. Satu SELECT per simbol per laporan."""

    store: Store
    _series: dict[str, tuple[list[PricePoint], int]] = field(default_factory=dict)

    def get(self, symbol: str) -> tuple[list[PricePoint], int]:
        cached = self._series.get(symbol)
        if cached is None:
            cached = price_series(self.store, symbol)
            self._series[symbol] = cached
        return cached

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._series))
