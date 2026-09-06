
## 4 Sep 2026 — gate MERAH karena 0.12, bukan karena task yang sedang dikerjakan
`scripts/gate.sh` MERAH (`GATE MERAH: pytest`) sesudah 0.8a di-commit (`e06256e`). Penyebabnya BUKAN 0.8a —
`tests/test_recorder.py` 115 passed dan `make lint` hijau. Yang merah:

- `tests/test_params.py::test_tables_still_cover_every_value_in_the_live_recorder_db` — tiga nilai baru masuk
  `data/ryo.db` hidup di antara dua kali jalan: `monitor_market_sentiment_shift.data.evidence.sentiment_regime`
  = `complacent_crowding`, `…evidence.liquidation.pressure_state` = `extreme`,
  `…evidence.fear_greed.change_direction` = `flat`. Ini task **0.12**.
- `tests/test_gate.py::test_gate_is_green_on_this_tree` — merah sebagai akibat, karena ia memang menegaskan gate hijau.

**Kenapa tidak langsung ditutup di iterasi ini:** menutupnya berarti mengubah `preflight/params.py`, dan itu
(1) task yang berbeda (loop.md: satu task per iterasi), (2) menyentuh berkas yang task **5.0** bekukan — mengubah
params membuat keputusan lama tidak bisa di-replay, dan (3) menurut aturan anti-halusinasi nilai field RYO yang baru WAJIB lewat
`verifikasi API` lebih dulu, bukan ditebak. Memetakan ketiganya ke arah item spec §4 tanpa itu = persis
"nilai default diam-diam" yang dilarang.

**Yang dibutuhkan:** keputusan pemilik repo/penjagaan ruang lingkup apakah params boleh dibuka untuk tiga nilai ini
meski 5.0 sudah membekukannya, lalu 0.12 dikerjakan (verifikasi API → kerja preflight). Sampai itu, gate tetap MERAH.
0.12 ditandai `[!]` di TASKS.md karena gate menuntutnya, tetapi bacalah apa adanya: ia belum pernah
dicoba dan gagal — ia menunggu keputusan, bukan menunggu perbaikan teknis.

**Catatan 4 Sep 2026, sesudah `c362fe2` (0.10):** gate masih MERAH karena sebab yang SAMA dan hanya itu.
Task 0.8a (`e06256e`) dan 0.10 (`c362fe2`) tetap dikerjakan dan di-commit di atas tree merah ini; keduanya
diverifikasi dengan suite penuh yang men-deselect dua tes di atas (1340 passed, 1 skipped) plus
`make lint` hijau, dan keduanya lolos tinjauan keamanan tanpa temuan kritis/tinggi.

## 4 Sep 2026, sore — 0.12 TIDAK lagi diblokir keputusan
Pemilik repo menjawab: **`preflight/params.py` boleh dibuka** meski TASKS 5.0 membekukannya. Jadi 0.12
kembali `[ ]` (sedang dikerjakan), bukan `[!]`.

Gate MASIH merah malam ini karena sebab yang sama, tetapi alasannya berbeda: pekerjaannya sedang berjalan
lewat jalur verifikasi wajib — `verifikasi API` memverifikasi ketiga nilai dan mencatatnya ke
`docs/api-facts.md` §A, `kerja preflight` menetapkan arah/meaning-nya (keputusan spec §4, bukan fakta
API). Menerapkan pemetaan sebelum keduanya melapor = menebak nama field RYO, yang justru dilarang.

Harga yang sudah diukur dan diterima pemilik repo: `Vocabulary.as_dict()` masuk ke `params_hash`, jadi
menambah tiga baris membuat **8 baris `decisions` yang masih replay `IDENTICAL` menjadi `MISMATCH`**
(19 lainnya sudah MISMATCH sejak dua perubahan params sebelumnya). Naskah demo §7 langkah 3 membuat
keputusan BARU lebih dulu, jadi demonya tidak rusak.

## 4 Sep 2026, malam — 0.12 selesai di working tree tetapi DITAHAN; 0.14 ternyata prasyaratnya
Gate MERAH dengan sebab BARU, dan sebab itu kita sebabkan sendiri:

```
tests/test_ui.py::test_live_zero_evidence_rows_are_marked_not_verified[20]
E   AssertionError: assert 'MISMATCH' == 'NO_EVIDENCE'
```

Empat baris `decisions` id 20-23 punya `snapshot_ids_json='[]'`. Sebelum 0.12 mereka dilaporkan
`NO_EVIDENCE` — jujur: tidak ada yang bisa direproduksi. Sesudah 0.12 menggeser `params_hash`,
mereka dilaporkan `MISMATCH`, yang mengklaim "bukti tidak mereproduksi keputusan" atas nol bukti.
Itu regresi terhadap TASKS 2.4a, yang sengaja membuat `NO_EVIDENCE` bukan `IDENTICAL`.

**Product-manager memilih "pindahkan urutan cabang saja" (Opsi A). Itu TIDAK BISA dijalankan** —
diuji, bukan disimpulkan dari membaca:

```
$ uv run python -m agent.replay 20        # dengan urutan sudah dibalik
replay decision_id=20: MISMATCH
sebab: keputusan ini ditulis dengan params_hash 'f2781184…' sementara ambang yang berlaku
sekarang '3d39fb1b…'
```

Sebabnya struktural: cabang `NO_EVIDENCE` (`agent/replay.py:204`) bersyarat
`computed == stored and not frozen_ids`, dan `computed` memakai params yang BERLAKU SEKARANG.
Baris-baris itu ditulis dengan params lama, jadi `computed != stored` selamanya — cabangnya tak
terjangkau di posisi mana pun. Syarat `computed == stored` juga tidak boleh dilonggarkan menjadi
`not frozen_ids` saja: ia yang menutup temuan SEDANG-1 tinjauan keamanan 4 Sep (baris yang
buktinya dikosongkan akan mengaku "memang ditulis tanpa bukti"). Repo sudah dipulihkan;
`agent/replay.py` tidak berubah.

**Keadaan sekarang:** 0.12 lengkap di working tree (empat pemetaan kosakata, item 8 sadar-arah,
fixture `crowded_long` yang karangan diganti, 12 tes baru; 1386 passed, 1 skipped, 1 merah)
tetapi DITAHAN, belum di-commit. Menunggu keputusan penjagaan ruang lingkup antara mengerjakan 0.14
(`STALE_PARAMS`) lebih dulu lalu commit berurutan, atau menerima `MISMATCH` untuk empat baris itu.
Tidak ada task ditandai `[!]`: keduanya sedang berjalan, bukan buntu.
