# Tenko (点呼) — RYO-CHAN Hackathon 2026
> **SIMULATED.** Semua posisi di proyek ini adalah *practice trades* atas market intelligence read-only.
> Tidak ada dana, wallet, kunci penandatangan, atau eksekusi nyata. Setiap envelope keluaran memuat `simulated: true`.

**Status build — 1 Sep 2026: Fase 0 & Fase 1 (fondasi) selesai; Fase 2 (skill `preflight` + agen) sedang dikerjakan.**
README ini sengaja memisahkan dua hal: **apa yang bisa Anda jalankan hari ini** (dengan perintah dan outputnya) dan
**apa yang masih rancangan** (ditandai BELUM). Penilaian pihak ketiga terakhir atas repo ini — termasuk daftar
kegagalannya — ada di `docs/judge-reports/fase-1.md`. Rencana kerja: `TASKS.md`.

---

## 1. Masalah → arah solusi

Trader ritel bertindak atas sebuah verdict tanpa memeriksa semua bukti yang tersedia, dan — lebih berbahaya — tanpa
tahu bukti mana yang **tidak tersedia**: data basi, seksi `unavailable`, plan yang tidak ada. Agen yang umum
memperlakukan data hilang sebagai nol atau melewatinya diam-diam (`PRD.md` §1).

*Tenko* (点呼) adalah roll call sebelum berangkat; digabung dengan *shisa kanko* (指差喚呼, "tunjuk & sebut").
**Rancangan produk** (ADR-012, `docs/spec.md` §4–§5 — **belum berjalan**, lihat §3 di bawah): sebelum tiap practice
trade, agen akan menunjuk 10 item bukti dari 5 tool RYO (nilai + field + `as_of`), menyebutnya, dan **tidak berangkat**
bila ada item merah atau tidak tersebut. "Tidak berangkat" (NO_GO) dicatat sebagai keputusan kelas satu dengan item
penahannya, bukan sebagai ketiadaan.

Yang **sudah menjadi kode hari ini** adalah fondasi kejujurannya: kontrak keluaran yang menolak menampilkan angka
tanpa bukti, store yang tidak bisa menghapus, dan primitif replay. Bagian §2 memberi perintahnya.

---

## 2. Yang bisa dijalankan hari ini

Prasyarat: Python 3.12 + [uv](https://docs.astral.sh/uv/) (`docs/versions.md`). Tanpa Node/npm (ADR-001).
Salin `.env.example` → `.env`; kredensial RYO hanya dibaca dari env, tidak pernah dicetak.

```bash
make doctor    # python 3.12.3 · uv 0.12.7 · sqlite 3.45.1 · .env ada · 2 sumber aktif
make test      # 536 passed, 1 skipped  (~42 dtk)
make lint      # All checks passed!  + 34 berkas terformat
make skill ARGS="preflight SOL"        # satu baris JSON kanonik, exit 0
python3 scripts/db_status.py           # snapshots / keputusan / trade event di data/ryo.db
bash scripts/daemons.sh status         # perekam hidup? berapa snapshot?
```

| Perintah | Output yang saya lihat 1 Sep 2026 | Berkas |
|---|---|---|
| `make doctor` | Python 3.12.3, uv 0.12.7, sqlite 3.45.1, `.env` ada, 2 sumber aktif | `Makefile`, `sources.yaml` |
| `make test` | **536 passed, 1 skipped** | `tests/` |
| `make lint` | `All checks passed!` | `pyproject.toml` (ruff) |
| `make skill ARGS="preflight SOL"` | envelope `status:"unavailable"`, exit 0, dua run **byte-identik** | `core/skill_cli.py`, `core/skill.py` |
| `bash scripts/daemons.sh status` | perekam hidup, **689+ snapshot** di `data/ryo.db` (tumbuh per jam) | `core/recorder.py`, `scripts/daemons.sh` |

Fondasi yang berdiri di balik angka itu, masing-masing dengan berkasnya: klien 6 tool RYO dengan token bucket dan
redaksi kredensial (`core/ryo_client.py`) · store hanya-tambah dengan `payload_hash` per baris (`core/store.py`,
ADR-006) · kontrak `SkillResult` (`core/skill.py`) · gate sumber `approved`/`enabled` (`core/sources.py`, `sources.yaml`,
ADR-005) · primitif replay `freeze`/`verify_bundle`/`decision_hash` (`core/replay.py`) · perekam pasar per jam
(`core/recorder.py`).

**Mode offline (tanpa jaringan, tanpa kredensial):** `core/ryo_client.py` menyediakan `FixtureClient` yang membaca
`docs/samples/*.json` (6 sampel live yang diambil 1 Sep 2026) alih-alih memanggil server, dan tidak memegang
kredensial sama sekali. `make demo` yang menyatukannya menjadi satu naskah **belum ada** (§3).

---

## 3. Yang BELUM dibangun — jangan dinilai sebagai ada

Naskah demo `docs/spec.md` §7, apa adanya per 1 Sep 2026:

| # | Langkah §7 | Status | Bukti hari ini |
|---|---|---|---|
| 0 | `make demo` | **BELUM** | exit ≠ 0 dengan pesan "TASKS 3.4 belum dikerjakan — `scripts/demo.sh` belum ada" (sengaja gagal keras) |
| 1 | `make skill ARGS="preflight SOL"` | sebagian | envelope sah & deterministik, tetapi `status:"unavailable"` karena skill belum terdaftar (TASKS 2.2) |
| 2 | fixture partial → item *unspoken* | **BELUM** | paket `preflight/` masih kosong; 10 item belum ada |
| 3 | `make agent-tick`, `make replay ID=` | **BELUM** | `No module named agent.main` / `agent.replay`; primitif replay ada di `core/replay.py` |
| 4 | narasi LLM → `narration_unavailable` | **BELUM** | `preflight/narrate.py` belum ada |
| 5 | `make ui` Layar A/B | **BELUM** | `Could not import module "ui.app"` |
| 6 | dua tanggal → keputusan berbeda | **BELUM** | bergantung pada langkah 1–3 |

Ringkasnya: **belum ada agen, belum ada kartu panggilan, belum ada 10 item preflight, belum ada narasi LLM, belum ada
satu layar pun.** Semua kalimat "agen akan…" di README ini adalah rancangan yang tertulis di `docs/spec.md`, bukan
laporan tentang perangkat lunak yang berjalan.

---

## 4. Arsitektur (rancangan `docs/spec.md` §2, ditandai per komponen)

```
 RYO tools (6) ──► core.ryo_client [ADA] ──► core.store (snapshot append-only, hash) [ADA]
                                                     │
                            preflight.skill [BELUM] ◄┘  freeze(snapshot_ids) [ADA]
                            10 item → GO | NO_GO + blocking
                                     │                      │
                             agent (practice trade      preflight.narrate (LLM,
                              plan ATR / declined)        tidak menyentuh keputusan)
                                  [BELUM]                    [BELUM]
                                     │                      │
                             ui: Layar A watchlist · Layar B kartu panggilan · banner SIMULATED  [BELUM]
 core.recorder (per jam) [ADA] ──► grading (opsional): outcome → reputasi item  [BELUM]
```

Aturan yang mengikat arah panah: tidak ada LLM di jalur keputusan (ADR-003) — keputusan adalah fungsi deterministik
atas snapshot beku, dan narasi hanya menerjemahkan hasilnya.

---

## 5. Bentuk output = envelope RYO (ADR-013/015)

Tidak ada spec formal untuk skill T3 (`docs/api-facts.md` §E), jadi bentuk yang paling murah dipakai RYO adalah bentuk
tool mereka sendiri. `SkillResult` memuat **sepuluh field top-level yang sama** dengan envelope RYO
(`schema_version, tool, status, data_mode, as_of, request, data, summary, availability, warnings`) ditambah **tepat
satu** field, `simulated` — yang diwajibkan `docs/rules-compliance.md`. Invarian itu diuji mekanis terhadap keenam
sampel di `docs/samples/`: `tests/test_skill.py::test_top_level_invariant_holds_for_all_six_samples`.

Keluaran nyata hari ini (`make skill ARGS="preflight SOL"`, `summary` dipangkas dalam kutipan — jalankan perintahnya
untuk baris penuh):

```json
{"as_of":null,"availability":{},"data":null,"data_mode":"unknown","request":{"args":["SOL"],"skill":"preflight"},"schema_version":"1.0","simulated":true,"status":"unavailable","summary":{...},"tool":"preflight","warnings":["skill 'preflight' not registered"]}
```

Perhatikan yang **tidak** terjadi: tidak ada `data` karangan, tidak ada `as_of` yang dibuat dari `now()`, tidak ada
nol sebagai pengganti "tidak tahu". Validator menolak `data` non-null saat `status="unavailable"` dan sebaliknya
menolak `ok`/`partial` tanpa data (`core/skill.py`, `_honesty_invariants`). Contoh kartu berisi 10 item dengan satu
item *unspoken* yang menghasilkan NO_GO **belum bisa ditempel di sini** — skillnya belum ada (TASKS 2.2).

---

## 6. Replay & determinisme

Yang **terbukti**: `make skill ARGS="preflight SOL"` dua kali menghasilkan byte yang identik; `core/replay.py`
membekukan snapshot (`freeze`), memverifikasi ulang hash-nya (`verify_bundle`), dan menghitung `decision_hash` atas
material kanonik. Juri Fase 1 menguji tiga batas yang diakui docstring modul itu satu per satu dan melaporkan
ketiganya benar persis seperti tertulis (`docs/judge-reports/fase-1.md`, tabel tes destruktif baris f).

CLI-nya sekarang **ada** (`agent/replay.py`; `make replay ID=<trade_id>` → `uv run python -m agent.replay $(ID)`,
`Makefile:24-25`), dan ia sudah dijalankan atas ke-39 baris ledger SIMULATED — hasilnya dipilah per vonis di §8.
Yang bisa Anda jalankan sendiri: `uv run python -m agent.replay 28` →
`IDENTICAL e37eafeddd47f22b344805a588a44d544caf855b5c2b5503ea94cda86600ba1d`,
`'SOL' NO_GO dari 5 snapshot beku (2166, 2167, 2169, 2170, 2219); blocking: 08_derivatives`.

Vonisnya empat, bukan tiga, dan hanya **satu** yang berarti "terverifikasi": `IDENTICAL` (`VERIFIED_VERDICTS =
frozenset({VERDICT_IDENTICAL})`, `agent/replay.py:126`). `MISMATCH` (exit 1) adalah **temuan** — bukti tidak
mereproduksi keputusan; `STALE_ALGO`, `STALE_PARAMS`, dan `NO_EVIDENCE` (exit 0) adalah **pengakuan tidak tahu**.
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
ditulis sebelum `params_hash` bergeser hari ini), dan 12 baris yang berlaku (id 28-39). Tidak satu pun dari 27 baris
lama itu kami sebut salah — kami hanya menyatakan tidak memeriksanya (§8).

Alasannya sama dengan tesis produknya: yang tidak tersebut wajib disebut sebagai tidak tersebut. Menyembunyikan 19
baris itu akan lebih rapi dan lebih tidak jujur.

---

## 8. Apa yang TIDAK kami klaim

- **Tidak ada hasil empiris.** `docs/reports/` masih kosong; tidak ada satu pun angka akurasi, hit-rate, PnL, atau
  reputasi item di repo ini. Lapisan grading (`docs/spec.md` §6) opsional dan baru sah bila n cukup; di bawah n=30
  jawabannya adalah "belum cukup data", bukan angka kecil.
- **Tidak ada angka keandalan tool RYO di dokumen publik mana pun** — output tool RYO adalah data RYO dan hanya boleh
  muncul di dalam submission (ADR-008, `docs/rules-compliance.md`).
- **Tidak ada klaim "production-ready", "teraudit", atau "100%".** Yang ada: 536 tes hijau atas fondasi, dan daftar
  kegagalan yang bisa direproduksi di `docs/judge-reports/fase-1.md` §"Kegagalan yang bisa direproduksi".
- **Tidak ada klaim performa perdagangan.** Semua posisi adalah practice; P&L jangka pendek juga tidak dinilai rubric.
- **Angka replay tidak boleh dijumlahkan.** `params_hash` yang berlaku hari ini (4 Sep 2026) adalah
  `3d39fb1bd1a03b7f5269f53fec347d34645d2b402ff1c2de47e53f19d69616b7`
  (`uv run python -c "from preflight.params import DEFAULT_PARAMS; print(DEFAULT_PARAMS.params_hash())"`). Ia bergeser
  hari ini (commit `6023700`, TASKS 0.12) karena empat nilai kosakata yang belum pernah kami lihat muncul pertama kali
  di snapshot id=1976 (2026-09-04T11:21:24Z): `sentiment_regime='complacent_crowding'`,
  `liquidation.pressure_state='extreme'`, `funding.crowding_state='long_crowding'`, `fear_greed.change_direction='flat'`.
  Memetakannya adalah keputusan sadar pemilik repo, bukan kecelakaan. Menjalankan `uv run python -m agent.replay <id>`
  atas ke-39 baris ledger (SIMULATED) memberi tiga vonis yang **berbeda arti** dan karena itu tidak kami gabungkan:
  `IDENTICAL` 12 (id 28-39, generasi params yang berlaku), `STALE_PARAMS` 8 (id 20-27), `STALE_ALGO` 19 (id 1-19).
  `STALE_PARAMS` — vonis yang baru mendarat hari ini (commit `bab34d5`, TASKS 0.14) — berarti **kami tidak menilai
  baris itu sama sekali**, bukan bahwa kami menemukannya salah; di repo ini `MISMATCH` berarti bukti tidak
  mereproduksi keputusan, sebuah tuduhan yang tidak kami dukung untuk baris yang aturannya memang berbeda (sebelum
  0.14 baris-baris itu memang dilaporkan `MISMATCH`; itu dicabut). Contoh yang bisa dijalankan juri:
  `uv run python -m agent.replay 28` → `IDENTICAL e37eafeddd47f22b344805a588a44d544caf855b5c2b5503ea94cda86600ba1d`,
  `'SOL' NO_GO dari 5 snapshot beku (2166, 2167, 2169, 2170, 2219); blocking: 08_derivatives`. Yang tetap TIDAK kami
  klaim: bahwa 12 `IDENTICAL` itu mengatakan sesuatu tentang mutu keputusannya, atau tentang keandalan sumber mana pun.

---

## 9. Track & kepatuhan

Satu BUIDL, tiga track (`docs/api-facts.md` §E: maksimal satu BUIDL per tim):

| Track | Rencana | Status |
|---|---|---|
| T3 | skill `preflight(symbol)` dengan bentuk envelope RYO | kontrak, CLI, dan isi skill ADA (`preflight/skill.py`, `preflight/items.py`, `preflight/params.py`); kartu nyata terlihat lewat `uv run python -m agent.replay 28` |
| T1 | agen practice-trade + kartu panggilan + replay | ADA sebagian: agen menulis keputusan (`agent/main.py`, `make agent-tick`) dan replay memvonis 39 baris — 12 `IDENTICAL`. **`make demo` (naskah §7 utuh) BELUM**, TASKS 3.4 |
| T2 | watchlist urut daya veto + kartu keyboard-first | BELUM diklaim: `ui/app.py` ada di working tree tetapi **belum di-commit** dan belum ada laporan yang menunjukkannya berjalan (TASKS 3.1/3.2) |

Kepatuhan (`docs/rules-compliance.md`): kredensial hanya dari env dan tidak pernah dilog atau di-repr · label
SIMULATED di README dan (nanti) di semua layar · jalur nyata hanya memakai sumber yang `enabled: true` **dan**
`approved: true` di `sources.yaml` — hari ini dua sumber: tool RYO dan satu LLM untuk narasi · dependency, aset, dan
sumber pihak ketiga didaftarkan di `DISCLOSURE.md` (32 paket `uv.lock`, dijaga `tests/test_disclosure.py`).

---

## 10. Roadmap jujur (urutan `TASKS.md`)

1. `DISCLOSURE.md` lengkap (5.3b) — memblokir publikasi apa pun.
2. Agen menulis keputusan generasi baru + replay CLI dengan tiga verdict (2.1, 2.2, 2.4/2.4a/2.4b).
3. UI Layar A/B, jujur soal dua generasi (3.1/3.1a, 3.2).
4. `make demo`: naskah §7 dari fixture offline, dua run identik (3.4).
5. Chaos test satu perintah + bukti dua tanggal dengan satu NO_GO yang penyebabnya data **hilang** (4.5, 4.7).
6. **Opsional, belum dibangun, mungkin tidak dibangun:** grading outcome → reputasi item, dan item ke-11 *Bunryō*
   (ukuran posisi dari reliabilitas bersyarat rezim). Syaratnya n ≥ 30 per kelas sebelum 6 Sep (ADR-012 butir 4).
   Bila datanya tidak cukup, keduanya tidak akan muncul — bukan diperkirakan.
