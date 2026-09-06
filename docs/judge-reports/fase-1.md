# Laporan juri — Fase 1 — 1 Sep 2026
Dijalankan `penilaian rubric` atas state repo setelah TASKS 0.1/0.5/1.1/1.2/1.3/1.4 selesai.
Semua tes destruktif dilakukan atas salinan/`mktemp -d`; `data/ryo.db` tidak disentuh; perekam tidak dihentikan.

## Ringkasan eksekutif
1. Fondasi (klien, store, kontrak skill, gate sumber, replay, perekam) **berjalan nyata**: 536 lulus + 1 skip, ruff bersih, 689 baris snapshot nyata lolos `freeze`/`verify_bundle`.
2. **Coping with failure adalah kekuatan sesungguhnya**: 12 skenario destruktif, nol traceback, kegagalan selalu menjadi DATA (`status:"unavailable"` + pesan yang bisa ditindaklanjuti), exit code masuk akal.
3. Tiga klaim batas di `core/replay.py` diuji satu per satu: **ketiganya benar persis seperti tertulis**. Contoh kejujuran yang harus ditiru berkas lain.
4. Naskah §7 langkah 1 berjalan sebagian (envelope jujur); langkah 2–6 **belum dibangun** (bukan rusak).
5. **Temuan paling serius: 19 baris `decisions` warisan implementasi yang sudah dihapus tidak bisa di-replay** oleh `core/replay.py` baru. Store hanya-tambah → tidak bisa dihapus.
6. **Risiko stabilitas terbesar: 429 nyata.** Perekam baru mengumpulkan 8× `HTTP 429, backoff 60.0s` dalam satu siklus; token bucket bersama hanya per-PROSES.
7. Klaim mendahului kode di `README.md` dan `DISCLOSURE.md` — dua berkas yang dibaca juri.
8. `DISCLOSURE.md` tidak lengkap: 13 paket didaftar, `uv.lock` berisi 32.
9. "Cabut kredensial" tidak bisa diuji dengan `unset` — `core/recorder.py` memuat ulang `.env`.
10. Skor: **T1 28/100 · T2 20/100 · T3 45/100** (umum 20/40). Wajar untuk Fase 1.

## Langkah naskah `docs/spec.md` §7
| # | Langkah | Hasil | Bukti / alasan |
|---|---|---|---|
| 0 | `make demo` | ✘ belum dibangun | mencetak "belum dikerjakan" tetapi **exit 0** — placeholder yang melapor sukses |
| 1 | `make skill ARGS="preflight SOL"` | ◐ sebagian, jujur | envelope sah, satu baris JSON kanonik, exit 0, `status:"unavailable"`, `warnings:["skill 'preflight' not registered"]`; dua run byte-identik |
| 2 | fixture partial → item unspoken | ✘ belum dibangun | `preflight/` hanya `__init__.py` |
| 3 | `make agent-tick` ×3, `make replay ID=` | ✘ belum dibangun | `No module named agent.main`; primitif replay ada dan bekerja |
| 4 | Narasi LLM → `narration_unavailable` | ✘ belum dibangun | tidak ada `preflight/narrate.py` |
| 5 | `make ui` Layar A/B | ✘ belum dibangun | `Could not import module "ui.app"` |
| 6 | Dua tanggal → keputusan berbeda | ✘ belum dibangun | prasyarat langkah 1–3 |

Berjalan: `make doctor` ✔ · `make test` ✔ (536+1 skip) · `make lint` ✔ · `db_status.py` ✔ (689/19/9).

## Tes destruktif — hasil
| # | Skenario | Hasil |
|---|---|---|
| a | Host RYO tidak terjangkau | ✔ backoff eksponensial terlihat, per-komponen, `exit 1`, nol traceback |
| b | `sources.yaml` false/"true"/rusak/duplikat/tag-python/kosong/hilang | ✔ tujuh pesan berbeda & tepat, semua `exit 0` + `unavailable`; tag Python tidak dieksekusi |
| c | DB dihapus di tengah jalan | ✔ berisik lalu berhenti (`exit 1`) — bukan pura-pura pulih |
| d | `kill -9` mid-siklus + restart | ✔ commit-per-snapshot terbukti (11→15 baris selamat), integrity ok, nol duplikasi; instance kedua ditolak `flock` |
| e | 429 + `Retry-After` | ✔ dihormati, lanjut, berhasil |
| f | Tamper snapshot → replay | ✔ ketiga batas yang diakui docstring terbukti persis |
| g | Argumen CLI aneh | ✔ semua `exit 0`, satu dokumen JSON, nol traceback |
| h | Envelope fixture dirusak | ✔ nol data karangan; lima bentuk kerusakan → lima pesan berbeda |
| i | Kredensial dicabut | ⚠ **tidak bisa diuji dengan `unset`** — `core/recorder.py` memuat ulang `.env` |
| j | Hanya-tambah `core/store.py` | ⚠ authorizer menolak UPDATE/DELETE/DROP/ATTACH; **`REPLACE INTO` lewat `store._db` LOLOS dan menghancurkan baris** |

## Klaim vs kode
| Klaim | Kenyataan | Nilai |
|---|---|---|
| `README.md` agen/kartu publik/replay byte-per-byte (present tense) | tidak ada agen/kartu/10 item; baris status "direset ke titik nol" sudah basi | ✘ terlalu kuat + basi |
| `DISCLOSURE.md` three/gsap/lenis "disalin apa adanya ke `ui/static/vendor/`" | `ui/static` tidak ada | ✘ klaim mendahului kode |
| `DISCLOSURE.md` tabel dependency | 13 didaftar, `uv.lock` berisi 32 (21 transitif tidak disebut) | ✘ tidak lengkap (Rules §5) |
| `core/store.py` "menutup satu-satunya celah yang lolos authorizer" | benar untuk `Store._insert`, **tidak** untuk `store._db` | ⚠ presisi kurang |
| `core/replay.py` batas yang diakui | **terbukti benar & cukup terang** — direproduksi ketiganya | ✔ jujur |
| `core/skill.py` 10 field + tepat `simulated`, `schema_version="1.0"` | diverifikasi terhadap sampel | ✔ akurat |
| TASKS 0.5 "ratchet diperbaiki" | perekam baru masih 8× 429/siklus — berkurang, belum selesai | ⚠ terlalu optimis |
| `docs/rules-compliance.md:10` `RECORDER_MAX_TOKENS` | variabel itu tidak ada | ⚠ nama salah |
| ADR-015 masih "USULAN" | kode sudah menerapkannya | ⚠ butuh keputusan manusia |

## Kepatuhan
Kredensial ✔ (nol kunci di repo & 497 baris log) · SIMULATED ◐ (UI belum ada) · Sumber ✔ (gate runtime, fail-closed 7 varian) · **DISCLOSURE ✘** · Data RYO ✔ · ⚠ `scripts/guard.sh` dangkal (hanya memblokir `sqlite3` CLI, bukan modul Python).

## Kegagalan yang bisa direproduksi
1. `make demo` → "belum dikerjakan", **exit 0**
2. `REPLACE INTO` lewat `store._db` → baris id=1 tertimpa
3. `ruff check --select RUF100 core/` → 15 `# noqa` inert
4. `decisions.blocking_json` = `'[5,6,10]'` (integer) → `ReplayError: blocking hanya boleh berisi id item (str)`; `card_json` tanpa `algo`
5. 8× 429 dalam satu siklus perekam baru
6. Argumen 100 kB masuk utuh ke field `tool` dan `summary.headline`
7. `unset` kredensial → perekam tetap memanggil RYO (`.env` dimuat ulang)

## Skor rubric (`docs/spec.md` §8)
**Umum** — Combining sources **5/20** (pipa ada, nol keputusan yang benar-benar berubah karena sumber) · Coping with failure **15/20** (12 skenario bersih; dipotong: `make demo` exit 0, chaos test belum satu perintah, cabut-kredensial defeated, 429 masih 8×/siklus).
**T1 28/100** — Real thinking 0/25 · Cause and effect 2/20 · Repeatability 6/10 · Advanced execution 0/5.
**T2 20/100** — Easy to read 0/25 · Sorted by importance 0/25 · Works for everyone 0/10 (tidak ada layar; uji 30 detik tidak dapat dijalankan).
**T3 45/100** — Fills a gap 12/25 · Follows the spec 6/7 (dibatasi 7/15 karena §E "tidak ada spec formal") · Could we use it 7/20.

## Lima pertanyaan juri
1. Bedanya dengan agen sinyal + guardrail biasa? Hari ini: belum ada agen. Yang membedakan secara struktural sudah kode — ketiadaan bukti adalah warga kelas satu. Buktikan dengan **satu NO_GO yang penyebabnya angka yang TIDAK ADA**, bukan angka jelek.
2. Kalau semua item selalu hijau untuk BTC, apa yang agen ini *pikirkan*? Item tanpa daya pisah bukan "aman".
3. Tunjukkan satu keputusan yang berubah karena data HILANG. Bahannya sudah ada: 269 baris `deep_analysis` berstatus `partial`.
4. Apa yang terjadi pada 19 keputusan yang ditulis kode yang sudah dihapus? `make replay ID=13` akan tampak seperti tamper.
5. Perekam memakai satu kunci 60/mnt dan sudah kena 8× 429. Apa yang terjadi saat agen menjadi proses kedua?

## Prioritas perbaikan
**Wajib sebelum Fase 2:** (1) putuskan nasib 19 `decisions` + 9 `trade_events` warisan sebelum 2.1 menulis baris ke-20; (2) strategi kuota lintas proses sebelum `agent/` berjalan berdampingan dengan perekam.
**Paralel dengan Fase 2:** README/DISCLOSURE jujur & lengkap (Rules §5 — harus beres sebelum publikasi apa pun) · `make demo` exit ≠ 0 · `RyoError` masuk allowlist pesan CLI · `require_source` sebelum cek kredensial di `from_env` · batasi panjang/charset `SkillResult.tool` · aktifkan ruff `BLE`/`S`/`RUF` · perbaiki `rules-compliance.md:10` · naikkan ADR-015 dari USULAN.

## Catatan operasional juri
`data/ryo.db` tidak disentuh (semua tulis di `mktemp -d`); perekam tetap hidup (pid 160582/160586); `decisions` tetap 19. Satu percobaan "cabut kredensial" ternyata tetap memanggil RYO karena `.env` dimuat ulang — panggilan itu menyumbang beberapa 429 pada 23:04; angka 8× yang dikutip diambil dari 22:38–22:55, sebelum tes tersebut.
