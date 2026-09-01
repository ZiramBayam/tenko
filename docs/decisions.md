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
Konsekuensi: pitch-writer & post sosial tidak memuat angka tool RYO.

## ADR-009 (DIGANTI oleh ADR-012: keputusan = preflight) Sinyal agen sengaja sederhana
Konteks: yang dinilai T1 adalah sebab→akibat & jejak, bukan alpha; sinyal kompleks menambah bug.
Keputusan: kandidat = scan_market rank + check_safety lolos; edge target parametris (bukan prediksi).
Konsekuensi: metrik utama agen = selisih kumulatif buku mid vs honest + kualitas keputusan skip, bukan PnL absolut.

## ADR-010 (USULAN — menunggu keputusan user, 1 Sep 2026) Pivot: Calibration Ledger jadi tulang punggung; agen T1 = "Trust Meter Agent"; Honest Fill diparkir
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

## ADR-011 (USULAN, 1 Sep 2026) Mekanik sosial & peran model AI — memenuhi definisi resmi "Agentic SocialFi"
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
   di versions.md oleh api-verifier saat task-nya dibuat (evaluator sudah memakai anthropic SDK — bisa disamakan).
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
host mati, dan TASKS 4.6 menuntut navigasi keyboard penuh. Verifikasi lisensi/versi: api-verifier 1 Sep 2026.
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

## ADR-015 (USULAN — menunggu keputusan manusia, 1 Sep 2026) `SkillResult` mengikuti envelope RYO NYATA: `schema_version="1.0"`, `simulated` satu-satunya field tambahan
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
4. (ditambahkan setelah verifikasi api-verifier 1 Sep 2026) Dua penyimpangan lain dari teks spec §3 ikut diputuskan
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

## ADR-016 (USULAN 1 Sep 2026) Dua generasi di data/ryo.db: dilaporkan STALE_ALGO, tidak dihapus, tidak disembunyikan
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
