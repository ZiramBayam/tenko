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

Empat vonis, dan hanya empat (TASKS 2.4a, ADR-016):

* ``IDENTICAL <hash>`` — sama persis, atas bukti beku yang ada, exit 0. **Satu-satunya vonis
  yang berarti "terverifikasi"** (:attr:`ReplayReport.verified`).
* ``MISMATCH`` + sebabnya (`snapshot_id` bila buktinya yang berubah), exit 1.
* ``STALE_ALGO`` — `card_json` baris ini tidak menyebut algoritma hash yang berlaku, jadi
  keputusannya ditulis kode yang sudah tidak ada dan tidak bisa dinilai ulang dengan aturan
  sekarang. Bukan tamper, bukan kegagalan: exit 0, dengan `algo: null` dan **jumlah snapshot
  bundle yang MASIH bisa diverifikasi** — bukti yang tersisa disebut apa adanya.
* ``NO_EVIDENCE`` — `snapshot_ids` baris ini kosong **dan** hash tersimpannya memang hash
  bundle kosong: baris itu TERBUKTI ditulis tanpa bukti. Exit 0, `verified` FALSE. Daftar
  bukti yang DIKOSONGKAN belakangan tidak sampai ke sini: hash-nya tidak cocok → MISMATCH.
* di luar vonis: pesan jujur + exit 2 — baris tidak ada, DB tidak terbaca, argumen bukan angka.

**Kenapa `NO_EVIDENCE` ada, dan kenapa ia BUKAN `IDENTICAL`** (keputusan sadar, TASKS 2.4a):
`data/ryo.db` baris 20-23 ditulis dengan `snapshot_ids_json='[]'`. Bundle kosong itu sah, dan
hash-nya cocok dengan dirinya sendiri, sehingga versi sebelumnya menjawab
"IDENTICAL … dari 0 snapshot beku" — kalimat yang **nyaris tidak membawa informasi tetapi
terbaca sebagai lencana hijau**. `agent.main.InsufficientEvidence` sudah menolak menulis baris
seperti itu lagi, tetapi store append-only (ADR-006) berarti keempatnya ada selamanya. Vonis
terpisah membuat perbedaannya bisa dibaca MESIN (nama vonis + `ReplayReport.verified`), bukan
hanya oleh manusia yang membaca ekor kalimat — syarat TASKS 3.1a untuk `ui/`.

**`params_hash` yang bergeser dilaporkan `STALE_PARAMS`, bukan `MISMATCH`** (TASKS 0.14).
`MISMATCH` di berkas ini berarti "bukti tidak mereproduksi keputusan" — sebuah temuan. Untuk
baris yang ditulis dengan aturan LAIN, temuan itu tidak kami dukung: `decision_hash` memakai
params yang berlaku sekarang, jadi buktinya belum diperiksa sama sekali. `STALE_PARAMS`
menyatakan ketidaktahuan dan keluar 0, seperti `STALE_ALGO`.

Batas yang harus dibaca apa adanya: begitu params bergeser, baris nol-bukti **kehilangan**
vonis `NO_EVIDENCE`-nya dan ikut menjadi `STALE_PARAMS`. Itu bukan urutan pemeriksaan yang
salah — cabang `NO_EVIDENCE` bersyarat `computed == stored`, dan syarat itulah yang menutup
temuan SEDANG-1 security-reviewer (baris yang `snapshot_ids_json`-nya dikosongkan tidak boleh
mengaku "memang ditulis tanpa bukti"). Memindahkan urutannya membuat cabang itu tak
terjangkau, bukan terlambat; diuji saat 0.14, bukan disimpulkan dari membaca.

Urutan vonis, dan urutan ini mengikat: `algo` usang (`STALE_ALGO`) → `params_hash` berbeda
(`STALE_PARAMS`) → sisanya.

**Sebelumnya (dicabut TASKS 0.14):** `params_hash` yang bergeser dilaporkan MISMATCH ber-sebab. Keputusan
yang dibuat dengan ambang lain memang tidak bisa direproduksi dengan ambang sekarang; itu fakta
tentang keputusan itu, bukan kegagalan alat.

**Kode keluar tidak boleh dibaca sebagai "terverifikasi".** 0 berarti "perintah ini berhasil
menghasilkan vonis", dan tiga vonis bisa berakhir 0. Yang mengikat adalah nama vonisnya.

Batas yang diakui: perintah ini membaca DB yang ditunjuk `DB_PATH`/`--db`. Ia tidak
membuktikan bahwa berkas itu adalah berkas yang dipakai saat keputusan dibuat — `core/replay.py`
sengaja tidak mengikat `snapshots.id` ke hash apa pun. Yang dibuktikannya: bukti dengan id
tersebut, di berkas ini, menghasilkan keputusan yang sama.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from agent.anchor import EvidenceMismatchError, recompute
from agent.ledger import DecisionRow, LedgerError, read_decision
from core.replay import ALGO, ReplayError, ReplayTamperError, decision_hash, freeze
from core.store import Store, StoreError
from preflight.params import DEFAULT_PARAMS, Params

#: Nilai asal-DB SELALU dicetak lewat :func:`_safe`. Alasannya diuji, bukan gaya: `card_json`
#: dan kolom teks lain tidak dilindungi hash apa pun, sehingga `algo` bernilai
#: `"x'\nreplay decision_id=5: IDENTICAL 0000deadbeef\nverified : yes"` menyisipkan BARIS PALSU
#: utuh ke keluaran; gerbang mesin (:attr:`ReplayReport.verified`) tetap False, tetapi mata
#: manusia, `grep`, dan UI teks-bebas (TASKS 3.1a) tertipu (temuan SEDANG-3 security-reviewer,
#: 4 Sep 2026, dibuktikan dengan baris palsu yang benar-benar tercetak).
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]")

#: Kosakata yang hanya boleh diucapkan alat ini sendiri (lihat langkah 3 di :func:`_safe`).
_RESERVED = re.compile(r"IDENTICAL|MISMATCH|STALE_ALGO|NO_EVIDENCE|verified", re.IGNORECASE)


def _safe(value: object, limit: int = 200) -> str:
    """Nilai asal-DB dalam bentuk yang tidak bisa memalsukan baris keluaran MAUPUN vonis.

    Tiga langkah, masing-masing menutup pembaca yang berbeda:

    1. `repr` — meng-escape baris baru dan aksara kendali serta memberi tanda kutip yang
       membuat batas nilainya terlihat MATA; nilai tidak bisa lagi menambah baris sendiri.
    2. regex aksara kendali — lapis kedua untuk yang lolos `repr` (mis. U+2028, yang dianggap
       pemisah baris oleh sebagian penampil dan oleh `ui/`), lalu pemotongan panjang supaya
       satu kolom raksasa tidak menenggelamkan vonisnya.
    3. **kosakata vonis dinetralkan** — `IDENTICAL` di dalam sebuah NILAI dituliskan
       `I\\x44ENTICAL`. Alasannya: setelah langkah 1 nilai jahat memang tinggal satu baris
       berkutip, tetapi `grep -qi IDENTICAL` atas seluruh keluaran (dan itulah yang dipakai
       naskah demo §7 serta bukti AC) masih akan lulus karena katanya ada di dalam kutip.
       Kosakata vonis adalah milik ALAT, bukan milik data.
    """
    text = repr(str(value))
    if len(text) > limit:
        text = text[: limit - 2] + "…'"
    text = _CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", text)
    return _RESERVED.sub(lambda m: f"{m.group()[0]}\\x{ord(m.group()[1]):02x}{m.group()[2:]}", text)


VERDICT_IDENTICAL = "IDENTICAL"
VERDICT_MISMATCH = "MISMATCH"
VERDICT_STALE_ALGO = "STALE_ALGO"
VERDICT_NO_EVIDENCE = "NO_EVIDENCE"
#: Baris ini ditulis dengan `params_hash` lain. Ia menyatakan KETIDAKTAHUAN, bukan tuduhan:
#: dengan aturan yang berbeda kami tidak bisa menyatakan apa pun tentang baris itu — termasuk
#: bahwa buktinya nol (TASKS 0.14).
VERDICT_STALE_PARAMS = "STALE_PARAMS"

#: Vonis yang boleh dibaca sebagai "keputusan ini terbukti bisa direproduksi". Satu, sengaja.
VERIFIED_VERDICTS = frozenset({VERDICT_IDENTICAL})

EXIT_IDENTICAL = 0
EXIT_MISMATCH = 1
EXIT_UNUSABLE = 2
#: Vonis yang keluar 0 — "perintah menghasilkan vonis", BUKAN "terverifikasi" (lihat docstring).
EXIT_ZERO_VERDICTS = frozenset(
    {VERDICT_IDENTICAL, VERDICT_STALE_ALGO, VERDICT_NO_EVIDENCE, VERDICT_STALE_PARAMS}
)


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """Hasil satu replay — cukup untuk dicetak dan untuk diuji tanpa menangkap stdout."""

    decision_id: int
    verdict: str
    stored_hash: str
    computed_hash: str | None
    detail: str
    #: `card_json["algo"]` baris ini; None = baris tidak menyebut algoritma (generasi lama).
    algo: str | None = None
    #: (bisa diverifikasi, seluruh) snapshot beku milik baris ini — diisi untuk STALE_ALGO.
    verifiable: tuple[int, int] | None = None

    @property
    def verified(self) -> bool:
        """True HANYA untuk IDENTICAL. Dibaca UI (TASKS 3.1a) alih-alih mengurai teks."""
        return self.verdict in VERIFIED_VERDICTS

    def lines(self) -> list[str]:
        head = f"replay decision_id={self.decision_id}: {self.verdict}"
        if self.verdict == VERDICT_IDENTICAL:
            return [f"{head} {self.computed_hash}", self.detail]
        if self.verdict in (VERDICT_STALE_ALGO, VERDICT_NO_EVIDENCE, VERDICT_STALE_PARAMS):
            out = [head, f"algo     : {'null' if self.algo is None else _safe(self.algo)}"]
            if self.verifiable is not None:
                usable, total = self.verifiable
                out.append(f"snapshots: {usable} of {total} still verifiable")
            out += [f"stored   : {_safe(self.stored_hash)}", "verified : no", self.detail]
            return out
        return [
            head,
            f"stored   : {_safe(self.stored_hash)}",
            f"recomputed: {self.computed_hash}",
            self.detail,
        ]


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

    if not row.is_current_generation():
        return _stale_algo(row, store)

    try:
        frozen_ids = row.snapshot_ids()
    except LedgerError as exc:
        return ReplayReport(
            decision_id,
            VERDICT_MISMATCH,
            stored,
            None,
            f"sebab: daftar bukti baris ini tidak terbaca — {exc}",
            algo=row.algo(),
        )
    if row.params_hash != params.params_hash():
        # Sampai TASKS 0.14 ini berbunyi MISMATCH — vonis yang berarti "bukti tidak
        # mereproduksi keputusan". Untuk baris yang ditulis dengan aturan LAIN itu tuduhan yang
        # tidak bisa kami dukung: kami belum memeriksa buktinya sama sekali, dan tidak bisa,
        # karena `decision_hash` memakai params yang berlaku sekarang. Empat baris nol-bukti
        # (id 20-23) bahkan kehilangan vonis `NO_EVIDENCE` mereka yang benar dengan cara ini —
        # cabang itu bersyarat `computed == stored`, jadi ia tak terjangkau begitu params
        # bergeser, dan memindahkan urutannya TIDAK menolong (diuji, TASKS 0.14).
        return ReplayReport(
            decision_id,
            VERDICT_STALE_PARAMS,
            stored,
            None,
            f"sebab: keputusan ini ditulis dengan params_hash {_safe(row.params_hash)} sementara "
            f"ambang yang berlaku sekarang {params.params_hash()}. Aturannya sudah berubah, jadi "
            "kami tidak menilai baris ini sama sekali — bukan menemukannya salah. Untuk "
            "memeriksanya, jalankan replay dengan params yang dipakai saat baris ini ditulis.",
            algo=row.algo(),
        )

    try:
        bundle = freeze(store, frozen_ids)
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
    if computed == stored and not frozen_ids:
        # Hash tersimpan MEMANG hash bundle kosong: baris ini bukan sekadar "tanpa bukti", ia
        # TERBUKTI keputusan tanpa bukti. Yang membuat pemeriksaan ini bukan formalitas: baris
        # yang tadinya punya bukti lalu `snapshot_ids_json`-nya dikosongkan TIDAK sampai ke
        # sini — hash-nya tidak akan cocok, dan vonisnya MISMATCH (temuan SEDANG-1
        # security-reviewer, 4 Sep 2026: sebelumnya "kosongkan satu kolom" menelan MISMATCH).
        return ReplayReport(
            decision_id,
            VERDICT_NO_EVIDENCE,
            stored,
            computed,
            f"sebab: baris ini tidak membekukan satu snapshot pun, dan hash tersimpannya memang "
            f"hash dari bundle KOSONG — jadi ia terbukti ditulis tanpa bukti, bukan kehilangan "
            f"buktinya. Tidak ada yang bisa diputar ulang tentang {_safe(row.symbol)}: yang "
            f"dinilai ulang hanya ketiadaan itu sendiri ({recomputed.decision.decision}).",
            algo=row.algo(),
            verifiable=(0, 0),
        )
    if computed == stored:
        return ReplayReport(
            decision_id,
            VERDICT_IDENTICAL,
            stored,
            computed,
            f"{_safe(row.symbol)} {recomputed.decision.decision} dari {len(bundle.entries)} "
            f"snapshot beku ({', '.join(str(i) for i in bundle.snapshot_ids) or '-'}); blocking: "
            f"{', '.join(recomputed.decision.blocking) or '-'}",
        )
    emptied = (
        " Baris ini mencatat NOL snapshot beku, sementara hash tersimpannya bukan hash bundle "
        "kosong: daftar buktinya dikosongkan setelah keputusan ditulis."
        if not frozen_ids
        else ""
    )
    return ReplayReport(
        decision_id,
        VERDICT_MISMATCH,
        stored,
        computed,
        f"sebab: keputusan yang dinilai ulang adalah {recomputed.decision.decision} "
        f"(blocking: {', '.join(recomputed.decision.blocking) or '-'}) atas {len(bundle.entries)} "
        f"snapshot beku, sementara baris tersimpan mencatat {_safe(row.decision)} "
        f"(blocking: {', '.join(_safe(i) for i in row.blocking()) or '-'}).{emptied}",
    )


def _verifiable_snapshots(store: Store, snapshot_ids: Sequence[int]) -> tuple[int, int]:
    """(berapa snapshot yang masih cocok dengan hash-nya sendiri, berapa yang dicatat baris ini).

    Diperiksa satu per satu supaya satu baris yang hilang/berubah tidak menghapus fakta bahwa
    sisanya masih utuh — untuk baris warisan, bukti yang tersisa itulah satu-satunya yang bisa
    kami tawarkan.
    """
    usable = 0
    for snapshot_id in snapshot_ids:
        try:
            freeze(store, [snapshot_id])
        except (ReplayError, StoreError):
            continue
        usable += 1
    return usable, len(snapshot_ids)


def _stale_algo(row: DecisionRow, store: Store) -> ReplayReport:
    """Vonis untuk baris generasi lama (ADR-016): sebabnya disebut, buktinya dihitung."""
    try:
        snapshot_ids = row.snapshot_ids()
    except LedgerError:
        snapshot_ids = []
    usable, total = _verifiable_snapshots(store, snapshot_ids)
    algo = row.algo()
    named = "tidak menyebut algoritma hash keputusan" if algo is None else f"menyebut algoritma {_safe(algo)}"
    # Klaim "bukan tanda bukti diubah" BERSYARAT pada angkanya sendiri (temuan SEDANG-2
    # security-reviewer, 4 Sep 2026): saat usable < total, buktinya memang berubah/hilang, dan
    # kalimat yang menyangkalnya bisa dikutip naskah demo §7 sebagai jaminan yang tidak benar.
    if total and usable == total:
        evidence = (
            f"Bukti bekunya sendiri masih utuh: {usable} dari {total} snapshot masih cocok "
            "dengan hash-nya, jadi vonis ini bukan tanda bukti diubah."
        )
    elif total:
        evidence = (
            f"PERHATIAN: hanya {usable} dari {total} snapshot yang masih cocok dengan hash-nya "
            f"— {total - usable} sisanya hilang atau berubah, dan vonis ini TIDAK menyatakan "
            "bukti baris ini utuh."
        )
    else:
        evidence = (
            "Baris ini tidak mencatat satu snapshot beku pun, jadi tidak ada bukti yang bisa diperiksa."
        )
    return ReplayReport(
        row.id,
        VERDICT_STALE_ALGO,
        row.decision_hash,
        None,
        f"sebab: kartu keputusan ini {named}, sementara yang berlaku sekarang {ALGO!r}; "
        f"baris ini ditulis implementasi yang sudah dihapus, jadi keputusannya tidak bisa "
        f"dinilai ulang dengan aturan sekarang. {evidence}",
        algo=algo,
        verifiable=(usable, total),
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
    return EXIT_IDENTICAL if report.verdict in EXIT_ZERO_VERDICTS else EXIT_MISMATCH


if __name__ == "__main__":
    raise SystemExit(main())
