"""`ui/screens.py` — Layar A (watchlist) & Layar B (kartu panggilan), HTML dirender di server.

Fungsi di sini **murni**: masuk `dict` (persis badan respons `/api/watchlist` dan
`/api/decision/{id}` dari :mod:`ui.app`), keluar `str` HTML. Tidak ada store, tidak ada jam
global selain `now` yang diberikan pemanggil, tidak ada I/O — jadi setiap kalimat yang muncul di
layar bisa diuji sebagai teks, bukan sebagai janji.

Kenapa TETAP dirender di server sesudah ADR-020
-----------------------------------------------
Warisan review 1.5 menuntut kartu **meng-escape sendiri** dan **mengisolasi arah teks**: U+202E
terbukti sampai ke stdout lewat `data` dan `ItemResult.value`, dan satu simbol ber-pembalik-arah
bisa membuat baris kartu terbaca TERBALIK di depan juri.

ADR-020 menambahkan klien TypeScript, dan yang TIDAK dilakukannya adalah memindahkan render ke
sana: modul ini tetap menghasilkan halaman LENGKAP — banner, tabel, tiap `aria-label` — sebelum
satu baris JS pun jalan (ADR-020 butir 6), dan klien yang gagal memuat meninggalkan layar yang
utuh, bukan kerangka kosong. CSP sekarang `script-src 'self'` (bukan lagi larangan total), jadi
permukaan `innerHTML` tidak lagi ditutup dengan "tidak ada JS sama sekali" melainkan dengan dua
aturan yang dipatok tes: klien HANYA memakai `textContent`/`createElement` (nol `innerHTML`,
nol `eval`), dan nilai apa pun dari RYO/DB tetap lewat dua lapis di bawah ini:

1. :func:`core.skill.clean_text` — sanitizer tunggal repo; aksara tak tercetak (termasuk U+202E)
   menjadi `\\u202e` yang TERLIHAT, bukan dibuang diam-diam;
2. :func:`html.escape` dengan `quote=True`, lalu **setiap** nilai asal-RYO/asal-DB dibungkus
   `<bdi>` sehingga arah teksnya diisolasi dari sel di sebelahnya.

Keduanya dilakukan di SATU tempat (:func:`_bdi`) dan tidak dititipkan ke lapisan lain — narasi
LLM ikut lewat pintu yang sama, karena `preflight.narrate` menetralkan kosakata untuk CLI, bukan
untuk HTML.

Yang WAJIB terbaca di layar (dan kenapa)
----------------------------------------
* **`reason` item dirender UTUH**, termasuk klausa "the clock is the newest as_of in this
  bundle …, not the wall clock" pada item 1. Memotongnya di "17.4 min (limit 120 min)" membuat
  kartu terbaca sebagai klaim kesegaran yang **tidak pernah dibuat kode**. Batasnya karena itu
  `core.skill.REASON_LIMIT` yang DIIMPOR — satu angka untuk ledger dan layar, bukan dua yang
  bisa berselisih diam-diam — dan bila ada yang terpotong, layar menyebut SIAPA yang memotong
  (:func:`_cut_marks`), tidak menuduh ledger atas potongannya sendiri.
* **`as_of` bundle + umurnya terhadap jam render**, berlabel, dihitung dari `as_of` (dijaga
  `payload_hash`) — **bukan** dari `ts_utc` (tidak dijaga hash apa pun). Umur itu TAMPILAN dan
  disebut demikian; ia tidak masuk `decision_hash`.
* **Hijau-netral ≠ hijau-konfirmasi** (amandemen ADR-017). Baris ber-`value.stance == "neutral"`
  mendapat penanda TEKSTUAL ("tidak menentang, bukan konfirmasi") yang terbaca tanpa hover dan
  ikut ke `aria-label`. Tidak ada status keempat yang ditambahkan ke `ItemStatus`.
* **Narasi model selalu berlabel** "narasi model — TIDAK diverifikasi terhadap bukti", dan selalu
  berdampingan dengan `reason` deterministik item yang sama. Isinya tidak diverifikasi terhadap
  bukti (hanya `item_id` yang divalidasi) dan larangan vonis di `SYSTEM_PROMPT` tidak ditegakkan
  satu baris kode pun; penjaga kata-kunci DITOLAK dengan alasan, jadi penutupnya adalah label ini.
  `narration_unavailable` ditampilkan apa adanya, bukan disembunyikan.
* **Pemotongan disebut**: `n_items_total`/`n_warnings_total`/`n_blocking`/`scan_limit`/
  `n_legacy_hidden`. Cacah yang dibaca layar SELALU cacah sebenarnya (`ui.app.COUNT_NAMING_RULE`);
  `n_*_rendered` hanya dipakai untuk mengatakan berapa yang muat.
* **Warna tidak pernah sendirian**: tiap lencana membawa teksnya (`replay_label`, status item,
  penanda netral), jadi layar tetap terbaca hitam-putih dan oleh pembaca layar.
"""

from __future__ import annotations

import re
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from core.skill import KEY_LIMIT, REASON_LIMIT, TEXT_LIMIT, TRUNCATED_MARK, WARNING_LIMIT, clean_text

#: Label yang menempel pada SETIAP narasi model. Dipatok tes: hilangnya label ini mengubah
#: "narasi tidak diverifikasi" menjadi "narasi tampak terverifikasi" — klaim menyesatkan yang
#: `docs/rules-compliance.md` sebut risiko diskualifikasi.
NARRATION_LABEL = "model narration — NOT verified against the evidence"
NARRATION_NOTE = (
    "The sentences in this column were written by a language model. Only their `item_id` is "
    "validated; their CONTENT is never checked against the evidence, and no code stops the "
    "model from writing a verdict. What can be replayed is the `reason` column beside it, not "
    "this column."
)
NARRATION_EMPTY = "no narration for this row"

#: Bahasa ISI bukti. Sejak ADR-020 bingkai halaman juga Inggris (`<html lang="en">`), jadi
#: penanda ini tidak lagi mengubah pelafalan; ia tetap dipasang karena ia menandai ASAL kalimat
#: (`preflight/`/RYO, bukan layar ini) — satu-satunya penanda tingkat-elemen yang melakukannya.
EVIDENCE_LANG = "en"

#: Batas panjang kolom `nilai`. Ia BUKAN batas kejujuran: `ui.app` sudah membersihkan tiap teks
#: di dalam `value` satu per satu, dan yang dibatasi di sini hanya panjang barisan `k=v` yang
#: dirakit layar. Kalau ia menggigit, layar MENGATAKANNYA dan menunjuk JSON mentahnya.
VALUE_LIMIT = 1500


#: Nama kelas CSS yang boleh diturunkan dari nilai asal-DB, dan HANYA ini.
#:
#: Dulu `str.isidentifier()`, yang menerima identifier Unicode: `status` bernilai `ｇreen`
#: (U+FF47) menghasilkan `class="s-ｇreen"` di server sementara klien (`render.ts`, regex ASCII)
#: menghasilkan `s-unknown` untuk baris yang SAMA — dua layar berbeda untuk satu baris. Bukan
#: lubang (kedua sisi meng-escape), tetapi divergensi, dan divergensi adalah cacat produk di
#: layar yang menjual "dua render, satu kalimat". Regex-nya ditulis identik dengan klien.
CSS_WORD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _css_word(text: str) -> bool:
    """Kembaran `render.ts:/^[A-Za-z_][A-Za-z0-9_]*$/` — satu aturan penamaan kelas, dua render."""
    return bool(CSS_WORD.match(text))


#: TIGA sebab yang WAJIB tidak tertukar, dan yang membedakannya adalah FAKTA terukur
#: (:func:`_cut_marks`), bukan keberadaan substring `...(truncated)` di teks yang dirender.
#:
#: 1. :data:`LEDGER_CUT_NOTE` — teksnya persis di SALAH SATU langit-langit `core.skill`
#:    (panjang `limit + len(TRUNCATED_MARK)` DAN berakhir dengan penandanya), jadi pemotongnya
#:    `ItemResult` sebelum baris dibukukan dan yang hilang memang tidak ada di ledger.
#:    Langit-langitnya JAMAK karena ledger ini append-only: 40 baris `data/ryo.db` yang paling
#:    mungkin dibuka juri dibukukan saat batasnya masih `TEXT_LIMIT`=240, dan kartu yang
#:    menjawab "tidak di langit-langit mana pun" untuk baris 254 aksara salah bicara tentang
#:    dirinya sendiri — kelas kesalahan yang sama dengan yang ditutup TINGGI-1. Yang disebut
#:    karena itu bukan hanya angkanya, melainkan ERA-nya: baris lama menyatakan ia dibukukan
#:    SEBELUM batasnya dinaikkan, sehingga pembaca tahu memuat ulang halaman tidak akan
#:    memunculkan kalimatnya;
#: 2. :data:`SCREEN_CUT_NOTE` — layar INI yang memotong, dan ia menyebut batasnya sendiri;
#: 3. :data:`FOREIGN_MARK_NOTE` — penandanya ADA tetapi tidak di langit-langit mana pun dan
#:    layar tidak memotong apa pun: yang jujur di sini adalah mengatakan kami tidak tahu siapa
#:    yang memotongnya, bukan mengaku menyembunyikan sesuatu yang tidak kami sembunyikan.
#:
#: Versi sebelumnya menabrak (1) dan (2) sekaligus: ia menyebut `TEXT_LIMIT`=240 padahal
#: `reason` dibatasi `REASON_LIMIT`, dan ia mengaku "dipotong sebelum dibukukan" untuk kalimat
#: yang justru dipotong layar ini sendiri (security review 3.2, TINGGI-1). Di layar yang seluruh
#: nilai jualnya kejujuran, kartu yang salah bicara tentang dirinya sendiri adalah cacat terburuk.
LEDGER_CUT_NOTE = (
    "this sentence was already cut BEFORE it was booked (`core.skill` ceiling {limit} "
    "characters; its length is exactly {total} characters, which is that ceiling); what is "
    "missing is in no ledger row at all, so this screen cannot bring it back.{era}"
)

#: Klausa yang menjelaskan langit-langit MANA yang cocok. `TEXT_LIMIT` disebut terlebih dahulu
#: supaya, kalau kedua konstanta kebetulan bernilai sama, yang menang adalah era BERJALAN
#: (klausa kosong) dan kartu tidak menuduh baris hari ini sebagai baris warisan.
LEDGER_CEILING_ERAS: dict[int, str] = {
    TEXT_LIMIT: (
        f" This row was booked while that ceiling was still `TEXT_LIMIT`={TEXT_LIMIT}, BEFORE "
        f"it was raised to `REASON_LIMIT`={REASON_LIMIT}: the sentence was never stored whole, "
        "so reloading this page will not bring it back — only rows recorded after that raise "
        "carry it."
    ),
    REASON_LIMIT: "",
}


def ledger_cut_note(limit: int) -> str:
    """Kalimat potongan-ledger untuk satu langit-langit, berikut ERA-nya bila ia bukan yang kini."""
    return LEDGER_CUT_NOTE.format(
        limit=limit,
        total=limit + len(TRUNCATED_MARK),
        era=LEDGER_CEILING_ERAS.get(limit, ""),
    )


#: Nama lama, dipertahankan supaya tes & pembaca lama tidak menunjuk konstanta yang hilang.
REASON_CUT_NOTE = ledger_cut_note(REASON_LIMIT)
#: Baris yang dibukukan pada langit-langit lama — bentuk SELURUH 40 baris `data/ryo.db` hari ini.
LEGACY_REASON_CUT_NOTE = ledger_cut_note(TEXT_LIMIT)
SCREEN_CUT_NOTE = (
    "this text was cut BY this screen at {limit} characters; its full form is in the card JSON linked above"
)
VALUE_CUT_NOTE = SCREEN_CUT_NOTE.format(limit=VALUE_LIMIT)

#: Langit-langit ledger yang bisa dikenali pada kolom `reason`, urut dari yang berlaku KINI.
REASON_CEILINGS = (REASON_LIMIT, TEXT_LIMIT)
FOREIGN_MARK_NOTE = (
    f"this text carries the `{TRUNCATED_MARK}` marker, but its length sits at no ceiling at all "
    "and this screen did not cut it: if something is missing, it went missing before this row "
    "was booked — this screen is hiding nothing from it"
)

#: Penanda hijau-netral (amandemen ADR-017). Tekstual, bukan warna.
NEUTRAL_MARK = "neutral — not against it, and not a confirmation"
UNSPOKEN_MARK = "unspoken — the evidence is not enough, and that is not zero"

#: Batas nilai DI DALAM `aria-label`. Ia SAMA PERSIS dengan :data:`VALUE_LIMIT`, batas sel yang
#: dilihat mata, dan itulah seluruh maksudnya.
#:
#: Sebelumnya ia 200 sementara selnya 1500: `value` item yang berbentuk `k=v` sepuluh field
#: tampil utuh di layar tetapi dibacakan terpotong `...(truncated)`. Artinya pemakai pembaca
#: layar menerima LEBIH SEDIKIT bukti daripada pemakai awas — di produk yang seluruh nilai
#: jualnya "nilai ini datang dari sini", itu bukan ketidaknyamanan melainkan kehilangan isi.
#: Alasan lamanya ("batas kesabaran") tidak salah sebagai keluhan, tetapi ia menyelesaikan
#: kesabaran dengan memotong FAKTA; yang benar adalah memotong panjang di tempat yang tidak
#: membuang fakta, dan tempat itu tidak ada di baris kartu.
ARIA_VALUE_LIMIT = VALUE_LIMIT

#: Ongkos klausa NON-nilai pada satu `aria-label` baris kartu, pada batas terburuk tiap klausa:
#: `item_id`, daya veto, status, lalu tool/field/snapshot/as_of — masing-masing sudah dipotong
#: `clean_text` di batasnya sendiri, ditambah kata penghubungnya.
#:
#: Ia DIHITUNG, bukan ditebak, karena :func:`_aria` memotong dari BELAKANG: anggaran yang terlalu
#: kecil akan memakan klausa SUMBER — klausa yang justru "tunjuk"-nya tunjuk→sebut — dan
#: memotongnya diam-diam saat `value` kebetulan panjang.
ARIA_CLAUSE_BUDGET = 6 * (KEY_LIMIT + len(TRUNCATED_MARK)) + (TEXT_LIMIT + len(TRUNCATED_MARK)) + 320

#: Batas panjang SATU `aria-label`: cukup untuk nilai PENUH plus seluruh klausa di sekitarnya.
ARIA_LABEL_LIMIT = ARIA_VALUE_LIMIT + len(TRUNCATED_MARK) + ARIA_CLAUSE_BUDGET
#: Nilai yang tidak ada di bukti, dibacakan sebagai "tidak disebut" — TIDAK PERNAH sebagai 0 dan
#: tidak pernah sebagai kesunyian (ADR-013). Sel kosong yang terdengar seperti nol adalah
#: kebohongan yang paling murah dilakukan pembaca layar, dan produk ini persis menjual lawannya.
ARIA_NULL = "the value is not in the evidence — unspoken, and that is not zero"
ARIA_NO_SOURCE = "source not stated"

#: Sel yang nilainya memang TIDAK ADA. Sebelumnya ia em-dash abu-abu (`<span class="dim">—</span>`),
#: yang di layar terbaca seperti sebuah nilai — persis bentuk yang produk ini jual lawannya.
#: Sekarang ia kata, dengan warna `--warn` yang sama dengan `unspoken`: ketidaktahuan tidak boleh
#: sewarna dengan angka.
ABSENT_MARK = "not stated"
#: Kalimat keyboard yang tercetak di kaki SETIAP layar (TASKS 4.6). Ia bukan hiasan: "Esc tidak
#: punya yang harus ditutup" adalah klaim yang dipatok tes (nol dialog, nol details, nol tabindex).
KEYBOARD_NOTE = (
    "Keyboard: Tab / Shift+Tab walk the whole screen in document order, Enter opens the link "
    "or control that has focus, and that focus always carries a thick yellow ring. There is no "
    "modal, overlay, menu, or block that has to be opened — so Esc has nothing to close and "
    "there is no focus trap. Esc does one thing only: it clears the client-side filter box and "
    "returns you to the full list. No content appears only while a cursor hovers."
)

#: Kalimat umur bundle. `as_of` dijaga `payload_hash`; `ts_utc` tidak dijaga apa pun.
AGE_NOTE = (
    "This age is PRESENTATION: it is computed from the bundle `as_of` (guarded by "
    "`payload_hash`), not from the ledger row `ts_utc` (guarded by no hash at all), and it "
    "never enters `decision_hash`."
)
EMPTY_SENTENCE = "no data yet (n=0)"

#: Urutan kolom kartu; dipakai header dan sel supaya tidak bisa bergeser diam-diam.
#:
#: Ia SEBELUMNYA berisi tujuh judul untuk sembilan sel: satu judul, "sumber: tool.field ·
#: snapshot · as_of", berdiri di atas TIGA kolom sekaligus, sehingga judul terakhir
#: (:data:`NARRATION_LABEL`) jatuh di atas kolom `snapshot` dan dua kolom terakhir tidak
#: berjudul sama sekali. Di layar itu terbaca sebagai tabel yang bergeser; bagi pembaca layar
#: itu `<th scope="col">` yang menunjuk kolom yang salah. Judulnya kini satu per kolom, dan
#: :func:`tests.test_ui.test_the_card_table_has_one_header_for_every_column_it_renders`
#: menghitung keduanya supaya tidak bisa berselisih lagi.
#: Judul kolom pertama menyebut STATUS peringkatnya, bukan hanya namanya: ia konstanta desain
#: spec §4 yang belum diukur, dan kalimat panjangnya ada di `notes` (lihat
#: :data:`ui.app.VETO_RANK_UNVALIDATED_SENTENCE`). Judul yang berbunyi "daya veto" saja membuat
#: angka 1..10 terbaca sebagai temuan.
VETO_RANK_COLUMN = "veto power (spec §4, not validated)"

CARD_COLUMNS = (
    VETO_RANK_COLUMN,
    "item",
    "status",
    "reason (deterministic, replayable)",
    "value spoken",
    "source: tool.field",
    "snapshot",
    "evidence as_of",
    NARRATION_LABEL,
)

#: Judul kolom Layar A, satu per kolom — kembaran :data:`CARD_COLUMNS` untuk layar sebelah.
#: Ia dipisah menjadi konstanta karena klien TypeScript merender tabel yang SAMA setelah ia
#: menyegarkan datanya, dan dua daftar judul yang bisa berselisih adalah dua tabel yang berbeda.
WATCHLIST_COLUMNS = (
    "symbol",
    "decision",
    f"{VETO_RANK_COLUMN} — blocker",
    "what holds it (and where that came from)",
    "blockers / rows",
    "unspoken",
    "evidence",
    "replay",
    "bundle as_of",
)

#: Ikon banner: SVG **tertanam di HTML**, bukan berkas dan bukan URI tertanam — tidak ada
#: permintaan jaringan yang dibuatnya, jadi ia tetap muncul saat demo dijalankan tanpa jaringan
#: (spec §7) dan tidak menyentuh `sources.yaml` maupun `DISCLOSURE.md`. `aria-hidden` karena
#: kalimat di sebelahnya sudah membawa seluruh maknanya: ikon ini menambah SATU kanal bentuk,
#: tidak pernah menggantikan teks.
BANNER_ICON = (
    '<svg width="18" height="18" viewBox="0 0 20 20" aria-hidden="true" focusable="false" '
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" '
    'stroke-linecap="round">'
    '<path d="M10 2.6 18.6 17.4 1.4 17.4 Z"></path>'
    '<path d="M10 8 10 12"></path>'
    '<path d="M10 14.6 10 14.8"></path>'
    "</svg>"
)

#: Berkas gaya yang DILAYANI `/ui/tenko.css`. Ia hasil build esbuild dari
#: `frontend/styles/app.css` (ADR-020 butir 3) dan **dilacak git**, jadi clone tanpa Node sama
#: sekali tetap bisa `make ui` — Node adalah alat BUILD, bukan runtime (ADR-020 butir 2).
#: Dibaca SEKALI saat impor, dari path yang dihitung dari `__file__`: tidak ada path yang datang
#: dari request, dan tidak ada I/O per-permintaan.
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _read_build(name: str) -> str:
    """Isi satu artefak build. Hilangnya artefak = kegagalan KERAS, bukan halaman tanpa gaya."""
    path = STATIC_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover — hanya terjadi bila artefak terlacak dihapus
        raise RuntimeError(
            f"ui/static/{name} tidak terbaca. Artefak build ini DILACAK git dan ikut terkirim "
            "(ADR-020 butir 2); bangun ulang dengan `make frontend` (butuh Node) atau kembalikan "
            f"berkasnya dengan `git checkout -- ui/static/{name}`."
        ) from exc


#: Empat face IBM Plex + teks lisensinya, dibaca SEKALI saat impor dari daftar yang ditulis di
#: sini — bukan dari `iterdir()`. Bedanya nyata: berkas yang tersasar ke `ui/static/fonts/`
#: tidak otomatis ikut terlayani, dan yang dilayani selalu bisa dibaca manusia dari kode ini.
#: SIL OFL 1.1 (ADR-022); `LICENSE.txt` ikut dilayani karena lisensinya menuntut itu.
FONT_NAMES = (
    "IBMPlexMono-Regular.woff2",
    "IBMPlexMono-SemiBold.woff2",
    "IBMPlexSans-Regular.woff2",
    "IBMPlexSans-SemiBold.woff2",
    "LICENSE.txt",
    # Tujuh face halaman `/kage` (ADR-022 memperluas daftarnya, bukan aturannya): sudah di-subset
    # dan dilacak git seperti empat di atasnya. Nama file = nama yang dilayani; tidak ada
    # `iterdir()`, jadi berkas yang tersasar ke folder ini tetap TIDAK terlayani.
    "Onest-300.woff2",
    "Onest-400.woff2",
    "Onest-500.woff2",
    "Onest-700.woff2",
    "NotoJP-400.woff2",
    "Wordmark-500.woff2",
    "Wordmark-600.woff2",
)


def _read_font(name: str) -> bytes:
    path = STATIC_DIR / "fonts" / name
    try:
        return path.read_bytes()
    except OSError as exc:  # pragma: no cover — hanya bila aset terlacak dihapus
        raise RuntimeError(
            f"ui/static/fonts/{name} tidak terbaca. Aset ini DILACAK git dan ikut terkirim; "
            f"kembalikan dengan `git checkout -- ui/static/fonts/{name}`."
        ) from exc


FONTS = {name: _read_font(name) for name in FONT_NAMES}

STYLESHEET = _read_build("app.css")

#: Klien TypeScript yang sudah dibundel (`frontend/src/**.ts` → esbuild IIFE). Ia MENAMBAH
#: perilaku di atas HTML yang sudah lengkap: banner, tabel, dan tiap `aria-label` sudah dirender
#: server sebelum satu baris JS pun jalan (ADR-020 butir 6).
CLIENT_SCRIPT = _read_build("app.js")


# --- aset halaman `/kage` -----------------------------------------------------------------
#: Halaman `/kage` (papan gambar yang dibawa masuk pemilik repo) dilayani dari aset yang
#: DILACAK git dan dibaca SEKALI saat impor, dari daftar TERTUTUP yang ditulis di sini. Pola
#: yang sama dengan `FONTS`, dan alasannya sama: `name` datang dari request, jadi ia dicocokkan
#: ke sebuah dict — tidak ada path filesystem yang pernah dibangun dari masukan, sehingga tidak
#: ada `..`, path absolut, atau symlink yang bisa dibujuk.
IMAGE_NAMES = (
    # sepuluh lapisan latar depan (dirender `<img>` di `ui/static/kage.html`)
    "basalt-stones.webp",
    "garden-bush.webp",
    "hill.webp",
    "maple-leaves.webp",
    "pine-tree.webp",
    "sakura-branch.webp",
    "shrine-ruins.webp",
    "stone-lantern.webp",
    "tall-grass.webp",
    "temple-wall.webp",
    # empat yang hanya disebut CSS (`background-image` di `ui/static/kage.css`)
    "kage-approach.webp",
    "kage-lantern-court.webp",
    "kage-moonwater.webp",
    "kage-sanmon-preview.webp",
)

#: Dua skrip yang dilayani `/ui/vendor/{name}`: three.js r149 (MIT) dan scene prosedural
#: `kage-scene.js`. Keduanya dari origin ini sendiri karena `script-src 'self'` tidak
#: mengizinkan satu pun CDN, dan tidak satu pun dari keduanya mengunduh apa pun saat runtime.
#: `LICENSE.three.txt` ikut di daftar ini dengan alasan yang sama seperti `LICENSE.txt` ada di
#: `FONT_NAMES`: MIT menuntut notisnya ikut TERDISTRIBUSI bersama kodenya, dan lisensi yang hanya
#: ada di git tetapi tidak pernah dilayani adalah lisensi yang tidak ikut ke pemakainya.
VENDOR_NAMES = ("three.min.js", "kage-scene.js", "LICENSE.three.txt")


def _read_static(*parts: str) -> bytes:
    """Isi satu aset statis yang DILACAK git. Hilangnya aset = kegagalan KERAS saat impor."""
    path = STATIC_DIR.joinpath(*parts)
    try:
        return path.read_bytes()
    except OSError as exc:  # pragma: no cover — hanya bila aset terlacak dihapus
        rel = "/".join(("ui", "static", *parts))
        raise RuntimeError(
            f"{rel} tidak terbaca. Aset ini DILACAK git dan ikut terkirim; kembalikan dengan "
            f"`git checkout -- {rel}`."
        ) from exc


IMAGES = {name: _read_static("img", name) for name in IMAGE_NAMES}
VENDOR = {name: _read_static("vendor", name).decode("utf-8") for name in VENDOR_NAMES}

#: Gaya halaman `/kage`. Ia BUKAN artefak esbuild (tidak lewat `frontend/`): berkas tulisan
#: tangan yang dilacak git, dan satu-satunya berkas gaya halaman itu.
KAGE_STYLESHEET = _read_static("kage.css").decode("utf-8")

#: Badan halaman `/kage` — markup statis TANPA satu pun `<style>`/`<script>` sebaris maupun
#: atribut `style=` (CSP `default-src 'none'; script-src 'self'; style-src 'self'` menolak
#: ketiganya, dan tidak dilonggarkan untuk halaman ini). Ia disimpan sebagai berkas, bukan
#: sebagai literal Python, supaya markupnya bisa dibaca sebagai HTML — nol nilai asal-DB dan
#: asal-RYO masuk ke sini, jadi tidak ada yang perlu di-escape: satu-satunya teks dinamis di
#: halaman itu (banner dan jam render) dirakit :func:`render_kage`, yang meng-escape keduanya.
KAGE_BODY = _read_static("kage.html").decode("utf-8")


# --- escaping & pembungkusan --------------------------------------------------------------
def _bdi(value: Any, limit: int = TEXT_LIMIT, *, lang: str | None = None) -> str:
    """Satu-satunya pintu untuk nilai asal-DB/asal-RYO masuk ke HTML.

    Tiga hal sekaligus, dan sengaja tidak bisa dipakai setengah-setengah: aksara tak tercetak
    di-escape TERLIHAT (`\\u202e`), `<`/`>`/`&`/kutip di-escape HTML, lalu arah teksnya
    diisolasi `<bdi>` supaya satu nilai tidak bisa membalik sel tetangganya.

    `lang` menandai bahasa ISI, bukan bahasa bingkainya. Sejak ADR-020 bingkainya `lang="en"`
    dan isi bukti juga Inggris, jadi penanda ini tidak lagi mengubah pelafalan — ia
    DIPERTAHANKAN karena maknanya bukan "bahasa lain" melainkan "kalimat ini datang dari
    `preflight/`/RYO, bukan ditulis layar ini", dan itulah satu-satunya penanda tingkat-elemen
    yang membedakan keduanya. Penandanya dipasang di elemen yang sama dengan isolasi arah, jadi
    tidak ada nilai yang bisa lolos lewat pintu kedua.
    """
    if value is None:
        return f'<span class="empty">{escape(ABSENT_MARK, quote=True)}</span>'
    text = value if isinstance(value, str) else _scalar(value)
    tag = f'<bdi lang="{escape(lang, quote=True)}">' if lang else "<bdi>"
    return f"{tag}{escape(clean_text(text, limit), quote=True)}</bdi>"


def _scalar(value: Any) -> str:
    """Angka & bool apa adanya; struktur diringkas ke bentuk `k=v; k=v` yang masih terbaca."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, dict):
        return "; ".join(f"{_scalar(k)}={_scalar(v)}" for k, v in sorted(value.items(), key=_key))
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(entry) for entry in value) + "]"
    if value is None:
        return "null"
    return str(value)


def _plain(value: Any, limit: int = KEY_LIMIT) -> str:
    """Nilai asal-DB untuk tempat yang di-escape pemanggilnya (mis. `<caption>`/judul seksi).

    Ia TIDAK mengembalikan HTML: memakai :func:`_bdi` di sana akan membuat tag `<bdi>` sendiri
    ikut ter-escape dan terbaca sebagai teks di layar.
    """
    return clean_text(_scalar(value), limit)


def _key(pair: tuple[Any, Any]) -> str:
    return str(pair[0])


# --- aria-label ---------------------------------------------------------------------------
def _aria(*parts: Any) -> str:
    """Satu-satunya pintu `aria-label`: gabung → :func:`core.skill.clean_text` → escape kutip.

    `aria-label` adalah TEKS di dalam atribut, bukan HTML. Ia karena itu tidak boleh lewat
    :func:`_bdi` (tag `<bdi>` akan ikut terbaca sebagai nama) dan tidak boleh lewat `escape`
    saja: U+202E, NUL, dan ESC lolos dari `escape` — dan justru atribut inilah yang dibacakan
    pembaca layar (security review 3.2, SEDANG). Bagian yang kosong DIBUANG, bukan diisi
    "null": nama yang berbunyi "null" sama menyesatkannya dengan nama yang berbunyi "0".
    """
    words = [str(part).strip() for part in parts if part is not None and str(part).strip()]
    return escape(clean_text("; ".join(words), ARIA_LABEL_LIMIT), quote=True)


def _said(value: Any, limit: int = KEY_LIMIT) -> str | None:
    """Teks untuk `aria-label`, atau None bila memang tidak ada — tidak pernah `0`/`null`."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _plain(value, limit)


def _phrase(label: str, value: Any, absent: str, limit: int = KEY_LIMIT) -> str:
    """`label nilai`, atau kalimat `absent` bila nilainya memang tidak ada."""
    said = _said(value, limit)
    return f"{label} {said}" if said is not None else absent


def _text(value: Any, limit: int = WARNING_LIMIT) -> str:
    """Teks panjang (reason, detail, notes) — escape + isolasi arah, tanpa pemotongan sendiri.

    Batas panjangnya `WARNING_LIMIT`, sama dengan yang dipakai `ui.app` untuk `warnings`,
    `detail`, dan narasi. `reason` TIDAK lewat sini: batasnya `REASON_LIMIT` dan pintunya
    :func:`_reason_cell`, karena satu-satunya angka yang boleh membatasi `reason` adalah angka
    yang sama dengan yang membatasinya di `core.skill`.
    """
    return _bdi(value, limit)


# --- jam & umur ---------------------------------------------------------------------------
def _parse(iso_utc: Any) -> datetime | None:
    if not isinstance(iso_utc, str) or not iso_utc.strip():
        return None
    try:
        moment = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None
    return moment if moment.tzinfo is not None else None


def age_phrase(as_of: Any, now: datetime) -> str | None:
    """Umur `as_of` terhadap jam render, dalam satuan yang tidak mengarang presisi.

    None bila `as_of` tidak terbaca — jam yang tidak terbaca TIDAK diganti jam sekarang, dan
    "umur 0 menit" tidak pernah dikarang untuk menutupinya.
    """
    moment = _parse(as_of)
    if moment is None:
        return None
    minutes = (now - moment).total_seconds() / 60.0
    if minutes < 0:
        return f"{-minutes:.1f} min AHEAD of the render clock (the clocks disagree, not freshness)"
    if minutes < 90:
        return f"{minutes:.1f} min before this page was rendered"
    if minutes < 60 * 48:
        return f"{minutes / 60:.1f} h ({minutes:.0f} min) before this page was rendered"
    return f"{minutes / 1440:.1f} d ({minutes:.0f} min) before this page was rendered"


def _now_jst(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M JST")


# --- kerangka halaman ---------------------------------------------------------------------
#: Selang penyegaran otomatis klien, dalam milidetik (ADR-020 butir 4). Ia DISEBUT di kaki tiap
#: layar dan bisa dimatikan dari layar; nilainya ikut ke `<body data-poll-ms>` supaya yang
#: tercetak di kalimat dan yang dipakai kode adalah SATU angka.
POLL_INTERVAL_MS = 30_000

#: Judul jendela tiap layar. Ia dipakai server DAN klien: sesudah perpindahan tanpa muat ulang,
#: judul yang tertinggal di jendela adalah judul layar SEBELUMNYA — URL dan isi berkata kartu #N
#: sementara tab berkata "watchlist". Kelas yang sama dengan pengakuan `kept()`: apa yang
#: dikatakan tentang layar harus ikut layar yang benar-benar ada. Kembarannya di
#: `frontend/src/text.ts` dipatok tes.
TITLE_WATCHLIST = "Tenko — watchlist (simulated)"
TITLE_CARD = "Tenko — card #{id} (simulated)"

#: Kalimat yang menyatakan apa yang dilakukan klien — dan apa yang TIDAK dilakukannya.
#:
#: Ia BERBEDA per layar, dan itu bukan kerapian melainkan kejujuran: `main.ts:schedule()` hanya
#: memasang timer bila layarnya watchlist, bar kontrol (saring/urut/sakelar) disembunyikan di
#: luar watchlist, dan pada halaman gagal klien berhenti sebelum permintaan pertamanya. Satu
#: kalimat untuk ketiganya berarti dua layar menjanjikan ritme 30 detik dan sakelar yang tidak
#: mereka punya — persis di layar yang paling mungkin dibuka juri kedua (kartu).
_CLIENT_NOTE_HEAD = "This screen is also served whole without JavaScript. "
_CLIENT_NOTE_TAIL = (
    "It computes no number of its own: every value on screen comes from the API response, and "
    "`unspoken` stays `unspoken`."
)

WATCHLIST_CLIENT_NOTE = (
    _CLIENT_NOTE_HEAD + "When the client script does load, this screen gets four things and "
    "nothing else: it moves between screens without a reload, it filters and sorts the rows you "
    "can already see, it re-fetches the same JSON API every "
    f"{POLL_INTERVAL_MS // 1000} seconds (a switch on screen turns that off), and it says out "
    "loud when it is loading or has failed. " + _CLIENT_NOTE_TAIL
)

CARD_CLIENT_NOTE = (
    _CLIENT_NOTE_HEAD + "When the client script does load, this card gets two things and nothing "
    "else: it moves between screens without a reload, and it re-fetches this one card once, when "
    "you arrive. This screen does NOT auto-refresh and carries no filter, sort, or refresh "
    "switch — those belong to the watchlist screen; reload the page to ask again. It says out "
    "loud when it is loading or has failed, and how old what you see is. " + _CLIENT_NOTE_TAIL
)

ERROR_CLIENT_NOTE = (
    _CLIENT_NOTE_HEAD + "The client script loads on this failure page too, and then stops: it "
    "fetches nothing, refreshes nothing, and changes nothing on screen. There is no "
    "auto-refresh, no filter, and no sort here, and every link works the ordinary way, with a "
    "reload."
)


#: Layar → kalimatnya. Kunci di sini HARUS sama dengan `data-screen` yang dipancarkan `_shell`.
CLIENT_NOTES = {
    "watchlist": WATCHLIST_CLIENT_NOTE,
    "card": CARD_CLIENT_NOTE,
    "error": ERROR_CLIENT_NOTE,
}

#: Layar yang TIDAK mencetak kalimat klien sama sekali (ADR-030, permintaan pemilik repo).
#:
#: Landing punya kalimatnya sendiri sampai ADR-030 dan kalimat itu BENAR — ia hanya berdiri di
#: tempat yang salah. `/` adalah layar pertama yang dilihat juri, dan pengakuan tentang perilaku
#: klien adalah pengakuan tentang APLIKASI: ia dibaca oleh orang yang sudah masuk, di Layar A,
#: Layar B, dan halaman gagal — di mana ia tetap dirender kata demi kata. Yang HILANG bersamanya
#: dari `/` tidak satu pun klaim yang tidak diucapkan di tempat lain di halaman yang sama:
#: caption papan sudah berkata ia dirender server dari store yang sama seperti aplikasi, dan
#: kaki `_shell` sudah berkata halaman ini dirender di server sebelum satu baris skrip jalan.
SILENT_CLIENT_NOTE_SCREENS = frozenset({"landing"})


def client_note(screen: str) -> str:
    """Kalimat klien untuk `screen`, atau string kosong bila layar itu tidak mencetaknya.

    Layar tak dikenal jatuh ke kalimat watchlist — karena ITULAH yang dilakukan `main.ts`: apa
    pun yang bukan `card` dan bukan `error` mendapat bar kontrol dan timer polling. Landing
    disebut EKSPLISIT di :data:`SILENT_CLIENT_NOTE_SCREENS`, bukan dibiarkan jatuh ke cadangan
    itu: ia layar inert, jadi kalimat watchlist akan menjadi janji yang tidak dimilikinya.
    """
    if screen in SILENT_CLIENT_NOTE_SCREENS:
        return ""
    return CLIENT_NOTES.get(screen, WATCHLIST_CLIENT_NOTE)


def _client_note_p(screen: str) -> str:
    """Kalimat klien sebagai satu paragraf kaki — atau TIDAK ADA PARAGRAF sama sekali.

    Bukan paragraf kosong: sebuah `<p class="sub"></p>` yang tak berisi apa-apa adalah blok yang
    diumumkan pembaca layar sebagai keheningan dan yang tetap memakan jarak di layar.
    """
    note = client_note(screen)
    return f'<p class="sub">{escape(note, quote=True)}</p>' if note else ""


def _banner(banner: str) -> str:
    """Pita SIMULATED, satu bentuk untuk keempat layar (`docs/rules-compliance.md` §5).

    Ia dipisahkan menjadi fungsi karena landing MELEBURKANNYA ke dalam satu pita mendatar
    bersama kepala halaman (ADR-030): yang berbagi baris adalah kotaknya, sedangkan teks,
    `role="note"`, ikon, warna, dan kelekatannya harus tetap SATU — dua salinan markup banner
    adalah dua tempat untuk salah satu di antaranya menyimpang tanpa terlihat.
    """
    return f'<p class="banner" role="note">{BANNER_ICON}<span>{escape(banner, quote=True)}</span></p>'


def _shell(
    title: str,
    banner: str,
    body: str,
    now: datetime,
    screen: str,
    decision_id: int | None = None,
    foot_top: str = "",
    masthead: str = "",
) -> str:
    """Kerangka halaman. Banner, isi, dan tiap `aria-label` ada SEBELUM satu baris JS pun jalan.

    `masthead` adalah pita mendatar yang MEMUAT banner itu sendiri — dipakai landing, yang
    meleburkan banner dan kepala halaman menjadi satu pita (ADR-030). Ia MENGGANTIKAN kotak
    banner, tidak menghapus bannernya: :func:`render_landing` merakitnya dengan :func:`_banner`
    yang sama, jadi tidak ada layar yang bisa kehilangan kalimat SIMULATED dengan mengoper
    `masthead` — yang paling buruk yang bisa terjadi adalah ia dirender di kotak yang lain.

    `foot_top` adalah SATU blok yang boleh berdiri di atas kaki tetap — dipakai landing untuk
    kisi tautan bergaya `kage.html` (ADR-029). Ia MENAMBAH, tidak menggantikan: kalimat-kalimat
    di bawahnya (jam JST, "read-only, nol tool RYO, nol LLM", kalimat keyboard, dan — di layar
    yang punya — kalimat klien) dirender persis sama di setiap layar. Layar A, Layar B, dan
    halaman gagal mencetak KEEMPATNYA; landing mencetak TIGA, karena ADR-030 memindahkan
    kalimat klien ke dalam aplikasi atas permintaan pemilik repo. Tesnya menembak angka itu
    sebagai angka, per layar.

    `screen` adalah satu-satunya hal yang dibaca klien untuk memutuskan layar mana yang sedang
    terbuka; ia atribut `data-*` pada `<body>`, bukan tebakan atas URL di dalam JS.

    Skripnya `defer` dan berasal dari origin sendiri (`script-src 'self'`, ADR-020 butir 5).
    Halaman ini tetap LENGKAP tanpanya: klien yang gagal memuat meninggalkan layar yang
    dirender server, ber-banner, bertabel, dan bisa ditelusuri keyboard (ADR-020 butir 6).
    """
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=1280">'
        '<meta name="tenko-simulated" content="true">'
        f"<title>{escape(title, quote=True)}</title>"
        '<link rel="stylesheet" href="/ui/tenko.css">'
        '<script src="/ui/app.js" defer></script>'
        f'</head><body data-screen="{escape(screen, quote=True)}" '
        f'data-poll-ms="{POLL_INTERVAL_MS}"'
        + ("" if decision_id is None else f' data-decision-id="{decision_id:d}"')
        + ">"
        + (masthead or _banner(banner))
        + f'<main id="isi">{body}</main>'
        f"<footer>{foot_top}<p>All clocks are JST. This page was rendered on the server at "
        f"{escape(_now_jst(now), quote=True)}, banner and all, before any script ran. Tenko "
        "reads the store READ-ONLY; no RYO tool and no LLM is called to show this screen. "
        'Every route this app serves is listed at <a href="/api/routes">/api/routes</a>.</p>'
        f'<p class="sub">{escape(KEYBOARD_NOTE, quote=True)}</p>'
        + _client_note_p(screen)
        + "</footer></body></html>"
    )


def _notes_list(notes: Any) -> str:
    entries = [note for note in notes if isinstance(note, str)] if isinstance(notes, list) else []
    if not entries:
        return ""
    items = "".join(f"<li>{_text(note)}</li>" for note in entries)
    return f'<h2>Screen notes</h2><ul class="notes">{items}</ul>'


def _decision_badge(decision: Any) -> str:
    text = decision if isinstance(decision, str) and decision else "?"
    css = "GO" if text == "GO" else "NO_GO" if text == "NO_GO" else "unknown"
    return f'<span class="badge d-{escape(css, quote=True)}">{_bdi(text, KEY_LIMIT)}</span>'


def _replay_badge(entry: dict[str, Any]) -> str:
    """Lencana replay — warnanya HANYA dari `replay_tone` (tidak diturunkan ulang di sini).

    Alasannya dipatok TASKS 0.14: kalau warna diturunkan dari `replay_status` lewat `if`
    terpisah, vonis keenam berikutnya akan diam-diam mewarisi warna `MISMATCH`. `replay_tone`
    punya nilai cadangan `unknown` di `ui.app`, jadi vonis yang belum dikenal tampil abu-abu
    bernama, bukan merah palsu.
    """
    tone = entry.get("replay_tone")
    tone_css = tone if isinstance(tone, str) and _css_word(tone) else "unknown"
    status = entry.get("replay_status") or "NOT RUN"
    label = entry.get("replay_label") or "not assessed"
    return (
        f'<span class="badge tone-{escape(tone_css, quote=True)}">{_bdi(status, KEY_LIMIT)}</span>'
        f'<span class="mark">{_text(label)}</span>'
    )


# --- Layar A ------------------------------------------------------------------------------
def _dict(value: Any) -> dict[str, Any]:
    """Dict atau tidak sama sekali — `x or {}` meloloskan `-1`/`b"x"`, yang lalu tidak punya `.get`."""
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float:
    """Angka untuk MENGURUTKAN, 0 untuk apa pun yang bukan angka.

    Renderer harus total: satu `veto_rank` bertipe aneh di satu baris tidak boleh menjatuhkan
    seluruh layar (yang sekarang berakhir sebagai halaman 500 — halaman, tetapi tetap layar
    yang hilang). Nilai yang tidak bisa diurutkan tidak dikarang jadi angka DI LAYAR; ia hanya
    tidak ikut menentukan urutan, dan tetap dirender apa adanya di selnya.
    """
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _closest_to_go(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Simbol yang paling dekat GO, menurut aturan yang DISEBUT di layar — bukan skor.

    `/api/watchlist` mengurutkan daya veto MENURUN (penahan terberat di atas), jadi yang paling
    dekat GO ada di bawah dan seorang juri tidak akan menemukannya dalam 30 detik. Aturannya:
    keputusan GO dulu; kalau semua ditahan, yang penahan TERBERATnya paling ringan (peringkat
    spec §4 terbesar), lalu yang penahannya paling sedikit.
    """
    if not entries:
        return None
    return min(
        entries,
        key=lambda entry: (
            0 if not entry.get("n_blocking") else 1,
            -_number(entry.get("veto_rank")),
            _number(entry.get("n_blocking")),
            str(entry.get("symbol", "")),
        ),
    )


def _watchlist_lede(entries: list[dict[str, Any]]) -> str:
    n = len(entries)
    nearest = _closest_to_go(entries)
    if nearest is None:
        return f'<p class="answer empty">{escape(EMPTY_SENTENCE, quote=True)}</p>'
    n_blocked = sum(1 for entry in entries if entry.get("n_blocking"))
    if nearest.get("n_blocking"):
        blocker = _dict(nearest.get("top_blocker"))
        tail = (
            f"nothing is GO; the closest is {_bdi(nearest.get('symbol'), KEY_LIMIT)}, held by "
            f"{_bdi(nearest.get('n_blocking'))} rows, heaviest "
            f"{_bdi(blocker.get('item_id'), KEY_LIMIT)} (veto power {_bdi(blocker.get('veto_rank'))})"
        )
    else:
        tail = f"closest to GO: {_bdi(nearest.get('symbol'), KEY_LIMIT)} — zero rows hold it"
    return (
        f'<p class="answer">n={n} symbols read (one row = that symbol\'s LAST decision); '
        f"{n_blocked} held; {tail}.</p>"
        '<p class="sub">Ordered by veto power: the heaviest blocker (spec §4 rank 1) on top. '
        '"Closest to GO" is read backwards from that order: the lightest heaviest-blocker '
        "first, then the fewest blockers — not a score, not a forecast. The rank itself is "
        "<strong>not validated</strong>: it is a spec §4 design constant, and this repo's own "
        "instrument contradicts it — see Screen notes below.</p>"
    )


def _watchlist_row(entry: dict[str, Any], now: datetime) -> str:
    blocker = _dict(entry.get("top_blocker"))
    decision_id = entry.get("decision_id")
    href = f"/ui/decision/{int(decision_id)}" if isinstance(decision_id, int) else None
    symbol = _bdi(entry.get("symbol"), KEY_LIMIT)
    open_label = _aria(
        _phrase("open the roll-call card for", entry.get("symbol"), "open this symbol's roll-call card"),
        _phrase("decision", entry.get("decision"), "decision not stated"),
        _phrase("card number", decision_id, "no card number"),
    )
    link = (
        f'<a href="{escape(href, quote=True)}" aria-label="{open_label}">{symbol}</a>'
        if href
        else f'{symbol} <span class="mark">this row has no id that can be opened</span>'
    )
    age = age_phrase(entry.get("as_of"), now)
    age_html = _text(age) if age else "age cannot be stated: as_of is unreadable"
    if blocker:
        held = (
            f"<strong>{_bdi(blocker.get('item_id'), KEY_LIMIT)}</strong> "
            f"(veto power {_bdi(blocker.get('veto_rank'))}, status {_bdi(blocker.get('status'), KEY_LIMIT)})"
            f'<span class="mark">{_reason_cell(blocker.get("reason"))}</span>'
            f'<span class="mark">source {_bdi(blocker.get("tool"), KEY_LIMIT)}.'
            f"{_bdi(blocker.get('field'), TEXT_LIMIT)} · as_of "
            f"{_bdi(blocker.get('as_of_jst'), KEY_LIMIT)}</span>"
        )
    else:
        held = '<span class="mark">zero rows hold this decision back</span>'
    # Nama baris Layar A menjawab pertanyaan juri untuk SATU simbol tanpa membaca sembilan sel:
    # keputusannya, berapa yang menahan, penahan terberatnya, dan dari mana penahan itu dibaca.
    n_unspoken = _said(entry.get("n_unspoken"))
    unspoken_phrase = (
        f"{n_unspoken} rows unspoken"
        if n_unspoken is not None
        else "the count of unspoken rows is not stated"
    )
    label = _aria(
        _phrase("symbol", entry.get("symbol"), "symbol not stated"),
        _phrase("decision", entry.get("decision"), "decision not stated"),
        (
            _phrase("held by", entry.get("n_blocking"), "blocker count not stated")
            + _phrase(" of", entry.get("n_items"), " of an unstated number of")
            + " roll-call rows"
        ),
        (
            "; ".join(
                (
                    _phrase("heaviest blocker", blocker.get("item_id"), "heaviest blocker has no id"),
                    _phrase("veto power", blocker.get("veto_rank"), "veto power not stated"),
                    _aria_status(blocker),
                    _aria_source(blocker),
                )
            )
            if blocker
            else "zero rows hold this decision back"
        ),
        unspoken_phrase,
        _phrase("bundle as_of", entry.get("as_of_jst"), "bundle as_of is unreadable"),
    )
    return (
        f'<tr aria-label="{label}">'
        f'<th scope="row">{link}</th>'
        f"<td>{_decision_badge(entry.get('decision'))}</td>"
        f'<td class="num">{_bdi(entry.get("veto_rank"))}</td>'
        f"<td>{held}</td>"
        f'<td class="num">{_bdi(entry.get("n_blocking"))} / {_bdi(entry.get("n_items"))}</td>'
        f'<td class="num">{_bdi(entry.get("n_unspoken"))}</td>'
        f'<td class="num">{_bdi(entry.get("n_snapshots"))} snapshots<br>'
        f"{_bdi(entry.get('n_warnings'))} warnings</td>"
        f"<td>{_replay_badge(entry)}</td>"
        f"<td>{_bdi(entry.get('as_of_jst'), KEY_LIMIT)}"
        f'<span class="mark">{age_html}</span></td>'
        "</tr>"
    )


def render_watchlist(payload: dict[str, Any], banner: str, now: datetime) -> str:
    """Layar A — "simbol mana yang paling dekat GO, dan apa yang menahan yang lain?"."""
    raw = payload.get("symbols")
    entries = [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []
    header = "".join(f'<th scope="col">{escape(name, quote=True)}</th>' for name in WATCHLIST_COLUMNS)
    rows = "".join(_watchlist_row(entry, now) for entry in entries)
    table = (
        f"<table id='watchlist'><caption class='sub'>n={len(entries)} symbols; read from the "
        f"{_bdi(payload.get('scan_limit'))} MOST RECENT decision rows, not the whole table. "
        f"{_bdi(payload.get('n_legacy_hidden'))} earlier-generation rows are not shown here."
        f"</caption><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>"
        if entries
        else ""
    )
    body = (
        "<h1>Tenko 点呼 — pre-departure roll call (simulated)</h1>"
        '<p class="sub"><a href="/api/watchlist">the JSON behind this screen, verbatim</a>'
        + (' · <a href="#watchlist">skip to the watchlist table</a>' if entries else "")
        + "</p>"
        f"{_watchlist_lede(entries)}"
        f"{table}"
        f"{_notes_list(payload.get('notes'))}"
    )
    return _shell(TITLE_WATCHLIST, banner, body, now, screen="watchlist")


# --- Layar B ------------------------------------------------------------------------------
def _stance_of(value: Any) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("stance"), str):
        return value["stance"]
    return None


def _status_words(item: dict[str, Any]) -> tuple[str, str, str]:
    """`(status, kelas CSS, penanda tekstual)` — SATU turunan untuk sel DAN untuk `aria-label`.

    Dipisah dari :func:`_status_cell` supaya baris kartu dan namanya di pembaca layar tidak bisa
    berselisih: kalau penanda hijau-netral dihitung dua kali, salah satunya akan tertinggal saat
    ADR-017 diamandemen lagi, dan yang tertinggal hampir pasti yang tidak terlihat di layar.

    `status` dibersihkan :func:`core.skill.clean_text` DULU (lewat :func:`_plain`), bukan dipakai
    mentah: `escape(quote=True)` mengurus kutip tetapi tidak menyentuh U+202E/NUL/ESC, dan
    atribut inilah yang dibacakan pembaca layar (security review 3.2, SEDANG).
    """
    status = item.get("status")
    text = _plain(status, KEY_LIMIT) if isinstance(status, str) else "?"
    stance = _stance_of(item.get("value"))
    css = f"s-{text}" if _css_word(text) else "s-unknown"
    mark = ""
    if stance == "neutral":
        mark = NEUTRAL_MARK
        if text == "green":
            css = "s-green-neutral"
    elif text == "unspoken":
        mark = UNSPOKEN_MARK
    return text, css, mark


def _aria_status(item: dict[str, Any]) -> str:
    text, _, mark = _status_words(item)
    return f"status {text}, {mark}" if mark else f"status {text}"


def _aria_value(item: dict[str, Any]) -> str:
    """Klausa NILAI untuk `aria-label` — bagian "sebut" dari tunjuk→sebut.

    Yang tidak ada di bukti dibacakan sebagai kalimatnya sendiri, bukan sebagai sel kosong dan
    bukan sebagai 0. Baris `unspoken` yang kebetulan MASIH membawa `value` (mis. `stance` saja)
    tetap dibacakan "tidak disebut" lebih dulu, lalu apa yang memang terbaca di bukti — urutan
    itu yang menjaga vonisnya tidak tertukar dengan angkanya.
    """
    value = item.get("value")
    _, _, mark = _status_words(item)
    if mark == UNSPOKEN_MARK:
        head = f"value: {UNSPOKEN_MARK}"
        if value is None:
            return head
        return f"{head}; what is readable in the evidence: {clean_text(_scalar(value), ARIA_VALUE_LIMIT)}"
    if value is None:
        return f"value: {ARIA_NULL}"
    return f"value spoken: {clean_text(_scalar(value), ARIA_VALUE_LIMIT)}"


def _aria_source(item: dict[str, Any]) -> str:
    """Klausa SUMBER untuk `aria-label` — bagian "tunjuk": tool.field, snapshot, as_of.

    Kartu ini menjual "nilai ini datang dari SINI, pada JAM INI". Nama baris yang hanya berbunyi
    "liquidity_floor, red" menghapus separuh produk untuk pemakai pembaca layar, jadi tiap baris
    membawa asalnya di namanya sendiri.
    """
    tool, field = _said(item.get("tool"), KEY_LIMIT), _said(item.get("field"), TEXT_LIMIT)
    if tool is None and field is None:
        return ARIA_NO_SOURCE
    where = f"{tool or 'tool not stated'}.{field or 'field not stated'}"
    parts = [f"source {where}"]
    # Klausa snapshot hanya bila field-nya MEMANG bagian kontrak baris ini: `top_blocker`
    # Layar A tidak membawa `snapshot_id` sama sekali, dan "snapshot tidak disebut" di sana akan
    # menuduh buktinya tak bernomor padahal yang tidak lengkap adalah ringkasan barisnya.
    if "snapshot_id" in item:
        parts.append(_phrase("snapshot", item.get("snapshot_id"), "snapshot not stated"))
    parts.append(_phrase("as_of", item.get("as_of_jst"), "as_of not stated"))
    return ", ".join(parts)


def _status_cell(item: dict[str, Any]) -> str:
    """Status baris + penanda TEKSTUAL-nya (amandemen ADR-017, dan `unspoken` yang bukan nol).

    Hijau-netral dirender berbeda dari hijau-konfirmasi tanpa menambah status keempat ke
    `ItemStatus`: yang berbeda adalah kelas CSS **dan** kalimatnya, dan kalimat itu ikut ke
    `aria-label` supaya pembaca layar mendapat perbedaan yang sama, bukan hanya warna.
    """
    text, css, mark = _status_words(item)
    aria = _aria_status(item)
    badge = (
        f'<span class="badge {escape(css, quote=True)}" aria-label="{_aria(aria)}">'
        f"{_bdi(text, KEY_LIMIT)}</span>"
    )
    return badge + (f'<span class="mark">{escape(mark, quote=True)}</span>' if mark else "")


def _mark(note: str) -> str:
    return f'<span class="mark">{escape(note, quote=True)}</span>'


def _cut_marks(source: Any, limit: int, *, ledger_limits: tuple[int, ...] = ()) -> str:
    """Kalimat penjelas pemotongan, ditentukan FAKTA — bukan substring `...(truncated)`.

    Yang diperiksa adalah teks SEBELUM masuk :func:`_bdi` (jadi hasilnya sama dengan apa yang
    benar-benar terjadi di sel sebelahnya), dengan tiga jawaban yang tidak bisa tertukar:

    * layar ini memotong bila `clean_text(source, limit)` BERBEDA dari sumbernya dan berakhir
      dengan penandanya — satu-satunya kondisi yang membuat pemotongan itu perbuatan kami;
    * pemotongan `core.skill` dikenali dari BENTUKNYA: panjang persis `L + 14` untuk salah satu
      `L` di `ledger_limits`, dan berakhir dengan penanda. Itu tanda tangan `clean_text` di
      langit-langit itu, dan kalimat yang keluar menyebut langit-langit MANA (ledger ini
      append-only: baris lama dibukukan pada batas lama dan tidak bisa ditulis ulang);
    * sisanya (`...(truncated)` yang muncul di tengah teks, atau di panjang yang bukan
      langit-langit mana pun) dijawab :data:`FOREIGN_MARK_NOTE`, yang tidak mengaku apa-apa.
      Nilai RYO yang kebetulan memuat literal itu karena itu tidak lagi membuat kartu mengaku
      menyembunyikan sesuatu yang tidak disembunyikannya (security review 3.2, RENDAH a).
    """
    note = cut_note(source, limit, ledger_limits=ledger_limits)
    return _mark(note) if note else ""


def cut_note(source: Any, limit: int, *, ledger_limits: tuple[int, ...] = ()) -> str | None:
    """Kalimat pemotongan sebagai TEKS POLOS, atau `None` bila tidak ada yang perlu dijelaskan.

    Publik karena `ui.app` menitipkannya ke respons `/api/decision` (`value_cut_note`,
    `reason_cut_note`): klien merender tabel yang sama tetapi TIDAK memotong apa pun sendiri,
    jadi ia tidak bisa menurunkan kalimat ini — dan sebelum ada field ini, `TRUNCATED_MARK`
    tetap terlihat sesudah polling sementara kalimat yang menyebut SIAPA yang memotong hilang.
    Yang dikirim adalah keluaran fungsi INI atas nilai payload yang sama, jadi tidak ada logika
    kedua yang bisa menyimpang: satu fungsi, dua perender.
    """
    if source is None:
        return None
    text = source if isinstance(source, str) else _scalar(source)
    cleaned = clean_text(text, limit)
    if cleaned != text and cleaned.endswith(TRUNCATED_MARK):
        return SCREEN_CUT_NOTE.format(limit=limit)
    if TRUNCATED_MARK not in text:
        return None
    if text.endswith(TRUNCATED_MARK):
        for ceiling in ledger_limits:
            if len(text) == ceiling + len(TRUNCATED_MARK):
                return ledger_cut_note(ceiling)
    return FOREIGN_MARK_NOTE


def _reason_cell(value: Any) -> str:
    """`reason` pada batas yang SAMA dengan `core.skill.REASON_LIMIT`, plus sebab potongnya.

    Langit-langit ledger yang dikenali ada DUA (:data:`REASON_CEILINGS`): batas hari ini dan
    batas yang berlaku saat 40 baris `data/ryo.db` dibukukan. Menghapus yang kedua membuat
    kartu warisan berkata "tidak di langit-langit mana pun" untuk potongan yang punya sebab
    yang bisa disebut — benar secara mekanis, menyesatkan sebagai kalimat.

    Batasnya diimpor, tidak disalin: `ui.app` menyajikan `reason` sampai `REASON_LIMIT`, dan
    layar yang memotong lebih pendek dari itu membuang kalimat yang ADA di ledger — lalu
    menyebutnya "hilang sebelum dibukukan". Selama kedua angka ini satu konstanta, pemotongan
    di sini hanya mungkin untuk teks yang TIDAK datang lewat `ItemResult`, dan kalimatnya pun
    sudah berbeda.
    """
    return _bdi(value, REASON_LIMIT, lang=EVIDENCE_LANG) + _cut_marks(
        value, REASON_LIMIT, ledger_limits=REASON_CEILINGS
    )


def _item_row(item: dict[str, Any]) -> str:
    narration = item.get("narration")
    narration_html = f'<span class="narr-label">{escape(NARRATION_LABEL, quote=True)}</span>' + (
        f'<span class="narr" lang="{EVIDENCE_LANG}">{_text(narration)}</span>'
        if isinstance(narration, str) and narration.strip()
        else f'<span class="narr">{escape(NARRATION_EMPTY, quote=True)}</span>'
    )
    reason = _reason_cell(item.get("reason"))
    value = _bdi(item.get("value"), VALUE_LIMIT, lang=EVIDENCE_LANG) + _cut_marks(
        item.get("value"), VALUE_LIMIT
    )
    source = _aria_source(item)
    # Nama BARIS, bukan nama sel: pembaca layar mengumumkannya sekali saat pindah baris, dan
    # kalimat itulah seluruh janji produk untuk baris ini — status, nilai yang disebut, dan dari
    # tool/snapshot/jam mana ia datang. Isi selnya tidak diubah sedikit pun; yang ditambah hanya
    # namanya, sehingga layar tetap terbaca sama oleh mata dan oleh telinga.
    label = _aria(
        _phrase("row", item.get("item_id"), "row without an item_id"),
        _phrase("veto power", item.get("veto_rank"), "veto power not stated"),
        _aria_status(item),
        _aria_value(item),
        source,
    )
    return (
        f'<tr aria-label="{label}">'
        f'<td class="num">{_bdi(item.get("veto_rank"))}</td>'
        f'<th scope="row">{_bdi(item.get("item_id"), KEY_LIMIT)}</th>'
        f"<td>{_status_cell(item)}</td>"
        f"<td>{reason}</td>"
        f'<td class="mono">{value}</td>'
        f'<td class="mono" aria-label="{_aria(source)}">'
        f"{_bdi(item.get('tool'), KEY_LIMIT)}.{_bdi(item.get('field'), TEXT_LIMIT)}</td>"
        f'<td class="num">{_bdi(item.get("snapshot_id"))}</td>'
        f"<td>{_bdi(item.get('as_of_jst'), KEY_LIMIT)}"
        f'<span class="mark">{_bdi(item.get("as_of"), KEY_LIMIT)}</span></td>'
        f"<td>{narration_html}</td>"
        "</tr>"
    )


def _card_answer(payload: dict[str, Any], items: list[dict[str, Any]]) -> str:
    """Kalimat yang harus menjawab "kenapa keputusan ini" tanpa satu klik pun."""
    blocking = payload.get("blocking")
    blocking = [entry for entry in blocking if isinstance(entry, str)] if isinstance(blocking, list) else []
    n_items = payload.get("n_items")
    if payload.get("generation") == "legacy":
        return (
            '<p class="answer">Earlier-generation row: its items and blockers are NOT rendered '
            "(see the notes).</p>"
        )
    if not blocking:
        return (
            f'<p class="answer">Zero rows hold this decision back; {_bdi(n_items)} roll-call '
            f"rows were spoken, {_bdi(payload.get('n_unspoken'))} unspoken.</p>"
        )
    # Hanya id BERUPA TEKS yang jadi kunci: `blocking` berisi id item (str), dan `item_id`
    # bertipe list/dict akan melempar "unhashable type" dan menjatuhkan seluruh kartu.
    by_id = {entry["item_id"]: entry for entry in items if isinstance(entry.get("item_id"), str)}
    top = by_id.get(blocking[0], {})
    # Judul memakai `n_blocking` (cacah SEBENARNYA), bukan panjang daftar yang dirender: kalau
    # ekor `blocking` dipotong di API, "Ditahan 200 dari 20.000" akan mengecilkan kegagalannya
    # persis di kalimat yang harus menjawab "apa yang berubah" dalam 30 detik.
    n_blocking = payload.get("n_blocking")
    if not isinstance(n_blocking, int) or isinstance(n_blocking, bool) or n_blocking < len(blocking):
        n_blocking = len(blocking)
    n_hidden = n_blocking - len(blocking)
    others = ", ".join(_bdi(entry, KEY_LIMIT) for entry in blocking[1:])
    tail = (
        f"{others}, and {_bdi(n_hidden)} more that are not rendered (`n_blocking` is the true count)."
        if n_hidden
        else (f"{others}." if len(blocking) > 1 else "none.")
    )
    return (
        f'<p class="answer">Held by {_bdi(n_blocking)} of {_bdi(n_items)} roll-call rows. '
        f"HEAVIEST blocker: <strong>{_bdi(blocking[0], KEY_LIMIT)}</strong> "
        f"(veto power {_bdi(top.get('veto_rank'))}) — {_reason_cell(top.get('reason'))} "
        f"Source: {_bdi(top.get('tool'), KEY_LIMIT)}.{_bdi(top.get('field'), TEXT_LIMIT)}, "
        f"as_of {_bdi(top.get('as_of_jst'), KEY_LIMIT)}.</p>"
        f'<p class="sub">Other blockers, by veto power: {tail}</p>'
    )


def _self_href(payload: dict[str, Any], prefix: str) -> str | None:
    decision_id = payload.get("decision_id")
    return f"{prefix}{int(decision_id)}" if isinstance(decision_id, int) else None


def _replay_link(payload: dict[str, Any]) -> str:
    """ "Tombol" replay: tautan biasa ke kartu ini sendiri — keyboard-first, tanpa satu baris JS.

    Tidak ada POST dan tidak ada eksekusi dari browser (UI baca-saja). Yang membuat tautan ini
    jujur adalah `ui.app`: vonis replay dihitung SAAT halaman dirender, dari snapshot beku yang
    sama, jadi memuat ulang halaman = memutar ulang keputusan.
    """
    href = _self_href(payload, "/ui/decision/")
    if href is None:
        return '<span class="mark">this row has no id that can be replayed</span>'
    label = _aria(
        _phrase("replay decision number", payload.get("decision_id"), "replay this card"),
        "reloads this card and recomputes the verdict from the same frozen snapshots",
    )
    return f'<a class="badge" href="{escape(href, quote=True)}" aria-label="{label}">replay now &#8635;</a>'


def _card_meta(payload: dict[str, Any], now: datetime) -> str:
    age = age_phrase(payload.get("as_of"), now)
    age_html = (
        f"{_bdi(payload.get('as_of_jst'), KEY_LIMIT)} — {_text(age)}"
        if age
        else '<span class="empty">age cannot be stated: the bundle as_of is unreadable</span>'
    )
    rows = [
        (
            "bundle as_of",
            f'{age_html}<span class="mark">{escape(AGE_NOTE, quote=True)}</span>'
            f'<span class="mark">raw as_of: {_bdi(payload.get("as_of"), KEY_LIMIT)}</span>',
        ),
        (
            "booked (ts_utc)",
            f'{_bdi(payload.get("ts_jst"), KEY_LIMIT)}<span class="mark">'
            f"{_bdi(payload.get('ts_utc'), KEY_LIMIT)}"
            " — the moment the ledger row was written, guarded by NO hash at all</span>",
        ),
        ("decision_hash", _bdi(payload.get("decision_hash"), KEY_LIMIT)),
        ("computed_hash", _bdi(payload.get("computed_hash"), KEY_LIMIT)),
        ("params_hash", _bdi(payload.get("params_hash"), KEY_LIMIT)),
        ("algo", _bdi(payload.get("algo"), KEY_LIMIT)),
        (
            "replay",
            f'{_replay_badge(payload)}<span class="mark">{_text(payload.get("detail"))}</span>',
        ),
        (
            "replay",
            f"{_replay_link(payload)}"
            f'<span class="mark">That button reloads this card, and the verdict above is '
            f"recomputed ON THE SERVER from the same {_bdi(payload.get('n_snapshots'))} frozen "
            "snapshots every time this page is rendered — so it really does replay, it does not "
            "show a stored result. Off screen: "
            f"<code>{_bdi(payload.get('replay_command'), KEY_LIMIT)}</code>, which reads the "
            "same snapshots and prints the same verdict.</span>",
        ),
    ]
    body = "".join(f"<dt>{escape(name, quote=True)}</dt><dd>{value}</dd>" for name, value in rows)
    return f'<dl class="meta">{body}</dl>'


def _narration_block(payload: dict[str, Any]) -> str:
    block = payload.get("narration")
    if not isinstance(block, dict):
        return ""
    status = block.get("status")
    line = (
        f'<p class="sub"><span class="narr-label">{escape(NARRATION_LABEL, quote=True)}</span>'
        f"status <code>{_bdi(status, KEY_LIMIT)}</code>, "
        f"n_lines={_bdi(block.get('n_lines'))} of n_items={_bdi(block.get('n_items'))}, "
        f"model {_bdi(block.get('model'), KEY_LIMIT)}.</p>"
        f'<p class="sub">{_text(block.get("reason"))}</p>'
        f'<p class="sub">{escape(NARRATION_NOTE, quote=True)}</p>'
    )
    return f"<h2>Model narration</h2>{line}"


def _list_block(title: str, entries: Any, empty: str, *, lang: str | None = None) -> str:
    """Daftar teks berjudul. `lang` menandai bahasa ISInya (peringatan envelope = Inggris)."""
    rows = [entry for entry in entries if isinstance(entry, str)] if isinstance(entries, list) else []
    if not rows:
        return f'<h2>{escape(title, quote=True)}</h2><p class="sub empty">{escape(empty, quote=True)}</p>'
    attr = f' lang="{escape(lang, quote=True)}"' if lang else ""
    body = "".join(f"<li{attr}>{_text(entry)}</li>" for entry in rows)
    return f'<h2>{escape(title, quote=True)}</h2><ul class="notes">{body}</ul>'


def _bundle_table(payload: dict[str, Any], now: datetime) -> str:
    raw = payload.get("replay_bundle")
    rows = [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []
    if not rows:
        return f'<h2>Frozen evidence</h2><p class="sub empty">{escape(EMPTY_SENTENCE, quote=True)}</p>'
    body = ""
    for entry in rows:
        age = age_phrase(entry.get("as_of"), now)
        body += (
            "<tr>"
            f'<td class="num">{_bdi(entry.get("snapshot_id"))}</td>'
            f"<td>{_bdi(entry.get('tool'), KEY_LIMIT)}</td>"
            f"<td>{_bdi(entry.get('status'), KEY_LIMIT)}</td>"
            f'<td>{_bdi(entry.get("as_of_jst"), KEY_LIMIT)}<span class="mark">'
            f"{_text(age) if age else 'age cannot be stated'}</span></td>"
            "</tr>"
        )
    return (
        f"<h2>Frozen evidence</h2><table><caption class='sub'>n={len(rows)} snapshots rendered "
        f"of the {_bdi(payload.get('n_snapshots'))} this row names.</caption>"
        '<thead><tr><th scope="col">snapshot</th><th scope="col">tool</th>'
        '<th scope="col">status</th><th scope="col">as_of</th></tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def render_card(payload: dict[str, Any], banner: str, now: datetime) -> str:
    """Layar B — "kenapa keputusan ini, bukti per baris dari mana & kapan?"."""
    raw = payload.get("items")
    items = [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []
    header = "".join(f'<th scope="col">{escape(name, quote=True)}</th>' for name in CARD_COLUMNS)
    rows = "".join(_item_row(item) for item in items)
    caption = (
        f"n={len(items)} rows rendered; this card holds "
        f"{_bdi(payload.get('n_items_total'))} rows (`n_items_total`), "
        f"{_bdi(payload.get('n_unspoken'))} of them unspoken."
    )
    table = (
        f"<table class='card'><caption class='sub'>{caption}</caption><thead><tr>{header}</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        if items
        else f'<p class="sub empty">{escape(EMPTY_SENTENCE, quote=True)} — this card holds no '
        "roll-call row at all.</p>"
    )
    # `n_warnings_rendered`, BUKAN `n_warnings`: yang di kiri kalimat ini adalah "berapa yang
    # muat di layar", dan `n_warnings` sekarang berarti cacah sebenarnya (TASKS 3.1b).
    warnings_caption = (
        f"{_plain(payload.get('n_warnings_rendered'))} rendered of "
        f"{_plain(payload.get('n_warnings_total'))} (`n_warnings_total`)"
    )
    api_href = _self_href(payload, "/api/decision/")
    json_link = (
        f' · <a href="{escape(api_href, quote=True)}">the JSON behind this card, verbatim</a>'
        if api_href
        else ""
    )
    warnings_block = _list_block(
        f"Envelope warnings ({warnings_caption})",
        payload.get("warnings"),
        "zero warnings (n=0)",
        lang=EVIDENCE_LANG,
    )
    body = (
        f'<p class="sub"><a href="/ui">← back to the watchlist</a>{json_link}'
        ' · <a href="#panggilan">skip to the ten point-then-speak rows</a></p>'
        f"<h1>{_bdi(payload.get('symbol'), KEY_LIMIT)} — {_decision_badge(payload.get('decision'))} "
        f"<span class='sub'>(decision #{_bdi(payload.get('decision_id'))}, "
        f"generation {_bdi(payload.get('generation'), KEY_LIMIT)})</span></h1>"
        f"{_card_answer(payload, items)}"
        f'<p class="sub" lang="{EVIDENCE_LANG}">{_text(payload.get("headline"))}</p>'
        # Sepuluh baris tunjuk→sebut mendahului daftar hash/jam: uji 30 detik menuntut isi
        # produk, bukan metadatanya, yang berada tepat di bawah kalimat jawaban. Daftar meta
        # sebelumnya berdiri di antara keduanya — delapan baris, sebagian berparagraf —
        # sehingga tabel yang menjawab "buktinya dari mana" terdorong ke bawah lipatan pada
        # 1280x720. Tidak ada isi yang hilang; yang berubah hanya urutannya, dan bagian meta
        # kini punya `<h2>` sendiri sehingga bisa dituju pembaca layar sebagai bagian.
        f'<h2 id="panggilan">Ten point-then-speak rows</h2>{table}'
        f"<h2>Hashes, clocks, and replay</h2>{_card_meta(payload, now)}"
        f"{_narration_block(payload)}"
        f"{warnings_block}"
        f"{_bundle_table(payload, now)}"
        f"{_notes_list(payload.get('notes'))}"
    )
    decision_id = payload.get("decision_id")
    title = TITLE_CARD.format(id=decision_id)
    return _shell(
        title,
        banner,
        body,
        now,
        screen="card",
        decision_id=decision_id if isinstance(decision_id, int) else None,
    )


def render_error(status_code: int, message: str, banner: str, now: datetime) -> str:
    """Halaman gagal yang tetap mengaku simulasi dan tidak mengarang satu angka pun."""
    body = (
        f'<p class="sub"><a href="/ui">← back to the watchlist</a></p>'
        f"<h1>{escape(str(status_code), quote=True)} — this screen cannot be served</h1>"
        f'<p class="answer">{_text(message)}</p>'
        '<p class="sub">No number was invented to cover this up, and neither a RYO tool nor an '
        "LLM was called.</p>"
    )
    return _shell(f"Tenko — {status_code} (simulated)", banner, body, now, screen="error")


# --- landing (ADR-021 · ADR-027 · ADR-028 · disusun ulang ADR-029) --------------------------
#: DUA gambar landing, NAMA dari :data:`IMAGE_NAMES` (daftar tertutup) — bukan string bebas.
#: Konstanta, bukan literal di tengah markup, supaya tes bisa menuntut keduanya benar-benar
#: dilayani dan supaya gambar ketiga tidak bisa menyelinap tanpa satu baris di sini.
#:
#: ADR-029 memindahkan keduanya tanpa menambah satu pun: `PROOF_IMAGE` kini berdiri di kepala
#: bagian 04 dan `BAND_IMAGE` menjadi pita penuh-lebar di kepala bagian 05. Masing-masing tetap
#: sendirian di dalam kotaknya (`.proofs-art`, `.band-art`), yang membuat klaim "nol kalimat
#: duduk di atas piksel gambar" tetap fakta tata letak, bukan harapan.
PROOF_IMAGE = "stone-lantern.webp"
BAND_IMAGE = "kage-moonwater.webp"

#: Kalimat yang menyatakan gambar-gambar itu PEMANDANGAN. Tanpa kalimat ini sebuah kuil di
#: belakang papan keberangkatan bisa terbaca sebagai bagian dari bukti — `docs/spec.md` §8
#: menyebut hiasan yang tampak nyata sebagai hal yang justru tidak dinilai.
ART_NOTE = (
    "Illustration — scenery, not evidence. Every measured number on this page stands on the "
    "departures board, beside the count it was read from."
)

#: Kalimat yang menyebut halaman `/kage` DAN menyatakan apa yang tidak ada di dalamnya. Ia
#: pindah ke kaki halaman pada ADR-029 (dulu di badan bagian "why"): halaman pemandangan tidak
#: boleh berdiri sejajar dengan papan di sepertiga atas, tetapi route yang dilayani dan tidak
#: pernah disebut adalah route yang hilang.
SCENERY_NOTE_LEAD = "Scenery, not evidence: an "
SCENERY_NOTE_TAIL = (
    " ships with this app. It reads nothing from the store, calls no RYO tool and no LLM, and "
    "every figure printed on it is design copy — the measured numbers are on the board."
)

if not {PROOF_IMAGE, BAND_IMAGE} <= set(IMAGE_NAMES):  # pragma: no cover — cacat kode
    raise RuntimeError(
        "gambar landing menunjuk nama di luar IMAGE_NAMES: rute `/ui/img/{name}` melayani "
        "daftar TERTUTUP, jadi nama yang tidak ada di sana adalah 404 di halaman pertama "
        "yang dilihat juri."
    )

#: Tiga angka yang boleh berdiri di landing. Masing-masing PUNYA pemeriksanya di repo ini, dan
#: kalimatnya menyebut perintah yang membuktikannya — bukan klaim pemasaran yang tidak bisa
#: dijalankan. Kalau salah satu berhenti benar, tesnya merah sebelum landing-nya berbohong.
LANDING_PROOFS = (
    (
        "10",
        "pieces of evidence, called one at a time",
        "Five RYO tools, ten items (docs/spec.md §4). Each one points at a tool.field, says its "
        "value and its as_of, and gets a status you can read without colour.",
    ),
    (
        "1",
        "red item — or one unspoken — holds the departure",
        "A field that is null or unavailable becomes unspoken, never 0 and never a pass. That is "
        "the whole product: missing evidence is said out loud, not defaulted away.",
    ),
    (
        "0",
        "LLM calls in the decision path",
        "The decision is a deterministic function over frozen snapshots (ADR-003). "
        "`make replay ID=` reproduces it byte for byte, and tampering reports MISMATCH.",
    ),
)

#: Lima langkah ritual, satu baris masing-masing: nomor, judul, istilah Jepangnya, satu kalimat,
#: dan **modul yang benar-benar menjalankannya**. Kolom terakhir itu yang membedakan daftar ini
#: dari daftar fitur: tiap baris menunjuk berkas di repo ini yang bisa dibuka juri, dengan cara
#: yang sama seperti tiap item kartu menunjuk `tool.field`-nya. Nama berkasnya diperiksa tes.
LANDING_STEPS = (
    (
        "01",
        "Freeze the snapshot",
        "記録",
        "The recorder stores tool envelopes as they arrived. Nothing is fetched while a "
        "decision is being made, so the same call can be made again later.",
        "core/recorder.py",
    ),
    (
        "02",
        "Point at the field",
        "指差",
        "Every item names the tool.field it read. A number without an address never reaches the "
        "card — it becomes an unspoken item instead.",
        "preflight/skill.py",
    ),
    (
        "03",
        "Say it out loud",
        "喚呼",
        "Value, status, and the as_of it was read at are printed in words beside each other, so "
        "the row reads correctly with the colour switched off.",
        "preflight/narrate.py",
    ),
    (
        "04",
        "Hold on anything unsaid",
        "未呼称",
        "A null or an unavailable section becomes unspoken, never 0 and never a pass. One "
        "unspoken item is enough to hold the departure on its own.",
        "core/skill.py",
    ),
    (
        "05",
        "Replay the whole call",
        "再現",
        "`make replay ID=` recomputes the decision from the stored bundle. Identical is the only "
        "verdict that counts as verified; a tampered bundle reports MISMATCH.",
        "agent/replay.py",
    ),
)

#: Bagian yang punya `id` dan karena itu boleh ditautkan, DALAM URUTAN DOKUMEN. Sejak ADR-030
#: yang menautkannya adalah KAKI halaman (kolom "On this page"), bukan pita atas: pita atas
#: dilebur menjadi satu baris bersama banner SIMULATED, dan banner itu yang mendapat lebarnya.
#: `pathways` (papan) sengaja TIDAK ada di sini: ia hanya dirender ketika store terbaca, dan
#: tautan yang menunjuk bagian yang tidak ada adalah tautan yang berbohong kepada keyboard. Ia
#: disisipkan :func:`_landing_sections` — pada tempatnya, di antara `gate` dan `lessons` —
#: HANYA ketika papannya benar-benar ikut. Urutan dokumen dan bukan "yang paling berguna dulu":
#: daftar yang menyebut bagian dengan urutan berbeda dari halamannya adalah peta yang harus
#: dipelajari, dan jalan pintas ke papan sudah ada dua kali (tautan lompat dan tombol
#: `Screen A`).
LANDING_SECTIONS = (
    ("gate", "Without it"),
    ("lessons", "How it works"),
    ("eternity", "Open the board"),
)

#: Kaki halaman bergaya kolom (susunan `kage.html`). Tiap tautan menunjuk sesuatu yang route-nya
#: BENAR-BENAR dilayani aplikasi ini; tesnya menembak semuanya, jadi kolom ini tidak bisa
#: menumbuhkan tautan hiasan.
FOOTER_COLUMNS = (
    (
        "Screens",
        (
            ("/app", "The board (Screen A)"),
            ("/", "This page"),
            ("/kage", "Illustrated page"),
        ),
    ),
    (
        "Read the JSON",
        (
            ("/api/routes", "/api/routes"),
            ("/api/watchlist", "/api/watchlist"),
            ("/api/health", "/api/health"),
        ),
    ),
)


def _sec_head(number: str, title: str, japanese: str) -> str:
    """Kepala bagian: label bernomor `NN — KATA`, garis, lalu satu istilah Jepang.

    Bentuknya diambil dari `kage.html` (`.sec-head`), idiom penomorannya dari ADR-028. Label
    tetap `<p class="kicker">` supaya SATU bentuk melayani hero, kepala bagian, dan kepala kolom
    papan — dan supaya penomorannya bisa dibaca dengan satu regex, bukan tiga.
    """
    return (
        '<div class="sec-head">'
        f'<p class="kicker">{escape(number, quote=True)} — {escape(title, quote=True)}</p>'
        '<span class="rule" aria-hidden="true"></span>'
        f'<span class="jp" lang="ja">{escape(japanese, quote=True)}</span>'
        "</div>"
    )


def _landing_sections(has_board: bool) -> tuple[tuple[str, str], ...]:
    """Bagian yang boleh ditautkan, DALAM URUTAN DOKUMEN.

    `pathways` (papan) hanya ada ketika store terbaca, jadi ia disisipkan pada tempatnya —
    di antara `gate` dan `lessons` — dan HANYA ketika papannya benar-benar ikut: tautan yang
    menunjuk bagian yang tidak ada adalah tautan yang berbohong kepada keyboard.
    """
    head, *rest = LANDING_SECTIONS
    return (head, ("pathways", "Departures"), *rest) if has_board else LANDING_SECTIONS


def _landing_masthead(banner: str, has_board: bool) -> str:
    """SATU pita mendatar di atas layar pertama: banner SIMULATED + wordmark + jalan ke Layar A.

    ADR-030. Sebelumnya ada EMPAT pita bertumpuk sebelum mata sampai ke isi (banner, kalimat
    klien, kepala halaman, railbar) — ~250px dari 720px, yaitu sepertiga layar pertama yang
    seluruh nilainya justru kekosongan. Yang tersisa sekarang satu pita setinggi 53px.

    Banner **dilebur, bukan dihapus**, dan itu batas yang tidak boleh salah dibaca: kalimatnya
    kewajiban Official Rules §5 (`docs/rules-compliance.md`) di layar PERTAMA yang dilihat juri.
    Yang berubah hanya kotaknya — teksnya utuh apa adanya, `role="note"`-nya utuh, warnanya
    (12.98:1) utuh, dan kelekatannya utuh: pita ini `position: sticky`, jadi kalimat itu ada di
    setiap posisi gulir dan di setiap frame video.

    Tautan bagian TIDAK ikut ke pita ini, dan itu keputusan lebar bukan selera: pada 1280px
    banner memakan ~770px, dan memaksa empat tautan bagian + wordmark + tombol ke sisa 500px
    akan membungkus banner menjadi dua baris — yaitu persis pita bertumpuk yang dibongkar
    ADR-030. Yang menang keterbacaan banner (perintah pemilik repo), dan tautan bagian itu tidak
    hilang: ia pindah utuh ke kolom "On this page" di kaki halaman.

    Tautan lompat berdiri di luar `<nav>` sebagai elemen fokusable PERTAMA halaman, dan saat
    difokus ia mendarat DI BAWAH pita — bukan di atasnya, karena yang di atasnya adalah banner.
    """
    skip = ("pathways", "Skip to the departures board") if has_board else ("hero", "Skip to the page")
    return (
        '<div class="headrow">'
        f'<a class="skip" href="#{skip[0]}">{skip[1]}</a>'
        f"{_banner(banner)}"
        '<nav class="topnav" aria-label="Tenko">'
        '<a class="brand" href="#hero">'
        '<b>TENKO<span class="jp" lang="ja">点呼</span></b>'
        "<i>pre-departure roll call</i></a>"
        '<a class="topnav-go" href="/app" aria-label="Open the watchlist board — Screen A">'
        "Screen A</a>"
        "</nav>"
        "</div>"
    )


def _landing_footer(has_board: bool) -> str:
    """Kisi kaki halaman (`.foot-grid` milik `kage.html`), dipasang di ATAS kaki tetap `_shell`.

    Yang di bawahnya tidak berubah satu kalimat pun: jam JST, "read-only, no RYO tool, no LLM",
    dan kalimat keyboard. Kisi ini menambahkan jalan pulang, bukan menggantikan pengakuan.

    Kolom ketiga ("On this page") adalah tautan bagian yang dulu berdiri di kepala halaman;
    ADR-030 memindahkannya ke sini supaya banner SIMULATED mendapat seluruh lebar yang ia
    butuhkan di pita atas. `has_board` diteruskan apa adanya ke :func:`_landing_sections`, jadi
    ketika store tak terbaca kolom ini kehilangan barisnya bersama papannya — kaki halaman
    tidak boleh menjadi tempat kedua di mana `#pathways` menggantung.
    """
    sections = (
        "On this page",
        tuple((f"#{anchor}", label) for anchor, label in _landing_sections(has_board)),
    )
    columns = "".join(
        f'<div class="foot-col"><h2 class="foot-h">{escape(title, quote=True)}</h2><ul>'
        + "".join(
            f'<li><a href="{escape(href, quote=True)}">{escape(label, quote=True)}</a></li>'
            for href, label in entries
        )
        + "</ul></div>"
        for title, entries in (*FOOTER_COLUMNS, sections)
    )
    return (
        '<div class="foot-grid">'
        '<div class="foot-brand">'
        '<p class="brand-mark">TENKO <span lang="ja">点呼</span></p>'
        '<p class="sub">A pre-departure roll call for a practice trading agent: ten items '
        "called out one at a time, each pointing at the tool.field it was read from. No funds, "
        "no wallet, no signing keys, no real execution.</p>"
        f'<p class="sub">{escape(SCENERY_NOTE_LEAD, quote=True)}'
        '<a href="/kage">illustrated page</a>'
        f"{escape(SCENERY_NOTE_TAIL, quote=True)}</p>"
        "</div>"
        f"{columns}"
        "</div>"
    )


def _landing_board(entries: list[dict[str, Any]]) -> str:
    """Papan keberangkatan: baris NYATA dari store, atau string kosong.

    Kolomnya mengikuti urutan papan stasiun — waktu, tujuan, status, penahan — karena itu urutan
    yang tidak perlu dipelajari ulang oleh orang yang baru pertama kali melihat layar ini.

    Kalau store tidak terbaca atau belum ada keputusan, papan ini **tidak muncul**. Yang tidak
    boleh terjadi adalah landing berisi baris contoh: `docs/spec.md` §8 menyebut "data
    fabrikasi/placeholder sebagai nyata" sebagai hal yang tidak dinilai, dan `rules-compliance`
    membacanya lebih keras dari itu.

    ADR-029 mengganti pakaiannya (kepala bagian bernomor, `id="pathways"`, tabel penuh lebar)
    dan TIDAK mengganti satu pun kolom, hitungan, atau kalimat caption-nya: struktur tabel
    adalah bagian yang dinilai, bukan bagian yang ditata ulang.
    """
    if not entries:
        return ""
    shown = entries[:5]
    rows = ""
    for entry in shown:
        decision = _plain(entry.get("decision"))
        held = entry.get("n_blocking")
        unspoken = entry.get("n_unspoken")
        if not isinstance(held, int) or held == 0:
            holder = "nothing holds it"
        else:
            holder = f"{held} held"
            if isinstance(unspoken, int) and unspoken:
                holder += f", {unspoken} unspoken"
        tone = decision.lower() if _css_word(decision) else "unknown"
        rows += (
            "<tr>"
            f'<td class="mono">{_bdi(entry.get("as_of_jst") or entry.get("ts_jst") or "—")}</td>'
            f'<th scope="row">{_bdi(entry.get("symbol"))}</th>'
            f'<td><span class="mark d-{tone}">{_bdi(decision)}</span></td>'
            f'<td class="dim">{escape(holder, quote=True)}</td>'
            "</tr>"
        )
    return (
        '<section class="board sec" id="pathways" aria-labelledby="board-h">'
        f"{_sec_head('03', 'Departures', '発車標')}"
        '<h2 id="board-h">Departures · simulated</h2>'
        '<table class="lb">'
        '<colgroup><col class="c-when"><col class="c-sym"><col class="c-call"><col></colgroup>'
        '<caption class="sub">'
        f"{len(shown)} of {len(entries)} symbols the store holds a current decision for, "
        "rendered by the server from the same store the app reads. All clocks are JST."
        "</caption>"
        "<thead><tr><th>As of</th><th>Symbol</th><th>Call</th><th>Held by</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "</section>"
    )


def render_landing(banner: str, now: datetime, entries: list[dict[str, Any]] | None = None) -> str:
    """Landing page (ADR-021). `entries` = baris `/api/watchlist`, atau `None` bila tak terbaca.

    Halaman ini TIDAK pernah 503: store yang mati menghilangkan PAPANnya, bukan halamannya —
    landing yang gagal akan menjadi kesan pertama yang salah tentang produk yang justru dibangun
    untuk tetap jujur saat sumbernya hilang.

    Susunan bagiannya (ADR-029) diambil dari `kage.html` — karya teman pemilik repo yang dipakai
    dengan izin (ADR-026) — dan hero-nya tetap formula tangkapan layar awwwards (ADR-028):

    * pita atas — SATU baris (ADR-030): banner SIMULATED, wordmark, jalan ke Layar A, tautan
      lompat. Empat pita bertumpuk sebelumnya (banner · kalimat klien · kepala · railbar)
      memakan ~250px dari layar 720px sebelum mata sampai ke isi;
    * `01 hero` — apa itu Tenko: void, satu cahaya, judul raksasa di sepertiga bawah;
    * `02 gate` — apa yang salah tanpa panggilan ini;
    * `03 pathways` — **papan keberangkatan, baris NYATA dari store** (hilang bila store mati);
    * `04 lessons` — cara kerjanya, lima langkah + tiga angka yang punya pemeriksanya;
    * `05 eternity` — penutup dan tautan ke `/app`;
    * kaki — kisi tautan di atas kaki tetap `_shell`.
    """
    proofs = "".join(
        f'<li><p class="fig">{escape(figure, quote=True)}</p>'
        f'<p class="fig-label">{escape(label, quote=True)}</p>'
        f'<p class="sub">{escape(body, quote=True)}</p></li>'
        for figure, label, body in LANDING_PROOFS
    )
    steps = "".join(
        f'<li class="les"><span class="k">{escape(number, quote=True)}</span>'
        f"<h3>{escape(title, quote=True)}"
        f'<em class="jp" lang="ja">{escape(japanese, quote=True)}</em></h3>'
        f"<p>{escape(sentence, quote=True)}</p>"
        f'<span class="t">{escape(module, quote=True)}</span></li>'
        for number, title, japanese, sentence, module in LANDING_STEPS
    )
    board = _landing_board(entries or [])
    # Penomoran bagian boleh BERLUBANG: nomor 03 hilang bersama papannya ketika store tak
    # terbaca, dan celah itu lebih jujur daripada menomori ulang — bagian yang hilang memang
    # hilang, dan halaman tidak berpura-pura utuh.
    body = (
        # HERO (ADR-028, TIDAK dibongkar ADR-029). Tiga hal saja di layar pertama, dan yang
        # KETIGA adalah ruang kosong: label mono di kiri atas, kekosongan yang disengaja di
        # tengah, judul + satu kalimat + satu tautan di sepertiga bawah. Tidak ada gambar dan
        # tidak ada kolom kedua — hero dua-kolom teks|gambar terbaca seperti brosur, dan brosur
        # adalah hal yang paling tidak dipercaya seorang juri di detik pertama.
        '<section class="hero" id="hero">'
        # Baris atas: label bernomor di kiri, dan di kanan kalimat yang menyatakan seluruh
        # halaman ini simulasi. Kalimat itu tetap di layar PERTAMA dengan sengaja
        # (`docs/rules-compliance.md`), dan ia diletakkan di tepi atas justru supaya ia tidak
        # membayar dengan ruang kosong yang menjadi inti tata letak ini.
        #
        # Labelnya SEPENDEK label rujukannya (ADR-030). Bentuk lamanya — `01 — 点呼 TENKO ·
        # PRE-DEPARTURE ROLL CALL` — mengulang wordmark yang berdiri persis di atasnya, dan
        # tangkapan layar yang menjadi rujukan tata letak ini hanya punya `01 — HERO`.
        '<div class="hero-top">'
        '<p class="kicker">01 — Roll call</p>'
        '<p class="hero-note">Every position here is a <strong>practice</strong> trade: no '
        "funds, no wallet, no signing keys, no real execution.</p>"
        "</div>"
        # Baris kedua: kekosongan, lalu judul + kalimat + tautan yang menempel ke dasarnya.
        # `hero-in` membatasi LEBAR baca (--wide) tanpa memusatkannya; baris atas di atasnya
        # sengaja tidak dibatasi, supaya label dan kalimat "practice" berdiri di kedua TEPI
        # halaman seperti pada tangkapan layar rujukan.
        '<div class="hero-in">'
        '<div class="hero-say">'
        "<h1>Point at the evidence.<br>Then decide.</h1>"
        '<p class="stand">Ten pieces of evidence, from five RYO tools, called out one at a '
        "time before every practice trade. One red item — or one that goes "
        "<strong>unspoken</strong> — holds the departure.</p>"
        '<p class="cta"><a class="go" href="/app" aria-label="Open the watchlist '
        'board — Screen A, every symbol with a current decision">Open the board</a>'
        '<a class="alt" href="/api/routes" aria-label="See every route this app '
        'serves, as JSON">See every route</a></p>'
        "</div>"
        "</div>"
        # Tanda tegak di tepi kanan hero — satu-satunya hiasan layar pertama, dan ia
        # `aria-hidden` karena kata yang sama sudah dibacakan wordmark di kepala halaman.
        '<p class="hero-side" aria-hidden="true"><span lang="ja">点呼</span></p>'
        "</section>"
        # 02 — apa yang salah tanpanya. Susunan `gate` milik `kage.html`: judul pendek di kiri,
        # prosa di kanan, lalu satu baris fakta struktural di bawah garis.
        '<section class="sec" id="gate" aria-labelledby="gate-h">'
        f"{_sec_head('02', 'Without the roll call', '見落とし')}"
        '<div class="gate-grid">'
        '<h2 id="gate-h">One verdict, and no idea what it never checked.</h2>'
        '<div class="gate-copy">'
        '<p class="lead">Almost every signal agent acts on a single verdict without checking '
        "the evidence that is available — and, worse, without knowing which evidence is "
        "<em>not</em> available: stale data, an <code>unavailable</code> section, a plan that "
        "does not exist. The common guardrail treats missing data as a pass, or as a zero.</p>"
        '<p class="body">Tenko never does. A null is not a zero, and it is not silence either: '
        "it is an <strong>unspoken</strong> item, printed on the card, and it is enough to hold "
        "the departure on its own.</p>"
        '<ul class="pills">'
        '<li class="on">Simulated · no funds</li>'
        "<li>5 RYO tools · 10 items</li>"
        "<li>Replay byte-identical</li>"
        "</ul>"
        '<a class="arrowlink" href="#lessons"><span>See how a call is made</span>'
        '<span class="ar" aria-hidden="true">→</span></a>'
        "</div>"
        "</div>"
        '<div class="rails">'
        '<p><span class="rail-label">All clocks</span><span>JST — as the board reads them</span></p>'
        '<p><span class="rail-label">Decision path</span><span>Deterministic · no LLM</span></p>'
        '<p><span class="rail-label">Store</span><span>Read-only · append-only</span></p>'
        "</div>"
        "</section>"
        f"{board}"
        # 04 — cara kerjanya. Kepala dua kolom (`cur-head`), tiga angka yang punya pemeriksanya,
        # lalu lima baris langkah (`cur`/`les`) yang masing-masing menunjuk modul pelaksananya.
        '<section class="sec lessons" id="lessons" aria-labelledby="lessons-h">'
        f"{_sec_head('04', 'How a call is made', '指差喚呼')}"
        '<div class="cur-head">'
        '<div class="cur-say">'
        '<h2 id="lessons-h">Point at it, and say it out loud</h2>'
        '<p class="lede">Japanese train crews do not trust a signal they have only looked at. '
        "They point at it and say it aloud — shisa kanko — and the error rate collapses. Tenko "
        "runs that ritual over market evidence: every item names the tool.field it came from, "
        "the value it read, and the as_of it was read at.</p>"
        "</div>"
        # Gambar: `<img>` sungguhan dari daftar TERTUTUP `IMAGE_NAMES`, dilayani origin ini
        # sendiri (`img-src 'self'`), dengan ukuran intrinsiknya supaya tidak ada geseran tata
        # letak saat ia tiba. `alt=""` disengaja dan BENAR: ia pemandangan, bukan bukti — dan
        # kotaknya sengaja TIDAK memuat teks apa pun, sehingga klaim "nol kalimat duduk di atas
        # piksel gambar" adalah fakta tata letak, bukan harapan.
        '<div class="proofs-art">'
        f'<img src="/ui/img/{PROOF_IMAGE}" alt="" width="1024" height="1499">'
        "</div>"
        "</div>"
        f'<p class="art-note">{escape(ART_NOTE, quote=True)}</p>'
        f'<ol class="figs">{proofs}</ol>'
        f'<ol class="cur">{steps}</ol>'
        "</section>"
        # 05 — penutup. Pita gambar penuh-lebar, lalu teksnya DI BAWAHNYA (bukan di atasnya),
        # dan satu tautan ke papan yang sama seperti di hero: seorang juri yang menggulir sampai
        # dasar tidak boleh harus menggulir balik untuk menemukan produknya.
        '<section class="band fin" id="eternity" aria-labelledby="fin-h">'
        '<div class="band-art">'
        f'<img src="/ui/img/{BAND_IMAGE}" alt="" width="1536" height="1024">'
        "</div>"
        '<div class="band-in">'
        '<p class="kicker">05 — Departure</p>'
        '<h2 id="fin-h">Open the board</h2>'
        '<p class="lede">Every symbol the store holds a current decision for, with the item '
        "that holds it and the count of items that were never spoken at all. Ten rows behind "
        "each call, each one pointing at where it was read and when.</p>"
        '<p class="cta"><a class="go" href="/app" aria-label="Open the watchlist board — '
        'Screen A, every symbol with a current decision">Open the board</a>'
        '<a class="alt" href="/api/watchlist" aria-label="Read the same board as JSON">'
        "Read it as JSON</a></p>"
        "</div>"
        "</section>"
    )
    return _shell(
        "Tenko (点呼) — simulated",
        banner,
        body,
        now,
        screen="landing",
        foot_top=_landing_footer(bool(board)),
        masthead=_landing_masthead(banner, bool(board)),
    )


# --- halaman `/kage` ----------------------------------------------------------------------
#: Kalimat yang menyatakan apa halaman ini DAN apa yang bukan. Ia dicetak di layar, bukan hanya
#: di README: halaman ini tidak membaca store, tidak memanggil tool RYO, dan tidak memutuskan
#: apa pun — semua angka di dalamnya adalah teks desain, dan satu-satunya angka yang dinilai ada
#: di Layar A (`/app`). Menyembunyikan itu akan membuat "1611" dan "92 minutes" di halaman ini
#: terbaca sebagai temuan (spec §8: data hiasan yang tampak nyata tidak dinilai).
KAGE_NOTE = (
    "This page is scenery, not evidence: it reads nothing from the store, calls no RYO tool and "
    "no LLM, and every figure printed on it is design copy — not a measurement. The numbers that "
    "are measured live on the board at /app, each next to its own n."
)


def render_kage(banner: str, now: datetime) -> str:
    """Halaman `/kage` — papan gambar bergerak, dengan banner SIMULATED dan jalan pulang.

    Dirender di server seperti layar lain, dari markup statis (:data:`KAGE_BODY`) yang tidak
    membawa satu pun nilai asal-DB — jadi tidak ada yang bisa disuntikkan lewat halaman ini.
    Bedanya dengan :func:`_shell`: halaman ini memuat `/ui/kage.css` (bukan `/ui/tenko.css`) dan
    tidak memuat `/ui/app.js` sama sekali, karena klien Layar A/B tidak punya pekerjaan di sini —
    yang jalan hanya three.js dan scene-nya, keduanya `defer` dan keduanya dari origin sendiri.

    Banner dan tautan pulang dirender DUA KALI dalam satu bentuk: satu strip tetap (`position:
    fixed`) yang tidak ikut tergulir, sehingga kalimat SIMULATED terlihat di setiap posisi
    gulir halaman sepanjang ini — bukan hanya di atas lipatan.
    """
    strip = (
        '<p class="tenko-banner" role="note">'
        f"<b>SIMULATED</b><span>{escape(banner, quote=True)}</span>"
        '<a href="/app">Open the board (Screen A)</a>'
        '<a href="/">Tenko landing</a>'
        "</p>"
    )
    footer = (
        '<p class="tenko-foot">'
        f"{escape(KAGE_NOTE, quote=True)} All clocks are JST; this page was rendered on the "
        f"server at {escape(_now_jst(now), quote=True)}, banner and all, before any script ran."
        "</p>"
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="tenko-simulated" content="true">'
        "<title>Kage — Tenko (点呼), simulated</title>"
        '<link rel="stylesheet" href="/ui/kage.css">'
        '<script src="/ui/vendor/three.min.js" defer></script>'
        '<script src="/ui/vendor/kage-scene.js" defer></script>'
        '</head><body data-screen="kage" data-layout-hero="b" data-layout-story="b" '
        'data-layout-gallery="b" data-layout-curriculum="b" data-layout-closing="b" '
        'data-layout-footer="b">'
        f"{strip}{KAGE_BODY}{footer}"
        "</body></html>"
    )
