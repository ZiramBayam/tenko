# Laporan juri — fase 5 — 6 Sep 2026

**Verdict: TIDAK ada track yang mencapai ambang 80. T1 79 · T2 78 · T3 77.** Semuanya kurang 1–3
poin, dan penyebab terbesarnya sama untuk ketiganya dan bukan kode: `README.md` masih memberi tahu
juri bahwa `make demo`, layar, dan demo dua tanggal **tidak ada** — padahal ketiganya berjalan di
depan saya hari ini.

## Langkah §7 (`make demo`, dua run)

| # | Hasil | Output nyata |
|---|---|---|
| 1 | ✔ | `status=partial data_mode=live decision=GO blocking=- snapshot_ids=[1,2,3,4,11] decision_hash=083d3ed0…` · 10 item, 10 green · envelope kanonik 8296 byte dicetak utuh |
| 2 | ✔ | `decision=NO_GO blocking=02_completeness, 04_sentiment_shift` · `n_items=10 (green 7, red 1, unspoken 2)` · blok `PENGAKUAN` mendahului hasilnya |
| 3 | ✔ | 3 tick × (3 GO + 1 NO_GO); hash identik antar-tick; `replay ID=1 → IDENTICAL`, `ID=4 → IDENTICAL`; tamper 1 byte → `MISMATCH` menyebut `snapshot_id=1` + kedua hash |
| 4 | ✔ | kartu 10 baris dari `decisions.card_json`; LLM mati → `narration_unavailable`, `n_lines=0`; hash sebelum = sesudah = `083d3ed0…` |
| 5 | ✔ | `/ui` 200, `/ui/decision/9` 200, banner SIMULATED True, `aria-label: 31 · tautan fokusable: 4 · handler hover/onclick: 0` |
| 6 | ✔ | SOL `GO → NO_GO` (03_regime, 08_derivatives berubah); DGAI `NO_GO → NO_GO` hash berbeda; tabel kontribusi per tool |

`make demo > a; make demo > b; diff a b` → **kosong, byte-identik**. Penjaga:
`0 berkas di data/ dibuka proses ini`. `make test` 1840 passed/1 skipped (207 dtk) ·
`make lint` All checks passed · `make doctor` Python 3.12.3, uv 0.12.7, sqlite 3.45.1, 2 sumber aktif.

## Tes destruktif — dijalankan sendiri, bukan dibaca dari laporan

**a) Sumber eksternal mati.** `RYO_MCP_URL=http://127.0.0.1:9` + kredensial dicabut → `make skill`
**exit 0**, envelope sah, `status=partial`, 0 traceback (jawabnya dari store, store-first).
`bash scripts/chaos.sh` dijalankan ulang penuh: **LULUS=8 GAGAL=0**, termasuk `host_down` (perekam
exit 1, 18 baris backoff) dan `rate_limit_429` (2×429 lalu 200, menunggu **4.155 s** ≥ 2× `Retry-After: 2`).

**b) DB dihapus.** Server hidup di atas `ui.db` salinan, lalu berkas dihapus di bawahnya:
`/api/health` `/api/watchlist` `/api/decisions` `/api/decision/1` `/ui` `/ui/decision/1` → semua
**503**, semua membawa `simulated`; `/ui/nope` → 404 + `simulated` content=true; proses tetap hidup;
**0 traceback** di log server.

**c) Tamper → replay.** Salinan `frozen.db`, `UPDATE snapshots SET payload_json` pada id 3722
(`verdict` `caution — overbought` → `accumulate`), `payload_hash` sengaja tidak diperbarui →
`replay 52` **MISMATCH**, menyebut `snapshot_id=3722` dan kedua sha256. Baseline pada salinan yang
sama: `IDENTICAL`.

**d) Kredensial dicabut.** `credentials_removed` chaos: `.env` sandbox dipindahkan (bukan `unset`) →
exit 2, **0 koneksi baru**, `.env` asli utuh (`273 1788234428` sebelum = sesudah). Sisi baca:
`make skill` tanpa kredensial tetap exit 0 dengan envelope jujur. `guard.sh` memblokir dua perintah
juri sendiri saat nama variabel kunci disebut — penjaganya nyata.

**e) Masukan sampah (8 kasus).** `preflight` tanpa argumen, `""`, `SOL; DROP TABLE snapshots;--`,
500×`A`, intent asing, tool asing, `<script>alert(1)</script>`, argumen berlebih → **8/8 envelope
sah, exit 0, 0 traceback**. Simbol XSS diterima dan di-uppercase, tetapi hasilnya `NO_GO` dengan
`02_completeness` merah (`no envelope for analyze_token, deep_analysis, compare_tokens`) dan
di-escape di HTML. Replay id tak ada / non-numerik → pesan bersih, exit 2.

**f) Serangan pada pengakuan §7 langkah 2.** Tiga varian:

- Kosongkan `synthetic.envelopes_rewritten_to_unavailable` + tulis `why: "nothing was fabricated"` →
  naskah **tetap** mencetak `Yang dibentuk: 1 envelope, yaitu monitor_market_sentiment_shift
  (source_id 2492)`. Angka dan namanya dihitung, hanya prosa yang dikutip.
- Kosongkan daftar **dan** bendera per-baris **dan** buang warning `SYNTHETIC` → **`KETIDAKCOCOKAN`
  dicetak 2×**, termasuk di ringkasan akhir: *"berkas TIDAK mengaku membentuk source_id 2492 —
  padahal envelopenya berstatus unavailable"*. **Penyembunyian gagal.**
- **LUBANG:** hapus blok `synthetic` seluruhnya → tidak ada `KETIDAKCOCOKAN`, tidak ada kata
  SINTETIS; yang tercetak hanya *"asal-usul … TIDAK DINYATAKAN"*. `_is_shaped()` sudah tahu envelope
  itu dibentuk; pengakuannya tetap bergantung pada berkas yang sukarela mengakui.

## Uji 30 detik Layar B (`/ui/decision/52`)

Yang saya pahami sebelum membaca kodenya: *BNB tidak berangkat. Satu dari sepuluh baris menahannya:
verdict cepat `caution — overbought` dari `analyze_token.data.verdict` per 06:15 JST, yang menentang
pembukaan posisi ke arah mana pun. Sembilan baris lain terjawab. Keputusan bisa diputar ulang dan
hasilnya cocok.* → **LULUS**, dalam ±12 detik, tanpa klik.

Catatan yang menahan nilainya: `<html lang="id">`. Kalimat kunci dan seluruh judul kolom berbahasa
Indonesia, kalimat buktinya berbahasa Inggris.

## Audit kode

- **Arah dependensi bersih.** `core/` nol impor ke `preflight|agent|ui|grading`; `preflight/` nol ke
  `agent|ui|grading`; `ui/*.py` nol `httpx`/`anthropic`; `agent/` nol klien live.
- **Exception:** 0 `except:` telanjang; 24 `except Exception`, semuanya pagar batas ber-`# noqa: BLE001`
  + alasan, dan yang me-log hanya mencetak `type(exc).__name__` — tidak ada pesan yang bisa membawa kredensial.
- **Tes:** 1311 fungsi → 1840 kasus, termasuk rute/metode yang **tidak** dideklarasikan, `algo`
  bermuatan `\n`, U+202E, fixture skew `ts_utc` vs `as_of`.
- **`preflight/params.py:69-71`** — mengakui sendiri bahwa `risk_reward = [1.0, 2.0]` dan `min_rr = 1.0`
  dipilih justru karena ambang lebih tinggi membuat item 9 merah untuk **setiap** plan RYO. Diverifikasi
  ulang: **1426/1426 baris `deep_analysis` = `[1.0, 2.0]`**. Uji ambang item 9 karena itu tautologi.
- **`preflight/params.py:163` `ITEM_ORDER`** — peringkat "daya veto" yang dicetak di kedua layar adalah
  konstanta desain spec §4, tanpa satu baris pun yang menyatakan ia belum divalidasi;
  `preflight/veto_table.py` justru membantahnya (01 tidak pernah merah, 06 merah 92×).
- **`preflight/skill.py:536`** — pemilihan store sudah membatasi umur ke `params.max_age_min` terhadap
  jam dinding. Itulah sebab struktural `01_freshness` tidak pernah menahan apa pun dalam n=237.
- **`agent/trade.py:123` `"sizing": "fixed_notional"`** — semua practice trade 1000 USD. Tidak ada
  jalur di mana reliabilitas menggerakkan ukuran.
- **`ui/app.py` / `ui/screens.py`** — nol `<script>`, CSP `default-src 'none'; style-src 'self'`,
  `a:focus, a:focus-visible { outline: 3px solid var(--warn) }`. `/ui/decision/0|-1|abc` → 422
  ber-banner; `POST /ui` → 405 ber-banner; `/ui/../etc/passwd` → 404 JSON ber-`simulated`.

## Kepatuhan

**DISCLOSURE ✔(dengan catatan)** · **SIMULATED ✔** · **sumber ✔** · **key ✔**

- Kunci: `.env` tidak dilacak, dan pemindaian nilai-per-nilai `RYO_MCP_KEY` (49 aksara) atas
  **125 berkas terlacak + seluruh `logs/`** → **0 kecocokan**.
- SIMULATED: banner di Layar A & B, `simulated` di 200/400/404/405/422/503, di setiap envelope CLI
  termasuk 8 masukan sampah, dan di baris penutup `make demo`.
- Sumber: hanya 2 di `sources.yaml`, keduanya `enabled+approved`. `llm_narration` `approved: true`
  bersandar pada "your own AI model" di landing page, bukan jawaban tertulis penyelenggara —
  satu-satunya `approved` yang ditegakkan sendiri; risikonya rendah karena narasi di luar jalur
  keputusan dan demo berjalan tanpa LLM.
- **Catatan DISCLOSURE:** baris `fastapi` masih berbunyi *"Yang mendarat adalah API-nya, bukan
  layarnya: Layar A/B (TASKS 3.2) belum ada"* dan menyebut "enam route GET"; hari ini layarnya ada
  dan rutenya delapan. Arahnya *mengecilkan*, jadi bukan misrepresentasi — tetapi aturan "diperbarui
  pada commit yang sama" tidak dipenuhi.
- **Tidak ada data placeholder yang menyamar sebagai nyata.**

## Skor

```
Umum:  combining 18/20 | failure 18/20
T1:  thinking 14/25 | cause-effect 17/20 | repeat 9/10 | exec 3/5   → T1 total 79/100
T2:  read 18/25 | importance 17/25 | everyone 7/10                 → T2 total 78/100
T3:  gap 20/25 | spec 7/15 (DIBATASI) | usable 14/20               → T3 total 77/100
```

- **Combining 18** — pada kartu #52 lima tool berbeda memasok 10 baris, dan lintas 4 baris watchlist
  + demo dua tanggal **kelima tool benar-benar pernah membalik keputusan**. Dipotong karena "ledger
  reliabilitas sendiri" yang dijanjikan spec §8 tidak memberi masukan apa pun ke keputusan, dan 3
  dari 10 baris market-wide berbagi satu `args_hash` untuk keempat simbol.
- **Failure 18** — chaos 8/8 direproduksi, ditambah dead-host, tanpa-kredensial, DB-dihapus-di-bawah-
  server, 8 masukan sampah, dan tamper: nol traceback. Dipotong karena `kill -9` hanya diuji pada
  perekam yang di-spawn chaos atas klien fixture.
- **Thinking 14** — verdict sama menghasilkan keputusan berbeda, dan pemisahan "bukti HILANG" vs
  "merah" pada DGAI adalah penalaran nyata tentang ketiadaan. Tidak bisa naik: keputusan tetap
  konjungsi statis, `sizing: fixed_notional`, dan `make grade` mencetak "TIDAK ada takaran yang
  dikeluarkan".
- **Cause-effect 17** — tiap baris membawa tool.field + snapshot_id + as_of + `reason` deterministik,
  dan `make grade` menutup lingkarnya sampai mengatakan "veto ini salah". Dipotong karena kolom "daya
  veto" yang dipajang adalah konstanta yang dibantah alat ukur repo ini sendiri.
- **Repeat 9** — `make demo` byte-identik, replay 49/52 IDENTICAL, tamper MISMATCH bernama. Dipotong
  karena resep reproduksi yang tertulis (`--until` saja) terbukti tidak mengulang angkanya.
- **Exec 3** — plan RYO dieksekusi sebagai practice trade dengan exit dari harga perekam dan tanpa
  P&L. Sizing 0/½/1 spec tidak ada, ukurannya tetap, dan di naskah 0 dari 9 posisi sempat tertutup.
- **Read 18** — dua baris pertama Layar B menjawab "apa yang ditahan, kenapa, dari mana, per kapan".
  Dipotong karena halaman dwibahasa dan kolom "nilai yang disebut" adalah gumpalan `key=value` 10 field.
- **Importance 17** — penahan terberat naik ke atas dengan kalimat dan sumbernya. Dipotong karena
  kunci urutannya diklaim, bukan diukur.
- **Everyone 7** — nol skrip, CSP `default-src 'none'`, cincin fokus 13.55:1, nol hover-only. Dipotong
  karena `aria-label` baris item memotong nilainya sehingga pengguna pembaca layar menerima **lebih
  sedikit** daripada pengguna awas, dan `lang="id"` salah untuk sebagian besar kalimat buktinya.
- **Gap 20** — skill yang melaporkan bukti mana yang **tidak ada** dan menolak berangkat karenanya
  tidak ada di 6 tool RYO maupun daftar ide resmi. Dipotong karena kebaruannya adalah disiplin
  pembukuan, bukan sumber sinyal baru.
- **Spec 7 — DIBATASI** — `docs/api-facts.md` §E menyatakan tidak ada spec skill T3 formal.
- **Usable 14** — satu fungsi, satu bentuk, exit 0 di setiap jalur yang diserang, tanpa Node.
  Dipotong karena konsumen tanpa perekam hanya mendapat `unavailable` tanpa jalur pemasangan yang
  didokumentasikan, dan README-nya sendiri menyuruh pembaca tidak mengharapkan `make demo`.

## Kegagalan yang bisa direproduksi

1. **Pemadaman sintetis tanpa pengakuan lolos tanpa `KETIDAKCOCOKAN`.**
   `python3 -c "import json;d=json.load(open('preflight/fixtures/outage-one-tool-2026-09-05.json'));d.pop('synthetic');json.dump(d,open('/tmp/fx.json','w'))"`
   lalu `bash scripts/demo.sh --step2-fixture /tmp/fx.json` → exit 0, `grep -c KETIDAKCOCOKAN` = **0**.
   `agent/demo.py:343 _synthetic_note` mengembalikan `None` → cabang lunak, padahal `_is_shaped`
   (`agent/demo.py:350`) sudah tahu.
2. **Angka tabel veto tidak terulang dengan resep yang tertulis.** `--until <sama>` terhadap
   `data/ryo.db` hidup → n bergerak (201 → **237** hari ini). Hanya `--db <salinan beku>` yang
   mengulang; itu tidak ada di `preflight/veto_table.py:68`.
3. **Klaim kartu melampaui buktinya (item 9).** `09_plan green … risk_reward[0]=1 >= 1 -> the plan is
   present and worth its risk`, sementara `risk_reward` **konstan `[1.0, 2.0]` di 1426/1426** dan
   `min_rr = 1.0`. "Worth its risk" tidak pernah bisa salah.
4. **README menyangkal produknya sendiri.** `grep -n 'BELUM' README.md` → langkah 0/5/6 §7 ditandai
   BELUM, "belum ada `make demo`, belum ada satu layar pun yang kami klaim", `make test` 1438.
   Ketiganya berjalan hari ini; suite 1840.

## 5 pertanyaan juri

1. **"Bedanya apa dengan agen sinyal + guardrail biasa?"** — Jawaban terkuat repo: guardrail biasa
   memperlakukan data hilang sebagai lolos atau sebagai nol; di sini `unspoken` **memveto** untuk item
   wajib, dicatat terpisah dari merah, dan tidak pernah menjadi angka. Repo punya jawabannya, tetapi
   jawabannya ada di layar, bukan di README.
2. **"Kalau semua item selalu hijau untuk BTC, apa yang agen ini pikirkan — bukan cuma memeriksa?"** —
   **Repo tidak punya jawaban.** Kartu SOL GO live (#49): 03, 05, 07 semuanya `stance=neutral`, 10
   hijau meski `pick=BNB`, 09 hijau atas rr konstan. GO di sini berarti "tidak ada yang keberatan",
   bukan "bukti mendukung" — dan agen tetap membuka 1000 USD notional.
3. **"Tunjukkan satu keputusan yang berubah karena data HILANG, bukan karena data buruk."** — **Ada,
   dan bersih.** DGAI 2026-09-03: `NO_GO` dengan 5 dari 6 penahan berlabel HILANG, dan sebabnya
   berantai — `trade_plan='unavailable'` → tidak ada arah plan → `03_regime` yang envelope-nya
   terbaca tetap tidak bisa disebut.
4. **"Veto kalian belum terbukti menyelamatkan apa pun (k=0), dan tiga kali justru merugikan?"** —
   Separuh terjawab. `make grade` menjawab bagian pertama dengan sangat jujur. Bagian kedua **tidak**
   terjawab: urutan daya veto tetap konstanta desain, dipajang otoritatif di dua layar.
5. **"`01_freshness` tidak pernah menahan apa pun dalam n=237 — untuk apa ia ada?"** — Jawaban repo
   benar tetapi tersembunyi: `preflight/skill.py:536` sudah membatasi umur di pemilihan store, jadi
   barisnya adalah **tanda terima**, bukan veto. Yang belum dilakukan: mengatakan itu di tempat
   namanya muncul.

## Prioritas perbaikan

**Wajib sebelum kirim (8 Sep 20:00 JST)**

1. **Tulis ulang README §2/§3/§4 ke keadaan sekarang.** Satu-satunya perbaikan yang menggerakkan
   **ketiga** track sekaligus (~+3 tiap track): saat ini README-lah yang menyuruh juri melewatkan
   produknya. (penulisan dokumen)
2. **Perbaiki kalimat item 9 di kartu.** `"worth its risk"` tidak boleh berdiri di atas konstanta.
   (kerja preflight)
3. **Beri status pada kolom "daya veto"** di Layar A & B: urutan spec §4, belum divalidasi.
   (kerja layar)
4. **Angkat `k=0` / `target_first=3` ke permukaan** — satu bagian README + satu baris Layar A.
   (penulisan dokumen + kerja layar)
5. **Tutup lubang §7 langkah 2:** "dibentuk tetapi tidak diakui" harus `KETIDAKCOCOKAN`.
   (kerja agen)
6. **Perbarui baris `fastapi` di DISCLOSURE.md.** (penulisan dokumen)
7. **Rekam video 30 detik Layar B.** (manusia)

**Kalau sempat**

8. Tambahkan `--db <salinan beku>` ke resep reproduksi `preflight/veto_table.py:68`.
9. Jangan potong `aria-label` baris item.
10. Putuskan bahasa UI: `lang="id"` + kalimat bukti Inggris adalah gabungan terburuk untuk juri RYO.
11. Ganti nama / pensiunkan `01_freshness` menjadi apa yang benar-benar diukurnya.
12. `scope=` pada header tabel kedua Layar B.
