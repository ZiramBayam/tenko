"""Reputasi item dari outcome nyata — TASKS 3.3, spec §6. Alat UKUR, baca-saja.

Menjawab pertanyaan juri yang paling tajam: *veto kalian menahan sesuatu — tapi apakah
menahannya BENAR?* `preflight/veto_table.py` (TASKS 2.1b) hanya bisa mengatakan item mana yang
pernah menahan; modul ini bertanya apakah menahan itu membayar, dengan kalimat spec §6:
**"item ini menahan k dari n trade yang akan kena stop dalam 24 jam"** — dan menolak mencetak
selang kepercayaan sebelum `n` efektif mencapai :data:`grading.stats.MIN_N` (30).

Nol pengaruh ke jalur keputusan (ADR-003)
-----------------------------------------
Modul ini mengimpor `preflight` hanya untuk daftar id item dan `params_hash`; arah impornya
satu arah dan tidak ada satu baris pun di `preflight/`/`agent/` yang mengimpor `grading`.
Ambang di sini (horizon 24 j, `MIN_N`) adalah milik `grading` sendiri dan **tidak** ikut ke
`params_hash` — yang wajib tetap ``3d39fb1b…`` (params beku, TASKS 5.0).

Unit amatan, dan kenapa bukan cacah baris
-----------------------------------------
Satu **episode veto** = satu ``(item, simbol, snapshot bukti item itu, plan)``. Dua baris
`decisions` yang menunjuk snapshot bukti dan plan yang sama untuk simbol yang sama adalah satu
peristiwa yang tercatat dua kali, bukan dua percobaan — dan itu bukan hipotesis: `data/ryo.db`
memuat tiga tick agen dalam rentang satu detik (id 28–39) yang membaca snapshot yang sama persis.
Di atas itu, `preflight.skill.tool_args` memberi ``{}`` untuk ``market_overview`` dan
``monitor_market_sentiment_shift``, sehingga keempat simbol inti membaca BARIS yang sama dan
item 3/4/8 mencacah satu peristiwa pasar sampai 4x. Karena itu tiap baris tabel membawa tiga
angka sekaligus — `k`, `n` (episode), dan `peristiwa` (snapshot bukti berbeda) — dan gerbang 30
berlaku atas ``min(n, peristiwa)`` (:mod:`grading.stats`). Bila hasilnya "belum cukup data",
itu jawaban yang SAH.

Yang sengaja dibuang, dengan cacahnya dicetak
---------------------------------------------
* Keputusan ber-`params_hash` **selain** yang beku hari ini. Veto di bawah ambang lain adalah
  aturan lain; mencampurnya membuat satu id item berarti dua hal. Dibuang, dicacah per generasi.
* Keputusan yang belum berumur `horizon` penuh pada `--until`.
* Keputusan yang kartunya tidak memuat plan yang lengkap (termasuk kartu generasi lama).
* Outcome ``no_prices``/``window_not_covered`` — ketiadaan data, tidak pernah dilipat jadi
  "tidak rugi".

Reproduksi (batas `--until` supaya angkanya bisa diulang meski perekam hidup)::

    uv run python -m grading.grader --until 2026-09-06T00:00:00Z
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core.skill import TEXT_LIMIT, clean_text
from core.store import FRESH_SCAN_LIMIT, Store, StoreError
from grading.outcomes import (
    HORIZON_HOURS,
    UNRESOLVED_STATUSES,
    GradingDataError,
    Outcome,
    Plan,
    SeriesCache,
    parse_ts,
    plan_from_card,
    resolve_outcome,
)
from grading.stats import MIN_N, ObservedRate, observed_rate
from preflight.items import ITEMS
from preflight.params import DEFAULT_PARAMS, params_hash

__all__ = ["Report", "GradingError", "grade", "main", "render"]


class GradingError(Exception):
    """Laporan tidak bisa dirakit (store tidak terbaca / argumen tidak masuk akal)."""


#: Kelas Bunryo (spec §4 item 11) bila belum ada cukup data — dicetak apa adanya.
NOT_ENOUGH = "belum cukup data"


def _safe(text: Any) -> str:
    """Teks yang berasal dari DB/argv lewat satu-satunya sanitizer repo (`core.skill`).

    `symbol`, `regime`, `stance`, dan path DB semuanya teks pihak lain: satu escape ANSI di
    dalamnya bisa memindahkan kursor dan menimpa baris tabel yang alat ini sendiri hitung.
    """
    return clean_text(text if isinstance(text, str) else repr(text), TEXT_LIMIT)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Episode:
    """Satu episode veto: item ini menahan plan ini, untuk simbol ini, atas bukti ini."""

    item_id: str
    symbol: str
    evidence_key: str
    plan_key: str
    outcome: Outcome
    decision_ids: tuple[int, ...]


@dataclass(slots=True)
class ItemTally:
    item_id: str
    episodes: list[Episode] = field(default_factory=list)
    dropped: Counter[str] = field(default_factory=Counter)

    @property
    def resolved(self) -> list[Episode]:
        return [episode for episode in self.episodes if episode.outcome.resolved]

    @property
    def rate(self) -> ObservedRate:
        resolved = self.resolved
        stops = sum(1 for episode in resolved if episode.outcome.would_have_lost)
        events = len({episode.evidence_key for episode in resolved})
        return observed_rate(stops, len(resolved), events)

    @property
    def composition(self) -> Counter[str]:
        return Counter(episode.outcome.status for episode in self.episodes)


@dataclass(slots=True)
class BunryoCell:
    """Satu sel takaran Bunryo: (kelas verdict item 7) x (rezim item 3)."""

    stance: str
    regime: str
    episodes: list[Episode] = field(default_factory=list)

    @property
    def rate(self) -> ObservedRate:
        resolved = [episode for episode in self.episodes if episode.outcome.resolved]
        stops = sum(1 for episode in resolved if episode.outcome.would_have_lost)
        events = len({episode.evidence_key for episode in resolved})
        return observed_rate(stops, len(resolved), events)


@dataclass(slots=True)
class Report:
    db_path: Path
    until: datetime
    horizon_hours: int
    frozen_params_hash: str
    decisions_total: int = 0
    decisions_graded: int = 0
    dropped: Counter[str] = field(default_factory=Counter)
    dropped_params: Counter[str] = field(default_factory=Counter)
    tallies: dict[str, ItemTally] = field(default_factory=dict)
    bunryo: dict[tuple[str, str], BunryoCell] = field(default_factory=dict)
    go_outcomes: Counter[str] = field(default_factory=Counter)
    nogo_outcomes: Counter[str] = field(default_factory=Counter)
    symbols: Counter[str] = field(default_factory=Counter)
    price_rows_unusable: int = 0
    price_samples: dict[str, int] = field(default_factory=dict)
    window_first: str | None = None
    window_last: str | None = None
    max_gap_min: float | None = None
    deepest_price_scan: int = 0
    deepest_price_scan_at: str | None = None


def _card_items(card: Any) -> dict[str, Mapping[str, Any]]:
    skill = card.get("skill") if isinstance(card, Mapping) and "skill" in card else card
    items = skill.get("data", {}).get("items") if isinstance(skill, Mapping) else None
    if not isinstance(items, list):
        return {}
    return {
        item["id"]: item for item in items if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }


def _evidence_key(item: Mapping[str, Any] | None, snapshot_ids: Sequence[Any]) -> str:
    """Identitas bukti yang dipakai item ini pada keputusan ini.

    Item yang menunjuk satu snapshot memakai id itu. Item agregat (1 & 2 membaca SELURUH
    envelope dan tidak menunjuk satu pun) memakai seluruh himpunan `snapshot_ids` keputusan —
    itu memang buktinya, dan memberinya kunci konstan akan melipat semua siklus jadi satu
    peristiwa palsu.
    """
    source = item.get("source") if isinstance(item, Mapping) else None
    snapshot_id = source.get("snapshot_id") if isinstance(source, Mapping) else None
    if isinstance(snapshot_id, int) and not isinstance(snapshot_id, bool):
        return f"snapshot:{snapshot_id}"
    ids = sorted(str(one) for one in snapshot_ids)
    return "bundle:" + ",".join(ids)


def _plan_key(plan: Plan) -> str:
    return f"{plan.direction}|{plan.entry!r}|{plan.stop!r}|{plan.target!r}"


def _stance(item: Mapping[str, Any] | None, key: str) -> str:
    """`value[key]` sebagai teks bersih, atau "unspoken" bila tidak ada.

    "unspoken" adalah kelas Bunryo yang SAH dan berbeda dari kelas mana pun — bukan default yang
    menyamar sebagai nilai (CLAUDE.md: null tidak pernah jadi 0).
    """
    value = item.get("value") if isinstance(item, Mapping) else None
    raw = value.get(key) if isinstance(value, Mapping) else None
    return _safe(raw) if isinstance(raw, str) and raw else "unspoken"


def grade(
    store: Store,
    *,
    until: datetime,
    horizon_hours: int = HORIZON_HOURS,
) -> Report:
    """Sapu `decisions`, resolusikan outcome tiap keputusan, cacah per item. Fungsi murni-baca.

    Idempoten: dua panggilan atas `--until` yang sama dan DB yang sama memberi laporan yang sama
    (tidak ada state, tidak ada tulisan, tidak ada `datetime.now()` di jalur perhitungan —
    `until` selalu dioper pemanggil).

    Raises:
        GradingError: `store` bisa menulis, atau barisnya tidak terbaca.
    """
    if not getattr(store, "read_only", False):
        raise GradingError(
            "grade() menuntut Store(read_only=True): grading mengukur outcome, ia tidak menulis"
        )
    frozen = params_hash(DEFAULT_PARAMS)
    report = Report(db_path=store.path, until=until, horizon_hours=horizon_hours, frozen_params_hash=frozen)
    report.tallies = {item.id: ItemTally(item_id=item.id) for item in ITEMS}

    try:
        rows = store._select(
            "SELECT id, ts_utc, symbol, decision, blocking_json, snapshot_ids_json, "
            "params_hash, card_json FROM decisions ORDER BY id"
        )
    except StoreError as exc:
        raise GradingError(f"gagal membaca decisions: {exc}") from None

    report.decisions_total = len(rows)
    cache = SeriesCache(store=store)
    seen: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    pending: dict[tuple[str, str, str, str], Episode] = {}
    horizon = timedelta(hours=horizon_hours)
    gaps: list[float] = []
    first_at: datetime | None = None
    last_at: datetime | None = None

    for row in rows:
        decision_id, raw_ts, symbol, decision, blocking_json, ids_json, row_params, card_json = row
        decided_at = parse_ts(raw_ts)
        if decided_at is None:
            report.dropped["ts_utc tak terbaca"] += 1
            continue
        if decided_at + horizon > until:
            report.dropped[f"belum berumur {horizon_hours} j pada --until"] += 1
            continue
        if row_params != frozen:
            report.dropped["params_hash bukan generasi beku"] += 1
            report.dropped_params[_safe(str(row_params))[:16]] += 1
            continue
        try:
            card = json.loads(card_json)
            blocking = json.loads(blocking_json)
            snapshot_ids = json.loads(ids_json)
        except (TypeError, ValueError):
            report.dropped["kartu/blocking/snapshot_ids bukan JSON terbaca"] += 1
            continue
        if not isinstance(blocking, list) or not isinstance(snapshot_ids, list):
            report.dropped["kartu/blocking/snapshot_ids bukan JSON terbaca"] += 1
            continue
        plan = plan_from_card(card)
        if plan is None:
            report.dropped["kartu tanpa plan lengkap (item 09)"] += 1
            continue

        symbol_text = _safe(str(symbol))
        points, unusable = cache.get(str(symbol))
        report.price_rows_unusable += unusable
        report.price_samples[symbol_text] = len(points)
        outcome = resolve_outcome(plan, points, decided_at, horizon_hours=horizon_hours)
        # Jarak ke `FRESH_SCAN_LIMIT`, dicetak walau jalur ini TIDAK memakainya (lihat
        # `price_series`): angkanya memberi tahu pembaca seberapa jauh ke belakang laporan ini
        # menjangkau dibanding sejauh mana `latest_fresh` masih akan menemukan buktinya.
        depth = 1 + sum(1 for point in points if point.ts > decided_at)
        if depth > report.deepest_price_scan:
            report.deepest_price_scan = depth
            report.deepest_price_scan_at = f"{symbol_text} @ {_iso(decided_at)}"

        report.decisions_graded += 1
        report.symbols[symbol_text] += 1
        bucket = report.go_outcomes if decision == "GO" else report.nogo_outcomes
        bucket[outcome.status] += 1
        if outcome.max_gap_min is not None:
            gaps.append(outcome.max_gap_min)
        first_at = decided_at if first_at is None else min(first_at, decided_at)
        last_at = decided_at if last_at is None else max(last_at, decided_at)

        items = _card_items(card)
        plan_key = _plan_key(plan)
        # Episode item hanya lahir dari keputusan NO_GO: "menahan" hanya punya arti bila
        # keberangkatan benar-benar dibatalkan. Item merah pada kartu GO tidak mungkin ada
        # (GO menuntut semua item wajib hijau), tetapi syaratnya ditulis, bukan diandaikan.
        if decision != "NO_GO":
            continue
        for item_id in blocking:
            if not isinstance(item_id, str) or item_id not in report.tallies:
                report.dropped["id item di blocking tidak dikenal"] += 1
                continue
            key = (item_id, symbol_text, _evidence_key(items.get(item_id), snapshot_ids), plan_key)
            seen[key].append(int(decision_id))
            if key not in pending:
                pending[key] = Episode(
                    item_id=item_id,
                    symbol=symbol_text,
                    evidence_key=key[2],
                    plan_key=plan_key,
                    outcome=outcome,
                    decision_ids=(),
                )

        # Bunryo (spec §4 item 11): kelas verdict dalam (item 7) x rezim (item 3).
        stance = _stance(items.get("07_verdict_deep"), "stance")
        regime = _stance(items.get("03_regime"), "regime")
        cell = report.bunryo.setdefault((stance, regime), BunryoCell(stance=stance, regime=regime))
        cell.episodes.append(
            Episode(
                item_id="11_bunryo",
                symbol=symbol_text,
                evidence_key=_evidence_key(items.get("07_verdict_deep"), snapshot_ids),
                plan_key=plan_key,
                outcome=outcome,
                decision_ids=(int(decision_id),),
            )
        )

    for key, episode in pending.items():
        report.tallies[key[0]].episodes.append(
            Episode(
                item_id=episode.item_id,
                symbol=episode.symbol,
                evidence_key=episode.evidence_key,
                plan_key=episode.plan_key,
                outcome=episode.outcome,
                decision_ids=tuple(sorted(seen[key])),
            )
        )
    for tally in report.tallies.values():
        for episode in tally.episodes:
            if episode.outcome.status in UNRESOLVED_STATUSES:
                tally.dropped[episode.outcome.status] += 1

    report.max_gap_min = max(gaps) if gaps else None
    report.window_first = _iso(first_at) if first_at else None
    report.window_last = _iso(last_at) if last_at else None
    return report


def render(report: Report) -> str:
    """Header provenance + tabel per item + Bunryo + kalimat AC. Tidak ada angka tanpa `n`."""
    lines: list[str] = []
    add = lines.append
    add("reputasi item dari outcome nyata — TASKS 3.3 (alat ukur, bukan alat putus) — SIMULATED")
    add("")
    add("provenance")
    add(f"  --until               : {_iso(report.until)}")
    add(f"  db                    : {_safe(str(report.db_path))} (read_only=True, PRAGMA query_only=ON)")
    add(f"  horizon outcome       : {report.horizon_hours} j (spec §6)")
    add(f"  params_hash beku      : {report.frozen_params_hash}")
    add(f"  keputusan             : {report.decisions_graded} dinilai dari {report.decisions_total} baris")
    for reason, count in sorted(report.dropped.items(), key=lambda pair: (-pair[1], pair[0])):
        add(f"    dibuang: {count:>4}x {reason}")
    if report.dropped_params:
        generations = ", ".join(
            f"{prefix}…={count}" for prefix, count in sorted(report.dropped_params.items())
        )
        add(f"    generasi params yang dibuang: {generations}")
    add(f"  jendela keputusan     : {report.window_first or '-'} .. {report.window_last or '-'}")
    per_symbol = ", ".join(f"{name}={count}" for name, count in sorted(report.symbols.items()))
    add(f"  keputusan per simbol  : {per_symbol or '-'}")
    samples = ", ".join(f"{name}={count}" for name, count in sorted(report.price_samples.items()))
    add(f"  seri harga            : analyze_token data.market.price_usd; sampel {samples or '-'}")
    add(f"    baris harga tak terpakai: {report.price_rows_unusable}")
    add(
        f"    kedalaman pindai harga  : terdalam {report.deepest_price_scan} dari batas "
        f"{FRESH_SCAN_LIMIT} (core.store.FRESH_SCAN_LIMIT)"
        + (f", di {report.deepest_price_scan_at}" if report.deepest_price_scan_at else "")
        + "; jalur harga TIDAK lewat latest_fresh, jadi batas itu tidak menggigit di sini —"
    )
    add(
        "      angkanya dicetak supaya terlihat seberapa jauh laporan ini menjangkau ke belakang "
        "dibanding sejauh mana `latest_fresh` masih menemukan bukti."
    )
    add(
        f"    jarak sampel terbesar di dalam jendela: "
        f"{'-' if report.max_gap_min is None else f'{report.max_gap_min:.1f} mnt'} "
        "(sentuhan di antara dua sampel TIDAK terlihat)"
    )
    add(f"  outcome NO_GO         : {_counter(report.nogo_outcomes)}")
    add(
        f"  outcome GO            : {_counter(report.go_outcomes)}"
        + ("" if report.go_outcomes else "  (nol keputusan GO di dalam himpunan yang dinilai)")
    )
    add("")

    add(f'per item — "item ini menahan k dari n trade yang akan kena stop dalam {report.horizon_hours} j"')
    add(f"  {'item':<20} {'k':>3} {'n':>4} {'peristiwa':>10} {'n_efektif':>10}  komposisi / vonis")
    for item in ITEMS:
        tally = report.tallies[item.id]
        rate = tally.rate
        if not tally.episodes:
            add(
                f"  {item.id:<20} {'-':>3} {'-':>4} {'-':>10} {'-':>10}  "
                "belum pernah menahan satu pun keputusan yang bisa dinilai"
            )
            continue
        composition = _counter(tally.composition)
        verdict = (
            f"CI95 {rate.interval.low:.3f}..{rate.interval.high:.3f}"
            if rate.enough and rate.interval is not None
            else f"{NOT_ENOUGH} (ambang {MIN_N})"
        )
        add(
            f"  {item.id:<20} {rate.k:>3} {rate.n:>4} {rate.events:>10} {rate.n_effective:>10}  "
            f"{composition} -> {verdict}"
        )
    add("")

    add(f"takaran Bunryo (spec §4 item 11) — kelas verdict dalam x rezim, ambang n>={MIN_N}")
    if not report.bunryo:
        add("  (tidak ada keputusan yang bisa dikelaskan)")
    for (stance, regime), cell in sorted(report.bunryo.items()):
        rate = cell.rate
        verdict = (
            f"ukuran boleh dihitung (CI95 {rate.interval.low:.3f}..{rate.interval.high:.3f})"
            if rate.enough and rate.interval is not None
            else f"{NOT_ENOUGH} -> TIDAK ada takaran yang dikeluarkan"
        )
        add(
            f"  {stance:<12} x {regime:<12} k={rate.k} n={rate.n} "
            f"peristiwa={rate.events} n_efektif={rate.n_effective}  {verdict}"
        )
    add("")

    add("kalimat per item (spec §6)")
    spoke = False
    for item in ITEMS:
        tally = report.tallies[item.id]
        if not tally.episodes:
            continue
        spoke = True
        add(f"  - {tally.rate.sentence(item.id)}")
        if tally.dropped:
            add(f"    (+ {_counter(tally.dropped)} dibuang: ketiadaan data, tidak dilipat jadi 'tidak rugi')")
    if not spoke:
        add("  - (tidak ada item yang pernah menahan satu pun keputusan yang bisa dinilai)")
    add("")
    add(
        "catatan: n = episode veto (satu item x simbol x snapshot bukti x plan), BUKAN cacah "
        "baris `decisions`; 'peristiwa' = snapshot bukti berbeda. Gerbang 30 berlaku atas "
        "min(n, peristiwa). Semua angka SIMULATED: nol dana, nol eksekusi, nol P&L."
    )
    return "\n".join(lines)


def _counter(counter: Mapping[str, int]) -> str:
    if not counter:
        return "-"
    return ", ".join(f"{key}={counter[key]}" for key in sorted(counter))


# --- CLI ----------------------------------------------------------------------------------
def _parse_until(raw: str) -> datetime:
    text = raw.strip().replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m grading.grader",
        description="Reputasi item dari outcome keputusan >=24 j (TASKS 3.3). Baca-saja.",
    )
    parser.add_argument("--until", default=None, help="batas atas keputusan (ISO UTC); default: sekarang")
    parser.add_argument("--db", default=None, help="path DB; default: DB_PATH / data/ryo.db")
    parser.add_argument(
        "--horizon-hours", type=int, default=HORIZON_HOURS, help=f"horizon outcome; default {HORIZON_HOURS}"
    )
    args = parser.parse_args(argv)

    if args.until is None:
        until = datetime.now(UTC)
    else:
        try:
            until = _parse_until(args.until)
        except ValueError:
            print(f"--until bukan waktu ISO yang terbaca: {args.until!r}", file=sys.stderr)
            return 2
    if args.horizon_hours <= 0:
        print("--horizon-hours harus > 0", file=sys.stderr)
        return 2

    try:
        store = Store(args.db, read_only=True)
    except StoreError as exc:
        print(f"tidak bisa membuka store baca-saja: {exc}", file=sys.stderr)
        return 2
    try:
        report = grade(store, until=until, horizon_hours=args.horizon_hours)
    except (GradingError, GradingDataError, StoreError) as exc:
        print(f"gagal merakit laporan: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()
    print(render(report))
    return 0


if __name__ == "__main__":  # pragma: no cover - dijalankan lewat `python -m`
    sys.exit(main())
