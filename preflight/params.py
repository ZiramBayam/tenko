"""Ambang keputusan + tabel kosakata preflight (TASKS 2.1, docs/spec.md §4).

Modul ini adalah SATU-SATUNYA tempat angka ambang dan pemetaan kosakata RYO hidup.
`preflight/items.py` tidak boleh memuat konstanta keputusan sendiri: yang tidak masuk
:meth:`Params.as_dict` tidak ikut ke ``params_hash``, dan ambang yang tidak ikut ke
``params_hash`` membuat keputusan lama tampak bisa di-replay padahal aturannya sudah berubah
(ADR-003, TASKS 5.0).

**Kosakata = pengamatan, BUKAN spesifikasi RYO.** Guide maupun katalog ``/tools`` tidak pernah
menerbitkan enum untuk ``verdict``, ``verdict.call``, ``confluence.state``, atau ``regime``;
tabel di bawah ini berasal dari 697 snapshot perekam kami sendiri (docs/api-facts.md §A,
"Kosakata terukur", api-verifier 1 Sep 2026). Dalam ~13 jam saja MUNCUL DUA nilai yang
sebelumnya "belum pernah terlihat" (``avoid``, ``regime: neutral``), jadi tabel ini bocor
menurut bukti sendiri. Konsekuensi yang ditegakkan kode: nilai di luar tabel →
item ``unspoken`` + warning. Tidak ditebak, tidak dipetakan ke tetangga terdekat, tidak
dijadikan 0/netral, tidak melempar exception.

Tiga aturan bentuk yang diminta api-verifier (docs/api-facts.md §A):

1. **Setiap tabel membawa `field`-nya sendiri** (path PERSIS). ``compare_tokens`` memakai
   kosakata ``deep_analysis.verdict.call``, BUKAN kosakata ``analyze_token.verdict`` — dua
   namespace berbeda, dan menyatukannya dalam satu tabel adalah bug yang nyaris terjadi.
   Karena itu tidak ada tabel "verdict" generik di sini; yang ada tabel per field.
2. **Cocokkan string PENUH lebih dulu.** :meth:`Vocabulary.lookup` hanya mencocokkan string
   utuh. Tidak ada pemotongan di ``" — "`` (spasi + U+2014 + spasi): satu-satunya bentuk
   majemuk yang pernah terlihat (``caution — overbought``) sudah ada utuh di tabel berikut
   qualifier-nya, dan memotong lebih dulu akan menyembunyikan informasi baru — ``avoid —
   <kualifikasi>`` akan terbaca sebagai ``avoid`` polos padahal kualifikasinya belum pernah
   kita lihat.
3. **Setiap baris punya hitungan amatan > 0 — tidak ada baris "spec-only".** Tabel ini
   adalah catatan APA YANG PERNAH DIKELUARKAN RYO, bukan daftar nilai yang masuk akal.
   Dua baris yang dulu dibenarkan oleh teks spec saja sudah diselesaikan 2 Sep 2026:

   * ``change_direction: "up"`` **DICABUT**: 0 dari 20 amatan (sapuan ``id <= 835``,
     2 Sep 2026), dan spec §4 item 4 bahkan tidak pernah menulis string itu (spec hanya
     bilang "searah/berlawanan") — ia murni tebakan kami. Mencabutnya gagal-tertutup:
     satu-satunya cabang yang terpengaruh (``material_shift=true`` + ``up`` + plan long)
     hanya bisa mengubah veto jadi izin.
   * ``regime: "risk_off"`` **DIKEMBALIKAN dengan bukti**, bukan dengan spec: setelah
     sapuan 762 baris menunjukkan 0×, baris ke-763 perekam (``id=763``,
     ``2026-09-01T18:06:48Z``, ``status: ok``) mengeluarkannya untuk pertama kali. Ia kini
     baris amatan biasa. Dampaknya pada keputusan nol untuk plan long — item 3 wajib,
     sehingga ``red`` (berlawanan) dan ``unspoken`` (kosakata asing) sama-sama memveto —
     yang berubah hanya kualitas alasan di kartu: "regime risk_off menentang plan long"
     alih-alih "kosakata tak dikenal".

   Nilai baru masuk ke sini hanya lewat ``@agent-api-verifier`` dengan hitungan + tanggal.
   Satu-satunya baris yang hitungannya 0 **di field-nya sendiri** adalah
   ``compare_tokens…verdict = "cautious"``; ia tetap ada karena api-facts §A menyatakan field
   itu memakai namespace ``deep_analysis.verdict.call`` (14× di sana), dan karena verdict di
   item 10 hanya konteks kartu — ia tidak bisa menggerakkan status item mana pun.

Kalibrasi ambang (dibaca read-only dari ``data/ryo.db``, sapuan 756 baris, 2 Sep 2026;
``sqlite3.connect("file:data/ryo.db?mode=ro", uri=True)``):

* ``confluence``: ``CONFIRMED`` selalu ``score == 1.0`` (169/169) dan ``MIXED`` selalu
  ``0.5`` (125/125). ``min_confluence = 1.0`` berarti "semua gate yang DIJAWAB lulus"; ambang
  di antara 0.5 dan 1.0 memberi keputusan yang sama tetapi menyembunyikan maknanya.

  **Koreksi 5 Sep 2026 (TASKS 0.15).** Kalimat lama di sini berbunyi "dua gate, skor = pecahan
  gate yang lulus". Itu **tidak benar**: sapuan ``data/ryo.db`` menemukan 19 baris (pertama
  ``id=1110``, 2026-09-02T09:08:58Z) dengan ``gates = [{"name": "momentum", "passed": null},
  {"name": "market_activity", "passed": true}]`` dan ``score = 1.0``. Satu gate lulus dari dua
  gate bukan 1.0 — jadi penyebutnya adalah gate yang DIJAWAB, bukan gate yang ada. Kami tidak
  tahu itu sebagai aturan RYO yang diterbitkan (tidak ada skema output; api-facts §A), hanya
  sebagai bentuk yang teramati. Yang berubah karena koreksi ini bukan ambangnya — ``1.0`` tetap
  angka yang sama — melainkan kalimat yang kami ucapkan tentangnya, dan gate yang diam kini
  DISEBUT di kartu (``gates_unspoken``) alih-alih hilang dari kedua daftar.
* ``trade_plan.risk_reward`` = ``[1.0, 2.0]`` di 294/294 baris. ``min_rr = 1.0``:
  ambang di atas 1.0 membuat item 9 merah untuk SETIAP plan yang pernah dikeluarkan RYO —
  item tanpa daya pisah, dan §7 langkah 3 ("≥1 GO dan ≥1 NO_GO") mustahil.
* ``max_age_min = 120`` menit (TASKS 2.2b-lite, 3 Sep 2026) — aturannya satu kalimat:
  **umur maksimum bukti = 2× interval perekam (``RECORDER_INTERVAL_S`` = 3600 s = 60 menit),
  supaya satu siklus perekam boleh terlewat tanpa membuat bukti dianggap basi.** Angka ini
  diturunkan dari periode pengumpulan data, bukan dari hasil keputusan: dengan perekam per jam,
  ``15`` menit menuntut bukti yang lebih muda daripada yang bisa dihasilkan jadwalnya sendiri,
  sehingga item 1 mengukur jadwal kami, bukan kesegaran RYO. Bila ``RECORDER_INTERVAL_S``
  berubah, angka ini dihitung ulang dari aturan yang sama (2× interval). Distribusi umur per
  tool ber-n tetap utang TASKS 2.2b penuh; ia dapat mempersempit angka ini, tidak menggantikan
  aturannya. Lihat :attr:`Params.max_age_min` soal jam acuan item 1.
  **Jangan samakan dengan ``core.store.FUTURE_SKEW_TOLERANCE_S = 120``**: yang itu 120 DETIK
  toleransi ``as_of`` di MASA DEPAN (jam server RYO mendahului jam kami), yang ini 120 MENIT
  batas umur di MASA LALU — sama-sama "120", beda satuan, beda arah, beda tujuan.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Literal

from core.replay import params_hash as _replay_params_hash

# --- id item (spec §4) -------------------------------------------------------------------
# Prefiks angka = urutan daya veto spec §4 dan sekaligus kunci urut yang stabil untuk kartu.
ITEM_FRESHNESS = "01_freshness"
ITEM_COMPLETENESS = "02_completeness"
ITEM_REGIME = "03_regime"
ITEM_SENTIMENT_SHIFT = "04_sentiment_shift"
ITEM_VERDICT_QUICK = "05_verdict_quick"
ITEM_CONFLUENCE = "06_confluence"
ITEM_VERDICT_DEEP = "07_verdict_deep"
ITEM_DERIVATIVES = "08_derivatives"
ITEM_PLAN = "09_plan"
ITEM_RELATIVE = "10_relative"

#: Urutan spec §4 (daya veto menurun). Item 11 (Bunryō) BELUM ada di sini: ia butuh ledger
#: reliabilitas dari `grading/` (TASKS 3.3) yang belum dibangun, dan item yang selalu
#: `unspoken` tanpa sumber hanya menambah baris kosong di kartu.
ITEM_ORDER: tuple[str, ...] = (
    ITEM_FRESHNESS,
    ITEM_COMPLETENESS,
    ITEM_REGIME,
    ITEM_SENTIMENT_SHIFT,
    ITEM_VERDICT_QUICK,
    ITEM_CONFLUENCE,
    ITEM_VERDICT_DEEP,
    ITEM_DERIVATIVES,
    ITEM_PLAN,
    ITEM_RELATIVE,
)

# --- kosakata makna (bahasa internal kita, bukan string RYO) -----------------------------
BULLISH = "bullish"
BEARISH = "bearish"
NEUTRAL = "neutral"
ABSTAIN = "abstain"  # "jangan masuk" — menentang entry ke arah MANA PUN
CALM = "calm"
ALERT = "alert"
CONFIRMED = "confirmed"
MIXED = "mixed"
KNOWN = "known"  # nilai konteks yang dikenal tetapi tidak menggerakkan status item

LONG = "long"
SHORT = "short"

Direction = Literal["long", "short"]


@dataclass(frozen=True, slots=True)
class VocabEntry:
    """Satu nilai string RYO yang sudah pernah diverifikasi + artinya bagi keputusan."""

    meaning: str
    provenance: str
    qualifier: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"meaning": self.meaning, "provenance": self.provenance, "qualifier": self.qualifier}


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """Tabel pemetaan untuk SATU field RYO; `field` adalah penanda namespace-nya.

    `field` bukan hiasan: ia dipakai di `reason`/warning item dan diuji
    (``tests/test_params.py``) supaya tabel ``compare_tokens`` tidak pernah dipakai untuk
    ``analyze_token`` dan sebaliknya.
    """

    field: str
    entries: Mapping[str, VocabEntry]

    def lookup(self, raw: Any) -> VocabEntry | None:
        """Cocokkan string PENUH; apa pun yang lain (termasuk non-str/None) → None.

        Tidak ada normalisasi (strip/lower) dan tidak ada pemotongan di ``" — "``: bentuk
        yang belum pernah kami lihat harus terbaca sebagai belum pernah kami lihat.
        """
        if type(raw) is not str:
            return None
        return self.entries.get(raw)

    def values_with_meaning(self, meaning: str) -> tuple[str, ...]:
        """Nilai mentah yang artinya `meaning` (terurut) — untuk pesan & tes."""
        return tuple(sorted(raw for raw, entry in self.entries.items() if entry.meaning == meaning))

    def as_dict(self) -> dict[str, Any]:
        """Bentuk untuk `params_hash`: mengubah satu pemetaan = mengubah keputusan."""
        return {
            "field": self.field,
            "entries": {raw: entry.as_dict() for raw, entry in sorted(self.entries.items())},
        }


def _vocab(field_path: str, entries: dict[str, VocabEntry]) -> Vocabulary:
    return Vocabulary(field=field_path, entries=MappingProxyType(dict(entries)))


_OBS = "docs/api-facts.md §A 'Kosakata terukur' (697 snapshot, api-verifier 1 Sep 2026)"
#: Sapuan yang BERLAKU (api-verifier, TASKS 0.12). `_OBS` di atas menunjuk sapuan 697 snapshot
#: yang sudah usang; entri yang disentuh 0.12 memakai konstanta ini, sisanya menyusul saat
#: fieldnya diperiksa ulang — angka lama tidak dihapus supaya jejaknya tidak hilang.
SWEEP = "data/ryo.db (mode=ro, sapuan id <= 2165, 4 Sep 2026)"

#: ``analyze_token.data.verdict`` — 365 amatan. Namespace TERPISAH dari verdict deep/compare.
ANALYZE_TOKEN_VERDICT = _vocab(
    "analyze_token.data.verdict",
    {
        "accumulate": VocabEntry(BULLISH, f"{_OBS}: 167×"),
        "neutral": VocabEntry(NEUTRAL, f"{_OBS}: 86×"),
        # Satu-satunya bentuk majemuk yang pernah terlihat; qualifier ikut tercatat, tidak dibuang.
        "caution — overbought": VocabEntry(ABSTAIN, f"{_OBS}: 108×", qualifier="overbought"),
        "avoid": VocabEntry(ABSTAIN, f"{_OBS}: 4× (nilai keempat, baru terlihat 1 Sep 08:45Z)"),
    },
)

#: ``deep_analysis.data.verdict.call`` — 271 amatan, semuanya kata tunggal (0 em dash).
DEEP_ANALYSIS_VERDICT_CALL = _vocab(
    "deep_analysis.data.verdict.call",
    {
        "constructive": VocabEntry(BULLISH, f"{_OBS}: 126×"),
        "neutral": VocabEntry(NEUTRAL, f"{_OBS}: 131×"),
        "cautious": VocabEntry(ABSTAIN, f"{_OBS}: 14×"),
    },
)

#: ``compare_tokens.data.tokens[].verdict`` — tabel SENDIRI meski kosakatanya sama dengan
#: ``deep_analysis.verdict.call`` (api-facts §A menyatakan namespace-nya itu, bukan
#: ``analyze_token``). ``accumulate``/``avoid`` sengaja TIDAK ada di sini: 0/42 amatan.
COMPARE_TOKENS_VERDICT = _vocab(
    "compare_tokens.data.tokens[].verdict",
    {
        "constructive": VocabEntry(BULLISH, f"{_OBS}: 11× dari 42 token"),
        "neutral": VocabEntry(NEUTRAL, f"{_OBS}: 31× dari 42 token"),
        "cautious": VocabEntry(
            ABSTAIN,
            f"{_OBS}: 0× di field ini; namespace-nya = deep_analysis.verdict.call (14× di sana)",
        ),
    },
)

#: ``market_overview.data.regime`` — 20 amatan, TIGA nilai per 2 Sep 2026.
#: ``risk_off`` sempat dicabut hari itu juga karena 0 dari 762 baris (dan karena string itu
#: hanya pernah ada di ``docs/spec.md``, dokumen KAMI), lalu **DIKEMBALIKAN dengan provenance
#: amatan**: baris ke-763 perekam, ``id=763``, ``ts 2026-09-01T18:06:48Z``, ``status: ok``,
#: ``sentiment.label='greed'``, ``market.breadth=0.3846`` — bandingkan ``breadth`` 0.8571–0.9011
#: saat ``risk_on`` dan 0.4835–0.8022 saat ``neutral``. **`label: greed` TIDAK diskriminatif**
#: dan jangan diturunkan jadi aturan: dari 8 amatan ``greed``, 7 berpasangan dengan
#: ``regime: neutral`` dan hanya 1 dengan ``risk_off`` (sapuan ``id <= 835``).
#: Jadi ia bukan lagi baris spec-only, melainkan nilai yang benar-benar dikeluarkan RYO —
#: aturan "hitungan amatan > 0" tetap utuh. Ulangi:
#: ``sqlite3.connect("file:data/ryo.db?mode=ro", uri=True)`` lalu hitung ``data.regime``.
MARKET_REGIME = _vocab(
    "market_overview.data.regime",
    {
        "risk_on": VocabEntry(BULLISH, f"{_OBS}: 6×"),
        "neutral": VocabEntry(NEUTRAL, f"{_OBS}: 13×"),
        "risk_off": VocabEntry(
            BEARISH,
            "data/ryo.db (mode=ro, sapuan id <= 835, 2 Sep 2026): 1× — snapshot id=763, "
            "ts 2026-09-01T18:06:48Z, status ok, sentiment greed, breadth 0.3846 (bandingkan "
            "0.8571–0.9011 saat risk_on); nilai ketiga, sebelumnya 0 dari 762",
        ),
    },
)

#: ``market_overview.data.sentiment.label`` — konteks kartu, tidak menggerakkan status.
SENTIMENT_LABEL = _vocab(
    "market_overview.data.sentiment.label",
    {
        "extreme_greed": VocabEntry(KNOWN, f"{_OBS}: 12×"),
        "greed": VocabEntry(KNOWN, f"{_OBS}: 8× (sapuan id <= 835, 2 Sep 2026)"),
    },
)

#: ``monitor….data.evidence.fear_greed.change_direction`` — HANYA ``down`` yang teramati
#: (20/20 pada sapuan ``id <= 835``, 2 Sep 2026; nilai lain tetap 0×). ``up`` sempat ada di
#: tabel ini sebagai "pasangan wajar" dan DICABUT: spec §4 item 4 tidak pernah menulis string
#: itu, jadi provenance-nya adalah tebakan kami sendiri.
FEAR_GREED_CHANGE_DIRECTION = _vocab(
    "monitor_market_sentiment_shift.data.evidence.fear_greed.change_direction",
    {
        "down": VocabEntry(BEARISH, f"{SWEEP}: 40×"),
        # NEUTRAL, BUKAN KNOWN. Tabel ini dikonsumsi `_classify`, yang tidak punya cabang
        # KNOWN dan akan jatuh ke RED — `flat` akan diam-diam memveto (temuan quant 0.12).
        "flat": VocabEntry(
            NEUTRAL,
            f"{SWEEP}: 2× — id 1976 & 2047, pertama 2026-09-04T11:21:24Z, status ok, "
            "change_7d_points 0 pada keduanya (satu-satunya 0 dari 42 amatan; 'down' selalu "
            "< 0, -3..-11) dan material_shift false; id 2106 kembali 'down'. Nilai kedua, "
            "sebelumnya 0 dari 40",
        ),
    },
)

#: ``monitor….data.evidence.sentiment_regime`` — konteks kartu.
SENTIMENT_REGIME = _vocab(
    "monitor_market_sentiment_shift.data.evidence.sentiment_regime",
    {
        "extreme_greed_unconfirmed": VocabEntry(KNOWN, f"{SWEEP}: 21×"),
        "mixed_sentiment": VocabEntry(KNOWN, f"{SWEEP}: 18×"),
        "complacent_crowding": VocabEntry(
            KNOWN,
            f"{SWEEP}: 3× — pertama snapshot id=1976, ts 2026-09-04T11:21:24Z, status ok; "
            "id 2047 & 2106 menyusul, berpasangan dengan funding 'long_crowding' + pressure "
            "'extreme' pada ketiganya; nilai ketiga, sebelumnya 0 dari 39. Konteks kartu: "
            "tidak menggerakkan status",
        ),
    },
)

#: ``monitor….data.evidence.funding.crowding_state``. Sampai 3 Sep hanya ``normal`` yang pernah
#: terlihat, dan komentar di sini berbunyi "tidak ada baris crowded_* : string itu belum pernah
#: kami lihat". **Kalimat itu sekarang tidak berlaku** (TASKS 0.12, 4 Sep 2026): RYO mengeluarkan
#: ``long_crowding`` — dan perhatikan ejaannya, BUKAN ``crowded_long`` yang pernah kami karang di
#: fixture tes. Nilai yang belum pernah terlihat tetap → unspoken + warning.
#:
#: **"Opsional" membebaskan ``unspoken``, BUKAN ``red``**: ``decide()`` memasukkan setiap item RED
#: ke ``blocking``, dan ``optional_items`` hanya membebaskan cabang UNSPOKEN. Jadi baris ``ALERT``
#: di tabel ini benar-benar bisa memveto.
FUNDING_CROWDING_STATE = _vocab(
    "monitor_market_sentiment_shift.data.evidence.funding.crowding_state",
    {
        "normal": VocabEntry(CALM, f"{SWEEP}: 39×"),
        "long_crowding": VocabEntry(
            ALERT,
            f"{SWEEP}: 3× — id 1976/2047/2106, pertama 2026-09-04T11:21:24Z, status ok, "
            "funding.current_7d_percentile_90d 91.111111 (bandingkan 56.666667–82.222222 pada "
            "39 baris 'normal'); nilai kedua, sebelumnya 0 dari 39. Ejaan RYO 'long_crowding', "
            "BUKAN 'crowded_long' yang pernah kami karang di fixture",
            qualifier="long",
        ),
    },
)

#: ``monitor….data.evidence.liquidation.pressure_state``.
LIQUIDATION_PRESSURE_STATE = _vocab(
    "monitor_market_sentiment_shift.data.evidence.liquidation.pressure_state",
    {
        "normal": VocabEntry(CALM, f"{SWEEP}: 39×"),
        "extreme": VocabEntry(
            ALERT,
            f"{SWEEP}: 3× — id 1976/2047/2106, pertama 2026-09-04T11:21:24Z, status ok, "
            "liquidation.percentile_90d 92.222222 (bandingkan 32.222222–63.333333 pada 39 "
            "baris 'normal'); nilai kedua, sebelumnya 0 dari 39. ALERT karena spec §4 item 8 "
            "menyebut 'pressure ekstrem' sebagai merah TANPA syarat arah",
        ),
    },
)

#: ``deep_analysis.data.confluence.state`` — 271 amatan, dua nilai.
CONFLUENCE_STATE = _vocab(
    "deep_analysis.data.confluence.state",
    {
        "CONFIRMED": VocabEntry(CONFIRMED, f"{_OBS}: 155×"),
        "MIXED": VocabEntry(MIXED, f"{_OBS}: 116×"),
    },
)

#: ``technical_analysis.trend`` — konteks kartu untuk item 5 (analyze_token).
TECHNICAL_TREND = _vocab(
    "analyze_token.data.technical_analysis.trend",
    {
        "up": VocabEntry(KNOWN, f"{_OBS}: 326×"),
        "sideways": VocabEntry(KNOWN, f"{_OBS}: 31×"),
        "down": VocabEntry(KNOWN, f"{_OBS}: 8×"),
    },
)

#: Semua tabel, dikunci nama field-nya. Ikut ke `params_hash` lewat :meth:`Params.as_dict`.
VOCABULARIES: tuple[Vocabulary, ...] = (
    ANALYZE_TOKEN_VERDICT,
    DEEP_ANALYSIS_VERDICT_CALL,
    COMPARE_TOKENS_VERDICT,
    MARKET_REGIME,
    SENTIMENT_LABEL,
    FEAR_GREED_CHANGE_DIRECTION,
    SENTIMENT_REGIME,
    FUNDING_CROWDING_STATE,
    LIQUIDATION_PRESSURE_STATE,
    CONFLUENCE_STATE,
    TECHNICAL_TREND,
)


# --- seksi envelope yang dipakai item 3–10 (docs/api-facts.md §A "availability") ---------
#: Seksi yang HARUS `available` supaya item 3–10 punya bahan. Kunci = nama tool.
REQUIRED_SECTIONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "market_overview": ("market_regime", "market_sentiment", "market_breadth"),
        "monitor_market_sentiment_shift": ("evidence",),
        "analyze_token": ("market_data", "technical_analysis"),
        "deep_analysis": ("market_data", "technical_analysis", "trade_plan"),
        "compare_tokens": ("token_market_data", "comparison_factors"),
    }
)

#: Seksi opsional: `unavailable` di sini HANYA DICATAT (spec §4 item 2 kolom "tidak tersebut").
#: 294/294 baris `deep_analysis` di data/ryo.db punya keduanya `unavailable` meski
#: `include_perp=true` — itulah alasan item 8 memakai bukti market-wide.
OPTIONAL_SECTIONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "deep_analysis": ("token_profile", "derivatives"),
        "compare_tokens": ("token_profiles",),
        "scan_market": ("theme_filter",),
    }
)

#: Tool yang harus menjawab supaya sepuluh item punya bahan (scan_market TIDAK dipakai item).
REQUIRED_TOOLS: tuple[str, ...] = (
    "market_overview",
    "monitor_market_sentiment_shift",
    "analyze_token",
    "deep_analysis",
    "compare_tokens",
)


@dataclass(frozen=True, slots=True)
class Params:
    """Ambang + kebijakan keputusan. Seluruh isinya masuk ``params_hash`` (TASKS 5.0).

    Semua field punya default yang dipakai jalur produksi; tes memakai
    :meth:`with_overrides` supaya cabang yang belum punya kosakata terverifikasi
    (mis. ``crowding_state`` selain ``normal``) tetap bisa dibuktikan tanpa mengarang
    kosakata di jalur nyata.
    """

    #: Umur maksimum bukti (menit) untuk item 1. Jam acuannya diambil dari `as_of` envelope
    #: TERBARU di dalam bukti beku itu sendiri, BUKAN jam lokal: keputusan harus fungsi murni
    #: atas snapshot (ADR-003) — memakai `now()` membuat replay menghasilkan item 1 yang
    #: berbeda seiring waktu. Angka 120 menit = 2× interval perekam (lihat docstring modul);
    #: spec §4 menulis 15, lebih muda daripada yang bisa dihasilkan jadwal perekam per jam.
    max_age_min: int = 120
    #: Item 6: `CONFIRMED` + score ≥ ambang. 1.0 = kedua gate lulus (lihat docstring modul).
    min_confluence: float = 1.0
    #: Item 9: `trade_plan.risk_reward[0]` ≥ ambang.
    min_rr: float = 1.0
    #: Item 10: pembanding untuk `compare_tokens` (simbol + 2 pembanding, spec §4).
    peers: tuple[str, ...] = ("BNB", "ETH")
    #: Keadaan ketiga spec §4 — bukti yang TIDAK berlawanan tetapi juga TIDAK menegaskan arah
    #: plan (`regime: neutral`, `verdict: neutral`/`verdict.call: neutral`, dan padanan
    #: strukturalnya di item 10: terakhir di momentum tetapi tidak di market_activity).
    #: Keadaan ini justru mayoritas di data nyata (regime `neutral` 13/19; `verdict.call`
    #: `neutral` 144/294) dan tidak punya baris di spec §4.
    #: Default `green` — **ADR-017 (USULAN 2 Sep 2026)**, tiga alasannya:
    #: 1. Tiap field RYO SUDAH punya kosakata keberatannya sendiri, teramati terpisah
    #:    (`caution — overbought` 118× · `avoid` 5× · `cautious` 15×). Memetakan `neutral`
    #:    menjadi merah = mengarang keberatan yang sumbernya menolak menyatakan — satu
    #:    keluarga dengan mengarang nilai `crowded` untuk item 8, hanya terbalik arahnya.
    #: 2. Hijau di Tenko berarti "tidak ada keberatan", bukan "mendukung". Spec §4 sendiri
    #:    menulisnya dua kali: item 4 hijau = `material_shift=false` ATAU searah, dan item 10
    #:    merah menuntut terakhir di KEDUA faktor.
    #: 3. `unspoken` ditolak untuk keadaan ini: `core/skill.py` mewajibkan item `unspoken`
    #:    tidak membawa `value`, sehingga jawaban nyata RYO akan hilang dari kartu dan
    #:    `unspoken` berhenti berarti "tidak ada jawaban".
    #: Kompensasinya (ADR-017 poin 5): daya pisah tiap item dilaporkan empiris dengan n
    #: (TASKS 2.1b) — item yang tidak pernah merah disebut apa adanya, bukan dibuat bergigi
    #: dengan menaikkan ambang. Knob ini seragam untuk semua item (tidak ada nilai per item
    #: tanpa bukti yang membedakannya) dan ikut ke `params_hash`; ganti di sini, bukan di
    #: items.py. Status ADR-017 masih USULAN (TASKS 2.1c, keputusan manusia).
    neutral_stance_status: Literal["green", "red"] = "green"
    #: Item yang TIDAK memveto saat `unspoken` (spec §4: item 8 derivatif; item 11 Bunryō
    #: belum dibangun). Item wajib yang `unspoken` masuk `blocking`.
    optional_items: frozenset[str] = frozenset({ITEM_DERIVATIVES})
    #: Tabel kosakata; ikut ke params_hash supaya perubahan pemetaan tidak senyap.
    vocabularies: tuple[Vocabulary, ...] = field(default=VOCABULARIES)

    def __post_init__(self) -> None:
        if self.max_age_min <= 0:
            raise ValueError("max_age_min harus > 0 menit")
        for name in ("min_confluence", "min_rr"):
            value = getattr(self, name)
            if not isinstance(value, int | float) or isinstance(value, bool) or value != value:
                raise ValueError(f"{name} harus angka finit, bukan {value!r}")
        unknown = set(self.optional_items) - set(ITEM_ORDER)
        if unknown:
            raise ValueError(f"optional_items memuat id di luar spec §4: {sorted(unknown)}")
        fields = [vocab.field for vocab in self.vocabularies]
        if len(fields) != len(set(fields)):
            raise ValueError("dua tabel kosakata memakai field yang sama; namespace harus tunggal")
        missing = sorted({vocab.field for vocab in VOCABULARIES} - set(fields))
        if missing:
            # Tabel boleh DIGANTI (tes/kalibrasi), tidak boleh DIHILANGKAN: item yang
            # kehilangan tabelnya akan diam-diam kehilangan cara membaca field itu.
            raise ValueError(f"tabel kosakata hilang untuk field: {missing}")

    def vocabulary(self, field: str) -> Vocabulary:
        """Tabel yang BERLAKU untuk `field` — yang ini pula yang ikut ke `params_hash`.

        `preflight/items.py` membaca kosakata lewat sini, bukan dari konstanta modul, supaya
        tabel yang dipakai memutuskan selalu sama dengan tabel yang di-hash.
        """
        for vocab in self.vocabularies:
            if vocab.field == field:
                return vocab
        raise ValueError(f"tidak ada tabel kosakata untuk field {field!r}")

    def is_required(self, item_id: str) -> bool:
        """Item wajib = yang `unspoken`-nya memveto (spec §4 kolom Wajib)."""
        return item_id not in self.optional_items

    def with_overrides(self, **changes: Any) -> Params:
        """Salinan dengan ambang lain — dipakai tes & kalibrasi, bukan jalur produksi."""
        return replace(self, **changes)

    def as_dict(self) -> dict[str, Any]:
        """Bentuk kanonik untuk ``params_hash`` (kunci str, nilai JSON)."""
        return {
            "max_age_min": self.max_age_min,
            "min_confluence": float(self.min_confluence),
            "min_rr": float(self.min_rr),
            "peers": list(self.peers),
            "neutral_stance_status": self.neutral_stance_status,
            "optional_items": sorted(self.optional_items),
            "item_order": list(ITEM_ORDER),
            "required_tools": list(REQUIRED_TOOLS),
            "required_sections": {
                tool: list(sections) for tool, sections in sorted(REQUIRED_SECTIONS.items())
            },
            "optional_sections": {
                tool: list(sections) for tool, sections in sorted(OPTIONAL_SECTIONS.items())
            },
            "vocabularies": {
                vocab.field: vocab.as_dict() for vocab in sorted(self.vocabularies, key=lambda v: v.field)
            },
        }

    def params_hash(self) -> str:
        """Hash parameter — memakai :func:`core.replay.params_hash`, definisi tunggal repo."""
        return _replay_params_hash(self.as_dict())


#: Parameter jalur produksi. TASKS 5.0 membekukannya sebelum demo/video.
DEFAULT_PARAMS = Params()


def params_hash(params: Params | Mapping[str, Any] = DEFAULT_PARAMS) -> str:
    """`params_hash` untuk :class:`Params` maupun mapping mentah (satu definisi, ADR-003)."""
    if isinstance(params, Params):
        return params.params_hash()
    return _replay_params_hash(params)


def vocabulary_fields(vocabularies: Iterable[Vocabulary] = VOCABULARIES) -> tuple[str, ...]:
    """Nama field yang punya tabel (terurut) — untuk tes & dokumentasi kartu."""
    return tuple(sorted(vocab.field for vocab in vocabularies))
