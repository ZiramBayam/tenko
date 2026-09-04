"""Agen practice-trade Tenko — `python -m agent.main --once` (TASKS 2.4 + 2.0b-lite + 2.4c-min).

SIMULASI SAJA: tidak ada dana, dompet, order, atau eksekusi. Setiap baris yang ditulis modul
ini membawa `simulated=1` (literal SQL di `core/store.py`, bukan parameter).

Satu tick = untuk tiap simbol watchlist: `preflight(symbol)` → **GO**: satu practice trade
sesuai plan ATR RYO (entry/stop/target, ukuran TETAP) · **NO_GO**: satu baris `decisions`
ber-`decision="NO_GO"` berikut `blocking`-nya. Sebelum itu, posisi yang masih terbuka ditutup
bila harga perekam sudah menyentuh target/stop/batas waktu (`agent/trade.py`).

**Penyimpangan dari spec §5 yang disengaja (TASKS 2.0b-lite):** agen ini **store-first murni
dan tidak pernah memanggil satu tool RYO pun** — ia tidak mengonstruksi klien live, tidak
mengimpor `core.ryo_client`, dan tidak memegang `core.livelock` — sehingga bukti yang tidak
segar menjadi item `unspoken` → `NO_GO` ber-`reason` jujur ("tidak ada snapshot dalam N menit
di store"), **bukan** "kuota dipegang perekam": tidak ada yang memegang apa pun, karena tidak
ada pemanggil kedua. Dasarnya usul **ADR-018** (product-manager 3 Sep 2026, **status: USULAN —
menunggu persetujuan manusia**): pemanggil live kedua atas satu token bucket 60/menit adalah
risiko tanpa imbalan menjelang kirim, sementara preflight (TASKS 2.2) sudah store-first dan
demo §7 berbasis fixture. `core.livelock.try_acquire` karena itu **tetap ada tanpa pemanggil
produksi**; itu disengaja, bukan kode mati yang terlupa.

Tiga penjaga dijalankan berurutan sebelum satu baris pun ditulis, dan urutannya penting:

1. `preflight.skill.check_aggregate_floor` — "envelope ini konsisten dengan itemnya sendiri?"
2. `agent.anchor.check_against_evidence` — "itemnya adalah apa yang dikatakan bukti beku?"
   (TASKS 2.4c-min; menutup serangan 6 & 8 security-reviewer atas 2.2). Dipanggil **sebelum**
   baris `decisions` ditulis dan **sebelum** posisi ditakar dari `plan`.
3. `core.replay.decision_hash` dihitung ULANG dari bundle + keputusan yang sudah dijangkarkan,
   lalu disamakan dengan `data["decision_hash"]` envelope. Yang disimpan adalah hash yang
   dihitung agen sendiri, bukan hash yang diberikan kepadanya.

Ketidakcocokan di penjaga mana pun = **tidak ada baris yang ditulis untuk simbol itu**, satu
baris laporan yang menyebut item + `snapshot_id`, dan kode keluar ≠ 0 pada mode `--once`.

Determinisme (ADR-003): keputusan adalah fungsi murni atas (bukti beku, params). Tidak ada
`random`, tidak ada LLM, dan satu-satunya jam di jalur ini adalah jam RESOLUSI kesegaran store
(`Store.latest_fresh`) — jam yang sama yang sudah dipakai `preflight`, dan yang bisa
disuntikkan lewat `now=` untuk tes. `make replay ID=<n>` menghitung ulang seluruh keputusan
dari snapshot beku dan wajib melaporkan IDENTICAL.

**Tidak ada P&L di keluaran utama** (rubric spec §8 tidak menilai P&L): yang dilaporkan adalah
keputusan, item penahannya, dan fakta apakah plan kena target/stop/timeout.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent.anchor import EvidenceMismatchError, check_against_evidence
from agent.ledger import LedgerError, read_positions
from agent.params import DEFAULT_AGENT_PARAMS, AgentParams
from agent.trade import PracticePlan, TradeError, open_trade, plan_from_items, settle_open_positions
from agent.watchlist import watchlist
from core.replay import ALGO, Bundle, ReplayError, decision_hash, freeze
from core.skill import SkillResult
from core.store import Store, StoreError
from preflight.skill import (
    DEFAULT_INTENT,
    check_aggregate_floor,
    preflight,
    symbol_evidence,
    symbol_tools,
)

log = logging.getLogger("agent.main")

#: Banner kepatuhan — dicetak setiap tick, tidak bisa dimatikan flag apa pun
#: (docs/rules-compliance.md: kata "practice/simulated" wajib terlihat).
BANNER = "tenko agent tick — SIMULATED practice trading: no funds, no wallet, no execution"


class InsufficientEvidence(Exception):
    """Tidak ada bukti beku untuk simbol ini → TIDAK ADA baris yang ditulis.

    Dipisahkan dari penolakan penjaga (:class:`agent.anchor.EvidenceMismatchError`) karena
    sebabnya beda dan konsekuensinya beda: penjaga yang menolak berarti ada yang berbohong
    (kode keluar ≠ 0), sedangkan "perekam belum menulis apa pun yang cukup baru" adalah
    keadaan normal yang jujur (kode keluar 0). Keduanya sama dalam satu hal: tidak ada baris
    `decisions` yang lahir.

    **Kenapa tidak ditulis sebagai NO_GO biasa** (temuan SEDANG security-reviewer, 4 Sep 2026):
    baris ber-`snapshot_ids=[]` membekukan bundle KOSONG, sehingga `decision_hash`-nya dihitung
    atas nol bukti dan `make replay` melaporkan **"IDENTICAL … dari 0 snapshot beku"** — vonis
    yang nyaris tidak membawa informasi tetapi TERLIHAT seperti verifikasi di layar demo §7.
    Bukti nyatanya ada di `data/ryo.db` baris 20-23 (ditulis autostart daemon, semuanya
    `snapshot_ids_json='[]'`). Yang jujur adalah menolak membukukannya sama sekali.
    """


#: Kode keluar: 0 = tick selesai, 1 = ada simbol yang DITOLAK penjaga, 2 = tidak bisa jalan.
EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_UNUSABLE = 2


@dataclass(slots=True)
class SymbolOutcome:
    """Hasil satu simbol dalam satu tick — cukup untuk dicetak, tanpa P&L."""

    symbol: str
    decision: str
    blocking: tuple[str, ...] = ()
    decision_id: int | None = None
    decision_hash: str = ""
    plan: PracticePlan | None = None
    trade_event_id: int | None = None
    rejected: str = ""
    insufficient: str = ""

    def line(self) -> str:
        if self.rejected:
            return f"{self.symbol:<6} REJECTED  {self.rejected}"
        if self.insufficient:
            return f"{self.symbol:<6} NO ROW    insufficient evidence: {self.insufficient}"
        head = f"{self.symbol:<6} {self.decision:<6} decision_id={self.decision_id}"
        if self.plan is not None:
            return (
                f"{head}  practice trade opened (SIMULATED): {self.plan.direction} "
                f"entry {self.plan.entry:g} stop {self.plan.stop:g} target {self.plan.target:g} "
                f"size {self.plan.size_usd:g} USD notional"
            )
        blocking = ", ".join(self.blocking) if self.blocking else "-"
        return f"{head}  declined; blocking: {blocking}"


@dataclass(slots=True)
class TickReport:
    """Ringkasan satu tick: keputusan, penutupan posisi, dan penolakan."""

    outcomes: list[SymbolOutcome] = field(default_factory=list)
    settlements: list[Any] = field(default_factory=list)
    open_after: int = 0
    lines: list[str] = field(default_factory=list)

    @property
    def n_go(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.decision == "GO" and not outcome.rejected)

    @property
    def n_no_go(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.decision == "NO_GO" and not outcome.rejected)

    @property
    def n_rejected(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.rejected)

    @property
    def n_insufficient(self) -> int:
        """Simbol yang tidak punya bukti beku sama sekali — tidak ada baris yang ditulis."""
        return sum(1 for outcome in self.outcomes if outcome.insufficient)


def _item_status_from(result: SkillResult) -> dict[str, str]:
    """`item_status` untuk material hash — dari `availability` bila `data` tidak ada.

    Envelope penolakan (`data is None`) tetap menyebut kesepuluh baris panggilan yang tidak
    terjawab di `availability` (`preflight._refusal`), jadi keputusannya tetap bisa di-hash
    tanpa mengarang satu status pun.
    """
    if result.data is not None:
        return {item.id: item.status for item in result.items}
    return dict(result.availability)


def _blocking_from(result: SkillResult) -> tuple[str, ...]:
    source = result.data if result.data is not None else result.summary
    raw = source.get("blocking", []) if isinstance(source, dict) else []
    return tuple(str(entry) for entry in raw) if isinstance(raw, list) else ()


def _card(result: SkillResult, params: AgentParams, plan: PracticePlan | None) -> dict[str, Any]:
    """Kartu panggilan yang disimpan di `decisions.card_json` (dibaca UI TASKS 3.x).

    `algo` ikut disimpan dengan sengaja: `core/replay.py` menyatakan kenaikan versi format
    hash hanya bisa dibedakan dari tamper kalau pemanggil menyimpan `algo` bersama keputusan
    (kait STALE_ALGO, ADR-016 / TASKS 2.4a).
    """
    agent_block: dict[str, Any] = {
        **params.as_dict(),
        "exec_params_hash": params.exec_params_hash(),
        "module": "agent.main",
    }
    if plan is not None:
        agent_block["plan"] = plan.as_dict()
    return {"algo": ALGO, "skill": result.model_dump(mode="json"), "agent": agent_block}


def _decide_symbol(
    store: Store,
    symbol: str,
    params: AgentParams,
    *,
    intent: str = DEFAULT_INTENT,
    now: datetime | None = None,
) -> SymbolOutcome:
    """Satu simbol: preflight → tiga penjaga → baris `decisions` (+ `open` bila GO)."""
    result = preflight(symbol, intent, store=store, params=params.preflight, now=now)
    # Penjaga 1: envelope konsisten dengan itemnya sendiri (dipanggil ulang di sisi pemakai,
    # sesuai kontrak `check_aggregate_floor` — envelope sudah lewat tangan lain).
    check_aggregate_floor(result, params.preflight)

    snapshot_ids: list[int] = []
    if result.data is not None:
        raw_ids = result.data.get("snapshot_ids", [])
        snapshot_ids = [i for i in raw_ids if isinstance(i, int) and not isinstance(i, bool)]
    if not snapshot_ids:
        # Nol bukti beku: tidak ada yang bisa di-replay, jadi tidak ada baris yang ditulis
        # (lihat :class:`InsufficientEvidence`). Sebabnya dari preflight sendiri, apa adanya.
        # Satu baris, bukan sepuluh: alasan lengkap per item hidup di `summary`/`warnings`
        # envelope preflight; yang dicetak di sini adalah sebab pokoknya.
        points = [str(point) for point in result.summary.get("key_points", [])]
        head = points[0].split(" -> ")[0] if points else "tidak ada tool yang menjawab"
        raise InsufficientEvidence(
            f"tidak ada snapshot dalam {params.preflight.max_age_min} menit terakhir di store "
            f"({len(points)} baris panggilan tidak terjawab; mis. {head})"
        )
    # Penjaga bukti BER-SIMBOL (TASKS 0.13). "Ada snapshot" saja tidak cukup: `market_overview`
    # dan `monitor_market_sentiment_shift` dicari dengan `{}`, jadi bukti market-wide SELALU ada
    # untuk simbol apa pun — termasuk `ZZZQQQ`. Sampai 0.13 itu menghasilkan baris `decisions`
    # permanen yang bahkan di-replay sebagai IDENTICAL: "terlihat seperti verifikasi padahal
    # tidak ada yang diverifikasi tentang simbol itu". `ui.app` sudah menuntut syarat ini untuk
    # menjawab 404 sejak 3.1a; agen memakai standar lebih rendah daripada UI-nya sendiri.
    # Simbol yang DIPAKAI mencari, bukan yang diketik: `result.data["symbol"]` sudah dijaga
    # `_check_bookkeeping`, jadi pesannya menyebut simbol yang benar-benar dicari (temuan RENDAH
    # security-reviewer 0.13 — `"bukti TENTANG sol"` untuk pencarian atas `SOL` menyesatkan).
    searched = result.data.get("symbol") if isinstance(result.data, dict) else None
    named = searched if isinstance(searched, str) and searched else symbol
    spoken_about = symbol_evidence(result.data, named)
    if not spoken_about:
        raise InsufficientEvidence(
            f"tidak ada satu pun bukti TENTANG {named} di store dalam "
            f"{params.preflight.max_age_min} menit terakhir "
            f"({', '.join(sorted(symbol_tools(named))) or '-'} tidak menjawab); yang terbaca "
            "hanya bukti market-wide, dan itu tidak berbicara tentang simbol ini"
        )
    bundle: Bundle = freeze(store, snapshot_ids)

    # Penjaga 2: item dijangkarkan ke payload beku SEBELUM baris ditulis & sebelum ditakar.
    recomputed = check_against_evidence(result, bundle, params.preflight, symbol=symbol)

    verdict = (
        result.data.get("decision") if result.data is not None else result.summary.get("decision", "NO_GO")
    )
    blocking = _blocking_from(result)
    material = {
        "decision": verdict,
        "blocking": list(blocking),
        "item_status": _item_status_from(result),
    }
    # Penjaga 3: hash dihitung ULANG di sini; yang disimpan bukan hash pemberian envelope.
    computed_hash = decision_hash(bundle, params.preflight.as_dict(), material)
    if result.data is not None and result.data.get("decision_hash") != computed_hash:
        raise EvidenceMismatchError(
            f"{symbol}: decision_hash envelope {result.data.get('decision_hash')!r} bukan hash "
            f"yang dihitung ulang dari bukti beku {computed_hash!r}"
        )

    plan: PracticePlan | None = None
    if verdict == "GO":
        # Ditakar dari item yang SUDAH dijangkarkan, bukan dari `data["plan"]` envelope.
        plan = plan_from_items(symbol, recomputed.items, params)

    decision_id = store.insert_decision(
        symbol=symbol,
        decision=str(verdict),
        blocking=list(blocking),
        snapshot_ids=snapshot_ids,
        params_hash=params.decision_params_hash(),
        decision_hash=computed_hash,
        card=_card(result, params, plan),
    )
    outcome = SymbolOutcome(
        symbol=symbol,
        decision=str(verdict),
        blocking=blocking,
        decision_id=decision_id,
        decision_hash=computed_hash,
        plan=plan,
    )
    if plan is not None:
        outcome.trade_event_id = open_trade(store, decision_id, plan)
    return outcome


def tick(
    store: Store,
    symbols: Sequence[str],
    params: AgentParams = DEFAULT_AGENT_PARAMS,
    *,
    now: datetime | None = None,
) -> TickReport:
    """Satu siklus penuh: tutup posisi yang jatuh tempo, lalu panggil tiap simbol."""
    report = TickReport()
    report.settlements = settle_open_positions(store, params, now=now)
    for settlement in report.settlements:
        state = "closed" if settlement.closed else "still open"
        report.lines.append(
            f"position decision_id={settlement.decision_id} {settlement.symbol}: {state} "
            f"({settlement.reason})"
        )
    for symbol in symbols:
        try:
            outcome = _decide_symbol(store, symbol, params, now=now)
        except InsufficientEvidence as exc:
            log.info("%s: tidak ada bukti beku; tidak ada baris yang ditulis: %s", symbol, exc)
            outcome = SymbolOutcome(symbol=symbol, decision="-", insufficient=str(exc))
        except (EvidenceMismatchError, TradeError, ReplayError) as exc:
            log.warning("%s: ditolak sebelum menulis apa pun: %s", symbol, exc)
            outcome = SymbolOutcome(symbol=symbol, decision="-", rejected=str(exc))
        report.outcomes.append(outcome)
    fold = read_positions(store.path)
    report.open_after = len(fold.positions)
    # Yang TIDAK dilipat disebut, bukan dibuang diam-diam (TASKS 2.4b): `trade_events` warisan
    # menunjuk keputusan generasi lain, jadi memasukkannya ke hitungan posisi terbuka akan
    # melaporkan posisi yang tidak bisa dipertanggungjawabkan sama sekali.
    for note in fold.notes():
        log.info("%s", note)
        report.lines.append(note)
    return report


def _print_report(report: TickReport, store: Store, symbols: Sequence[str], params: AgentParams) -> None:
    print(BANNER)
    print(f"db: {store.path}  ·  watchlist: {', '.join(symbols) or '-'}")
    print(f"params_hash (decision): {params.decision_params_hash()}")
    for line in report.lines:
        print(line)
    for outcome in report.outcomes:
        print(outcome.line())
    closed = sum(1 for settlement in report.settlements if settlement.closed)
    stale = sum(1 for settlement in report.settlements if settlement.stale_generation)
    generation = f" ({stale} of them from an earlier params generation, TASKS 2.4b)" if stale else ""
    print(
        f"tick: {report.n_go} GO, {report.n_no_go} NO_GO, {report.n_rejected} rejected, "
        f"{report.n_insufficient} without evidence (no row written); "
        f"{closed} position(s) closed, {report.open_after} still open{generation} — all SIMULATED"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent.main",
        description="Tenko practice-trade agent (SIMULATED; store-first, never calls RYO).",
    )
    parser.add_argument("--once", action="store_true", help="jalankan satu tick lalu keluar")
    parser.add_argument("--interval", type=int, default=0, help="detik antar tick (0 = sekali)")
    parser.add_argument("--symbols", default=None, help="daftar simbol, dipisah koma")
    parser.add_argument("--db", default=None, help="path DB (default: DB_PATH / ./data/ryo.db)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args, unknown = build_parser().parse_known_args(list(argv) if argv is not None else None)
    if any(arg.startswith("--client") for arg in unknown):
        # `scripts/daemons.sh` pernah mengoper `--client live`. Kemampuan itu DICABUT
        # (ADR-018, USULAN); menolaknya terang-terangan lebih jujur daripada mengabaikannya.
        print(
            "agent: opsi --client dicabut — agen ini store-first murni dan tidak pernah "
            "memanggil tool RYO (ADR-018, USULAN; lihat docstring agent/main.py)",
            file=sys.stderr,
        )
        return EXIT_UNUSABLE
    if unknown:
        print(f"agent: argumen tidak dikenal: {unknown}", file=sys.stderr)
        return EXIT_UNUSABLE

    params = DEFAULT_AGENT_PARAMS
    selected = watchlist(args.symbols)
    if not selected.symbols:
        print("agent: watchlist kosong; tidak ada yang dipanggil", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        # Buka BACA-SAJA lebih dulu: `Store(...)` biasa MEMBUAT berkas baru, sehingga `--db`
        # yang salah ketik dulu menghasilkan DB kosong yang lalu diisi baris tanpa bukti
        # (temuan SEDANG security-reviewer, 4 Sep 2026). Resolusi path tetap milik
        # `core.store` — agen tidak menebak nama berkasnya sendiri.
        Store(args.db, read_only=True).close()
    except StoreError as exc:
        print(
            f"agent: DB belum ada atau tidak terbaca ({exc}); agen tidak membuat DB baru — "
            "jalankan `make record` dulu, atau tunjuk berkas yang benar dengan --db/DB_PATH",
            file=sys.stderr,
        )
        return EXIT_UNUSABLE
    try:
        store = Store(args.db)
    except StoreError as exc:
        print(f"agent: store tidak bisa dibuka: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    status = EXIT_OK
    try:
        while True:
            started = datetime.now(UTC)
            try:
                report = tick(store, selected.symbols, params)
            except (StoreError, LedgerError) as exc:
                print(f"agent: tick gagal membaca/menulis store: {exc}", file=sys.stderr)
                return EXIT_UNUSABLE
            _print_report(report, store, selected.symbols, params)
            if report.n_rejected:
                status = EXIT_REJECTED
            if args.once or args.interval <= 0:
                return status
            elapsed = (datetime.now(UTC) - started).total_seconds()
            time.sleep(max(0.0, args.interval - elapsed))
    except KeyboardInterrupt:  # pragma: no cover - jalur daemon
        print("agent: dihentikan", file=sys.stderr)
        return status
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
