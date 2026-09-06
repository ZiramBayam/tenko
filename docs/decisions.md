# ADR — Keputusan Desain (kenapa, bukan cuma apa)
Format: Konteks → Keputusan → Konsekuensi. Jangan buka ulang tanpa bukti baru; tambahkan ADR baru bila berubah.

## ADR-001 Python murni, tanpa Node
Konteks: grand prize & review menilai code quality/stability/bug count; deadline 8 hari; T3 mensyaratkan Python.
Keputusan: uv + FastAPI + satu halaman statis (vanilla JS). Tidak ada Next/pnpm.
Konsekuensi: UI sederhana; kekuatan visual dikompensasi kejelasan angka (n, CI) — juri T2 menilai "apa yang berubah", bukan estetika.

## ADR-002 (DIGANTI oleh ADR-012: satu produk, satu BUIDL) Satu repo, dua produk, keputusan split ditunda
Konteks: Honest Fill dan Calibration berbagi core (client, store, skill contract, replay). Belum tahu apakah DoraHacks/RYO menerima dua BUIDL dari satu repo (api-facts §E).
Keputusan: monorepo selama build; TASKS 5.4 memutuskan split (`git subtree split` per produk + salinan core) berdasarkan jawaban §E.
Konsekuensi: core harus tidak punya import dari honest_fill/calibration/agent/ui (tes menjaga arah dependency).

## ADR-003 Tidak ada LLM di jalur keputusan
Konteks: brief T1 minta jejak penalaran yang bisa diulang; review menilai stabilitas; honesty convention.
Keputusan: keputusan agen & semua skill = fungsi deterministik atas snapshot beku; `decision_hash` harus identik saat replay. Narasi LLM = stretch setelah TASKS 5.2, di luar jalur keputusan, dan ditandai sebagai narasi.
Konsekuensi: "kecerdasan" terlihat dari kejujuran pembukuan & bukti, bukan dari teks.

## ADR-004 Kontrak SkillResult = honesty convention sebagai kode
Konteks: brief T3 "never fabricate placeholder data when a source fails".
Keputusan: skema docs/spec.md §3; validator menolak `data` bila status ≠ ok; komponen gagal tercatat per komponen.
Konsekuensi: UI wajib menampilkan status/n; tidak ada default yang terlihat nyata.

## ADR-005 sources.yaml sebagai registry sumber eksternal
Konteks: Official Rules: hanya endpoint approved; disclosure wajib; izin API pihak ketiga belum dijawab.
Keputusan: setiap sumber dideklarasikan dengan `enabled`, `approved`, `tos_url`, `used_for`; kode membaca registry; `approved ≠ true` → hanya fixture.
Konsekuensi: mematikan sumber tidak boleh menyebabkan exception; DISCLOSURE.md diturunkan dari registry.

## ADR-006 Perekam mulai hari-1; store append-only
Konteks: tidak ada histori output tool RYO; Calibration butuh umur data; replay butuh snapshot tak berubah.
Keputusan: SQLite append-only (tanpa UPDATE/DELETE), `payload_hash` per baris; grading membaca snapshot perekam sendiri (bukan sumber eksternal) agar 100% dalam endpoint approved.
Konsekuensi: ukuran DB tumbuh (≈40 token × 3 tool × 288 siklus/hari — kecil); "hapus data" hanya dengan menghapus file DB (tes destruktif).

## ADR-007 (DIBATALKAN oleh ADR-012) Model biaya v1 sederhana & terdokumentasi
Konteks: 8 hari; validasi lebih penting daripada model canggih.
Keputusan: impact constant-product; sandwich = median empiris per jam×hari; fee = API atau konstanta berlabel `estimated_static`.
Konsekuensi: MAE dilaporkan apa adanya; kill criterion (PRD §7) berlaku.

## ADR-008 (DIBATALKAN oleh ADR-012; prinsip "hasil hanya di dalam submission" tetap) Framing Calibration = reliability meter untuk user
Konteks: data tool = milik RYO; hasil bisa dibaca sebagai audit negatif.
Keputusan: bahasa produk "rapor sinyal untuk user"; angka selalu relatif ke base rate dan dengan CI; hasil tidak dipublikasikan di luar submission.
Konsekuensi: penulisan dokumen & post sosial tidak memuat angka tool RYO.

## ADR-009 (DIGANTI oleh ADR-012: keputusan = preflight) Sinyal agen sengaja sederhana
Konteks: yang dinilai T1 adalah sebab→akibat & jejak, bukan alpha; sinyal kompleks menambah bug.
Keputusan: kandidat = scan_market rank + check_safety lolos; edge target parametris (bukan prediksi).
Konsekuensi: metrik utama agen = selisih kumulatif buku mid vs honest + kualitas keputusan skip, bukan PnL absolut.

## ADR-010 (DITERIMA 6 Sep 2026; diusulkan 1 Sep 2026) Pivot: Calibration Ledger jadi tulang punggung; agen T1 = "Trust Meter Agent"; Honest Fill diparkir
Konteks: RYO Builder MCP Guide (13 Aug 2026) menunjukkan 6 tool berbasis **simbol** (SOL/BTC/CAKE), listing pasar luas, tanpa
tool safety, tanpa reserves/likuiditas pool, tanpa mint address. Honest Fill v1 (impact constant-product + sandwich per jam)
membutuhkan reserves & mint untuk join ke swap on-chain — keduanya tidak tersedia dari endpoint approved; untuk aset seukuran
SOL/BTC biaya tersembunyi ritel ≈ 0. Kill criterion PRD §7 terpicu sebelum build. Sebaliknya `deep_analysis` memberi
**deterministic verdict + ATR-based preview plan** dan `monitor_market_sentiment_shift` memberi perubahan 7 hari — prediksi
eksplisit yang bisa dinilai 100% dari endpoint approved.
Keputusan (usulan):
1. Calibration Ledger = BUIDL utama (T3 + T2): perekam merekam `deep_analysis` (verdict, plan, confluence) + `analyze_token`
   (harga untuk outcome) + `monitor_market_sentiment_shift` + `market_overview`; grader menilai verdict (arah 24 j/7 h) dan
   plan (target tersentuh sebelum stop? R-multiple realized); `signal_reliability(tool, field)` + base rate + CI.
2. T1 = **Trust Meter Agent**: practice trade diambil dari preview plan RYO sendiri (entry/stop/target), tetapi ukuran posisi
   (0 / ½ / 1) ditentukan oleh reliabilitas terukur kelas verdict itu pada saat keputusan (insufficient → abstain, dicatat).
   Sebab→akibat harfiah: "masuk karena verdict X yang 7 hari terakhir benar k/n"; jejak = snapshot_ids + reliability_at_decision
   + decision_hash; replay identik. Ini menyatukan T1–T2–T3 dalam satu objek tanpa sumber eksternal.
3. Honest Fill → roadmap README. Dihidupkan hanya bila sampel 0.3 menunjukkan scan_market memuat small-cap dengan field
   likuiditas/alamat DAN sumber eksternal diizinkan (§E) — dievaluasi di TASKS 0.6, bukan sekarang.
Konsekuensi: PRD §2/§5 & spec §4–§5 direvisi setelah user setuju; paket `honest_fill/` tidak dibuat di Fase 2; Fase 2 menjadi
Calibration + agen; jadwal lebih longgar (dua produk → satu produk dua wajah). Risiko: quota per key membatasi cadence
perekam → prioritaskan watchlist tetap (≤25 simbol) dan `deep_analysis` per jam, `analyze_token` lebih sering untuk outcome.

## ADR-011 (DITERIMA 6 Sep 2026; diusulkan 1 Sep 2026) Mekanik sosial & peran model AI — memenuhi definisi resmi "Agentic SocialFi"
Konteks: landing ryobuild.com mendefinisikan Social = public reasoning / reputation / debat / group discovery, dan menyatakan
"your own AI model — RYO does not call one for you". Rencana ADR-010 belum punya mekanik sosial eksplisit dan tanpa model AI.
Keputusan (usulan):
1. **Reputation** = rapor reliabilitas sinyal (Calibration Ledger) dipublikasikan sebagai leaderboard sinyal: field mana yang
   layak dipercaya minggu ini, dengan n & CI. Objek sosialnya adalah *sinyal*, bukan user.
2. **Public reasoning** = setiap practice trade Trust Meter Agent menjadi *kartu penalaran* yang bisa dibagikan (URL /trade/{id}):
   bukti terstruktur, reliability_at_decision, ukuran posisi & alasannya, decision_hash, tombol "replay". Kartu = unit share untuk
   post sosial (berlabel SIMULATED).
3. **Model AI** = LLM hanya untuk narasi kartu dari bukti terstruktur (bukan keputusan; ADR-003 tetap). Tes menjaga: narasi tidak
   mengubah decision_hash; prompt membungkus data sebagai data (anti-injeksi dari field summary tool). Model & SDK diputuskan
   di versions.md oleh verifikasi API saat task-nya dibuat (evaluator sudah memakai anthropic SDK — bisa disamakan).
4. Tidak dibangun: debat multi-agen, KOL/X monitoring, komentar/voting user (butuh auth & moderasi; di luar 7 hari).
Konsekuensi: +1 task UI (kartu trade) dan +1 task narasi (Fase 3/4); DISCLOSURE menyebut model AI; LLM = dependency baru.

## ADR-012 (DITERIMA 1 Sep 2026 — menutup ADR-010/011) Produk = Tenko; Bunryō = item opsional; tidak ada brainstorm lagi
Konteks: setelah semua fakta (6 tool berbasis simbol, envelope status/availability/warnings/as_of, rubric 40+60, definisi Social,
ide resmi = zona jenuh), setiap ide yang bergantung pada ISI jawaban RYO (variasi verdict, reserves, histori) punya pembunuh yang
baru bisa dicek setelah sampel. Tenko hanya bergantung pada BENTUK jawaban RYO, yang sudah pasti.
Keputusan:
1. Produk tunggal **Tenko (点呼)** — roll call sebelum berangkat, memakai gerakan tunjuk & sebut (*shisa kanko*, 指差喚呼): sebelum setiap practice trade, agen "menunjuk" 10 item bukti dari 5 tool RYO (nilai +
   field + as_of), "menyebutnya" (narasi LLM dari data terstruktur, tidak mengubah hasil), dan **tidak berangkat** bila ada item
   merah ATAU tidak tersebut (`null`/`unavailable`/basi). Tidak berangkat = keputusan yang dicatat, dengan item penahan.
2. Track: T3 skill `preflight(symbol)` (satu shape: items + decision + blocking) · T1 agen practice-trade dengan plan ATR RYO,
   kartu panggilan + replay · T2 layar watchlist diurutkan berdasar daya veto item penahan + kartu panggilan keyboard-first.
3. Social = kartu panggilan publik per keputusan (public reasoning). Reputation (opsional, hanya bila n cukup) = item mana yang
   paling sering menyelamatkan (grading outcome dari perekam).
4. **Bunryō** (ukuran posisi dari reliabilitas bersyarat rezim) = item ke-11 opsional, hanya bila perekam menghasilkan n≥30 per
   kelas sebelum 6 Sep. Bukan jalur kritis.
5. Dibuang permanen: Honest Fill, what_changed sebagai produk, RYO-vs-RYO, TTL, Fork, debat, KOL. ADR-010/011 ditutup oleh ADR ini.
Konsekuensi: perekam (0.5) tetap nyala hari-1 untuk lapisan opsional; paket `preflight/` menggantikan `honest_fill/` & `calibration/`;
risiko yang tersisa adalah PENILAIAN ("real thinking" bisa dianggap aturan statis) → dijawab oleh item kelengkapan/kesegaran data
(veto karena *tidak tahu*) dan demo dua tanggal dengan keputusan berbeda.

## ADR-013 (DITERIMA 1 Sep 2026) Tidak ada spec skill T3 → output skill meniru envelope RYO; satu BUIDL
Konteks: penyelenggara: tidak ada format skill T3 (T3 = tool yang builder anggap dibutuhkan RYO); rubric T3 "follows the spec 15",
"could we use it 20", "predictable shape, minimal work to ship". Penyelenggara juga: maksimal SATU BUIDL per tim.
Keputusan: `SkillResult` = cermin envelope tool RYO (schema_version, tool, status, data_mode, as_of, request, data, summary,
availability, warnings) + `simulated`. Skill `preflight` bisa dipasang di katalog RYO tanpa mengubah klien mereka. Satu BUIDL, tiga track,
satu repo; tidak ada split (ADR-002 batal).
Konsekuensi: core/skill.py mengikuti §3 spec v2; README menunjukkan diff bentuk output vs `analyze_token` (harus identik di level envelope).

## ADR-014 (DITERIMA 1 Sep 2026) Gaya awwwards di SEMUA layar; library JS di-vendor lokal, tanpa CDN, tanpa Node
Konteks: pemilik proyek memutuskan estetika awwwards (3D + GSAP + smooth scroll) untuk seluruh layar, bukan hanya
landing page. ADR-001 mengunci Python murni tanpa Node/npm; versions.md lama mengandaikan "Chart.js dari CDN".
spec §7 langkah 5 menuntut `make demo` jalan dari fixture offline dengan dua run identik, TASKS 4.5 menguji keadaan
host mati, dan TASKS 4.6 menuntut navigasi keyboard penuh. Verifikasi lisensi/versi: verifikasi API 1 Sep 2026.
Keputusan:
1. Gaya awwwards diterapkan ke Layar A (watchlist) dan Layar B (kartu panggilan), bukan hanya landing.
2. Library di-vendor apa adanya ke `ui/static/vendor/` — TANPA CDN, TANPA npm, tanpa build step (ADR-001 utuh).
   Pin: three **0.170.0** (MIT), gsap **3.15.0** + ScrollTrigger (GSAP Standard "No Charge" License, Webflow),
   lenis **1.1.0** (MIT). three ditahan di 0.170.0 karena build-nya masih satu file self-contained; 0.185.x
   memecah `three.core.js` sehingga vendoring jadi berpasangan.
3. GSAP diterima meski BUKAN open source: gratis selama end user tidak ditagih (produk ini tidak menagih apa pun),
   ScrollTrigger termasuk, tanpa kewajiban atribusi — tetapi header copyright di file WAJIB utuh dan GSAP dicatat
   di bagian terpisah DISCLOSURE.md, bukan di tabel "Open-source dependencies".
4. Nol permintaan jaringan dari halaman UI saat render. Ini termasuk jebakan bawaan resep awwwards: decoder DRACO,
   transcoder KTX2, dan font `*.typeface.json` yang defaultnya diambil dari unpkg saat runtime. Dijaga tes.
5. Aksesibilitas tidak dinegosiasikan meskipun gayanya berat: `prefers-reduced-motion` mematikan animasi, seluruh
   data terjangkau keyboard, dan animasi tidak boleh menahan akses ke informasi. Alasannya bukan selera — rubric T2
   menilai "Works for everyone 10" dan juri menguji Layar B dalam 30 detik; yang dinilai keterbacaannya, bukan efeknya.
Konsekuensi: DISCLOSURE.md mendapat bagian baru "Third-party proprietary (no-charge license)"; `ui/static/vendor/gsap/
LICENSE.txt` harus dibuat manual karena upstream tidak menyediakan file lisensi sama sekali; kompatibilitas
ScrollTrigger 3.12.5 → 3.15.0 BELUM DIVERIFIKASI (changelog resmi tidak dapat diambil) sehingga wajib diuji visual di
3.2; Chart.js gugur — grafik memakai SVG/canvas inline; `sources.yaml` tidak bertambah karena file vendored bukan
sumber runtime eksternal.

## ADR-015 (DITERIMA 6 Sep 2026; diusulkan 1 Sep 2026) `SkillResult` mengikuti envelope RYO NYATA: `schema_version="1.0"`, `simulated` satu-satunya field tambahan
Konteks: `docs/spec.md` §3 menulis `schema_version: str = "1"`. Itu ditulis SEBELUM sampel live TASKS 0.3 ada.
Keenam `docs/samples/*.json` memakai `"1.0"` dan berbagi sepuluh field top-level yang persis sama
(`schema_version, tool, status, data_mode, as_of, request, data, summary, availability, warnings`) — diverifikasi
1 Sep 2026. Sementara itu ADR-013 menuntut skill kita bisa dipasang di katalog RYO tanpa mengubah klien mereka,
dan `docs/rules-compliance.md` mewajibkan `simulated: true` di setiap respons API — padahal envelope RYO tidak
punya field itu. AC TASKS 1.3 ("field top-level identik") karena itu tidak bisa dibaca harfiah: dua kewajiban
yang sah saling bertabrakan.
Keputusan (usulan):
1. `SkillResult.schema_version` default **`"1.0"`**, mengikuti fakta sampel, bukan teks spec.
2. Bentuk kita = **superset aditif**: seluruh sepuluh field RYO ada dengan nama & tipe sama, ditambah TEPAT SATU
   field baru, `simulated`. Klien RYO membaca per-nama sehingga field ekstra tidak merusak; menghapusnya justru
   melanggar rules-compliance.
3. "Field top-level identik" di AC 1.3 dibaca sebagai invarian himpunan yang diuji mekanis terhadap
   `docs/samples/analyze_token.json`: `RYO_TOP_LEVEL ⊆ milik kita` DAN selisihnya tepat `{"simulated"}`.
4. (ditambahkan setelah verifikasi verifikasi API 1 Sep 2026) Dua penyimpangan lain dari teks spec §3 ikut diputuskan
   di sini, supaya tidak ada perubahan diam-diam: (a) spec menulis `as_of: datetime` non-null, kode memakai
   `datetime | None` karena cabang `unavailable` tidak punya bukti dan karenanya tidak punya waktu — memaksa
   `now()` di situ akan melanggar determinisme ADR-003 dan merusak AC "dua run byte-identik"; (b) spec tidak
   memberi default untuk `data_mode`, kode memakai `"unknown"` — nilai jujur ketika belum ada bukti sama sekali.
   Catatan fakta: RYO sendiri TIDAK PERNAH mengeluarkan `status: unavailable`, `data: null`, maupun `as_of: null`
   (0 dari 476 amatan: 6 sampel + 470 snapshot perekam), jadi ketiga cabang itu milik kita dan tidak punya
   padanan RYO untuk dibandingkan. Itu konsekuensi yang diterima, bukan kelalaian.
5. `as_of` diserialisasi pada presisi DETIK (`...THH:MM:SSZ`), mengikuti 476/476 amatan RYO. `snapshots.ts_utc`
   menyimpan mikrodetik, jadi tanpa pemotongan eksplisit envelope kita akan berbentuk yang tidak pernah dipakai RYO.
Konsekuensi: `docs/spec.md` §3 perlu dikoreksi (`"1"` → `"1.0"`) oleh manusia — agen tidak mengubah spec.
Penambahan field top-level baru di kemudian hari butuh ADR sendiri. README menampilkan diff bentuk terhadap
`analyze_token` sebagaimana diminta ADR-013.
Status: kode 1.3 dibangun memakai `"1.0"` karena itu yang terverifikasi dari sumber primer; bila manusia menolak,
yang berubah hanya satu konstanta + satu tes.

## ADR-016 (DITERIMA 6 Sep 2026; diusulkan 1 Sep 2026) Dua generasi di data/ryo.db: dilaporkan STALE_ALGO, tidak dihapus, tidak disembunyikan
Konteks: `data/ryo.db` memuat 19 baris `decisions` + 9 `trade_events` yang ditulis implementasi yang
dihapus 1 Sep 2026. `blocking_json`-nya berisi integer (`'[5,6,10]'`) sedangkan `core/replay.py`
mewajibkan id item bertipe `str` → `ReplayError`; `card_json`-nya tidak memuat `algo`; `params_hash`-nya
dihitung algoritma yang tidak ada lagi. ADR-006 (append-only) melarang penghapusan. Produk ini menjual
"replay identik", jadi `make replay ID=13` yang meledak akan terbaca sebagai tamper — itu persis
pertanyaan juri #4 di `docs/judge-reports/fase-1.md`.
Keputusan:
1. Baris `decisions` yang `card_json`-nya TIDAK memuat kunci `algo` dilaporkan `STALE_ALGO` dengan
   `algo: null` dan kalimat sebab yang menyebut penghapusan implementasi. Bukan `ReplayError`, bukan
   MISMATCH. Kosakata verdict replay menjadi tiga: IDENTICAL · MISMATCH · STALE_ALGO. Kait ini sudah
   dirancang di TASKS 1.4; ADR ini menghidupkannya lebih awal dari yang diduga.
2. Pembeda generasi HANYA ada/tidaknya `algo` di `card_json`. Tidak ada kolom baru, tidak ada migrasi,
   tidak ada ambang id sebagai angka ajaib.
3. Baris warisan tidak dihapus, tidak ditulis ulang, tidak di-backfill, dan tidak disembunyikan dari
   `/api/decision/{id}`. Yang dikecualikan hanya dari agregat yang akan berbohong bila memuatnya:
   watchlist, daftar keputusan, dan lipatan posisi ledger — masing-masing dengan alasan tertulis.
4. Ditolak: menulis baris penanda batas generasi ke store. Append-only membuatnya sah secara mekanis,
   tetapi baris `decisions` yang bukan keputusan adalah data fabrikasi (spec §8 "tidak dinilai").
5. Ditolak: menyembunyikan 19 baris itu. PRD §2 — yang tidak tersebut wajib disebut sebagai tidak
   tersebut; menyembunyikannya adalah kebalikan dari tesis produk.
Konsekuensi: AC TASKS 2.4 berubah dari dua verdict menjadi tiga (task 2.4a/2.4b); `ui/` mendapat satu
cabang tampilan (3.1a); README memuat satu paragraf "dua generasi". Kenaikan versi algoritma di masa
depan memakai jalur yang sama dan tidak akan pernah tampak sebagai tuduhan tamper.

**Perluasan 4 Sep 2026 (TASKS 0.14), bukan ADR baru.** ADR ini menamai SATU generasi: baris tanpa
`algo`. Ia tidak menjawab baris yang PUNYA `algo` tetapi ditulis dengan `params_hash` lain. Sampai
0.14 baris seperti itu dilaporkan `MISMATCH` — vonis yang di `agent/replay.py` berarti "bukti tidak
mereproduksi keputusan", yaitu sebuah temuan. Untuk baris yang aturannya berbeda, temuan itu tidak
bisa kami dukung: `decision_hash` memakai params yang berlaku sekarang, jadi buktinya belum diperiksa.
Vonis `STALE_PARAMS` ditambahkan untuk menyatakan ketidaktahuan itu, keluar 0 seperti `STALE_ALGO`,
dengan urutan yang mengikat: `STALE_ALGO` → `STALE_PARAMS` → sisanya.

Konsekuensi yang ditulis apa adanya: baris **nol-bukti** kehilangan vonis `NO_EVIDENCE`-nya begitu
params bergeser, karena cabang itu bersyarat `computed == stored` dan `computed` memakai params
sekarang. Memindahkan urutan cabangnya tidak menolong — ia menjadi tak terjangkau, bukan terlambat
(diuji, bukan disimpulkan). Melonggarkan syaratnya menjadi `not frozen_ids` saja akan membuka kembali
temuan SEDANG-1 tinjauan keamanan 4 Sep, jadi syarat itu dipertahankan.

## ADR-017 (DITERIMA 6 Sep 2026; diusulkan 2 Sep 2026) Keadaan ketiga spec §4: `neutral` = hijau tanpa konfirmasi
**Status: DITERIMA** oleh pemilik repo 6 Sep 2026 (TASKS 2.1c). Kode 2.1 sudah menerapkannya sejak
2 Sep; yang berubah hari ini adalah ALASANNYA: `Params.neutral_stance_status` kini merujuk ADR ini,
bukan lagi "bacaan harfiah kolom Merah" spec §4. Nilai default (`green`) tidak berubah, jadi
`params_hash` TIDAK bergeser dan tidak ada baris `decisions` yang menjadi `STALE_PARAMS`.
Knob `Literal["green", "red"]` sengaja DIPERTAHANKAN: bila RYO menerbitkan enum resmi yang
menyatakan `neutral` bermakna bearish, ADR ini dicabut dengan mengubah satu nilai, dan pergeseran
`params_hash` yang menyertainya justru yang membuat keputusan lama tetap jujur di-replay.

Konteks: spec §4 hanya punya kolom Hijau ("searah plan") dan Merah ("berlawanan"). Data nyata
(`data/ryo.db`, 756 baris, dibaca `mode=ro`, 2 Sep 2026) justru DIDOMINASI keadaan ketiga:
`market_overview.data.regime` `neutral` 13/19 (68%) · `deep_analysis.data.verdict.call` `neutral`
144/294 (49%) · `analyze_token.data.verdict` `neutral` 99/407 (24%); item 10 "terakhir di momentum saja"
juga tidak punya baris di spec. Setiap field itu SUDAH punya kosakata keberatan tersendiri yang teramati
terpisah: `caution — overbought` 118× · `avoid` 5× · `cautious` 15× · `risk_off` 0× (ada di spec, belum
pernah dikeluarkan RYO).
Keputusan:
1. `neutral` (dan padanan struktural di item 10) → item **hijau**, dengan `reason` yang menyatakan
   "not opposed to the plan, but not a confirmation either" dan `value.stance = "neutral"`. Hijau dalam
   Tenko berarti **"tidak ada keberatan"**, bukan "mendukung" — persis seperti kolom Hijau item 4
   (`material_shift=false` ATAU searah) dan kolom Merah item 10 (terakhir di KEDUA faktor) yang ditulis
   manusia di spec §4.
2. `neutral` → `red` DITOLAK: itu **mengarang keberatan yang sumbernya menolak nyatakan** — satu keluarga
   dengan mengarang nilai `crowded` untuk item 8, hanya terbalik arahnya. Konsistensi anti-halusinasi
   harus berlaku dua arah.
3. `neutral` → `unspoken` DITOLAK: `core/skill.py` mewajibkan item `unspoken` tidak membawa `value`, jadi
   jawaban nyata RYO akan HILANG dari kartu — kita melaporkan "tidak terjawab" untuk panggilan yang
   terjawab. Itu juga membuat `unspoken` berarti dua hal sekaligus dan merusak dasar klaim produk serta
   jawaban untuk pertanyaan juri #1 ("NO_GO yang penyebabnya angka yang TIDAK ADA").
4. Satu knob seragam `Params.neutral_stance_status`; TIDAK ada nilai per item tanpa bukti yang
   membedakannya. Knob ikut `params_hash`.
5. Kompensasi WAJIB: daya pisah tiap item dilaporkan empiris dengan n (TASKS 2.1b) dan dirender berbeda
   di kartu (TASKS 3.2). Item yang 0× merah DAN 0× unspoken atas n penuh disebut apa adanya — tidak
   disembunyikan dan tidak dibuat bergigi buatan dengan menambah ambang.
Konsekuensi: NO_GO datang dari item yang benar-benar berbicara (verdict abstain 31% pada item 5,
confluence MIXED 43%, kesegaran, kelengkapan, rr, relatif), bukan dari mayoritas yang diam. Bila RYO kelak
menerbitkan enum resmi yang menyatakan `neutral` bermakna bearish, ADR ini dicabut, `params_hash` berubah,
dan keputusan lama tetap bisa di-replay dengan params lamanya.

## ADR-018 (DITERIMA 6 Sep 2026; diusulkan 3 Sep 2026, ditulis 4 Sep 2026) Agen store-first murni: fallback live DICABUT
**Status: USULAN — menunggu keputusan manusia.** Jangan dibaca sebagai DITERIMA. Kode yang
mengandalkannya (`agent/main.py`, `scripts/daemons.sh`) menyebut status ini apa adanya dan
dipatok tes (`tests/test_agent.py::test_main_docstring_states_the_deviation_and_points_at_adr_018`).

Konteks: `docs/spec.md` §5 menulis "**Sumber snapshot: store dulu** … live hanya bila tidak ada yang
segar", dan TASKS 2.0b semula meminta agen menjadi pemanggil live kedua di belakang
`core/livelock.py`. Tiga fakta menggeser perhitungannya, semuanya muncul SESUDAH spec ditulis:
1. Kunci API RYO punya **satu** token bucket 60 panggilan/menit (docs/api-facts.md §A) yang dibagi
   seluruh proses di mesin ini; perekam sudah memakainya terus-menerus, dan 429 sudah pernah terlihat
   di log perekam.
2. Satu preflight yang semuanya live ≈ **60 detik** (api-facts §A "Latensi terukur": `deep_analysis`
   17–34 dtk, `compare_tokens` ≈40 dtk) — di depan juri itu bukan demo, itu jeda.
3. TASKS 2.2 sudah menjadikan `preflight` **store-first murni** dan naskah demo §7 berbasis fixture
   offline, sehingga kemampuan live agen tidak dipakai satu langkah demo pun.
Ditambah temuan tinjauan keamanan 2.0a (2 Sep 2026): resolusi path kunci berbeda antara perekam
(`load_dotenv`) dan agen (tidak), sehingga `RECORDER_LOCK_PATH` di `.env` membuat **kedua** pemanggil
menang atas kunci yang sama — dua pemanggil live atas satu kuota, persis kegagalan yang kunci itu ada
untuk mencegahnya.

Keputusan (usulan penjagaan ruang lingkup, dilaksanakan di TASKS 2.0b-lite + 2.4):
1. `agent/` **tidak pernah** mengonstruksi klien live dan tidak mengimpor `core.ryo_client`. Bukti yang
   tidak cukup segar menjadi item `unspoken` → `NO_GO` ber-`reason` yang menyebut **store** ("no snapshot
   within N min in the store"), **bukan** "kuota dipegang perekam": tidak ada yang memegang apa pun,
   karena tidak ada pemanggil kedua. Bila tidak ada satu pun snapshot beku, agen **tidak menulis baris
   `decisions` sama sekali** (lihat `agent.main.InsufficientEvidence`).
2. Opsi CLI `--client live` **dicabut** dan ditolak terang-terangan (exit ≠ 0), bukan diabaikan diam-diam.
3. `core.livelock.try_acquire` **tetap ada tanpa pemanggil produksi**. Itu disengaja dan ditulis di
   docstring modulnya: ia adalah pengaman yang siap dipakai bila ADR ini ditolak, bukan kode mati.
4. AC lama 2.0b (1)(3)(5)(6) — yang mengukur agen sebagai pemanggil live — **gugur bersama kemampuannya**;
   penggantinya AC 2.0b-lite (nol panggilan live dibuktikan dengan `socket.connect` diblokir).

Konsekuensi: satu-satunya sumber bukti agen adalah perekam, sehingga **uptime perekam menjadi jalur
kritis produk** — celah rekaman langsung menjadi "tidak tersebut" di kartu (terukur: harga tersedia
≤120 mnt hanya 51,8–57,5 % waktu, docs/api-facts.md §A "Harga …"). Itu ditanggung sadar: lebih baik
kartu yang jujur berkata "tidak tahu" daripada demo yang berhenti 60 detik atau kena 429. Bila ADR ini
DITOLAK manusia, yang dikembalikan bukan hanya `--client live` melainkan juga: satu resolusi path kunci
yang dibagikan agen+perekam, log path kunci di kedua proses, dan `reason` yang membedakan "dipegang
perekam" dari "kunci tidak bisa dibuka" (AC 2.0b lama (5)(6)).

## ADR-019 (DITERIMA 6 Sep 2026) Narasi kartu pindah ke Google Gemini, dan berhenti mengirim nilai RYO
Konteks: narasi kartu memakai satu LLM **di luar jalur keputusan** (ADR-003) — satu panggilan per kartu,
sesudah keputusan final, menempel di `summary.narration` bukan `data`. Tanpa kredensial hasilnya
`narration_unavailable` dan `decision_hash` tidak bergeser; `docs/spec.md` §7 langkah 4 memperagakan itu.
Basis `approved` di `sources.yaml` adalah "your own AI model" (landing penyelenggara) — **netral-vendor**,
jadi yang belum pernah dinilai bukan pilihan vendor melainkan **ketentuan penggunaan data**.

Pemilik repo tidak memiliki kredensial Anthropic yang hidup (6 Sep 2026). Tanpa LLM apa pun, paruh pertama
§7 langkah 4 ("kartu menampilkan panggilan per item") tidak bisa diperagakan sama sekali.

Fakta terverifikasi dari sumber primer (6 Sep 2026, tercatat `docs/api-facts.md` §B2 dan `docs/versions.md`):
* Paket yang benar **`google-genai` 2.22.0** (Apache-2.0). `google-generativeai` **DEPRECATED, EOL 30 Nov 2025** —
  paket yang paling mungkin ditebak, dan salah.
* Kredensial dibaca dari **dua** nama environment yang berbeda (tercatat di `docs/api-facts.md` §B2.2);
  memeriksa satu saja akan tidak sinkron dengan SDK.
* **Tidak ada padanan** `EFFORT`/`output_config` milik Anthropic. Yang ada `thinking_level`/`thinking_budget`
  dengan semantik berbeda. Jangan cari yang mirip.
* Keluaran JSON terstruktur didukung (`response_mime_type` + `response_schema`).
* Ketentuan tier gratis, kutipan apa adanya: *"Google uses the content you submit … to provide, improve, and
  develop Google products and services and machine learning technologies"* · *"human reviewers may read,
  annotate, and process your API input and output"* · *"**Do not submit sensitive, confidential, or personal
  information to the Unpaid Services.**"* Tabel harga: `Used to improve our products | Yes (Free) | No (Paid)`.

Keputusan:
1. Penyedia narasi pindah dari `anthropic` ke `google-genai`. `sources.yaml` `provider` diubah, dan `approved`
   turun ke `null` sampai perpindahannya mendarat lengkap — stempel approval lama tidak boleh dipakai untuk
   sesuatu yang belum dinilai (ADR-005).
2. **`evidence_payload` berhenti mengirim `item.value` DAN `item.reason`.** Yang dikirim hanya `item_id`,
   `status`, dan nama `source.tool`/`source.field`. Alasannya: keduanya memuat nilai keluaran tool RYO
   (`verdict`, persentil, `regime`, `crowding_state`; `reason` menginterpolasinya, mis. `rsi_14=70.4`), dan
   `docs/rules-compliance.md` membatasi data itu untuk submission. Google menyuruh eksplisit jangan mengirim
   informasi rahasia ke tier gratis, dan kita **tidak berada dalam posisi memutuskan kerahasiaan data
   penyelenggara atas nama mereka**. Membuang keduanya membuat pertanyaan itu tidak perlu dijawab —
   dan berlaku terlepas dari penyedia mana pun, sekarang atau nanti.
3. Nama model dan nama penyedia **DILARANG** masuk `Params` atau `decision_hash`. `params_hash` tetap
   `3d39fb1b…` (beku, TASKS 5.0).
4. Bila Interactions API dipakai: `store=false` eksplisit (bawaan `store=true`; retensi tier gratis 1 hari).
5. Fallback tidak berubah: tanpa kredensial → `narration_unavailable`, kartu tetap lengkap, hash tetap.

Konsekuensi, dan yang dikembalikan bila kelak DITOLAK: `sources.yaml` (`provider`, `approved`) ·
`preflight/narrate.py` (`MODEL`, nama env kredensial, sembilan kelas error SDK, `evidence_payload`) ·
`pyproject.toml` + `uv.lock` (pohon transitif berubah total — `docstring-parser`/`jiter` masuk karena
`anthropic`; `certifi` MPL-2.0 dan `cryptography` Apache-2.0-OR-BSD-3-Clause masuk karena `google-genai`) ·
`DISCLOSURE.md` (15 baris dependensi baru) · `docs/versions.md` · `tests/test_narrate.py` (termasuk penjaga
mekanis ADR-003 yang mencari nama paket).

Risiko yang diterima sadar: dengan muatan tipis, **narasi mungkin tidak menambah apa pun** di atas yang sudah
tertulis di kartu. Bila pengukuran menunjukkan begitu, jawaban yang jujur adalah mematikan narasi dan
menyatakannya — bukan menyalakannya demi kotak centang. Batas waktu percobaan: **7 Sep 12:00 JST**; lewat itu
kembalikan ke narasi mati dan kirim.

BELUM DIJAWAB penyelenggara (§E): apakah keluaran tool RYO boleh dikirim ke LLM pihak ketiga yang memakai data
untuk pelatihan. Keputusan ini sengaja dibuat agar **tidak bergantung** pada jawaban itu.

## ADR-020 (DITERIMA 6 Sep 2026) Frontend TypeScript yang dinamis; Node menjadi alat BUILD, bukan runtime
Konteks: pemilik repo meminta (6 Sep 2026, eksplisit dan diulang setelah keberatan dicatat) frontend ditulis ulang
dengan **TypeScript** dan dibuat **dinamis**, dengan getlayers.ai sebagai rujukan visual. ADR-001 mengunci "Python
murni, tanpa Node" dan ADR-014 menegaskan "tanpa CDN, tanpa npm, tanpa build step". Keberatan yang diajukan
koordinator sebelum keputusan diambil, dan yang tetap berlaku sebagai risiko yang diterima: deadline 8 Sep 2026,
laporan juri 5.2 (T1 82 · T2 85 · T3 80) menilai UI yang ADA, checklist a11y 4.6 menggambarkan layar yang ADA,
dan `Content-Security-Policy: default-src 'none'` hari ini melarang skrip sama sekali. Pemilik repo mengulang
permintaannya sesudah membaca semua itu; ini keputusannya, bukan kelalaian.

Keputusan:
1. **ADR-001 dan ADR-014 DIPERSEMPIT, tidak dicabut.** Yang gugur hanya kalimat "tanpa Node/npm/build step"
   untuk lapisan presentasi. Yang TETAP berlaku utuh: tidak ada Next/pnpm, tidak ada CDN, tidak ada framework
   runtime, dan Python tetap satu-satunya yang melayani permintaan.
2. **Node/npm adalah alat BUILD saja, dan tidak pernah dibutuhkan untuk menjalankan Tenko.** Hasil build
   (`ui/static/app.js` + `ui/static/app.css`) **dilacak git dan ikut terkirim**. Clone bersih tanpa Node sama
   sekali harus tetap bisa `make ui`, `make test`, dan `make demo`. Ini syarat penerimaan, bukan harapan:
   ada tes yang gagal bila jalur runtime menyentuh Node. Alasannya langsung — mesin juri belum tentu punya
   Node, dan ADR-001 lahir persis dari kekhawatiran itu.
3. **Tanpa framework runtime.** TypeScript + DOM apa adanya. Bundler: **esbuild** (satu binary,
   pohon transitif nyaris nol) — bukan Vite/webpack, supaya baris `DISCLOSURE.md` tetap bisa dibaca manusia.
   Dependency runtime npm: **nol**.
4. **"Dinamis" didefinisikan sempit dan jujur**: klien mengambil dari API JSON yang SUDAH ada
   (`/api/watchlist`, `/api/decisions`, `/api/decision/{id}`, `/api/preflight/{symbol}`), berpindah antar Layar A
   dan Layar B tanpa muat ulang, menyaring/mengurutkan di klien, dan menyegarkan berkala. Ia TIDAK menghitung
   apa pun sendiri: setiap angka yang tampil berasal dari respons API. Tidak ada logika keputusan di klien
   (ADR-003 utuh), dan `unspoken` tetap dirender sebagai `unspoken` — tidak pernah 0, tidak pernah kosong.
5. **CSP dilonggarkan seminimal mungkin**: `script-src 'self'` menggantikan larangan total, sisanya tetap
   (`default-src 'none'`, `img-src 'none'`, `base-uri 'none'`). Tanpa `unsafe-inline`, tanpa `unsafe-eval`,
   tanpa host pihak ketiga. Skrip dan gaya disajikan dari origin sendiri sebagai berkas terpisah.
   *Koreksi (tinjauan keamanan, 2026-09-06 — bukan keputusan baru):* butir ini kurang satu direktif.
   `connect-src` adalah fetch-directive yang fallback-nya `default-src`, jadi `default-src 'none'`
   tanpa menyebutnya = `connect-src 'none'` = setiap `fetch()` klien ditolak browser, termasuk ke
   origin sendiri — butir 4 tidak akan pernah jalan. CSP terpasang karena itu juga memuat
   `connect-src 'self'`, dan tidak lebih dari `'self'`.
6. **Banner SIMULATED wajib dirender oleh server**, bukan oleh klien. Klien yang gagal memuat tidak boleh
   menghasilkan halaman tanpa banner: HTML kerangka sudah membawa banner sebelum satu baris JS pun jalan.
   Ini kewajiban `docs/rules-compliance.md`, dan ia tidak boleh bergantung pada JavaScript.
7. **Jaminan 4.6 (a11y) tidak turun**: Tab/Enter/Esc, fokus terlihat, `aria-label` bernilai+bersumber,
   kontras ≥4.5:1, tanpa hover-only — semuanya wajib tetap terbukti oleh tes sesudah penulisan ulang.
   Konten dinamis wajib memakai `aria-live` supaya perubahan terdengar pembaca layar.
8. **Rujukan visual getlayers.ai dipakai untuk ARAH, bukan disalin**: gelap, kontras tinggi, kartu bergrid,
   ritme spasi longgar, gerak halus. Nol aset, nol font, nol CSS diambil dari sana.

Konsekuensi: `ui/app.py` (CSP + kerangka HTML) · `ui/screens.py` (server-render menyusut jadi kerangka + banner) ·
`frontend/` (baru: sumber TypeScript) · `ui/static/` (baru: hasil build yang dilacak) · `package.json` +
`package-lock.json` (baru) · `Makefile` (target build) · `docs/versions.md` (pin Node/npm/typescript/esbuild) ·
`DISCLOSURE.md` (baris dependensi baru) · `tests/test_ui.py` (tes yang memaku "nol `<script>`" WAJIB ditulis
ulang jadi tes yang memaku CSP baru — jangan dihapus, jangan dilonggarkan).

Risiko yang diterima sadar: laporan juri 5.2 menilai UI lama, jadi skor T2 85 **tidak lagi berlaku** sampai
`hackathon-judge` dijalankan ulang atas UI baru. Bila penulisan ulang tidak selesai sebelum 8 Sep, jalan
mundurnya adalah `git revert` commit-commit UI — bukan mengirim layar setengah jadi.

## ADR-021 (DITERIMA 6 Sep 2026) `/` menjadi landing page; aplikasi pindah ke `/app`; `/ui` tetap hidup sebagai alias
Konteks: pemilik repo meminta (6 Sep 2026, sesudah keberatan dicatat dan diulang) landing page yang menjual di `/`,
dengan Layar A/B di `/app`. Ini MEMBUKA ULANG TASKS 3.5 — yang ditulis pemilik repo sendiri pada hari yang sama —
dan karena itu butuh ADR, bukan perubahan diam-diam.

Keberatan yang diajukan sebelum keputusan, dan yang tetap berlaku sebagai risiko yang diterima:
1. Rubric resmi (spec §8) tidak memberi satu poin pun untuk landing page. 100 = 40 umum + 60 track; T2 =
   Easy to read 25 · Sorted by importance 25 · Works for everyone 10. Daftar "Tidak dinilai" memuat
   "tembok chart tanpa argumen" — hero section masuk kategori yang sama.
2. Alasan TASKS 3.5 masih berlaku apa adanya: pemilik repo membuka `127.0.0.1:8000`, melihat indeks JSON, dan
   mengira itu seluruh frontend. Landing di `/` memasang satu langkah lagi antara juri dan produk, dan juri
   punya 30 detik tanpa siapa pun yang memandu.
3. Sisa waktu (deadline 8 Sep) lebih dibutuhkan TASKS 4.6 (Works for everyone 10 — sepuluh poin rubric yang
   belum diklaim) dan 5.0 (bekukan params; prasyarat rekaman video).
Pemilik repo mengulang permintaannya sesudah membaca semua itu; ini keputusannya, bukan kelalaian.

Keputusan:
1. **`GET /` = landing page.** 200 HTML, BUKAN 3xx — alasan ADR-020/TASKS 3.5 tidak berubah: `curl` tanpa `-L`
   menerima badan kosong dari 302, sehingga respons pertama yang dilihat pemanggil akan jadi respons tanpa
   banner SIMULATED, dan `docs/rules-compliance.md` menuntut banner itu di layar yang dilihat orang.
2. **`GET /app` = Layar A, `GET /app/decision/{id}` = Layar B — dan itu yang KANONIK.** Landing wajib membawa
   tautan ke `/app` di atas lipatan, sebagai hal pertama yang bisa diklik; kalau juri hanya membaca satu layar,
   ia harus sampai ke aplikasi dalam satu klik.
3. **`GET /ui` dan `/ui/decision/{id}` TETAP dilayani** sebagai alias (bukan redirect), karena README,
   `agent/demo.py`, dan `scripts/chaos.sh` menunjuk ke sana. Mematikannya berarti mengubah tiga berkas lain
   plus laporan chaos di tengah minggu deadline, tanpa satu pun poin rubric sebagai imbalan.
4. **Aset TIDAK dipindah**: `/ui/tenko.css` dan `/ui/app.js` tetap di tempatnya, dipakai landing dan aplikasi.
   `/ui` dengan begitu berperan ganda (prefix aset + alias layar). Itu ketidakrapian yang DITERIMA SADAR:
   memindahkannya menyentuh ~15 asersi tes dan dua berkas sumber, dan tidak mengubah apa pun yang dinilai.
5. **Landing tidak punya gambar.** CSP `img-src 'none'` (ADR-020 butir 5) melarang setiap gambar, termasuk yang
   di-host sendiri; melonggarkannya butuh ADR tersendiri dan TIDAK dilakukan di sini. Grafis landing karena itu
   **inline SVG** — markup, bukan fetch — mengikuti preseden `BANNER_ICON`. Nol permintaan jaringan tetap utuh.
6. **Landing adalah layar INERT bagi klien.** `data-screen="landing"`; `main.ts` berhenti di situ persis seperti
   pada `error`: tidak mengambil apa pun, tidak menyegarkan apa pun, tanpa bar kontrol dan tanpa timer polling.
   Tanpa butir ini `start()` akan jatuh ke cabang watchlist (`main.ts:338`) dan melukis Layar A di atas landing.
7. **Banner SIMULATED tetap dirender server di landing**, sama seperti layar lain (ADR-020 butir 6). Landing
   memakai `_shell` yang sama; ia tidak mendapat kerangka sendiri.
8. **Gaya: papan informasi kereta Jepang** (rujukan pemilik repo, 6 Sep). Alasannya bukan selera: Tenko (点呼)
   dan "tunjuk & sebut" ADALAH 指差喚呼, prosedur keselamatan kereta Jepang — jadi bahasa visualnya milik produk
   ini, bukan tempelan. Aturan yang diambil: urutan kolom tetap, maksimal 4 ukuran teks, hijau/amber/merah
   HANYA sebagai status, warna jenuh di bawah ~20% area. Yang TIDAK diambil: rotasi bahasa tiap beberapa detik —
   ia menyembunyikan informasi di balik timer dan akan menggagalkan uji 30 detik spec §8.

Konsekuensi: `ui/app.py` (route `/`, `/app`, `/app/decision/{id}`; `/api/routes` menyebut `/app`) ·
`ui/screens.py` (`render_landing`, `LANDING_CLIENT_NOTE`) · `frontend/src/main.ts` (layar inert) ·
`tests/test_ui.py` (tes yang memaku `/` sebagai Layar A dan `screens.A_watchlist == "/"` WAJIB ditulis ulang
jadi tes yang memaku niat baru — jangan dihapus, jangan dilonggarkan) · README (peta route) · TASKS 3.5 (AC
diamandemen). Nol dependency baru, nol baris `DISCLOSURE.md`, nol perubahan `sources.yaml`.

Risiko yang diterima sadar: laporan juri 5.2 menilai `/` sebagai Layar A. Bila landing tidak selesai sebelum
8 Sep, jalan mundurnya `git revert` commit landing — `/app` dan `/ui` sudah berdiri sendiri, jadi revert itu
mengembalikan `/` ke Layar A tanpa menyentuh apa pun yang lain.

## ADR-022 (DITERIMA 6 Sep 2026) `img-src`, `font-src`, `media-src` dibuka ke `'self'` — aset boleh, pihak ketiga tetap tidak
Konteks: pemilik repo meminta (6 Sep 2026) larangan gambar, font, dan media dicabut supaya landing ADR-021 bisa
memakai aset. Hari ini `img-src 'none'` melarang setiap gambar termasuk yang di-host sendiri, dan `font-src`
maupun `media-src` tidak pernah disebut sehingga jatuh ke `default-src 'none'` — artinya webfont dan video ikut
mati tanpa satu baris pun yang menyatakannya. ADR-020 butir 5 menetapkan CSP itu dan menyebut dirinya
"dilonggarkan seminimal mungkin", jadi mengubahnya butuh ADR, bukan sunting diam-diam.

Keputusan:
1. **Tiga direktif dibuka, semuanya ke `'self'`**: `img-src 'self'`, `font-src 'self'`, `media-src 'self'`.
2. **BUKAN `*` dan BUKAN `data:`.** Ini pembatas yang membuat butir 1 aman, dan alasannya dua:
   (a) `'self'` menjaga janji **nol permintaan jaringan saat render** (ADR-014 butir 4) yang membuat `make demo`
   bisa jalan tanpa jaringan (spec §7) dan yang menjauhkan repo ini dari CDN; aset pihak ketiga tetap ditolak
   browser, bukan hanya oleh sopan santun. (b) `data:` menghidupkan kembali permukaan penyuntikan lewat nilai
   asal-RYO/DB yang seluruh `ui/screens.py` dibangun untuk menutup.
3. **`default-src 'none'` tetap**, dan setiap direktif yang disebut bernilai `'none'` atau `'self'` — tidak ada
   nilai ketiga. Tes memaku daftar direktif yang dilonggarkan **beserta nilainya**, karena daftar yang boleh
   tumbuh diam-diam adalah persis cara CSP berhenti menjaga apa pun.
4. **Setiap aset yang benar-benar masuk repo tetap butuh barisnya sendiri di `DISCLOSURE.md`** pada commit yang
   sama, dengan lisensi dan URL asalnya (CLAUDE.md). CSP yang terbuka bukan izin untuk aset tanpa asal-usul.
5. **Aset dari repo pihak ketiga tidak diambil sebagai aset.** Yang boleh masuk hanya yang lisensinya menyebut
   aset secara eksplisit (mis. SIL OFL untuk font, CC0/domain publik untuk piktogram) atau yang kita buat
   sendiri. Repo bergaya awwwards umumnya MIT **hanya untuk kodenya**, sementara font, video, dan teksturnya
   milik klien aslinya — dan submission hackathon adalah publikasi, sehingga ini urusan `rules-compliance`,
   bukan selera.

Konsekuensi: `ui/app.py` (`HTML_HEADERS`) · `tests/test_ui.py` (dua tes CSP ditulis ulang: daftar direktif yang
dilonggarkan bertambah tiga, DAN asersi baru bahwa setiap nilai adalah `'none'`/`'self'`) · `ui/README.md` tetap
tidak boleh mengutip nilai CSP harfiah (tes menjaganya). Nol dependency baru. Halaman yang ada tidak berubah
sedikit pun: mereka memang tidak memuat gambar, font, atau media, jadi pelonggaran ini menambah kemampuan tanpa
mengubah satu byte pun yang dirender hari ini.

Risiko yang diterima sadar: permukaan serang bertambah tiga jenis sumber daya. Yang mengecilkannya adalah
`'self'` — penyerang harus lebih dulu bisa menaruh berkas di `ui/static/`, dan siapa pun yang bisa melakukan itu
sudah bisa mengganti `app.js`.

## ADR-023 (DITERIMA 7 Sep 2026) Palet = warna jalur kereta Jepang; susunan landing = anatomi awwwards
Konteks: pemilik repo menilai landing ADR-021 "jelek banget" dua kali, dan meminta (7 Sep 2026) dua hal terpisah:
skema warna seperti stasiun/shinkansen Jepang alih-alih abu-abu gelap, dan susunan yang dicari dari awwwards serta
dribbble. Keberatan yang tetap berlaku dan diterima sebagai risiko: deadline 8 Sep, rubric spec §8 tidak memberi
satu poin pun untuk landing, dan TASKS 4.6 (Works for everyone 10) serta 5.0 (bekukan params, prasyarat video)
masih terbuka. Ini keputusan pemilik repo sesudah membaca itu.

Keputusan:
1. **Empat warna status menjadi warna jalur kereta Jepang yang SEBENARNYA**, bukan warna "yang terasa Jepang".
   Sumber: Wikipedia `Template:Ja-rail-color` (dibaca 7 Sep 2026). Masing-masing kebetulan memetakan persis ke
   makna yang sudah dipakai layar ini, jadi tidak ada makna yang dipaksa:
   `--go` **#9acd32** Yamanote (JNR Yellow Green No.6, ウグイス色) · `--nogo` **#f62e36** Marunouchi ·
   `--warn` **#ff9500** Ginza · `--link` **#00bfff** Keihin-Tōhoku.
2. **Latar menjadi navy papan penunjuk stasiun** (`--bg` #0a1220, `--panel` #0f1829, `--row` #0c1523), bukan
   abu-abu netral. Ini yang mengubah kesan dari "dark fintech" menjadi "papan stasiun"; warna jalur di atas
   navy adalah kombinasi yang dilihat orang di peron, bukan yang dikarang di sini.
3. **Seluruh dua belas pasangan DIUKUR ULANG, bukan diasumsikan.** Batas tidak diturunkan sedikit pun: teks
   ≥4.5:1, non-teks ≥3:1. Yang paling ketat sekarang **#f62e36 di atas #0f1829 = 4.53:1** — 0.03 di atas batas.
   Itu margin paling tipis di repo ini dan disebut apa adanya di `ui/README.md`, `frontend/styles/app.css`, dan
   tesnya. **Konsekuensi operasional: `--panel` tidak boleh diterangkan tanpa menghitung ulang.**
4. **Palet ini menggantikan palet 4.6 di SELURUH layar, bukan hanya landing.** Dua palet dalam satu produk adalah
   produk yang terlihat belum selesai, dan Layar A/B justru yang paling diuntungkan: papan keberangkatan dengan
   warna jalur adalah bahasa yang sama dengan isinya.
5. **Susunan landing mengikuti anatomi landing pemenang awwwards**: hero (ruang paling mahal, di atas lipatan) →
   PRODUK → bukti → detail. Yang diambil bukan efeknya melainkan tiga keputusan bentuk: skala berani (judul
   clamp(44px, 8vw, 92px), tracking −.035em, angka bukti setinggi judul), **garis bukan kotak** (panel berbingkai
   di mana-mana adalah bahasa admin template), dan label mono kecil ber-tracking lebar sebagai penanda teknis —
   yang kebetulan juga persis cara papan stasiun menamai kolomnya. TIDAK diambil: animasi gulir, rotasi bahasa,
   dan aset apa pun.
6. **Warna tetap tidak pernah membawa informasi sendirian.** Status di papan memakai warna jalurnya DAN tetap
   mencetak teksnya (`GO`/`NO_GO`), jadi papan terbaca utuh hitam-putih dan oleh pembaca layar. Jaminan 4.6 tidak
   turun satu pun.

Konsekuensi: `frontend/styles/app.css` (palet + blok landing ditulis ulang) · `ui/screens.py` (urutan bagian,
`<colgroup>` papan) · `tests/test_ui.py` (`MEASURED_PALETTE` + tiga angka yang dipatok diperbarui;
`test_the_restyle_did_not_move_a_single_measured_colour` DIGANTI oleh
`test_the_palette_is_the_measured_jr_line_palette_and_nothing_else`, yang kini menjaga ASAL-USUL tiap warna
status, bukan hanya nilainya) · `ui/README.md` (tabel kontras dihitung ulang seluruhnya, bukan disunting tangan) ·
`README.md` (tiga angka). Nol dependency baru, nol aset baru, CSP tidak disentuh.

Catatan yang sengaja TIDAK dilakukan: `docs/judge-reports/fase-5.md` tetap menyebut angka lama. Itu catatan
sejarah tentang UI yang dinilai saat itu; menyuntingnya akan membuat laporan juri berbohong tentang apa yang
benar-benar dilihatnya.

## ADR-024 (DITERIMA 7 Sep 2026) Badan shinkansen: palet TERANG, judul dot-matrix, bilah gulir peta jalur
Konteks: pemilik repo meminta (7 Sep 2026, berturut-turut dalam satu sesi) tiga hal: hero bergaya ohmydesign.com,
bilah gulir bergaya kereta di atas halaman, dan latar TERANG yang menggambarkan shinkansen alih-alih navy gelap
ADR-023. Ini palet ketiga dalam satu hari; ongkosnya nyata (tiap perubahan menuntut hitung ulang seluruh tabel
kontras, tes, dan dua dokumen) dan deadline 8 Sep. Diterima sebagai keputusan pemilik repo.

Keputusan:
1. **Palet dibalik menjadi TERANG**: `--bg` #eef2f7 (badan shinkansen), `--panel` #ffffff, `--row` #e4eaf2,
   `--ink` #0b1a2e (JNR navy).
2. **Warna jalur DIGELAPKAN, dan itu disebut apa adanya.** Di atas badan terang, keempat hex RESMI jalur GAGAL
   4.5:1 — Yamanote 1.75:1, Ginza 2.04:1, Keihin-Tohoku 1.97:1, Marunouchi 3.64:1. Pilihannya adalah warna resmi
   yang tidak terbaca sebagian orang, atau warna yang terbaca semua orang. Dipilih yang kedua: hue jalur
   dipertahankan, lightness diturunkan sampai lolos — `--go` #55721c, `--nogo` #d20911, `--warn` #995900,
   `--link` #007197. Klaim "warna jalur JR" di ADR-023 karena itu DIPERSEMPIT: sekarang hue-nya, bukan hex-nya,
   dan `frontend/styles/app.css` menyatakannya di tempat warnanya didefinisikan.
3. **Kasus terburuk berbalik.** Pada tema gelap, pasangan tersempit ada di latar TERTERANG; pada tema terang ia
   ada di latar TERGELAP (baris genap). Itu kesalahan yang sempat dibuat saat menyetel palet ini dan ditangkap
   oleh perhitungan, bukan oleh mata. Tersempit sekarang: `--go` di atas `--row` = **4.55:1**; garis 3.06:1.
4. **Judul hero dot-matrix**, CSS murni (`radial-gradient` + `background-clip: text`), nol aset. DUA lapis: titik
   di atas huruf PADAT, sehingga lantai kontras tetap `--ink` di atas `--bg` dan titiknya murni tekstur.
   `@supports` wajib: tanpa dukungan `background-clip: text`, `color: transparent` akan MENGHILANGKAN judul.
5. **Bilah gulir bergaya peta jalur** (landing saja). Ia dirender server pada 0% sehingga halaman tanpa JS tetap
   punya rel utuh, `aria-hidden` karena posisi gulir sudah dibawa bilah gulir browser (ini kanal kedua, bukan
   satu-satunya), dan klien hanya menulis satu custom property `--p` — landing tetap INERT dalam arti yang
   penting: nol permintaan, nol perubahan isi. Pembaruan digandeng ke `requestAnimationFrame`.
6. **Tidak ada blok `prefers-reduced-motion` untuk rel**, dan itu disengaja: seluruh repo nol animasi dan nol
   transisi (dijaga tes), jadi kereta langsung berada di posisinya — tidak ada gerak untuk dikurangi.
7. **Langit berawan ohmydesign.com TIDAK diambil.** Ia menuntut aset gambar atau gradien besar dan mengubah
   halaman menjadi ilustrasi; yang diambil adalah keputusan bentuknya (dot-matrix, pil, label sudut).

Konsekuensi: `frontend/styles/app.css` · `ui/screens.py` · `frontend/src/main.ts` (fungsi `railbar`) ·
`tests/test_ui.py` (MEASURED_PALETTE, tiga angka dipatok, hash artefak) · `ui/README.md` (tabel kontras dihitung
ulang skrip) · `README.md`. Nol dependency baru, nol aset baru, CSP tidak disentuh.

Risiko yang diterima: tiga palet dalam satu hari berarti `docs/judge-reports/fase-5.md` dan checklist 4.6 makin
jauh dari apa yang benar-benar dilayani. 4.6 HARUS dijalankan ulang atas UI ini sebelum video direkam.
