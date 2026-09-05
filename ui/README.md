# `ui/` — dua layar juri, dan apa yang membuatnya bisa dipakai semua orang

Tenko (点呼) — **SIMULATED — practice positions on read-only market intelligence. No real funds.**
Tidak ada dana, wallet, atau eksekusi nyata; layar ini hanya membaca store append-only.

Dua layar, dua pertanyaan:

| Layar | Route | Pertanyaan juri | JSON yang sama persis |
|---|---|---|---|
| A. Watchlist | `/ui` | "simbol mana yang paling dekat GO, dan apa yang menahan yang lain?" | `/api/watchlist` |
| B. Kartu panggilan | `/ui/decision/{id}` | "kenapa keputusan ini, bukti per baris dari mana & kapan?" | `/api/decision/{id}` |

Keduanya dirender **di server**, tanpa satu pun `<script>`, tanpa CDN, tanpa npm (ADR-001).
Satu-satunya aset adalah `/ui/tenko.css`, yang lahir dari `ui/screens.py:STYLESHEET`;
`Content-Security-Policy: default-src 'none'; style-src 'self'` melarang sisanya.

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
- [x] **Esc tidak punya apa pun untuk ditutup, dan itu disengaja.** Nol `<dialog>`, `<details>`,
      `role="dialog"`, `autofocus`, dan nol overlay `position: fixed` di CSS — jadi tidak ada
      perangkap fokus dan tidak ada keadaan yang hanya bisa dibatalkan dengan JS (yang dilarang
      CSP di sini). Kalimat itu tercetak di kaki setiap layar, bukan hanya di dokumen ini.
      → `test_escape_has_nothing_to_close_because_no_screen_traps_focus`
- [x] **Fokus terlihat.** `a:focus, a:focus-visible { outline: 3px solid var(--warn) }` —
      `:focus` ikut disebut supaya cincin tidak bergantung dukungan `:focus-visible`.
      Kuning `#ffd479` di atas latar tergelap = **13.55:1** (batas non-teks 3:1).
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
- [x] **Tanpa hover-only.** Nol `:hover` di CSS; tidak ada isi yang muncul karena kursor
      melayang, jadi pemakai keyboard, layar sentuh, dan pembaca layar melihat kalimat yang sama.
      → `test_the_stylesheet_has_no_animation_and_no_hover_only_information`
- [x] **Tanpa animasi**, nol `transition`/`@keyframes` — aman untuk `prefers-reduced-motion`
      dan untuk video demo.
      → `test_the_stylesheet_has_no_animation_and_no_hover_only_information`
- [x] **Struktur yang bisa dinavigasi pembaca layar.** `<html lang="id">`, satu `<h1>` per
      layar, `<h2>` berurut, `<main id="isi">`, `<table>` ber-`<caption>` dengan
      `<th scope="col">` dan `<th scope="row">`, banner ber-`role="note"`.
- [x] **Tautan lompat tanpa JS.** `/ui` → "lompat ke tabel watchlist" (`#watchlist`);
      `/ui/decision/{id}` → "lompat ke sepuluh baris tunjuk→sebut" (`#panggilan`). Keduanya
      SELALU terlihat — tidak ada tautan yang hanya muncul saat fokus, karena layar ini tidak
      menyembunyikan apa pun dari siapa pun.
      → `test_every_skip_link_points_at_an_id_that_exists_on_the_page`
- [x] **Terbaca 1280×720.** `<meta name="viewport" content="width=1280">`, tujuh kolom kartu
      berlebar tetap supaya kolom `reason` tidak terhimpit, teks minimum 13px.
- [ ] **Rekaman 30 detik** — lihat "Batas yang belum tertutup" di bawah.

### Angka kontras (WCAG 2.1, sRGB relative luminance)

Dihitung dari warna yang benar-benar dilayani `/ui/tenko.css`. Batas: **4.5:1** untuk teks
(1.4.3) dan **3:1** untuk garis/cincin fokus (1.4.11).

**Teks (batas 4.5:1)**

| Depan | Belakang | Rasio | |
|---|---|---|---|
| `--ink` #f3f5f9 | `--bg` #0d1016 | **17.45:1** | PASS |
| `--ink` #f3f5f9 | `--panel` #161b24 | **15.82:1** | PASS |
| `--ink` #f3f5f9 | baris genap #11151d | **16.75:1** | PASS |
| `--dim` #c3cad6 | `--bg` #0d1016 | **11.55:1** | PASS |
| `--dim` #c3cad6 | `--panel` #161b24 | **10.47:1** | PASS |
| `--dim` #c3cad6 | baris genap #11151d | **11.09:1** | PASS |
| `--go` #7ff0a0 | `--bg` #0d1016 | **13.49:1** | PASS |
| `--go` #7ff0a0 | `--panel` #161b24 | **12.23:1** | PASS |
| `--go` #7ff0a0 | baris genap #11151d | **12.95:1** | PASS |
| `--nogo` #ff9a90 | `--bg` #0d1016 | **9.32:1** | PASS |
| `--nogo` #ff9a90 | `--panel` #161b24 | **8.45:1** | PASS |
| `--nogo` #ff9a90 | baris genap #11151d | **8.95:1** | PASS |
| `--warn` #ffd479 | `--bg` #0d1016 | **13.55:1** | PASS |
| `--warn` #ffd479 | `--panel` #161b24 | **12.29:1** | PASS |
| `--warn` #ffd479 | baris genap #11151d | **13.01:1** | PASS |
| `--link` #9fd0ff | `--bg` #0d1016 | **11.73:1** | PASS |
| `--link` #9fd0ff | `--panel` #161b24 | **10.64:1** | PASS |
| `--link` #9fd0ff | baris genap #11151d | **11.26:1** | PASS |
| teks banner #ffe8e5 | latar banner #3a1d1d | **13.05:1** | PASS |
| `--ink` #f3f5f9 | latar banner #3a1d1d | **14.01:1** | PASS |

Yang paling ketat: **8.45:1** (`--nogo` di atas `--panel`), yaitu 1,88× batas 4.5:1.

**Non-teks: garis tabel & cincin fokus (batas 3:1)**

| Depan | Belakang | Rasio | |
|---|---|---|---|
| `--line` #646e84 | `--bg` #0d1016 | **3.72:1** | PASS |
| `--line` #646e84 | `--panel` #161b24 | **3.38:1** | PASS |
| `--line` #646e84 | baris genap #11151d | **3.57:1** | PASS |
| `--nogo` #ff9a90 (garis banner) | latar banner #3a1d1d | **7.48:1** | PASS |
| `--warn` #ffd479 (cincin fokus) | `--bg` #0d1016 | **13.55:1** | PASS |
| `--warn` #ffd479 (cincin fokus) | `--panel` #161b24 | **12.29:1** | PASS |
| `--warn` #ffd479 (cincin fokus) | baris genap #11151d | **13.01:1** | PASS |

`--line` sebelumnya `#2c3442` = **1.52:1**: cukup untuk hiasan, tidak cukup untuk garis yang
memisahkan sembilan kolom di layar 1280×720. Ia dinaikkan ke `#646e84` pada TASKS 4.6, dan
angkanya sekarang dipatok tes — bukan mata.

---

## Batas yang belum tertutup (jangan dibaca sebagai selesai)

- **Rekaman 30 detik BELUM DIBUAT.** Ia artefak manusia: tidak ada agen di repo ini yang bisa
  merekam layar, dan tidak ada berkas video di `demo/` untuk task ini. Skripnya ada di bawah,
  tinggal diikuti. **Jangan tandai TASKS 4.6 selesai penuh sebelum rekaman itu ada.**
- **Nol pengujian dengan pembaca layar sungguhan** (NVDA/VoiceOver/Orca). Yang diuji otomatis
  adalah ISI atribut dan struktur HTML-nya, bukan bagaimana satu pembaca layar tertentu
  membacakannya. Khususnya: `aria-label` pada `<tr>` didukung baik oleh pembaca layar arus
  utama, tetapi urutan pengumumannya berbeda antar produk — kami tidak mengklaim sebaliknya.
- **Nol audit kontras otomatis pihak ketiga.** ADR-001 melarang toolchain npm, jadi rasio di
  atas dihitung sendiri dari rumus WCAG 2.1 di dalam tes. Rumusnya standar; alatnya bukan alat
  bersertifikat.
- **`aria-label` baris kartu dipotong pada 600 aksara** (`ARIA_LABEL_LIMIT`) dan nilainya pada
  200 (`ARIA_VALUE_LIMIT`). Bila menggigit, penanda `...(truncated)` ikut TERDENGAR, dan bentuk
  penuhnya tetap ada di sel yang sama. Baris terpanjang pada data hari ini: 412 aksara.
- **Belum diuji dengan zoom 200%** maupun `prefers-contrast`/`forced-colors`.

---

## Skrip rekaman 30 detik (untuk manusia yang memegang perekam)

Prasyarat: `make ui` berjalan, jendela browser **1280×720**, zoom 100%, kursor mouse
**tidak disentuh sama sekali** sepanjang rekaman — itulah yang membuktikan "tanpa hover-only".
Mulai dari `http://127.0.0.1:8000/ui`. Total 30 detik.

| Detik | Tombol | Yang harus terlihat | Poin checklist yang dibuktikan |
|---|---|---|---|
| 0–3 | (diam di `/ui`) | Banner SIMULATED merah di baris paling atas; kalimat pertama menjawab simbol mana yang paling dekat GO | banner, uji 30 detik |
| 3–6 | `Tab` ×2 | Cincin kuning tebal berpindah: "JSON layar ini" → "lompat ke tabel watchlist" | fokus terlihat, urutan dokumen |
| 6–8 | `Enter` | Halaman melompat ke tabel; tabel bergaris kuning (`:target`) | tautan lompat, `:target` |
| 8–12 | `Tab` ×3 | Fokus berjalan menuruni kolom simbol, satu baris satu perhentian, tanpa satu pun klik | Tab menelusuri seluruh layar |
| 12–14 | `Esc` | **Tidak terjadi apa-apa, dan itulah buktinya** — tidak ada modal/overlay yang bisa menjebak fokus; baca kalimat keyboard di kaki halaman | Esc, tanpa perangkap fokus |
| 14–16 | `Enter` | Layar B terbuka: judul sudah menyebut simbol + GO/NO_GO + penahan terberat | uji 30 detik Layar B |
| 16–20 | `Shift+Tab` lalu `Tab` | Cincin fokus bolak-balik antara "kembali ke watchlist", "JSON kartu ini", dan "lompat ke sepuluh baris" — kedua arah bekerja | Shift+Tab |
| 20–24 | `Enter` di "lompat ke sepuluh baris" | Tabel sepuluh baris tunjuk→sebut, tiap baris membawa nilai, `tool.field`, `snapshot`, `as_of` di kolom yang terlihat | nilai + sumber |
| 24–27 | (diam; sorot baris `unspoken` bila ada) | Baris `unspoken` bertuliskan "tidak disebut — bukti tidak cukup, dan itu bukan nol"; hijau-netral bertuliskan "tidak menentang, bukan konfirmasi" | unspoken ≠ 0, warna tidak sendirian |
| 27–30 | `Tab` ke "putar ulang sekarang", `Enter` | Kartu dimuat ulang dan vonis replay dihitung ULANG di server dari snapshot beku yang sama | Enter, replay keyboard-first |

Opsional (di luar 30 detik, kalau ada waktu rekaman kedua): nyalakan pembaca layar, arahkan ke
satu baris kartu, dan biarkan ia membacakan `aria-label`-nya — satu kalimat itu memuat nilai
DAN sumbernya. Rekaman itu, bila dibuat, akan menutup butir "nol pengujian dengan pembaca layar
sungguhan" di atas; sampai saat itu, butir tersebut tetap terbuka.
