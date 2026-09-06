# LOG — keputusan, perubahan, dan utang

Catatan yang terbaca manusia tentang bagaimana repo ini sampai ke keadaannya hari ini: apa yang diputuskan dan
kenapa, apa yang ditemukan tinjauan (termasuk yang memalukan), apa yang diperbaiki sesudah dinilai, dan apa yang
**masih** menjadi utang. Ini bukan daftar tugas dan bukan bahan promosi — kalau sesuatu terlihat buruk, ia tetap
ditulis, karena bagian yang buruk itulah yang paling berguna untuk orang yang membaca kode ini nanti.

Setiap klaim di bawah menunjuk berkas, perintah, atau laporan yang bisa diperiksa. Ringkasan produk dan angka
empirisnya ada di `README.md`; keputusan formal ada di `docs/decisions.md`; dua penilaian pihak ketiga terakhir ada
di `docs/judge-reports/fase-5.md` dan `docs/judge-reports/fase-5-ulang.md`.

Rentang kerja: 1–6 September 2026. Batas kirim: 8 September 2026.

---

## 1. Apa yang dibangun, dan kenapa bentuknya begini

Tenko (点呼) adalah *roll call* sebelum berangkat. Sebelum setiap practice trade — semuanya **SIMULATED**, tanpa dana,
wallet, atau eksekusi — agen menunjuk sepuluh item bukti dari lima tool RYO satu per satu, menyebut nilainya dan
`as_of`-nya, dan **tidak berangkat** bila ada satu item merah **atau satu item yang tidak tersebut**. Gerakannya
diambil dari *shisa kanko* (指差喚呼) di transportasi Jepang: menunjuk yang sudah terlihat, dengan suara, menangkap apa
yang luput dari pemeriksaan diam.

Keputusan produknya (ADR-012, 1 Sep) diambil setelah semua ide lain punya pembunuh yang baru bisa dicek **setelah**
sampel diambil. Tenko dipilih justru karena ia hanya bergantung pada **bentuk** jawaban RYO — envelope
`{status, data_mode, as_of, data, availability, warnings}` — yang sudah pasti dari sampel live 1 Sep
(`docs/samples/*.json`), bukan pada **isi** jawabannya. Dua ide sebelumnya (Honest Fill: biaya tersembunyi swap;
Calibration Ledger sebagai produk utama) dibuang permanen di ADR ini, dan ADR-010/011 ditutup olehnya.

Keputusan lain yang mengikat sampai hari ini:

| Keputusan | Isi singkat | Konsekuensi yang benar-benar terasa |
|---|---|---|
| ADR-001 | Python murni, tanpa Node/npm | Layar dirender di server, nol `<script>`; tidak ada audit kontras pihak ketiga karena toolchain-nya dilarang |
| ADR-003 | **Tidak ada LLM di jalur keputusan** | Keputusan = fungsi deterministik atas snapshot beku; `make demo` dua run byte-identik; narasi hanya menerjemahkan hasil dan terbukti tidak menggeser `decision_hash` |
| ADR-004 | Kontrak `SkillResult` = konvensi kejujuran sebagai kode | Field null/`unavailable` → item `unspoken`, **bukan** nilai default; validator menolak item `unspoken` yang membawa nilai, termasuk `0` |
| ADR-005 | `sources.yaml` sebagai registry sumber | Hari ini dua sumber, keduanya `enabled+approved`; mematikan sumber tidak boleh melempar exception |
| ADR-006 | Store append-only, tanpa UPDATE/DELETE | Artefak beku tidak pernah ditulis ulang — termasuk saat teks yang salah sudah diperbaiki (lihat §3) |
| ADR-013 / ADR-015 | Envelope skill meniru envelope RYO + **tepat satu** field baru, `simulated` | Tidak ada spec skill T3 formal dari penyelenggara (`docs/api-facts.md` §E), jadi bentuk yang paling murah dipakai RYO adalah bentuk mereka sendiri |
| ADR-016 | Generasi lama di ledger dilaporkan `STALE_ALGO`/`STALE_PARAMS`, tidak dihapus | Baris lama dinyatakan **tidak dinilai**, bukan salah, dan bukan tamper |
| ADR-017 | `neutral` = hijau tanpa konfirmasi | **DITERIMA 6 Sep 2026.** Lihat §4 |

Dua ADR yang masih berstatus **USULAN** meski kodenya sudah bersandar padanya, dan itu ditulis apa adanya di
`docs/decisions.md`:

- **ADR-018** (agen store-first murni; opsi `--client live` dicabut). Alasannya nyata: satu kunci API punya satu
  token bucket 60 panggilan/menit yang dibagi seluruh proses, dan satu preflight yang semuanya live ≈ 60 detik.
  Konsekuensi yang ditanggung sadar: **uptime perekam menjadi jalur kritis produk** — celah rekaman langsung menjadi
  "tidak tersebut" di kartu. Status USULAN-nya dipatok tes
  (`tests/test_agent.py::test_main_docstring_states_the_deviation_and_points_at_adr_018`).
- **ADR-014** (vendoring three/GSAP/Lenis). Tidak pernah terjadi. Layar yang benar-benar dikirim memakai **nol**
  library JS, dan `DISCLOSURE.md` menyebut ketiganya sebagai rencana, bukan sebagai berkas yang ada.

---

## 2. Perekam: satu-satunya sumber bukti, dan pernah bocor 24 jam

Karena agen tidak pernah memanggil RYO secara live (ADR-018), seluruh bukti berasal dari perekam. Itu membuat celah
rekaman menjadi masalah produk, bukan masalah operasi.

**Yang ditemukan (4 Sep 2026).** Sapuan baca-saja atas DB perekam kami (tidak ikut dikirim — §5), jendela 64,7 jam:
harga yang cukup segar
(≤120 menit) hanya tersedia **51,8–57,5 %** waktu per simbol inti, dengan celah terpanjang **1455 menit (24,3 jam)** —
melebihi `DEFAULT_TIMEOUT_MIN` saat itu, sehingga posisi bisa lewat jatuh tempo tanpa pernah bisa ditutup. Sebabnya
perekam kami, bukan RYO: `price_usd` terisi 929/929 = 100 %.

**Hipotesis pertama TERBUKTI KELIRU, dan itu ditulis, bukan dihapus.** Dugaan awal "request menggantung" salah:
semua durasi siklus ada di 990–1268 detik, jadi timeout per-request bekerja. Penyebab sebenarnya lebih membosankan
dan lebih menarik: `run()` memakai `time.monotonic()`, yang di lingkungan ini **tidak menghitung host yang
tersuspend** — satu siklus dengan wall-clock 3 jam 36 menit melaporkan `durasi=1054s`. Perbaikannya: `time_fn`
default menjadi `time.time`, tidur dipotong per 60 detik dengan sisa dihitung ulang dari jam dinding, lompatan
≥1 interval mencetak satu baris peringatan "tersuspend", dan watchdog memotong siklus yang melewati batas tanpa
membunuh proses apa pun (8 tes baru di `tests/test_recorder.py`).

**Sapuan kedua (5 Sep) TIDAK BOLEH dikutip sebagai bukti perbaikan**, dan itu dicatat di tempatnya sendiri: jendela
23,6 jam yang diukur berisi ~7,7 jam kode lama + 6,4 jam perekam mati + 9,5 jam kode baru. Angka 77 % di dalamnya
adalah campuran. Jangkar yang dipakai ("pendaratan commit") memang keliru; jangkar yang benar adalah baris
`perekam mulai` terakhir di log.

**Sapuan ketiga dan terakhir (6 Sep 2026 01:49Z)**, atas salinan beku (`max(snapshots.id)=4048`), jendela 23,51 jam:
99,8–100 % waktu ada harga ≤120 menit, max gap **60–87 menit** lawan batas 720, **nol** celah >120 menit. Bandingkan
baseline 4 Sep: 51,8–57,5 %, max 1194–1455 menit, 5–7 celah per simbol.

**Yang sapuan itu TIDAK buktikan, dan disebut sendiri di sana:** bahwa perbaikan kode-lah penyebabnya. Jendelanya
memuat **satu** restart manual, jadi jam terbang tanpa putus hanya 14,05 jam, dan mekanisme restart otomatis di luar
repo **belum dipasang** (lihat §6). Uptime yang bagus ini masih bergantung pada tidak adanya proses yang mati.

Pelajaran metodologis yang mahal dan berlaku ke seluruh angka empiris repo ini: **"% segar" sangat sensitif terhadap
letak tepi jendela.** Penurunan cacah celah 7 → 3 pada satu sapuan hampir seluruhnya perpindahan jendela, bukan
perbaikan; enam dari tujuh celah hilang semata-mata karena **mulai** sebelum tepi jendela. Angka seperti itu tidak
boleh dikutip tanpa kalimat ini.

---

## 3. Temuan tinjauan yang mengubah kode

Diurutkan dari yang paling tidak enak.

**Koneksi "baca-saja" ternyata bisa MENULIS (TINGGI, 4 Sep).** `core/store.py` merakit URI `file:{path}?mode=ro`
lewat f-string. Path yang memuat `?` atau `#` mengubah arti URI, dan tinjauan keamanan adversarial membuktikan
`INSERT … simulated=0` **berhasil** lewat koneksi yang modul itu sendiri sebut tidak bisa menulis — termasuk lewat
nama berkas ber-`#`, yang sah sebagai nama berkas. Ini membatalkan klaim struktural yang dipakai di beberapa tempat
sebagai alasan keamanan (misalnya: `agent/ledger.py` boleh memuat SQL di luar `core/store.py` **hanya** karena
koneksinya tidak bisa menulis). Ditutup tiga lapis, masing-masing dipatok tes: penolakan setiap `?`/`#` di resolusi
path, `quote()` saat merakit URI, dan `PRAGMA query_only=ON` + authorizer yang hanya meloloskan pembacaan. Tiap lapis
dihapus satu per satu untuk memastikan ada tes yang merah (mutation testing, dua pihak independen). Docstring modul
kini menyebut bahwa klaim "tidak bisa menulis" **pernah tidak benar**, dan apa yang tidak diklaim.

**Klaim provenans afirmatif tanpa verifikasi isi (TINGGI, 6 Sep).** Fixture bisa menyatakan dirinya "salinan apa
adanya dari respons RYO" sementara angkanya sudah diedit, dan tidak ada yang memeriksa. Ditutup dengan `sha256` yang
direkam saat penyalinan dan dijangkarkan ke `snapshots.payload_hash` DB nyata. Ini keluarga yang sama dengan lubang
pengakuan §7 langkah 2 di §4.

**Injeksi ANSI lewat payload tool (TINGGI, 6 Sep).** Nilai dari tool bisa memalsukan atau menghapus baris tabel daya
veto di terminal. Asumsi bahwa `ItemResult` sudah membersihkan `value` terbukti **salah** — tidak ada validatornya.
Ditutup di sumber (`preflight/items.py`) dan di sink (`preflight/veto_table.py`).

**CLI yang memuntahkan bukti karangan tanpa jejak (TINGGI, 6 Sep).** Perintah pembuat fixture pemadaman bisa
menghasilkan envelope palsu ke stdout. Kini ia **menolak** (rc=2, stdout 0 byte) dan mengaku ke stderr.

**Simbol yang tidak ada menulis baris ledger permanen — dan `replay` menyebutnya `IDENTICAL`.** `--`, `"BTC"`,
`{"SYMBOL":"SOL"}`, `123`, fullwidth `ＳＯＬ`, `BNB😀` semuanya diterima. Nol injeksi SQL dan nol crash — ini polusi
ledger, bukan lubang keamanan — tetapi store append-only berarti barisnya **tidak bisa dihapus**. Ditutup dengan
`plausible_symbol()` di `agent/watchlist.py`, dijalankan **sesudah** normalisasi (yang sengaja tidak diperketat,
karena paritasnya dengan perekam dipatok tes dari sisi berlawanan). Sengaja **bukan** whitelist ASCII: DB perekam
kami memuat `H`, `牛来`, `龙虾`, `币安人生`, `비트` sebagai simbol yang benar-benar dikembalikan RYO — 103 simbol nyata
diperiksa, 0 ditolak; kanji+kana dihitung satu aksara supaya `ドージ`/`犬コイン` tidak ditolak. **Batas yang diakui dan
dipatok tes, bukan diklaim tertutup:** confusable ber-aksara tunggal (`ВТС` Kiril penuh, Cherokee `ᏚᎢᏞ`, Yunani
`ΒΤΑ`) tetap lolos.

**Klaim "7 baris `decisions`" DICABUT.** Angka itu pernah ditulis dalam laporan temuan di atas; sapuan ulang atas
DB + 13 backup memberi 27 baris, semuanya bersimbol SOL/BTC/ETH/BNB. Pencabutannya ditulis di tempat klaimnya
berdiri, bukan dihapus diam-diam.

**Baris fixture mencemari DB perekam, dan tidak ada yang mencegahnya.** Ditutup dengan penjaga yang menolak tujuan
tulis di dalam `ROOT/data` (`agent/demo_fixture.py`).

**Kartu mengucapkan kalimat yang tidak benar.** `availability: "partial"` dibacakan sebagai "not available" —
salah, dan diperbaiki. Item 6 diam tentang gate yang `passed=null`. Item 8 diam tentang bukti berumur 1–2 hari
padahal `funding`/`liquidation` **selalu** D-1 (47/47 baris nyata, umur 25,2–47,5 jam, median 35,8 jam); kini kartu
mengucapkan tanggalnya, dengan "date not stated" bila tanggalnya null.

**Kesegaran kami bukan kesegaran.** `as_of` RYO membeku ~2,5 hari di belakang `ts_utc` yang kami catat, jadi yang
item 1 ukur adalah **konsistensi bundel**, bukan kesegaran isi. Ini bukan bug yang bisa ditutup; ia dituliskan ke
dalam kalimat item itu sendiri (lihat §4 dan `README.md` §7 butir 1).

**RYO tampak mengabaikan argumen `symbols` (atau melayani cache)**, sehingga BTC tidak akan pernah hijau di item 10.
Dicatat sebagai fakta sumber, bukan diperbaiki dengan menyetel ambang.

**Hal-hal kecil yang tetap ditulis:** kanari pemeriksa kosakata tidak menyapu nilai di dalam list, sehingga kosakata
baru dari RYO bisa masuk tanpa terdeteksi. `preflight()` melempar `TypeError` mentah alih-alih exception domainnya
di jendela tanpa bukti. Satu tes memakan 295 detik dari 583 detik suite — suite yang menjalankan dirinya sendiri.
Satu skrip pemeriksa lokal menjalankan `ruff check` tetapi **tidak** `ruff format --check`, sehingga ia bisa hijau
sementara `make lint` merah; drift format benar-benar lolos selama beberapa commit sebelum ketahuan.

**Tinjauan keamanan menyeluruh terakhir (6 Sep)** dijalankan sebagai satu batch atas 23 commit. Ronde 1: **BLOKIR,
5 TINGGI + 3 SEDANG.** Ronde 2 sesudah perbaikan: **1 TINGGI + 2 SEDANG.** Ronde 3: **dicabut, nol KRITIS, nol
TINGGI.** Tiap ronde serangannya diulang dari awal (11 → 14 → 9 serangan), bukan dibaca dari laporan sebelumnya.

**Empat batas yang diterima apa adanya di vonis akhir, dan belum ditutup:** teks bebas `source_db` dicetak
afirmatif; `agent.demo` berteriak atas ketidakcocokan isi tetapi rc-nya tetap 0 sementara CLI-nya menolak — dua
ketegasan atas satu berkas; kunci datum di `grading/` masih proxy nilai, sah **hanya sampai selang kepercayaan
pertama dicetak**; item 01/02 masih memakai `bundle:<snapshot_ids>` sebagai sumber.

---

## 4. Dinilai, gagal, diperbaiki, dinilai lagi

Repo ini dinilai dua kali dengan rubric resmi (`docs/spec.md` §8) oleh pihak yang menjalankan sendiri semua
perintahnya, bukan membaca laporan kami.

**Penilaian pertama (6 Sep) — TIDAK ADA track yang mencapai ambang 80: T1 79 · T2 78 · T3 77.** Penyebab terbesarnya
sama untuk ketiganya, dan **bukan kode**: `README.md` masih memberi tahu pembaca bahwa `make demo`, kedua layar, dan
demo dua tanggal **tidak ada** — padahal ketiganya berjalan di depan penilai hari itu. README-lah yang menyuruh juri
melewatkan produknya sendiri.

Empat kegagalan yang bisa direproduksi:

1. Fixture pemadaman sintetis yang blok pengakuannya **dihapus seluruhnya** lolos tanpa satu pun `KETIDAKCOCOKAN`,
   padahal kode sudah tahu envelope itu dibentuk. Pengakuannya bergantung pada berkas yang sukarela mengaku.
2. Angka tabel daya veto **tidak terulang** dengan resep yang tertulis: `--until` yang sama atas DB hidup memberi
   201 → 237.
3. Kalimat kartu melampaui buktinya: item 9 berkata "the plan is present and worth its risk" sementara
   `risk_reward` konstan `[1.0, 2.0]` dan `min_rr = 1.0`. Kalimat itu tidak pernah bisa salah.
4. README menyangkal produknya sendiri.

**Yang dikerjakan sesudahnya:** kalimat item 9 dan item 1 diperbaiki sehingga membawa angkanya sendiri; kolom "daya
veto" di **kedua** layar diberi status `belum divalidasi`; `k = 0` diangkat ke permukaan (satu bagian README + satu
baris Layar A); lubang pengakuan §7 langkah 2 ditutup dengan menjadikan **ketiadaan** blok pengakuan sebagai selisih
tersendiri ("pemadaman yang TIDAK DIAKUI"); resep tabel veto diganti menjadi `--db <salinan beku>`; dua potongan
aksesibilitas ditutup (`aria-label` tidak lagi dipotong, `scope=` pada header tabel, `lang="en"` pada span Inggris);
satu tick agen dijalankan supaya kartu terbaru mengucapkan kalimat baru.

**Penilaian kedua (6 Sep) — ketiga track lewat: T1 82 · T2 85 · T3 80.** T3 lewat dengan **margin nol**. Serangan
diulang: enam langkah demo ✔ dengan `diff` kosong dan md5 sama, chaos **8/8**, DB dihapus di bawah server hidup →
tujuh route 503/404 semuanya membawa `simulated`, tamper → MISMATCH ber-hash, dan uji 30 detik Layar B **LULUS dalam
±12 detik tanpa klik**.

**Yang penilaian kedua tetap temukan, dan ini bagian yang tidak enak:**

- **README masih salah di enam klaim / sepuluh tempat** — kali ini hampir semuanya ke arah **mengecilkan** repo
  sendiri (suite ditulis 1840 padahal 1848; item 9 ditulis 1426/1426 padahal kartunya sendiri berkata 1476/1476;
  tiga hal yang sudah ada masih berdiri di bawah judul "belum ada"). Satu-satunya yang salah ke arah
  **membesarkan**: tiga rujukan ke naskah video yang berkasnya tidak ada. Terhadap
  `docs/rules-compliance.md:14` itu satu klaim tak didukung — kelas kesalahan yang sama yang perbaikan hari ini
  (§5) ada untuk menghentikannya.
- **Judul baru item 1 tidak pernah sampai ke mata siapa pun.** `ItemDef.title` punya **tepat satu** pemakai: sebuah
  assertion di tes. String itu nol kali muncul di `make demo` maupun di HTML kedua layar.
- **Perbaikan item 9 tidak menjangkau kartu tersimpan.** 0 dari 52 kartu memuat kalimat baru; 29 masih memuat
  kalimat lama — termasuk kartu terbaru, yang justru dibuka untuk uji 30 detik. Store append-only, jadi ini
  konsekuensi desain yang benar; tetapi layar yang dibuka penilai hari itu masih mengucapkan klaim yang sudah
  ditarik. Kartu **sejak keputusan 53** mengucapkan kalimat baru; kartu 1–52 akan selamanya mengucapkan kalimat
  lama, dan itu tidak akan ditulis ulang.
- **Serangan keempat pada pengakuan sintetis ditemukan.** Bentuk pemadaman lewat jalur yang pemeriksa bentuk tidak
  lihat: `status` tetap `partial`, tanpa warning, tetapi `availability.*` semua `unavailable` dan `data=None`. Ia
  **tertangkap** pagar kedua (pemeriksa isi). Varian berikutnya, yang juga menghapus `source_payload_hash` baris itu,
  **tidak** tertangkap: naskah turun dari tuduhan menjadi catatan lunak, mencetak "TIDAK DIVERIFIKASI di sini". Itu
  jujur secara teknis, dan tetap residu — lihat §6.
- **Dua dari lima pertanyaan penilai belum terjawab penuh.** "Kalau semua item hijau, apa yang agen ini pikirkan?"
  masih tidak punya jawaban; yang ada hanyalah pengakuan bahwa GO berarti "tidak ada yang keberatan", bukan "bukti
  mendukung". Dan `sizing: fixed_notional` tidak tersentuh: setiap practice trade 1000 USD notional, tidak ada jalur
  di mana reliabilitas menggerakkan ukuran.

**ADR-017 diterima 6 Sep 2026.** Kodenya sudah menerapkan `neutral` = hijau sejak 2 Sep; yang berubah hari ini
adalah **alasannya** — knob-nya kini merujuk ADR, bukan lagi bacaan harfiah spec §4. Pengukuran menunjukkan
`params_hash` **tidak bergeser** (`3d39fb1b…9616b7` sebelum = sesudah), karena materi hash hanya membaca nilai dan
bukan docstring; jadi tidak ada baris `decisions` yang menjadi `STALE_PARAMS` dan tidak ada fixture yang perlu
diperbarui. Nilai final itu kini ditempel di `README.md` §4 dan dijaga tes yang gagal bila ia berubah
(`tests/test_params.py:242`). Alasan pembekuannya: `params_hash` ikut ke setiap `decision_hash`, jadi mengubah ambang
mana pun membuat keputusan lama dilaporkan `STALE_PARAMS` — perilaku yang benar, tetapi bukan yang mau kami tunjukkan
tiga hari sebelum kirim.

---

## 5. Apa yang dikirim, dan apa yang tidak

Diputuskan pemilik repo 6 Sep 2026, setelah dua penilaian berturut-turut memotong nilai karena dokumen menunjuk
berkas yang tidak ikut terkirim.

**Ikut dikirim:** kode (`core/`, `preflight/`, `agent/`, `ui/`, `grading/`, `tests/`), `PRD.md`, `docs/` termasuk
kedua laporan penilaian, `scripts/guard.sh`, `README.md`, `DISCLOSURE.md`, `LOG.md`.

**Tidak ikut dikirim, dan alasannya:**

- **DB perekam (`data/`).** Ada di `.gitignore`, dan snapshot pasar ber-timestamp tidak bisa dibuat ulang oleh siapa
  pun kecuali perekam yang berjalan pada waktu itu — mengirimkannya pun tidak membuatnya bisa diperiksa ulang.
  Konsekuensinya: setiap angka empiris di `README.md` adalah laporan tentang mesin kami, dan setiap resep reproduksi
  ditulis atas **DB yang perekam pembaca sendiri hasilkan**. Alat-alatnya sudah menjawab jujur tanpanya: `make ui`
  memberi 503 ber-banner, `make skill` memberi envelope `unavailable` dengan `data:null`, `make grade` menyatakan
  tidak ada bukti — tidak satu pun mengarang angka.
- **Satu skrip lint/kepatuhan lokal.** Ia perkakas kerja internal, bukan bagian aplikasi. Gejalanya sudah ditutup:
  dua belas tes paritasnya kini **SKIP dengan alasan yang terbaca** alih-alih merah. Yang ditemukan saat
  memutuskannya lebih besar dari yang dilaporkan: pada klon bersih bukan 2 tes merah melainkan **10 merah + 2 HIJAU
  PALSU** — satu tes lulus karena skrip yang tidak ada keluar dalam 0,0 detik sehingga lolos anggaran waktu, satu
  lagi lulus karena pesan `bash: No such file` tidak memuat kata "ruff" sehingga dianggap "sepakat". Dua tes paritas
  melaporkan AMAN tentang skrip yang tidak ada. Bukti dua arah: klon bersih → `3 passed, 12 skipped`; pohon identik
  dengan skripnya disalin masuk → `15 passed, nol skip`.
- **`scripts/guard.sh` sebaliknya IKUT dikirim**, karena `docs/rules-compliance.md` menyebutnya sebagai kontrol
  kepatuhan dan ia terbukti menggigit pada orang yang bukan kami: dua perintah pemeriksa pihak ketiga sendiri
  diblokir saat nama variabel kunci disebut (`docs/judge-reports/fase-5.md` §tes destruktif d).
- **Naskah video.** Dilewatkan atas permintaan pemilik repo; berkasnya tidak ada dan tidak akan dibuat. Naskah
  adegan untuk rekaman 30 detik Layar B tetap ada di `ui/README.md`.

---

## 6. Utang yang belum ditutup

Ditulis sebagai belum ditutup, bukan sebagai rencana.

1. **`k = 0`.** `make grade` menilai 13 keputusan berumur ≥24 jam dan menemukan **nol** kerugian yang terbukti dicegah
   oleh item mana pun. Lebih buruk: **`target_first = 3`** — tiga kali veto menahan plan yang justru menyentuh target
   lebih dulu, yaitu tiga kali vetonya salah, dan kalimat itu dicetak alat ukurnya sendiri. `n_efektif` 1–5 lawan
   ambang 30, jadi **nol selang kepercayaan dicetak** dan item ke-11 (Bunryō, ukuran posisi dari reliabilitas) menutup
   dengan "TIDAK ada takaran yang dikeluarkan". **Kami tidak punya bukti bahwa sepuluh item ini menghasilkan keputusan
   yang lebih baik.**
2. **Bukti market-wide dihitung empat kali, dan itu menekan `n` di SELURUH angka empiris.** `market_overview` dan
   `monitor_market_sentiment_shift` dipanggil dengan argumen `{}`, jadi keempat simbol inti membaca baris yang sama:
   `08_derivatives` 32 merah = **8** peristiwa berbeda, `03_regime` 30 = 8, `04_sentiment_shift` 20 = 5. **Ini bukan
   bug** — satu envelope market-wide per siklus memang benar dan hemat kuota; yang salah adalah membacanya sebagai
   empat amatan. Belum diputuskan apakah jalur pengukuran menghitungnya sekali per siklus, dan keputusan itu menyentuh
   `params_hash` sehingga butuh ADR sendiri, bukan perbaikan diam-diam.
3. **`01_freshness` belum pernah menahan apa pun.** 0× merah dalam n=201, 0× lagi dalam ulangan pihak ketiga (n=186),
   0× dalam n=237. Sebabnya struktural: pemilih bukti sudah membatasi umur **sebelum** item mana pun dinilai, jadi
   baris itu tanda terima, bukan veto. Kalimatnya kini mengaku sendiri; **id-nya sengaja tidak diganti** karena id ikut
   ke `params_hash`/`decision_hash`. Utangnya: judul barunya tidak pernah sampai ke layar mana pun.
4. **Envelope §7 langkah 2 SINTETIS.** RYO belum pernah mengeluarkan `status: "unavailable"` dalam pengamatan kami —
   0 dari 3645 snapshot — jadi satu envelope di fixture langkah 2 **dibentuk**. Berkasnya mengaku, naskahnya
   menghitung ulang pengakuan itu dan meneriakkan `KETIDAKCOCOKAN` bila berselisih. Residunya: **baris yang tidak
   membawa `source_payload_hash`** membuat naskah kehilangan kunci pembanding dan turun ke catatan lunak. Belum ada
   aturan yang menyatakan baris tak-terverifikasi tidak layak memerankan pemadaman.
5. **Fitur andalan produk baru pernah menyala di fixture.** Veto oleh item wajib yang `unspoken` — inti tesis
   Tenko — belum pernah terjadi di ledger keputusan hidup: **nol** penahan berstatus `unspoken` pada 29 keputusan
   generasi berjalan. Yang memperagakannya berjalan dari fixture beku, dan itu disebut sebagai fixture.
6. **Rekaman 30 detik Layar B belum dibuat.** Artefak manusia; naskah adegannya ada di `ui/README.md`.
7. **Tidak ada mekanisme restart otomatis untuk perekam.** Delapan restart manual dalam lima hari; celah 20 jam
   berasal dari sini, bukan dari kode perekam. Selama ini belum dipasang, setiap sapuan mengukur campuran kode +
   waktu mati (§2).
8. **Posisi yang jatuh tempo tanpa harga yang diketahui tidak ditutup.** `trade_events.price` adalah `REAL NOT NULL`.
   Tiga jalan dipertimbangkan; sentinel (0 / -1) **ditolak di muka** karena itu persis "nilai default menggantikan
   `unspoken`" yang produk ini ada untuk mencegah. Yang berlaku: posisinya dibiarkan terbuka dan keterlambatannya
   dilaporkan apa adanya ("N min past the M min limit"). Dipilih karena tidak menyentuh skema dan tidak berbohong,
   bukan karena ia jawaban yang bagus.
9. **Enam pertanyaan ke penyelenggara masih BELUM DIJAWAB** (`docs/api-facts.md` §E), dan tiga di antaranya
   memengaruhi apa yang boleh kami tulis: tidak ada spec skill T3 formal (itu yang membatasi satu sel rubric),
   kriteria Social Media prize (karena itu tag & handle di `docs/posts/` sengaja **dikosongkan**, bukan ditebak), dan
   enum resmi untuk `regime`/`verdict` — jawaban tertulis atasnya akan **menggantikan** tafsir ADR-017 dengan fakta.
10. **Satu `approved` yang ditegakkan sendiri.** `approved: true` untuk LLM narasi bersandar pada frasa "your own AI
    model" di landing page penyelenggara, bukan jawaban tertulis. Risikonya ditekan dengan menempatkan narasi di luar
    jalur keputusan sehingga demo berjalan penuh tanpanya — bukan dengan menganggap izinnya pasti.
11. **Aksesibilitas belum diuji dengan manusia.** Checklist lengkap ada di `ui/README.md` dengan nama tes yang gagal
    bila janjinya dilanggar, dan angka kontras dihitung dari CSS yang benar-benar dilayani. Yang **belum** ada: nol
    pengujian dengan pembaca layar sungguhan, nol audit pihak ketiga, belum diuji zoom 200 %.

---

## 7. Cara memeriksa isi catatan ini

```bash
make doctor    # versi toolchain + jumlah sumber aktif
make demo      # naskah spec §7 langkah 1-6, seluruhnya dari fixture offline, dua run byte-identik
make test      # suite penuh
make lint
```

Semua yang di §4 dijalankan ulang oleh pihak ketiga dan tercatat di `docs/judge-reports/fase-5.md` +
`docs/judge-reports/fase-5-ulang.md`, termasuk daftar kegagalan yang bisa direproduksi dan skor per kategori.
Angka chaos ada di `docs/reports/chaos-2026-09-05.md`. Keputusan formal beserta yang **ditolak** dan alasannya ada di
`docs/decisions.md`. Yang tidak kami klaim ada di `README.md` §8.
