# Tenko (点呼) — RYO-CHAN Hackathon 2026

> **SIMULATED — practice positions on read-only market intelligence.** Semua posisi di repo ini adalah *practice
> trade*: tidak ada dana, wallet, kunci penandatangan, atau eksekusi nyata. Setiap envelope keluaran membawa
> `simulated: true`, setiap respons UI membawanya juga (termasuk 404/405/422/503), dan setiap layar membawa banner
> SIMULATED.

**Masalahnya:** trader ritel — dan hampir semua agen sinyal — bertindak atas satu verdict tanpa memeriksa seluruh bukti
yang tersedia, dan (lebih berbahaya) tanpa tahu bukti mana yang **tidak tersedia**: data basi, seksi `unavailable`,
plan yang tidak ada. Guardrail yang umum memperlakukan data hilang sebagai lolos atau sebagai nol (`PRD.md` §1).
**Solusinya, satu produk untuk tiga track** (ADR-012 — maksimal satu BUIDL, `docs/api-facts.md` §E): sebelum tiap
practice trade, Tenko memanggil sepuluh item bukti dari lima tool RYO satu per satu — menunjuk `tool.field`, menyebut
nilainya dan `as_of`-nya — lalu **tidak berangkat** bila ada satu item merah **atau satu item yang tidak tersebut**;
kartu panggilan itu adalah keputusan kelas satu yang bisa dibaca di dua layar keyboard-first, dikeluarkan sebagai skill
ber-envelope RYO, dan bisa diputar ulang byte-per-byte.

**Apa yang berjalan hari ini (6 Sep 2026).** Naskah demo `docs/spec.md` §7 langkah 1–6 berjalan dari satu perintah dan
dua run byte-identik; kedua layar berjalan; demo dua tanggal membalik keputusan SOL dari GO ke NO_GO; suite **1840
passed, 1 skipped**; `make lint` bersih. Yang **belum** ada disebut sebagai belum ada di §7, §8, dan §11 — termasuk
angka paling tidak menyenangkan di repo ini, yang mendapat bagiannya sendiri di §7.

---

## 1. Arsitektur (`docs/spec.md` §2) — semua kotak di bawah ini berjalan

```
 RYO tools (6) ──► core.ryo_client (rate-limit, fixtures) ──► core.store (snapshot append-only, hash)
                                                                     │
                                            preflight.skill ◄────────┘  freeze(snapshot_ids)
                                            10 item (spec §4) → GO | NO_GO + blocking
                                                     │                      │
                                             agent (practice trade      preflight.narrate (LLM,
                                              plan / declined)           tidak menyentuh keputusan)
                                                     │                      │
                                             agent.replay ID= (IDENTICAL | MISMATCH | 3 vonis "tidak tahu")
                                                     │
                                             ui: Layar A watchlist (/ui) · Layar B kartu panggilan
                                                 (/ui/decision/{id}) · banner SIMULATED
 core.recorder (per siklus) ──► grading (make grade): outcome → reputasi item, hanya dengan n (§7)
```

Aturan yang mengikat arah panah: **tidak ada LLM di jalur keputusan** (ADR-003). Keputusan adalah fungsi deterministik
atas snapshot beku; narasi hanya menerjemahkan hasilnya dan terbukti tidak menggeser `decision_hash` bahkan saat kartu
diracuni dan narasinya bermusuhan (`preflight/narrate.py`, `tests/test_narrate.py`). Agen tidak pernah mengimpor klien
live (`tests/test_agent.py::test_agent_package_never_imports_the_live_client`), dan `ui/` tidak memuat `httpx` maupun
`anthropic` sama sekali — layar hanya membaca keputusan yang sudah tersimpan.

---

## 2. Cara menjalankan

Prasyarat: Python 3.12 + [uv](https://docs.astral.sh/uv/) (`docs/versions.md`). Tanpa Node/npm (ADR-001).

```bash
make doctor    # python 3.12.3 · uv 0.12.7 · sqlite 3.45.1 · 2 sumber aktif
make demo      # naskah spec §7 langkah 1-6, seluruhnya dari fixture offline
make test      # 1840 passed, 1 skipped
```

**`make demo` tidak butuh jaringan, kredensial, maupun `data/ryo.db`.** Ia membangun DB SQLite baru di direktori
sementara dari `agent/fixtures/*.json` + `preflight/fixtures/*.json` setiap run, dan memasang audit hook yang
**mengukur** klaim itu; barisnya ikut tercetak: `penjaga: 0 berkas di data/ dibuka proses ini (sys.addaudithook atas
open + sqlite3.connect)` (`agent/demo.py:186`). `.env` sengaja **tidak** dimuat, karena §7 langkah 4 memperagakan LLM
yang mati (`scripts/demo.sh:15-18`). Dua run wajib byte-identik:

```bash
make demo > a; make demo > b; diff a b     # kosong (dipatok tests/test_agent_demo.py)
```

**Klon bersih tidak memuat `data/ryo.db`** — `data/` ada di `.gitignore`, dan snapshot pasar ber-timestamp tidak bisa
dibuat ulang. Konsekuensinya jujur, dan alat-alatnya tetap menjawab jujur, bukan dengan angka default:

| Perintah | Butuh apa | Tanpa `data/ryo.db` |
|---|---|---|
| `make demo` · `make two-dates` · `make test` · `make lint` | tidak ada | berjalan penuh dari fixture |
| `make ui` (`/ui`, `/ui/decision/{id}`) | store terbaca | **503 ber-banner SIMULATED**, bukan halaman kosong palsu |
| `make skill ARGS="preflight SOL"` | snapshot di store | exit 0, envelope `status:"unavailable"`, `data:null` |
| `make agent-tick` · `make replay ID=` · `make grade` · `uv run python -m preflight.veto_table` | ledger/snapshot lokal | menyatakan tidak ada bukti; tidak mengarang angka |
| `make record` | kredensial RYO dari env | perintah inilah yang **membuat** store; ia menghormati rate limit (`docs/api-facts.md` §A) |

Untuk **melihat kedua layar di browser tanpa perekam**, ada jalur fixture: `uv run python -m agent.demo_fixture --db
/tmp/tenko-demo.db` menulis DB dari fixture yang sama dengan demo (`agent/demo_fixture.py:318`; penjaga TASKS 0.19
menolak tujuan di dalam `ROOT/data`), lalu `DB_PATH=/tmp/tenko-demo.db make ui` (env `DB_PATH` dibaca `ui/app.py`
docstring butir 2, dan terbukti dipakai juri saat menjalankan server di atas salinan DB —
`docs/judge-reports/fase-5.md` §tes destruktif b). **Yang kami jalankan hari ini sebagai satu resep adalah `make demo`
langkah 5**, yang merender kedua layar dari aplikasi yang sama; dua perintah di atas belum kami jalankan berurutan dan
tidak boleh dibaca sebagai sudah diuji.

---

## 3. "Tunjuk & sebut" sebagai kode — satu item tidak tersebut ⇒ NO_GO

Ini produknya. `make demo` §7 langkah 2 menjalankan fixture di mana **satu tool wajib menjawab `unavailable` dan empat
lainnya menjawab** (`preflight/fixtures/outage-one-tool-2026-09-05.json`, dipakai bawaan oleh `scripts/demo.sh:28`).
Kartunya — **cuplikan**; baris `status=`, `snapshot_ids=`, sepuluh baris panggilan, dan envelope kanoniknya dipotong
di sini (`…` menandai potongan). `make demo` mencetak semuanya:

```
=== §7 langkah 2 — satu tool wajib unavailable, sisanya menjawab ===
…
decision=NO_GO  blocking=02_completeness, 04_sentiment_shift
…
n_items=10 (green 7, red 1, unspoken 2)
…
```

Sepuluh barisnya, per item (dipatok tes, bukan diketik ulang dari layar —
`tests/test_preflight_outage.py::test_one_required_tool_unavailable_makes_item_two_red_and_the_envelope_partial`):

| item | status | kenapa ia berarti apa |
|---|---|---|
| `01_freshness` `03_regime` `05_verdict_quick` `06_confluence` `07_verdict_deep` `09_plan` `10_relative` | **green** (7) | bukti ada, terbaca, dan tidak menentang plan |
| `02_completeness` | **red** | satu tool wajib menjawab `unavailable`; `reason` menyebut nama toolnya, dan `source.tool` menunjuk tool itu |
| `04_sentiment_shift` | **unspoken** | sumbernya hilang. Item wajib ⇒ **ikut ke `blocking`** meski tidak merah |
| `08_derivatives` | **unspoken** | sumbernya hilang juga, tetapi item ini opsional (spec §4) ⇒ **tidak** memveto |

Yang membuat kedua baris `unspoken` itu bukan sekadar label: **item `unspoken` tidak boleh membawa nilai apa pun**, termasuk `0`
dan `0.0` — bentuk kebohongan yang persis ingin kami cegah. Ditegakkan validator, bukan konvensi:
`core/skill.py::_unspoken_carries_no_value` · `tests/test_skill.py::test_unspoken_item_may_not_carry_a_value` ·
`availability` wajib menandai `"unspoken"` **jika dan hanya jika** item itu unspoken
(`::test_unspoken_item_must_be_marked_unspoken_in_availability`) · sekitar 30 tes `*_unspoken_*` di
`tests/test_items.py` memaksa null / `unavailable` / kosakata tak dikenal / angka non-finit menjadi `unspoken`
ber-`reason`, bukan nol. Jalankan `make demo` untuk melihat baris JSON kanoniknya utuh; kami tidak menempel salinan
hasil sunting tangan di sini.

**Bentuknya sengaja bentuk RYO** (ADR-013/015). Tidak ada spec formal untuk skill T3 (`docs/api-facts.md` §E), jadi
yang paling murah dipakai RYO adalah envelope tool mereka sendiri: `SkillResult` memuat **sepuluh field top-level yang
sama** (`schema_version, tool, status, data_mode, as_of, request, data, summary, availability, warnings`) ditambah
**tepat satu** field, `simulated`, yang diwajibkan `docs/rules-compliance.md`. Invarian itu diuji mekanis terhadap
keenam sampel envelope nyata di `docs/samples/`
(`tests/test_skill.py::test_top_level_invariant_holds_for_all_six_samples`). Yang **tidak** terjadi di envelope: tidak
ada `data` karangan, tidak ada `as_of` yang dibuat dari `now()`, tidak ada nol sebagai pengganti "tidak tahu" —
validator menolak `data` non-null saat `status="unavailable"` dan menolak `ok`/`partial` tanpa data
(`core/skill.py::_honesty_invariants`).

---

## 4. Replay: keputusan yang sama, atau tuduhan yang bernama

```bash
make demo                    # langkah 3 menjalankan replay + tamper di depan mata
make replay ID=<trade_id>    # atas ledger lokal Anda sendiri
```

Di dalam `make demo` langkah 3 (fixture, jadi hasilnya sama di mesin siapa pun): tiga tick menulis 3 GO + 1 NO_GO,
`replay ID=1` dan `ID=4` menjawab **IDENTICAL**, lalu satu byte `payload_json` diubah pada salinan DB dan vonisnya
menjadi **MISMATCH yang menyebut `snapshot_id=1`** berikut kedua sha256 (`docs/judge-reports/fase-5.md`, tabel §7
baris 3; dipatok `tests/test_agent_replay.py::test_one_tampered_byte_in_the_copy_gives_mismatch_naming_the_snapshot`).

Vonisnya **lima**, dan hanya **satu** yang berarti "terverifikasi": `IDENTICAL`
(`VERIFIED_VERDICTS = frozenset({VERDICT_IDENTICAL})`, `agent/replay.py:126`). `MISMATCH` (exit 1) adalah **temuan** —
bukti tidak mereproduksi keputusan. `STALE_ALGO`, `STALE_PARAMS`, dan `NO_EVIDENCE` adalah **pengakuan tidak tahu**,
bukan kelulusan: empat dari lima keluar 0, dan exit 0 di sini berarti "perintah menghasilkan vonis", bukan "keputusan
terverifikasi" (`agent/replay.py:132-134`).

`data/ryo.db` memuat tiga generasi karena store hanya-tambah (ADR-006/016): baris yang ditulis implementasi yang sudah
dihapus dilaporkan `STALE_ALGO` (id 1–19), baris dari generasi params sebelumnya `STALE_PARAMS` (id 20–27), sisanya
generasi berjalan. Tidak satu pun dari baris lama itu kami sebut salah — kami menyatakan **tidak menilainya**.
Menyembunyikannya akan lebih rapi dan lebih tidak jujur.

---

## 5. Dua layar (T2) — `make ui`

| Layar | Route | Pertanyaan yang dijawab ≤30 dtk | JSON yang sama persis |
|---|---|---|---|
| A. Watchlist | `/ui` | "simbol mana yang paling dekat GO, dan apa yang menahan yang lain?" | `/api/watchlist` |
| B. Kartu panggilan | `/ui/decision/{id}` | "kenapa keputusan ini — bukti per baris dari tool/field mana, per kapan?" | `/api/decision/{id}` |

Dirender di server, **nol `<script>`**, CSP `default-src 'none'; style-src 'self'`, satu-satunya aset adalah
`/ui/tenko.css` yang lahir dari `ui/screens.py`. Semua interaksi navigasi `GET`, nol `tabindex`, cincin fokus 13.55:1,
nol `:hover`, nol animasi; setiap baris kartu diumumkan sebagai satu kalimat "tunjuk → sebut" ber-`aria-label`, dan
baris `unspoken` diumumkan "tidak disebut", tidak pernah sebagai 0. Checklist lengkap dengan **nama tes yang gagal bila
janjinya dilanggar** ada di `ui/README.md`; angka kontrasnya dihitung dari CSS yang benar-benar dilayani, bukan
ditaksir.

Batas yang diakui, disalin dari `ui/README.md` §"Batas yang belum tertutup": **rekaman 30 detik Layar B belum dibuat**
(artefak manusia; skripnya sudah ditulis di berkas yang sama dan di `demo/video-script.md`) · nol pengujian dengan
pembaca layar sungguhan · nol audit kontras pihak ketiga (ADR-001 melarang toolchain npm) · belum diuji zoom 200 %.

---

## 6. Naskah §7 langkah demi langkah — status hari ini

Satu perintah menjalankan keenamnya: `make demo`. Kolom "bukti" adalah keluaran run 6 Sep 2026 yang juga diverifikasi
juri (`docs/judge-reports/fase-5.md`, tabel §7); jalankan sendiri untuk mendapatkan byte yang sama.

| # | Langkah §7 | Status | Bukti |
|---|---|---|---|
| 1 | `make skill ARGS="preflight SOL"` → 10 item, GO/NO_GO, blocking, snapshot_ids | ✔ | `status=partial data_mode=live decision=GO blocking=- snapshot_ids=[1,2,3,4,11] decision_hash=083d3ed0…`; 10 item, 10 hijau; envelope kanonik dicetak utuh |
| 2 | fixture rusak → item merah + item tidak tersebut, tanpa exception | ✔ (envelope padamnya **SINTETIS**, lihat di bawah) | `decision=NO_GO blocking=02_completeness, 04_sentiment_shift`; `n_items=10 (green 7, red 1, unspoken 2)` (§3) |
| 3 | 3 tick → ≥1 GO + ≥1 NO_GO; replay IDENTICAL; tamper → MISMATCH | ✔ | 3 GO + 1 NO_GO; `replay ID=1` & `ID=4` IDENTICAL; tamper → MISMATCH ber-`snapshot_id=1` |
| 4 | narasi per item; LLM dimatikan → `narration_unavailable`, hash sama | ✔ | kartu 10 baris dari `decisions.card_json`; `narration_unavailable`, `n_lines=0`; hash sebelum = sesudah = `083d3ed0…` |
| 5 | `make ui` Layar A/B, keyboard-first, banner SIMULATED | ✔ | `/ui` 200, `/ui/decision/9` 200, banner SIMULATED, `aria-label: 31 · tautan fokusable: 4 · handler hover/onclick: 0` |
| 6 | dua tanggal, simbol sama → keputusan berbeda | ✔ | `make two-dates`: SOL **GO (2026-09-03) → NO_GO (2026-09-04)**, `03_regime` & `08_derivatives` hijau→merah, `params_hash` sama sehingga yang berubah **hanya buktinya** (`agent/two_dates.py:9-12`) |

**Langkah 2 memakai envelope SINTETIS, dan naskahnya mengatakannya sendiri.** RYO **belum pernah** mengeluarkan
`status: "unavailable"` dalam pengamatan kami — **0 dari 3645 snapshot** (`docs/api-facts.md` §A, sapuan 6 Sep 2026,
`id <= 3645`). Karena itu satu envelope di fixture langkah 2 **dibentuk** menjadi `unavailable`; envelope yang lain
adalah salinan apa adanya dari bukti nyata. Naskah menghitung sendiri envelope mana yang berbentuk dibentuk dan
membandingkannya dengan pengakuan berkasnya: bila keduanya berselisih ia mencetak `KETIDAKCOCOKAN` (`agent/demo.py`
`_disclosure_mismatch`). Lubang yang masih terbuka: fixture yang blok `synthetic`-nya **dihapus seluruhnya** lolos
tanpa `KETIDAKCOCOKAN` — dilaporkan di `docs/judge-reports/fase-5.md` §"Kegagalan yang bisa direproduksi" butir 1, dan
belum ditutup saat baris ini ditulis.

Selain demo dua tanggal, `make two-dates` membawa satu kasus yang lebih tajam: **DGAI NO_GO yang penyebabnya bukti
HILANG, bukan angka jelek**. Pada 2026-09-03, dari enam penahan DGAI **lima `unspoken`** dan satu-satunya yang merah
(`02_completeness`) merah justru karena lubang itu — `deep_analysis` menjawab tetapi menyebut
`availability.trade_plan='unavailable'`, sehingga `09_plan` menjadi `unspoken` dan **ikut disebut** di `blocking`
(`agent/two_dates.py:13-18`). Pasar yang sama, tool yang sama: satu simbol ditahan **angka**, satu simbol ditahan
**jawaban yang tidak ada**, dan kartu menyebut yang mana yang mana.

---

## 7. Angka paling tidak menyenangkan di repo ini: k = 0

`make grade` menilai keputusan yang sudah berumur ≥24 j dan bertanya satu hal: **apakah veto ini menyelamatkan
sesuatu?** Jawabannya hari ini, atas ledger lokal kami (6 Sep 2026, horizon 24 j, `grading/outcomes.py:68`):

- **13 keputusan bisa dinilai. `k = 0`.** Nol kerugian terbukti dicegah oleh item mana pun.
- **`target_first = 3`.** Tiga kali veto menahan plan yang justru **menyentuh target lebih dulu** — yaitu tiga kali
  vetonya salah. Kalimat itu dicetak alat ukurnya sendiri, dengan penyebut yang sama:
  `"yang justru menyentuh target lebih dulu (veto ini salah)"` (`grading/grader.py:100`).
- **`n_efektif` 1–5 lawan ambang 30** (`grading/stats.py:80`) → **nol selang kepercayaan dicetak**, dan Bunryō
  (ukuran posisi dari reliabilitas, spec §4 item 11) menutup dengan `TIDAK ada takaran yang dikeluarkan`
  (`grading/grader.py:537`).

`n_efektif` bukan `n`: amatan di sini **tidak independen** — `market_overview` dan `monitor_market_sentiment_shift`
dipanggil dengan argumen `{}`, jadi keempat simbol membaca baris yang sama dan satu peristiwa pasar bisa tercacah
sampai 4×. `grading/stats.py` mengempiskan `n` ke `min(n, peristiwa bukti berbeda)` justru supaya selangnya tidak
terlihat yakin karena duplikasi. Konsekuensinya: **kami tidak punya bukti bahwa sepuluh item ini menghasilkan
keputusan yang lebih baik.** Yang kami punya adalah keputusan yang bisa ditunjuk, disebut, diputar ulang, dan
dibantah — dan alat ukur yang mengatakan "belum cukup data" alih-alih mencetak angka kecil yang meyakinkan.

Sisanya, dengan `n` dan periodenya:

| Pengukuran | Angka | n & periode | Perintah / laporan |
|---|---|---|---|
| Chaos (host mati · kill -9 · 429 · DB dihapus · kredensial dicabut · celah bukti) | **LULUS 8 / GAGAL 0**, nol traceback | 8 skenario, 5 Sep 2026 23:27–23:30 JST | `bash scripts/chaos.sh` → `docs/reports/chaos-2026-09-05.md` |
| Celah bukti: `preflight()` atas bukti bolong | **0 melempar**; sebaran `{partial: 156, unavailable: 268}` | n = 424 panggilan | laporan yang sama, skenario `evidence_gap` |
| Reputasi item dari outcome | **k = 0**, `target_first = 3`, nol selang | 13 keputusan dinilai, horizon 24 j, 6 Sep 2026 | `make grade` |
| Daya veto empiris per item | `01_freshness` **0× merah**; `06_confluence` **92× merah** | n = 237 amatan (4 simbol inti × jangkar perekam) | `uv run python -m preflight.veto_table --db <salinan beku>` |
| Suite | **1840 passed, 1 skipped** | 6 Sep 2026, pohon kerja kami | `make test` (pada klon bersih, 12 tes paritas gate **SKIP** dengan alasan terbaca: skrip lokalnya tidak ikut terkirim, `TASKS.md` 0.25 — angkanya bergeser sebanyak itu) |
| Envelope RYO yang pernah `unavailable` | **0 dari 3645** | sapuan 6 Sep 2026, `id <= 3645` | `docs/api-facts.md` §A |

**Tiga peringatan yang menempel pada tabel itu, dan tidak boleh dilepas:**

1. **`01_freshness` belum pernah menahan apa pun dalam n=237, dan sebabnya struktural.** Pemilih bukti sudah membatasi
   umur ke `params.max_age_min` **sebelum** item mana pun dinilai (`preflight/skill.py:536`). Jadi baris itu adalah
   **tanda terima**, bukan veto: ia menyatakan "bukti yang dipakai memang di dalam jendela", dan bukti yang di luar
   jendela tidak pernah sampai kepadanya. Namanya menjanjikan lebih daripada yang dikerjakannya; menggantinya ada di
   daftar perbaikan juri (`docs/judge-reports/fase-5.md`, butir 11) dan **belum dikerjakan**.
2. **Urutan "daya veto" yang dipajang kedua layar adalah konstanta desain spec §4, belum divalidasi**
   (`preflight/params.py:163` `ITEM_ORDER`). Alat ukur repo ini sendiri membantahnya: item peringkat 1 tidak pernah
   merah, item peringkat 6 merah 92×. Urutan itu berguna sebagai "mana yang lebih dulu dibaca manusia", bukan sebagai
   klaim empiris.
3. **Angka tabel veto tidak terulang dengan `--until` saja.** Perekam menambah baris tiap siklus, jadi jangkar yang
   sama menghasilkan `n` yang berbeda beberapa hari kemudian (terukur 201 → 237). Yang mengulang angkanya adalah
   `--db <salinan beku>`. Alatnya memang memberi tahu saat itu terjadi (`scan_limited` naik, `n` turun, keduanya di
   header) — tetapi resep di `preflight/veto_table.py:68` masih menulis `--until` saja, dan itu resep yang salah.

---

## 8. Apa yang TIDAK kami klaim

- **Tidak ada klaim bahwa vetonya benar.** Lihat §7: `k = 0`, `target_first = 3`, nol selang kepercayaan. "Merah"
  berarti item itu menahan keberangkatan, bukan bahwa menahan itu tepat.
- **Tidak ada klaim performa perdagangan.** Semua posisi practice, nol P&L; ukuran posisi tetap
  (`agent/trade.py` `"sizing": "fixed_notional"`) — tidak ada jalur di mana reliabilitas menggerakkan ukuran.
- **GO tidak berarti "bukti mendukung".** Ia berarti "tidak ada item yang keberatan". Pada kartu GO live di ledger
  kami (id 49), tiga item bersikap `neutral` dan tetap hijau: hijau di sini berarti "tidak ada keberatan", bukan
  "mendukung" (`preflight/params.py:506` `neutral_stance_status`; ADR-017, tercatat **USULAN** meski sudah
  diimplementasikan). Kartunya mengatakan itu dengan kalimat, bukan hanya dengan warna.
- **Item 9 tidak menguji apa pun hari ini.** `risk_reward[0] ≥ min_rr` dengan `min_rr = 1.0` sementara
  `risk_reward = [1.0, 2.0]` di **1426/1426** baris `deep_analysis` yang diperiksa: ambangnya tautologi pada bukti yang
  ada (`preflight/params.py:69-71`, `docs/judge-reports/fase-5.md`).
- **`as_of` yang segar tidak menjamin isi envelope segar, dan item 1 tidak menangkapnya.** Pada 47 baris
  `monitor_market_sentiment_shift` nyata, 47/47 membawa `fear_greed`/`altseason` ber-`data_date` D-0 tetapi
  `funding`/`liquidation` ber-`data_date` **D-1** (umur bukti 25,2–47,5 jam, median 35,8 jam; `docs/api-facts.md` §A,
  sapuan 5 Sep 2026). Yang kami lakukan (TASKS 0.21): kartu **mengucapkannya** — item 8 membawa `funding_data_date`
  dan `liquidation_data_date` di `value`, dan `reason`-nya berbunyi "evidence dates: funding from … (1d before this
  reading)" di setiap cabang, dengan "date not stated" bila tanggalnya null. Yang **tidak** kami lakukan, dan itu
  keputusan tertulis: memveto atasnya — keadaan D-1 normal 47/47, jadi memvetonya akan memerahkan item 8 selamanya.
  Jadi jangan membaca item 1 sebagai janji kesegaran isi envelope.
- **Tidak ada angka keandalan tool RYO di dokumen publik mana pun.** Output tool RYO adalah data RYO: hanya di dalam
  submission, tidak pernah di post sosial (ADR-008, `docs/rules-compliance.md`).
- **Tidak ada "production-ready", "teraudit", atau "100 %".** Yang ada: 1840 tes hijau — ukuran cakupan pemeriksaan
  kami sendiri, bukan bukti bahwa keputusannya benar — dan daftar kegagalan yang bisa direproduksi yang ditulis pihak
  ketiga di `docs/judge-reports/fase-5.md`, termasuk satu lubang yang masih terbuka (§6).
- **Rekaman 30 detik Layar B belum ada** (§5).

---

## 9. Bedanya dengan agen sinyal + guardrail, dan dengan arena P&L

- **Data hilang memveto, dan tidak pernah menjadi angka.** Guardrail yang umum memperlakukan field kosong sebagai
  lolos (tidak ada aturan yang menyala) atau sebagai nol (aturan menyala atas angka karangan). Di sini item wajib yang
  `unspoken` masuk ke `blocking` seperti item merah, dicatat **terpisah** dari merah, dan validator **menolak** item
  `unspoken` yang membawa nilai — termasuk `0` (§3).
- **NO_GO adalah keputusan kelas satu, bukan ketiadaan keputusan.** Ia punya baris ledger, kartu sepuluh baris,
  `decision_hash`, dan halaman sendiri. Sebagian besar agen hanya mencatat apa yang mereka lakukan; di sini yang
  **tidak** dilakukan juga punya alamat dan sebab yang bisa ditunjuk sampai `tool.field` + `snapshot_id` + `as_of`.
- **Jejaknya bisa dibantah, bukan cuma dipamerkan.** `make replay` memvonis ulang dari snapshot beku; satu byte yang
  diubah menghasilkan MISMATCH yang **menyebut snapshot-nya** (§4). Yang tidak bisa diverifikasi mendapat vonis
  "tidak tahu" sendiri, bukan lulus diam-diam.
- **Bukan arena P&L.** Rubric resmi tidak menilai P&L jangka pendek maupun volume panggilan API
  (`docs/api-facts.md` §E), dan repo ini tidak mencetak satu angka P&L pun. Yang dipertaruhkan adalah kualitas
  pembukuan keputusan — termasuk berani mencetak `k = 0` tentang dirinya sendiri (§7).
- **Kartu publik adalah fakta, bukan penghakiman.** Kartu menyebut nilai, sumber, dan waktunya; ia tidak menyebut
  siapa pun salah dan tidak menilai tool RYO di ruang publik (ADR-008).

---

## 10. Track & kepatuhan

Satu BUIDL, tiga track (`docs/api-facts.md` §E: maksimal satu BUIDL per tim; tiap track dinilai terpisah).

| Track | Yang dikirim | Bukti satu perintah |
|---|---|---|
| **T1** agen | agen practice-trade + kartu panggilan 10 item + replay yang bisa membantah dirinya | `make demo` (langkah 1–4, 6), `make replay ID=` |
| **T2** layar | Layar A watchlist + Layar B kartu, keyboard-first, banner SIMULATED, nol skrip | `make ui`, `make demo` langkah 5, `ui/README.md` |
| **T3** skill | `preflight(symbol, intent)` ber-envelope RYO + satu field `simulated` | `make skill ARGS="preflight SOL"`, `make demo` langkah 1 |

Kepatuhan (`docs/rules-compliance.md`):

- **Kredensial hanya dari env**, tidak pernah dilog, di-repr, atau muncul di tes/screenshot. Yang menegakkannya **di
  dalam submission** adalah kode dan tesnya: klien meredaksi kredensial (`core/ryo_client.py`), CLI tidak pernah
  mencetak pesan exception pihak lain (`core/skill_cli.py`), dan log hanya menulis `type(exc).__name__`. Dua kontrol
  lain — `scripts/guard.sh` (memblokir perintah yang mencetak kunci) dan `scripts/gate.sh` (stop hook: ruff + sapuan
  kredensial + penjaga append-only) — adalah **mesin kerja internal yang sengaja tidak dilacak git** (`TASKS.md`
  0.25), jadi klon bersih tidak memilikinya; jangan menilai repo ini atas keduanya. Pemindaian juri nilai-per-nilai
  atas 125 berkas terlacak + seluruh `logs/` → **0 kecocokan** (`docs/judge-reports/fase-5.md`).
- **SIMULATED** di README (paragraf pertama), di banner kedua layar, di setiap envelope CLI, di setiap respons API
  termasuk 400/404/405/422/503, dan di baris penutup `make demo`.
- **Sumber eksternal hanya yang `enabled: true` DAN `approved: true`** di `sources.yaml` (ADR-005) — hari ini dua:
  tool RYO dan satu LLM untuk narasi. `approved` LLM bersandar pada "your own AI model" di landing page, bukan jawaban
  tertulis penyelenggara; itu satu-satunya `approved` yang ditegakkan sendiri, dan narasi berada di luar jalur
  keputusan sehingga demo berjalan tanpanya.
- **Dependency, aset, dan sumber pihak ketiga** didaftarkan di `DISCLOSURE.md` pada commit yang sama, dijaga dua arah
  terhadap `uv.lock` oleh `tests/test_disclosure.py`.
- **Output tool RYO tidak dipublikasikan** di post sosial (ADR-008); draf post ada di `docs/posts/`.

---

## 11. Roadmap jujur

Semua yang di bawah ini **belum ada** hari ini, dan ditulis sebagai belum ada. Dua yang pertama adalah fitur yang
mungkin tidak akan pernah dibangun; lima sisanya adalah utang yang sudah kami sebut sendiri di atas.

1. **Reputasi item yang benar-benar memberi masukan ke keputusan.** Hari ini `grading/` adalah alat **ukur**, bukan
   alat **putus**: tidak satu angka pun darinya masuk ke `preflight`. Syaratnya n ≥ 30 per kelas (spec §6); dengan
   `n_efektif` 1–5 hari ini (§7), jawabannya "belum cukup data".
2. **Item ke-11 Bunryō** (ukuran posisi dari reliabilitas bersyarat rezim, spec §4). `make grade` sudah mencetak
   selnya dan sudah menolak mengeluarkan takaran. Selama gerbang 30 belum terlewati, item ini **tidak akan** muncul —
   bukan diperkirakan, bukan dilonggarkan.
3. **Menutup lubang pengakuan §7 langkah 2** (fixture tanpa blok `synthetic` sama sekali) — §6.
4. **Memberi status pada kolom "daya veto" di kedua layar** ("urutan spec §4, belum divalidasi") — §7 butir 2.
5. **Memperbaiki resep reproduksi tabel veto** menjadi `--db <salinan beku>` — §7 butir 3.
6. **Mengganti nama `01_freshness`** menjadi apa yang benar-benar diukurnya — §7 butir 1.
7. **Rekaman 30 detik Layar B** (artefak manusia; naskah siap di `demo/video-script.md` dan `ui/README.md`).

Urutan dan status per task: `TASKS.md`. Penilaian pihak ketiga terakhir atas repo ini — termasuk skor per kategori,
daftar kegagalan yang bisa direproduksi, dan lima pertanyaan yang belum semuanya terjawab — ada di
`docs/judge-reports/fase-5.md`.
