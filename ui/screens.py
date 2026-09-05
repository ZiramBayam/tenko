"""`ui/screens.py` — Layar A (watchlist) & Layar B (kartu panggilan), HTML dirender di server.

Fungsi di sini **murni**: masuk `dict` (persis badan respons `/api/watchlist` dan
`/api/decision/{id}` dari :mod:`ui.app`), keluar `str` HTML. Tidak ada store, tidak ada jam
global selain `now` yang diberikan pemanggil, tidak ada I/O — jadi setiap kalimat yang muncul di
layar bisa diuji sebagai teks, bukan sebagai janji.

Kenapa dirender di server, bukan dirakit `fetch` + DOM di browser
-----------------------------------------------------------------
Warisan review 1.5 menuntut kartu **meng-escape sendiri** dan **mengisolasi arah teks**: U+202E
terbukti sampai ke stdout lewat `data` dan `ItemResult.value`, dan satu simbol ber-pembalik-arah
bisa membuat baris kartu terbaca TERBALIK di depan juri. Cara paling murah untuk membuktikan itu
tertutup adalah menghapus permukaannya: **halaman ini tidak memuat satu pun `<script>`**, jadi
tidak ada `innerHTML` yang bisa salah dipakai, dan header `Content-Security-Policy` di
:mod:`ui.app` melarang skrip sama sekali (`default-src 'none'`). Yang tersisa hanya dua lapis
yang keduanya dipatok tes:

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

from datetime import datetime
from html import escape
from typing import Any

from core.skill import KEY_LIMIT, REASON_LIMIT, TEXT_LIMIT, TRUNCATED_MARK, WARNING_LIMIT, clean_text

#: Label yang menempel pada SETIAP narasi model. Dipatok tes: hilangnya label ini mengubah
#: "narasi tidak diverifikasi" menjadi "narasi tampak terverifikasi" — klaim menyesatkan yang
#: `docs/rules-compliance.md` sebut risiko diskualifikasi.
NARRATION_LABEL = "narasi model — TIDAK diverifikasi terhadap bukti"
NARRATION_NOTE = (
    "Kalimat di kolom ini ditulis model bahasa. Hanya `item_id`-nya yang divalidasi; ISI-nya "
    "tidak pernah dicocokkan dengan bukti, dan tidak ada kode yang menahan model bila ia menulis "
    "vonis. Yang bisa diputar ulang adalah kolom `reason` di sebelahnya, bukan kolom ini."
)
NARRATION_EMPTY = "tidak ada narasi untuk baris ini"

#: Batas panjang kolom `nilai`. Ia BUKAN batas kejujuran: `ui.app` sudah membersihkan tiap teks
#: di dalam `value` satu per satu, dan yang dibatasi di sini hanya panjang barisan `k=v` yang
#: dirakit layar. Kalau ia menggigit, layar MENGATAKANNYA dan menunjuk JSON mentahnya.
VALUE_LIMIT = 1500

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
    "kalimat ini sudah terpotong SEBELUM dibukukan (langit-langit `core.skill` {limit} aksara; "
    "panjangnya persis {total} aksara, yaitu langit-langit itu); yang hilang tidak ada di baris "
    "ledger mana pun, jadi layar ini tidak bisa memunculkannya.{era}"
)

#: Klausa yang menjelaskan langit-langit MANA yang cocok. `TEXT_LIMIT` disebut terlebih dahulu
#: supaya, kalau kedua konstanta kebetulan bernilai sama, yang menang adalah era BERJALAN
#: (klausa kosong) dan kartu tidak menuduh baris hari ini sebagai baris warisan.
LEDGER_CEILING_ERAS: dict[int, str] = {
    TEXT_LIMIT: (
        f" Baris ini dibukukan ketika langit-langit itu masih `TEXT_LIMIT`={TEXT_LIMIT}, SEBELUM "
        f"dinaikkan ke `REASON_LIMIT`={REASON_LIMIT}: kalimatnya tidak pernah tersimpan utuh, "
        "jadi memuat ulang halaman ini tidak akan memunculkannya — hanya baris yang direkam "
        "sesudah kenaikan itu yang membawanya."
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
    "teks ini dipotong OLEH layar ini pada {limit} aksara; bentuk penuhnya ada di JSON kartu "
    "yang ditautkan di atas"
)
VALUE_CUT_NOTE = SCREEN_CUT_NOTE.format(limit=VALUE_LIMIT)

#: Langit-langit ledger yang bisa dikenali pada kolom `reason`, urut dari yang berlaku KINI.
REASON_CEILINGS = (REASON_LIMIT, TEXT_LIMIT)
FOREIGN_MARK_NOTE = (
    f"teks ini memuat penanda `{TRUNCATED_MARK}` tetapi panjangnya tidak berada di langit-langit "
    "mana pun dan layar ini tidak memotongnya: kalau ada yang hilang, hilangnya terjadi sebelum "
    "baris ini dibukukan — layar ini tidak menyembunyikan apa pun darinya"
)

#: Penanda hijau-netral (amandemen ADR-017). Tekstual, bukan warna.
NEUTRAL_MARK = "netral — tidak menentang, bukan konfirmasi"
UNSPOKEN_MARK = "tidak disebut — bukti tidak cukup, dan itu bukan nol"

#: Batas panjang SATU `aria-label`. Ia bukan batas kejujuran melainkan batas kesabaran: pembaca
#: layar membacakan atribut ini utuh dan tanpa jeda, jadi kalimat 1500 aksara mengubur nilai yang
#: harus terdengar. Bila ia menggigit, `clean_text` menempelkan penandanya sehingga PENDENGAR
#: tahu ada yang dipotong — dan sel di sebelahnya tetap memuat bentuk penuhnya.
ARIA_LABEL_LIMIT = 600
#: Batas nilai DI DALAM `aria-label`; `value` item bisa ratusan aksara `k=v`.
ARIA_VALUE_LIMIT = 200
#: Nilai yang tidak ada di bukti, dibacakan sebagai "tidak disebut" — TIDAK PERNAH sebagai 0 dan
#: tidak pernah sebagai kesunyian (ADR-013). Sel kosong yang terdengar seperti nol adalah
#: kebohongan yang paling murah dilakukan pembaca layar, dan produk ini persis menjual lawannya.
ARIA_NULL = "nilai tidak ada di bukti — tidak disebut, dan itu bukan nol"
ARIA_NO_SOURCE = "sumber tidak disebut"
#: Kalimat keyboard yang tercetak di kaki SETIAP layar (TASKS 4.6). Ia bukan hiasan: "Esc tidak
#: punya yang harus ditutup" adalah klaim yang dipatok tes (nol dialog, nol details, nol tabindex).
KEYBOARD_NOTE = (
    "Keyboard: Tab / Shift+Tab menelusuri seluruh layar dalam urutan dokumen, Enter membuka "
    "tautan yang sedang berfokus, dan fokus itu selalu bergaris kuning tebal. Tidak ada modal, "
    "overlay, menu, maupun blok yang harus dibuka — jadi Esc tidak punya apa pun untuk ditutup "
    "dan tidak ada perangkap fokus. Tidak ada isi yang muncul hanya saat kursor melayang."
)

#: Kalimat umur bundle. `as_of` dijaga `payload_hash`; `ts_utc` tidak dijaga apa pun.
AGE_NOTE = (
    "Umur ini TAMPILAN: dihitung dari `as_of` bundle (dijaga `payload_hash`), bukan dari "
    "`ts_utc` baris ledger (tidak dijaga hash apa pun), dan ia tidak masuk `decision_hash`."
)
EMPTY_SENTENCE = "belum ada data (n=0)"

#: Urutan kolom kartu; dipakai header dan sel supaya tidak bisa bergeser diam-diam.
#:
#: Ia SEBELUMNYA berisi tujuh judul untuk sembilan sel: satu judul, "sumber: tool.field ·
#: snapshot · as_of", berdiri di atas TIGA kolom sekaligus, sehingga judul terakhir
#: (:data:`NARRATION_LABEL`) jatuh di atas kolom `snapshot` dan dua kolom terakhir tidak
#: berjudul sama sekali. Di layar itu terbaca sebagai tabel yang bergeser; bagi pembaca layar
#: itu `<th scope="col">` yang menunjuk kolom yang salah. Judulnya kini satu per kolom, dan
#: :func:`tests.test_ui.test_the_card_table_has_one_header_for_every_column_it_renders`
#: menghitung keduanya supaya tidak bisa berselisih lagi.
CARD_COLUMNS = (
    "daya veto",
    "item",
    "status",
    "reason (deterministik, bisa di-replay)",
    "nilai yang disebut",
    "sumber: tool.field",
    "snapshot",
    "as_of bukti",
    NARRATION_LABEL,
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

STYLESHEET = """/* Tenko — Layar A & B. Tanpa library, tanpa CDN, tanpa animasi (ADR-001).

   PALET TIDAK BERUBAH pada penyegaran tampilan ini. Kedua belas warna di bawah adalah warna
   yang sama yang diukur TASKS 4.6, dan angkanya masih dipatok
   `tests/test_ui.py::test_every_colour_pair_in_the_served_stylesheet_is_measured_not_claimed`:
   teks terendah nogo #ff9a90 di atas panel #161b24 = 8.45:1 (batas 4.5:1); garis #646e84
   di atas panel = 3.38:1 (batas non-teks 3:1); cincin fokus #ffd479 di atas #0d1016 = 13.55:1.
   Yang berubah hanya tipografi, ritme spasi, bentuk, dan lebar kolom — bukan satu pun warna,
   jadi tidak ada rasio yang perlu dihitung ulang.

   Warna tidak pernah membawa informasi sendirian: tiap lencana membawa teksnya, hijau-netral
   membawa kalimatnya, dan `unspoken` membawa "tidak disebut". Nol aset dari luar repo ini:
   nol webfont, nol gambar yang dimuat, nol URI tertanam — CSP `img-src 'none'` juga
   melarangnya, dan tes menolak keduanya di berkas ini. */
:root {
  --bg: #0d1016; --panel: #161b24; --line: #646e84; --ink: #f3f5f9; --dim: #c3cad6;
  --go: #7ff0a0; --nogo: #ff9a90; --warn: #ffd479; --link: #9fd0ff;
  /* Baris genap tabel. Dulu angka telanjang di satu aturan; sekarang bernama supaya ia ikut
     terbaca sebagai bagian palet yang diukur, bukan warna kelima belas yang menyelinap. */
  --row: #11151d;
  /* Satu tangga spasi dan dua radius, dipakai ulang di seluruh layar: jarak antarblok tidak
     lagi dikarang per tempat, dan itulah yang membuat halaman terbaca punya ritme. */
  --s1: 4px; --s2: 8px; --s3: 12px; --s4: 18px; --s5: 28px; --s6: 44px;
  --r1: 5px; --r2: 9px;
  /* Font SISTEM saja — nol webfont dan nol permintaan ke luar. Prosa memakai sans (kalimat
     yang harus terbaca dalam 30 detik), sedangkan angka, hash, id, dan nilai asal-RYO tetap
     monospace supaya kolomnya sejajar dan aksara yang mirip tidak tertukar. */
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "DejaVu Sans", Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "DejaVu Sans Mono", "Liberation Mono", Consolas, monospace;
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body {
  margin: 0 auto; max-width: 1240px; padding: 0 var(--s4) var(--s6);
  background: var(--bg); color: var(--ink); font: 15px/1.55 var(--sans);
}
a { color: var(--link); text-underline-offset: 2px; }
/* Fokus keyboard: `:focus` ikut disebut supaya cincin ini tidak bergantung pada dukungan
   `:focus-visible`; 3px kuning di atas latar segelap ini = 13.55:1, jauh di atas 3:1 yang
   diminta 1.4.11 dan terlihat pada video 1280x720. */
a:focus, a:focus-visible { outline: 3px solid var(--warn); outline-offset: 2px; }
/* Tautan lompat memindahkan fokus ke tabel; `:target` menandai TEMPAT MENDARATNYA supaya
   pemakai keyboard tidak kehilangan jejak. `scroll-margin-top` menjaga tempat itu tidak
   mendarat di balik banner yang melekat. Tanpa JS, tanpa animasi. */
:target { outline: 3px solid var(--warn); outline-offset: 4px; scroll-margin-top: 88px; }

/* --- banner SIMULATED ------------------------------------------------------------------
   `sticky`, BUKAN `fixed`: ia tetap mengalir di dalam dokumen (jadi tidak menutupi apa pun
   dan tidak menjadi overlay yang bisa menjebak fokus — lihat
   `test_escape_has_nothing_to_close_because_no_screen_traps_focus`), tetapi tidak pernah
   keluar dari bingkai saat juri menggulir. Kewajiban `docs/rules-compliance.md` ini karena
   itu terbukti di setiap frame video, bukan hanya di frame pertama. */
.banner {
  position: sticky; top: 0; z-index: 2;
  display: flex; align-items: center; gap: var(--s2);
  background: #3a1d1d; border: 2px solid var(--nogo); border-top: 0;
  border-radius: 0 0 var(--r2) var(--r2);
  color: #ffe8e5; padding: 10px var(--s3);
  margin: 0 calc(var(--s4) * -1) var(--s4);
  font: 700 14px/1.4 var(--mono); letter-spacing: .02em;
}
.banner svg { flex: 0 0 auto; }

/* --- hierarki tipografi ------------------------------------------------------------------ */
h1 { font: 700 30px/1.25 var(--sans); letter-spacing: -.01em; margin: var(--s4) 0 var(--s2); }
h1 .badge { font-size: 17px; vertical-align: 4px; }
h1 .sub { font: 500 15px/1.4 var(--sans); }
h2 {
  font: 700 19px/1.3 var(--sans); letter-spacing: .01em;
  margin: var(--s5) 0 var(--s2); padding-bottom: var(--s2);
  border-bottom: 2px solid var(--line);
}
p { margin: var(--s2) 0; }
/* Kalimat yang harus menjawab pertanyaan juri dalam 30 detik. Ia dinaikkan menjadi kartu
   ber-panel supaya mata mendarat di sana lebih dulu — elevasi lewat garis + latar, bukan
   bayangan tebal. */
.answer {
  font-size: 18px; line-height: 1.5; max-width: 100ch;
  background: var(--panel); border: 1px solid var(--line); border-left: 4px solid var(--line);
  border-radius: var(--r2); padding: var(--s3) var(--s4); margin: var(--s3) 0;
}
.sub { color: var(--dim); font-size: 14px; margin: var(--s1) 0; max-width: 108ch; }
.dim { color: var(--dim); }
.empty { color: var(--warn); font-weight: 700; }
code { font-family: var(--mono); font-size: inherit; overflow-wrap: anywhere; }
.mono { font-family: var(--mono); }

/* --- tabel ------------------------------------------------------------------------------- */
table {
  border-collapse: collapse; width: 100%; table-layout: fixed;
  margin: var(--s2) 0 var(--s4); font-size: 13.5px;
}
caption { text-align: left; padding: 0 0 var(--s2); }
th, td {
  border: 1px solid var(--line); padding: var(--s2) 10px; text-align: left; vertical-align: top;
  overflow-wrap: anywhere;
}
thead th {
  background: var(--panel); color: var(--ink);
  font: 700 13px/1.35 var(--sans); letter-spacing: .03em; border-bottom-width: 2px;
}
tbody th { background: var(--panel); color: var(--ink); font-weight: 700; }
tbody tr:nth-child(even) td { background: var(--row); }
td.num {
  font-family: var(--mono); text-align: right; white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
/* 1280x720, Layar B: sembilan kolom diberi lebar tetap supaya `reason` (kolom yang menjawab
   pertanyaan juri) tidak terhimpit kolom nilai yang panjangnya bisa ratusan aksara. */
table.card tr > *:nth-child(1) { width: 5%; }
table.card tr > *:nth-child(2) { width: 10%; }
table.card tr > *:nth-child(3) { width: 10%; }
table.card tr > *:nth-child(4) { width: 22%; }
table.card tr > *:nth-child(5) { width: 14%; }
table.card tr > *:nth-child(6) { width: 11%; }
table.card tr > *:nth-child(7) { width: 6%; }
table.card tr > *:nth-child(8) { width: 10%; }
table.card tr > *:nth-child(9) { width: 12%; }
/* Layar A: kolom "apa yang menahan" adalah jawabannya, jadi ia mendapat sepertiga lebar —
   sebelumnya sembilan kolom dibagi rata dan kalimat penahan terperas menjadi satu kata per
   baris. */
table#watchlist tr > *:nth-child(1) { width: 10%; }
table#watchlist tr > *:nth-child(2) { width: 8%; }
table#watchlist tr > *:nth-child(3) { width: 6%; }
table#watchlist tr > *:nth-child(4) { width: 30%; }
table#watchlist tr > *:nth-child(5) { width: 8%; }
table#watchlist tr > *:nth-child(6) { width: 7%; }
table#watchlist tr > *:nth-child(7) { width: 9%; }
table#watchlist tr > *:nth-child(8) { width: 10%; }
table#watchlist tr > *:nth-child(9) { width: 12%; }

/* --- lencana ----------------------------------------------------------------------------- */
.badge {
  display: inline-block; padding: 2px 10px; border: 2px solid currentColor; border-radius: 999px;
  font: 700 13px/1.5 var(--mono); letter-spacing: .03em; white-space: nowrap;
}
/* Satu-satunya lencana yang juga tautan ("putar ulang sekarang"): bentuk tombol, bukan pil,
   supaya afordansinya terbaca tanpa warna dan tanpa hover. */
a.badge { padding: 7px 14px; border-radius: var(--r2); text-decoration: none; }
.d-GO { color: var(--go); }
.d-NO_GO { color: var(--nogo); }
.s-green { color: var(--go); }
.s-green-neutral { color: var(--go); border-style: dashed; }
.s-red { color: var(--nogo); }
.s-unspoken { color: var(--warn); }
.tone-verified { color: var(--go); }
.tone-finding { color: var(--nogo); }
.tone-not_assessed { color: var(--warn); }
.tone-no_evidence { color: var(--warn); }
.tone-unknown { color: var(--dim); }
.mark { display: block; color: var(--dim); font-size: 13px; line-height: 1.45; margin-top: var(--s1); }
.narr { color: var(--dim); }
.narr-label {
  display: block; color: var(--warn); font-weight: 700; font-size: 13px;
  letter-spacing: .02em; margin-bottom: var(--s1);
}

/* --- daftar definisi (hash, jam, replay) -------------------------------------------------- */
dl.meta {
  display: grid; grid-template-columns: 24ch 1fr; gap: 0;
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--r2);
  overflow: hidden; margin: var(--s3) 0 var(--s4);
}
dl.meta dt {
  color: var(--dim); font-weight: 700; font-size: 14px;
  padding: var(--s2) var(--s3); border-top: 1px solid var(--line);
}
dl.meta dd {
  margin: 0; padding: var(--s2) var(--s3); border-top: 1px solid var(--line);
  font-family: var(--mono); font-size: 13.5px; overflow-wrap: anywhere;
}
dl.meta dt:first-of-type, dl.meta dd:first-of-type { border-top: 0; }

ul.notes { margin: var(--s2) 0 var(--s4); padding-left: 22px; color: var(--dim); max-width: 104ch; }
ul.notes li { margin-bottom: var(--s2); line-height: 1.55; }
footer {
  margin-top: var(--s6); padding-top: var(--s3); border-top: 1px solid var(--line);
  color: var(--dim); font-size: 13px;
}
footer p { max-width: 108ch; }

/* Layar sempit: lebar kolom tetap berhenti membantu ketika tidak ada lebar untuk dibagi. */
@media (max-width: 900px) {
  body { padding: 0 var(--s3) var(--s5); }
  .banner { margin: 0 calc(var(--s3) * -1) var(--s3); }
  table { table-layout: auto; }
  h1 { font-size: 25px; }
  dl.meta { grid-template-columns: 1fr; }
  dl.meta dd { border-top: 0; padding-top: 0; }
}
"""


# --- escaping & pembungkusan --------------------------------------------------------------
def _bdi(value: Any, limit: int = TEXT_LIMIT) -> str:
    """Satu-satunya pintu untuk nilai asal-DB/asal-RYO masuk ke HTML.

    Tiga hal sekaligus, dan sengaja tidak bisa dipakai setengah-setengah: aksara tak tercetak
    di-escape TERLIHAT (`\\u202e`), `<`/`>`/`&`/kutip di-escape HTML, lalu arah teksnya
    diisolasi `<bdi>` supaya satu nilai tidak bisa membalik sel tetangganya.
    """
    if value is None:
        return '<span class="dim">—</span>'
    text = value if isinstance(value, str) else _scalar(value)
    return f"<bdi>{escape(clean_text(text, limit), quote=True)}</bdi>"


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
        return f"{-minutes:.1f} menit DI DEPAN jam render (jamnya bertengkar, bukan kesegaran)"
    if minutes < 90:
        return f"{minutes:.1f} menit sebelum halaman ini dirender"
    if minutes < 60 * 48:
        return f"{minutes / 60:.1f} jam ({minutes:.0f} menit) sebelum halaman ini dirender"
    return f"{minutes / 1440:.1f} hari ({minutes:.0f} menit) sebelum halaman ini dirender"


def _now_jst(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M JST")


# --- kerangka halaman ---------------------------------------------------------------------
def _shell(title: str, banner: str, body: str, now: datetime) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="id"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=1280">'
        '<meta name="tenko-simulated" content="true">'
        f"<title>{escape(title, quote=True)}</title>"
        '<link rel="stylesheet" href="/ui/tenko.css">'
        "</head><body>"
        f'<p class="banner" role="note">{BANNER_ICON}<span>{escape(banner, quote=True)}</span></p>'
        f'<main id="isi">{body}</main>'
        "<footer><p>Semua jam JST. Halaman ini dirender di server, tanpa satu pun skrip: "
        f"dirender {escape(_now_jst(now), quote=True)}. Tenko membaca store BACA-SAJA; "
        "tidak ada tool RYO dan tidak ada LLM yang dipanggil untuk menampilkan layar ini.</p>"
        f'<p class="sub">{escape(KEYBOARD_NOTE, quote=True)}</p>'
        "</footer></body></html>"
    )


def _notes_list(notes: Any) -> str:
    entries = [note for note in notes if isinstance(note, str)] if isinstance(notes, list) else []
    if not entries:
        return ""
    items = "".join(f"<li>{_text(note)}</li>" for note in entries)
    return f'<h2>Catatan layar</h2><ul class="notes">{items}</ul>'


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
    tone_css = tone if isinstance(tone, str) and tone.isidentifier() else "unknown"
    status = entry.get("replay_status") or "TIDAK DIJALANKAN"
    label = entry.get("replay_label") or "tidak dinilai"
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
            f"tidak ada yang GO; yang paling dekat {_bdi(nearest.get('symbol'), KEY_LIMIT)}, "
            f"ditahan {_bdi(nearest.get('n_blocking'))} baris, terberat "
            f"{_bdi(blocker.get('item_id'), KEY_LIMIT)} (daya veto {_bdi(blocker.get('veto_rank'))})"
        )
    else:
        tail = f"paling dekat GO: {_bdi(nearest.get('symbol'), KEY_LIMIT)} — nol baris menahan"
    return (
        f'<p class="answer">n={n} simbol dibaca (satu baris = keputusan TERAKHIR simbol itu); '
        f"{n_blocked} ditahan; {tail}.</p>"
        '<p class="sub">Urut daya veto: penahan terberat (spec §4 peringkat 1) di atas. '
        '"Paling dekat GO" dihitung terbalik dari urutan itu: penahan terberatnya paling ringan, '
        "lalu penahan paling sedikit — bukan skor, bukan ramalan.</p>"
    )


def _watchlist_row(entry: dict[str, Any], now: datetime) -> str:
    blocker = _dict(entry.get("top_blocker"))
    decision_id = entry.get("decision_id")
    href = f"/ui/decision/{int(decision_id)}" if isinstance(decision_id, int) else None
    symbol = _bdi(entry.get("symbol"), KEY_LIMIT)
    open_label = _aria(
        _phrase("buka kartu panggilan", entry.get("symbol"), "buka kartu panggilan simbol ini"),
        _phrase("keputusan", entry.get("decision"), "keputusan tidak disebut"),
        _phrase("kartu nomor", decision_id, "tanpa nomor kartu"),
    )
    link = (
        f'<a href="{escape(href, quote=True)}" aria-label="{open_label}">{symbol}</a>'
        if href
        else f'{symbol} <span class="mark">baris ini tidak punya id yang bisa dibuka</span>'
    )
    age = age_phrase(entry.get("as_of"), now)
    age_html = _text(age) if age else "umur tidak bisa disebut: as_of tidak terbaca"
    if blocker:
        held = (
            f"<strong>{_bdi(blocker.get('item_id'), KEY_LIMIT)}</strong> "
            f"(daya veto {_bdi(blocker.get('veto_rank'))}, status {_bdi(blocker.get('status'), KEY_LIMIT)})"
            f'<span class="mark">{_reason_cell(blocker.get("reason"))}</span>'
            f'<span class="mark">sumber {_bdi(blocker.get("tool"), KEY_LIMIT)}.'
            f"{_bdi(blocker.get('field'), TEXT_LIMIT)} · as_of "
            f"{_bdi(blocker.get('as_of_jst'), KEY_LIMIT)}</span>"
        )
    else:
        held = '<span class="mark">nol baris menahan keputusan ini</span>'
    # Nama baris Layar A menjawab pertanyaan juri untuk SATU simbol tanpa membaca sembilan sel:
    # keputusannya, berapa yang menahan, penahan terberatnya, dan dari mana penahan itu dibaca.
    n_unspoken = _said(entry.get("n_unspoken"))
    unspoken_phrase = (
        f"{n_unspoken} baris tidak disebut"
        if n_unspoken is not None
        else "cacah baris tidak-disebut tidak ada"
    )
    label = _aria(
        _phrase("simbol", entry.get("symbol"), "simbol tidak disebut"),
        _phrase("keputusan", entry.get("decision"), "keputusan tidak disebut"),
        (
            _phrase("ditahan", entry.get("n_blocking"), "cacah penahan tidak disebut")
            + _phrase(" dari", entry.get("n_items"), " dari cacah baris yang tidak disebut")
            + " baris panggilan"
        ),
        (
            "; ".join(
                (
                    _phrase("penahan terberat", blocker.get("item_id"), "penahan terberat tanpa id"),
                    _phrase("daya veto", blocker.get("veto_rank"), "daya veto tidak disebut"),
                    _aria_status(blocker),
                    _aria_source(blocker),
                )
            )
            if blocker
            else "nol baris menahan keputusan ini"
        ),
        unspoken_phrase,
        _phrase("as_of bundle", entry.get("as_of_jst"), "as_of bundle tidak terbaca"),
    )
    return (
        f'<tr aria-label="{label}">'
        f'<th scope="row">{link}</th>'
        f"<td>{_decision_badge(entry.get('decision'))}</td>"
        f'<td class="num">{_bdi(entry.get("veto_rank"))}</td>'
        f"<td>{held}</td>"
        f'<td class="num">{_bdi(entry.get("n_blocking"))} / {_bdi(entry.get("n_items"))}</td>'
        f'<td class="num">{_bdi(entry.get("n_unspoken"))}</td>'
        f'<td class="num">{_bdi(entry.get("n_snapshots"))} snapshot<br>'
        f"{_bdi(entry.get('n_warnings'))} peringatan</td>"
        f"<td>{_replay_badge(entry)}</td>"
        f"<td>{_bdi(entry.get('as_of_jst'), KEY_LIMIT)}"
        f'<span class="mark">{age_html}</span></td>'
        "</tr>"
    )


def render_watchlist(payload: dict[str, Any], banner: str, now: datetime) -> str:
    """Layar A — "simbol mana yang paling dekat GO, dan apa yang menahan yang lain?"."""
    raw = payload.get("symbols")
    entries = [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []
    header = "".join(
        f"<th scope='col'>{escape(name, quote=True)}</th>"
        for name in (
            "simbol",
            "keputusan",
            "daya veto penahan",
            "apa yang menahan (dan dari mana)",
            "penahan / baris",
            "tidak disebut",
            "bukti",
            "replay",
            "as_of bundle",
        )
    )
    rows = "".join(_watchlist_row(entry, now) for entry in entries)
    table = (
        f"<table id='watchlist'><caption class='sub'>n={len(entries)} simbol; dibaca dari "
        f"{_bdi(payload.get('scan_limit'))} baris keputusan TERBARU, bukan seluruh tabel. "
        f"{_bdi(payload.get('n_legacy_hidden'))} baris generasi lama tidak ditampilkan di sini."
        f"</caption><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>"
        if entries
        else ""
    )
    body = (
        "<h1>Tenko 点呼 — panggilan pra-berangkat (simulasi)</h1>"
        '<p class="sub"><a href="/api/watchlist">JSON layar ini, apa adanya</a>'
        + (' · <a href="#watchlist">lompat ke tabel watchlist</a>' if entries else "")
        + "</p>"
        f"{_watchlist_lede(entries)}"
        f"{table}"
        f"{_notes_list(payload.get('notes'))}"
    )
    return _shell("Tenko — watchlist (simulasi)", banner, body, now)


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
    css = f"s-{text}" if text.isidentifier() else "s-unknown"
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
        head = f"nilai: {UNSPOKEN_MARK}"
        if value is None:
            return head
        return f"{head}; yang terbaca di bukti: {clean_text(_scalar(value), ARIA_VALUE_LIMIT)}"
    if value is None:
        return f"nilai: {ARIA_NULL}"
    return f"nilai yang disebut: {clean_text(_scalar(value), ARIA_VALUE_LIMIT)}"


def _aria_source(item: dict[str, Any]) -> str:
    """Klausa SUMBER untuk `aria-label` — bagian "tunjuk": tool.field, snapshot, as_of.

    Kartu ini menjual "nilai ini datang dari SINI, pada JAM INI". Nama baris yang hanya berbunyi
    "liquidity_floor, red" menghapus separuh produk untuk pemakai pembaca layar, jadi tiap baris
    membawa asalnya di namanya sendiri.
    """
    tool, field = _said(item.get("tool"), KEY_LIMIT), _said(item.get("field"), TEXT_LIMIT)
    if tool is None and field is None:
        return ARIA_NO_SOURCE
    where = f"{tool or 'tool tidak disebut'}.{field or 'field tidak disebut'}"
    parts = [f"sumber {where}"]
    # Klausa snapshot hanya bila field-nya MEMANG bagian kontrak baris ini: `top_blocker`
    # Layar A tidak membawa `snapshot_id` sama sekali, dan "snapshot tidak disebut" di sana akan
    # menuduh buktinya tak bernomor padahal yang tidak lengkap adalah ringkasan barisnya.
    if "snapshot_id" in item:
        parts.append(_phrase("snapshot", item.get("snapshot_id"), "snapshot tidak disebut"))
    parts.append(_phrase("as_of", item.get("as_of_jst"), "as_of tidak disebut"))
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
    if source is None:
        return ""
    text = source if isinstance(source, str) else _scalar(source)
    cleaned = clean_text(text, limit)
    if cleaned != text and cleaned.endswith(TRUNCATED_MARK):
        return _mark(SCREEN_CUT_NOTE.format(limit=limit))
    if TRUNCATED_MARK not in text:
        return ""
    if text.endswith(TRUNCATED_MARK):
        for ceiling in ledger_limits:
            if len(text) == ceiling + len(TRUNCATED_MARK):
                return _mark(ledger_cut_note(ceiling))
    return _mark(FOREIGN_MARK_NOTE)


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
    return _bdi(value, REASON_LIMIT) + _cut_marks(value, REASON_LIMIT, ledger_limits=REASON_CEILINGS)


def _item_row(item: dict[str, Any]) -> str:
    narration = item.get("narration")
    narration_html = f'<span class="narr-label">{escape(NARRATION_LABEL, quote=True)}</span>' + (
        f'<span class="narr">{_text(narration)}</span>'
        if isinstance(narration, str) and narration.strip()
        else f'<span class="narr">{escape(NARRATION_EMPTY, quote=True)}</span>'
    )
    reason = _reason_cell(item.get("reason"))
    value = _bdi(item.get("value"), VALUE_LIMIT) + _cut_marks(item.get("value"), VALUE_LIMIT)
    source = _aria_source(item)
    # Nama BARIS, bukan nama sel: pembaca layar mengumumkannya sekali saat pindah baris, dan
    # kalimat itulah seluruh janji produk untuk baris ini — status, nilai yang disebut, dan dari
    # tool/snapshot/jam mana ia datang. Isi selnya tidak diubah sedikit pun; yang ditambah hanya
    # namanya, sehingga layar tetap terbaca sama oleh mata dan oleh telinga.
    label = _aria(
        _phrase("baris", item.get("item_id"), "baris tanpa item_id"),
        _phrase("daya veto", item.get("veto_rank"), "daya veto tidak disebut"),
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
            '<p class="answer">Baris generasi lama: item dan penahannya TIDAK dirender (lihat catatan).</p>'
        )
    if not blocking:
        return (
            f'<p class="answer">Nol baris menahan keputusan ini; {_bdi(n_items)} baris panggilan '
            f"disebut, {_bdi(payload.get('n_unspoken'))} tidak disebut.</p>"
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
        f"{others}, dan {_bdi(n_hidden)} lagi yang tidak dirender (`n_blocking` = cacah sebenarnya)."
        if n_hidden
        else (f"{others}." if len(blocking) > 1 else "tidak ada.")
    )
    return (
        f'<p class="answer">Ditahan {_bdi(n_blocking)} dari {_bdi(n_items)} baris panggilan. '
        f"Penahan TERBERAT: <strong>{_bdi(blocking[0], KEY_LIMIT)}</strong> "
        f"(daya veto {_bdi(top.get('veto_rank'))}) — {_reason_cell(top.get('reason'))} "
        f"Sumber: {_bdi(top.get('tool'), KEY_LIMIT)}.{_bdi(top.get('field'), TEXT_LIMIT)}, "
        f"as_of {_bdi(top.get('as_of_jst'), KEY_LIMIT)}.</p>"
        f'<p class="sub">Penahan lain, urut daya veto: {tail}</p>'
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
        return '<span class="mark">baris ini tidak punya id yang bisa diputar ulang</span>'
    label = _aria(
        _phrase("putar ulang keputusan nomor", payload.get("decision_id"), "putar ulang kartu ini"),
        "memuat ulang kartu ini dan menghitung ulang vonis dari snapshot beku yang sama",
    )
    return (
        f'<a class="badge" href="{escape(href, quote=True)}" aria-label="{label}">'
        "putar ulang sekarang &#8635;</a>"
    )


def _card_meta(payload: dict[str, Any], now: datetime) -> str:
    age = age_phrase(payload.get("as_of"), now)
    age_html = (
        f"{_bdi(payload.get('as_of_jst'), KEY_LIMIT)} — {_text(age)}"
        if age
        else '<span class="empty">umur tidak bisa disebut: as_of bundle tidak terbaca</span>'
    )
    rows = [
        (
            "as_of bundle",
            f'{age_html}<span class="mark">{escape(AGE_NOTE, quote=True)}</span>'
            f'<span class="mark">as_of mentah: {_bdi(payload.get("as_of"), KEY_LIMIT)}</span>',
        ),
        (
            "dibukukan (ts_utc)",
            f'{_bdi(payload.get("ts_jst"), KEY_LIMIT)}<span class="mark">'
            f"{_bdi(payload.get('ts_utc'), KEY_LIMIT)}"
            " — jam penulisan baris ledger, TIDAK dijaga hash apa pun</span>",
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
            "putar ulang",
            f"{_replay_link(payload)}"
            f'<span class="mark">Tombol itu memuat ulang kartu ini, dan vonis di atas dihitung '
            f"ULANG di server dari {_bdi(payload.get('n_snapshots'))} snapshot beku yang sama "
            "setiap kali halaman ini dirender — jadi ia benar-benar memutar ulang, bukan "
            "menampilkan hasil yang disimpan. Di luar layar: "
            f"<code>{_bdi(payload.get('replay_command'), KEY_LIMIT)}</code>, yang membaca "
            "snapshot yang sama dan mencetak vonis yang sama.</span>",
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
        f"n_lines={_bdi(block.get('n_lines'))} dari n_items={_bdi(block.get('n_items'))}, "
        f"model {_bdi(block.get('model'), KEY_LIMIT)}.</p>"
        f'<p class="sub">{_text(block.get("reason"))}</p>'
        f'<p class="sub">{escape(NARRATION_NOTE, quote=True)}</p>'
    )
    return f"<h2>Narasi model</h2>{line}"


def _list_block(title: str, entries: Any, empty: str) -> str:
    rows = [entry for entry in entries if isinstance(entry, str)] if isinstance(entries, list) else []
    if not rows:
        return f'<h2>{escape(title, quote=True)}</h2><p class="sub empty">{escape(empty, quote=True)}</p>'
    body = "".join(f"<li>{_text(entry)}</li>" for entry in rows)
    return f'<h2>{escape(title, quote=True)}</h2><ul class="notes">{body}</ul>'


def _bundle_table(payload: dict[str, Any], now: datetime) -> str:
    raw = payload.get("replay_bundle")
    rows = [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []
    if not rows:
        return f'<h2>Bukti beku</h2><p class="sub empty">{escape(EMPTY_SENTENCE, quote=True)}</p>'
    body = ""
    for entry in rows:
        age = age_phrase(entry.get("as_of"), now)
        body += (
            "<tr>"
            f'<td class="num">{_bdi(entry.get("snapshot_id"))}</td>'
            f"<td>{_bdi(entry.get('tool'), KEY_LIMIT)}</td>"
            f"<td>{_bdi(entry.get('status'), KEY_LIMIT)}</td>"
            f'<td>{_bdi(entry.get("as_of_jst"), KEY_LIMIT)}<span class="mark">'
            f"{_text(age) if age else 'umur tidak bisa disebut'}</span></td>"
            "</tr>"
        )
    return (
        f"<h2>Bukti beku</h2><table><caption class='sub'>n={len(rows)} snapshot dirender dari "
        f"{_bdi(payload.get('n_snapshots'))} yang disebut baris ini.</caption>"
        "<thead><tr><th scope='col'>snapshot</th><th scope='col'>tool</th>"
        "<th scope='col'>status</th><th scope='col'>as_of</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def render_card(payload: dict[str, Any], banner: str, now: datetime) -> str:
    """Layar B — "kenapa keputusan ini, bukti per baris dari mana & kapan?"."""
    raw = payload.get("items")
    items = [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []
    header = "".join(f"<th scope='col'>{escape(name, quote=True)}</th>" for name in CARD_COLUMNS)
    rows = "".join(_item_row(item) for item in items)
    caption = (
        f"n={len(items)} baris dirender; kartu ini memuat "
        f"{_bdi(payload.get('n_items_total'))} baris (`n_items_total`), "
        f"{_bdi(payload.get('n_unspoken'))} di antaranya tidak disebut."
    )
    table = (
        f"<table class='card'><caption class='sub'>{caption}</caption><thead><tr>{header}</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        if items
        else f'<p class="sub empty">{escape(EMPTY_SENTENCE, quote=True)} — kartu ini tidak '
        "memuat satu baris panggilan pun.</p>"
    )
    # `n_warnings_rendered`, BUKAN `n_warnings`: yang di kiri kalimat ini adalah "berapa yang
    # muat di layar", dan `n_warnings` sekarang berarti cacah sebenarnya (TASKS 3.1b).
    warnings_caption = (
        f"{_plain(payload.get('n_warnings_rendered'))} dirender dari "
        f"{_plain(payload.get('n_warnings_total'))} (`n_warnings_total`)"
    )
    api_href = _self_href(payload, "/api/decision/")
    json_link = (
        f' · <a href="{escape(api_href, quote=True)}">JSON kartu ini, apa adanya</a>' if api_href else ""
    )
    warnings_block = _list_block(
        f"Peringatan envelope ({warnings_caption})", payload.get("warnings"), "nol peringatan (n=0)"
    )
    body = (
        f'<p class="sub"><a href="/ui">← kembali ke watchlist</a>{json_link}'
        ' · <a href="#panggilan">lompat ke sepuluh baris tunjuk→sebut</a></p>'
        f"<h1>{_bdi(payload.get('symbol'), KEY_LIMIT)} — {_decision_badge(payload.get('decision'))} "
        f"<span class='sub'>(keputusan #{_bdi(payload.get('decision_id'))}, "
        f"generasi {_bdi(payload.get('generation'), KEY_LIMIT)})</span></h1>"
        f"{_card_answer(payload, items)}"
        f'<p class="sub">{_text(payload.get("headline"))}</p>'
        # Sepuluh baris tunjuk→sebut mendahului daftar hash/jam: uji 30 detik menuntut isi
        # produk, bukan metadatanya, yang berada tepat di bawah kalimat jawaban. Daftar meta
        # sebelumnya berdiri di antara keduanya — delapan baris, sebagian berparagraf —
        # sehingga tabel yang menjawab "buktinya dari mana" terdorong ke bawah lipatan pada
        # 1280x720. Tidak ada isi yang hilang; yang berubah hanya urutannya, dan bagian meta
        # kini punya `<h2>` sendiri sehingga bisa dituju pembaca layar sebagai bagian.
        f'<h2 id="panggilan">Sepuluh baris tunjuk→sebut</h2>{table}'
        f"<h2>Hash, jam, dan pemutaran ulang</h2>{_card_meta(payload, now)}"
        f"{_narration_block(payload)}"
        f"{warnings_block}"
        f"{_bundle_table(payload, now)}"
        f"{_notes_list(payload.get('notes'))}"
    )
    title = f"Tenko — kartu #{payload.get('decision_id')} (simulasi)"
    return _shell(title, banner, body, now)


def render_error(status_code: int, message: str, banner: str, now: datetime) -> str:
    """Halaman gagal yang tetap mengaku simulasi dan tidak mengarang satu angka pun."""
    body = (
        f'<p class="sub"><a href="/ui">← kembali ke watchlist</a></p>'
        f"<h1>{escape(str(status_code), quote=True)} — layar ini tidak bisa dilayani</h1>"
        f'<p class="answer">{_text(message)}</p>'
        '<p class="sub">Tidak ada angka yang dikarang untuk menutupi ini, dan tidak ada tool '
        "RYO maupun LLM yang dipanggil.</p>"
    )
    return _shell(f"Tenko — {status_code} (simulasi)", banner, body, now)
