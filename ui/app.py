"""`ui/app.py` — API baca-saja untuk Layar A & B (TASKS 3.1 + 3.1a, spec §4–§7).

Empat route menjawab empat pertanyaan juri, dan tidak satu pun memanggil tool RYO atau LLM:

* ``GET /api/watchlist`` — "simbol mana yang paling dekat GO, dan apa yang menahan yang lain?"
  Satu baris per simbol (keputusan TERAKHIR simbol itu), diurutkan **daya veto** item
  penahannya (:data:`preflight.params.ITEM_ORDER` = urutan spec §4, daya veto menurun).
* ``GET /api/decisions`` — daftar keputusan terbaru, tanpa paginasi/pencarian (scope PM).
* ``GET /api/decision/{id}`` — kartu panggilan 10 baris + bundle replay + vonis replay.
* ``GET /api/preflight/{symbol}`` — panggilan pra-berangkat store-first untuk satu simbol.

Ditambah route pelayan: ``GET /`` (**Layar A itu sendiri**, TASKS 3.5 — bukan indeks JSON dan
bukan landing page), ``GET /api/routes`` (rumah baru daftar route), ``GET /api/health``
(``calls_ryo_tools: false``), serta dua aset build ``GET /ui/tenko.css`` dan ``GET /ui/app.js``.

Aturan yang ditegakkan di sini, bukan dijanjikan
------------------------------------------------
1. **`simulated: true` di SETIAP respons**, termasuk 404/405/422/500/503 — lewat :func:`_body`
   dan TIGA exception handler. Sejak TASKS 3.2 ketiganya menjawab dalam BENTUK yang sedang
   dilihat pemanggil (:func:`_fail`): di bawah `/ui` sebagai halaman ber-banner + CSP, di luar
   itu sebagai JSON. Sebelumnya `/ui/decision/0` (salah ketik yang wajar, `ge=1`), `/ui/nope`,
   dan `POST /ui` menjawab JSON telanjang TANPA CSP — `simulated` ada, tetapi layarnya hilang.
   Handler itu sendiri: `starlette.exceptions.HTTPException` (induk dari
   `fastapi.HTTPException`, sekaligus 404 rute tak dikenal dan 405 metode salah),
   `RequestValidationError`, dan `Exception` (jaring terakhir, menggantikan "Internal Server
   Error" teks polos bawaan). Ini kewajiban aturan lomba (docs/rules-compliance.md), bukan
   hiasan — dan yang menjaganya adalah tes yang menembak path/metode yang TIDAK dideklarasikan,
   bukan hanya yang dideklarasikan.
2. **Nol panggilan RYO/LLM.** Yang dibaca hanya `data/ryo.db` lewat koneksi BACA-SAJA
   (`Store(read_only=True)`) dan `agent/ledger.py`. `preflight.preflight` sendiri store-first.
   Koneksi baca-saja itu sekarang meng-`quote()` path-nya dan memasang `PRAGMA query_only`
   (TASKS **0.10**, ditutup di `c362fe2`), jadi path ber-`?`/`#` tidak lagi bisa membuka koneksi
   yang BISA menulis. Modul ini tetap **tidak pernah menerima path DB dari request** — path
   hanya dari `DB_PATH` (env) atau :attr:`app.state.db_path` yang dipasang tes — karena itu
   kebijakan yang berdiri sendiri, bukan tambalan atas kelemahan yang sudah diperbaiki.
3. **Dua generasi keputusan dipisah (TASKS 3.1a, ADR-016).** `/api/watchlist` dan
   `/api/decisions` hanya menampilkan baris ber-`algo` (`DecisionRow.is_current_generation()`),
   dan menyebut berapa baris warisan yang tidak ditampilkan — disembunyikan secara TERTULIS,
   bukan diam-diam. `/api/decision/{id}` untuk id warisan menjawab **200** (barisnya ada) dengan
   ``replay_status: "STALE_ALGO"``, ``items: []``, dan satu kalimat sebab; `blocking_json`
   warisan **tidak dirender** karena isinya integer (indeks implementasi yang sudah dihapus),
   bukan id item — merendernya akan menampilkan "blocking: 5, 6, 10" yang tidak berarti apa pun.
4. **"Terverifikasi" hanya dari `ReplayReport.verified`**, tidak pernah dari string, exit code,
   atau `grep`. `agent/replay.py` menyediakan `VERIFIED_VERDICTS = {IDENTICAL}` (satu anggota);
   keempat vonis lain — `STALE_ALGO`, `STALE_PARAMS` (TASKS 0.14), `NO_EVIDENCE`, dan `MISMATCH`
   — semuanya `verified = False`. Baris nol-bukti (`data/ryo.db` 20-23) karena itu tidak pernah
   bisa tampil sebagai lencana hijau: field ``verified`` UI **adalah** field yang sama, bukan
   tafsiran atasnya.
4b. **Temuan dan ketidaktahuan tidak boleh sewarna** (AC 0.14 butir 4). Setiap baris ringkas dan
   setiap kartu membawa :data:`VERDICT_TONE` dan :data:`VERDICT_LABEL` di samping
   ``replay_status``. `MISMATCH` bernada ``finding`` (kami memeriksa buktinya dan ia tidak
   mereproduksi keputusan); `STALE_ALGO`/`STALE_PARAMS` bernada ``not_assessed`` (kami tidak
   memeriksanya sama sekali). Nada itu nama kelas CSS untuk Layar A/B (TASKS 3.2), sehingga
   perbedaannya wajib terlihat sebagai warna DAN terbaca sebagai kalimat, bukan salah satunya.
5. **Nilai asal-DB di-escape di sini.** Teks dari `card_json`/kolom `decisions` tidak dilindungi
   hash apa pun (`agent/replay.py` membuktikan `algo` bermuatan `\\n` bisa memalsukan baris
   keluaran CLI). Di HTML bahayanya berbeda: `</script>` di dalam string JSON yang ditempel ke
   halaman menutup tag. Dua lapis: `core.skill.clean_text` (aksara kendali di-escape TERLIHAT,
   panjang dibatasi — sanitizer tunggal repo, TASKS 1.5b) dan :class:`SafeJSONResponse` yang
   menuliskan `<`, `>`, `&` sebagai escape JSON. Yang kedua **lossless**: `json.loads` atas
   keluaran itu mengembalikan string yang sama persis, jadi `->` di `reason` tetap `->` di
   konsumen. Yang TIDAK dijanjikan modul ini: keamanan `innerHTML` — Layar A/B (TASKS 3.2)
   tetap wajib merender lewat `textContent`.
6. **Tidak ada aset dari host.** `docs_url`/`redoc_url` dimatikan: halaman Swagger bawaan
   FastAPI memuat JS/CSS dari CDN pihak ketiga yang tidak diputuskan ADR mana pun.

7. **Satu aturan penamaan cacah** (:data:`COUNT_NAMING_RULE`, TASKS 3.1b): `n_x`/`n_x_total`
   selalu cacah SEBENARNYA, `n_x_rendered` selalu yang ikut di respons ini. Sebelumnya `n_items`
   dan `n_warnings` bersebelahan di `/api/decision` sambil menghitung hal berbeda, sehingga
   kartu ber-20.000 peringatan tampil sebagai "100 peringatan" — angka yang mengecilkan jumlah
   kegagalan. Ketiga daftar yang bisa dipotong (`items`, `warnings`, `blocking`) punya batas
   render DAN kalimat di `notes` yang menyebut totalnya.

Yang SENGAJA tidak ada (scope PM 4 Sep 2026): paginasi, pencarian, filter, dan penulisan apa
pun. Batas jumlah baris dipatok konstanta modul dan **disebut di respons** (`limit`), supaya
"20 keputusan" tidak pernah terbaca sebagai "hanya ada 20 keputusan".
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent.ledger import DecisionRow, LedgerError, read_decision, recent_decisions
from agent.replay import (
    VERDICT_IDENTICAL,
    VERDICT_MISMATCH,
    VERDICT_NO_EVIDENCE,
    VERDICT_STALE_ALGO,
    VERDICT_STALE_PARAMS,
    ReplayReport,
    replay,
)
from agent.watchlist import plausible_symbol
from core.replay import ALGO
from core.skill import KEY_LIMIT, REASON_LIMIT, TEXT_LIMIT, UNSPOKEN, WARNING_LIMIT, clean_text
from core.store import Store, StoreError
from preflight.narrate import SUMMARY_KEY, Narration
from preflight.params import DEFAULT_PARAMS, ITEM_ORDER
from preflight.skill import (
    PreflightError,
    normalize_symbol,
    preflight,
    symbol_evidence,
    symbol_tools,
)
from ui.screens import (
    CLIENT_SCRIPT,
    FONTS,
    IMAGES,
    KAGE_STYLESHEET,
    REASON_CEILINGS,
    STYLESHEET,
    VALUE_LIMIT,
    VENDOR,
    cut_note,
    render_card,
    render_error,
    render_kage,
    render_landing,
    render_watchlist,
)

log = logging.getLogger("ui.app")

#: Banner tetap — kalimat yang sama di tiap layar dan tiap respons (rules-compliance).
BANNER = "SIMULATED — practice positions on read-only market intelligence. No real funds."

#: Header setiap halaman HTML — dan setiap aset yang dilayaninya.
#:
#: ADR-020 butir 5 melonggarkan SATU direktif dan tidak lebih: `script-src 'self'` menggantikan
#: larangan total, supaya `/ui/app.js` (hasil build `frontend/`, dilayani dari origin ini
#: sendiri) bisa berjalan. Yang TIDAK berubah, dan yang menjadikan pelonggaran itu sempit:
#: `default-src 'none'` tetap menutup segalanya yang tidak disebut, `style-src 'self'` tetap
#: hanya mengizinkan `/ui/tenko.css`, dan TIDAK ADA `'unsafe-inline'`, TIDAK ADA
#: `'unsafe-eval'`, serta nol host pihak ketiga — jadi skrip sebaris, `eval`, dan CDN mana
#: pun tetap ditolak browser, bukan hanya oleh sopan santun.
#:
#: ADR-022 membuka TIGA direktif aset — `img-src`, `font-src`, `media-src` — atas permintaan
#: pemilik repo, dan ketiganya ke `'self'`, BUKAN `*` dan BUKAN `data:`. Bedanya bukan gaya:
#: `'self'` membiarkan aset yang KITA layani hidup sambil membuat setiap aset pihak ketiga
#: tetap ditolak browser — dan itulah yang menjaga janji "nol permintaan jaringan saat
#: render" (ADR-014 butir 4) yang membuat `make demo` bisa jalan tanpa jaringan (spec §7).
#: `data:` sengaja TIDAK ditambahkan DI SINI: ia menghidupkan kembali permukaan penyuntikan
#: lewat nilai asal-RYO/DB yang seluruh modul `ui/screens.py` dibangun untuk menutup. Ia
#: ditambahkan pada `img-src` di SATU tempat lain — :data:`KAGE_HTML_HEADERS`, dipakai hanya
#: oleh `/kage`, halaman yang tidak membaca store sama sekali (ADR-026 butir 11).
#:
#: `connect-src 'self'` adalah direktif KEDUA yang dilonggarkan, dan tanpa ia ADR-020 tidak ada
#: gunanya: `connect-src` adalah fetch-directive yang fallback-nya `default-src`, jadi
#: `default-src 'none'` tanpa menyebutnya = `connect-src 'none'` = SETIAP `fetch()` klien
#: ditolak browser, TERMASUK ke origin sendiri (security review: TINGGI). Tidak ada browser di
#: jalur tes, jadi tidak ada yang merah — yang merah sekarang adalah tes header di bawah.
#: `'self'` DAN BUKAN LEBIH: klien hanya boleh bicara ke `/api/...` milik aplikasi ini.
HTML_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; font-src 'self'; media-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

#: SATU-SATUNYA pelonggaran CSP di aplikasi ini, dan ia hanya dipasang pada respons `/kage`.
#:
#: ADR-026 butir 2 berbunyi "CSP TIDAK dilonggarkan sepotong pun". Itu tidak lagi benar, dan
#: ADR-026 butir 11 mengoreksinya terbuka. Sebabnya: `kage-scene.js` melukis lapisan `#grain`
#: dengan `style.backgroundImage = 'url(' + canvas.toDataURL('image/png') + ')'` — sebuah URL
#: `data:`, yang `img-src 'self'` tolak TANPA gejala apa pun di server: halaman tetap 200, tes
#: tetap hijau, dan satu lapisan halaman itu diam-diam tidak pernah terlukis.
#:
#: Lingkupnya sesempit yang bisa: SATU direktif (`img-src`), SATU token (`data:`), SATU route.
#: Yang TIDAK ikut: `/` (landing), `/app` + `/app/decision/{id}` (Layar A & B — layar yang
#: dinilai juri dan satu-satunya yang merender nilai asal-RYO/DB ke DOM), seluruh `/api/...`,
#: seluruh route aset, dan setiap halaman GAGAL termasuk yang di sekitar `/kage` sendiri.
#: `script-src`/`style-src`/`connect-src`/`font-src`/`media-src` tidak disentuh, dan tidak ada
#: `unsafe-inline`/`unsafe-eval` yang masuk ke mana pun. Diturunkan dari `HTML_HEADERS`, bukan
#: disalin, supaya direktif yang berubah di sana tidak bisa tertinggal di sini.
KAGE_HTML_HEADERS = {
    **HTML_HEADERS,
    "Content-Security-Policy": HTML_HEADERS["Content-Security-Policy"].replace(
        "img-src 'self'", "img-src 'self' data:"
    ),
}

#: Jam tampilan: JST (spec §7 & deadline lomba). `ts_utc` tetap ikut supaya angkanya bisa dicek.
JST = timezone(timedelta(hours=9), "JST")

#: Baris `decisions` yang dibaca untuk menyusun satu layar. Bukan paginasi: batas kerja, dan
#: nilainya SELALU ikut ke respons (`limit`) supaya cacah yang terlihat tidak terbaca sebagai total.
DECISIONS_LIMIT = 50
WATCHLIST_SCAN_LIMIT = 200

#: Kedalaman maksimum saat membersihkan nilai item (`value` item 1 bersarang dua tingkat).
MAX_CLEAN_DEPTH = 6

#: Id `decisions` terbesar yang MASIH muat di INTEGER SQLite. Id di luar rentang ini tidak
#: pernah sampai ke `agent.ledger` (yang akan melempar `OverflowError` di luar `sqlite3.Error`,
#: sehingga respons keluar sebagai teks polos tanpa `simulated`): ia ditolak di batas route
#: sebagai 422, bentuk yang sama dengan `/api/decision/abc`.
MAX_DECISION_ID = 2**63 - 1

#: Batas render satu kartu. Kartu ditulis agen, bukan pemanggil, jadi ini bukan penjaga
#: terhadap masukan bermusuhan melainkan janji bahwa SATU baris `decisions` yang rusak/besar
#: tidak bisa membuat satu respons UI berukuran puluhan MB — dan setiap pemotongan DISEBUT
#: (`n_items_total`, `n_warnings_total`, `n_snapshots`), tidak pernah diam-diam.
MAX_ITEM_ROWS = 200
MAX_WARNING_ROWS = 100
MAX_BUNDLE_ROWS = 200
MAX_BLOCKING_ROWS = 200

#: SATU aturan penamaan cacah, supaya dua field bersebelahan tidak pernah menghitung hal berbeda
#: (security review 3.1b butir b). Sebelumnya `n_items` di `/api/decision` berarti TOTAL sementara
#: `n_warnings` di sebelahnya berarti YANG DIRENDER: pembaca Layar B yang melihat `n_warnings: 100`
#: pada baris ber-20.000 peringatan membaca angka yang MENGECILKAN jumlah kegagalan — arah
#: kesalahan paling mahal untuk produk ini. Aturannya sekarang, tanpa kekecualian:
#:
#: * ``n_<x>`` dan ``n_<x>_total`` = cacah SEBENARNYA di baris/kartu itu (dua nama, satu makna;
#:   ``_total`` dipertahankan karena Layar B sudah menyebut namanya di layar);
#: * ``n_<x>_rendered`` = berapa yang benar-benar ikut di respons INI.
#:
#: Arahnya dipilih begitu karena angka yang dibaca juri adalah cacah sebenarnya; berapa yang muat
#: di satu respons adalah urusan implementasi. Kalau salah satu field bergeser diam-diam, yang
#: tersisa adalah angka yang terlalu KECIL — jadi `n_<x>` dipatok pada makna yang tidak bisa
#: meremehkan apa pun, dan pemotongannya disebut di `notes`.
COUNT_NAMING_RULE = "`n_x` dan `n_x_total` = cacah sebenarnya; `n_x_rendered` = yang ikut di respons ini"

#: Kalimat yang dipakai berulang; dipatok tes supaya tidak menguap saat teks diedit.
LEGACY_SENTENCE = (
    "This decision was written by an implementation that no longer exists (its card does not "
    f"name the {ALGO} hash algorithm), so its items, blocking list and hash cannot be judged "
    "again under today's rules — the row is shown as it stands, with no invented numbers."
)
NO_EVIDENCE_SENTENCE = (
    "This row was booked without a single frozen snapshot, so there is no evidence to replay: "
    "the only thing that matches its stored hash is that absence itself. This is NOT "
    "verification."
)
STALE_PARAMS_SENTENCE = (
    "This row was written with thresholds (`params_hash`) different from the ones in force "
    "now, so we did NOT judge it at all — we did not find it wrong. Its evidence has not been "
    "checked: to check it, replay with the params that were used when this row was written. "
    "This is not verification, and it is not a finding either."
)
EMPTY_SENTENCE = "no data yet (n=0)"

#: Status kolom "daya veto" — WAJIB tercetak di Layar A DAN Layar B (laporan juri fase 5, #3).
#:
#: Kolom itu adalah :data:`preflight.params.ITEM_ORDER`, yaitu urutan spec §4: sebuah konstanta
#: DESAIN. Sampai kalimat ini ditulis tidak ada satu pun baris di kedua layar yang mengakuinya,
#: sehingga peringkat 1 terbaca sebagai temuan. Ia bukan — dan alat ukur repo ini sendiri
#: (`preflight/veto_table.py`, TASKS 2.1b) MEMBANTAHNYA dengan contoh: item peringkat 1
#: (`01_freshness`) tidak pernah menahan apa pun, sementara item peringkat 6 (`06_confluence`)
#: menahan puluhan kali. Sebabnya struktural dan diakui `preflight/skill.py`: pemilihan store
#: sudah membatasi umur bukti, jadi `01_freshness` adalah TANDA TERIMA, bukan veto.
#:
#: Yang TIDAK dilakukan di sini: mengubah urutannya. Urutan adalah milik `preflight/`, dan
#: layar yang mengarang urutan sendiri akan berbeda dari keputusan yang benar-benar diambil
#: agen. Yang dilakukan hanya memberi label jujur pada kolom yang sudah ada. Angka contohnya
#: sengaja kualitatif ("puluhan kali"): `preflight/veto_table.py` sendiri memperingatkan `n`-nya
#: bergerak selama perekam hidup, jadi angka statis di layar akan basi tanpa ada yang tahu.
VETO_RANK_UNVALIDATED_SENTENCE = (
    "The `veto power` column is NOT validated: its order is a spec §4 design constant "
    "(`preflight/params.py` ITEM_ORDER), not a measurement — and this repo's own instrument "
    "contradicts it. `preflight/veto_table.py` records `01_freshness` (rank 1) red ZERO times, "
    "while `06_confluence` (rank 6) is red dozens of times. For the numbers: "
    "`uv run python -m preflight.veto_table --db <copy of data/ryo.db>`."
)

#: Angka paling jujur di repo ini, ditaruh di layar yang paling mungkin dibuka (laporan juri
#: fase 5, #4). Ia KUALITATIF dengan sengaja, dan itu bukan kemalasan:
#:
#: * ADR-003 mengikat — `grading/` TIDAK boleh masuk jalur keputusan, dan
#:   `test_decision_path_never_imports_grading` memeriksa `core/`, `preflight/`, `agent/`, dan
#:   `ui/` nol impor `grading`. Jadi modul ini tidak boleh memanggil `grading.grader` untuk
#:   mendapat `k`/`n`/`target_first` yang hidup, betapapun menariknya angka itu;
#: * dan menaruh SALINAN angkanya (`k=0`, `target_first=3`, `n=13`) sebagai literal di sini
#:   akan menjadi angka yang membeku diam-diam: begitu satu outcome baru matang, layar akan
#:   mengutip laporan yang sudah tidak ada lagi — persis kelas kesalahan yang produk ini jual
#:   lawannya.
#:
#: Yang tersisa dan jujur: sebutkan ARAHnya (belum ada yang terbukti diselamatkan; ada veto
#: yang justru merugikan), sebutkan kenapa nol selang kepercayaan dicetak (`n` efektif di bawah
#: ambang spec §6), dan sebutkan PERINTAH yang mencetak angkanya hari ini.
GRADE_UNPROVEN_SENTENCE = (
    "These vetoes are NOT yet proven to have saved anything: `make grade` has recorded no "
    "loss demonstrably avoided, and some vetoes held back plans that later reached target. "
    "The effective n is below the threshold of 30, so zero confidence intervals are printed. "
    "This screen does NOT compute it (ADR-003: `grading` stays off the decision path) — run "
    "`make grade`."
)
WARNINGS_SENTENCE = (
    "{n} envelope warnings are shown verbatim in `warnings` — including evidence DROPPED "
    "before it was judged (e.g. a snapshot whose `ts_utc` and `as_of` disagree). The missing "
    "row is named here, not only in the log."
)

#: Nada tiap vonis = nama kelas CSS Layar A/B (TASKS 3.2). Yang mengikat: `finding` (kami
#: memeriksa dan menemukan) TIDAK PERNAH sama dengan `not_assessed` (kami tidak memeriksa),
#: supaya `MISMATCH` dan `STALE_PARAMS` yang berdampingan di layar tidak terbaca sama.
TONE_VERIFIED = "verified"
TONE_FINDING = "finding"
TONE_NOT_ASSESSED = "not_assessed"
TONE_NO_EVIDENCE = "no_evidence"
TONE_UNKNOWN = "unknown"

VERDICT_TONE: dict[str, str] = {
    VERDICT_IDENTICAL: TONE_VERIFIED,
    VERDICT_MISMATCH: TONE_FINDING,
    VERDICT_STALE_ALGO: TONE_NOT_ASSESSED,
    VERDICT_STALE_PARAMS: TONE_NOT_ASSESSED,
    VERDICT_NO_EVIDENCE: TONE_NO_EVIDENCE,
}

#: Teks pendek yang MENYERTAI nama vonis (tidak menggantikannya) — supaya juri tidak perlu tahu
#: kosakata internal untuk membedakan temuan dari ketidaktahuan.
VERDICT_LABEL: dict[str, str] = {
    VERDICT_IDENTICAL: "verified: the frozen evidence produces the same hash",
    VERDICT_MISMATCH: "finding: the evidence does not reproduce this decision",
    VERDICT_STALE_ALGO: "not assessed: this card does not name the hash algorithm in force",
    VERDICT_STALE_PARAMS: "not assessed: the thresholds changed after this row was written",
    VERDICT_NO_EVIDENCE: "not verified: this row was booked without a single piece of evidence",
}
UNKNOWN_LABEL = "not assessed: replay could not be run over this row"


class SafeJSONResponse(JSONResponse):
    """JSON yang tidak bisa menutup tag HTML mana pun.

    `<`, `>`, `&` ditulis sebagai escape JSON (`\\u003c`…). Struktur JSON sendiri tidak pernah
    memuat ketiganya di luar string, jadi penggantian ini hanya menyentuh isi string dan
    **lossless**: pembaca JSON mana pun mendapat teks aslinya kembali.
    """

    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        text = json.dumps(content, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        return text.encode("ascii")


app = FastAPI(
    title="Tenko (点呼) — practice roll call",
    description=BANNER,
    default_response_class=SafeJSONResponse,
    # Halaman /docs & /redoc bawaan memuat aset dari CDN pihak ketiga: dimatikan (checklist UI).
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
#: Path DB yang dipakai route. None = resolusi `core.store` (`DB_PATH`, default ./data/ryo.db).
#: Hanya tes yang memasangnya; TIDAK PERNAH diisi dari request (lihat butir 2 docstring modul).
app.state.db_path = None


# --- pelayan ------------------------------------------------------------------------------
def _open_store() -> Store:
    """Koneksi baca-saja baru untuk satu request.

    Dibuka per-request dengan sengaja: perekam menulis ke berkas yang sama, dan koneksi yang
    hidup lama membuat layar menampilkan snapshot lama tanpa mengatakannya.
    """
    return Store(app.state.db_path, read_only=True)


def _body(payload: dict[str, Any]) -> dict[str, Any]:
    """Bungkus respons: `simulated` & `banner` ditempel TERAKHIR supaya tidak bisa ditimpa."""
    return {**payload, "simulated": True, "banner": BANNER}


def _clean(value: Any, limit: int = TEXT_LIMIT, depth: int = 0) -> Any:
    """Nilai asal-DB dalam bentuk yang aman dicetak; angka & None dibiarkan apa adanya.

    Mengubah angka menjadi teks adalah kebohongan tersendiri (aturan yang sama dengan
    `preflight/items.py`), jadi yang disentuh hanya `str` — lewat sanitizer tunggal repo.
    """
    if isinstance(value, str):
        return clean_text(value, limit)
    if isinstance(value, dict) and depth < MAX_CLEAN_DEPTH:
        return {
            (clean_text(k, KEY_LIMIT) if isinstance(k, str) else k): _clean(v, limit, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list) and depth < MAX_CLEAN_DEPTH:
        return [_clean(entry, limit, depth + 1) for entry in value]
    if isinstance(value, dict | list):
        return f"[{type(value).__name__} nested deeper than {MAX_CLEAN_DEPTH} levels]"
    return value


def _jst(iso_utc: Any) -> str | None:
    """`2026-09-03T20:28:28Z` → `2026-09-04 05:28 JST`; bentuk yang tidak terbaca → None.

    None dikembalikan apa adanya: jam yang tidak terbaca tidak diganti jam sekarang.

    **Tiga jenis kegagalan, satu jawaban.** `as_of` datang dari envelope RYO dan disalin ke
    store tanpa validasi rentang (`core/store.py`), jadi `9999-12-31T15:00:00Z` bisa benar-benar
    ada di sebuah baris. `datetime.astimezone` atas jam itu melempar **`OverflowError`**, bukan
    `ValueError` — dan karena fungsi ini dipakai enam kali di tiga route, satu baris seperti itu
    memadamkan `/api/watchlist` DAN `/api/decisions` sekaligus dengan 500 teks polos yang TIDAK
    membawa `simulated` (kewajiban docs/rules-compliance.md). Ketiga jenisnya ditangkap DI SINI,
    bukan disaring di keenam pemanggil: jam yang tidak terbaca adalah `as_of_jst: null` di
    samping `as_of` mentah yang tetap ditampilkan apa adanya, bukan layar yang padam.
    """
    if not isinstance(iso_utc, str) or not iso_utc.strip():
        return None
    try:
        moment = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")
    except (ValueError, OverflowError, OSError):
        return None


def _rank(item_id: Any) -> int | None:
    """Peringkat daya veto (1 = terberat, spec §4). Id di luar :data:`ITEM_ORDER` → None."""
    if isinstance(item_id, str) and item_id in ITEM_ORDER:
        return ITEM_ORDER.index(item_id) + 1
    return None


def _sort_key(rank: int | None) -> int:
    """Kunci urut untuk peringkat yang mungkin tidak dikenal (ditaruh SESUDAH yang dikenal)."""
    return len(ITEM_ORDER) + 1 if rank is None else rank


# --- pembacaan kartu ----------------------------------------------------------------------
def _card_of(row: DecisionRow) -> dict[str, Any]:
    """`card_json` sebagai dict; kartu yang tidak terbaca → dict kosong (bukan exception)."""
    try:
        card = row.card()
    except LedgerError:
        log.warning("decision %s: card_json tidak terbaca", row.id)
        return {}
    return card


def _skill_of(card: dict[str, Any]) -> dict[str, Any]:
    skill = card.get("skill")
    return skill if isinstance(skill, dict) else {}


def _narration_lines(skill: dict[str, Any]) -> dict[str, str]:
    """`{item_id: kalimat}` dari `summary["narration"]` bila kartu membawanya."""
    summary = skill.get("summary")
    block = summary.get(SUMMARY_KEY) if isinstance(summary, dict) else None
    if not isinstance(block, dict):
        return {}
    lines = block.get("lines")
    if not isinstance(lines, list):
        return {}
    out: dict[str, str] = {}
    for line in lines:
        if isinstance(line, dict) and isinstance(line.get("item_id"), str):
            out[clean_text(line["item_id"], KEY_LIMIT)] = clean_text(str(line.get("text", "")), TEXT_LIMIT)
    return out


def _narration_block(skill: dict[str, Any], n_items: int) -> dict[str, Any]:
    """Blok narasi kartu ini, dalam bentuk `Narration.as_summary()` (status/n_lines/n_items/reason).

    UI **tidak pernah** memanggil LLM (ADR-003 & butir 2 docstring modul): kalau kartunya tidak
    membawa narasi, yang dikembalikan adalah `narration_unavailable` ber-sebab — bentuk yang
    sama, sehingga Layar B (TASKS 3.2) bisa menempelkan narasi di samping `reason` deterministik
    item yang sama dan melabelinya "tidak diverifikasi terhadap bukti" tanpa cabang khusus.
    """
    summary = skill.get("summary")
    block = summary.get(SUMMARY_KEY) if isinstance(summary, dict) else None
    if isinstance(block, dict) and block.get("status"):
        return _clean(block, WARNING_LIMIT)
    return Narration.unavailable(
        "no narration was stored with this decision; the UI never calls an LLM, so the card "
        "stands on its item list",
        n_items,
    ).as_summary()


def _items_of(skill: dict[str, Any], narration: dict[str, str]) -> tuple[list[dict[str, Any]], int]:
    """(baris tunjuk→sebut kartu ini urut daya veto, CACAH baris yang ada di kartu).

    Envelope penolakan (`data is None`) tetap menyebut kesepuluh baris panggilan lewat
    `availability` (`preflight._refusal`), jadi barisnya dibangun dari sana — dengan `value`,
    `source`, dan `reason` KOSONG, bukan diisi nol.

    Kartu sehat punya sepuluh baris. Yang dirender dibatasi :data:`MAX_ITEM_ROWS`, dan cacah
    kedua yang dikembalikan adalah cacah SEBENARNYA — supaya pemotongan (kalau pernah terjadi)
    muncul sebagai `n_items_total` di respons, bukan sebagai daftar yang diam-diam lebih pendek.
    """
    data = skill.get("data")
    raw = data.get("items") if isinstance(data, dict) else None
    rows: list[dict[str, Any]] = []
    n_total = 0
    if isinstance(raw, list) and raw:
        n_total = len(raw)
        for entry in raw[:MAX_ITEM_ROWS]:
            if not isinstance(entry, dict):
                continue
            source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
            item_id = clean_text(str(entry.get("id", "")), KEY_LIMIT)
            as_of = source.get("as_of")
            rows.append(
                {
                    "item_id": item_id,
                    "veto_rank": _rank(item_id),
                    "status": _clean(entry.get("status"), KEY_LIMIT),
                    "value": _clean(entry.get("value")),
                    "tool": _clean(source.get("tool"), KEY_LIMIT),
                    "field": _clean(source.get("field"), TEXT_LIMIT),
                    # `_clean` seperti SEMUA tetangganya: ini satu-satunya nilai asal-DB yang
                    # dulu lolos mentah, dan klien merender `snapshot_id` lewat `textContent`
                    # tanpa `<bdi>` — jadi U+202E/ESC/NUL di sana masuk DOM klien hidup-hidup
                    # (security review: SEDANG). Dibersihkan di batas API, tempat KEDUA
                    # perender membacanya, bukan di salah satunya saja.
                    "snapshot_id": _clean(source.get("snapshot_id"), KEY_LIMIT),
                    "as_of": _clean(as_of, KEY_LIMIT),
                    "as_of_jst": _jst(as_of),
                    # `REASON_LIMIT`, BUKAN `WARNING_LIMIT`: `core.skill.ItemResult` sudah
                    # membatasi `reason` di 800 sebelum baris dibukukan, jadi memotongnya lagi
                    # di 400 di sini akan membuang kalimat yang ADA di ledger — dan Layar B
                    # kemudian menyebutnya "terpotong sebelum dibukukan", yang tidak benar.
                    # Angkanya diimpor, tidak disalin: dua konstanta yang bisa berselisih diam-diam
                    # adalah persis cara cacat itu lahir (security review 3.2, TINGGI-1).
                    "reason": _clean(entry.get("reason"), REASON_LIMIT),
                    "narration": narration.get(item_id),
                }
            )
            # Kalimat "siapa yang memotong" ikut dalam respons, bukan hanya dalam HTML.
            # Server-render menurunkannya sendiri lewat `_cut_marks`; klien TIDAK memotong
            # apa pun, jadi tanpa dua field ini `...(truncated)` tetap terlihat sesudah polling
            # sementara penjelasnya hilang. Keduanya keluaran `ui.screens.cut_note` atas nilai
            # payload yang SAMA yang dibaca server-render — satu fungsi, dua perender.
            rows[-1]["value_cut_note"] = cut_note(rows[-1]["value"], VALUE_LIMIT)
            rows[-1]["reason_cut_note"] = cut_note(
                rows[-1]["reason"], REASON_LIMIT, ledger_limits=REASON_CEILINGS
            )
    else:
        availability = skill.get("availability")
        pairs = list(availability.items()) if isinstance(availability, dict) else []
        n_total = len(pairs)
        for item_id, marker in pairs[:MAX_ITEM_ROWS]:
            clean_id = clean_text(str(item_id), KEY_LIMIT)
            rows.append(
                {
                    "item_id": clean_id,
                    "veto_rank": _rank(clean_id),
                    "status": _clean(marker, KEY_LIMIT),
                    "value": None,
                    "tool": None,
                    "field": None,
                    "snapshot_id": None,
                    "as_of": None,
                    "as_of_jst": None,
                    "reason": "",
                    # Bentuk baris yang SAMA dengan cabang kartu sehat: tidak ada yang dipotong
                    # di sini, dan `None` mengatakan itu — bukan key yang hilang diam-diam.
                    "value_cut_note": None,
                    "reason_cut_note": None,
                    "narration": narration.get(clean_id),
                }
            )
    rows.sort(key=lambda row: (_sort_key(row["veto_rank"]), str(row["item_id"])))
    return rows, n_total


def _warnings_of(skill: dict[str, Any]) -> tuple[list[str], int]:
    """(`warnings` envelope skill apa adanya, cacah SEBENARNYA) — dibatasi, tidak diringkas.

    Di sinilah bukti yang DIBUANG disebut. `core.store.latest_fresh` melewati snapshot yang
    `ts_utc`-nya bertengkar dengan `as_of`-nya melebihi toleransi (`ts_utc` tidak dijaga hash
    apa pun, `as_of` dijaga `payload_hash`), dan `preflight.skill` meneruskan notanya ke
    `warnings`. Kalau layar menelannya, sebuah baris hilang dari kartu tanpa satu kalimat pun
    yang mengatakannya — dan item kesegaran tampak memveto tanpa sebab. UI tidak menafsirkan
    isinya (mencocokkan teks modul lain akan rapuh); ia hanya menolak membuangnya.
    """
    raw = skill.get("warnings")
    if not isinstance(raw, list):
        return [], 0
    lines = [clean_text(str(entry), WARNING_LIMIT) for entry in raw[:MAX_WARNING_ROWS]]
    return lines, len(raw)


def _blocking_of(row: DecisionRow, skill: dict[str, Any]) -> tuple[list[str], int]:
    """(id item penahan urut daya veto, CACAH SEBENARNYA). HANYA untuk baris generasi berjalan.

    Sumbernya `decisions.blocking_json`; kartu dipakai sebagai cadangan saat kolomnya tidak
    terbaca. Baris warisan tidak pernah sampai ke sini (isinya integer, bukan id item).

    Dipotong :data:`MAX_BLOCKING_ROWS` dengan alasan yang sama seperti `items`/`warnings`, dan
    ditemukan belakangan karena alasan yang layak dicatat: `blocking` adalah field TERBESAR
    KETIGA di respons yang sama, jadi memotong dua yang lain saja membuat satu baris
    `/api/watchlist` tetap bisa membengkak ratusan kilobyte oleh daftar id penahan. Yang dipotong
    adalah EKORNYA — daftar sudah diurutkan daya veto, jadi penahan terberat (satu-satunya yang
    dibaca `top_blocker` dan judul Layar B) tidak pernah ikut terbuang.
    """
    try:
        raw: list[Any] = row.blocking()
    except LedgerError:
        data = skill.get("data")
        candidate = data.get("blocking") if isinstance(data, dict) else None
        raw = candidate if isinstance(candidate, list) else []
    ids = sorted(
        (clean_text(str(entry), KEY_LIMIT) for entry in raw),
        key=lambda item_id: (_sort_key(_rank(item_id)), item_id),
    )
    return ids[:MAX_BLOCKING_ROWS], len(ids)


def _counts_of(skill: dict[str, Any], items: list[dict[str, Any]], n_total: int) -> dict[str, int]:
    """`n_items`/`n_unspoken`/… dari kartu; bila tidak ada, DIHITUNG dari baris yang ada."""
    data = skill.get("data")
    counts: dict[str, int] = {}
    if isinstance(data, dict):
        for key in ("n_items", "n_spoken", "n_green", "n_red", "n_unspoken"):
            value = data.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                counts[key] = value
    if counts.get("n_items") is None:
        counts["n_items"] = n_total
    if counts.get("n_unspoken") is None:
        counts["n_unspoken"] = sum(1 for row in items if row["status"] == UNSPOKEN)
    return counts


def _verdict_of(report: ReplayReport, row: DecisionRow) -> dict[str, Any]:
    """Vonis replay dalam bentuk yang tidak bisa dibaca sebagai lencana hijau kecuali memang.

    `verified` DIAMBIL dari :attr:`ReplayReport.verified` (`VERIFIED_VERDICTS = {IDENTICAL}`),
    bukan dari nama vonis yang dicocokkan sendiri di sini.
    """
    usable, total = report.verifiable if report.verifiable is not None else (None, None)
    # `ReplayReport.algo` hanya diisi untuk vonis yang MEMPERSOALKAN algoritma; untuk vonis lain
    # nilainya diambil dari kartu baris ini sendiri, supaya `algo: null` di respons berarti
    # "baris ini tidak menyebut algoritma" (generasi lama) dan bukan "field ini tidak diisi".
    algo = report.algo if report.algo is not None else row.algo()
    return {
        "replay_status": report.verdict,
        "replay_tone": VERDICT_TONE.get(report.verdict, TONE_UNKNOWN),
        "replay_label": VERDICT_LABEL.get(report.verdict, UNKNOWN_LABEL),
        "verified": report.verified,
        "detail": clean_text(report.detail, WARNING_LIMIT),
        "stored_hash": clean_text(report.stored_hash, KEY_LIMIT),
        "computed_hash": (
            None if report.computed_hash is None else clean_text(report.computed_hash, KEY_LIMIT)
        ),
        "algo": None if algo is None else clean_text(algo, KEY_LIMIT),
        "n_snapshots_verifiable": usable,
        "n_snapshots_recorded": total,
        "replay_command": f"make replay ID={report.decision_id}",
    }


def _unreadable(what: str, exc: Exception) -> HTTPException:
    """503 yang menyebut JENIS kegagalan, bukan pesannya: pesan store memuat path berkas."""
    log.warning("%s: store tidak terbaca: %s", what, type(exc).__name__)
    return HTTPException(status_code=503, detail=f"{what} cannot be served ({type(exc).__name__})")


def _report_for(row: DecisionRow, store: Store) -> ReplayReport | None:
    """Vonis replay baris ini; None bila replay sendiri tidak bisa dijalankan (dicatat, bukan ditebak)."""
    try:
        return replay(row.id, store, DEFAULT_PARAMS)
    except (LedgerError, StoreError) as exc:
        log.warning("decision %s: replay tidak bisa dijalankan: %s", row.id, type(exc).__name__)
        return None


def _unknown_verdict() -> dict[str, Any]:
    return {
        "replay_status": None,
        "replay_tone": TONE_UNKNOWN,
        "replay_label": UNKNOWN_LABEL,
        "verified": False,
        "detail": "replay could not be run over this row; do not read this as verified",
        "stored_hash": None,
        "computed_hash": None,
        "algo": None,
        "n_snapshots_verifiable": None,
        "n_snapshots_recorded": None,
        "replay_command": None,
    }


def _summary_row(row: DecisionRow, store: Store) -> dict[str, Any]:
    """Satu baris ringkas untuk Layar A & daftar keputusan (generasi berjalan saja)."""
    card = _card_of(row)
    skill = _skill_of(card)
    items, n_item_rows = _items_of(skill, _narration_lines(skill))
    blocking, n_blocking_total = _blocking_of(row, skill)
    counts = _counts_of(skill, items, n_item_rows)
    by_id = {entry["item_id"]: entry for entry in items}
    top = by_id.get(blocking[0]) if blocking else None
    report = _report_for(row, store)
    try:
        n_snapshots = len(row.snapshot_ids())
    except LedgerError:
        n_snapshots = 0
    summary = skill.get("summary") if isinstance(skill.get("summary"), dict) else {}
    n_warnings_total = _warnings_of(skill)[1]
    return {
        "decision_id": row.id,
        "symbol": clean_text(row.symbol, KEY_LIMIT),
        "decision": clean_text(row.decision, KEY_LIMIT),
        "ts_utc": clean_text(row.ts_utc, KEY_LIMIT),
        "ts_jst": _jst(row.ts_utc),
        "as_of": _clean(skill.get("as_of"), KEY_LIMIT),
        "as_of_jst": _jst(skill.get("as_of")),
        "headline": _clean(summary.get("headline"), TEXT_LIMIT),
        "blocking": blocking,
        # `n_blocking` TETAP cacah sebenarnya walau `blocking` dipotong (COUNT_NAMING_RULE) —
        # urutan Layar A dihitung dari angka ini, dan simbol dengan 20.000 penahan tidak boleh
        # naik ke atas hanya karena daftarnya tidak muat.
        "n_blocking": n_blocking_total,
        "n_blocking_total": n_blocking_total,
        "n_blocking_rendered": len(blocking),
        "top_blocker": None
        if top is None
        else {
            "item_id": top["item_id"],
            "veto_rank": top["veto_rank"],
            "status": top["status"],
            "tool": top["tool"],
            "field": top["field"],
            "as_of": top["as_of"],
            "as_of_jst": top["as_of_jst"],
            "reason": top["reason"],
        },
        "veto_rank": None if top is None else top["veto_rank"],
        "n_items": counts.get("n_items", n_item_rows),
        "n_items_total": counts.get("n_items", n_item_rows),
        "n_unspoken": counts.get("n_unspoken", 0),
        "n_snapshots": n_snapshots,
        # Baris ringkas tidak membawa `warnings` sama sekali, jadi tidak ada yang "dirender":
        # yang disebut hanya cacah sebenarnya, sesuai aturan yang sama.
        "n_warnings": n_warnings_total,
        "n_warnings_total": n_warnings_total,
        **(_unknown_verdict() if report is None else _verdict_of(report, row)),
    }


def _current_rows(store: Store, limit: int) -> tuple[list[DecisionRow], int]:
    """(baris generasi BERJALAN, cacah baris warisan yang dilewati) dari `limit` baris terbaru."""
    rows = recent_decisions(store.path, limit=limit)
    current = [row for row in rows if row.is_current_generation()]
    return current, len(rows) - len(current)


def _tone_note(entries: list[dict[str, Any]]) -> str | None:
    """Satu kalimat pembeda saat `MISMATCH` dan `STALE_PARAMS` muncul di layar yang sama.

    Keduanya sama-sama BUKAN lencana hijau, dan justru karena itu mudah terbaca sama. Kalimat
    ini menyebut cacahnya (n) supaya perbedaannya bisa dihitung, bukan hanya dirasakan.
    """
    n_finding = sum(1 for entry in entries if entry["replay_tone"] == TONE_FINDING)
    n_unassessed = sum(1 for entry in entries if entry["replay_tone"] == TONE_NOT_ASSESSED)
    if not n_unassessed:
        return None
    return (
        f"{n_unassessed} rows carry the `{TONE_NOT_ASSESSED}` tone (STALE_ALGO/STALE_PARAMS): "
        "the rules used to write them have changed, so we did NOT check their evidence at all. "
        f"That is not a finding; rows carrying the `{TONE_FINDING}` tone (MISMATCH, "
        f"n={n_finding}) are findings — their evidence was checked and does not reproduce the "
        "decision. These two tones must appear in different colours on screen."
    )


def _row_truncation_note(entries: list[dict[str, Any]]) -> str | None:
    """Satu kalimat bila ada baris ringkas yang daftar `blocking`-nya dipotong.

    Tiap baris sudah membawa `n_blocking` (total) di samping `n_blocking_rendered`, tetapi Layar A
    menampilkan daftar itu apa adanya: pemotongan harus terbaca di halaman yang sama, bukan hanya
    bisa dihitung dari dua field oleh pembaca yang curiga.
    """
    cut = [entry for entry in entries if entry["n_blocking_rendered"] < entry["n_blocking"]]
    if not cut:
        return None
    worst = max(entry["n_blocking"] for entry in cut)
    return (
        f"{len(cut)} rows on this screen are held by more than {MAX_BLOCKING_ROWS} items (most: "
        f"{worst}); their `blocking` lists are cut at that render bound, while each row's "
        "`n_blocking` stays the true count and the screen order is computed from that count."
    )


def _truncation_notes(
    n_items: int,
    n_items_total: int,
    n_warnings: int,
    n_warnings_total: int,
    n_blocking: int = 0,
    n_blocking_total: int = 0,
) -> list[str]:
    """Kalimat untuk setiap daftar yang DIPOTONG saat dirender. Kosong bila tidak ada.

    Kartu ditulis agen, bukan pemanggil, jadi pemotongan ini tidak pernah terjadi pada baris
    yang sehat (sepuluh item, segelintir peringatan). Kalau sekali waktu terjadi, ia harus
    terbaca sebagai pemotongan — daftar yang lebih pendek tanpa kalimat adalah bentuk lain dari
    "angka tanpa n".

    Ketiga daftar yang bisa dipotong disebut di sini, termasuk `blocking`: batas render yang
    tidak punya kalimat sendiri adalah batas yang hilang dari layar.
    """
    notes: list[str] = []
    if n_items_total > n_items:
        notes.append(
            f"this card holds {n_items_total} roll-call rows; only {n_items} are rendered "
            f"(render bound {MAX_ITEM_ROWS}). `n_items_total` is the true count."
        )
    if n_warnings_total > n_warnings:
        notes.append(
            f"{n_warnings_total} envelope warnings sit on this row; only {n_warnings} are "
            f"rendered (render bound {MAX_WARNING_ROWS}). `n_warnings_total` is the true count, "
            "and not one of them was deleted from the store."
        )
    if n_blocking_total > n_blocking:
        notes.append(
            f"{n_blocking_total} rows hold this decision back; only {n_blocking} blocker ids "
            f"are rendered in `blocking` (render bound {MAX_BLOCKING_ROWS}; the tail of the "
            "list is dropped — the heaviest blocker is always kept). `n_blocking` is the true "
            "count."
        )
    return notes


# --- layar HTML (TASKS 3.2) ---------------------------------------------------------------
def _now_jst() -> datetime:
    """Jam render, JST. Dipakai HANYA untuk kalimat umur di layar — tidak masuk hash apa pun."""
    return datetime.now(tz=JST)


def _html(
    body: str,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    policy: dict[str, str] | None = None,
) -> Response:
    """Satu-satunya cara halaman keluar dari modul ini — termasuk halaman GAGAL.

    `headers` hanya MENAMBAH (mis. `Allow` pada 405); ia tidak bisa mencopot header kebijakan,
    karena halaman tanpa CSP adalah halaman yang mengizinkan skrip.

    `policy` adalah SATU-SATUNYA cara memilih kebijakan lain, dan satu-satunya pemanggilnya
    adalah `/kage` dengan :data:`KAGE_HTML_HEADERS`. Ia parameter tersendiri (bukan bagian
    `headers`) justru supaya pelonggaran itu tidak bisa datang dari pemanggil HTTP: default-nya
    tetap `HTML_HEADERS` untuk setiap halaman lain, termasuk halaman GAGAL.
    """
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        status_code=status_code,
        headers={**(headers or {}), **(policy or HTML_HEADERS)},
    )


#: Prefix satu-satunya keluarga route HTML. Dipakai handler kegagalan untuk memutuskan bentuk
#: jawaban: di bawah prefix ini juri sedang melihat LAYAR, jadi 404/405/422/500 pun wajib keluar
#: sebagai halaman ber-banner + CSP, bukan JSON telanjang (security review 3.2, TINGGI-2).
HTML_PREFIX = "/ui"

#: Halaman gambar (`render_kage`). Ia berdiri di luar `/ui` karena ia bukan layar juri — tetapi
#: ia tetap LAYAR: `POST /kage` dan salah ketik di sekitarnya wajib menjawab halaman ber-banner
#: + CSP, bukan JSON telanjang, dengan alasan yang sama persis seperti `/`.
KAGE_PATH = "/kage"


def _is_screen(request: Request) -> bool:
    """Apakah permintaan ini menuju layar HTML?

    Dijawab dari PATH, bukan dari header `Accept`: `Accept` datang dari pemanggil, dan
    membiarkannya memilih bentuk berarti `/api/decision/abc` bisa menjawab HTML tanpa
    `simulated: true` di badan JSON-nya. Path `/ui...` adalah milik kami sendiri — dan sejak
    TASKS 3.5, `/` juga: ia MENYAJIKAN Layar A, jadi `POST /` yang dijawab JSON telanjang akan
    kehilangan banner di route yang paling mungkin dibuka orang.
    """
    path = request.url.path
    return path == "/" or path == KAGE_PATH or path == HTML_PREFIX or path.startswith(HTML_PREFIX + "/")


def _fail(
    request: Request,
    status_code: int,
    message: str,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> Response:
    """Kegagalan dalam BENTUK yang sedang dilihat pemanggil, tanpa pernah kehilangan banner.

    Layar → halaman HTML ber-banner + CSP; API → JSON ber-`simulated: true`. Perakitan HTML-nya
    dijaga `try`: kalau renderer sendiri yang gagal, jawabannya turun ke JSON yang selalu bisa
    dirakit — bukan ke "Internal Server Error" teks polos tanpa `simulated`.
    """
    if _is_screen(request):
        try:
            return _html(render_error(status_code, message, BANNER, _now_jst()), status_code, headers)
        except Exception:  # noqa: BLE001 — renderer gagal tidak boleh menghapus banner
            log.error("%s %s: render halaman gagal", request.method, request.url.path)
    return SafeJSONResponse(
        status_code=status_code,
        content=_body(
            {
                "error": clean_text(message, WARNING_LIMIT),
                "status_code": status_code,
                **(extra or {}),
            }
        ),
        headers=headers,
    )


@app.api_route("/ui/tenko.css", methods=["GET", "HEAD"])
def stylesheet() -> Response:
    """Gaya layar — hasil build `frontend/styles/app.css`, dilayani dari origin ini sendiri.

    Sejak ADR-020 ia lahir lewat esbuild dan tinggal di `ui/static/app.css`, yang **dilacak
    git**: clone tanpa Node sama sekali tetap bisa `make ui`. Yang tidak berubah: nol CDN, nol
    webfont, nol aset yang diunduh saat runtime, dan nol berkas pihak ketiga yang disalin ke
    repo ini.
    """
    return Response(content=STYLESHEET, media_type="text/css; charset=utf-8", headers=HTML_HEADERS)


@app.api_route("/ui/fonts/{name}", methods=["GET", "HEAD"])
def font(name: str) -> Response:
    """Satu face IBM Plex (SIL OFL 1.1, ADR-022), dari daftar TERTUTUP.

    `name` datang dari request, jadi ia tidak pernah menyentuh filesystem: ia dicocokkan ke
    :data:`ui.screens.FONTS` yang dibaca sekali saat impor. Berkas yang tidak ada di daftar itu
    adalah 404, bukan pembacaan disk — tidak ada `..`, tidak ada path absolut, tidak ada
    symlink yang bisa dibujuk, karena tidak ada path yang dibangun dari masukan sama sekali.

    OFL 1.1 menuntut lisensinya ikut terdistribusi bersama fontnya; ia ada di
    `ui/static/fonts/LICENSE.txt` (dilacak git) DAN dilayani di sini sebagai `LICENSE.txt`.
    """
    blob = FONTS.get(name)
    if blob is None:
        raise HTTPException(status_code=404, detail=f"no font is served as {name!r}")
    media = "text/plain; charset=utf-8" if name.endswith(".txt") else "font/woff2"
    return Response(content=blob, media_type=media, headers=HTML_HEADERS)


@app.api_route("/ui/img/{name}", methods=["GET", "HEAD"])
def image(name: str) -> Response:
    """Satu gambar halaman `/kage`, dari daftar TERTUTUP :data:`ui.screens.IMAGES`.

    Aturannya sama dengan `/ui/fonts/{name}` dan bukan kebetulan: `name` datang dari request,
    jadi ia dicocokkan ke dict yang dibaca sekali saat impor dan TIDAK PERNAH dipakai untuk
    membangun path filesystem. Nama di luar daftar adalah 404, bukan pembacaan disk.
    """
    blob = IMAGES.get(name)
    if blob is None:
        raise HTTPException(status_code=404, detail=f"no image is served as {name!r}")
    return Response(content=blob, media_type="image/webp", headers=HTML_HEADERS)


@app.api_route("/ui/vendor/{name}", methods=["GET", "HEAD"])
def vendor_script(name: str) -> Response:
    """three.js r149 (MIT) atau `kage-scene.js` — daftar TERTUTUP, dari origin ini sendiri.

    Keduanya dilayani di sini justru supaya `script-src 'self'` tidak perlu dilonggarkan: CDN
    mana pun akan ditolak browser, dan janji "nol permintaan jaringan saat render" (ADR-014
    butir 4) tetap berdiri. `kage-scene.js` membangkitkan seluruh teksturnya sendiri dan tidak
    memuat satu berkas pun saat berjalan.
    """
    blob = VENDOR.get(name)
    if blob is None:
        raise HTTPException(status_code=404, detail=f"no script is served as {name!r}")
    media = "text/plain" if name.endswith(".txt") else "text/javascript"
    return Response(content=blob, media_type=f"{media}; charset=utf-8", headers=HTML_HEADERS)


@app.api_route("/ui/kage.css", methods=["GET", "HEAD"])
def kage_stylesheet() -> Response:
    """Gaya halaman `/kage` — berkas tulisan tangan yang dilacak git, bukan artefak esbuild."""
    return Response(content=KAGE_STYLESHEET, media_type="text/css; charset=utf-8", headers=HTML_HEADERS)


@app.api_route("/ui/app.js", methods=["GET", "HEAD"])
def client_script() -> Response:
    """Klien TypeScript yang sudah dibundel (`frontend/src/` → esbuild → `ui/static/app.js`).

    Ia dilayani dari origin ini sendiri karena itulah satu-satunya sumber yang diizinkan
    `script-src 'self'`. Isinya dibaca SEKALI saat impor `ui.screens`; route ini tidak pernah
    menyentuh berkas berdasarkan apa pun yang datang dari request.
    """
    return Response(content=CLIENT_SCRIPT, media_type="text/javascript; charset=utf-8", headers=HTML_HEADERS)


@app.api_route("/app", methods=["GET", "HEAD"])
@app.api_route("/ui", methods=["GET", "HEAD"])
def screen_watchlist() -> Response:
    """Layar A — HTML atas badan respons `/api/watchlist` yang sama persis.

    Fungsi route JSON dipanggil langsung (bukan HTTP ke diri sendiri) supaya layar dan API
    tidak bisa menyimpang: satu sumber angka, satu urutan, satu daftar catatan.

    `HEAD` ikut dideklarasikan: FastAPI tidak menambahkannya sendiri seperti Starlette, dan
    `curl -I /ui` yang dijawab 405 menyatakan halaman ini tidak ada padahal ia ada.
    """
    try:
        payload = watchlist()
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "the watchlist cannot be served"
        return _html(render_error(exc.status_code, detail, BANNER, _now_jst()), exc.status_code)
    return _html(render_watchlist(payload, BANNER, _now_jst()))


@app.api_route("/app/decision/{decision_id}", methods=["GET", "HEAD"])
@app.api_route("/ui/decision/{decision_id}", methods=["GET", "HEAD"])
def screen_card(decision_id: int = PathParam(ge=1, le=MAX_DECISION_ID)) -> Response:
    """Layar B — kartu panggilan satu keputusan, dirender di server, tanpa satu pun skrip.

    404/503 pun keluar sebagai halaman ber-banner SIMULATED, bukan JSON telanjang: layar yang
    gagal tetap layar, dan juri yang salah ketik id tidak boleh melihat halaman tanpa banner.
    Itu berlaku untuk id di LUAR rentang juga (`/ui/decision/0`, `/ui/decision/abc`): keduanya
    ditolak `PathParam` sebelum fungsi ini dipanggil, dan yang menjadikannya halaman adalah
    :func:`_fail` di handler `RequestValidationError` — bukan kalimat ini.
    """
    try:
        payload = decision(decision_id)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "this card cannot be served"
        return _html(render_error(exc.status_code, detail, BANNER, _now_jst()), exc.status_code)
    return _html(render_card(payload, BANNER, _now_jst()))


# --- route --------------------------------------------------------------------------------
#: Daftar route yang bisa ditemukan, sekarang dari `/api/routes` (TASKS 3.5). Ia dipatok
#: konstanta supaya "route yang ada" dan "route yang diumumkan" bisa DIBANDINGKAN oleh tes,
#: bukan hanya dijanjikan docstring.
ROUTE_INDEX = (
    "/",
    "/app",
    "/app/decision/{id}",
    "/ui",
    "/ui/decision/{id}",
    "/kage",
    "/ui/tenko.css",
    "/ui/kage.css",
    "/ui/app.js",
    "/ui/fonts/{name}",
    "/ui/img/{image}",
    "/ui/vendor/{script}",
    "/api/routes",
    "/api/health",
    "/api/watchlist",
    "/api/decisions",
    "/api/decision/{id}",
    "/api/preflight/{symbol}",
)


@app.api_route("/", methods=["GET", "HEAD"])
def index() -> Response:
    """`/` ADALAH landing page (ADR-021) — Layar A pindah ke `/app`.

    **200 HTML, bukan 3xx ke `/app`.** Alasannya tidak berubah sejak ADR-020: `curl` tanpa `-L`
    menerima badan KOSONG dari sebuah 302, jadi respons PERTAMA yang dilihat pemanggil akan
    menjadi respons tanpa banner SIMULATED — dan `docs/rules-compliance.md` menuntut banner itu
    ada di layar yang dilihat orang, bukan di layar berikutnya.

    Halaman ini TIDAK pernah gagal. Papan keberangkatannya dirender dari store yang sama dengan
    aplikasi; store yang tidak terbaca menghilangkan PAPANnya, bukan halamannya, dan tidak
    pernah diganti baris contoh (spec §8: data placeholder sebagai nyata tidak dinilai). Itulah
    kenapa `HTTPException` ditelan di sini, dan hanya di sini.

    `/ui` dan `/ui/decision/{id}` tetap dilayani sebagai alias `/app` (ADR-021 butir 3) supaya
    README, `agent/demo.py`, dan `scripts/chaos.sh` tidak mati di tengah minggu deadline.
    """
    try:
        entries = watchlist().get("symbols")
    except HTTPException:
        entries = None
    except Exception:  # noqa: BLE001 — landing tidak boleh jatuh karena store yang aneh
        entries = None
    rows = entries if isinstance(entries, list) else None
    return _html(render_landing(BANNER, _now_jst(), rows))


@app.api_route("/kage", methods=["GET", "HEAD"])
def screen_kage() -> Response:
    """Halaman gambar `/kage` — aset yang dibawa masuk pemilik repo, disambungkan apa adanya.

    **`/` tidak dibajak.** Layar A tetap `/app`, landing yang dinilai tetap `/` (ADR-021), dan
    halaman ini berdiri di route sendiri: ia tidak membaca store, tidak memutuskan apa pun, dan
    angka-angka di dalamnya adalah teks desain (:data:`ui.screens.KAGE_NOTE` mengatakannya di
    layar). Menaruhnya di `/` akan menempatkan prosa hiasan di depan satu-satunya layar yang
    menjawab pertanyaan juri — dan menempatkan angka yang bukan pengukuran di tempat yang paling
    mungkin dibaca sebagai pengukuran.

    Ia tidak bisa gagal karena sebab data: satu-satunya masukannya adalah aset statis yang sudah
    ada di memori sejak impor, dan banner SIMULATED-nya dirender server seperti di layar lain.

    Ini SATU-SATUNYA respons di aplikasi ini yang membawa :data:`KAGE_HTML_HEADERS` — CSP yang
    sama persis dengan yang lain kecuali `img-src 'self' data:`, karena lapisan `#grain`-nya
    dilukis dari `canvas.toDataURL()`. Alasan lengkap ada di konstanta itu dan di ADR-026
    butir 11; Layar A/B, `/`, dan `/api/...` tidak ikut.
    """
    return _html(render_kage(BANNER, _now_jst()), policy=KAGE_HTML_HEADERS)


@app.get("/api/routes")
def routes() -> dict[str, Any]:
    """Indeks route untuk konsumen API — rumah baru daftar yang dulu ada di `/`."""
    return _body(
        {
            "product": "Tenko (点呼)",
            "routes": list(ROUTE_INDEX),
            "screens": {"A_watchlist": "/app", "B_call_card": "/app/decision/{id}"},
            # `/kage` sengaja BUKAN salah satu "screens": ia tidak menjawab pertanyaan juri mana
            # pun dan tidak membaca store sama sekali. Ia disebut supaya daftar ini tetap jujur.
            "scenery": {
                "path": "/kage",
                "reads_store": False,
                "note": ("an illustrated page; every figure on it is design copy, not a measurement"),
            },
            "calls_ryo_tools": False,
            "calls_llm": False,
        }
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Apakah DB terbaca, dan berapa banyak keputusan tiap generasi — tanpa memanggil apa pun."""
    try:
        with _open_store() as store:
            rows = recent_decisions(store.path, limit=WATCHLIST_SCAN_LIMIT)
    except (StoreError, LedgerError) as exc:
        raise _unreadable("health", exc) from None
    current = [row for row in rows if row.is_current_generation()]
    return _body(
        {
            "db_readable": True,
            "calls_ryo_tools": False,
            "calls_llm": False,
            "algo": ALGO,
            "params_hash": DEFAULT_PARAMS.params_hash(),
            "scan_limit": WATCHLIST_SCAN_LIMIT,
            "n_decisions_scanned": len(rows),
            "n_decisions_current_generation": len(current),
            "n_decisions_legacy": len(rows) - len(current),
            "note": (
                f"the counts above cover the {WATCHLIST_SCAN_LIMIT} MOST RECENT rows, not the whole table"
            ),
        }
    )


@app.get("/api/watchlist")
def watchlist() -> dict[str, Any]:
    """Layar A: satu baris per simbol, diurutkan daya veto item penahannya.

    Urutannya menjawab "apa yang menahan yang lain": NO_GO dulu, yang penahannya paling BERAT
    (peringkat spec §4 terkecil) di atas; simbol tanpa penahan (GO) menyusul. Yang ditampilkan
    hanya keputusan TERAKHIR tiap simbol, dan hanya generasi berjalan (TASKS 3.1a).
    """
    try:
        with _open_store() as store:
            rows, n_legacy = _current_rows(store, WATCHLIST_SCAN_LIMIT)
            latest: dict[str, DecisionRow] = {}
            for row in rows:  # recent_decisions: terbaru dulu
                latest.setdefault(clean_text(row.symbol, KEY_LIMIT), row)
            entries = [_summary_row(row, store) for row in latest.values()]
    except (StoreError, LedgerError) as exc:
        raise _unreadable("watchlist", exc) from None

    entries.sort(
        key=lambda entry: (
            0 if entry["n_blocking"] else 1,
            _sort_key(entry["veto_rank"]),
            -entry["n_blocking"],
            entry["symbol"],
        )
    )
    notes = [
        "ordered by veto power: the heaviest blocker (spec §4 rank 1) on top; symbols with no blocker follow",
        VETO_RANK_UNVALIDATED_SENTENCE,
        GRADE_UNPROVEN_SENTENCE,
    ]
    tone_note = _tone_note(entries)
    if tone_note:
        notes.append(tone_note)
    cut_note = _row_truncation_note(entries)
    if cut_note:
        notes.append(cut_note)
    if n_legacy:
        notes.append(
            f"{n_legacy} decision rows from an earlier generation are not shown here: their "
            f"cards do not name the {ALGO} hash algorithm, so their items and blockers cannot "
            "be judged again under today's rules (see /api/decision/{id})"
        )
    if not entries:
        notes.append(EMPTY_SENTENCE)
    return _body(
        {
            "n": len(entries),
            "scan_limit": WATCHLIST_SCAN_LIMIT,
            "n_legacy_hidden": n_legacy,
            "symbols": entries,
            "notes": notes,
        }
    )


@app.get("/api/decisions")
def decisions() -> dict[str, Any]:
    """Keputusan terbaru (generasi berjalan saja), tanpa paginasi — scope PM 4 Sep 2026."""
    try:
        with _open_store() as store:
            rows, n_legacy = _current_rows(store, DECISIONS_LIMIT)
            entries = [_summary_row(row, store) for row in rows]
    except (StoreError, LedgerError) as exc:
        raise _unreadable("decisions", exc) from None
    notes = [f"at most {DECISIONS_LIMIT} MOST RECENT rows; no pagination, no server-side filter"]
    tone_note = _tone_note(entries)
    if tone_note:
        notes.append(tone_note)
    cut_note = _row_truncation_note(entries)
    if cut_note:
        notes.append(cut_note)
    if n_legacy:
        notes.append(
            f"{n_legacy} rows from an earlier generation were skipped (their cards do not name "
            f"the {ALGO} hash algorithm); each row can still be opened one by one via "
            "/api/decision/{id}"
        )
    if not entries:
        notes.append(EMPTY_SENTENCE)
    return _body(
        {
            "n": len(entries),
            "limit": DECISIONS_LIMIT,
            "n_legacy_skipped": n_legacy,
            "min_id": min((entry["decision_id"] for entry in entries), default=None),
            "decisions": entries,
            "notes": notes,
        }
    )


@app.get("/api/decision/{decision_id}")
def decision(decision_id: int = PathParam(ge=1, le=MAX_DECISION_ID)) -> dict[str, Any]:
    """Layar B: kartu panggilan satu keputusan + bundle replay + vonisnya.

    Tiga bentuk jawaban, semuanya 200 selama barisnya ADA (TASKS 3.1a):

    * **generasi berjalan** — 10 baris tunjuk→sebut, `decision`, `blocking`, `decision_hash`,
      bundle snapshot beku, dan vonis replay (`IDENTICAL`/`MISMATCH`/`NO_EVIDENCE`/`STALE_PARAMS`);
    * **ambang bergeser** — `replay_status: "STALE_PARAMS"` (TASKS 0.14), `verified: false`,
      nada `not_assessed`, dan satu kalimat yang menyatakan ketidaktahuan. Sejak `params_hash`
      bergeser (TASKS 0.12), keempat baris nol-bukti `data/ryo.db` 20-23 ada di sini: cabang
      `NO_EVIDENCE` bersyarat `computed == stored`, jadi kami tidak lagi bisa membuktikan bahwa
      barisnya memang ditulis tanpa bukti;
    * **nol bukti** — `replay_status: "NO_EVIDENCE"`, `verified: false`, dan satu kalimat sebab,
      untuk baris nol-bukti yang params-nya MASIH berlaku. Bukan lencana hijau;
    * **warisan** — `replay_status: "STALE_ALGO"`, `items: []`, `blocking` TIDAK dirender.

    Id di luar `1..MAX_DECISION_ID` ditolak **di batas route** (422), sebelum satu pernyataan
    SQL pun dirakit: id ≥ 2**63 membuat `sqlite3` melempar `OverflowError`, yang bukan
    `sqlite3.Error` dan karena itu lolos dari seluruh penangkap `agent/ledger.py` maupun route
    ini — satu GET tanpa autentikasi cukup untuk memaksa 500 teks polos TANPA `simulated`.
    """
    try:
        with _open_store() as store:
            row = read_decision(store.path, decision_id)
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"there is no decision with id {decision_id} in this ledger",
                )
            report = _report_for(row, store)
            verdict = _unknown_verdict() if report is None else _verdict_of(report, row)
            legacy = not row.is_current_generation()
            card = _card_of(row)
            skill = _skill_of(card)
            try:
                snapshot_ids = row.snapshot_ids()
            except LedgerError:
                snapshot_ids = []
            bundle = [
                {
                    "snapshot_id": snapshot.id,
                    "tool": clean_text(snapshot.tool, KEY_LIMIT),
                    "as_of": _clean(snapshot.as_of, KEY_LIMIT),
                    "as_of_jst": _jst(snapshot.as_of),
                    "status": _clean(snapshot.status, KEY_LIMIT),
                }
                for snapshot in store.get_snapshots(snapshot_ids[:MAX_BUNDLE_ROWS])
            ]
    except HTTPException:
        raise
    except (StoreError, LedgerError) as exc:
        raise _unreadable(f"decision {decision_id}", exc) from None

    items, n_item_rows = ([], 0) if legacy else _items_of(skill, _narration_lines(skill))
    counts = {} if legacy else _counts_of(skill, items, n_item_rows)
    warnings, n_warnings_total = ([], 0) if legacy else _warnings_of(skill)
    blocking, n_blocking_total = ([], 0) if legacy else _blocking_of(row, skill)
    summary = skill.get("summary") if isinstance(skill.get("summary"), dict) else {}
    notes: list[str] = [] if legacy else [VETO_RANK_UNVALIDATED_SENTENCE]
    if legacy:
        notes.append(LEGACY_SENTENCE)
        notes.append(
            "The `blocking_json` column of this row holds integers (indices of an "
            "implementation that no longer exists), not roll-call item ids, so it is NOT "
            "rendered here."
        )
    elif verdict["replay_status"] == VERDICT_NO_EVIDENCE:
        notes.append(NO_EVIDENCE_SENTENCE)
    elif verdict["replay_status"] == VERDICT_STALE_PARAMS:
        notes.append(STALE_PARAMS_SENTENCE)
    if not items and not legacy:
        notes.append(f"this decision card holds no roll-call row at all ({EMPTY_SENTENCE})")
    if not bundle:
        notes.append(f"zero frozen snapshots on this row ({EMPTY_SENTENCE})")
    if warnings:
        notes.append(WARNINGS_SENTENCE.format(n=n_warnings_total))
    notes.extend(
        _truncation_notes(
            len(items), n_item_rows, len(warnings), n_warnings_total, len(blocking), n_blocking_total
        )
    )
    if len(snapshot_ids) > len(bundle):
        notes.append(
            f"this row names {len(snapshot_ids)} frozen snapshots; only the first {len(bundle)} "
            f"are rendered in `replay_bundle` (render bound {MAX_BUNDLE_ROWS}). `n_snapshots` "
            "stays the true count, and `make replay` replays ALL of them."
        )

    return _body(
        {
            "decision_id": row.id,
            "symbol": clean_text(row.symbol, KEY_LIMIT),
            "decision": clean_text(row.decision, KEY_LIMIT),
            "ts_utc": clean_text(row.ts_utc, KEY_LIMIT),
            "ts_jst": _jst(row.ts_utc),
            # `as_of` bundle = jam ENVELOPE, dijaga `payload_hash`; `ts_utc` di atas adalah jam
            # penulisan baris ledger dan TIDAK dijaga hash apa pun. Layar B menghitung umur
            # bundle dari field ini, bukan dari `ts_utc` (TASKS 3.2, amandemen TINGGI-2).
            "as_of": _clean(skill.get("as_of"), KEY_LIMIT),
            "as_of_jst": _jst(skill.get("as_of")),
            "generation": "legacy" if legacy else "current",
            "headline": None if legacy else _clean(summary.get("headline"), TEXT_LIMIT),
            "blocking": blocking,
            "blocking_rendered": not legacy,
            "n_blocking": n_blocking_total,
            "n_blocking_total": n_blocking_total,
            "n_blocking_rendered": len(blocking),
            "items": items,
            "n_items": counts.get("n_items", n_item_rows),
            "n_items_total": n_item_rows,
            "n_items_rendered": len(items),
            "n_unspoken": counts.get("n_unspoken", 0),
            "warnings": warnings,
            # TOTAL, bukan yang dirender (COUNT_NAMING_RULE): `n_warnings` bersebelahan dengan
            # `n_items` di layar, dan dua tetangga yang menghitung hal berbeda akan salah dibaca.
            "n_warnings": n_warnings_total,
            "n_warnings_total": n_warnings_total,
            "n_warnings_rendered": len(warnings),
            "narration": None if legacy else _narration_block(skill, n_item_rows),
            "decision_hash": clean_text(row.decision_hash, KEY_LIMIT),
            "params_hash": clean_text(row.params_hash, KEY_LIMIT),
            "snapshot_ids": snapshot_ids,
            "n_snapshots": len(snapshot_ids),
            "replay_bundle": bundle,
            **verdict,
            "notes": notes,
        }
    )


@app.get("/api/preflight/{symbol}")
def preflight_symbol(symbol: str) -> dict[str, Any]:
    """Panggilan pra-berangkat untuk satu simbol — store-first, tanpa satu panggilan RYO pun.

    Dua saringan bentuk, keduanya **400**, keduanya definisi BERSAMA (bukan salinan):
    :func:`preflight.skill.normalize_symbol` ("bisakah teks ini jadi kunci pencarian store",
    kembar dengan perekam) dan :func:`agent.watchlist.plausible_symbol` ("apakah ia berbentuk
    ticker sama sekali", TASKS 0.11). Tanpa yang kedua, `--`, `ＳＯＬ` (lebar), dan `ВТС`
    (Kiril) dijawab 404 "tidak ada bukti TENTANG …" — kalimat yang MENYIRATKAN simbolnya sah
    dan hanya belum direkam, padahal agen tidak akan pernah memutuskan simbol berbentuk itu.

    Simbol yang tidak bisa dibaca → **400**. Simbol yang tidak punya SATU pun bukti di store →
    **404 jujur** (`simulated: true` tetap ada): tidak ada yang bisa dipanggil untuk simbol yang
    tidak pernah direkam, dan mengembalikan kartu berisi sepuluh baris kosong akan terlihat
    seperti penilaian padahal tidak ada yang dinilai.

    "Punya bukti" dijawab :func:`preflight.skill.symbol_evidence` — SATU definisi yang dipakai
    bersama `agent.main` (TASKS 0.13), bukan salinan di sini. Salinan lama menanyakan
    `symbol in json.dumps(args)`; karena `json.dumps({}) == "{}"`, simbol `{}`/`{`/`}` membuat
    dua tool market-wide (`market_overview`, `monitor_market_sentiment_shift`) terhitung
    ber-simbol dan route ini menjawab 200 dengan kalimat yang salah. Definisi sekarang bertanya
    "apakah argumen tool ini berubah bila simbolnya berubah", jadi tool market-wide tidak pernah
    bisa disebut di catatan, dan `M` di kalimat "N dari M tool ber-simbol" adalah 3.
    """
    normalized = normalize_symbol(symbol)
    if normalized is None:
        raise HTTPException(status_code=400, detail="symbol cannot be read; nothing was called")
    if not plausible_symbol(normalized):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{clean_text(normalized, KEY_LIMIT)} is not shaped like a symbol (not "
                "single-character letters/digits), so nothing was looked up; the agent refuses "
                "the same shape, and this screen must not promise that it is merely unrecorded"
            ),
        )
    try:
        with _open_store() as store:
            result = preflight(normalized, store=store)
    except (StoreError, PreflightError) as exc:
        raise _unreadable(f"preflight {normalized}", exc) from None

    # Dua bentuk dari satu hasil: `raw` (untuk baris item, yang membersihkan tiap field dengan
    # batasnya SENDIRI — `reason` di `REASON_LIMIT`) dan `envelope` (yang diterbitkan apa adanya,
    # tiap teksnya dibatasi `WARNING_LIMIT`). Membangun item dari `envelope` akan memotong
    # `reason` di 400 dan membuat route ini menyajikan kalimat yang lebih pendek daripada
    # /api/decision untuk item yang sama.
    raw = result.model_dump(mode="json")
    envelope = _clean(raw, WARNING_LIMIT)
    spoken_about = symbol_evidence(result.data, normalized)
    if result.data is None or not spoken_about:
        raise HTTPException(
            status_code=404,
            detail=(
                f"there is no evidence ABOUT {normalized} in the store within the last "
                f"{DEFAULT_PARAMS.max_age_min} minutes "
                f"({', '.join(sorted(symbol_tools(normalized))) or '-'} did not answer); only "
                "market-wide evidence was readable, and that does not speak about this symbol"
            ),
        )
    skill = envelope if isinstance(envelope, dict) else {}
    warnings, n_warnings_total = _warnings_of(skill)
    items, n_item_rows = _items_of(raw, {})
    counts = _counts_of(skill, items, n_item_rows)
    data = skill.get("data") if isinstance(skill.get("data"), dict) else {}
    # Batas render yang sama dengan `/api/decision` (`_blocking_of`), dan cacah sebenarnya di
    # sampingnya: route ini merakit `blocking`-nya sendiri dari envelope yang baru dihitung,
    # jadi batas yang hanya dipasang di sana tidak akan mengikat di sini.
    all_blocking = sorted(
        (clean_text(str(entry), KEY_LIMIT) for entry in data.get("blocking", []) or []),
        key=lambda item_id: (_sort_key(_rank(item_id)), item_id),
    )
    blocking = all_blocking[:MAX_BLOCKING_ROWS]
    return _body(
        {
            "symbol": normalized,
            "decision": _clean(data.get("decision"), KEY_LIMIT),
            "blocking": blocking,
            "n_blocking": len(all_blocking),
            "n_blocking_total": len(all_blocking),
            "n_blocking_rendered": len(blocking),
            "as_of": _clean(skill.get("as_of"), KEY_LIMIT),
            "as_of_jst": _jst(skill.get("as_of")),
            "items": items,
            "n_items": counts.get("n_items", n_item_rows),
            "n_items_total": n_item_rows,
            "n_items_rendered": len(items),
            "n_unspoken": counts.get("n_unspoken", 0),
            "warnings": warnings,
            "n_warnings": n_warnings_total,
            "n_warnings_total": n_warnings_total,
            "n_warnings_rendered": len(warnings),
            "snapshot_ids": data.get("snapshot_ids", []),
            "decision_hash": _clean(data.get("decision_hash"), KEY_LIMIT),
            "params_hash": _clean(data.get("params_hash"), KEY_LIMIT),
            "narration": Narration.unavailable(
                "the UI never calls an LLM; run the skill with credentials to attach narration",
                n_item_rows,
            ).as_summary(),
            "envelope": skill,
            "notes": [
                "recomputed from snapshots ALREADY stored; no RYO tool was called",
                f"{len(spoken_about)} of {len(symbol_tools(normalized))} symbol-bearing tools "
                f"hold evidence about {normalized}: {', '.join(spoken_about)}",
                "this decision is not booked: /api/preflight writes no `decisions` row",
                *([WARNINGS_SENTENCE.format(n=n_warnings_total)] if warnings else []),
                *_truncation_notes(
                    len(items),
                    n_item_rows,
                    len(warnings),
                    n_warnings_total,
                    len(blocking),
                    len(all_blocking),
                ),
            ],
        }
    )


# --- kegagalan tetap membawa `simulated` --------------------------------------------------
@app.exception_handler(StarletteHTTPException)
def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
    """404/400/405/503 pun wajib membawa `simulated: true` (docs/rules-compliance.md).

    Didaftarkan pada HTTPException **Starlette**, bukan turunannya di FastAPI: 404 untuk rute
    yang tidak dikenal (`/favicon.ico`) dan 405 untuk metode yang salah (`POST /api/health`)
    dilempar router Starlette sebagai kelas induk itu, dan pencarian handler menyusuri MRO —
    jadi satu pendaftaran ini menutup keduanya SEKALIGUS `fastapi.HTTPException` yang dilempar
    route di berkas ini. Sebelumnya keduanya keluar sebagai `{"detail": "..."}` telanjang.

    Bentuknya mengikuti PATH: `/ui/nope` dan `POST /ui` menjawab halaman ber-banner + CSP,
    `/favicon.ico` dan `POST /api/health` tetap JSON ber-`simulated`.
    """
    detail = exc.detail if isinstance(exc.detail, str) else "this request cannot be served"
    # `Allow` pada 405 dibawa apa adanya dari exception, karena menjawab "metode salah" tanpa
    # menyebut metode yang benar adalah jawaban setengah — dan ia ikut ke halaman HTML juga.
    return _fail(request, exc.status_code, detail, dict(exc.headers) if exc.headers else None)


@app.exception_handler(RequestValidationError)
def _validation_error(request: Request, exc: RequestValidationError) -> Response:
    """Path/query yang tidak bisa dibaca (mis. `/api/decision/abc`) — jujur, tanpa traceback.

    Di bawah `/ui` jawabannya HALAMAN, bukan JSON: `ge=1` membuat `/ui/decision/0` (salah ketik
    yang wajar) berakhir di sini, dan juri yang salah ketik tidak boleh melihat respons tanpa
    banner SIMULATED maupun tanpa CSP.
    """
    return _fail(
        request,
        422,
        "the request parameters cannot be read; nothing was read from the store",
        extra={"n_errors": len(exc.errors())},
    )


@app.exception_handler(Exception)
def _unexpected_error(request: Request, exc: Exception) -> Response:
    """Jaring terakhir: bug pun tidak boleh menjawab tanpa `simulated: true`.

    Tanpa handler ini `ServerErrorMiddleware` menjawab `text/plain` "Internal Server Error" —
    respons yang tidak mengaku simulasi, yaitu persis bentuk yang dilarang aturan lomba. Jenis
    kegagalannya dicatat ke log (nama kelas saja: pesan exception bisa memuat path berkas),
    dan yang keluar ke pemanggil hanya satu kalimat tanpa traceback — sebagai halaman di bawah
    `/ui` (renderer yang ikut gagal turun ke JSON), sebagai JSON di tempat lain. Starlette tetap
    melempar ulang `exc` sesudah respons ini terkirim, jadi kegagalannya tidak hilang dari log
    server — yang berubah hanya isi jawabannya.
    """
    log.error("%s %s: kegagalan tak terduga: %s", request.method, request.url.path, type(exc).__name__)
    return _fail(
        request,
        500,
        "this request failed on an unexpected server error; no number was invented to cover it up",
    )
