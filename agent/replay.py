"""`python -m agent.replay <decision_id>` — putar ulang satu keputusan (TASKS 2.4, ADR-003).

Yang dijawab perintah ini: *"kalau kita menilai ulang bukti beku milik keputusan ini, dengan
aturan yang berlaku sekarang, apakah kita sampai pada keputusan yang sama persis?"*.

Alurnya tidak menyentuh jaringan, tidak menyentuh `latest_fresh`, dan tidak menebak apa pun:

1. baca baris `decisions` (koneksi BACA-SAJA, `agent/ledger.py`);
2. `core.replay.freeze(store, snapshot_ids_json)` — baris store dibaca ulang dan tiap
   `args_hash`/`payload_hash` dihitung ulang. Satu byte `payload_json` yang berubah berhenti
   di sini sebagai `ReplayTamperError` ber-`snapshot_id`;
3. nilai ulang kesepuluh item dari bundle itu (`agent.anchor.recompute`) — fungsi murni;
4. `core.replay.decision_hash(bundle, params, keputusan hasil penilaian ulang)`;
5. bandingkan dengan kolom `decisions.decision_hash` yang TERSIMPAN.

Tiga keluaran, dan hanya tiga:

* ``IDENTICAL <hash>`` — sama persis, exit 0.
* ``MISMATCH`` + sebabnya (`snapshot_id` bila buktinya yang berubah), exit 1.
* pesan jujur + exit 2 — baris tidak ada, DB tidak terbaca, argumen bukan angka.

**`params_hash` yang bergeser dilaporkan MISMATCH ber-sebab, bukan disembunyikan.** Keputusan
yang dibuat dengan ambang lain memang tidak bisa direproduksi dengan ambang sekarang; itu fakta
tentang keputusan itu, bukan kegagalan alat. (Generasi lama tanpa kunci `algo` di `card_json`
akan mendapat vonis ketiga `STALE_ALGO` — itu TASKS 2.4a, sengaja belum di sini.)

Batas yang diakui: perintah ini membaca DB yang ditunjuk `DB_PATH`/`--db`. Ia tidak
membuktikan bahwa berkas itu adalah berkas yang dipakai saat keputusan dibuat — `core/replay.py`
sengaja tidak mengikat `snapshots.id` ke hash apa pun. Yang dibuktikannya: bukti dengan id
tersebut, di berkas ini, menghasilkan keputusan yang sama.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass

from agent.anchor import EvidenceMismatchError, recompute
from agent.ledger import LedgerError, read_decision
from core.replay import ReplayError, ReplayTamperError, decision_hash, freeze
from core.store import Store, StoreError
from preflight.params import DEFAULT_PARAMS, Params

VERDICT_IDENTICAL = "IDENTICAL"
VERDICT_MISMATCH = "MISMATCH"

EXIT_IDENTICAL = 0
EXIT_MISMATCH = 1
EXIT_UNUSABLE = 2


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """Hasil satu replay — cukup untuk dicetak dan untuk diuji tanpa menangkap stdout."""

    decision_id: int
    verdict: str
    stored_hash: str
    computed_hash: str | None
    detail: str

    def lines(self) -> list[str]:
        head = f"replay decision_id={self.decision_id}: {self.verdict}"
        if self.verdict == VERDICT_IDENTICAL:
            return [f"{head} {self.computed_hash}", self.detail]
        return [head, f"stored   : {self.stored_hash}", f"recomputed: {self.computed_hash}", self.detail]


def replay(decision_id: int, store: Store, params: Params = DEFAULT_PARAMS) -> ReplayReport:
    """Putar ulang satu keputusan; tidak pernah melempar untuk bukti yang rusak.

    Raises:
        LedgerError: baris `decisions` tidak ada / tidak terbaca (pemanggil → exit 2).
        core.store.StoreError: DB tidak terbaca.
    """
    row = read_decision(store.path, decision_id)
    if row is None:
        raise LedgerError(f"tidak ada keputusan ber-id {decision_id} di {store.path}")
    stored = row.decision_hash

    if row.params_hash != params.params_hash():
        return ReplayReport(
            decision_id,
            VERDICT_MISMATCH,
            stored,
            None,
            f"sebab: keputusan ini ditulis dengan params_hash {row.params_hash} sementara "
            f"ambang yang berlaku sekarang {params.params_hash()}; aturannya sudah berubah, "
            "jadi keputusan ini memang tidak bisa direproduksi apa adanya",
        )

    try:
        bundle = freeze(store, row.snapshot_ids())
    except ReplayTamperError as exc:
        return ReplayReport(decision_id, VERDICT_MISMATCH, stored, None, f"sebab: bukti berubah — {exc}")
    except ReplayError as exc:
        detail = f"sebab: bukti tidak bisa dibekukan — {exc}"
        return ReplayReport(decision_id, VERDICT_MISMATCH, stored, None, detail)

    try:
        recomputed = recompute(row.symbol, bundle, params)
    except (EvidenceMismatchError, ReplayError) as exc:
        detail = f"sebab: bukti tidak bisa dinilai ulang — {exc}"
        return ReplayReport(decision_id, VERDICT_MISMATCH, stored, None, detail)

    computed = decision_hash(bundle, params.as_dict(), recomputed.decision.as_material())
    if computed == stored:
        return ReplayReport(
            decision_id,
            VERDICT_IDENTICAL,
            stored,
            computed,
            f"{row.symbol} {recomputed.decision.decision} dari {len(bundle.entries)} snapshot beku "
            f"({', '.join(str(i) for i in bundle.snapshot_ids) or '-'}); blocking: "
            f"{', '.join(recomputed.decision.blocking) or '-'}",
        )
    return ReplayReport(
        decision_id,
        VERDICT_MISMATCH,
        stored,
        computed,
        f"sebab: keputusan yang dinilai ulang adalah {recomputed.decision.decision} "
        f"(blocking: {', '.join(recomputed.decision.blocking) or '-'}) atas {len(bundle.entries)} "
        f"snapshot beku, sementara baris tersimpan mencatat {row.decision} "
        f"(blocking: {', '.join(str(i) for i in row.blocking()) or '-'})",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    db_path: str | None = None
    if "--db" in args:
        index = args.index("--db")
        if index + 1 >= len(args):
            print("pakai: python -m agent.replay <decision_id> [--db PATH]", file=sys.stderr)
            return EXIT_UNUSABLE
        db_path = args[index + 1]
        del args[index : index + 2]
    if len(args) != 1:
        print("pakai: python -m agent.replay <decision_id> [--db PATH]", file=sys.stderr)
        return EXIT_UNUSABLE
    try:
        decision_id = int(args[0])
    except ValueError:
        print(f"decision_id harus bilangan bulat, bukan {args[0]!r}", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        store = Store(db_path, read_only=True)
    except StoreError as exc:
        print(f"replay: store tidak bisa dibuka: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE
    try:
        report = replay(decision_id, store)
    except (StoreError, LedgerError) as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE
    finally:
        store.close()

    for line in report.lines():
        print(line)
    return EXIT_IDENTICAL if report.verdict == VERDICT_IDENTICAL else EXIT_MISMATCH


if __name__ == "__main__":
    raise SystemExit(main())
