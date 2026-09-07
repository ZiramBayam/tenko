# `ui/` — dua layar juri, dan apa yang membuatnya bisa dipakai semua orang

Tenko (点呼) — **SIMULATED — practice positions on read-only market intelligence. No real funds.**
Tidak ada dana, wallet, atau eksekusi nyata; layar ini hanya membaca store append-only.

Dua layar, dua pertanyaan:

| Layar | Route | Pertanyaan juri | JSON yang sama persis |
|---|---|---|---|
| A. Watchlist | `/ui` | "simbol mana yang paling dekat GO, dan apa yang menahan yang lain?" | `/api/watchlist` |
| B. Kartu panggilan | `/ui/decision/{id}` | "kenapa keputusan ini, bukti per baris dari mana & kapan?" | `/api/decision/{id}` |

`/` **menyajikan Layar A** (200 HTML, bukan pengalihan — TASKS 3.5); `/ui` tetap dilayani supaya
tautan lama tidak mati. Daftar route pindah ke `/api/routes`, dan tautannya tercetak di kaki
setiap layar.

Keduanya dirender **di server** — banner, tabel, dan tiap `aria-label` ada sebelum satu baris JS
pun jalan (ADR-020 butir 6) — lalu **klien TypeScript menambah perilaku di atasnya**, dan tidak
sama banyak di tiap layar:

| Layar | Yang ditambahkan klien | Yang TIDAK ada di sini |
|---|---|---|
| A. Watchlist | pindah layar tanpa muat ulang, saring, urutkan, segarkan tiap 30 detik (dengan sakelar mati) | — |
| B. Kartu | pindah layar tanpa muat ulang, ambil ulang kartu ini **sekali** saat tiba | tanpa auto-refresh, tanpa saring/urut/sakelar |
| Halaman gagal | tidak ada: klien berhenti sebelum permintaan pertamanya | tanpa fetch, tanpa refresh, tanpa saring/urut |

Kalimat di kaki halaman menyesuaikan diri dengan layarnya (`ui.screens.client_note`) — halaman
yang tidak menyegarkan tidak boleh mengatakan ia menyegarkan. Landing tidak mencetaknya sama
sekali sejak ADR-030 (permintaan pemilik repo: kalimat itu "di dalam app saja"), dan klien pun
tidak memasang baris status `#tenko-live` di sana: tidak ada isi yang pernah berubah di landing,
dan kalimat inert-nya berbunyi "on a failure page…", yaitu kalimat layar yang lain. Jumlahnya
dipatok per layar — tiga kalimat pengakuan kaki di `/`, empat di ketiga layar lain.
→ `test_every_screen_promises_only_the_rhythm_that_screen_actually_has`,
`test_the_client_note_lives_inside_the_app_not_on_the_landing`

Umur yang tercetak di baris status adalah jam pengambilan **sukses terakhir**, bukan jam saat
layar dilukis: menyaring dan mengurutkan tidak mengambil apa pun, jadi keduanya tidak boleh
mengganti pengakuan "yang di layar ini tua" dengan jam sekarang.
→ `test_a_keystroke_in_the_filter_box_cannot_reset_the_age_of_what_is_on_screen`

Nol framework, nol CDN, nol dependency runtime npm. Dua aset, keduanya dari origin sendiri dan keduanya hasil build yang
**dilacak git**: `/ui/tenko.css` (`ui/static/app.css`) dan `/ui/app.js` (`ui/static/app.js`).
Satu `Content-Security-Policy` menutup sisanya; **nilainya tidak dikutip di sini** — ia hidup di
satu tempat, `ui/app.py:HTML_HEADERS`, karena tiga berkas yang harus diperbarui serempak adalah
tiga berkas yang akan menyimpang. Sifatnya: semua yang tidak disebut ditolak (`default-src
'none'`), skrip/gaya/`fetch` hanya dari origin ini sendiri, dan — sejak ADR-022 — gambar,
font serta media juga HANYA dari origin ini sendiri (`'self'`, tidak pernah `*` dan — di
layar yang dinilai — tidak pernah `data:`), tanpa `unsafe-inline`,
tanpa `unsafe-eval`, tanpa satu pun host pihak ketiga.
Satu pengecualian, dan hanya satu (ADR-026 butir 11): respons `/kage` memakai
`ui/app.py:KAGE_HTML_HEADERS`, yaitu `HTML_HEADERS` dengan `img-src` ditambahi `data:` — karena
lapisan grain halaman itu dilukis dari `canvas.toDataURL()` dan diblokir diam-diam tanpanya.
`/`, Layar A & B, seluruh `/api/…`, dan seluruh route aset TIDAK ikut; tidak ada direktif lain
yang berubah dan tidak ada `unsafe-*` yang masuk.
→ `test_the_kage_csp_is_the_ordinary_one_plus_data_on_img_src_and_nothing_else`,
`test_no_judged_screen_and_no_api_response_allows_a_data_url_in_any_directive` Direktif `fetch` disebut EKSPLISIT: ia
fallback ke `default-src`, jadi tanpanya setiap `fetch()` klien ditolak browser — termasuk ke
origin sendiri, yang membuat seluruh ADR-020 tidak jalan.
Satu-satunya ikon adalah SVG **tertanam di HTML** (`ui/screens.py:BANNER_ICON`) — nol berkas,
nol permintaan jaringan, jadi demo spec §7 tetap jalan tanpa jaringan dan tidak ada domain baru
yang butuh baris `DISCLOSURE.md`.
→ `test_the_only_icon_on_the_screens_is_embedded_and_says_nothing_by_itself`,
`test_the_stylesheet_loads_nothing_from_anywhere_including_itself`,
`test_the_only_script_is_this_apps_own_bundle_and_the_csp_allows_nothing_else`

---

## Build (ADR-020): Node adalah alat BUILD, dan Tenko tidak pernah membutuhkannya untuk jalan

`make ui`, `make test`, dan `make demo` **tidak menyentuh Node sama sekali**. Hasil build
(`ui/static/app.js`, `ui/static/app.css`) dilacak git dan ikut terkirim, jadi clone bersih di
mesin tanpa Node tetap menjalankan Tenko utuh.
→ `test_a_clone_without_node_can_run_tenko_because_the_build_output_is_tracked`,
`test_nothing_on_the_runtime_path_reaches_for_node_or_npm`

Yang **butuh** Node hanyalah `make frontend`, dan hanya kalau kamu mengubah `frontend/`:

```
make frontend      # npm ci && tsc --noEmit && esbuild ... && git add ui/static/
```

Versi yang dipin (`docs/versions.md` adalah sumbernya, bukan berkas ini): node **v24.15.0**,
npm **11.12.1**, `typescript` **7.0.2** (Apache-2.0), `esbuild` **0.28.2** (MIT). Dependency
runtime npm: **nol** (`package.json` `dependencies` kosong, dipatok
`test_npm_has_zero_runtime_dependencies`).

Dua hal yang perlu dikatakan apa adanya tentang build itu:

- **`typescript` 7.0.2 bukan kompiler JS murni.** Ia menarik binary native per-platform
  (`@typescript/typescript-linux-x64` di mesin ini) dan mem-vendor `vscode-jsonrpc` 9.0.0.
  Build di arsitektur lain menarik paket yang berbeda; `package-lock.json` mendaftarkan
  ke-20 varian platformnya (semua Apache-2.0), dan `esbuild` menambah 26 varian (semua MIT).
  Yang benar-benar terpasang di satu mesin hanyalah empat paket.
- **Build-nya TIDAK offline, dan kami tidak mengklaim sebaliknya.** `esbuild` punya `postinstall`
  yang bisa mengunduh tarball platform bila paket `@esbuild/<platform>`-nya tidak ada — itulah
  sebabnya targetnya memakai `npm ci` (lockfile yang menentukan), bukan `npm install`. Yang
  dijamin offline adalah **menjalankan** Tenko, bukan membangunnya.

---

## Penyegaran tampilan (ADR-027, 7 Sep 2026) — apa yang berubah, dan apa yang sengaja tidak

Yang dinilai di layar ini adalah **keterbacaan dalam 30 detik**, bukan kemewahan — tetapi
"terbaca" dan "terbaca seperti dokumen Word" bukan hal yang sama, dan sampai ADR-027 layar ini
adalah yang kedua. Sebabnya bisa ditunjuk: tes palet lama melarang SETIAP hex di luar sepuluh
warna jalur JR, jadi nol permukaan antara, nol tint kartu, nol garis rambut — dan tanpa itu
tidak ada satu pun cara membuat kedalaman.

Yang berubah:

- **Satu bahasa visual untuk ketiga layar**, didamaikan dengan `/kage` (utang yang diakui
  ADR-026): `--ink:#05070a` + `--fg:#dfe7e0` — token yang sama dengan `ui/static/kage.css` —
  dengan tiga permukaan di atasnya (`--panel`, `--row`, `--card`), satu garis struktural, dan
  empat warna status yang mempertahankan ARTI palet lama pada nilai yang terbaca di latar gelap.
- **Sistem tata letak, bukan blok yang dipusatkan.** `main` adalah kisi tiga jalur (pinggir ·
  kolom baca 1240px · pinggir); bagian yang ingin penuh-lebar meminta jalur `full`. Nol
  `overflow-x: hidden` dan nol `margin-inline: calc(50% - 50vw)` — keduanya mematikan
  `position: sticky`, dan banner SIMULATED bergantung padanya.
- **Permukaan berlapis.** Tabel menjadi kartu ber-radius dengan **kepala kolom yang melekat**
  (pada 1280×720 nama kolom tetap terbaca saat baris kesepuluh sedang dilihat), baris genap
  punya permukaannya sendiri, dan baris yang ditunjuk ATAU memegang fokus keyboard naik satu
  tingkat lagi — dua jalan masuk, satu keadaan.
- **Skala tipografi.** Muka huruf aplikasi menjadi Onest (di-vendor, ADR-026); IBM Plex Mono
  tetap memegang angka, hash, id, dan tiap nilai asal-RYO supaya kolomnya sejajar dan aksara
  mirip tidak tertukar. Simbol Layar A naik menjadi 19px mono: ia judul barisnya.
- **Gambar masuk ke layar juri** — dua di landing, satu sebagai latar `html` di semua layar,
  semuanya dari daftar TERTUTUP `ui.screens.IMAGE_NAMES` dan dilayani origin sendiri. Hero
  TIDAK punya gambar sejak ADR-028: layar pertama adalah void dengan satu `radial-gradient`,
  dan tiap perhentian warnanya adalah permukaan palet yang sudah diukur.
- **Gerak yang sudah dibayar ADR-025 dibelanjakan**: reveal berurutan pada blok landing dan
  parallax ber-`scrub` pada dua gambar. Keduanya di dalam `motion()`, yang berhenti sebelum
  baris pertamanya bila `prefers-reduced-motion` menyala.

Yang **tidak** berubah, dan dijaga tes:

- **Tiap pasangan warna DIUKUR** dari stylesheet yang benar-benar dilayani, dan sekarang lebih
  banyak dari sebelumnya: termasuk latar TERBURUK yang mungkin muncul di bawah gambar.
  → `test_every_colour_pair_in_the_served_stylesheet_is_measured_not_claimed`,
  `test_the_palette_is_measured_from_the_stylesheet_that_ships`
- **Nol teks di atas piksel gambar.** Tiap gambar landing duduk di kotaknya sendiri yang berisi
  hanya satu `<img>`, dan hero tidak punya gambar sama sekali.
  → `test_no_sentence_on_a_judged_screen_sits_on_top_of_an_image`
- **Nol `position: fixed`.** Gambar latar dipasang sebagai latar `html`, bukan elemen melayang,
  jadi tidak ada permukaan baru yang bisa menjebak fokus.
  → `test_escape_has_nothing_to_close_because_no_screen_traps_focus`
- **Layar A tetap `<table>`** dengan kolom, `scope`, dan `aria-label` baris yang sama persis:
  `render.ts` merender Layar A dengan markup identik dengan server, dan itu yang membuat render
  klien dan render server tidak bisa menyimpang.
- Banner SIMULATED tetap `sticky` (bukan `fixed`), warna tetap tidak pernah sendirian, dan
  `unspoken` tetap terbaca "tidak disebut", bukan 0.

---

## Susunan `/` (ADR-029, 7 Sep 2026) — dipinjam dari `kage.html`, dan apa yang tidak ikut

ADR-028 membuat layar PERTAMA `/` menjadi apa yang dimaksud pemilik repo; yang ada di bawahnya
tidak ikut. Halaman yang susunannya memang bekerja sudah ada di repo ini sejak ADR-026:
`ui/static/kage.html`, karya teman pemilik repo yang dipakai dengan izin. ADR-029 menyusun ulang
`/` mengikuti susunan itu — dan HANYA `/`.

Urutan bagiannya sekarang, dan pertanyaan yang dijawab masing-masing:

| Bagian | `id` | Pertanyaan yang dijawab | Sumber datanya |
|---|---|---|---|
| pita atas | — | "ini simulasi, dan ke mana saya pergi dari sini?" | banner SIMULATED **dilebur** ke baris yang sama (ADR-030) + wordmark + `Screen A` |
| `01` hero | `hero` | "ini apa?" | teks |
| `02` gate | `gate` | "apa yang salah tanpanya?" | teks |
| `03` pathways | `pathways` | "simbol mana yang paling dekat GO?" | **`/api/watchlist`, baris NYATA dari store** |
| `04` lessons | `lessons` | "bagaimana satu panggilan dibuat?" | teks + lima nama berkas yang ada di repo ini |
| `05` eternity | `eternity` | "sekarang ke mana?" | tautan `/app` |
| kaki | — | "apa lagi yang dilayani aplikasi ini?" | tautan (termasuk kolom "On this page", pindahan dari pita atas); **tiga** kalimat pengakuan `_shell` — kalimat klien tinggal di dalam app (ADR-030) |

Empat hal dari halaman itu sengaja **tidak** ikut, dan tiap alasannya keras:

- **`position: fixed`** (nav, kanvas, grain, rail, kursor) — dilarang di seluruh
  `frontend/styles/app.css`, dan larangan itu yang membeli "nol overlay, nol perangkap fokus".
  Pita atas `/` karena itu `sticky`, bukan `fixed` — ia tetap mengalir di dalam dokumen dan
  tidak pernah menjadi overlay — dan tingginya (53px) dihitung ke dalam `--chrome` supaya hero
  tetap persis satu layar.
- **Lapisan `.fg`** (gambar latar depan di tiap bagian) — itu teks di atas piksel gambar, yaitu
  satu-satunya cara membatalkan seluruh pengukuran kontras halaman ini. Kedua gambar landing
  tetap sendirian di dalam kotaknya.
- **Tukar-label EN→JP saat hover** — hover tidak boleh menjadi satu-satunya jalan ke sebuah kata.
- **Scene WebGL-nya.** Ia boleh secara CSP (`script-src 'self'`, dan berkasnya memang dilayani),
  tetapi ia menuntut tiga lapisan `position: fixed`, dan `makeGrain()` melukis dari
  `canvas.toDataURL()` — yang menuntut `img-src data:` pada layar yang merender nilai asal-RYO/DB.
  Pelonggaran itu ada untuk `/kage` saja justru karena halaman itu tidak membaca store (ADR-026
  butir 11), dan ia tidak akan dibuka untuk `/`. Cahaya hero tetap `radial-gradient` CSS murni.

Yang ikut hanya susunan dan bentuk tipografinya: nol `@import`, nol warna, nol aset baru.
Layar A dan B ikut **warnanya**, bukan strukturnya — `test_the_visual_language_never_grows_new_
structure_on_screen_a_and_b` melarang `topnav`/`sec-head`/`headrow`/`band-art`/`foot-grid`/`<img>`
muncul di sana, karena "merapikan Layar A seperti landing" adalah cara paling mudah mendorong
tabel yang dinilai ke bawah lipatan 1280×720.

---

## Kepala `/` (ADR-030, 8 Sep 2026) — satu pita, dan banner tetap ada di dalamnya

Pemilik repo membuka `/` dan menghitung **empat pita mendatar bertumpuk** sebelum matanya sampai
ke isi: banner SIMULATED (52px) → baris status klien `#tenko-live` (hanya muncul ketika JS jalan,
yaitu justru keadaan yang dilihat juri) → kepala halaman (50px) → railbar (39px). Sekitar 250px
dari 720px, di halaman yang seluruh nilainya justru kekosongan yang tersisa.

Sekarang **satu** pita, **53px**: `.headrow` = banner SIMULATED + wordmark + tombol `Screen A`.

| Yang berubah | Yang TIDAK berubah |
|---|---|
| banner berbagi baris dengan kepala halaman | teks `BANNER` utuh apa adanya, `role="note"`, ikon, warna **12.98:1**, `position: sticky` (bukan `fixed`) |
| tautan bagian pindah ke kolom "On this page" di kaki | tiap tautan tetap menunjuk `id` yang ada / route yang 200; `#pathways` tetap hilang bersama papannya |
| railbar dibuang seluruhnya (`.railbar`, `railbar()`) | posisi gulir tetap dibawa bilah gulir browser — kanal yang sudah ada |
| kalimat klien keluar dari `/` | ia tetap dirender kata demi kata di Layar A, Layar B, dan halaman gagal |
| label hero `01 — Roll call`, rata tepi kiri halaman | penomoran bagian `01…05`, judul dua baris, satu cahaya radial, tautan `/app` di dalam layar pertama |
| kalimat "practice trade" jadi `.hero-note` (`--fg`, `text-wrap: balance`) | kata **practice** tetap di layar pertama — dua kali, karena banner juga mengatakannya |

Kenapa banner **dilebur** dan bukan dihapus: kalimat itu kewajiban Official Rules §5
(`docs/rules-compliance.md`), dan `/` adalah layar pertama yang dilihat juri. Yang boleh berubah
hanyalah kotaknya. Ketika lebar habis, yang mengalah adalah kepala halaman: banner `flex: 0 0
auto`, `nav` `flex: 1 1 auto` — pada 1280px banner memakan ~766px dan sisa lebarnya ~120px.
→ `test_one_band_stands_above_the_first_screen_and_it_still_says_simulated`,
`test_the_client_note_lives_inside_the_app_not_on_the_landing`

---

## `/kage` — halaman gambar, dan kenapa ia bukan `/`

Pemilik repo membawa masuk sebuah halaman bergambar dari proyek temannya (ADR-026). Ia dilayani
di **`/kage`**, dan hanya di sana.

**`/` tidak dibajak.** `/` tetap landing ADR-021 dan `/app` tetap Layar A, karena `/kage` tidak
membaca store satu baris pun: setiap angka di halaman itu — "1611", "92 minutes", "05 chapters" —
adalah teks desain, bukan pengukuran. Menaruhnya di depan papan akan menempatkan angka yang bukan
pengukuran persis di tempat yang paling mungkin dibaca sebagai pengukuran, di produk yang menjual
lawannya. Halaman itu **mengatakan** hal itu sendiri di kakinya (`ui.screens.KAGE_NOTE`), dan
`/api/routes` menyebutnya di kunci `scenery` (`reads_store: false`) — bukan di antara `screens`.

Tautannya ada **satu**, di dasar landing, berlabel "Scenery, not evidence". Bukan di hero: tautan
yang tampak setara dengan "Open the board" akan membelah 30 detik pertama seorang juri.

Apa yang dilayani, semuanya dari daftar TERTUTUP yang dibaca sekali saat impor (pola yang sama
dengan `/ui/fonts/{name}`: `name` dari request dicocokkan ke sebuah dict, dan **tidak pernah**
dipakai membangun path filesystem — nama di luar daftar adalah 404, bukan pembacaan disk):

| Route | Isi | Daftar tertutup |
|---|---|---|
| `/kage` | halaman itu sendiri (markup statis `ui/static/kage.html` + banner & jam dari server) | — |
| `/ui/kage.css` | `ui/static/kage.css` — berkas gaya tulisan tangan, **bukan** artefak esbuild | — |
| `/ui/img/{image}` | 14 `.webp` (`image/webp`) | `ui.screens.IMAGE_NAMES` |
| `/ui/vendor/{script}` | `three.min.js` (r149, MIT) & `kage-scene.js` (`text/javascript`), plus
`LICENSE.three.txt` — MIT menuntut notisnya ikut terdistribusi, jadi ia dilayani, bukan hanya dilacak git | `ui.screens.VENDOR_NAMES` |
| `/ui/fonts/{name}` | 4 face IBM Plex + **7 face baru** (Onest 300/400/500/700, NotoJP 400, Wordmark 500/600) | `ui.screens.FONT_NAMES` |

**CSP tidak dilonggarkan seujung direktif pun untuk halaman ini**, dan itulah satu-satunya
syarat yang membentuk markupnya: nol `<style>`, nol `<script>` sebaris, dan nol atribut `style=`
(`style-src 'self'` menolak atribut sama seperti ia menolak tag). Markup yang dibawa masuk
memuat tiga atribut `style=` untuk tiga cahaya kartu; nilainya pindah utuh ke kelas
`.glow--approach/--lanterns/--moonwater` di `ui/static/kage.css`. Kedua `<script>`-nya `defer`
dan `src`-nya origin sendiri; `kage-scene.js` membangkitkan seluruh teksturnya sendiri dan tidak
mengunduh satu berkas pun saat berjalan, jadi janji "nol permintaan jaringan ke host asing" tetap
berdiri (dipatok tes: nol `http://`/`https://` di halaman itu).
→ `test_the_kage_page_is_a_screen_that_still_says_simulated`,
`test_the_kage_page_carries_no_inline_script_no_inline_style_and_no_foreign_host`,
`test_every_kage_asset_route_serves_a_closed_list_and_builds_no_path_from_the_request`,
`test_the_kage_page_never_hijacks_the_screen_that_is_judged`

**Banner SIMULATED**: strip tetap (`position: fixed`) di kaki jendela, dirender server sebelum
satu baris JS pun jalan, dengan kalimat yang sama persis dengan layar lain — dan ia membawa jalan
pulang ke `/app` dan `/`, supaya halaman hiasan tidak pernah menjadi jalan buntu.

**Yang BELUM tertutup di halaman ini** (jangan dibaca sebagai selesai):
lisensi tujuh face baru **belum diverifikasi** terhadap sumber hulu, dan `ui/static/fonts/LICENSE.txt`
menutupi IBM Plex saja — `DISCLOSURE.md` §Fonts menulisnya sebagai kewajiban **OPEN**, dan tes
font di atas gagal bila pengakuan itu dihapus tanpa kewajibannya diselesaikan. Halaman ini juga
belum melewati checklist aksesibilitas di bawah: checklist itu berlaku untuk Layar A & B, yang
tidak berubah satu perilaku pun oleh ADR-026.

---

## Checklist aksesibilitas (TASKS 4.6)

Tiap baris menyebut **tes yang gagal bila janjinya dilanggar**, bukan sekadar klaim.
Semua di `tests/test_ui.py`; jalankan dengan `uv run pytest tests/test_ui.py -q`.

- [x] **Tab / Shift+Tab menelusuri seluruh layar dalam urutan dokumen.** Tiap baris Layar A
      adalah `<a href>` sungguhan, bukan `onclick`; tombol replay juga tautan biasa. Nol
      `tabindex` di seluruh HTML, jadi urutan Tab = urutan baca.
      → `test_the_card_screen_is_reachable_from_the_watchlist_by_keyboard`
- [x] **Enter membuka apa yang sedang berfokus.** Semua interaksi adalah navigasi `GET`; tidak
      ada tombol yang butuh JS untuk bekerja, dan tiap `href` benar-benar dijawab 200.
      → `test_the_card_screen_is_reachable_from_the_watchlist_by_keyboard`
- [x] **Esc tidak punya apa pun untuk ditutup, dan itu tetap disengaja.** Nol `<dialog>`,
      `<details>`, `role="dialog"`, `autofocus`, dan nol overlay `position: fixed` — jadi tidak
      ada perangkap fokus. Sesudah ADR-020, Esc melakukan **satu** hal: mengosongkan kotak
      saringan Layar A dan mengembalikan daftar penuh. Itu bukan "menutup" apa pun; tidak ada
      keadaan yang hanya bisa dibatalkan dengan JS, dan tanpa JS kotak saringan itu memang tidak
      pernah muncul. Kalimatnya tercetak di kaki setiap layar, bukan hanya di dokumen ini.
      → `test_escape_has_nothing_to_close_because_no_screen_traps_focus`

- [x] **Isi yang berubah diumumkan** (`aria-live`). Tabel yang disegarkan klien tanpa muat ulang
      adalah perubahan yang TIDAK terdengar pembaca layar kecuali dikatakan. Klien memasang satu
      wilayah `role="status" aria-live="polite"` (`#tenko-live`) sebelum permintaan pertamanya,
      dan menuliskan ke situ setiap kali ia memuat, gagal, menyaring, atau mengurutkan —
      lengkap dengan `n` yang tampil dan `n` seluruhnya. Sakelar penyegaran membawa
      `aria-pressed`, jadi keadaannya terdengar, bukan hanya terlihat.
      → `test_the_screens_declare_the_polling_interval_they_actually_use`
- [x] **Fokus terlihat.** `a:focus, a:focus-visible { outline: 3px solid var(--warn) }` —
      `:focus` ikut disebut supaya cincin tidak bergantung dukungan `:focus-visible`.
      Oranye Ginza `#995900` di atas `--bg` = **4.94:1** (batas non-teks 3:1).
      Tautan "lompat ke tabel" memakai `:target` dengan cincin yang sama sehingga pemakai
      keyboard melihat di mana ia mendarat.
      → `test_the_stylesheet_has_no_animation_and_no_hover_only_information`
- [x] **`aria-label` memuat nilai + sumber** (ini produknya: tunjuk → sebut). Nama tiap baris
      kartu adalah satu kalimat penuh: `baris 01_freshness; daya veto 1; status green; nilai
      yang disebut: …; sumber market_overview.as_of, snapshot 2166, as_of 2026-09-04 23:42 JST`.
      Baris Layar A serupa: keputusan, berapa yang menahan, penahan terberat, dan dari
      tool/field/jam mana penahan itu dibaca.
      → `test_every_call_row_is_announced_as_a_point_then_speak_sentence`,
      `test_every_watchlist_row_is_announced_with_its_blocker_and_where_it_came_from`
- [x] **`unspoken` terdengar sebagai "tidak disebut", tidak pernah sebagai 0 atau kesunyian**
      (ADR-013). Nilai yang tidak ada di bukti punya kalimatnya sendiri di dalam `aria-label`.
      → `test_an_unspoken_row_is_announced_as_not_spoken_never_as_zero`
- [x] **Warna tidak pernah sendirian.** Tiap lencana membawa teksnya (`GO`/`NO_GO`, status item,
      vonis replay), dan hijau-netral dibedakan dari hijau-konfirmasi oleh KALIMAT
      ("netral — tidak menentang, bukan konfirmasi"), bukan hanya oleh garis putus-putus.
      → `test_green_neutral_is_rendered_differently_from_green_confirmation`
- [x] **Kontras ≥4.5:1 untuk teks, ≥3:1 untuk garis** — dihitung, bukan ditaksir; lihat tabel
      di bawah. Tesnya membaca CSS yang benar-benar dilayani `/ui/tenko.css` dan menolak warna
      baru yang tidak terdaftar di pasangan mana pun.
      → `test_every_colour_pair_in_the_served_stylesheet_is_measured_not_claimed`
- [x] **Tanpa hover-only.** Tidak ada isi yang muncul karena kursor melayang, jadi pemakai
      keyboard, layar sentuh, dan pembaca layar melihat kalimat yang sama. ADR-025 mencabut
      larangan `:hover` itu sendiri; yang menggantikannya lebih tepat sasaran — **tiap aturan
      `:hover` wajib punya padanan `:focus`**, diperiksa per-aturan di CSS yang dilayani.
      → `test_motion_is_allowed_but_never_required_to_read_the_screen`
- [x] **Gerak diizinkan, tetapi tidak pernah dibutuhkan untuk membaca layar** (ADR-025, 7 Sep
      2026 — MENGGANTIKAN butir "tanpa animasi"). Landing memakai smooth scroll (Lenis) dan
      reveal saat gulir (GSAP). Tiga hal yang menjaganya, dan ketiganya diuji, bukan dijanjikan:
      (a) `prefers-reduced-motion` MEMATIKAN gerak seluruhnya — Lenis tidak pernah dibuat, dan
      tesnya memeriksa bahwa pemeriksaan preferensi terjadi **sebelum** `new Lenis`, bukan
      sekadar ada; (b) keadaan awal reveal (`opacity: 0`) hanya hidup di bawah kelas
      `.js-motion` yang ditempel KLIEN, jadi HTML server tidak pernah menyembunyikan isi dan
      halaman tanpa JS terlihat utuh; (c) Layar A dan B tidak diberi gerak sama sekali.
      → `test_motion_is_allowed_but_never_required_to_read_the_screen`
- [x] **Mode paksa-warna tidak menghilangkan judul** (`forced-colors: active`). Judul hero
      memakai `background-clip: text` + `color: transparent`; mode paksa-warna membuang gambar
      latar tetapi TIDAK membuang `color: transparent`, sehingga tanpa penawar judul utama
      halaman **hilang sama sekali**. Cacat itu lahir 7 Sep bersama efeknya dan hanya terlihat
      karena checklist ini diaudit ulang atas UI yang sekarang.
      → `test_the_landing_is_reachable_and_readable_the_way_screen_a_and_b_are`
- [x] **Bentuk layar pertama dipatok, bukan dititipkan ke selera** (ADR-028): label mono
      bernomor `01 —`, judul dua baris berukuran `clamp(56px, 7vw, 104px)`, satu sumber cahaya
      `radial-gradient` yang SELURUH perhentian warnanya adalah permukaan palet terukur (nol
      `rgba()` yang menerangi, nol hex baru), baris kisi kosong yang melar di tengah, dan
      tautan `/app` tetap di dalam layar pertama itu.
      → `test_the_first_screen_is_a_void_with_one_light_and_a_two_line_title`
- [x] **Landing (`/`) diuji dengan tuntutan yang sama seperti Layar A/B**: satu `h1` di depan
      dan tiap tingkat berikutnya paling banyak satu langkah lebih dalam (ADR-029 melonggarkan
      ini dari "h2 saja": lima langkah bagian `04` ADALAH anak dari `h2`-nya, dan merendahkannya
      menjadi paragraf akan menghapus struktur yang benar dari pembaca layar demi memuaskan
      sebuah tes), tiap tautan bernama, aksara Jepang ber-`lang="ja"`, **satu** pita mendatar
      di atas hero (ADR-030; rel gulir dibuang dan tesnya kini melarangnya kembali), dan banner
      SIMULATED dirender server.
      → `test_the_landing_is_reachable_and_readable_the_way_screen_a_and_b_are`
- [x] **Susunan `/` dipaku sebagai URUTAN** (ADR-029), bukan sebagai daftar bagian yang
      kebetulan ada: papan berdiri di TENGAH (bukan di dasar halaman), tiap tautan kepala/kaki
      menunjuk `id` yang ada di halaman itu atau route yang dijawab 200, dan lima langkah
      bagian `04` menunjuk berkas yang benar-benar ada di repo ini.
      → `test_the_landing_is_laid_out_in_the_order_kage_lays_its_page_out`

- [x] **Nilai yang tidak ada adalah KATA, bukan tanda hubung.** Sel kosong dulu berisi em-dash
      abu-abu (`<span class="dim">—</span>`), yang di layar terbaca seperti sebuah nilai.
      Sekarang ia `not stated` dengan warna `--warn` yang sama dengan `unspoken`: ketidaktahuan
      tidak boleh sewarna dengan angka. Klien memakai kata yang sama persis.
      → `test_the_client_and_the_server_speak_one_sentence_not_two`,
      `test_the_client_renders_unspoken_as_a_word_and_computes_no_decision`
- [x] **Struktur yang bisa dinavigasi pembaca layar.** `<html lang="en">`, satu `<h1>` per
      layar, `<h2>` berurut, `<main id="isi">`, `<table>` ber-`<caption>` dengan
      `<th scope="col">` dan `<th scope="row">`, banner ber-`role="note"`.
- [x] **Tautan lompat tanpa JS.** `/ui` → "lompat ke tabel watchlist" (`#watchlist`);
      `/ui/decision/{id}` → "lompat ke sepuluh baris tunjuk→sebut" (`#panggilan`). Keduanya
      SELALU terlihat — tidak ada tautan yang hanya muncul saat fokus, karena kedua layar itu
      tidak menyembunyikan apa pun dari siapa pun. `/` menambah yang KETIGA sejak ADR-029, dan
      ia satu-satunya yang terpotong sampai difokus (`.skip`): ia berdiri di depan pita atas,
      jadi menampilkannya terus-menerus berarti menaruh sebuah tombol di atas banner SIMULATED
      — sementara menyembunyikannya dari MATA tidak menyembunyikannya dari Tab, dan Tab pertama
      itulah gunanya. Saat difokus ia mendarat DI BAWAH pita (`top: 100%`), bukan di atasnya,
      justru karena yang di atasnya adalah kalimat yang harus terlihat di setiap frame. Ia menunjuk `#pathways` ketika papannya ada dan `#hero`
      ketika tidak; tautan yang menunjuk bagian yang tidak dirender adalah tombol yang
      berbohong kepada keyboard.
      → `test_every_skip_link_points_at_an_id_that_exists_on_the_page`,
      `test_the_landing_board_shows_real_rows_or_no_board_at_all`
- [x] **Terbaca 1280×720.** `<meta name="viewport" content="width=1280">`, badan halaman
      dibatasi 1240px, **sembilan** kolom kartu dan sembilan kolom watchlist berlebar tetap
      supaya kolom `reason` (Layar B) dan kolom "apa yang menahan" (Layar A) tidak terhimpit,
      teks minimum 12,5px. Hero ADR-028 setinggi persis satu layar (`100vh - var(--chrome)`,
      dengan `--chrome` = **satu** pita melekat, 52px + garis 1px = **53px**, sejak ADR-030
      meleburkan banner dan kepala halaman menjadi satu baris dan membuang railbar), jadi judul
      di sepertiga bawah berada DI ATAS lipatan pada 720px — bukan di bawahnya. Di bawah 1000px lebar tetap itu
      dilepas kembali (`table-layout: auto`), kolom bukti/rails menumpuk jadi satu, hero
      kembali setinggi isinya (`min-height: 0`), dan gambar hiasan `.proofs-art` menghilang
      sama sekali.
- [ ] **Rekaman 30 detik** — lihat "Batas yang belum tertutup" di bawah.

### Angka kontras (WCAG 2.1, sRGB relative luminance)

Dihitung dari warna yang benar-benar dilayani `/ui/tenko.css`. Batas: **4.5:1** untuk teks
(1.4.3) dan **3:1** untuk garis/cincin fokus (1.4.11). Paletnya berubah di **ADR-027** (palet
jalur JR ADR-023/024 DICABUT; tema gelap berlapis yang didamaikan dengan `/kage`), dan seluruh
pasangan di bawah diukur ULANG — bukan disalin dari tabel lama.

**Teks (batas 4.5:1)**

| Depan | Belakang | Rasio | |
|---|---|---|---|
| `--fg` #dfe7e0 | `--ink` #05070a | **15.99:1** | PASS |
| `--fg` #dfe7e0 | `--panel` #0e131a | **14.77:1** | PASS |
| `--fg` #dfe7e0 | `--row` #141b23 | **13.75:1** | PASS |
| `--fg` #dfe7e0 | `--card` #1a222c | **12.71:1** | PASS |
| `--dim` #9daba4 | `--ink` #05070a | **8.45:1** | PASS |
| `--dim` #9daba4 | `--panel` #0e131a | **7.81:1** | PASS |
| `--dim` #9daba4 | `--row` #141b23 | **7.26:1** | PASS |
| `--dim` #9daba4 | `--card` #1a222c | **6.72:1** | PASS |
| `--go` #7ddc9a | `--ink` #05070a | **12.08:1** | PASS |
| `--go` #7ddc9a | `--panel` #0e131a | **11.16:1** | PASS |
| `--go` #7ddc9a | `--row` #141b23 | **10.39:1** | PASS |
| `--go` #7ddc9a | `--card` #1a222c | **9.61:1** | PASS |
| `--nogo` #ff6f61 | `--ink` #05070a | **7.39:1** | PASS |
| `--nogo` #ff6f61 | `--panel` #0e131a | **6.83:1** | PASS |
| `--nogo` #ff6f61 | `--row` #141b23 | **6.36:1** | PASS |
| `--nogo` #ff6f61 | `--card` #1a222c | **5.88:1** | PASS |
| `--warn` #e0b552 | `--ink` #05070a | **10.47:1** | PASS |
| `--warn` #e0b552 | `--panel` #0e131a | **9.67:1** | PASS |
| `--warn` #e0b552 | `--row` #141b23 | **9.00:1** | PASS |
| `--warn` #e0b552 | `--card` #1a222c | **8.33:1** | PASS |
| `--link` #6fd2e8 | `--ink` #05070a | **11.58:1** | PASS |
| `--link` #6fd2e8 | `--panel` #0e131a | **10.70:1** | PASS |
| `--link` #6fd2e8 | `--row` #141b23 | **9.96:1** | PASS |
| `--link` #6fd2e8 | `--card` #1a222c | **9.21:1** | PASS |
| teks banner #ffd9d4 | latar banner #3a0d0b | **12.98:1** | PASS |

Yang paling ketat: **5.88:1** (`--nogo` di atas `--card`, permukaan paling terang) — 1.31x
batas 4.5:1. Untuk teks TERANG kasus terburuk adalah latar paling TERANG, kebalikan dari palet
terang yang dipakai sebelum ADR-027.

**Teks di atas gambar (batas 4.5:1)**

ADR-027 memasang satu `.webp` sebagai latar `html` di semua layar. Kontrasnya karena itu tidak
lagi ditentukan palet saja, dan yang menjaganya BUKAN "gambarnya kebetulan gelap" melainkan
scrim di atasnya: alpha minimalnya **.88** (dipatok tes). Piksel paling terang yang mungkin ada
di sebuah gambar adalah putih, dan putih di bawah scrim setebal itu menjadi **#232527**:

| Depan | Belakang | Rasio | |
|---|---|---|---|
| `--fg` #dfe7e0 | scrim gambar #232527 | **12.19:1** | PASS |
| `--dim` #9daba4 | scrim gambar #232527 | **6.44:1** | PASS |
| `--go` #7ddc9a | scrim gambar #232527 | **9.21:1** | PASS |
| `--nogo` #ff6f61 | scrim gambar #232527 | **5.64:1** | PASS |
| `--warn` #e0b552 | scrim gambar #232527 | **7.98:1** | PASS |
| `--link` #6fd2e8 | scrim gambar #232527 | **8.83:1** | PASS |

Dua gambar landing (`stone-lantern`, `kage-moonwater`) TIDAK masuk hitungan
ini karena tidak ada satu kalimat pun di atasnya: masing-masing duduk di kotaknya sendiri
(`.proofs-art`, `.band-art`) yang berisi **hanya** satu `<img>` — dipatok
`test_no_sentence_on_a_judged_screen_sits_on_top_of_an_image`. Semuanya `alt=""` (pemandangan,
bukan bukti) dan satu baris `ART_NOTE` mengatakannya untuk mata.

**Non-teks (batas 3:1)**

| Pasangan non-teks | Latar | Rasio | Batas 3:1 |
|---|---|---|---|
| `--line` #5d6f78 | `--ink` #05070a | **3.85:1** | PASS |
| `--line` #5d6f78 | `--panel` #0e131a | **3.56:1** | PASS |
| `--line` #5d6f78 | `--row` #141b23 | **3.31:1** | PASS |
| `--line` #5d6f78 | `--card` #1a222c | **3.06:1** | PASS |
| `--nogo` #ff6f61 (garis banner) | latar banner #3a0d0b | **6.20:1** | PASS |
| `--nogo` #ff6f61 (garis banner) | `--ink` #05070a | **7.39:1** | PASS |
| `--warn` #e0b552 (cincin fokus) | `--ink` #05070a | **10.47:1** | PASS |
| `--warn` #e0b552 (cincin fokus) | `--card` #1a222c | **8.33:1** | PASS |

Satu warna TIDAK punya ambang: `--hair` #232c35, pemisah rambut di dalam kartu. Ia dibayar
dengan larangan yang diperiksa tes — ia tidak boleh muncul sebagai `color:` apa pun, jadi ia
tidak pernah membawa teks dan kehilangannya tidak menghilangkan satu pun informasi.

Dan satu aturan yang menutup lubang terakhir: tiap `rgba()` di stylesheet wajib hitam atau
`--ink`. Transparansi karena itu hanya bisa MENGGELAPKAN, dan karena seluruh teks halaman ini
lebih terang daripada seluruh permukaannya, sebuah lapis transparan hanya bisa menaikkan
kontras — tidak pernah menurunkannya, tanpa satu hex baru pun perlu muncul di berkas.

---

## Kolom "daya veto" adalah klaim, dan kedua layar mengatakannya

Angka 1..10 di samping tiap penahan adalah `preflight.params.ITEM_ORDER` — **urutan spec §4,
sebuah konstanta desain**, bukan hasil ukur. Sampai 6 Sep 2026 tidak ada satu baris pun di layar
yang mengakuinya, jadi kolom itu terbaca sebagai temuan; juri fase 5 memotong nilai di dua
kategori karenanya. Yang membuatnya bukan sekadar "belum diukur" melainkan **dibantah** adalah
alat ukur repo ini sendiri: `preflight/veto_table.py` mencatat `01_freshness` (peringkat 1) nol
kali merah, sementara `06_confluence` (peringkat 6) merah puluhan kali.

Yang dilakukan layar: judul kolomnya berbunyi `veto power (spec §4, not validated)` di **kedua**
layar, dan satu kalimat penuh (`ui.app.VETO_RANK_UNVALIDATED_SENTENCE`) berdiri di Catatan layar
A **dan** B, menyebut asal urutannya, status validasinya, alat yang membantahnya, dan perintah
yang mencetak angkanya.

Yang **tidak** dilakukan: mengubah urutannya. `ITEM_ORDER` milik `preflight/`; layar yang
mengarang urutan sendiri akan memajang peringkat yang bukan peringkat yang dipakai agen saat
memutuskan — kartu yang berhenti menjelaskan keputusannya sendiri.

Layar A juga memuat satu baris tentang `make grade` (`ui.app.GRADE_UNPROVEN_SENTENCE`): veto ini
belum terbukti menyelamatkan apa pun, beberapa justru menahan plan yang kemudian menyentuh
target, dan `n` efektifnya di bawah ambang spec §6 sehingga nol selang kepercayaan dicetak.
Kalimat itu **kualitatif dan menyebut perintahnya**, bukan angka: ADR-003 melarang `ui/`
mengimpor `grading/` (dijaga `test_decision_path_never_imports_grading`), dan menyalin `k`/`n`
sebagai literal hanya akan membekukan laporan yang esok sudah berubah.

---

## Batas yang belum tertutup (jangan dibaca sebagai selesai)

- **Rekaman 30 detik BELUM DIBUAT.** Ia artefak manusia: tidak ada agen di repo ini yang bisa
  merekam layar, dan tidak ada berkas video di `demo/` untuk task ini. Skripnya ada di bawah,
  tinggal diikuti. **Jangan tandai TASKS 4.6 selesai penuh sebelum rekaman itu ada.**
- **Nol pengujian dengan pembaca layar sungguhan** (NVDA/VoiceOver/Orca). Yang diuji otomatis
  adalah ISI atribut dan struktur HTML-nya, bukan bagaimana satu pembaca layar tertentu
  membacakannya. Khususnya: `aria-label` pada `<tr>` didukung baik oleh pembaca layar arus
  utama, tetapi urutan pengumumannya berbeda antar produk — kami tidak mengklaim sebaliknya.
- **Nol audit kontras otomatis pihak ketiga.** ADR-020 mengizinkan npm hanya sebagai alat
  BUILD (esbuild + tsc), bukan sebagai alat audit, jadi rasio di atas tetap dihitung sendiri
  dari rumus WCAG 2.1 di dalam tes. Rumusnya standar; alatnya bukan alat
  bersertifikat.
- ~~**`aria-label` baris kartu dipotong pada 600 aksara**~~ — **ditutup 6 Sep 2026.**
  `ARIA_VALUE_LIMIT` kini **sama dengan** `VALUE_LIMIT` (batas sel yang dilihat mata) dan
  `ARIA_LABEL_LIMIT` dihitung dari situ plus anggaran klausa lainnya, jadi pemakai pembaca layar
  tidak lagi menerima lebih sedikit bukti daripada pemakai awas. Alasan lamanya ("batas
  kesabaran") menyelesaikan kesabaran dengan membuang fakta; di kartu ini tidak ada tempat yang
  bisa dipotong tanpa membuang fakta.
- ~~**Bahasa: bingkai Indonesia, bukti Inggris**~~ — **ditutup 6 Sep 2026 (ADR-020).** Seluruh
  teks layar kini Inggris dan halamannya `<html lang="en">`; permintaan pemilik repo, dan
  memang keputusan produk seperti yang dicatat butir ini dulu. `EVIDENCE_LANG` (`lang="en"`
  per-span) DIPERTAHANKAN meski bingkainya tidak lagi berbeda: maknanya bukan "bahasa lain"
  melainkan "kalimat ini datang dari `preflight/`/RYO, bukan ditulis layar ini".
- **Nol tes mengikat `ui/static/app.js` ke `frontend/src/`.** Mengubah sumber TypeScript tanpa
  menjalankan `make frontend` tetap lolos hijau untuk kode yang tidak menyebut satu pun kalimat
  yang dipatok. Yang menggigit di clone tanpa Node adalah pin `sha256` bundel
  (`tests/test_ui.py:BUNDLE_SHA256`): bundelnya sendiri tidak bisa berubah diam-diam, tetapi
  bundel yang BASI terhadap sumbernya hanya terdeteksi lewat kalimat yang dipatok. Utang yang
  diterima sadar, dicatat di sini sebagai batas yang diketahui.
- **Klien TypeScript tidak dijalankan oleh satu tes pun.** `tests/test_ui.py` adalah tes Python,
  dan jalur runtime repo ini tidak boleh memanggil Node — jadi yang bisa dipatok tentang
  `frontend/` hanyalah SUMBER dan BUNDEL sebagai teks: nol `innerHTML`/`eval`, nol host, nol
  `?? 0` di modul yang merender, dan kalimat yang identik dengan kalimat Python. Yang **tidak**
  terbukti: bahwa DOM yang dirakit klien identik dengan HTML yang dirakit server. Itu risiko
  penyimpangan yang nyata dan diterima sadar; penawarnya adalah render server yang tetap utuh
  (jadi layar yang dinilai juri tanpa JS tetap layar yang sama) dan
  `test_the_client_and_the_server_speak_one_sentence_not_two` untuk kalimat yang paling mahal
  bila menyimpang.
- **Zoom 200%: DIUJI** (7 Sep 2026) dengan merender landing pada viewport 640px — setara 200%
  pada 1280px. Isi mengalir ulang, nol pemotongan, nol gulir mendatar; pil dan label sudut
  membungkus. Yang **belum** diuji pada zoom itu adalah Layar A/B, yang tabelnya jauh lebih
  lebar. `forced-colors` ditangani untuk judul hero (lihat checklist) tetapi **belum diuji
  menyeluruh**; `prefers-contrast` belum disentuh sama sekali.
- **`<title>` memuat `点呼` tanpa `lang="ja"`, dan itu tidak bisa diperbaiki.** HTML tidak
  mengizinkan markup di dalam `<title>`, jadi tidak ada tempat memasang atributnya. Pembaca
  layar berbahasa Inggris akan salah mengejanya. Disebut di sini, bukan disembunyikan; badan
  halaman menandai setiap kemunculan lain dengan benar.
- **Gerak baru (ADR-025) belum diuji dengan pembaca layar sungguhan.** Yang dijamin otomatis
  adalah bahwa gerak mati total di bawah `prefers-reduced-motion` dan bahwa tidak ada isi yang
  disembunyikan server — bukan bagaimana satu pembaca layar tertentu berperilaku saat
  ScrollTrigger memindahkan elemen.

---

## Skrip rekaman 30 detik (untuk manusia yang memegang perekam)

Prasyarat: `make ui` berjalan, jendela browser **1280×720**, zoom 100%, kursor mouse
**tidak disentuh sama sekali** sepanjang rekaman — itulah yang membuktikan "tanpa hover-only".
Mulai dari `http://127.0.0.1:8000/` (yang KINI Layar A). Total 30 detik.

| Detik | Tombol | Yang harus terlihat | Poin checklist yang dibuktikan |
|---|---|---|---|
| 0–3 | (diam di `/`) | Banner SIMULATED merah di baris paling atas; kalimat pertama menjawab simbol mana yang paling dekat GO | banner, uji 30 detik |
| 3–6 | `Tab` ×2 | Cincin kuning tebal berpindah: "the JSON behind this screen" → "skip to the watchlist table" | fokus terlihat, urutan dokumen |
| 6–8 | `Enter` | Halaman melompat ke tabel; tabel bergaris kuning (`:target`) | tautan lompat, `:target` |
| 8–12 | `Tab` ×3 | Fokus berjalan menuruni kolom simbol, satu baris satu perhentian, tanpa satu pun klik | Tab menelusuri seluruh layar |
| 12–14 | `Esc` | **Tidak terjadi apa-apa, dan itulah buktinya** — tidak ada modal/overlay yang bisa menjebak fokus; baca kalimat keyboard di kaki halaman | Esc, tanpa perangkap fokus |
| 14–16 | `Enter` | Layar B terbuka: judul sudah menyebut simbol + GO/NO_GO + penahan terberat | uji 30 detik Layar B |
| 16–20 | `Shift+Tab` lalu `Tab` | Cincin fokus bolak-balik antara "back to the watchlist", "the JSON behind this card", dan "skip to the ten point-then-speak rows" — kedua arah bekerja | Shift+Tab |
| 20–24 | `Enter` di "skip to the ten point-then-speak rows" | Tabel sepuluh baris tunjuk→sebut, tiap baris membawa nilai, `tool.field`, `snapshot`, `as_of` di kolom **yang berjudul benar** — sembilan judul untuk sembilan kolom | nilai + sumber |
| 24–27 | (diam; sorot baris `unspoken` bila ada) | Baris `unspoken` bertuliskan "unspoken — the evidence is not enough, and that is not zero"; hijau-netral bertuliskan "neutral — not against it, and not a confirmation" | unspoken ≠ 0, warna tidak sendirian |
| 27–30 | `Tab` ke "replay now" (tepat di bawah tabel, di bagian "Hashes, clocks, and replay"), `Enter` | Kartu dimuat ulang dan vonis replay dihitung ULANG di server dari snapshot beku yang sama; banner SIMULATED tetap melekat di puncak sepanjang gulir | Enter, replay keyboard-first, banner di setiap frame |

Opsional (di luar 30 detik, kalau ada waktu rekaman kedua): nyalakan pembaca layar, arahkan ke
satu baris kartu, dan biarkan ia membacakan `aria-label`-nya — satu kalimat itu memuat nilai
DAN sumbernya. Rekaman itu, bila dibuat, akan menutup butir "nol pengujian dengan pembaca layar
sungguhan" di atas; sampai saat itu, butir tersebut tetap terbuka.
