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
yang tidak menyegarkan tidak boleh mengatakan ia menyegarkan.
→ `test_every_screen_promises_only_the_rhythm_that_screen_actually_has`

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

## Penyegaran tampilan (5 Sep 2026) — apa yang berubah, dan apa yang sengaja tidak

Yang dinilai di layar ini adalah **keterbacaan dalam 30 detik**, bukan kemewahan. Yang berubah:

- **Tipografi punya dua peran.** Prosa memakai font sans **sistem** (`system-ui`, dst.), angka /
  hash / id / nilai asal-RYO tetap monospace supaya kolomnya sejajar. Nol webfont diunduh.
- **Satu tangga spasi** (`--s1`…`--s6`) dan dua radius (`--r1`, `--r2`) menggantikan jarak yang
  dulu dikarang per aturan; halaman dibatasi 1240px dan dipusatkan.
- **Kalimat jawaban dinaikkan menjadi kartu** (`.answer`): panel + garis, elevasi lewat
  border/background — bukan bayangan tebal, bukan gradien.
- **Layar B disusun ulang**: sepuluh baris tunjuk→sebut kini tepat di bawah kalimat jawaban, dan
  daftar hash/jam/replay pindah ke bawahnya dengan `<h2>` sendiri. Tidak ada isi yang dihapus.
  → `test_screen_b_puts_the_ten_rows_above_the_hashes_not_below_them`
- **Judul kolom kartu diperbaiki**: `CARD_COLUMNS` dulu berisi tujuh judul untuk sembilan sel,
  sehingga `<th scope="col">` menunjuk kolom yang salah — cacat tampilan sekaligus cacat pembaca
  layar. → `test_the_card_table_has_one_header_for_every_column_it_renders`
- **Lebar kolom Layar A ditetapkan**: kolom "apa yang menahan" (jawabannya) mendapat 30%, bukan
  seperembilan lebar yang sama rata seperti sebelumnya.
- **Banner SIMULATED melekat** (`position: sticky`, **bukan** `fixed`): ia ada di setiap frame
  video, tetapi tetap mengalir di dalam dokumen — bukan overlay, tidak menutupi apa pun, tidak
  bisa menjebak fokus. Sasaran tautan lompat diberi `scroll-margin-top` supaya tidak mendarat di
  baliknya. → `test_the_banner_stays_in_frame_without_becoming_an_overlay`

Yang **tidak** berubah, dan dijaga tes:

- **Nol dari dua belas warna digeser.** Rasio di tabel bawah karena itu masih rasio yang sama;
  tidak ada yang perlu dihitung ulang. `--line` khususnya tetap `#646e84` (3.72:1) dan tidak
  dikembalikan ke `#2c3442` (1.52:1, GAGAL 1.4.11) demi rasa.
  → `test_the_restyle_did_not_move_a_single_measured_colour`
- Nol animasi, nol `transition`, nol `:hover`, nol `position: fixed`. (Sejak ADR-020 ada TEPAT SATU
  `<script src="/ui/app.js" defer>`, tanpa satu aksara pun isi sebaris; yang dijaga tes sekarang
  adalah jumlahnya = 1, bukan 0 — lihat baris CSP di atas.)
- Warna tetap tidak pernah sendirian; `unspoken` tetap terbaca "tidak disebut", bukan 0.

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
- [x] **Landing (`/`) diuji dengan tuntutan yang sama seperti Layar A/B**: satu `h1` lalu `h2`
      saja tanpa lompatan tingkat, tiap tautan bernama, aksara Jepang ber-`lang="ja"`, rel gulir
      `aria-hidden` karena ia kanal kedua (posisi gulir sudah dibawa bilah gulir browser), dan
      banner SIMULATED dirender server.
      → `test_the_landing_is_reachable_and_readable_the_way_screen_a_and_b_are`

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
      SELALU terlihat — tidak ada tautan yang hanya muncul saat fokus, karena layar ini tidak
      menyembunyikan apa pun dari siapa pun.
      → `test_every_skip_link_points_at_an_id_that_exists_on_the_page`
- [x] **Terbaca 1280×720.** `<meta name="viewport" content="width=1280">`, badan halaman
      dibatasi 1240px, **sembilan** kolom kartu dan sembilan kolom watchlist berlebar tetap
      supaya kolom `reason` (Layar B) dan kolom "apa yang menahan" (Layar A) tidak terhimpit,
      teks minimum 13px. Di bawah 900px lebar tetap itu dilepas kembali (`table-layout: auto`).
- [ ] **Rekaman 30 detik** — lihat "Batas yang belum tertutup" di bawah.

### Angka kontras (WCAG 2.1, sRGB relative luminance)

Dihitung dari warna yang benar-benar dilayani `/ui/tenko.css`. Batas: **4.5:1** untuk teks
(1.4.3) dan **3:1** untuk garis/cincin fokus (1.4.11).

**Teks (batas 4.5:1)**

| Depan | Belakang | Rasio | |
|---|---|---|---|
| `--ink` #0b1a2e | `--bg` #eef2f7 | **15.55:1** | PASS |
| `--ink` #0b1a2e | `--panel` #ffffff | **17.48:1** | PASS |
| `--ink` #0b1a2e | baris genap #e4eaf2 | **14.44:1** | PASS |
| `--dim` #586985 | `--bg` #eef2f7 | **4.95:1** | PASS |
| `--dim` #586985 | `--panel` #ffffff | **5.56:1** | PASS |
| `--dim` #586985 | baris genap #e4eaf2 | **4.60:1** | PASS |
| `--go` #55721c (Yamanote, digelapkan) | `--bg` #eef2f7 | **4.90:1** | PASS |
| `--go` #55721c (Yamanote, digelapkan) | `--panel` #ffffff | **5.51:1** | PASS |
| `--go` #55721c (Yamanote, digelapkan) | baris genap #e4eaf2 | **4.55:1** | PASS |
| `--nogo` #d20911 (Marunouchi, digelapkan) | `--bg` #eef2f7 | **4.93:1** | PASS |
| `--nogo` #d20911 (Marunouchi, digelapkan) | `--panel` #ffffff | **5.54:1** | PASS |
| `--nogo` #d20911 (Marunouchi, digelapkan) | baris genap #e4eaf2 | **4.58:1** | PASS |
| `--warn` #995900 (Ginza, digelapkan) | `--bg` #eef2f7 | **4.94:1** | PASS |
| `--warn` #995900 (Ginza, digelapkan) | `--panel` #ffffff | **5.55:1** | PASS |
| `--warn` #995900 (Ginza, digelapkan) | baris genap #e4eaf2 | **4.59:1** | PASS |
| `--link` #007197 (Keihin-Tohoku, digelapkan) | `--bg` #eef2f7 | **4.90:1** | PASS |
| `--link` #007197 (Keihin-Tohoku, digelapkan) | `--panel` #ffffff | **5.51:1** | PASS |
| `--link` #007197 (Keihin-Tohoku, digelapkan) | baris genap #e4eaf2 | **4.56:1** | PASS |
| teks banner #8c0b10 | latar banner #fdeaea | **8.35:1** | PASS |
| `--ink` #0b1a2e | latar banner #fdeaea | **15.09:1** | PASS |

Yang paling ketat: **4.55:1** (`--go` Yamanote di atas baris genap), yaitu 1.01x batas 4.5:1. Untuk teks GELAP kasus terburuk adalah latar TERGELAP, kebalikan dari tema gelap.

| Pasangan non-teks | Latar | Rasio | Batas 3:1 |
|---|---|---|---|
| `--line` #6c87a9 | `--bg` #eef2f7 | **3.29:1** | PASS |
| `--line` #6c87a9 | `--panel` #ffffff | **3.70:1** | PASS |
| `--line` #6c87a9 | baris genap #e4eaf2 | **3.06:1** | PASS |
| `--nogo` #d20911 (garis banner) | latar banner #fdeaea | **4.79:1** | PASS |
| `--warn` #995900 (cincin fokus) | `--bg` #eef2f7 | **4.94:1** | PASS |
| `--warn` #995900 (cincin fokus) | `--panel` #ffffff | **5.55:1** | PASS |
| `--warn` #995900 (cincin fokus) | baris genap #e4eaf2 | **4.59:1** | PASS |

`--line` sebelumnya `#2c3442` = **1.52:1**: cukup untuk hiasan, tidak cukup untuk garis yang
memisahkan sembilan kolom di layar 1280×720. Ia dinaikkan ke `#646e84` pada TASKS 4.6, dan
angkanya sekarang dipatok tes — bukan mata.

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
