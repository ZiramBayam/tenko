"""Primitif replay Tenko — bekukan snapshot, hitung hash keputusan (ADR-003, ADR-006).

Modul ini PRIMITIF: tidak ada CLI, tidak ada pembacaan tabel ``decisions``, tidak ada logika
preflight. Empat fungsi publik saja — :func:`freeze`, :func:`verify_bundle`,
:func:`params_hash`, :func:`decision_hash` — yang dipakai TASKS 2.1 (menyimpan keputusan) dan
TASKS 2.4 (memutar ulang).

Kenapa hash-nya berbentuk seperti ini (tiap poin punya tes di ``tests/test_replay.py``):

* **``id`` baris TIDAK ikut di-hash.** Bundle harus bisa diverifikasi di mesin lain — juri
  menyalin repo, bukan berkas DB kami — sedangkan ``snapshots.id`` adalah nomor urut lokal
  satu berkas. Dua store dengan isi baris identik tapi id berbeda menghasilkan
  ``decision_hash`` yang SAMA. Konsekuensinya yang harus dibaca pemanggil:
  :attr:`BundleEntry.snapshot_id` adalah **petunjuk pencarian yang TIDAK dilindungi hash** —
  mengubahnya tidak menggeser ``bundle_hash`` maupun ``decision_hash``, dan
  :func:`verify_bundle` tidak akan protes. Sumber kebenaran id adalah kolom
  ``decisions.snapshot_ids_json`` yang tersimpan; TASKS 2.4 wajib membandingkan
  :attr:`Bundle.snapshot_ids` DENGAN kolom itu, bukan mempercayai objek bundle.
* **``payload_json`` dipakai APA ADANYA sebagai string dari DB.** Tidak ada ``json.loads``
  lalu dump ulang. Alasannya bukan selera: setiap teks yang bukan persis bentuk kanonik kami
  — ``1E+2``, ``100.0``, kunci tak terurut, kunci duplikat, integer di luar jangkauan float —
  BERUBAH bentuk saat di-round-trip, sehingga hash-nya bergeser dan replay atas DB yang
  ditulis penulis lain (atau versi perekam lain) akan melaporkan MISMATCH palsu. Yang
  dikanonikalisasi hanyalah WADAH-nya (dict material), memakai
  :func:`core.store.canonical_json` — satu-satunya definisi kanonikalisasi di repo ini.
* **Konstanta versi :data:`ALGO` ikut di-hash.** Kalau kelak bentuk material berubah (butuh
  ADR sendiri), keputusan lama akan menghasilkan hash berbeda. Yang disediakan modul ini
  HANYA konstantanya dan cabang error-nya: :func:`verify_bundle` menolak bundle ber-``algo``
  asing dengan :class:`ReplayError` biasa (bukan :class:`ReplayTamperError`) supaya pemanggil
  bisa melaporkan ``STALE_ALGO``. Kait itu **belum hidup**: tabel ``decisions`` tidak punya
  kolom ``algo``, dan :func:`freeze` selalu memberi ``algo == ALGO``, jadi selama pemanggil
  belum menyimpan ``algo`` bersama keputusan (rencana: di dalam ``card_json``, TASKS 2.1/2.4)
  kenaikan versi ``ALGO`` tetap terbaca sebagai MISMATCH.
* **Dari ``decision`` yang ikut di-hash HANYA ``decision``, ``blocking``, ``item_status``.**
  Narasi, ``summary``, dan ``card_json`` tidak — kalau ikut, AC TASKS 2.3 ("narasi LLM tidak
  mengubah ``decision_hash``") mustahil dipenuhi. Ini kontrak lintas-task.
* **``params`` masuk sebagai :func:`params_hash`, bukan mentah**, supaya kolom
  ``decisions.params_hash`` dan ``decisions.decision_hash`` diturunkan dari satu definisi.

Dua jenis kerusakan dibedakan, karena penyebabnya beda:

1. **Baris snapshot berubah setelah keputusan dibuat** — ``args_hash``/``payload_hash`` tidak
   lagi cocok dengan isi kolomnya. :func:`freeze` dan :func:`verify_bundle` menolaknya dengan
   :class:`ReplayTamperError` yang MENYEBUT ``snapshot_id``.
2. **Objek bundle diubah setelah dibekukan** — tiap entri konsisten sendiri, tapi
   :attr:`Bundle.bundle_hash` tidak lagi cocok dengan isi bundle. Juga
   :class:`ReplayTamperError`, dengan pesan tentang bundle, tanpa ``snapshot_id``.

Membandingkan hasil dengan kolom ``decisions.decision_hash`` yang tersimpan (IDENTICAL vs
MISMATCH) adalah tugas TASKS 2.4, bukan modul ini.

**Batas yang harus dibaca sebelum mengklaim apa pun tentang modul ini** (dibuktikan
tinjauan keamanan, 1 Sep 2026; bukan cacat yang belum diperbaiki, melainkan scope):

* :func:`verify_bundle` menjawab "bundle ini konsisten dengan dirinya sendiri", BUKAN "bukti
  ini benar-benar ada di store". :class:`Bundle` yang dirakit tangan dari
  :class:`BundleEntry` karangan — dengan ``payload_hash`` dan ``bundle_hash`` yang dihitung
  sendiri — akan LOLOS verifikasi dan menghasilkan ``decision_hash`` yang tampak sah.
  Karena itu **satu-satunya konstruktor yang sah adalah** :func:`freeze`, yang membaca baris
  dari store. Siapa pun yang kelak membangun ``Bundle`` dari JSON (API, berkas, argumen CLI)
  harus tahu bahwa asal-usul bukti TIDAK ikut terverifikasi; jalur seperti itu belum ada di
  repo dan tidak boleh ditambahkan tanpa membekukan ulang dari store.
* Yang dideteksi adalah **perubahan PARSIAL atas berkas DB**, bukan anti-tamper. Penulis
  mentah yang bisa mengubah ``payload_json`` sekaligus ``payload_hash`` dan
  ``decisions.decision_hash`` akan membuat replay melaporkan IDENTICAL. Tidak ada MAC
  berkunci maupun jangkar eksternal di sini, dan tidak ada ADR yang menuntutnya. Alasan
  konstruksinya tetap berguna: penulisan parsial (satu kolom, satu tabel, satu baris) jauh
  lebih mungkin terjadi daripada penulisan konsisten menyeluruh.
* **Kasus bernama yang TIDAK dideteksi sama sekali:** ``UPDATE snapshots SET ts_utc``. Kolom
  ``ts_utc`` tidak masuk ke ``args_hash`` maupun ``payload_hash``, jadi :func:`_check_entry`
  yang hanya menghitung ULANG kedua hash itu **LOLOS**, dan :func:`verify_bundle` — yang
  memeriksa entri terhadap dirinya sendiri lalu ``bundle_hash`` terhadap entrinya — juga
  **LOLOS**, meski ``ts_utc`` ikut masuk ke material bundle. Sebabnya: material dihitung dari
  kolom yang SUDAH digeser, bukan dari nilai yang dijaga hash tersimpan. Akibatnya baris
  berumur 3 hari bisa dibuat tampak berumur 3 menit; :meth:`core.store.Store.latest_fresh`
  memilihnya sebagai bukti segar (``unavailable/NO_GO`` bisa menjadi ``partial/GO``), dan
  ``freeze`` membekukan versi yang sudah digeser itu tanpa keberatan. Satu-satunya tempat
  pergeseran itu muncul adalah perbandingan dengan ``decisions.decision_hash`` **yang sudah
  tersimpan** (MISMATCH saat replay keputusan lama) — yaitu **sesudah** kartu dicetak dan
  keputusannya dipakai, bukan sebelum. Dua penutup parsial yang ada/direncanakan berada di
  luar modul ini: batas atas kesegaran :data:`core.store.FUTURE_SKEW_TOLERANCE_S` (TASKS 2.0d,
  hanya menangkap pergeseran ke MASA DEPAN) dan ambang skew ``ts_utc`` vs ``as_of`` — ``as_of``
  ada di dalam ``payload_json`` dan karenanya DIJAGA ``payload_hash`` — pada TASKS 2.2b.
* Nilai kolom yang bertipe BLOB dibaca :class:`core.store.Snapshot` sebagai teks hasil
  ``str(bytes)``, sehingga baris BLOB dan baris TEXT yang isinya sama bisa ber-hash sama.
  Membedakannya menuntut ``typeof()`` — SQL sendiri di dalam modul ini — dan karenanya
  membatalkan sifatnya sebagai primitif yang hanya bergantung pada
  :meth:`core.store.Store.get_snapshots`. Sengaja tidak dilakukan; butuh penulisan mentah
  yang melewati ``Store`` untuk terjadi sama sekali.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, get_args

from core.skill import ItemStatus
from core.store import DECISIONS, Snapshot, Store, StoreError, canonical_json

#: Versi format material hash. Berubah HANYA lewat ADR baru; lihat docstring modul.
ALGO = "tenko-decision-hash/1"

#: Kosakata status item — diturunkan dari `core.skill.ItemResult`, bukan diketik ulang.
ITEM_STATUSES: frozenset[str] = frozenset(get_args(ItemStatus))

#: Kolom baris `snapshots` yang masuk material hash. `id` sengaja TIDAK ada di sini.
HASHED_FIELDS: tuple[str, ...] = (
    "tool",
    "args_json",
    "args_hash",
    "ts_utc",
    "as_of",
    "status",
    "payload_json",
    "payload_hash",
)

#: Kunci `decision` yang ikut di-hash. Kunci lain (narration/summary/card) diabaikan.
DECISION_FIELDS: tuple[str, ...] = ("decision", "blocking", "item_status")


class ReplayError(Exception):
    """Bundle/keputusan tidak bisa dibekukan atau di-hash."""


class ReplayTamperError(ReplayError):
    """Isi tidak cocok dengan hash-nya sendiri: bukti berubah setelah dibekukan."""


class ReplayMissingSnapshotError(ReplayError):
    """`snapshot_ids` menunjuk baris yang tidak ada di store ini."""


def _sha256_text(text: str) -> str:
    """sha256 heksadesimal atas `text`.

    Hasilnya identik dengan :func:`core.store.sha256_hex` untuk setiap teks yang bisa
    di-encode UTF-8 — termasuk 550/550 baris `data/ryo.db` yang ada saat modul ini ditulis.
    Bedanya hanya lone surrogate, yang di sini dilewatkan (`surrogatepass`) alih-alih
    menggagalkan encode. Itu BUKAN untuk kolom DB (SQLite sendiri menolak mengembalikan TEXT
    yang bukan UTF-8 sah, jadi baris seperti itu tidak bisa ada), melainkan untuk `params` /
    `decision`: `sys.argv` memakai `surrogateescape`, sehingga simbol dari CLI bisa membawa
    `\udcff` sampai ke sini. Hash keputusan tidak boleh meledak karena argumen aneh.
    """
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def _canonical(obj: Any, label: str) -> str:
    """Bentuk kanonik `obj`; kegagalan kanonikalisasi jadi :class:`ReplayError` berlabel."""
    try:
        return canonical_json(obj)
    except StoreError as exc:
        raise ReplayError(f"{label} tidak bisa dikanonikalisasi: {exc}") from None


@dataclass(frozen=True, slots=True)
class BundleEntry:
    """Satu baris `snapshots` yang dibekukan; teks JSON-nya apa adanya dari DB."""

    snapshot_id: int
    tool: str
    args_json: str
    args_hash: str
    ts_utc: str
    as_of: str | None
    status: str | None
    payload_json: str
    payload_hash: str

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot) -> BundleEntry:
        return cls(
            snapshot_id=snapshot.id,
            tool=snapshot.tool,
            args_json=snapshot.args_json,
            args_hash=snapshot.args_hash,
            ts_utc=snapshot.ts_utc,
            as_of=snapshot.as_of,
            status=snapshot.status,
            payload_json=snapshot.payload_json,
            payload_hash=snapshot.payload_hash,
        )


@dataclass(frozen=True, slots=True)
class Bundle:
    """Kumpulan snapshot beku + hash isinya; urut `snapshot_id` menaik, tanpa duplikat."""

    algo: str
    entries: tuple[BundleEntry, ...]
    bundle_hash: str

    @property
    def snapshot_ids(self) -> tuple[int, ...]:
        """Id baris asal — jejak untuk kolom `decisions.snapshot_ids_json`, bukan bahan hash."""
        return tuple(entry.snapshot_id for entry in self.entries)


def _text(value: Any, label: str) -> str:
    """Bidang yang akan di-hash wajib `str`; bentuk lain jadi ReplayError, bukan AttributeError."""
    if not isinstance(value, str):
        raise ReplayError(f"{label} harus str, bukan {type(value).__name__}")
    return value


def _reject_non_str_keys(obj: Any, label: str) -> None:
    """Tolak kunci non-`str` di seluruh kedalaman.

    `json.dumps` MEMAKSA kunci `1`, `True`, dan `None` menjadi `"1"`, `"true"`, `"null"`,
    sehingga `{1: "a"}` dan `{"1": "a"}` akan menghasilkan hash yang sama — dua parameter
    berbeda yang tampak identik di pembukuan. Ditolak di batas, bukan didiamkan.
    """
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if not isinstance(key, str):
                raise ReplayError(f"kunci {label} harus str, bukan {type(key).__name__}: {key!r}")
            _reject_non_str_keys(value, f"{label}[{key!r}]")
    elif isinstance(obj, list | tuple):
        for index, value in enumerate(obj):
            _reject_non_str_keys(value, f"{label}[{index}]")


def _entry_material(entry: BundleEntry) -> dict[str, str | None]:
    """Material hash satu entri: seluruh kolom baris KECUALI `id`."""
    return {field: getattr(entry, field) for field in HASHED_FIELDS}


def _bundle_material(entries: Sequence[BundleEntry]) -> dict[str, Any]:
    return {"algo": ALGO, "snapshots": [_entry_material(entry) for entry in entries]}


def _check_entry(entry: BundleEntry) -> None:
    """Periksa tipe bidang, lalu hitung ulang `args_hash`/`payload_hash`; tidak cocok → tamper.

    `BundleEntry` adalah dataclass biasa: ia menerima `payload_json=123` tanpa protes. Tipe
    diperiksa DI SINI supaya kesalahan pemanggil keluar sebagai :class:`ReplayError`, bukan
    `AttributeError` yang menembus sampai ke UI (spec §7: nol traceback).
    """
    _int_field(entry.snapshot_id, "snapshot_id")
    for field in HASHED_FIELDS:
        value = getattr(entry, field)
        if field in ("as_of", "status") and value is None:
            continue
        _text(value, f"snapshot_id={entry.snapshot_id}: {field}")
    for text_field, hash_field in (("args_json", "args_hash"), ("payload_json", "payload_hash")):
        text = getattr(entry, text_field)
        recorded = getattr(entry, hash_field)
        actual = _sha256_text(text)
        if actual != recorded:
            raise ReplayTamperError(
                f"snapshot_id={entry.snapshot_id}: {hash_field} tidak cocok dengan {text_field} "
                f"({recorded} tercatat, {actual} dihitung ulang)"
            )


def _int_field(value: Any, label: str) -> int:
    """Bilangan bulat sungguhan; `bool` ditolak karena `True == 1` akan menyamarkan bug."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReplayError(f"{label} harus bilangan bulat, bukan {type(value).__name__}")
    return value


def freeze(store: Store, snapshot_ids: Iterable[int]) -> Bundle:
    """Bekukan baris `snapshots` untuk `snapshot_ids` menjadi :class:`Bundle`.

    Urutan `snapshot_ids` tidak penting dan duplikatnya tidak berarti apa-apa: keduanya
    dinormalkan ke urut id menaik tanpa duplikat, sehingga bundle (dan hash-nya) hanya
    bergantung pada HIMPUNAN bukti. `snapshot_ids` kosong menghasilkan bundle kosong yang
    tetap sah — keputusan NO_GO ketika semua tool gagal memang tidak punya snapshot.

    Raises:
        ReplayMissingSnapshotError: ada id yang tidak ada di store (append-only: baris yang
            hilang berarti berkas DB-nya bukan yang dipakai saat keputusan dibuat).
        ReplayTamperError: isi baris tidak cocok dengan hash-nya sendiri; pesan menyebut
            `snapshot_id`.
        core.store.StoreError: DB gagal dibaca (itu kegagalan store, bukan replay).
    """
    if isinstance(snapshot_ids, str | bytes) or not isinstance(snapshot_ids, Iterable):
        raise ReplayError(f"snapshot_ids harus daftar, bukan {type(snapshot_ids).__name__}")
    wanted = sorted({_int_field(i, "snapshot_ids") for i in snapshot_ids})
    rows = store.get_snapshots(wanted) if wanted else []
    found = {row.id for row in rows}
    missing = [i for i in wanted if i not in found]
    if missing:
        raise ReplayMissingSnapshotError(f"snapshot tidak ada di store: {missing}")
    entries = tuple(BundleEntry.from_snapshot(row) for row in sorted(rows, key=lambda r: r.id))
    for entry in entries:
        _check_entry(entry)
    return Bundle(
        algo=ALGO,
        entries=entries,
        bundle_hash=_sha256_text(_canonical(_bundle_material(entries), "bundle")),
    )


def verify_bundle(bundle: Bundle) -> str:
    """Periksa KONSISTENSI-DIRI bundle dan kembalikan `bundle_hash` yang dihitung ulang.

    Yang dijawab fungsi ini: "isi bundle ini cocok dengan hash-hash di dalamnya sendiri".
    Yang TIDAK dijawab: "bukti ini benar-benar pernah ada di store". Bundle yang dirakit
    tangan dari `BundleEntry` karangan berikut hash yang dihitung sendiri akan LOLOS di sini
    (lihat "Batas" di docstring modul). Satu-satunya konstruktor `Bundle` yang sah karena itu
    adalah :func:`freeze`, yang membacanya dari store.

    Dua pemeriksaan yang berbeda maknanya: tiap entri harus cocok dengan hash kolomnya
    sendiri (bukti di DB berubah → pesan menyebut `snapshot_id`), lalu `bundle_hash` harus
    cocok dengan isi bundle (objek bundle diubah setelah `freeze`, mis. lewat
    `dataclasses.replace` — tiap entri masih konsisten, bundle-nya yang tidak).

    Raises:
        ReplayError: bukan Bundle, bidang bertipe salah, atau `algo`-nya bukan :data:`ALGO`
            (pemanggil melaporkan yang terakhir sebagai STALE_ALGO, bukan tamper — lihat
            catatan keterbatasannya di docstring modul).
        ReplayTamperError: entri atau bundle tidak cocok dengan hash-nya.
    """
    if not isinstance(bundle, Bundle):
        raise ReplayError(f"bundle harus Bundle, bukan {type(bundle).__name__}")
    if bundle.algo != ALGO:
        raise ReplayError(f"algoritma bundle {bundle.algo!r} bukan {ALGO!r}")
    entries = bundle.entries
    if not isinstance(entries, tuple) or any(not isinstance(e, BundleEntry) for e in entries):
        raise ReplayError("bundle.entries harus tuple BundleEntry")
    ids = [entry.snapshot_id for entry in entries]
    if ids != sorted(set(ids)):
        raise ReplayTamperError(f"entri bundle harus urut id menaik tanpa duplikat, bukan {ids}")
    for entry in entries:
        _check_entry(entry)
    actual = _sha256_text(_canonical(_bundle_material(entries), "bundle"))
    if actual != bundle.bundle_hash:
        raise ReplayTamperError(
            f"bundle_hash tidak cocok dengan isinya ({bundle.bundle_hash} tercatat, {actual} dihitung ulang)"
        )
    return actual


def params_hash(params: Mapping[str, Any]) -> str:
    """Hash parameter keputusan (ambang preflight dsb.); `params` diperlakukan buram.

    Satu-satunya definisi `params_hash` di repo — kolom `decisions.params_hash` (TASKS 2.1)
    dan :func:`decision_hash` sama-sama memakainya, jadi keduanya tidak bisa berbeda diam-diam.
    "Buram" berarti isinya tidak ditafsirkan, BUKAN bahwa apa pun diterima: kunci non-`str`
    ditolak sampai ke kedalaman mana pun (lihat :func:`_reject_non_str_keys`).
    """
    if isinstance(params, str | bytes) or not isinstance(params, Mapping):
        raise ReplayError(f"params harus objek, bukan {type(params).__name__}")
    _reject_non_str_keys(params, "params")
    return _sha256_text(_canonical({"algo": ALGO, "params": dict(params)}, "params"))


def _decision_material(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Ambil TEPAT tiga kunci yang menentukan keputusan; sisanya (narasi) diabaikan."""
    if isinstance(decision, str | bytes) or not isinstance(decision, Mapping):
        raise ReplayError(f"decision harus objek, bukan {type(decision).__name__}")
    missing = [key for key in DECISION_FIELDS if key not in decision]
    if missing:
        raise ReplayError(f"decision kekurangan kunci {missing}; tidak ada default untuk keputusan")

    verdict = decision["decision"]
    if not isinstance(verdict, str) or verdict not in DECISIONS:
        raise ReplayError(f"decision harus salah satu {sorted(DECISIONS)}, bukan {verdict!r}")

    blocking = decision["blocking"]
    if isinstance(blocking, str | bytes) or not isinstance(blocking, Iterable):
        raise ReplayError(f"blocking harus daftar id item, bukan {type(blocking).__name__}")
    items = list(blocking)
    if any(not isinstance(i, str) for i in items):
        raise ReplayError("blocking hanya boleh berisi id item (str)")

    item_status = decision["item_status"]
    if not isinstance(item_status, Mapping):
        raise ReplayError(f"item_status harus objek, bukan {type(item_status).__name__}")
    statuses: dict[str, str] = {}
    for key, value in item_status.items():
        if not isinstance(key, str):
            raise ReplayError(f"kunci item_status harus id item (str), bukan {type(key).__name__}")
        # `value` diuji str DULU: mengoper `ItemResult` mentah (bukan `.status`) atau
        # `{"status": "red"}` akan meledakkan `in` dengan `TypeError: unhashable type`,
        # dan traceback dari jalur keputusan dilarang spec §7.
        if not isinstance(value, str) or value not in ITEM_STATUSES:
            raise ReplayError(
                f"item_status[{key!r}] harus salah satu {sorted(ITEM_STATUSES)}, bukan {value!r}"
            )
        statuses[key] = value

    # `blocking` = HIMPUNAN penyebab veto, bukan urutan tampilan (urutan tampilan hidup di
    # card_json yang memang tidak ikut di-hash), jadi diurutkan & dibuang duplikatnya.
    return {"decision": verdict, "blocking": sorted(set(items)), "item_status": statuses}


def decision_hash(bundle: Bundle, params: Mapping[str, Any], decision: Mapping[str, Any]) -> str:
    """Hash keputusan atas bukti beku — inti janji "replay identik" (ADR-003).

    Bahan: :data:`ALGO`, `bundle.bundle_hash` (yang diverifikasi ulang di sini), hasil
    :func:`params_hash`, dan TIGA kunci `decision` (:data:`DECISION_FIELDS`). Kunci lain di
    `decision` — `narration`, `summary`, `card_json` — tidak mengubah hasil.
    """
    material = {
        "algo": ALGO,
        "bundle_hash": verify_bundle(bundle),
        "decision": _decision_material(decision),
        "params_hash": params_hash(params),
    }
    return _sha256_text(_canonical(material, "decision"))
