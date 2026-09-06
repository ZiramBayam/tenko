"""Practice trade: buka posisi sesuai plan ATR, tutup dari harga perekam (TASKS 2.4).

SIMULASI SAJA. Tidak ada dana, dompet, order, atau eksekusi (docs/rules-compliance.md);
`core.store.Store.insert_trade_event` menulis `simulated=1` sebagai literal SQL yang tidak
punya parameter. Yang dicatat modul ini adalah **fakta pembukuan**: rencana yang dipakai, dan
apakah harga perekam kemudian menyentuh target, stop, atau batas waktu.

Angka plan datang UTUH dari `deep_analysis.data.trade_plan` (item 9), lewat item yang sudah
**dijangkarkan ke bukti beku** (`agent/anchor.py`). Nol aritmetika sendiri: `entry`, `stop`,
`targets[0]` dipakai apa adanya. Yang ditambahkan agen hanya ukuran nosional TETAP
(`agent.params.DEFAULT_SIZE_USD`) — spec §4 item 11 (Bunryō, penakaran) opsional dan tidak
dikerjakan.

**Angka plan yang dipakai MENUTUP posisi diturunkan ULANG dari snapshot beku**
(:func:`plan_from_frozen_evidence`), bukan dibaca dari `decisions.card_json`: kolom itu tidak
dilindungi hash apa pun, dan satu `UPDATE` atasnya sudah cukup untuk membuat exit dibukukan
pada harga yang mustahil sementara `make replay` tetap berkata IDENTICAL (temuan TINGGI
tinjauan keamanan, 4 Sep 2026).

Harga keluar: `analyze_token.data.market.price_usd` dari snapshot PEREKAM, dibaca
`core.store.Store.latest_fresh` dengan batas kesegaran yang sama dengan preflight
(`params.max_age_min`, 120 mnt). Agen tidak pernah memanggil tool RYO untuk mendapatkan harga.

**Seberapa sering harga itu ADA — angkanya, bukan kesan** (docs/api-facts.md §A "Harga —
`analyze_token.data.market.price_usd` — DIVERIFIKASI 4 Sep 2026 (TASKS 2.4)", sapuan
`id <= 1727`, jendela ≈64,7 jam):

* field-nya **929/929 terisi numerik > 0 (100 %)**, `availability.market_data` `available`
  929/929 — **harga null belum pernah teramati**, jadi cabang "harga null" di
  :func:`latest_price` menjaga bentuk yang belum pernah kami lihat, bukan kejadian rutin;
* yang benar-benar membuat harga tidak ada adalah **kesegaran**: jarak antar-baris per simbol
  inti median **62–63 mnt**, maksimum **1194 mnt (SOL/BTC)** dan **1455 mnt = 24,3 jam
  (ETH/BNB)**, dengan **5–7 celah > 120 mnt** per simbol dalam 2,7 hari;
* sehingga posisi terbuka menghadapi harga "tidak diketahui" **42–48 % waktu**
  (SOL/BTC 57,5 % · ETH 54,5 % · BNB 51,8 % waktu ADA harga ≤ 120 mnt).
* Penyebabnya **uptime perekam kami, bukan RYO** — dan `deep_analysis.data.market.price_usd`
  bukan pengganti yang lebih baik: 653 baris (vs 929), ditulis pada siklus yang SAMA sehingga
  tidak pernah lebih segar, dan pada 648 pasang sesiklus selisihnya median 0,0000 % / maks
  0,6057 % (verifikasi API 4 Sep 2026: "jangan ganti").

Konsekuensinya ditanggung terang-terangan: tidak ada harga segar → posisi **TETAP TERBUKA**,
sebabnya disebut per posisi, dan TIDAK diisi harga terakhir yang kebetulan ada. Itu berlaku
untuk `timeout` juga (lihat :func:`exit_reason` dan
`agent.params.DEFAULT_TIMEOUT_MIN` soal cabang yang tidak diambil).

Tiga jalan keluar saja (dipotong penjagaan ruang lingkup 3 Sep 2026): `target`, `stop`, `timeout`.
Tanpa exit parsial, tanpa trailing.

**Urutan pemeriksaan: `stop` DULU, baru `target`.** Satu snapshot memberi satu harga, bukan
lintasan; kalau harga yang terbaca berada di luar KEDUA batas, kita tidak tahu mana yang
tersentuh lebih dulu. Membaca yang merugikan lebih dulu adalah pembacaan yang tidak
menguntungkan diri sendiri — dan P&L memang tidak dinilai (rubric spec §8), yang dinilai adalah
kejujuran pembukuannya.

**Tidak ada P&L di jalur ini.** Baris `exit` mencatat harga dan alasan; selisihnya tidak
dihitung, tidak disimpan, dan tidak dicetak.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent.anchor import EvidenceMismatchError, recompute
from agent.ledger import DecisionRow, Position, open_positions, read_decision
from agent.params import DEFAULT_AGENT_PARAMS, AgentParams
from core.replay import ReplayError, decision_hash, freeze
from core.skill import ItemResult
from core.store import Store, StoreError, sha256_hex
from preflight.items import TOOL_ANALYZE
from preflight.params import ITEM_PLAN

log = logging.getLogger("agent.trade")

#: Field harga yang dipakai menutup posisi — nama PERSIS dari envelope RYO (api-facts §A).
PRICE_FIELD = "analyze_token.data.market.price_usd"

#: Kosakata alasan keluar. `kind` baris `trade_events` tetap "exit" (`core.store`); yang
#: membedakan ketiganya adalah kolom `reason`, yang diawali salah satu kata ini.
EXIT_TARGET = "target"
EXIT_STOP = "stop"
EXIT_TIMEOUT = "timeout"
EXIT_REASONS: frozenset[str] = frozenset({EXIT_TARGET, EXIT_STOP, EXIT_TIMEOUT})


class TradeError(Exception):
    """Plan tidak bisa dipakai membuka posisi (bug pemanggil, bukan bukti jelek)."""


@dataclass(frozen=True, slots=True)
class PracticePlan:
    """Rencana yang benar-benar dibukukan: angka RYO apa adanya + ukuran tetap kami."""

    symbol: str
    direction: str
    entry: float
    stop: float
    target: float
    size_usd: float
    snapshot_id: int | None = None
    as_of: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Bentuk untuk `card_json["agent"]["plan"]` — **bahan kartu saja**.

        Sejak 4 Sep 2026 tidak ada satu pun keputusan yang membacanya kembali: penutupan
        posisi menurunkan ulang plan dari snapshot beku (:func:`plan_from_frozen_evidence`),
        karena `card_json` tidak dilindungi hash apa pun.
        """
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "size_usd": self.size_usd,
            "source_snapshot_id": self.snapshot_id,
            "source_as_of": self.as_of,
            "source_field": "deep_analysis.data.trade_plan",
            "sizing": "fixed_notional",
            "simulated": True,
        }


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TradeError(f"{label} harus angka, bukan {type(value).__name__}")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise TradeError(f"{label} bukan angka finit: {value!r}")
    return number


def plan_from_items(
    symbol: str,
    items: Sequence[ItemResult],
    params: AgentParams = DEFAULT_AGENT_PARAMS,
) -> PracticePlan:
    """Susun :class:`PracticePlan` dari item 9 yang SUDAH dijangkarkan ke bukti beku.

    Masukannya sengaja `items` hasil `agent.anchor.check_against_evidence`, bukan
    `data["plan"]` envelope: envelope adalah yang diperiksa, bukti beku adalah yang dipercaya.

    Raises:
        TradeError: item 9 tidak ada/`unspoken`/tidak membawa angka yang bisa ditakar. Itu
            berarti pemanggil mencoba membuka posisi tanpa plan — GO tanpa item 9 yang hijau
            tidak mungkin terjadi (item 9 wajib), jadi ini bug pemanggil.
    """
    item = next((entry for entry in items if entry.id == ITEM_PLAN), None)
    if item is None or item.value is None:
        raise TradeError(f"{symbol}: item {ITEM_PLAN} tidak membawa trade_plan; tidak ada yang bisa dibuka")
    value = item.value
    if not isinstance(value, Mapping):
        raise TradeError(f"{symbol}: value item {ITEM_PLAN} bukan objek")
    targets = value.get("targets")
    if not isinstance(targets, list) or not targets:
        raise TradeError(f"{symbol}: trade_plan tanpa targets; tidak ada sasaran yang bisa dicatat")
    direction = value.get("direction")
    if direction not in ("long", "short"):
        raise TradeError(f"{symbol}: arah plan {direction!r} tidak terbaca")
    return PracticePlan(
        symbol=symbol,
        direction=direction,
        entry=_finite(value.get("entry"), "entry"),
        stop=_finite(value.get("stop"), "stop"),
        # Sasaran PERTAMA saja: tanpa exit parsial, target kedua tidak pernah dipakai.
        target=_finite(targets[0], "targets[0]"),
        size_usd=float(params.size_usd),
        snapshot_id=None if item.source is None else item.source.snapshot_id,
        as_of=None if item.source is None or item.source.as_of is None else item.source.as_of.isoformat(),
    )


def open_trade(
    store: Store,
    decision_id: int,
    plan: PracticePlan,
    *,
    ts_utc: str | None = None,
) -> int:
    """Catat satu baris `open` (`simulated=1`); kembalikan id barisnya.

    `price` = `entry` plan. Itu bukan klaim bahwa kami "dapat harga itu": tidak ada eksekusi
    sama sekali, dan yang dibukukan adalah rencananya sendiri berikut `snapshot_id` asalnya.
    """
    return store.insert_trade_event(
        decision_id=decision_id,
        symbol=plan.symbol,
        direction=plan.direction,
        kind="open",
        price=plan.entry,
        size_usd=plan.size_usd,
        reason=(
            f"plan entry {plan.entry:g} stop {plan.stop:g} target {plan.target:g} "
            f"(deep_analysis.data.trade_plan, snapshot_id={plan.snapshot_id}); simulated"
        ),
        ts_utc=ts_utc,
    )


@dataclass(frozen=True, slots=True)
class PriceRead:
    """Harga perekam untuk satu simbol + asalnya (tidak pernah angka tanpa sumber)."""

    price: float
    snapshot_id: int
    as_of: str | None
    ts_utc: str


def latest_price(
    store: Store,
    symbol: str,
    params: AgentParams = DEFAULT_AGENT_PARAMS,
    *,
    now: datetime | None = None,
) -> PriceRead | None:
    """Harga terakhir yang CUKUP SEGAR dari snapshot perekam, atau None.

    None berarti "perekam belum punya harga yang cukup baru", dan pemanggil WAJIB
    memperlakukannya sebagai "tidak tahu" — bukan sebagai harga 0, bukan sebagai harga lama.
    """
    args = {"symbol": symbol}
    snapshot = store.latest_fresh(TOOL_ANALYZE, sha256_hex(args), params.preflight.max_age_min, now=now)
    if snapshot is None:
        return None
    envelope = snapshot.payload
    if not isinstance(envelope, Mapping):
        return None
    availability = envelope.get("availability")
    if isinstance(availability, Mapping) and availability.get("market_data") != "available":
        return None
    data = envelope.get("data")
    market = data.get("market") if isinstance(data, Mapping) else None
    raw = market.get("price_usd") if isinstance(market, Mapping) else None
    try:
        price = _finite(raw, PRICE_FIELD)
    except TradeError:
        log.warning("%s: %s tidak terbaca di snapshot_id=%d", symbol, PRICE_FIELD, snapshot.id)
        return None
    if price <= 0:
        log.warning("%s: %s = %r pada snapshot_id=%d; tidak dipakai", symbol, PRICE_FIELD, raw, snapshot.id)
        return None
    return PriceRead(price=price, snapshot_id=snapshot.id, as_of=snapshot.as_of, ts_utc=snapshot.ts_utc)


def _parse_ts(raw: str | None) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def overdue_min(opened_at: str, *, now: datetime, timeout_min: int) -> float | None:
    """Berapa MENIT posisi sudah lewat jatuh tempo, atau None kalau belum/`opened_at` tak terbaca.

    Dipisah dari :func:`exit_reason` supaya keterlambatan bisa DIBUKUKAN, bukan hanya dipakai
    sebagai syarat: exit `timeout` yang tercatat 300 menit setelah jatuh temponya (karena
    perekam diam selama itu) harus terlihat sebagai exit yang terlambat 300 menit.
    """
    opened = _parse_ts(opened_at)
    if opened is None:
        return None
    late = (now - opened).total_seconds() / 60.0 - float(timeout_min)
    return late if late >= 0 else None


def exit_reason(
    plan: PracticePlan,
    price: PriceRead | None,
    opened_at: str,
    *,
    now: datetime,
    timeout_min: int,
) -> str | None:
    """Alasan keluar (`stop`/`target`/`timeout`), atau None kalau posisi tetap terbuka.

    Fungsi MURNI: seluruh masukannya eksplisit, termasuk `now`. `stop` diperiksa sebelum
    `target` (lihat docstring modul).

    **`timeout` pun hanya bisa dicatat bila ADA harga**, dan itu keputusan yang dipilih sadar,
    bukan kelalaian: `trade_events.price` adalah `REAL NOT NULL`, jadi menutup tanpa harga
    menuntut perubahan skema (bukan milik `agent/` — dilaporkan, tidak dikerjakan sendiri),
    sedangkan mengisinya dengan 0 atau dengan harga basi adalah persis "nilai default
    menggantikan `unspoken`" yang produk ini ada untuk mencegahnya. Akibatnya, pada celah data
    yang terukur (median 62–63 mnt, maksimum 1455 mnt; docs/api-facts.md §A "Harga …"), sebuah
    `timeout` bisa tercatat TERLAMBAT — dan seberapa terlambat ikut ditulis di `reason` baris
    `exit` lewat :func:`overdue_min`.
    """
    if price is not None:
        if plan.direction == "long":
            if price.price <= plan.stop:
                return EXIT_STOP
            if price.price >= plan.target:
                return EXIT_TARGET
        else:
            if price.price >= plan.stop:
                return EXIT_STOP
            if price.price <= plan.target:
                return EXIT_TARGET
        if overdue_min(opened_at, now=now, timeout_min=timeout_min) is not None:
            return EXIT_TIMEOUT
    return None


def plan_from_frozen_evidence(
    store: Store,
    row: DecisionRow,
    params: AgentParams = DEFAULT_AGENT_PARAMS,
) -> PracticePlan:
    """Turunkan ULANG plan sebuah keputusan dari snapshot bekunya — bukan dari `card_json`.

    **Kenapa bukan `card_json`** (temuan TINGGI tinjauan keamanan, 4 Sep 2026, dibuktikan
    dengan serangan yang berhasil): `decisions.card_json` TIDAK dilindungi hash apa pun —
    `core.replay.DECISION_FIELDS` sengaja hanya `decision`/`blocking`/`item_status`. Satu
    `UPDATE decisions SET card_json=…` tanpa menyentuh satu hash pun sudah cukup untuk
    mengganti `entry`/`stop`/`target`, sementara `make replay` tetap melaporkan IDENTICAL —
    dan tick berikutnya membukukan `exit` "target" pada harga yang mustahil. Prasyaratnya
    jauh lebih murah daripada batas yang diakui `core/replay.py` (di sana penyerang harus
    menulis ulang `payload_json` + `payload_hash` + `decision_hash` sekaligus).

    Karena itu jalur penutupan posisi memakai bukti yang DIJAGA HASH: `snapshot_ids` dibekukan
    ulang (`freeze` menghitung ulang `args_hash`/`payload_hash` tiap baris), item dinilai ulang
    (`agent.anchor.recompute`), `decision_hash` hasil penilaian ulang WAJIB sama dengan kolom
    `decisions.decision_hash` yang tersimpan, dan barulah item 9 dipakai menakar. `card_json`
    kini murni bahan kartu untuk UI — tidak ada satu pun keputusan yang bergantung padanya.

    Raises:
        TradeError: keputusan itu bukan GO, tanpa bukti, atau item 9-nya tidak bisa ditakar.
        core.replay.ReplayError: baris snapshot tidak cocok dengan hash-nya (menyebut id).
        agent.anchor.EvidenceMismatchError: bundle tidak konsisten.
    """
    snapshot_ids = row.snapshot_ids()
    if not snapshot_ids:
        raise TradeError(f"keputusan {row.id} tidak punya snapshot beku; tidak ada yang bisa diturunkan")
    bundle = freeze(store, snapshot_ids)
    recomputed = recompute(row.symbol, bundle, params.preflight)
    computed = decision_hash(bundle, params.preflight.as_dict(), recomputed.decision.as_material())
    if computed != row.decision_hash:
        raise TradeError(
            f"keputusan {row.id}: decision_hash tersimpan {row.decision_hash[:12]}... tidak sama "
            f"dengan yang dihitung ulang dari bukti beku {computed[:12]}..."
        )
    if recomputed.decision.decision != "GO":
        raise TradeError(
            f"keputusan {row.id} dinilai ulang menjadi {recomputed.decision.decision}; "
            "posisi ini tidak punya plan yang sah"
        )
    return plan_from_items(row.symbol, recomputed.items, params)


@dataclass(frozen=True, slots=True)
class Settlement:
    """Satu posisi yang ditutup tick ini (atau alasan mengapa ia tetap terbuka)."""

    decision_id: int
    symbol: str
    closed: bool
    reason: str
    price: float | None = None
    snapshot_id: int | None = None
    #: Posisi milik generasi `params_hash` lain (TASKS 2.4b): dilaporkan, tidak disentuh.
    stale_generation: bool = False


def settle_open_positions(
    store: Store,
    params: AgentParams = DEFAULT_AGENT_PARAMS,
    *,
    now: datetime | None = None,
) -> list[Settlement]:
    """Tutup posisi terbuka yang sudah kena target/stop/timeout menurut harga perekam.

    Membaca `trade_events` lewat `agent.ledger` (koneksi baca-saja) dan menulis HANYA baris
    `exit` baru lewat `core.store.Store`. Posisi yang keputusannya tidak punya plan terbaca di
    `card_json` dilewati dengan alasan tertulis — bukan ditutup dengan angka karangan.
    """
    moment = now if now is not None else datetime.now(UTC)
    out: list[Settlement] = []
    for position in open_positions(store.path):
        settlement = _settle_one(store, position, params, moment)
        out.append(settlement)
    return out


def _settle_one(store: Store, position: Position, params: AgentParams, now: datetime) -> Settlement:
    row = read_decision(store.path, position.decision_id)
    if row is None:
        return Settlement(
            position.decision_id,
            position.symbol,
            False,
            f"keputusan {position.decision_id} tidak ada di ledger; posisi dibiarkan terbuka",
        )
    if row.params_hash != params.decision_params_hash():
        # Ambang yang bergeser (mis. ADR-017) — BUKAN generasi algoritma: baris generasi lama
        # sudah tidak sampai ke sini karena `open_positions` tidak melipatnya (TASKS 2.4b,
        # `agent.ledger.fold_positions`). Yang tersisa di cabang ini adalah keputusan generasi
        # berjalan yang params-nya berubah setelah posisinya dibuka: disebut, tidak disentuh,
        # karena plan-nya tidak bisa diturunkan ulang dengan ambang sekarang.
        return Settlement(
            position.decision_id,
            position.symbol,
            False,
            f"keputusan {position.decision_id} memakai params_hash generasi lain "
            f"({row.params_hash[:12]}...); posisi dibiarkan terbuka, tidak ditutup dengan "
            "angka yang tidak bisa diturunkan ulang",
            stale_generation=True,
        )
    try:
        plan = plan_from_frozen_evidence(store, row, params)
    except (TradeError, StoreError, ReplayError, EvidenceMismatchError) as exc:
        # Bukti/keputusan tidak lagi bisa diturunkan → posisi TIDAK ditutup dengan angka yang
        # tidak bisa dipertanggungjawabkan. Dibiarkan terbuka, sebabnya ditulis.
        return Settlement(
            position.decision_id,
            position.symbol,
            False,
            f"plan keputusan {position.decision_id} tidak bisa diturunkan ulang dari bukti beku: {exc}",
        )
    if position.symbol != plan.symbol:
        return Settlement(
            position.decision_id,
            position.symbol,
            False,
            f"posisi {position.symbol} menunjuk keputusan bersimbol {plan.symbol}; tidak ditutup",
        )
    if position.entry_price != plan.entry:
        return Settlement(
            position.decision_id,
            position.symbol,
            False,
            f"entry baris open {position.entry_price:g} bukan entry plan {plan.entry:g} "
            "menurut bukti beku; posisi tidak ditutup",
        )
    price = latest_price(store, position.symbol, params, now=now)
    if price is None:
        late = overdue_min(position.opened_at, now=now, timeout_min=params.timeout_min)
        overdue = "" if late is None else f" (jatuh tempo sudah lewat {late:.0f} menit)"
        return Settlement(
            position.decision_id,
            position.symbol,
            False,
            f"tidak ada {PRICE_FIELD} yang lebih muda dari {params.preflight.max_age_min} menit; "
            f"posisi tetap terbuka{overdue}",
        )
    reason = exit_reason(
        plan,
        price,
        position.opened_at,
        now=now,
        timeout_min=params.timeout_min,
    )
    if reason is None:
        return Settlement(
            position.decision_id,
            position.symbol,
            False,
            f"harga {price.price:g} masih di antara stop {plan.stop:g} dan target {plan.target:g}",
            price=price.price,
            snapshot_id=price.snapshot_id,
        )
    # Keterlambatan DIBUKUKAN: pada celah harga yang terukur (docs/api-facts.md §A "Harga …":
    # median 62-63 mnt, maksimum 1455 mnt) sebuah exit bisa tercatat jauh setelah jatuh
    # temponya. Angkanya ditulis, bukan dibiarkan tampak seolah tepat waktu.
    late = overdue_min(position.opened_at, now=now, timeout_min=params.timeout_min)
    lateness = "" if late is None else f"; {late:.0f} min past the {params.timeout_min} min limit"
    store.insert_trade_event(
        decision_id=position.decision_id,
        symbol=position.symbol,
        direction=position.direction,
        kind="exit",
        price=price.price,
        size_usd=position.size_usd,
        reason=(
            f"{reason}: {PRICE_FIELD}={price.price:g} vs stop {plan.stop:g} / target "
            f"{plan.target:g} (snapshot_id={price.snapshot_id}{lateness}); simulated"
        ),
    )
    return Settlement(
        position.decision_id,
        position.symbol,
        True,
        reason,
        price=price.price,
        snapshot_id=price.snapshot_id,
    )
