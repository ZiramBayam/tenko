# Tenko (点呼) — RYO-CHAN Hackathon 2026
> **SIMULATED.** Semua posisi di proyek ini adalah *practice trades* atas market intelligence read-only.
> Tidak ada dana, wallet, kunci penandatangan, atau eksekusi nyata. Setiap envelope keluaran memuat `simulated: true`.

**Status build — 5 Sep 2026: Fase 0, Fase 1, dan Fase 2 selesai (skill `preflight` 10 item, narasi, agen, replay);
Fase 3 (layar) sedang dikerjakan dan BELUM diklaim.** README ini sengaja memisahkan dua hal: **apa yang bisa Anda
jalankan hari ini** (dengan perintah dan outputnya) dan **apa yang masih rancangan** (ditandai BELUM). Penilaian
pihak ketiga terakhir atas repo ini — termasuk daftar kegagalannya — ada di `docs/judge-reports/fase-1.md`; ia menilai
keadaan 1 Sep, sebelum Fase 2 mendarat. Rencana kerja & status per task: `TASKS.md`.

---

## 1. Masalah → arah solusi

Trader ritel bertindak atas sebuah verdict tanpa memeriksa semua bukti yang tersedia, dan — lebih berbahaya — tanpa
tahu bukti mana yang **tidak tersedia**: data basi, seksi `unavailable`, plan yang tidak ada. Agen yang umum
memperlakukan data hilang sebagai nol atau melewatinya diam-diam (`PRD.md` §1).

*Tenko* (点呼) adalah roll call sebelum berangkat; digabung dengan *shisa kanko* (指差喚呼, "tunjuk & sebut").
Produk (ADR-012, `docs/spec.md` §4–§5): sebelum tiap practice trade, agen menunjuk 10 item bukti dari 5 tool RYO
(nilai + field + `as_of`), menyebutnya satu per satu, dan **tidak berangkat** bila ada item merah atau tidak tersebut.
"Tidak berangkat" (NO_GO) dicatat sebagai keputusan kelas satu dengan item penahannya, bukan sebagai ketiadaan.

Sepuluh item itu **berjalan hari ini**: `make skill ARGS="preflight SOL"` mencetak kartunya (§5), `make agent-tick`
menulis keputusan ke ledger SIMULATED, dan `make replay ID=` memvonis ulang keputusan itu dari snapshot beku (§6).
Yang **belum**: satu perintah `make demo` yang menjalankan naskah `docs/spec.md` §7 dari nol, dan layar UI — keduanya
diuraikan apa adanya di §3, dan tidak boleh dinilai sebagai ada.

---

## 2. Yang bisa dijalankan hari ini

Prasyarat: Python 3.12 + [uv](https://docs.astral.sh/uv/) (`docs/versions.md`). Tanpa Node/npm (ADR-001).
Salin `.env.example` → `.env`; kredensial RYO hanya dibaca dari env, tidak pernah dicetak.

```bash
make doctor    # python 3.12.3 · uv 0.12.7 · sqlite 3.45.1 · .env ada · 2 sumber aktif
make test      # 1438 passed, 1 skipped
make lint      # All checks passed!
make skill ARGS="preflight SOL"        # kartu 10 item + GO/NO_GO, satu baris JSON kanonik, exit 0
make agent-tick                        # satu siklus agen (SIMULASI) → baris ledger
make replay ID=28                      # vonis ulang keputusan dari snapshot beku
uv run python scripts/db_status.py     # snapshots / keputusan / trade event di data/ryo.db
bash scripts/daemons.sh status         # perekam hidup? berapa snapshot?
```

| Perintah | Output yang saya lihat 5 Sep 2026 | Berkas |
|---|---|---|
| `make doctor` | Python 3.12.3, uv 0.12.7, sqlite 3.45.1, `.env` ada, 2 sumber aktif | `Makefile`, `sources.yaml` |
| `make test` | **1438 passed, 1 skipped** | `tests/` |
| `make lint` | `All checks passed!` | `pyproject.toml` (ruff) |
| `make skill ARGS="preflight SOL"` | envelope `status:"partial"`, `simulated:true`, `decision:"NO_GO"`, `blocking:['03_regime','08_derivatives']`, exit 0 | `preflight/skill.py`, `core/skill_cli.py` |
| `make replay ID=28` | `IDENTICAL e37eafed…`, `'SOL' NO_GO dari 5 snapshot beku` | `agent/replay.py` |
| `uv run python scripts/db_status.py` | **2382 snapshot** (`analyze_token` 1257 · `deep_analysis` 910 · `compare_tokens` 86 · `market_overview` 46 · `monitor_market_sentiment_shift` 46 · `scan_market` 37), terbaru berumur **50 dtk**, **40 keputusan**, **12 trade event** | `scripts/db_status.py`, `core/store.py` |
| `bash scripts/daemons.sh status` | perekam hidup, snapshot tumbuh per siklus | `core/recorder.py`, `scripts/daemons.sh` |

Yang berdiri di balik angka itu, masing-masing dengan berkasnya: klien 6 tool RYO dengan token bucket dan redaksi
kredensial (`core/ryo_client.py`) · store hanya-tambah dengan `payload_hash` per baris (`core/store.py`, ADR-006) ·
kontrak `SkillResult` (`core/skill.py`) · gate sumber `approved`/`enabled` (`core/sources.py`, `sources.yaml`, ADR-005)
· 10 item preflight + params ber-hash (`preflight/items.py`, `preflight/params.py`) · skill yang merakit kartu dari
store (`preflight/skill.py`) · narasi LLM yang tidak menyentuh keputusan (`preflight/narrate.py`) · agen & replay
(`agent/main.py`, `agent/replay.py`) · perekam pasar per siklus (`core/recorder.py`).

**Mode offline (tanpa jaringan, tanpa kredensial):** `core/ryo_client.py` menyediakan `FixtureClient` yang membaca
`docs/samples/*.json` (6 sampel live yang diambil 1 Sep 2026) alih-alih memanggil server, dan tidak memegang
kredensial sama sekali; seluruh suite tes berjalan atasnya, dan paket `agent/` diuji tidak pernah membuka soket
(`tests/test_agent.py::test_agent_never_opens_a_socket`). `make demo` yang menyatukan fixture itu menjadi satu naskah
**belum ada** (§3).

---

## 3. Naskah §7 langkah demi langkah — apa yang berjalan, apa yang BELUM

Naskah demo `docs/spec.md` §7, apa adanya per 5 Sep 2026. Kolom "status" hanya boleh dibaca setegas kolom "bukti":

| # | Langkah §7 | Status | Bukti hari ini |
|---|---|---|---|
| 0 | `make demo` (satu perintah, naskah utuh) | **BELUM** | exit ≠ 0 dengan pesan "TASKS 3.4 belum dikerjakan — `scripts/demo.sh` belum ada" (sengaja gagal keras, `Makefile:30-35`) |
| 1 | `make skill ARGS="preflight SOL"` → 10 item, GO/NO_GO, blocking, snapshot_ids | **BERJALAN** | kartu 10 item ditempel di §5; `status:"partial"`, `decision:"NO_GO"`, `blocking:['03_regime','08_derivatives']` (`preflight/skill.py`) |
| 2 | fixture rusak → item *unspoken* / item 2 merah | sebagian | mekanismenya ada dan diuji per item (`tests/test_items.py`, mis. `::test_partial_deep_analysis_without_market_wide_derivatives_leaves_item8_unspoken`, `::test_item2_unspoken_when_no_tool_answered_at_all`). Dua **fixture demo** yang berbeda (satu-tool-gagal → item 2 **merah**; semua-gagal → `status:"unavailable"`) masih TASKS 2.2c |
| 3 | `make agent-tick` ×3 → ≥1 GO + ≥1 NO_GO; `make replay ID=` IDENTICAL; tamper → MISMATCH | sebagian | agen menulis keputusan dari store nyata (`agent/main.py`); `make replay ID=28` → `IDENTICAL`; tamper 1 byte → `MISMATCH` ber-`snapshot_id` (`tests/test_agent_replay.py::test_one_tampered_byte_in_the_copy_gives_mismatch_naming_the_snapshot`). Cabang **GO** hari ini hanya terlihat di tes (`tests/test_agent.py::test_three_ticks_produce_at_least_one_go_and_one_no_go`), bukan dari bukti live — lihat catatan di bawah tabel |
| 4 | narasi LLM → `narration_unavailable`, decision_hash sama | sebagian | modul + 85 tes ada (`preflight/narrate.py`, `tests/test_narrate.py`); tanpa kredensial klien **tidak pernah dibangun** dan hasilnya `narration_unavailable`. **Belum ada pemanggil produksi** dan jalur live **nol kali dijalankan** (TASKS 2.3 "Batas yang diakui") |
| 5 | `make ui` Layar A/B, keyboard-first, banner SIMULATED | **BELUM diklaim** | `ui/app.py` ada di working tree tetapi **belum di-commit** dan belum ada laporan yang menunjukkannya berjalan (TASKS 3.1/3.2) |
| 6 | dua tanggal → keputusan berbeda | **BELUM** | TASKS 4.7; belum ada dua kartu ber-hash berbeda yang bisa ditunjuk |

**Catatan yang akan ditanyakan juri, ditulis lebih dulu.** Pada bukti live hari ini item 8 menahan: `deep_analysis`
melaporkan `liquidation.pressure_state='extreme'` dan `funding.crowding_state='long_crowding'`, jadi `08_derivatives`
merah untuk rencana long (lihat kartu SOL di §5). Itu produk yang bekerja sebagaimana mestinya — item yang menahan
trade saat derivatif stres — tetapi konsekuensinya jujur: **selama keadaan itu bertahan, "≥1 GO" pada §7 langkah 3
tidak bisa diperagakan dari data live.** Ke-13 keputusan generasi params yang berlaku (id 28-40) semuanya NO_GO, dan
ke-12 `trade_events` di DB adalah warisan generasi lama (id 1-19, kini `STALE_ALGO` — §7): **tidak ada posisi baru
yang dibuka sejak item 8 merah.** GO bukan cabang mati — ia dieksekusi tes di atas — tetapi kami tidak mengklaim GO
live hari ini.

Ringkasnya: **ada agen, ada kartu panggilan 10 item, ada narasi, ada replay; belum ada `make demo`, belum ada satu
layar pun yang kami klaim, belum ada demo dua tanggal.**

---

## 4. Arsitektur (rancangan `docs/spec.md` §2, ditandai per komponen)

```
 RYO tools (6) ──► core.ryo_client [ADA] ──► core.store (snapshot append-only, hash) [ADA]
                                                     │
                            preflight.skill [ADA] ◄──┘  freeze(snapshot_ids) [ADA]
                            10 item → GO | NO_GO + blocking
                                     │                      │
                             agent (practice trade      preflight.narrate (LLM,
                              plan / declined) [ADA]     tidak menyentuh keputusan)
                                     │                   [ADA, tanpa pemanggil produksi]
                             agent.replay ID= [ADA]
                                     │
                             ui: Layar A watchlist · Layar B kartu panggilan · banner SIMULATED
                                                  [BELUM diklaim — belum di-commit]
 core.recorder (per siklus) [ADA] ──► grading (opsional): outcome → reputasi item  [BELUM]
```

Aturan yang mengikat arah panah: tidak ada LLM di jalur keputusan (ADR-003) — keputusan adalah fungsi deterministik
atas snapshot beku, dan narasi hanya menerjemahkan hasilnya. Panah itu bukan gambar saja: `narrate`/`attach` hanya
menambah `summary["narration"]` dan terbukti tidak menggeser `decision_hash` bahkan saat kartu diracuni dan narasinya
bermusuhan (`preflight/narrate.py` docstring "Batas yang diakui", `tests/test_narrate.py`), dan agen diuji tidak
pernah mengimpor klien live (`tests/test_agent.py::test_agent_package_never_imports_the_live_client`).

---

## 5. Bentuk output = envelope RYO (ADR-013/015)

Tidak ada spec formal untuk skill T3 (`docs/api-facts.md` §E), jadi bentuk yang paling murah dipakai RYO adalah bentuk
tool mereka sendiri. `SkillResult` memuat **sepuluh field top-level yang sama** dengan envelope RYO
(`schema_version, tool, status, data_mode, as_of, request, data, summary, availability, warnings`) ditambah **tepat
satu** field, `simulated` — yang diwajibkan `docs/rules-compliance.md`. Invarian itu diuji mekanis terhadap keenam
sampel di `docs/samples/`: `tests/test_skill.py::test_top_level_invariant_holds_for_all_six_samples`.

Keluaran nyata hari ini — `make skill ARGS="preflight SOL"` (5 Sep 2026) atas snapshot yang sudah ada di
`data/ryo.db`, nol panggilan live. Envelopenya `status:"partial"`, `simulated:true`, dan kartunya
`decision:"NO_GO"` dengan `blocking:['03_regime','08_derivatives']`. Sepuluh baris "tunjuk & sebut"-nya, apa adanya
(kolom `reason` dipotong oleh lebar terminal, bukan oleh kami — jalankan perintahnya untuk baris penuh):

```
01_freshness       green   oldest evidence is compare_tokens at 76.0 min (limit 120 min) -> every envelope is within the li…
02_completeness    green   all 5 tools answered and every section used by items 3-10 reports available -> no hole in the ro…
03_regime          red     market regime 'risk_off' (bearish) -> opposed to the long plan
04_sentiment_shift green   fear & greed moved 'down' but material_shift=false -> nothing pushes against the plan
05_verdict_quick   green   quick verdict 'neutral' (neutral) -> neutral: not opposed to the plan, but not a confirmation ei…
06_confluence      green   confluence CONFIRMED at 1 (limit 1), gates passed: momentum, market_activity -> the evidence agr…
07_verdict_deep    green   deep verdict 'neutral' (neutral) -> neutral: not opposed to the plan, but not a confirmation ei…
08_derivatives     red     liquidation.pressure_state='extreme'; funding.crowding_state='long_crowding' -> derivatives are …
09_plan            green   long plan entry 101.413 / stop 94.552 / targets 108.273, 115.134, risk_reward[0]=1 >= 1 -> the p…
10_relative        green   SOL is last on momentum only -> last on momentum but not on market activity: not opposed, not a…
```

Setiap baris menunjuk field RYO-nya, menyebut nilainya, lalu menyatakan arahnya terhadap rencana. Dua baris merah
menahan keputusan, dan namanya ikut ke `blocking` — NO_GO di sini adalah keputusan yang beralasan, bukan ketiadaan.

**Yang TIDAK bisa saya tempel hari ini: kartu dengan item *unspoken*.** Saya memeriksa keempat simbol inti
(SOL, BTC, ETH, BNB) pada 5 Sep 2026 dan **nol** item `unspoken` di keempatnya — buktinya lengkap, jadi semua item
terucap. Mekanisme "tidak tersebut" karena itu ditunjukkan lewat kontrak dan tesnya, bukan lewat kartu karangan:
`ItemResult` **menolak** item `unspoken` yang membawa `value` — termasuk `0` dan `0.0`, bentuk kebohongan yang persis
ingin kami cegah (`core/skill.py::_unspoken_carries_no_value`, `tests/test_skill.py::test_unspoken_item_may_not_carry_a_value`)
· `availability` wajib menandai `"unspoken"` **jika dan hanya jika** item itu unspoken
(`tests/test_skill.py::test_unspoken_item_must_be_marked_unspoken_in_availability`) · null / `unavailable` / kosakata
tak dikenal / angka non-finit menjadi item `unspoken` ber-`reason`, bukan nol (`tests/test_items.py`, ~30 tes bernama
`*_unspoken_*`) · item wajib yang `unspoken` **memveto** keputusan sementara item 8 yang `unspoken` tidak
(`::test_required_item_unspoken_does_veto_the_decision`, `::test_item8_unspoken_does_not_veto_the_decision`).

Perhatikan juga yang **tidak** terjadi di envelope: tidak ada `data` karangan, tidak ada `as_of` yang dibuat dari
`now()`, tidak ada nol sebagai pengganti "tidak tahu". Validator menolak `data` non-null saat `status="unavailable"`
dan sebaliknya menolak `ok`/`partial` tanpa data (`core/skill.py`, `_honesty_invariants`), dan angka agregat mana pun
di kartu ini disandingkan dengan `n`-nya (`n_spoken` ≤ `n_items`, TASKS 2.2 AC (a)-(c)).

---

## 6. Replay & determinisme

Yang **terbukti**: `make skill ARGS="preflight SOL"` dua kali menghasilkan byte yang identik; `core/replay.py`
membekukan snapshot (`freeze`), memverifikasi ulang hash-nya (`verify_bundle`), dan menghitung `decision_hash` atas
material kanonik. Juri Fase 1 menguji tiga batas yang diakui docstring modul itu satu per satu dan melaporkan
ketiganya benar persis seperti tertulis (`docs/judge-reports/fase-1.md`, tabel tes destruktif baris f).

CLI-nya sekarang **ada** (`agent/replay.py`; `make replay ID=<trade_id>` → `uv run python -m agent.replay $(ID)`,
`Makefile:24-25`), dan ia sudah dijalankan atas ke-40 baris ledger SIMULATED — hasilnya dipilah per vonis di §8.
Yang bisa Anda jalankan sendiri: `uv run python -m agent.replay 28` →
`IDENTICAL e37eafeddd47f22b344805a588a44d544caf855b5c2b5503ea94cda86600ba1d`,
`'SOL' NO_GO dari 5 snapshot beku (2166, 2167, 2169, 2170, 2219); blocking: 08_derivatives`.

Vonisnya **lima** (`grep -n '^VERDICT_' agent/replay.py` → `agent/replay.py:116-123`), dan hanya **satu** yang berarti
"terverifikasi": `IDENTICAL` (`VERIFIED_VERDICTS = frozenset({VERDICT_IDENTICAL})`, `agent/replay.py:126`).
`MISMATCH` (exit 1) adalah **temuan** — bukti tidak mereproduksi keputusan; `STALE_ALGO`, `STALE_PARAMS`, dan
`NO_EVIDENCE` adalah **pengakuan tidak tahu**. Empat dari lima keluar 0 (`EXIT_ZERO_VERDICTS`, `agent/replay.py:132-134`
— semua kecuali `MISMATCH`); **empat itu cacah vonis yang keluar 0, bukan cacah vonis**, dan exit 0 di sini berarti
"perintah menghasilkan vonis", bukan "keputusan terverifikasi".
Yang dipatok tes, bukan cuma didokumentasikan: satu byte `payload_json` yang diubah pada salinan DB → `MISMATCH` yang
menyebut snapshot-nya (`tests/test_agent_replay.py::test_one_tampered_byte_in_the_copy_gives_mismatch_naming_the_snapshot`)
· daftar bukti yang dikosongkan → `MISMATCH`, bukan `NO_EVIDENCE` yang keluar 0
(`::test_a_row_whose_evidence_list_was_emptied_is_mismatch_not_no_evidence`) · generasi params lain → `STALE_PARAMS`,
bukan tuduhan (`::test_a_decision_from_another_params_generation_is_stale_params_not_mismatch`).

Yang **belum**: `make demo` — naskah `docs/spec.md` §7 dari nol dengan fixture offline — masih TASKS 3.4 dan sengaja
gagal keras (`Makefile:30-33`). Replay yang berjalan bukan naskah demo yang berjalan.

---

## 7. Tiga generasi di `data/ryo.db` (ADR-016, TASKS 0.14)

`data/ryo.db` memuat lebih dari satu generasi. 19 baris `decisions` + 9 `trade_events` pertama ditulis oleh implementasi yang
**dihapus 1 Sep 2026**. Store bersifat hanya-tambah (ADR-006), sehingga baris itu tidak dihapus, tidak ditulis ulang,
dan tidak disembunyikan. `make replay ID=13` **sekarang benar-benar** melaporkannya `STALE_ALGO` — snapshot buktinya
utuh, algoritma yang memproduksinya tidak ada lagi. Itu perilaku yang disengaja, bukan kerusakan, dan bukan tamper.
Pembedanya satu hal saja: ada atau tidaknya kunci `algo` di `card_json` (ADR-016), dan urutan pemeriksaannya mengikat —
`algo` usang diperiksa **sebelum** `params_hash`, sehingga baris pra-1-Sep tetap `STALE_ALGO` dan tidak berubah vonis
tiap kali ambang bergeser (`agent/replay.py:52-53`;
`tests/test_agent_replay.py::test_a_row_without_algo_is_stale_algo_not_mismatch`).

Sejak TASKS 0.14 generasinya ada tiga, bukan dua: 19 baris `STALE_ALGO` (id 1-19), 8 baris `STALE_PARAMS` (id 20-27,
ditulis sebelum `params_hash` bergeser 4 Sep), dan 13 baris yang berlaku (id 28-40). Tidak satu pun dari 27 baris
lama itu kami sebut salah — kami hanya menyatakan tidak memeriksanya (§8).

Ke-13 baris generasi berlaku itu semuanya NO_GO, dan ke-12 `trade_events` di DB adalah **warisan** generasi lama
(keputusan GO id 1-19, yang kini `STALE_ALGO`). Konsekuensinya ditulis apa adanya: **sejak `08_derivatives` merah pada
bukti live, tidak ada posisi baru yang dibuka.** Itu item yang bekerja, bukan agen yang macet (§3, catatan di bawah
tabel).

Alasannya sama dengan tesis produknya: yang tidak tersebut wajib disebut sebagai tidak tersebut. Menyembunyikan 19
baris itu akan lebih rapi dan lebih tidak jujur.

---

## 8. Apa yang TIDAK kami klaim

- **Tidak ada hasil empiris.** `docs/reports/` masih kosong; tidak ada satu pun angka akurasi, hit-rate, PnL, atau
  reputasi item di repo ini. Lapisan grading (`docs/spec.md` §6) opsional dan baru sah bila n cukup; di bawah n=30
  jawabannya adalah "belum cukup data", bukan angka kecil.
- **Tidak ada angka keandalan tool RYO di dokumen publik mana pun** — output tool RYO adalah data RYO dan hanya boleh
  muncul di dalam submission (ADR-008, `docs/rules-compliance.md`).
- **Tidak ada klaim "production-ready", "teraudit", atau "100%".** Yang ada: **1438 tes hijau, 1 skipped**
  (`make test`, 5 Sep 2026), dan daftar kegagalan yang bisa direproduksi di `docs/judge-reports/fase-1.md`
  §"Kegagalan yang bisa direproduksi". Angka tes adalah ukuran cakupan pemeriksaan kami sendiri, bukan bukti bahwa
  keputusannya benar.
- **Tidak ada klaim performa perdagangan.** Semua posisi adalah practice; P&L jangka pendek juga tidak dinilai rubric.
- **Item 1 (`01_freshness`) mengukur kesegaran di dalam satu bundel, bukan terhadap jam dinding.** Acuannya adalah
  `as_of` terbaru di bundel itu, sehingga bila RYO menyajikan seluruh bundel yang usang seragam, item 1 tetap hijau.
  Kami menerima batas ini secara sadar: pada **2443** baris snapshot nyata, selisih `ts_utc − as_of` bermedian
  **0,00 jam** (+0,48 detik) dengan maksimum **0,02 jam** (+71,04 detik, id=451); satu-satunya sampel yang melenceng
  (**60,7 jam, n=47**) adalah blok fixture salinan `docs/samples` (`id 1597-1643`). Yang lebih penting: **`as_of` yang
  segar tidak menjamin isi envelope segar.** Pada 47 baris `monitor_market_sentiment_shift` nyata, 47/47 membawa
  `fear_greed`/`altseason` ber-`data_date` D-0 tetapi `funding`/`liquidation` ber-`data_date` **D-1** — umur bukti
  25,2-47,5 jam, median 35,8 jam. Jadi sebuah envelope dengan `as_of` beberapa detik yang lalu bisa memuat bukti
  berumur satu setengah hari. Kami **belum** menangani ini; ia diketahui, terbuka sebagai TASKS 0.21, dan tidak boleh
  dibaca sebagai janji kesegaran. Semua angka di butir ini dari `docs/api-facts.md` §A ("Skew `as_of` vs `ts_utc`",
  diverifikasi 5 Sep 2026, sapuan `mode=ro` atas `max(id)=2490`).
- **Angka replay tidak boleh dijumlahkan.** `params_hash` yang berlaku hari ini (5 Sep 2026) adalah
  `3d39fb1bd1a03b7f5269f53fec347d34645d2b402ff1c2de47e53f19d69616b7`
  (`uv run python -c "from preflight.params import DEFAULT_PARAMS; print(DEFAULT_PARAMS.params_hash())"`). Ia bergeser
  4 Sep (commit `6023700`, TASKS 0.12) karena empat nilai kosakata yang belum pernah kami lihat muncul pertama kali
  di snapshot id=1976 (2026-09-04T11:21:24Z): `sentiment_regime='complacent_crowding'`,
  `liquidation.pressure_state='extreme'`, `funding.crowding_state='long_crowding'`, `fear_greed.change_direction='flat'`.
  Memetakannya adalah keputusan sadar pemilik repo, bukan kecelakaan. Menjalankan `uv run python -m agent.replay <id>`
  atas ke-40 baris ledger (SIMULATED, 5 Sep 2026) memberi tiga vonis yang **berbeda arti** dan karena itu tidak kami
  gabungkan: `IDENTICAL` 13 (id 28-40, generasi params yang berlaku), `STALE_PARAMS` 8 (id 20-27), `STALE_ALGO` 19
  (id 1-19). Dua vonis lain yang dikenal CLI ini (`MISMATCH`, `NO_EVIDENCE`; lima vonis total, §6) **nol kali** muncul
  atas ledger hari ini — itu keadaan hari ini, bukan jaminan.
  `STALE_PARAMS` — vonis yang mendarat 4 Sep (commit `bab34d5`, TASKS 0.14) — berarti **kami tidak menilai
  baris itu sama sekali**, bukan bahwa kami menemukannya salah; di repo ini `MISMATCH` berarti bukti tidak
  mereproduksi keputusan, sebuah tuduhan yang tidak kami dukung untuk baris yang aturannya memang berbeda (sebelum
  0.14 baris-baris itu memang dilaporkan `MISMATCH`; itu dicabut). Contoh yang bisa dijalankan juri:
  `uv run python -m agent.replay 28` → `IDENTICAL e37eafeddd47f22b344805a588a44d544caf855b5c2b5503ea94cda86600ba1d`,
  `'SOL' NO_GO dari 5 snapshot beku (2166, 2167, 2169, 2170, 2219); blocking: 08_derivatives`. Yang tetap TIDAK kami
  klaim: bahwa 13 `IDENTICAL` itu mengatakan sesuatu tentang mutu keputusannya, atau tentang keandalan sumber mana pun.

---

## 9. Track & kepatuhan

Satu BUIDL, tiga track (`docs/api-facts.md` §E: maksimal satu BUIDL per tim):

| Track | Rencana | Status |
|---|---|---|
| T3 | skill `preflight(symbol)` dengan bentuk envelope RYO | kontrak, CLI, dan isi skill ADA (`preflight/skill.py`, `preflight/items.py`, `preflight/params.py`); kartu nyata ditempel di §5 (`make skill ARGS="preflight SOL"`) dan bisa diverifikasi ulang lewat `uv run python -m agent.replay 28` |
| T1 | agen practice-trade + kartu panggilan + replay | ADA sebagian: agen menulis keputusan (`agent/main.py`, `make agent-tick`) dan replay memvonis 40 baris — 13 `IDENTICAL` (id 28-40), sisanya dinyatakan tidak dinilai, bukan salah (§8). **`make demo` (naskah §7 utuh) BELUM**, TASKS 3.4 |
| T2 | watchlist urut daya veto + kartu keyboard-first | BELUM diklaim: `ui/app.py` ada di working tree tetapi **belum di-commit** dan belum ada laporan yang menunjukkannya berjalan (TASKS 3.1/3.2) |

Kepatuhan (`docs/rules-compliance.md`): kredensial hanya dari env dan tidak pernah dilog atau di-repr · label
SIMULATED di README dan (nanti) di semua layar · jalur nyata hanya memakai sumber yang `enabled: true` **dan**
`approved: true` di `sources.yaml` — hari ini dua sumber: tool RYO dan satu LLM untuk narasi · dependency, aset, dan
sumber pihak ketiga didaftarkan di `DISCLOSURE.md` (32 paket `uv.lock`, dijaga `tests/test_disclosure.py`).

---

## 10. Roadmap jujur (urutan `TASKS.md`)

Sudah mendarat (jangan dibaca sebagai rencana): `DISCLOSURE.md` lengkap untuk isi `uv.lock` hari ini · 10 item
preflight + `params_hash` beku (2.1, 2.2) · narasi yang tidak menyentuh keputusan (2.3) · agen + replay CLI dengan
vonis yang dibedakan artinya (2.4). Yang masih di depan, berurutan:

1. UI Layar A watchlist (urut daya veto) + Layar B kartu panggilan, keyboard-first, banner SIMULATED — termasuk label
   wajib "narasi model, **tidak diverifikasi terhadap bukti**" berdampingan dengan `reason` deterministik (3.1, 3.2).
   Berkasnya ada di working tree tetapi belum di-commit; sampai ada laporan yang menunjukkannya berjalan, T2 tidak
   kami klaim.
2. `make demo`: naskah §7 dari fixture offline, dua run identik atas DB tmp baru, `decision_hash` identik apa adanya
   (3.4).
3. Tabel daya veto empiris per item, ber-`n`, dihitung atas seluruh `data/ryo.db` secara baca-saja — termasuk kalimat
   eksplisit "item ini belum pernah menahan apa pun dalam n=<N> amatan" untuk item yang nol kali merah (2.1b). Tabel
   itu **belum dijalankan**; tidak ada angkanya di README ini.
4. Dua fixture kegagalan berbeda untuk §7 langkah 2 (2.2c) dan bukti dua tanggal dengan minimal satu NO_GO yang
   penyebabnya data **hilang**, bukan angka jelek (4.7).
5. Chaos test satu perintah: host RYO mati · kill -9 perekam mid-siklus · 429 + `Retry-After` · DB dihapus saat UI
   hidup → status jujur, nol traceback, laporannya ke `docs/reports/` (4.5). Direncanakan, belum ada laporannya.
6. **Opsional, belum dibangun, mungkin tidak dibangun:** grading outcome → reputasi item ("item ini menahan k dari n
   trade yang akan rugi", Wilson CI), dan item ke-11 *Bunryō* (ukuran posisi dari reliabilitas bersyarat rezim).
   Syaratnya n ≥ 30 per kelas (ADR-012 butir 4, `docs/spec.md` §6); di bawah itu jawabannya "belum cukup data".
   Bila datanya tidak cukup, keduanya tidak akan muncul — bukan diperkirakan.
