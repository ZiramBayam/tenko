# Laporan juri — fase 5 (penilaian ulang) — 6 Sep 2026

**Verdict: ketiga track melewati ambang 80. T1 82 · T2 85 · T3 80.** T3 lewat dengan margin nol, dan
yang menahannya di garis itu adalah README — enam klaim di dalamnya masih salah, kali ini semuanya ke
arah *mengecilkan*. Itu presedennya sendiri (TASKS 0.18: "arahnya tidak dibatasi ke yang menguntungkan kita").

## Langkah §7 (`make demo`, dua run)

Keenam langkah ✔. `diff a b` **kosong**, md5 keduanya `0f8bf5694fdc11fa4c6c0d37c6052e54`. Penjaga:
`0 berkas di data/ dibuka proses ini`. Langkah 1 `GO`, `decision_hash=083d3ed0…`, envelope kanonik
**8656 byte**. Langkah 2 `NO_GO blocking=02_completeness, 04_sentiment_shift`, blok `PENGAKUAN`
mendahului hasil. Langkah 3 `replay ID=1` & `ID=4` IDENTICAL, tamper → MISMATCH ber-`snapshot_id=1`.
Langkah 4 `narration_unavailable`, hash tidak bergeser. Langkah 5 `/ui` 200, `aria-label: 31`,
`handler hover/onclick: 0`. Langkah 6 SOL `GO → NO_GO`.

`make test` → **1848 passed, 1 skipped (383,83 dtk)** · `make lint` bersih, 88 berkas ·
`make doctor` Python 3.12.3 · uv 0.12.7 · sqlite 3.45.1 · `TENKO_GATE_FULL=1 gate.sh` exit 0.

## Tes destruktif — dijalankan sendiri

**a) Sumber mati.** `RYO_MCP_URL=http://127.0.0.1:9` + seluruh env ber-`KEY/TOKEN/SECRET/API` dibuang →
`preflight SOL` exit 0, `status=partial`, **stderr 0 byte, 0 traceback**.

**b) DB dihapus di bawah server hidup.** Tujuh route → semua 503 / 404, **ketujuhnya membawa
`simulated`**, proses hidup, 0 traceback.

**c) Tamper.** Baseline `replay 52` → `IDENTICAL 6bb83acf…`. Setelah `id=3722` diubah → **MISMATCH**
menyebut kedua sha256 (`bec48f02…` tercatat vs `732bc674…` dihitung ulang).

**d) Kredensial dicabut.** `.env` sandbox dipindahkan → exit 2, **0 koneksi baru**. `.env` produksi
utuh (273 byte, md5 `24f93b80…` sebelum = sesudah). Catatan: percobaan menjalankan perekam kedua
ditolak `flock` — *"menjalankan dua perekam berarti dua kali laju panggilan atas satu kunci API"*.
Pagar itu bekerja pada juri.

**e) Chaos penuh.** **LULUS=8 GAGAL=0**, termasuk `recorder_kill9` (SIGKILL mid-siklus, restart,
`integrity ok`, 46 → 69 baris), `rate_limit_429` (menunggu **4,116 s** ≥ 2× `Retry-After`),
`evidence_gap` (424 panggilan, 0 melempar), `bukti_utuh` (3981 → 3981).

**f) Empat varian serangan pengakuan §7 langkah 2**

| varian | apa yang dilakukan | hasil |
|---|---|---|
| V1 | kosongkan daftar, `why: "nothing was fabricated"` | 0 KETIDAKCOCOKAN, **tapi** cacah & nama tetap dihitung — prosa bohong berdiri di sebelah fakta yang membantahnya |
| V2 | kosongkan daftar + bendera + warning | **KETIDAKCOCOKAN 2×** |
| V3 | **hapus blok `synthetic` seluruhnya** (lubang lama) | **KETIDAKCOCOKAN 2×** — *"pemadaman yang TIDAK DIAKUI"*. **LUBANG TERTUTUP** |
| V3b | V3 + buang bendera + warning | **KETIDAKCOCOKAN 2×** |

**Varian keempat yang dicari dan ditemukan.** Bentuk pemadaman lewat jalur yang `is_shaped()` tidak
lihat: `status` tetap `partial`, tanpa warning, tetapi `availability.*` semua `unavailable` dan `data=None`.

- **V4** (blok + bendera dihapus): **tertangkap** oleh pagar kedua — `KETIDAKCOCOKAN ISI … source_id 2492`.
  Pertahanan berlapis dua sumbu (bentuk + isi) nyata dan bagus.
- **V5** (V4 + `source_payload_hash` baris itu ikut dihapus): **0 KETIDAKCOCOKAN**, exit 0, langkah 2
  tetap memperagakan `NO_GO` identik. Yang tercetak: *"TIDAK DIVERIFIKASI di sini: barisnya tidak
  membawa `source_payload_hash`"*.

V5 **bukan** klaim palsu — naskah menyatakan jujur bahwa ia tidak bisa memverifikasi. Ini residu:
kekuatan pagar bergantung pada kunci yang berkas itu sendiri sertakan, dan menghapusnya menurunkan
vonis dari MISMATCH menjadi catatan lunak. Belum ada kalimat yang berkata baris tak-terverifikasi
**tidak layak** memerankan pemadaman.

## Uji 30 detik Layar B — LULUS, ±12 detik, nol klik

Kalimat baru **tidak** membuatnya ramai, dan itu keputusan desain yang benar: status "daya veto belum
divalidasi" mendarat di **judul kolom** dan **kaki halaman**, bukan di atas lipatan. Tiga baris teratas
Layar B tidak bertambah satu kata pun.

## Klaim yang diverifikasi benar

- **#8 resep `--db`** ✔ — diuji: `--until` sama atas salinan berbeda memberi **n=186** (bukan 237),
  dan alat mencetak `max(snapshots.id): 3981` sehingga selisihnya bisa dijelaskan. `01_freshness`
  **0× merah**, `06_confluence` **75× merah** — arahnya bertahan.
- **#9 aria-label** ✔ — 31 label, **nol elipsis**, terpanjang **702 aksara** dan lengkap (diperiksa byte mentah).
- **#12 `scope=`** ✔ — Layar A 13/13, Layar B 23/23, nol kutip tunggal.
- **#10 `lang`** ✔ — bingkai `lang="id"`, 23 span Inggris ber-`lang="en"`.
- **#6 DISCLOSURE** ✔ · **#3/#4 layar** ✔ · **#5 lubang** ✔ · **#2 item 9** ✔ (pada keputusan yang dihitung hari ini).
- **Arah dependensi** ✔ · **Exception** ✔ 24/24 ber-`noqa: BLE001` · 1317 fungsi → 1848 kasus, 19.001 LOC.

## Temuan baru

1. **Judul baru item 1 tidak pernah sampai ke mata siapa pun.** `ItemDef.title` punya **tepat satu**
   pemakai: sebuah assertion di tes. String itu nol kali muncul di `make demo` maupun HTML kedua layar.
   Yang benar-benar mendarat adalah ekor `reason` — itu terverifikasi hidup.
2. **Perbaikan item 9 tidak menjangkau kartu tersimpan.** **0/52** kartu memuat kalimat baru; **29**
   masih memuat `"the plan is present and worth its risk"` — termasuk **#52, kartu terbaru**, yang
   dibuka untuk uji 30 detik. Store append-only, jadi ini konsekuensi desain yang benar — tetapi
   layar yang juri buka hari ini masih mengucapkan klaim yang sudah ditarik.
3. **README salah tentang reponya sendiri di enam klaim / sepuluh tempat.**
   - `:18 :57 :243 :287` — "1840 passed"; terukur **1848 passed, 1 skipped**.
   - `:275` — item 9 "1426/1426"; kartu yang dijelaskannya berkata **1476/1476**. README membantah
     kartu di halaman yang sama.
   - `:204` — "lubang blok `synthetic` belum ditutup"; **salah**, V3/V3b mencetak KETIDAKCOCOKAN 2×.
   - `:260` — "resep masih `--until` saja"; **salah**, sudah `cp` + `--db`.
   - `:356 :357 :358` di bawah judul "belum ada hari ini" — ketiganya **sudah ada**.
   - `:178 :360` + `docs/posts/05-two-dates.md:5` — merujuk `demo/video-script.md` yang **tidak ada**.
     Satu-satunya yang salah ke arah **membesarkan**.
4. **`agent/trade.py` `"sizing": "fixed_notional"`** — tidak tersentuh; setiap trade 1000 USD.

## Kepatuhan — DISCLOSURE ✔ · SIMULATED ✔ · sumber ✔ · key ✔

- **Key ✔** pemindaian nilai-per-nilai atas 120 berkas terlacak + seluruh `logs/` → **0 kecocokan**.
- **SIMULATED ✔** banner kedua layar, `simulated` di 200/404/503 (tujuh route saat DB hilang).
- **Sumber ✔** 2 sumber, keduanya `enabled+approved`. `llm_narration` masih satu-satunya `approved`
  yang ditegakkan sendiri; risikonya rendah.
- **`docs/posts/` ✔ terhadap ADR-008** — nol nilai mentah RYO, nol angka grading, nol tabel veto.
  Tag/handle sengaja kosong dengan alasan §E — benar.
- **Catatan:** `demo/video-script.md` dihapus koordinator sesuai TASKS 5.3, tetapi **tiga rujukan
  tertinggal** (README ×2, post ×1). Terhadap `rules-compliance.md:14` itu satu klaim tak didukung.

## Skor

```
Umum:  combining 18/20 | failure 18/20                              → 36/40
T1:  thinking 15/25 | cause-effect 18/20 | repeat 10/10 | exec 3/5  → T1 total 82/100  ✅
T2:  read 19/25 | importance 21/25 | everyone 9/10                  → T2 total 85/100  ✅
T3:  gap 21/25 | spec 7/15 (DIBATASI) | usable 16/20                → T3 total 80/100  ✅ margin nol
```

Yang naik: **thinking 14→15** (verdict `accumulate` → GO 2× / NO_GO 7×; `neutral` → GO 4× / NO_GO 7×
— verdict sama, keputusan beda) · **cause-effect 17→18** (kolom daya veto bernyatakan status di kedua
layar) · **repeat 9→10** (resep `--db` benar dan terbukti bekerja) · **importance 17→21** (layar
mengatakan sendiri vetonya belum terbukti menyelamatkan apa pun) · **everyone 7→9** (kedua potongan
a11y ditutup, diverifikasi di HTML mentah) · **gap 20→21** (menerbitkan ambangnya sendiri yang tidak
pernah menolak apa pun) · **usable 14→16** (README tidak lagi menyuruh pembaca melewatkan produknya).

Yang tetap: **exec 3** (sizing 0/½/1 spec tidak ada) · **spec 7 DIBATASI** (§E: tidak ada spec T3 formal)
· **combining 18** · **failure 18**.

## 5 pertanyaan juri

1. **"Bedanya apa dengan guardrail biasa?"** — Jawabannya kini di **tiga** tempat: `unspoken` memveto
   item wajib, ditolak validator bila membawa nilai termasuk `0`, dan skillnya **menerbitkan ambangnya
   sendiri yang tidak pernah menolak apa pun**. Guardrail biasa tidak mengaudit dirinya di depan umum.
   Yang menahan: di ledger hidup 29 keputusan, **nol** penahan berstatus `unspoken` — fitur andalannya
   baru pernah menyala di fixture.
2. **"Kalau semua item hijau, apa yang agen ini pikirkan?"** — **Tetap tidak ada jawaban, dan kini
   diakui lebih keras.** README §8 menyatakan sendiri "GO berarti tidak ada item yang keberatan".
   Sepuluh hijau berarti 7–8 pemeriksaan yang bisa gagal, dua yang secara struktural tidak bisa.
   Mengakui tautologi lebih baik daripada menyembunyikannya; ia belum menjadi pikiran.
3. **"Tunjukkan keputusan yang berubah karena data HILANG."** — **Ada, dan lebih tajam.** DGAI
   2026-09-04: 7 penahan, **5 HILANG**. Pada tanggal itu `market_overview` berkata `risk_off` — nilai
   yang, kalau arahnya diketahui, akan **merah**. Sistem menolak memakainya dan melabelinya HILANG.
   Itu perbedaan antara "saya tahu ini buruk" dan "saya tidak berhak menyimpulkan".
4. **"Kenapa kartu terbaru masih berkata 'worth its risk'?"** — Jawaban benar ada (append-only; kartu
   artefak beku), tetapi **tidak ada satu baris pun di layar** yang mengatakannya, dan yang dibuka juri
   adalah kartu. Satu tick agen sebelum kirim menutup pertanyaan ini seluruhnya.
5. **"Apa yang terjadi kalau saya buang `source_payload_hash`?"** — Naskah turun dari MISMATCH ke
   catatan lunak dan langkah 2 tetap jalan. Jujur secara teknis; yang belum ada adalah kalimat bahwa
   baris tak-terverifikasi **tidak layak** memerankan pemadaman.

## Prioritas perbaikan

**Wajib sebelum kirim (8 Sep 20:00 JST)**

1. **Perbaiki enam klaim salah di README** (`:18 :57 :243 :287` 1840→1848 · `:275` 1426→1476 · `:204`
   dan `:356` lubang sudah ditutup · `:260` dan `:358` resep sudah benar · `:357` status kolom sudah
   mendarat · `:178 :360` + `docs/posts/05-two-dates.md:5` hapus rujukan `demo/video-script.md`).
   Menggerakkan ketiga track, dan satu-satunya yang menyentuh `rules-compliance.md:14`. Terbesar untuk
   **T3** yang lewat dengan margin nol. (penulisan dokumen)
2. **Jalankan satu `make agent-tick` sebelum kirim**, supaya kartu terbaru mengucapkan kalimat item 9
   dan item 1 yang baru. (kerja agen)
3. **Rekam video 30 detik Layar B.** (manusia)

**Kalau sempat**

4. Tolak fixture langkah 2 yang barisnya tidak membawa `source_payload_hash` (varian 5).
5. Bawa judul item ke layar, atau hapus `ItemDef.title`.
6. Cantumkan `max(snapshots.id)` pada baris tabel veto di `README.md:243`.
7. Buang angka "−85 %" dari baris tabel `docs/posts/plan.md:6`.
