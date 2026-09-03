"""Penjangkaran item ke bukti beku (TASKS 2.4c-min) — jawaban atas serangan 6 & 8.

`preflight.skill.check_aggregate_floor` menjawab *"envelope ini konsisten dengan
`data["items"]`-nya sendiri?"*. Ia TIDAK menjawab *"`data["items"]` adalah apa yang dikatakan
bukti beku?"* — dan itu bukan dugaan, melainkan dua serangan yang benar-benar dijalankan
security-reviewer atas TASKS 2.2 (3 Sep 2026):

* **Serangan 6.** Balik `03_regime`/`10_relative` dari `red` menjadi `green` di
  `data["items"]`, selaraskan `n_*`/`decision`/`blocking`/`summary`/`availability`, lalu hitung
  ulang `decision_hash` dari bundle store ASLI: hash-nya **COCOK**, karena materialnya memuat
  `item_status` (yang sudah dipalsukan) sementara `bundle_hash` dihitung dari baris yang tidak
  disentuh. Replay akan melaporkan IDENTICAL untuk keputusan yang dibalik.
* **Serangan 8.** Tulis ulang angka `trade_plan` di `data["items"][8]["value"]` **dan** di
  `data["plan"]["value"]` sekaligus. Cek "plan == passthrough" lolos, karena kedua sisinya
  berasal dari item palsu yang sama.

Satu-satunya lapisan yang bisa menutup keduanya adalah lapisan yang **MEMEGANG bundle** —
yaitu pemanggil yang membekukannya. Itulah modul ini. Yang dilakukannya bukan memeriksa
konsistensi, melainkan **menilai ulang kesepuluh item dari `payload_json` beku** dan menuntut
hasilnya sama persis, item per item, `value` per `value`.

Cara kerja, dan mengapa ia bisa dipercaya lebih dari lantai sebelumnya
---------------------------------------------------------------------
1. `Bundle` hanya boleh datang dari `core.replay.freeze()` — ia membaca baris store dan
   menghitung ulang `args_hash`/`payload_hash` tiap entri. Bundle rakitan tangan (lihat
   "Batas" di `core/replay.py`) tidak sah di sini juga; modul ini tidak menyediakan jalan
   membangun bundle dari JSON.
2. Envelope dibaca ULANG dari `entry.payload_json`, dengan aturan penyaringan yang **sama**
   seperti `preflight.skill.gather` (payload tak terbaca / bukan objek / menyangkal dirinya
   sendiri tetapi membawa `data` → tidak dipakai sebagai bukti). Aturan itu disalin, bukan
   diimpor, karena `gather` mengikatnya pada pembacaan store; kesamaannya **dipatok tes**
   (`tests/test_agent_anchor.py::test_recomputation_reproduces_preflight_items`), bukan
   dipercaya.
3. `Evidence` → `evaluate_all` → `decide` dijalankan ulang dengan `params` yang sama, lalu
   tiap `ItemResult` dibandingkan **utuh** (status, value, source, reason) dengan yang ada di
   envelope. Ketidakcocokan menyebut **id item dan `snapshot_id`**-nya.
4. `item.source.snapshot_id` wajib ada di `data["snapshot_ids"]` **DAN** ada di bundle. Ini
   menutup `source` hantu: `core/replay.py` menyatakan `BundleEntry.snapshot_id` tidak
   dilindungi hash, jadi id yang menunjuk baris yang tidak pernah ikut dibekukan harus ditolak
   di sini, bukan dipercaya.

Batas yang diakui (baca sebelum menyebut modul ini "penjaga")
-------------------------------------------------------------
* Yang dijangkarkan adalah **item terhadap payload beku**, bukan payload beku terhadap dunia.
  Penulis mentah yang mengubah `payload_json` **beserta** `payload_hash` (dan `ts_utc`, yang
  memang tidak dijaga hash mana pun — lihat `core/replay.py`) tetap bisa membuat bukti palsu
  yang konsisten. Yang ditutup di sini adalah kebohongan pada lapisan ENVELOPE, yang jauh
  lebih murah dilakukan: ia tidak menuntut akses tulis ke DB sama sekali.
* Modul ini **tidak** memeriksa `n_*`, `availability`, `summary`, `plan == passthrough`, atau
  bentuk pembukuan lain. Itu tetap tugas `check_aggregate_floor`, dan agen memanggil KEDUANYA
  (`agent/main.py`): yang satu memeriksa envelope terhadap dirinya sendiri, yang lain
  memeriksa itemnya terhadap bukti.
* Penilaian ulang memakai `params` yang dioper pemanggil, dan menuntut `params` itu sama
  dengan `data["params_hash"]` — aturan yang sama dengan `check_decision_floor`. Tanpa itu,
  penjangkaran akan menilai dengan ambang yang tidak pernah dipakai menilai apa pun.
* **Kesegaran bukti beku TIDAK diperiksa di sini** (dicatat security-reviewer 4 Sep 2026;
  scope, bukan cacat yang belum diperbaiki — jangan ditambal di modul ini). Bundle berumur
  tiga hari yang konsisten dengan dirinya sendiri menghasilkan `GO` yang lolos penjangkaran:
  item 1 mengukur jarak antar-bukti dengan jam INTERNAL bundle (`preflight/items.py` aturan 5),
  bukan jam dinding. Yang menyaring umur adalah `Store.latest_fresh` **saat bukti dipilih**
  (`params.max_age_min`), yaitu satu lapisan sebelum modul ini, dan `ts_utc` yang menentukan
  pilihan itu tidak dijaga hash mana pun (`core/replay.py`). Konsekuensinya: penjangkaran
  menjawab "item ini benar menurut bukti INI", bukan "bukti ini masih layak dipakai hari ini".
* **Butir 4 (`check_source_ids`) tidak bisa menyala di jalur agen, dan itu ditulis apa adanya.**
  `agent/main.py` membangun bundle DARI `data["snapshot_ids"]` (`freeze(store, snapshot_ids)`),
  jadi kesetaraan "declared == frozen" di sana benar secara konstruksi. Pemeriksaan itu tetap
  ada karena ia mengikat `item.source.snapshot_id` ke himpunan bukti — dan karena pemanggil
  LAIN (UI, grading, replay atas bundle yang dibekukan terpisah) bisa mengoper bundle yang
  bukan turunan `snapshot_ids` envelope. Tesnya memakai bundle yang dirakit terpisah.
* Baris ber-`tool` di luar :data:`preflight.params.REQUIRED_TOOLS` **diabaikan saat menilai**
  tetapi tetap ikut ke `bundle_hash` (`core/replay.py` mem-hash seluruh entri). Artinya bundle
  yang membawa baris asing menghasilkan hash berbeda — terdeteksi sebagai MISMATCH saat
  replay, bukan sebagai bukti tambahan yang diam-diam ikut menilai.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.replay import Bundle, verify_bundle
from core.skill import ANSWERED_STATUSES, ItemResult, SkillResult
from preflight.items import Decision, Evidence, decide, evaluate_all, results_of
from preflight.params import DEFAULT_PARAMS, ITEM_ORDER, REQUIRED_TOOLS, Params

#: Kunci `data` yang dibaca modul ini (nama dari `preflight/skill.py`, tidak diketik ulang
#: sebagai string bebas di dalam fungsi).
DATA_ITEMS = "items"
DATA_SYMBOL = "symbol"
DATA_SNAPSHOT_IDS = "snapshot_ids"
DATA_PARAMS_HASH = "params_hash"

#: Field `ItemResult` yang dibandingkan satu per satu supaya pesan menyebut APA yang berbeda.
COMPARED_FIELDS: tuple[str, ...] = ("status", "value", "reason")


class EvidenceMismatchError(Exception):
    """Item di envelope tidak bisa diturunkan dari bukti beku: envelope ini tidak dipakai."""


@dataclass(frozen=True, slots=True)
class Recomputed:
    """Hasil penilaian ulang dari bundle: item + keputusan + peta tool→snapshot_id."""

    items: tuple[ItemResult, ...]
    decision: Decision
    snapshot_ids: Mapping[str, int]
    envelopes: Mapping[str, Mapping[str, Any]]


def envelopes_from_bundle(bundle: Bundle) -> tuple[dict[str, Mapping[str, Any]], dict[str, int]]:
    """Envelope yang BOLEH dipakai sebagai bukti + id barisnya, dibaca dari bundle beku.

    Aturan penyaringan disalin dari `preflight.skill.gather` (lihat butir 2 docstring modul):
    payload yang bukan JSON objek dilewati, dan envelope yang menyatakan status tidak-menjawab
    tetapi tetap membawa `data` **tidak dipakai sama sekali**.

    Raises:
        EvidenceMismatchError: satu tool punya lebih dari satu baris di bundle. Preflight
            membekukan tepat satu baris per tool; dua baris berarti bundle ini bukan bundle
            yang dihasilkan jalur keputusan, dan memilih salah satunya berarti menebak.
    """
    envelopes: dict[str, Mapping[str, Any]] = {}
    ids: dict[str, int] = {}
    seen: dict[str, int] = {}
    for entry in bundle.entries:
        if entry.tool not in REQUIRED_TOOLS:
            continue
        if entry.tool in seen:
            raise EvidenceMismatchError(
                f"bundle memuat dua baris {entry.tool!r} (snapshot_id {seen[entry.tool]} dan "
                f"{entry.snapshot_id}); satu keputusan membekukan satu baris per tool"
            )
        seen[entry.tool] = entry.snapshot_id
        try:
            envelope = json.loads(entry.payload_json)
        except ValueError:
            continue
        if not isinstance(envelope, Mapping):
            continue
        if envelope.get("status") not in ANSWERED_STATUSES and envelope.get("data") is not None:
            continue
        envelopes[entry.tool] = envelope
        ids[entry.tool] = entry.snapshot_id
    return envelopes, ids


def recompute(symbol: str, bundle: Bundle, params: Params = DEFAULT_PARAMS) -> Recomputed:
    """Nilai ulang kesepuluh item dari bukti beku — fungsi murni atas (bundle, params)."""
    envelopes, ids = envelopes_from_bundle(bundle)
    evidence = Evidence.from_envelopes(symbol, envelopes, params=params, snapshot_ids=ids)
    items = results_of(evaluate_all(evidence))
    return Recomputed(items=items, decision=decide(items, params), snapshot_ids=ids, envelopes=envelopes)


def _snapshot_id_of(item: ItemResult) -> int | None:
    return None if item.source is None else item.source.snapshot_id


def _describe(item: ItemResult) -> str:
    snapshot_id = _snapshot_id_of(item)
    tool = "?" if item.source is None else item.source.tool
    return f"item {item.id!r} (tool {tool}, snapshot_id={snapshot_id})"


def check_source_ids(result: SkillResult, bundle: Bundle) -> None:
    """`item.source.snapshot_id` wajib ∈ `data["snapshot_ids"]` DAN ada di bundle (AC 2.4c-min 2).

    `source` boleh tidak punya `snapshot_id` (item yang tool-nya tidak menjawab menunjuk field
    yang memang tidak pernah dibaca); yang dilarang adalah id yang MENUNJUK ke mana-mana.
    """
    data = result.data
    if data is None:
        return
    declared = data.get(DATA_SNAPSHOT_IDS)
    if not isinstance(declared, Sequence) or isinstance(declared, str | bytes):
        raise EvidenceMismatchError(
            f"data['snapshot_ids'] harus daftar id baris, bukan {type(declared).__name__}"
        )
    declared_ids = {i for i in declared if isinstance(i, int) and not isinstance(i, bool)}
    frozen_ids = set(bundle.snapshot_ids)
    if declared_ids != frozen_ids:
        raise EvidenceMismatchError(
            f"data['snapshot_ids'] {sorted(declared_ids)} bukan bukti yang dibekukan "
            f"{sorted(frozen_ids)}: envelope menyebut bukti lain daripada yang di-hash"
        )
    for item in result.items:
        snapshot_id = _snapshot_id_of(item)
        if snapshot_id is None:
            continue
        if snapshot_id not in declared_ids:
            raise EvidenceMismatchError(
                f"{_describe(item)} menunjuk snapshot_id yang tidak ada di data['snapshot_ids'] "
                f"{sorted(declared_ids)}"
            )
        if snapshot_id not in frozen_ids:
            raise EvidenceMismatchError(
                f"{_describe(item)} menunjuk snapshot_id yang tidak ada di bundle beku {sorted(frozen_ids)}"
            )


def check_against_evidence(
    result: SkillResult,
    bundle: Bundle,
    params: Params = DEFAULT_PARAMS,
    *,
    symbol: str | None = None,
) -> Recomputed:
    """Tolak envelope yang itemnya tidak bisa diturunkan dari `bundle` (TASKS 2.4c-min AC 1–3).

    Dipanggil agen **sebelum** menulis baris `decisions` dan **sebelum** menakar posisi dari
    `plan`. Kembaliannya adalah hasil penilaian ulang, supaya pemanggil memakai angka yang
    sudah dijangkarkan alih-alih angka yang baru saja ia periksa.

    `symbol` = simbol yang SEDANG diputuskan (dari watchlist). Wajib dioper jalur produksi:
    tanpa itu, penjangkaran membaca simbol DARI envelope, sehingga envelope ETH yang lengkap
    dan konsisten dengan buktinya sendiri bisa dipakai untuk keputusan berlabel SOL — baris
    `decisions` `symbol='SOL'` GO dengan angka milik ETH (temuan TINGGI security-reviewer,
    4 Sep 2026, dibuktikan berhasil). Default `None` dipertahankan HANYA untuk pemanggil yang
    memang tidak punya simbol acuan (tes lantai preflight); ia mematikan pemeriksaan ini.

    Raises:
        EvidenceMismatchError: satu item (atau `decision`/`blocking`) berbeda dari hasil
            penilaian ulang atas bukti beku, atau `data["symbol"]` bukan `symbol`. Pesannya
            menyebut id item dan `snapshot_id`.
    """
    if not isinstance(result, SkillResult):
        raise EvidenceMismatchError(f"result harus SkillResult, bukan {type(result).__name__}")
    verify_bundle(bundle)  # bundle yang tidak konsisten dengan dirinya sendiri: berhenti di sini
    data = result.data
    if data is None:
        # Envelope penolakan (`data=None`) tidak membawa item bervonis — `preflight._refusal`
        # menegakkannya — jadi tidak ada yang bisa (dan perlu) dijangkarkan.
        empty = Decision("NO_GO", (), {}, params.params_hash())
        return Recomputed(items=(), decision=empty, snapshot_ids={}, envelopes={})

    expected_params = params.params_hash()
    stored_params = data.get(DATA_PARAMS_HASH)
    if stored_params != expected_params:
        raise EvidenceMismatchError(
            f"params_hash envelope {stored_params!r} bukan {expected_params!r}: ambang yang "
            "menilai dan ambang yang menjangkarkan harus sama"
        )
    envelope_symbol = data.get(DATA_SYMBOL)
    if not isinstance(envelope_symbol, str) or not envelope_symbol:
        raise EvidenceMismatchError(f"data['symbol'] tidak terbaca: {envelope_symbol!r}")
    if symbol is not None and envelope_symbol != symbol:
        raise EvidenceMismatchError(
            f"envelope ini menjawab {envelope_symbol!r}, bukan {symbol!r} yang sedang "
            "diputuskan: bukti satu simbol tidak boleh diberi label simbol lain"
        )

    check_source_ids(result, bundle)

    recomputed = recompute(envelope_symbol, bundle, params)
    actual = {item.id: item for item in result.items}
    expected = {item.id: item for item in recomputed.items}

    if sorted(actual) != sorted(ITEM_ORDER) or sorted(expected) != sorted(ITEM_ORDER):
        raise EvidenceMismatchError(
            f"id item {sorted(actual)} bukan sepuluh baris panggilan {sorted(ITEM_ORDER)}"
        )

    for item_id in ITEM_ORDER:
        got, want = actual[item_id], expected[item_id]
        if got == want:
            continue
        differing = [name for name in COMPARED_FIELDS if getattr(got, name) != getattr(want, name)]
        if not differing:
            # Bedanya di `source` — tool/field/as_of yang tidak sesuai bukti.
            raise EvidenceMismatchError(
                f"{_describe(want)}: source envelope {got.source!r} bukan sumber yang dibaca "
                f"dari bukti beku {want.source!r}"
            )
        details = "; ".join(
            f"{name}={getattr(got, name)!r} di envelope, {getattr(want, name)!r} pada bukti beku"
            for name in differing
        )
        raise EvidenceMismatchError(f"{_describe(want)} tidak cocok dengan bukti beku: {details}")

    stored_decision = data.get("decision")
    stored_blocking = data.get("blocking")
    if stored_decision != recomputed.decision.decision or list(stored_blocking or []) != list(
        recomputed.decision.blocking
    ):
        raise EvidenceMismatchError(
            f"keputusan envelope {stored_decision!r}/{stored_blocking!r} bukan keputusan yang "
            f"diturunkan dari bukti beku {recomputed.decision.decision!r}/"
            f"{list(recomputed.decision.blocking)!r}"
        )
    return recomputed
