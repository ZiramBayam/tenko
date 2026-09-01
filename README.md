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

Yang **belum**: CLI `make replay ID=<trade_id>` (`agent/replay.py`) belum ada, jadi verdict IDENTICAL / MISMATCH /
STALE_ALGO belum bisa Anda lihat di terminal. Itu TASKS 2.4.

---

## 7. Dua generasi di `data/ryo.db` (ADR-016)

`data/ryo.db` memuat dua generasi. 19 baris `decisions` + 9 `trade_events` pertama ditulis oleh implementasi yang
**dihapus 1 Sep 2026**. Store bersifat hanya-tambah (ADR-006), sehingga baris itu tidak dihapus, tidak ditulis ulang,
dan tidak disembunyikan. Begitu CLI replay ada, `make replay ID=13` akan melaporkannya sebagai `STALE_ALGO` — snapshot
buktinya utuh, algoritma yang memproduksinya tidak ada lagi. Itu perilaku yang disengaja, bukan kerusakan, dan bukan
tamper. Pembedanya satu hal saja: ada atau tidaknya kunci `algo` di `card_json` (ADR-016; kait di `core/replay.py`
belum hidup, TASKS 2.4a/2.4b).

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
- **`params_hash` final belum dibekukan** (TASKS 5.0), jadi keputusan yang dibuat hari ini belum tentu bisa di-replay
  setelah ambang berubah — guard-nya memang dirancang untuk mengeluh.

---

## 9. Track & kepatuhan

Satu BUIDL, tiga track (`docs/api-facts.md` §E: maksimal satu BUIDL per tim):

| Track | Rencana | Status |
|---|---|---|
| T3 | skill `preflight(symbol)` dengan bentuk envelope RYO | kontrak & CLI ADA; isi skill BELUM (TASKS 2.2) |
| T1 | agen practice-trade + kartu panggilan + replay | BELUM (TASKS 2.1/2.4) |
| T2 | watchlist urut daya veto + kartu keyboard-first | BELUM (TASKS 3.1/3.2) |

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
