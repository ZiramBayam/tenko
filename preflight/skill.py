"""Skill `preflight` (TASKS 2.2, docs/spec.md §3/§5/§7) — panggilan sepuluh item jadi SATU envelope.

`preflight(symbol, intent)` menjawab satu pertanyaan: *boleh berangkat atau tidak, dan siapa
yang menahannya*. Alurnya lurus dan hanya sekali jalan:

1. **resolusi bukti** — SATU pembacaan store per tool (:meth:`core.store.Store.latest_fresh`);
2. **beku** — :func:`core.replay.freeze` atas seluruh baris yang terbaca, SEBELUM satu item
   pun dinilai (bukti yang dinilai = bukti yang di-hash, byte per byte);
3. **nilai** — :func:`preflight.items.evaluate_all` atas bukti beku (fungsi murni);
4. **putuskan** — :func:`preflight.items.decide` + :func:`preflight.items.check_decision_floor`;
5. **rakit** — :meth:`core.skill.SkillResult.from_items` + :func:`check_aggregate_floor`.

Tidak ada LLM di jalur ini (ADR-003): narasi adalah TASKS 2.3 dan tidak boleh menggeser
`decision` maupun `decision_hash`.

Batas yang diakui (baca sebelum memakai modul ini; gaya `core/replay.py`)
-----------------------------------------------------------------------
* **Store-first SAJA — NOL panggilan live.** Modul ini tidak pernah membangun klien RYO, dan
  store-nya dibuka `read_only=True`. Kalau tidak ada snapshot yang cukup segar, jawabannya
  adalah "tidak tersebut", bukan panggilan tool. Alasannya kuota dan waktu, bukan kemalasan:
  satu preflight yang semuanya live = ~60 dtk (api-facts §A "Latensi terukur": `deep_analysis`
  17–34 dtk, `compare_tokens` ≈40 dtk) plus risiko 429 di depan juri, dan kunci pemanggil
  live dipegang perekam (`core/livelock.py`, ADR-006). Fallback live adalah TASKS 2.0b/2.4,
  bukan CLI ini.
* **Tidak menulis apa pun.** Baris `decisions`/ledger adalah TASKS 2.4. `read_only=True`
  membuat itu bukan janji melainkan sifat koneksi.
* **`intent` tidak menggerakkan SATU ambang pun.** Ia hanya (a) masuk `request` dan (b) menjadi
  bagian argumen `compare_tokens` yang dicari di store. Yang terbukti ada di `data/ryo.db`
  adalah **bentuk kuncinya** — `{"symbols": <str>, "intent": "swing"}` di 14/14 baris — bukan
  string `"SOL, BNB, ETH"`, yang hanya ada di `docs/samples/compare_tokens.json` (0 baris DB;
  api-facts §A "`compare_tokens.symbols` — batas & simbol berulang"). Nilai di luar
  :data:`INTENTS` menghasilkan envelope `unavailable` + warning **tanpa membaca satu baris store
  pun**: yang tidak punya bukti terekam tidak dijawab dengan bukti intent lain.
* **`plan` adalah PASSTHROUGH.** Isinya `value` item 9 apa adanya (yang dibaca dari
  `deep_analysis.data.trade_plan`) berikut `source`+`as_of`-nya. Nol aritmetika: entry/stop/
  target/ukuran posisi/ATR adalah TASKS 2.4.
* **KESEGARAN TIDAK DIJAGA OLEH APA PUN YANG BISA DIVERIFIKASI ULANG** (temuan TINGGI
  security-reviewer, 3 Sep 2026; scope, bukan cacat yang sudah diperbaiki — perbaikannya ada di
  `core/store.py`/`core/replay.py`, sudah dipecah product-manager menjadi TASKS 2.0d + 2.2b).
  Yang menentukan bukti mana yang terpilih adalah kolom `snapshots.ts_utc`, dan:

  - `ts_utc` **tidak dijaga hash mana pun**. `core.replay._check_entry` menghitung ulang
    `args_hash` dan `payload_hash` saja; satu `UPDATE snapshots SET ts_utc=…` (payload utuh)
    mengubah keputusan dari `unavailable`/NO_GO menjadi `partial`/GO dan `verify_bundle` TETAP
    LOLOS. Dibuktikan reviewer, bukan diduga.
  - `latest_fresh` memilih dengan `ORDER BY ts_utc DESC`. Sisi MASA DEPAN sudah **ditutup
    TASKS 2.0d**: baris lebih dari `core.store.FUTURE_SKEW_TOLERANCE_S` (120) detik di depan
    `now` dibuang dan dicatat ber-`snapshot_id`, jadi jam perekam yang melompat maju tidak lagi
    menang selamanya tanpa jejak. Sisi MUNDUR tetap terbuka sampai TASKS 2.2b: `ts_utc` yang
    digeser ke belakang, ke dalam jendela, membuat bukti tua tampak baru tanpa satu keberatan
    pun dari lapisan mana pun.
  - Item 1 **bukan** lapisan kedua: ia mengukur jarak antar-bukti dengan jam internal bundle
    (modul ini sengaja tidak menyuntikkan `reference_as_of`, `preflight/items.py` aturan 5),
    jadi bukti bertanggal +10 tahun tetap hijau. Konsekuensi lain dari desain yang sama: pada
    jalur store-first item 1 praktis tidak pernah bisa merah karena basi — yang basi tidak ikut
    terbaca, dan yang muncul adalah item `unspoken` karena tool-nya tidak punya envelope.
  - **Yang pernah berbohong adalah docstring ini, BUKAN kartunya** (klaim palsu itu dihapus
    3 Sep 2026; batas atas kesegarannya sendiri menyusul di 2.0d). `reason` item 1
    sudah menyebut batasnya sendiri dengan kalimat lengkap ("... within the limit OF EACH OTHER;
    the clock is the newest as_of in this bundle …, not the wall clock, so this says nothing
    about how old the bundle itself is", `preflight/items.py`). Yang salah adalah klaim di sini
    bahwa ada "lapisan lain" yang menjaga umur terhadap dunia; lapisan itu tidak ada.
  - Yang TERSISA sebagai pertahanan nyata: `as_of` — ia ada DI DALAM `payload_json`, jadi ikut
    dijaga `payload_hash` yang dihitung ulang `core.replay._check_entry` — dan `bundle_hash`
    yang akan MISMATCH saat replay. Keduanya bekerja SETELAH keputusan diambil, bukan
    mencegahnya. `ts_utc`, yang MEMILIH bukti, tidak dijaga hash mana pun.

  Jangan menulis di mana pun bahwa preflight menjamin umur bukti terhadap dunia nyata.
  Perbaikannya milik task lain, jangan dikerjakan di sini: batas ATAS kesegaran di
  `core/store.py` = **TASKS 2.0d** (core-engineer); ambang skew `ts_utc − as_of` dan kalibrasi
  `max_age_min` ber-n = **TASKS 2.2b**.
* **Determinisme punya satu celah yang diketahui: jam resolusi.** Dua panggilan atas store yang
  sama menghasilkan envelope byte-identik SELAMA tidak ada baris yang melewati batas kesegaran
  di antara keduanya. Setelah bukti terpilih, seluruh sisa jalur adalah fungsi murni atas
  (bukti beku, params) — itulah yang dijamin `decision_hash`. Tes AC3 memakai `now=` tetap.
* **Envelope yang mengaku tidak menjawab TIDAK dipakai sebagai data.** Baris ber-`status`
  `unavailable`/tak dikenal tetap ikut ke bukti supaya item 2 bisa MENYEBUT namanya, tetapi
  hanya bila `data`-nya `null` (bentuk yang dijanjikan api-facts §A dan ditegakkan
  `core/skill.py`). Baris yang menyangkal dirinya sendiri TETAPI membawa `data` tidak dipakai
  sama sekali (warning menyebut tool + status): mengambil angka dari envelope yang menyatakan
  gagal adalah persis kebohongan yang produk ini ada untuk mencegahnya.

Angka agregat dan `n` (AC 2.2 (a)–(e), warisan review TASKS 2.2a)
----------------------------------------------------------------
`core/skill.py` sengaja TIDAK menjaga angka di luar `data["items"]`; lapisan inilah yang wajib
menyediakan `n`-nya sendiri. :func:`check_aggregate_floor` menegakkannya secara MEKANIS atas
envelope yang sudah jadi — dan dipanggil `preflight` sendiri sebelum mengembalikan apa pun:

* ``n_spoken`` = cacah entri ``data["items"]`` ber-``status != "unspoken"``, dihitung ulang
  dari envelope (bukan dari ``len(ITEMS)``, bukan dari cacah tool);
* ``n_items`` selalu berdampingan dengan ``n_spoken`` — tidak ada angka agregat telanjang;
* invarian ``0 <= n_spoken <= n_items``;
* ``n_spoken == 0`` → **tanpa `plan`, tanpa skor**: `decision` "NO_GO" + `blocking` + `reason`;
* ``data`` berstatus menjawab TANPA kunci ``items`` ditolak — celah "tidak ada items berarti
  tidak ada yang diperiksa" ditutup di sini, bukan dibiarkan sebagai asimetri;
* kunci ``availability`` yang bukan id item dinilai maupun nama seksi yang dikenal
  (:data:`KNOWN_SECTIONS`) ditolak, dan item yang HILANG dari ``availability`` juga ditolak
  (dua arah).

Angka yang konsisten belum berarti jujur — itu temuan TINGGI security-reviewer (3 Sep 2026):
lima serangan yang menyelaraskan seluruh ``n_*`` tetap lolos versi pertama lantai ini (GO
dengan item merah · ``blocking`` dikosongkan · item merah DIHAPUS lalu ``n_*`` dihitung ulang ·
item karangan disisipkan supaya ``n_spoken`` bukan nol · ``plan`` diganti angka lain). Karena
itu lantai ini sekarang **menghitung ulang vonisnya**, bukan memeriksa konsistensi angka saja:
id item wajib TEPAT :data:`preflight.params.ITEM_ORDER`, ``decision``/``blocking``
direkonstruksi dengan :func:`preflight.items.decide`, dan ``plan`` wajib sama persis dengan
:func:`_plan_passthrough`. Kelima serangan itu ada sebagai tes.

**Yang TIDAK dicapai dengan itu, dan jangan pernah ditulis seolah tercapai** (temuan TINGGI
ronde kedua, 3 Sep 2026): batas kepercayaan tidak hilang, ia **berpindah** dari
``data["decision"]`` ke ``data["items"]``. Lihat "Batas yang diakui" di bawah — lantai ini
menjawab "envelope ini konsisten dengan itemnya sendiri", BUKAN "item ini adalah apa yang
dikatakan bukti beku".

Batas yang diakui untuk :func:`check_aggregate_floor` — baca sebelum menyebutnya "penjaga"
-------------------------------------------------------------------------------------------
Pertanyaan yang DIJAWAB lantai ini: *"envelope ini konsisten dengan `data["items"]`-nya
sendiri?"*. Pertanyaan yang TIDAK dijawabnya, pada kedalaman berapa pun: *"`data["items"]`
adalah apa yang dikatakan bukti beku?"*. Konsekuensinya dibuktikan security-reviewer dengan
serangan yang benar-benar dijalankan (3 Sep 2026), dan semuanya LOLOS lantai ini:

* **Membalik status item membalik seluruh keputusan, dan setiap penjaga di sini ikut patuh.**
  Ubah ``03_regime``/``10_relative`` menjadi ``green`` di ``data["items"]``, hitung ulang
  ``n_*``, lalu biarkan lantai ini bekerja: :func:`preflight.items.decide` hanya membaca
  ``result.status``, jadi ia merekonstruksi ``GO``/``blocking=[]`` yang PERSIS sama dengan yang
  dipalsukan. Envelope keluar berbunyi "10 of 10 spoken, 0 red" sementara ``payload_json`` beku
  di store masih berbunyi ``regime: "risk_off"``.
* **Kesetaraan ``plan`` tidak menolong bila kedua sisi dipalsukan bersamaan.** ``plan`` diikat
  ke :func:`_plan_passthrough` atas item YANG SAMA; mengganti ``entry``/``stop``/``targets``
  di ``data["items"][8]["value"]`` DAN di ``data["plan"]["value"]`` sekaligus tetap lolos.
  Yang ditutup cek kesetaraan itu hanyalah ``plan`` yang tidak sinkron dengan itemnya.
* **``decision_hash`` ikut berbohong, jadi ia bukan jaring pengaman.** Materialnya
  ``(decision, blocking, item_status, bundle_hash, params_hash)`` — ``item_status`` diambil
  dari item yang sudah dipalsukan, sedangkan ``bundle_hash`` dihitung dari baris store yang
  ASLI dan karena itu tetap cocok. Replay (TASKS 2.4/2.4a) akan melaporkan **IDENTICAL untuk
  keputusan yang dibalik**.

Alasannya scope, bukan kemalasan: penjangkaran ber-bukti menuntut pembandingan ulang tiap item
terhadap ``payload_json`` di dalam bundle, dan satu-satunya lapisan yang MEMEGANG bundle adalah
pemanggil yang membekukannya. **Lapisan itu sekarang ADA:**
:func:`agent.anchor.check_against_evidence` (TASKS 2.4c-min) menilai ulang kesepuluh item dari
payload beku dan MENOLAK kedua serangan di atas, menyebut id item + ``snapshot_id``. Agen
memanggilnya sebelum menulis baris ``decisions`` dan sebelum menakar posisi dari ``plan``
(``agent/main.py``). **Yang TIDAK berubah adalah batas fungsi di modul ini**: ia tetap
meloloskan kedua serangan, karena pertanyaannya memang lain — dan justru itu yang di-assert
kedua arah oleh ``tests/test_preflight_skill.py`` (dulu ``test_acknowledged_gap_*``, dibalik di
commit TASKS 2.4; pola 2.2a dan 1.6) serta ``tests/test_agent_anchor.py``. Siapa pun yang
memakai envelope ini tanpa penjangkaran tetap memikul celah itu utuh.

Kontrak keluaran untuk TASKS 2.3 (narasi) dan 2.4 (agen)
--------------------------------------------------------
* Ada bukti yang menjawab (``status`` ``ok``/``partial``) → ``data`` memuat ``items``,
  ``decision``, ``blocking``, ``n_items``/``n_spoken``/``n_green``/``n_red``/``n_unspoken``,
  ``snapshot_ids``, ``params_hash``, ``decision_hash``, ``store_reads``, ``reason``, dan ``plan``
  (hanya bila item 9 tersebut).
* Tidak ada satu pun tool yang menjawab → ``status == "unavailable"`` dan ``data is None``
  (kontrak `core/skill.py` 1.3). ``decision``/``blocking`` tetap terbaca — di ``summary`` —
  tetapi ``decision_hash`` TIDAK ikut: bundle kosong tidak membuktikan apa pun, dan pemanggil
  yang perlu menyimpan barisnya menghitung sendiri dari bundle yang ia bekukan.
* ``summary["decision"]``/``summary["blocking"]`` adalah cermin yang dibaca mesin, bukan hiasan:
  narasi (TASKS 2.3) boleh mengganti ``headline``/``key_points`` tetapi **wajib mempertahankan
  keduanya** — :func:`check_aggregate_floor` menolak envelope yang ``summary``-nya berbeda
  pendapat dengan ``data``.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.replay import Bundle, ReplayError, decision_hash, freeze
from core.skill import (
    ANSWERED_STATUSES,
    AVAILABLE,
    ENVELOPE_STATUSES,
    KEY_LIMIT,
    SKILL_MODULES,
    TEXT_LIMIT,
    UNAVAILABLE,
    UNSPOKEN,
    ItemResult,
    SkillError,
    SkillResult,
    clean_text,
    derive_status,
    register_skill,
)
from core.store import DECISIONS, Store, StoreError, sha256_hex
from preflight.items import (
    TOOL_ANALYZE,
    TOOL_COMPARE,
    TOOL_DEEP,
    TOOL_MARKET_OVERVIEW,
    TOOL_MONITOR,
    Decision,
    DecisionFloorError,
    Evidence,
    ItemOutcome,
    check_decision_floor,
    decide,
    evaluate_all,
    results_of,
    warnings_of,
)
from preflight.params import (
    DEFAULT_PARAMS,
    ITEM_ORDER,
    ITEM_PLAN,
    OPTIONAL_SECTIONS,
    REQUIRED_SECTIONS,
    REQUIRED_TOOLS,
    Params,
)

log = logging.getLogger("preflight.skill")

#: Nama skill di registry dan di `SkillResult.tool`.
TOOL = "preflight"

#: `intent` yang dilayani. Enum RYO sendiri lebih luas (`swing|hold|spot`, api-facts §A
#: inputSchema), tetapi hanya `swing` yang PUNYA snapshot terekam (`data/ryo.db`: 14/14 baris
#: `compare_tokens` ber-`intent: "swing"`), dan jalur ini store-first. Melayani `hold` berarti
#: menjawab dengan bukti yang dikumpulkan untuk pertanyaan lain.
DEFAULT_INTENT = "swing"
INTENTS: frozenset[str] = frozenset({DEFAULT_INTENT})

#: Batas simbol — SALINAN SENGAJA dari `core/recorder.py` (`MAX_SYMBOL_LEN`,
#: `FORBIDDEN_SYMBOL_CATEGORIES`), dengan alasan yang ditulis di :func:`normalize_symbol`.
MAX_SYMBOL_LEN = 64
FORBIDDEN_SYMBOL_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
#: Pemisah daftar simbol `compare_tokens`. Koma+spasi adalah bentuk yang terlihat di 14/14
#: baris DB (mis. `"SOL, BTC, ETH"`) dan di `docs/samples/compare_tokens.json`
#: (`"SOL, BNB, ETH"` — string itu sendiri 0 baris di DB). `catalog.json` menyebut
#: "comma- or space-separated", jadi pemisah ini pilihan yang teramati, bukan satu-satunya sah.
SYMBOL_SEPARATOR = ", "

#: Nama seksi envelope yang DIKENAL lapisan ini — satu-satunya kunci `availability` di luar id
#: item yang boleh muncul (AC 2.2 (e)). Diturunkan dari `preflight/params.py`, bukan diketik
#: ulang, supaya seksi yang ditambahkan di sana tidak perlu diingat dua kali.
KNOWN_SECTIONS: frozenset[str] = frozenset(
    section for sections in (*REQUIRED_SECTIONS.values(), *OPTIONAL_SECTIONS.values()) for section in sections
)

#: Berapa banyak warning yang DICETAK; jumlah aslinya tetap disebut (konvensi repo:
#: `core/skill_cli.py` MAX_ARGS + `args_total`). Membuang warning = menyembunyikan kegagalan.
MAX_WARNINGS = 12
#: Berapa banyak alasan penahan yang masuk `summary.key_points`.
MAX_KEY_POINTS = 5
#: Batas cacah entri daftar di dalam `plan` (`targets`, `risk_reward`) — angka yang sama dengan
#: `preflight.items.MAX_ENTRIES`, dengan jumlah aslinya tetap disebut (`<nama>_total`).
MAX_PLAN_ENTRIES = 8

#: Kunci `data` yang dirakit lapisan ini (di luar `items` yang dirakit `from_items`).
DATA_DECISION = "decision"
DATA_BLOCKING = "blocking"
DATA_PLAN = "plan"
DATA_ITEMS = "items"
DATA_N_ITEMS = "n_items"
DATA_N_SPOKEN = "n_spoken"
DATA_N_GREEN = "n_green"
DATA_N_RED = "n_red"
DATA_N_UNSPOKEN = "n_unspoken"
#: Pembukuan bukti di `data` — ikut diperiksa lantai supaya tidak bisa hilang/bertipe salah/
#: bertentangan satu sama lain (temuan SEDANG security-reviewer ronde kedua).
DATA_SYMBOL = "symbol"
DATA_SNAPSHOT_IDS = "snapshot_ids"
DATA_PARAMS_HASH = "params_hash"
DATA_DECISION_HASH = "decision_hash"
DATA_REASON = "reason"
#: Catatan resolusi store — nama SENGAJA bukan "evidence": `data.evidence` adalah seksi
#: `monitor_market_sentiment_shift` yang nyata (api-facts §A), dan kunci kami tidak boleh
#: menyamar sebagai field RYO.
#:
#: Klaim "nol tabrakan nama" tidak berlaku mutlak, dan itu disebut di sini alih-alih didiamkan:
#: `data["intent"]` (dirakit :func:`preflight`) BERTABRAKAN dengan `compare_tokens.data.intent`
#: yang nyata — level sama, makna sama (`"swing"`), jadi tabrakan itu dibiarkan dengan sengaja;
#: mengganti namanya justru membuat pembaca mengira keduanya beda hal. `data["symbol"]` tidak
#: bertabrakan (RYO selalu menyarangkannya: `tokens[].symbol`, `coverage_by_token.<SYM>`).
DATA_STORE_READS = "store_reads"

#: Envelope pengganti untuk tool yang TIDAK menjawab. Bukan envelope RYO dan tidak pernah ikut
#: ke keluaran: ia hanya membawa satu fakta — "tool ini tidak menjawab" — ke
#: `core.skill.derive_status`, supaya 1 dari 5 tool yang menjawab terbaca `partial`, bukan `ok`.
_ABSENT_ENVELOPE: Mapping[str, Any] = {"status": UNAVAILABLE, "data_mode": "unknown"}


class PreflightError(SkillError):
    """Kegagalan perakitan preflight (bug lapisan ini), bukan bukti yang jelek.

    Turunan :class:`core.skill.SkillError` supaya `core/skill_cli.py` boleh MENCETAK pesannya:
    teksnya kami tulis sendiri dan tidak memuat bahan dari luar.
    """


def normalize_symbol(raw: Any) -> str | None:
    """Simbol yang bisa dipakai sebagai KUNCI PENCARIAN store, atau None.

    Aturannya sengaja sama dengan `core.recorder._normalize_symbol` (strip → upper; tolak
    kosong/terlalu panjang/berspasi/kontrol/format/surrogate) karena baris yang dicari di sini
    adalah baris yang ditulis perekam itu: normalisasi yang berbeda = pencarian yang tidak
    pernah ketemu. Ia DISALIN, bukan diimpor, karena `core.recorder` adalah daemon (ia menarik
    klien HTTP dan `load_dotenv` ke dalam proses CLI); pelajaran TASKS 1.5b dijawab dengan tes
    yang MEMATOK kedua fungsi memberi jawaban sama (`tests/test_preflight_skill.py`), bukan
    dengan kepercayaan.

    Satu aturan TAMBAHAN di sini: koma ditolak. Daftar `compare_tokens.symbols` dipisah koma
    (`"SOL, BNB, ETH"`), jadi simbol ber-koma menyelundupkan token tambahan ke argumen.
    """
    if not isinstance(raw, str):
        return None
    symbol = raw.strip().upper()
    if not symbol or len(symbol) > MAX_SYMBOL_LEN or any(ch.isspace() for ch in symbol):
        return None
    if "," in symbol:
        return None
    if any(unicodedata.category(ch) in FORBIDDEN_SYMBOL_CATEGORIES for ch in symbol):
        return None
    return symbol


def tool_args(symbol: str, intent: str, params: Params = DEFAULT_PARAMS) -> dict[str, dict[str, Any]]:
    """Argumen per tool yang dipakai MENCARI snapshot — bukan argumen yang dikirim ke API.

    Kuncinya: `snapshots.args_json` menyimpan argumen yang **DIKIRIM**, sedangkan `request` di
    dalam envelope adalah argumen **TERNORMALISASI** yang dikembalikan server. Keduanya berbeda
    (api-facts §A "Argumen yang DIKIRIM ≠ `request` yang DIKEMBALIKAN": monitor dikirim `{}`
    menjawab `request: {"time_window": "7d"}` di 26/26 baris), jadi `market_overview` dan
    `monitor_market_sentiment_shift` WAJIB dicari dengan `{}` — bentuk yang ditulis
    `core/recorder.py` — dan bukan dengan `request` dari `docs/samples/*.json`. Untuk
    `analyze_token`/`deep_analysis`/`compare_tokens` keduanya kebetulan sama.

    `args_hash` dihitung dari kanonikalisasi (kunci terurut), jadi urutan kunci di sini tidak
    berpengaruh — urutan ISI string `symbols` tetap berpengaruh (lihat :func:`_symbol_list`).

    **Simbol yang sudah ada di `params.peers` tidak diulang** (:func:`_symbol_list`): dua dari
    empat simbol inti ADALAH pembanding (`peers = ("BNB", "ETH")`), sehingga tanpa dedupe
    `preflight("BNB")` meminta perbandingan `"BNB, BNB, ETH"` — satu token dibandingkan dengan
    dirinya sendiri. Alasannya SEMANTIK, bukan kecocokan args: bentuk ber-dedupe pun 0 baris di
    `data/ryo.db` hari ini (`core/recorder.py` belum memanggil `compare_tokens` sama sekali —
    itu TASKS 2.0c), jadi "supaya cocok dengan baris perekam" tidak memisahkan apa pun.
    `docs/samples/catalog.json` menyebut `symbols` sebagai **string** "2-4 token symbols to
    compare, comma- or space-separated" tanpa `minItems`/`maxItems`/`pattern` dan tanpa satu
    kata pun soal keunikan; perilaku RYO terhadap simbol berulang **BELUM DIVERIFIKASI** (0
    amatan), jadi jangan dibaca sebagai "ditolak API". Yang dijaga di sini adalah pertanyaannya
    tetap masuk akal dan cacahnya tetap di dalam rentang 2–4 yang didokumentasikan.

    Fungsi ini TIDAK ikut ke `params_hash` — ia bukan bagian `Params.as_dict`; yang berubah
    hanya baris store mana yang dicari.
    """
    return {
        TOOL_MARKET_OVERVIEW: {},
        TOOL_MONITOR: {},
        TOOL_ANALYZE: {"symbol": symbol},
        TOOL_DEEP: {"symbol": symbol, "include_perp": True},
        TOOL_COMPARE: {
            "symbols": _symbol_list(symbol, params.peers),
            "intent": intent,
        },
    }


def _symbol_list(symbol: str, peers: Sequence[str]) -> str:
    """`"SOL, BNB, ETH"` — simbol lebih dulu, pembanding menyusul, tanpa duplikat.

    Dedupe mempertahankan urutan (bukan `set`): `args_hash` dihitung dari teksnya, jadi
    himpunan yang sama dengan urutan berbeda adalah baris store yang berbeda. Alasan dedupe ada
    di :func:`tool_args` — semantik perbandingan + rentang 2–4 `catalog.json`, BUKAN kecocokan
    dengan baris perekam.
    """
    out: list[str] = []
    for name in (symbol, *peers):
        if name not in out:
            out.append(name)
    return SYMBOL_SEPARATOR.join(out)


@dataclass(frozen=True, slots=True)
class ToolRead:
    """Hasil resolusi SATU tool: baris mana yang dibaca, dan apakah ia dipakai sebagai bukti."""

    tool: str
    snapshot_id: int | None = None
    status: str | None = None
    as_of: str | None = None
    used: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Bentuk untuk `data["store_reads"]["tools"]` — teks RYO dibatasi panjang & charset."""
        return {
            "snapshot_id": self.snapshot_id,
            "status": None if self.status is None else clean_text(self.status, KEY_LIMIT),
            "as_of": None if self.as_of is None else clean_text(self.as_of, KEY_LIMIT),
            "used_as_evidence": self.used,
            "note": clean_text(self.note, TEXT_LIMIT),
        }


#: Dua simbol yang tidak mungkin dipakai siapa pun (NUL ditolak `normalize_symbol`). Dipakai
#: :func:`symbol_tools` sebagai pembanding; dua, bukan satu, supaya fungsinya tetap benar
#: seandainya seseorang memanggilnya DENGAN salah satu sentinel.
_SENTINEL_SYMBOLS = ("\x00SENTINEL-A", "\x00SENTINEL-B")


def symbol_tools(symbol: str, intent: str = DEFAULT_INTENT, params: Params = DEFAULT_PARAMS) -> set[str]:
    """Tool yang argumen PENCARIANnya BERGANTUNG pada `symbol` — diturunkan dari :func:`tool_args`.

    Pertanyaannya "apakah argumen tool ini berubah bila simbolnya berubah", dijawab dengan
    membangun argumen untuk simbol pembanding lalu membandingkan strukturnya. Tidak didaftar
    ulang di sini: kalau `tool_args` berubah, himpunan ini ikut berubah sendiri.

    **Bukan uji substring atas teks JSON-nya** (TASKS 0.13, temuan TINGGI security-reviewer).
    `json.dumps({}) == "{}"`, jadi `"{}" in json.dumps(args)` menjawab True untuk
    `market_overview` dan `monitor_market_sentiment_shift` — yang dicari dengan `{}` dan justru
    market-wide. Akibatnya simbol `{}`, `{`, atau `}` — ketiganya lolos `normalize_symbol` —
    dianggap punya bukti ber-simbol, menulis baris `decisions` permanen, dan di-replay sebagai
    `IDENTICAL`: persis lubang yang task ini seharusnya menutup. Uji substring juga akan salah
    untuk simbol yang kebetulan sama dengan nilai argumen lain (mis. `"7d"`).
    """
    if not isinstance(symbol, str):
        # Fail-closed seperti `normalize_symbol`: pemanggil yang salah tipe tidak boleh
        # menjatuhkan seluruh tick lewat TypeError (temuan RENDAH security-reviewer 0.13).
        return set()
    mine = tool_args(symbol, intent, params)
    other_symbol = _SENTINEL_SYMBOLS[1] if symbol == _SENTINEL_SYMBOLS[0] else _SENTINEL_SYMBOLS[0]
    other = tool_args(other_symbol, intent, params)
    return {tool for tool, args in mine.items() if args != other.get(tool)}


def symbol_evidence(data: Mapping[str, Any] | None, symbol: str) -> list[str]:
    """Tool ber-simbol yang benar-benar dipakai sebagai bukti untuk `symbol`, terurut.

    Kosong berarti satu hal yang tajam: envelope ini berdiri **hanya** di atas bukti
    market-wide, dan tidak ada satu baris pun di store yang berbicara tentang simbol ini.

    **Batas yang diakui:** ia memakai `intent`/`params` DEFAULT saat menurunkan tool ber-simbol,
    sementara envelope yang diperiksa bisa saja dirakit dengan yang lain. Hari ini tak
    terjangkau (`INTENTS == {"swing"}` dan pemanggilnya memakai default), tetapi `params.peers`
    yang berbeda akan mengubah bentuk args `compare_tokens` — dan penjaga akan menilai
    `tool_args` yang bukan yang dipakai preflight.

    Envelope harus MILIK `symbol`, dan itu **diperiksa di sini**, bukan diserahkan ke disiplin
    pemanggil: nama tool tidak membawa simbol, jadi envelope SOL yang ditanyai tentang `ZZZQQQ`
    dulu menjawab "ada buktinya". Sekarang `data["symbol"]` dibandingkan lebih dulu (nilai itu
    dijaga `_check_bookkeeping` agar sama dengan `request.symbol`), dan envelope milik simbol
    lain menjawab kosong.

    Rumahnya di sini, bukan di pemakainya, karena bentuk yang dibacanya
    (``data["store_reads"]["tools"][…]["used_as_evidence"]``) dirakit di berkas ini
    (:meth:`ToolRead.as_dict`). Dua pemakai memakainya: `ui.app` — yang lebih dulu punya
    definisi ini dan menjawab 404 — dan `agent.main`, yang sampai TASKS 0.13 memakai standar
    LEBIH RENDAH daripada UI-nya sendiri dan menulis baris ledger permanen untuk simbol yang
    tidak ada.
    """
    if not isinstance(symbol, str) or not symbol:
        return []
    if not isinstance(data, Mapping):
        return []
    # Envelope harus MILIK simbol ini. `data["symbol"]` dijaga `_check_bookkeeping` (wajib sama
    # dengan `request.symbol`), jadi ia bisa dipercaya sebagai penanda kepemilikan — dan dengan
    # memeriksanya di sini, prasyaratnya tidak lagi bergantung pada disiplin pemanggil.
    # Kunci `symbol` wajib ADA, bukan sekadar cocok bila kebetulan ada: envelope buatan tangan
    # tanpa kunci itu dulu lolos cek kepemilikan (temuan RENDAH security-reviewer 0.13).
    if data.get("symbol") != symbol:
        return []
    reads = data.get("store_reads")
    tools = reads.get("tools") if isinstance(reads, Mapping) else None
    if not isinstance(tools, Mapping):
        return []
    return sorted(
        tool
        for tool in symbol_tools(symbol)
        if isinstance(tools.get(tool), Mapping) and tools[tool].get("used_as_evidence") is True
    )


@dataclass(frozen=True, slots=True)
class Gathered:
    """Bukti beku untuk satu keputusan + catatan bagaimana ia terkumpul."""

    bundle: Bundle
    envelopes: dict[str, Mapping[str, Any]]
    snapshot_ids: dict[str, int]
    reads: tuple[ToolRead, ...]
    warnings: tuple[str, ...]

    @property
    def status_envelopes(self) -> list[Mapping[str, Any]]:
        """Satu entri per tool WAJIB untuk `derive_status` — yang tidak menjawab jadi absen."""
        out: list[Mapping[str, Any]] = []
        for tool in REQUIRED_TOOLS:
            envelope = self.envelopes.get(tool)
            if envelope is not None and envelope.get("status") in ENVELOPE_STATUSES:
                out.append(envelope)
            else:
                out.append(_ABSENT_ENVELOPE)
        return out

    @property
    def n_answered(self) -> int:
        """Cacah tool yang benar-benar menjawab (`ok`/`partial`) — bukan cacah baris terbaca."""
        return sum(1 for envelope in self.envelopes.values() if envelope.get("status") in ANSWERED_STATUSES)


def gather(
    store: Store,
    symbol: str,
    intent: str,
    params: Params = DEFAULT_PARAMS,
    *,
    now: datetime | None = None,
) -> Gathered:
    """Resolusi store-first: SATU `latest_fresh` per tool, lalu bekukan semuanya sekaligus.

    Pembekuan terjadi SEBELUM item dinilai dan tidak ada pembacaan kedua di tengah jalan:
    envelope yang dinilai adalah `payload_json` dari bundle itu sendiri, jadi bukti yang
    di-hash dan bukti yang dibaca tidak bisa berbeda.

    Raises:
        core.store.StoreError: DB tidak terbaca.
        core.replay.ReplayError: baris tidak cocok dengan hash-nya (pesan menyebut snapshot_id).
    """
    found: dict[str, int] = {}
    reads: dict[str, ToolRead] = {}
    for tool, args in tool_args(symbol, intent, params).items():
        snapshot = store.latest_fresh(tool, sha256_hex(args), params.max_age_min, now=now)
        if snapshot is None:
            reads[tool] = ToolRead(
                tool=tool,
                note=f"no snapshot within {params.max_age_min} min in the store",
            )
            continue
        found[tool] = snapshot.id
        reads[tool] = ToolRead(tool=tool, snapshot_id=snapshot.id)

    bundle = freeze(store, found.values())
    by_id = {entry.snapshot_id: entry for entry in bundle.entries}

    envelopes: dict[str, Mapping[str, Any]] = {}
    warnings: list[str] = []
    for tool in REQUIRED_TOOLS:
        snapshot_id = found.get(tool)
        if snapshot_id is None:
            continue
        entry = by_id[snapshot_id]
        try:
            envelope = json.loads(entry.payload_json)
        except ValueError:
            note = "frozen payload is not readable JSON"
            warnings.append(f"{tool}: {note} (snapshot_id={snapshot_id}); not used as evidence")
            reads[tool] = ToolRead(tool=tool, snapshot_id=snapshot_id, note=note)
            continue
        if not isinstance(envelope, Mapping):
            note = f"frozen payload is {type(envelope).__name__}, not an envelope object"
            warnings.append(f"{tool}: {note} (snapshot_id={snapshot_id}); not used as evidence")
            reads[tool] = ToolRead(tool=tool, snapshot_id=snapshot_id, note=note)
            continue
        status = envelope.get("status")
        status_text = status if isinstance(status, str) else None
        if status not in ANSWERED_STATUSES and envelope.get("data") is not None:
            # Menyangkal diri sendiri TAPI membawa data: tidak dipakai sama sekali.
            note = "envelope reports a non-answering status yet carries data"
            warnings.append(
                f"{tool}: status={clean_text(str(status), KEY_LIMIT)} but data is not null "
                f"(snapshot_id={snapshot_id}); not used as evidence"
            )
            reads[tool] = ToolRead(
                tool=tool, snapshot_id=snapshot_id, status=status_text, as_of=entry.as_of, note=note
            )
            continue
        envelopes[tool] = envelope
        reads[tool] = ToolRead(
            tool=tool,
            snapshot_id=snapshot_id,
            status=status_text,
            as_of=entry.as_of,
            used=True,
            note="",
        )
    return Gathered(
        bundle=bundle,
        envelopes=envelopes,
        snapshot_ids={tool: found[tool] for tool in found},
        reads=tuple(reads[tool] for tool in REQUIRED_TOOLS),
        warnings=tuple(warnings),
    )


# --- angka agregat -------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Counts:
    """Cacah item PER STATUS — selalu berdampingan dengan `n_items` (tidak ada angka telanjang)."""

    n_items: int
    n_green: int
    n_red: int
    n_unspoken: int

    @property
    def n_spoken(self) -> int:
        """Item yang BENAR-BENAR tersebut (green+red). Bukan `len(ITEMS)`, bukan cacah tool."""
        return self.n_green + self.n_red

    def as_dict(self) -> dict[str, int]:
        return {
            DATA_N_ITEMS: self.n_items,
            DATA_N_SPOKEN: self.n_spoken,
            DATA_N_GREEN: self.n_green,
            DATA_N_RED: self.n_red,
            DATA_N_UNSPOKEN: self.n_unspoken,
        }


def count_items(items: Sequence[ItemResult]) -> Counts:
    """Hitung dari objek item yang benar-benar akan masuk envelope."""
    green = sum(1 for item in items if item.status == "green")
    red = sum(1 for item in items if item.status == "red")
    unspoken = sum(1 for item in items if item.status == UNSPOKEN)
    return Counts(n_items=len(items), n_green=green, n_red=red, n_unspoken=unspoken)


def check_aggregate_floor(result: SkillResult, params: Params = DEFAULT_PARAMS) -> None:
    """Lantai envelope preflight: angka agregat DAN vonis harus didukung `data["items"]`.

    Fungsi ini memeriksa envelope YANG SUDAH JADI, bukan variabel di tengah perakitan: itulah
    bedanya penjaga dari niat baik. `preflight` memanggilnya sebelum mengembalikan hasil, dan
    pemakai hilir WAJIB memanggilnya atas envelope yang sudah lewat tangan lain — narasi
    (TASKS 2.3) boleh mengganti `headline`/`key_points`, dan agen (TASKS 2.4) menakar posisi
    dari `data["plan"]` lalu menulis baris `decisions`.

    Yang diperiksa, semuanya dari `data["items"]` yang benar-benar ada di envelope (tiap poin
    punya tes penyerang di ``tests/test_preflight_skill.py``):

    0. `params` yang MEMERIKSA = `params` yang MENILAI (`data["params_hash"]`) — aturan yang
       sama dengan :func:`preflight.items.check_decision_floor`; tanpa ini lantai memeriksa
       ambang yang tidak pernah menilai apa pun.
    1. **Id item = TEPAT sepuluh baris spec §4** (:data:`preflight.params.ITEM_ORDER`).
       Kesetaraan, bukan subset: tanpa arah "⊆" item merah bisa DIHAPUS lalu seluruh `n_*`
       dihitung ulang agar konsisten; tanpa arah "⊇" item karangan bisa DISISIPKAN untuk
       menaikkan `n_spoken` dari nol.
    2. **`availability` setara dengan id item** (setelah nama seksi yang dikenal dikeluarkan) —
       dua arah, sehingga baris hantu tertangkap baik saat kuncinya asing maupun saat itemnya
       yang hilang.
    3. Kelima `n_*` dihitung ulang dari `data["items"]`; invarian `0 <= n_spoken <= n_items`.
    4. `decision`/`blocking` DIREKONSTRUKSI dengan aturan veto yang sama
       (:func:`preflight.items.decide`) lalu dibandingkan. Inilah yang menutup "GO padahal ada
       item merah" dan "blocking dikosongkan": vonis di envelope tidak dipercaya, ia dihitung
       ulang dari itemnya.
    5. `summary.decision`/`summary.blocking` wajib sama dengan `data`-nya.
    6. `n_spoken == 0` → tanpa `plan`, wajib `NO_GO` ber-`blocking`.
    7. `plan` wajib SAMA PERSIS dengan :func:`_plan_passthrough` atas item yang sama —
       passthrough yang tidak diikat hanyalah kata di docstring.

    Raises:
        PreflightError: salah satu di atas dilanggar.
    """
    data = result.data
    if data is None:
        # `status == "unavailable"` sudah dipastikan `core/skill.py`; yang diperiksa di sini
        # adalah bahwa penolakan tetap MENYEBUT dirinya penolakan, dan bahwa daftar item di
        # `availability` (bila ada) tetap sepuluh baris yang sama.
        verdict = result.summary.get(DATA_DECISION)
        if verdict != "NO_GO":
            raise PreflightError(
                f"envelope tanpa data harus menyebut decision 'NO_GO', bukan {verdict!r}: "
                "tidak ada bukti berarti tidak berangkat, dan itu harus terbaca"
            )
        # `availability` kosong = permintaan yang ditolak sebelum satu item pun dinilai
        # (simbol/intent tidak terbaca, store gagal). Selain itu ia wajib sepuluh baris penuh.
        expected_ids = () if not result.availability else ITEM_ORDER
        _check_availability(result, expected_ids)
        if expected_ids and set(result.availability.values()) != {UNSPOKEN}:
            raise PreflightError(
                "envelope tanpa data tetapi ada item yang tidak `unspoken` di availability: "
                "tidak ada bukti berarti tidak ada yang tersebut"
            )
        # Cabang inilah yang membawa "kenapa" ke kartu (`key_points`), jadi ia diperiksa dengan
        # aturan yang sama: `blocking` hanya boleh menyebut baris panggilan yang nyata, dan
        # `headline` tidak boleh berbunyi GO untuk keputusan NO_GO.
        blocking = result.summary.get(DATA_BLOCKING)
        if not isinstance(blocking, list) or any(not isinstance(i, str) for i in blocking):
            raise PreflightError("summary.blocking harus daftar id item")
        ghosts = sorted(set(blocking) - set(expected_ids))
        if ghosts:
            raise PreflightError(f"summary.blocking menyebut item yang tidak dinilai: {ghosts}")
        _check_summary_text(result.summary, "NO_GO")
        return

    if DATA_ITEMS not in data:
        # Asimetri yang ditutup di sini (temuan review TASKS 2.2a): `core/skill.py` memeriksa
        # "nol bukti" hanya untuk `data={"items": []}`; `data` TANPA kunci itu lolos seluruh
        # invarian item. Envelope preflight tanpa `items` berarti angka agregatnya tidak punya
        # penjaga sama sekali — jadi ia ditolak di sini, bukan didiamkan.
        raise PreflightError(
            "data envelope preflight wajib punya kunci 'items': angka agregat tanpa item yang "
            "bisa dihitung adalah angka tanpa n"
        )
    try:
        # `data["items"]` bisa ditambal setelah validasi (celah yang diakui `core/skill.py`),
        # jadi penguraian ulang di sini BISA melempar `pydantic.ValidationError`/`ValueError`.
        # Kontrak `Raises:` di atas menyebut satu kelas saja; pemanggil hilir yang menulis
        # `except PreflightError` tidak boleh pecah karena envelope yang cacat.
        items = result.items
    except Exception as exc:  # noqa: BLE001 — apa pun dari pydantic dijadikan satu kelas
        raise PreflightError(
            f"data['items'] tidak bisa diurai sebagai ItemResult: {type(exc).__name__}"
        ) from None
    ids = sorted(item.id for item in items)
    if ids != sorted(ITEM_ORDER):
        raise PreflightError(
            f"data['items'] harus TEPAT sepuluh baris panggilan spec §4 {sorted(ITEM_ORDER)}, "
            f"bukan {ids}: item yang dihapus menyembunyikan penahan, item yang disisipkan "
            "mengarang bukti"
        )
    _check_availability(result, ITEM_ORDER, items)

    expected_params = params.params_hash()
    if data.get(DATA_PARAMS_HASH) != expected_params:
        raise PreflightError(
            f"params_hash envelope {data.get(DATA_PARAMS_HASH)!r} bukan {expected_params!r}: "
            "ambang yang MENILAI item dan ambang yang memeriksanya harus sama"
        )

    counts = count_items(items)
    for key, value in counts.as_dict().items():
        if data.get(key) != value:
            raise PreflightError(
                f"{key}={data.get(key)!r} tidak cocok dengan item yang benar-benar tersebut "
                f"({value}); angka agregat harus dihitung dari data['items'], bukan dari "
                "panjang daftar apa pun"
            )
    if not 0 <= counts.n_spoken <= counts.n_items:
        raise PreflightError(f"n_spoken={counts.n_spoken} di luar [0, n_items={counts.n_items}]")

    verdict = data.get(DATA_DECISION)
    if verdict not in DECISIONS:
        raise PreflightError(f"decision harus salah satu {sorted(DECISIONS)}, bukan {verdict!r}")
    if result.summary.get(DATA_DECISION) != verdict:
        raise PreflightError(
            f"summary.decision={result.summary.get(DATA_DECISION)!r} berbeda dari "
            f"data.decision={verdict!r}: kartu dan data tidak boleh mengatakan dua hal"
        )
    blocking = data.get(DATA_BLOCKING)
    if not isinstance(blocking, list) or any(not isinstance(i, str) for i in blocking):
        raise PreflightError("blocking harus daftar id item")
    unknown = [item_id for item_id in blocking if item_id not in set(ids)]
    if unknown:
        raise PreflightError(f"blocking menyebut item yang tidak dinilai: {unknown}")
    if result.summary.get(DATA_BLOCKING) != blocking:
        raise PreflightError(
            f"summary.blocking={result.summary.get(DATA_BLOCKING)!r} berbeda dari "
            f"data.blocking={blocking!r}: kartu dan data tidak boleh menyebut penahan berbeda"
        )

    # Vonis tidak dipercaya, ia DIHITUNG ULANG dari item yang ada di envelope ini.
    try:
        recomputed = decide(items, params)
    except DecisionFloorError as exc:
        raise PreflightError(f"item envelope tidak bisa dinilai ulang: {exc}") from None
    if sorted(blocking) != sorted(recomputed.blocking):
        raise PreflightError(
            f"blocking {sorted(blocking)} bukan himpunan penyebab veto atas item envelope ini "
            f"{sorted(recomputed.blocking)}: penahan tidak boleh disembunyikan maupun dikarang"
        )
    if verdict != recomputed.decision:
        raise PreflightError(
            f"decision {verdict!r} tidak konsisten dengan item envelope ini (seharusnya "
            f"{recomputed.decision!r})"
        )

    if counts.n_spoken == 0:
        # Nol bukti: bukan skor rendah, melainkan panggilan yang tidak terjawab.
        if DATA_PLAN in data:
            raise PreflightError(
                "n_spoken=0 tetapi envelope membawa plan: rencana tanpa satu pun item yang "
                "tersebut adalah angka tanpa bukti"
            )
        if verdict != "NO_GO":
            raise PreflightError(f"n_spoken=0 harus NO_GO, bukan {verdict!r}")
        if not blocking:
            raise PreflightError("n_spoken=0 tanpa blocking: penolakan harus menyebut penahannya")
    _check_plan(data.get(DATA_PLAN), items)
    _check_summary_text(result.summary, verdict)
    _check_bookkeeping(result, data, items)


def _check_summary_text(summary: Mapping[str, Any], verdict: str) -> None:
    """Kartu tidak boleh berbunyi lain dari vonisnya (serangan 15, 3 Sep 2026).

    `summary` adalah SATU-SATUNYA hal yang boleh diganti narasi (TASKS 2.3), dan pada cabang
    penolakan ia juga satu-satunya tempat "kenapa" dibaca manusia. Yang dijaga di sini hanya
    yang bisa diperiksa mesin tanpa bukti tambahan: tipe, dan bahwa `headline` untuk `NO_GO`
    tidak memuat "GO" yang berdiri sendiri (`"NO_GO"` sendiri tentu boleh — ia dibuang dulu).
    Isi kalimatnya tidak dijaga siapa pun; itu urusan 2.3 dan review manusia.
    """
    headline = summary.get("headline")
    if not isinstance(headline, str) or not headline:
        raise PreflightError(f"summary.headline harus teks, bukan {type(headline).__name__}")
    # Urutan disengaja: "NO_GO yang berbunyi GO" diperiksa DULU. Ia kegagalan yang lebih
    # spesifik DAN lebih berbahaya daripada "tidak menyebut vonisnya", jadi pesannya harus
    # menyebut hal itu — kartu yang berbunyi "SOL GO" untuk keputusan NO_GO adalah persis
    # kebohongan yang dicari serangan 15, bukan sekadar headline yang tidak lengkap.
    if verdict == "NO_GO" and "GO" in headline.replace("NO_GO", ""):
        raise PreflightError(f"summary.headline berbunyi GO untuk keputusan NO_GO: {headline!r}")
    if verdict not in headline:
        raise PreflightError(f"summary.headline tidak menyebut decision {verdict!r}: {headline!r}")
    points = summary.get("key_points")
    if not isinstance(points, list) or any(not isinstance(point, str) for point in points):
        raise PreflightError("summary.key_points harus daftar teks")


def _check_bookkeeping(result: SkillResult, data: Mapping[str, Any], items: Sequence[ItemResult]) -> None:
    """Pembukuan bukti di `data` wajib ADA, bertipe benar, dan tidak saling bertentangan.

    Semuanya intra-envelope (serangan 9/10/11/12/13 security-reviewer, 3 Sep 2026): kunci yang
    hilang atau bertipe salah membuat TASKS 2.4 menulis baris `decisions` dengan kolom kosong,
    dan `snapshot_ids` yang berselisih dengan `store_reads.n_snapshots_frozen` adalah dua
    pembukuan yang menyebut jumlah bukti berbeda di envelope yang sama. Yang TIDAK dijawab di
    sini: apakah id itu benar-benar bukti yang dipakai — itu butuh bundle (TASKS 2.4c).
    """
    if data.get(DATA_SYMBOL) != result.request.get(DATA_SYMBOL):
        raise PreflightError(
            f"data.symbol={data.get(DATA_SYMBOL)!r} berbeda dari "
            f"request.symbol={result.request.get(DATA_SYMBOL)!r}: satu envelope, satu simbol"
        )
    for key in (DATA_DECISION_HASH, DATA_PARAMS_HASH, DATA_REASON):
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise PreflightError(f"data[{key!r}] harus teks tak kosong, bukan {value!r}")
    snapshot_ids = data.get(DATA_SNAPSHOT_IDS)
    if not isinstance(snapshot_ids, list) or any(
        not isinstance(i, int) or isinstance(i, bool) for i in snapshot_ids
    ):
        raise PreflightError(f"data['snapshot_ids'] harus daftar id baris, bukan {snapshot_ids!r}")
    if sorted(set(snapshot_ids)) != snapshot_ids:
        raise PreflightError(f"data['snapshot_ids'] harus urut menaik tanpa duplikat: {snapshot_ids}")
    reads = data.get(DATA_STORE_READS)
    if not isinstance(reads, Mapping):
        raise PreflightError(f"data['store_reads'] harus objek, bukan {type(reads).__name__}")
    if reads.get("n_snapshots_frozen") != len(snapshot_ids):
        raise PreflightError(
            f"store_reads.n_snapshots_frozen={reads.get('n_snapshots_frozen')!r} berbeda dari "
            f"{len(snapshot_ids)} id di data['snapshot_ids']: dua pembukuan, dua jumlah"
        )
    tools = reads.get("tools")
    if not isinstance(tools, Mapping) or set(tools) != set(REQUIRED_TOOLS):
        raise PreflightError(
            f"store_reads.tools harus menyebut tepat {sorted(REQUIRED_TOOLS)}, bukan "
            f"{sorted(tools) if isinstance(tools, Mapping) else type(tools).__name__}"
        )
    answered = reads.get("n_tools_answered")
    if (
        not isinstance(answered, int)
        or isinstance(answered, bool)
        or not 0 <= answered <= len(REQUIRED_TOOLS)
    ):
        raise PreflightError(f"store_reads.n_tools_answered tidak masuk akal: {answered!r}")
    for item in items:
        # Item boleh menunjuk `None` (item agregat 1/2, atau tool yang tidak punya envelope),
        # tetapi id yang DISEBUT harus ada di daftar bukti envelope ini.
        source_id = None if item.source is None else item.source.snapshot_id
        if source_id is not None and source_id not in snapshot_ids:
            raise PreflightError(
                f"item {item.id!r} menunjuk snapshot_id={source_id} yang tidak ada di "
                f"data['snapshot_ids']={snapshot_ids}: tunjukan harus menunjuk bukti envelope ini"
            )


def _check_plan(plan: Any, items: Sequence[ItemResult]) -> None:
    """`plan` wajib SAMA PERSIS dengan passthrough item 9 — atau tidak ada sama sekali.

    Perbandingan kesetaraan, bukan daftar periksa lapangan: daftar periksa akan meloloskan
    `plan` yang "bentuknya benar" tetapi menunjuk angka lain daripada item 9-nya sendiri, dan
    TASKS 2.4 menakar posisi dari angka itu.

    **Batasnya harus dibaca bersama klaim itu** (serangan 8/12 security-reviewer, 3 Sep 2026):
    yang ditutup HANYA `plan` yang tidak sinkron dengan item. Ia TIDAK mencegah `trade_plan`
    yang dipalsukan **di kedua sisi** — `entry` 103.8957 -> 1.0, `stop` -> 0.0001, `targets` ->
    [999999.0], `source.snapshot_id` -> 424242 di `data["items"][8]` DAN di `data["plan"]`
    sekaligus — karena kedua sisi perbandingan berasal dari item yang sama. Penjangkaran ke
    `payload_json` beku adalah TASKS 2.4c; lihat "Batas yang diakui" di docstring modul.
    """
    expected = _plan_passthrough(items)
    if plan is None:
        if expected is not None:
            # Bukan pelanggaran kejujuran (menghilangkan rencana tidak mengarang apa pun),
            # tetapi envelope preflight yang item 9-nya tersebut SELALU membawanya; kalau tidak,
            # pemanggil hilir akan mengira item 9 tidak tersebut.
            raise PreflightError(
                f"item {ITEM_PLAN} tersebut tetapi envelope tidak membawa plan-nya: "
                "kartu dan data harus menunjuk rencana yang sama"
            )
        return
    if expected is None:
        raise PreflightError(
            f"envelope membawa plan padahal item {ITEM_PLAN} tidak tersebut: "
            "rencana harus punya baris panggilan yang menyebutkannya"
        )
    if plan != expected:
        raise PreflightError(
            f"plan tidak sama dengan passthrough item {ITEM_PLAN}: `plan` hanya boleh "
            "meneruskan apa yang dibaca item 9 (nilai, source, status), bukan angka lain"
        )


def _check_availability(
    result: SkillResult, expected_ids: Sequence[str], items: Sequence[ItemResult] = ()
) -> None:
    """AC 2.2 (e), DUA ARAH: `availability` = tepat `expected_ids` + nama seksi yang dikenal.

    Kunci asing (id item hantu) ditolak — itu temuan RENDAH review 2.2a. Arah sebaliknya juga:
    item yang HILANG dari `availability` berarti satu baris panggilan lenyap dari kartu tanpa
    ada yang protes, dan `core/skill.py` hanya menjaga arah "tiap item punya entri" untuk item
    yang masih ada di `data["items"]` — bukan untuk item yang dihapus dari keduanya.
    """
    keys = set(result.availability)
    expected = set(expected_ids)
    ghosts = sorted(keys - expected - KNOWN_SECTIONS)
    if ghosts:
        raise PreflightError(
            f"availability menyebut kunci yang bukan id item dinilai maupun seksi yang dikenal: {ghosts}"
        )
    missing = sorted(expected - keys)
    if missing:
        raise PreflightError(f"availability tidak menyebut item yang dinilai: {missing}")
    for item in items:
        marker = result.availability.get(item.id)
        wanted = UNSPOKEN if item.status == UNSPOKEN else AVAILABLE
        if marker != wanted:
            # `core/skill.py` menjaga aturan yang sama SAAT validasi; `availability` adalah dict
            # biasa yang masih bisa ditambal sesudahnya (celah yang sama dengan `data`).
            raise PreflightError(
                f"availability[{item.id!r}]={marker!r} tidak cocok dengan status item "
                f"({item.status!r} -> {wanted!r}): kartu dan item harus menyebut hal yang sama"
            )


# --- perakitan ------------------------------------------------------------------------------
def _bounded_warnings(warnings: Sequence[str]) -> list[str]:
    """Potong JUMLAH warning tetapi sebutkan berapa aslinya (konvensi `core/skill_cli.py`)."""
    total = len(warnings)
    if total <= MAX_WARNINGS:
        return list(warnings)
    shown = list(warnings[:MAX_WARNINGS])
    shown.append(f"{total} warnings in total; {total - MAX_WARNINGS} not shown")
    return shown


def _capped_lists(value: Any) -> Any:
    """Salin `value` item 9 dengan daftar panjang DIPOTONG + jumlah aslinya disebut.

    `trade_plan.targets`/`risk_reward` dibaca item 9 tanpa batas cacah, dan `plan` menyalinnya
    LAGI: payload dengan 50.000 target menghasilkan envelope >1 MB yang ikut ke stdout, ke log,
    dan kelak ke `card_json` — tepat yang dilarang `preflight/items.py` dan `core/skill.py`
    untuk koleksi lain. Polanya sama dengan sisa repo: batasi, lalu sebutkan berapa aslinya
    (`<nama>_total`), jangan membuang diam-diam.

    **Batas yang diakui:** yang dipotong hanya SALINAN di `plan`. `data["items"][*]["value"]`
    milik item 9 masih membawa daftar penuh — pemotongannya ada di `preflight/items.py`
    (TASKS 2.1), bukan di sini, dan mengubahnya berarti menggeser nilai item yang sudah
    direview. Dilaporkan, bukan didiamkan.
    """
    if not isinstance(value, Mapping):
        return value
    out = dict(value)
    for name in ("targets", "risk_reward"):
        entries = out.get(name)
        if isinstance(entries, list) and len(entries) > MAX_PLAN_ENTRIES:
            out[f"{name}_total"] = len(entries)
            out[name] = entries[:MAX_PLAN_ENTRIES]
    return out


def _plan_passthrough(items: Sequence[ItemResult]) -> dict[str, Any] | None:
    """`deep_analysis.data.trade_plan` seperti yang SUDAH dibaca item 9 — tanpa aritmetika.

    Fungsi ini adalah SATU-SATUNYA definisi `plan`: :func:`check_aggregate_floor` mengikat
    `data["plan"]` sama dengan keluarannya, sehingga "passthrough" bisa diperiksa mesin.
    """
    item = next((entry for entry in items if entry.id == ITEM_PLAN), None)
    if item is None or item.status == UNSPOKEN or item.value is None:
        return None
    return {
        "item_id": item.id,
        "item_status": item.status,
        "source": None if item.source is None else item.source.model_dump(mode="json"),
        "value": _capped_lists(item.value),
    }


def _key_points(outcomes: Sequence[ItemOutcome], decision: Decision) -> list[str]:
    """Alasan penahan (id + `reason` item), dipotong jumlahnya dengan totalnya disebut."""
    reasons = {outcome.id: outcome.result.reason for outcome in outcomes}
    points = [f"{item_id}: {reasons.get(item_id, '')}" for item_id in decision.blocking]
    if len(points) > MAX_KEY_POINTS:
        hidden = len(points) - MAX_KEY_POINTS
        points = points[:MAX_KEY_POINTS]
        points.append(f"{len(decision.blocking)} blocking items in total; {hidden} not shown")
    return points


def _headline(symbol: str, verdict: str, counts: Counts) -> str:
    return (
        f"{symbol} {verdict}: {counts.n_spoken} of {counts.n_items} roll-call items spoken "
        f"({counts.n_green} green, {counts.n_red} red, {counts.n_unspoken} unspoken)"
    )


def _refusal(
    request: dict[str, Any],
    warnings: Sequence[str],
    headline: str,
    *,
    items: Sequence[ItemResult] = (),
    key_points: Sequence[str] = (),
    blocking: Sequence[str] = (),
    params: Params = DEFAULT_PARAMS,
) -> SkillResult:
    """Envelope penolakan: `data=None`, tetapi MENYEBUT dirinya NO_GO berikut penahannya.

    Dipakai dua keadaan yang berbeda sebabnya dan sama akibatnya: permintaan yang tidak bisa
    dibaca (simbol/intent, store tidak terbaca, bukti gagal diverifikasi) dan **tidak satu pun
    tool menjawab**. Untuk yang kedua, item TETAP disebut di `availability` (kesepuluhnya
    `unspoken`) supaya kartu tetap menunjukkan sepuluh baris panggilan yang tidak terjawab —
    `data` sendiri wajib `None` (kontrak `core/skill.py`), jadi `decision`/`blocking` dibawa
    `summary`. Envelope seperti ini TIDAK membawa `decision_hash`: bundle kosong tidak
    membuktikan apa pun, dan pemanggil yang perlu menyimpannya (TASKS 2.4) menghitungnya
    sendiri dari bundle yang ia bekukan.

    Item yang tersebut TIDAK boleh sampai ke sini: itu berarti bukti dan kesimpulan
    bertentangan (aturan yang sama dijaga `SkillResult.from_items`).

    **Batas yang diakui:** untuk penolakan yang terjadi SEBELUM satu item pun dinilai (simbol
    atau `intent` tidak terbaca, store gagal dibuka, bukti gagal diverifikasi) `blocking`
    kosong — bukan karena tidak ada yang menahan, melainkan karena belum ada baris panggilan
    yang boleh disebut; mengisinya dengan id item berarti mengarang penahan yang tidak pernah
    dinilai. Supaya kartu tetap menjawab "kenapa", sebabnya dibawa `summary.key_points`
    (di samping `warnings`), bukan disembunyikan di log.
    """
    spoken = [item.id for item in items if item.status != UNSPOKEN]
    if spoken:
        raise PreflightError(
            f"tidak ada tool yang menjawab, tetapi item {spoken} tetap bervonis: "
            "bukti dan kesimpulan tidak boleh bertentangan"
        )
    result = SkillResult(
        tool=TOOL,
        status=UNAVAILABLE,
        data_mode="unknown",
        as_of=None,
        request=dict(request),
        data=None,
        summary={
            "headline": headline,
            "key_points": list(key_points),
            DATA_DECISION: "NO_GO",
            DATA_BLOCKING: list(blocking),
        },
        availability={item.id: UNSPOKEN for item in items},
        warnings=_bounded_warnings(list(warnings)),
    )
    check_aggregate_floor(result, params)
    return result


def preflight(
    symbol: str,
    intent: str = DEFAULT_INTENT,
    *,
    store: Store | None = None,
    params: Params = DEFAULT_PARAMS,
    now: datetime | None = None,
) -> SkillResult:
    """Panggilan pra-berangkat untuk `symbol` — store-first, deterministik, tanpa LLM.

    Argumen posisional sengaja dua supaya `make skill ARGS="preflight SOL"` dan
    `ARGS="preflight SOL swing"` sama-sama jalan lewat `core/skill_cli.py` (`fn(*args)`).
    `intent` **tidak menggerakkan satu ambang pun** — lihat "Batas yang diakui" di docstring
    modul.

    Args:
        symbol: simbol RYO (dinormalkan seperti perekam: strip + huruf besar).
        intent: harus ada di :data:`INTENTS`; selain itu → envelope `unavailable` + warning
            TANPA membaca store.
        store: store yang sudah terbuka (tes/agen). Default: `Store(read_only=True)` — koneksi
            yang secara struktural tidak bisa menulis baris `decisions` (itu TASKS 2.4).
        params: ambang keputusan; ikut ke `params_hash` dan `decision_hash`.
        now: jam resolusi kesegaran store (default jam dinding). Item TIDAK memakainya:
            keputusan tetap fungsi murni atas bukti beku (ADR-003).

    Tidak pernah melempar untuk bukti yang jelek: bukti yang tidak ada menjadi item `unspoken`
    dan `NO_GO` yang menyebut penahannya. Yang bisa melempar hanyalah kegagalan program
    (:class:`PreflightError`) — dan `core/skill_cli.py` mengubahnya menjadi envelope, bukan
    traceback.
    """
    request: dict[str, Any] = {
        "symbol": clean_text(symbol, KEY_LIMIT) if isinstance(symbol, str) else None,
        "intent": clean_text(intent, KEY_LIMIT) if isinstance(intent, str) else None,
    }
    normalized = normalize_symbol(symbol)
    if normalized is None:
        reason = f"symbol {request['symbol']!r} is not usable; nothing was read from the store"
        return _refusal(
            request,
            [reason],
            f"{TOOL}: NO_GO — the symbol could not be read, so nothing was called.",
            key_points=[reason],
            params=params,
        )
    request["symbol"] = normalized
    if intent not in INTENTS:
        reason = (
            f"intent {request['intent']!r} is outside {sorted(INTENTS)}; preflight is "
            "store-first and has no recorded evidence gathered for that intent, so no tool "
            "was read at all"
        )
        return _refusal(
            request,
            [reason],
            f"{normalized} NO_GO: intent {request['intent']!r} is not served by this skill.",
            key_points=[reason],
            params=params,
        )

    owned: Store | None = None
    try:
        if store is None:
            # read_only: bukan janji "kami tidak menulis", melainkan koneksi yang tidak bisa.
            store = owned = Store(read_only=True)
        gathered = gather(store, normalized, intent, params, now=now)
    except StoreError as exc:
        log.warning("preflight %s: store tidak terbaca: %s", normalized, exc)
        reason = f"the snapshot store could not be read ({type(exc).__name__}); no evidence was frozen"
        return _refusal(
            request,
            [reason],
            f"{normalized} NO_GO: the snapshot store could not be read.",
            key_points=[reason],
            params=params,
        )
    except ReplayError as exc:
        # Pesan `core/replay.py` menyebut snapshot_id — teks kami sendiri, aman dicetak.
        log.warning("preflight %s: bukti gagal dibekukan: %s", normalized, exc)
        reason = f"frozen evidence failed verification: {exc}"
        return _refusal(
            request,
            [reason],
            f"{normalized} NO_GO: the frozen evidence did not verify.",
            key_points=[reason],
            params=params,
        )
    finally:
        if owned is not None:
            owned.close()

    evidence = Evidence.from_envelopes(
        normalized,
        gathered.envelopes,
        params=params,
        snapshot_ids=gathered.snapshot_ids,
    )
    outcomes = evaluate_all(evidence)
    items = results_of(outcomes)
    decision = decide(items, params)
    snapshot_ids = list(gathered.bundle.snapshot_ids)
    try:
        check_decision_floor(decision, items, params, snapshot_ids=snapshot_ids)
    except DecisionFloorError as exc:  # pragma: no cover - bug lapisan ini, bukan bukti jelek
        raise PreflightError(f"keputusan preflight melanggar lantai konsistensi: {exc}") from None

    counts = count_items(items)
    warnings = [*gathered.warnings, *warnings_of(outcomes)]
    if derive_status([envelope.get("status") for envelope in gathered.status_envelopes]) == UNAVAILABLE:
        # Tidak SATU tool pun menjawab: envelope wajib `data=None` (kontrak 1.3), jadi tidak ada
        # tempat yang jujur untuk angka agregat maupun plan. Yang tersisa adalah penolakan yang
        # menyebut kesepuluh baris panggilan yang tidak terjawab.
        return _refusal(
            request,
            [
                f"no required tool answered ({gathered.n_answered} of {len(REQUIRED_TOOLS)}); "
                f"nothing could be spoken from the {len(snapshot_ids)} snapshot(s) frozen within "
                f"{params.max_age_min} min",
                *warnings,
            ],
            _headline(normalized, decision.decision, counts),
            items=items,
            key_points=_key_points(outcomes, decision),
            blocking=decision.blocking,
            params=params,
        )
    data_extra: dict[str, Any] = {
        # `data` TIDAK dibersihkan model (batas yang diakui `core/skill.py`), jadi satu-satunya
        # teks masukan di sini di-escape sendiri — kalau tidak, `request["symbol"]` (dibersihkan
        # validator) dan `data["symbol"]` bisa menyebut simbol yang berbeda di envelope yang sama.
        "symbol": clean_text(normalized, KEY_LIMIT),
        "intent": intent,
        DATA_DECISION: decision.decision,
        DATA_BLOCKING: list(decision.blocking),
        **counts.as_dict(),
        "snapshot_ids": snapshot_ids,
        "params_hash": params.params_hash(),
        "decision_hash": decision_hash(gathered.bundle, params.as_dict(), decision.as_material()),
        DATA_STORE_READS: {
            "n_tools_required": len(REQUIRED_TOOLS),
            "n_tools_answered": gathered.n_answered,
            "n_snapshots_frozen": len(snapshot_ids),
            "max_age_min": params.max_age_min,
            "tools": {read.tool: read.as_dict() for read in gathered.reads},
        },
    }
    if counts.n_spoken == 0:
        # Tanpa `plan`, tanpa skor: yang dilaporkan adalah penolakan berikut sebabnya.
        data_extra["reason"] = (
            f"0 of {counts.n_items} roll-call items could be spoken from the frozen evidence "
            f"({gathered.n_answered} of {len(REQUIRED_TOOLS)} tools answered) -> this is a "
            "refusal, not a score"
        )
    else:
        plan = _plan_passthrough(items)
        if plan is not None:
            data_extra[DATA_PLAN] = plan
        data_extra["reason"] = (
            f"{counts.n_spoken} of {counts.n_items} items spoken; {len(decision.blocking)} blocking"
        )

    summary = {
        "headline": _headline(normalized, decision.decision, counts),
        "key_points": _key_points(outcomes, decision),
        DATA_DECISION: decision.decision,
        DATA_BLOCKING: list(decision.blocking),
    }
    try:
        result = SkillResult.from_items(
            tool=TOOL,
            request=request,
            items=items,
            envelopes=gathered.status_envelopes,
            summary=summary,
            warnings=_bounded_warnings(warnings),
            data_extra=data_extra,
        )
    except SkillError as exc:  # pragma: no cover - kontradiksi bukti/kesimpulan
        raise PreflightError(f"envelope preflight tidak bisa dirakit: {exc}") from None
    check_aggregate_floor(result, params)
    return result


register_skill(TOOL, preflight)
# Paket pemilik skill yang mengisi peta modul (core/ tidak boleh mengimpor preflight, ADR-002).
# `setdefault`: entri yang sudah ada — mis. dipasang tes — tidak ditimpa diam-diam.
SKILL_MODULES.setdefault(TOOL, "preflight.skill")
