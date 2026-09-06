# Official Rules §5 → kewajiban kode (ringkasan operasional; analisis lengkap: ../ryo-rules-impact.md)
Sumber: teks Official Rules draft yang ditempel user 1 Sep 2026. Produk: Tenko (ADR-012). Final Rules/Participant Terms/technical requirements menang bila beda.

| Klausul | Kewajiban di repo ini | Ditegakkan oleh |
|---|---|---|
| Hanya endpoint/dependency approved | `sources.yaml` `approved: true` sebelum dipakai di jalur nyata | `core/sources.py`, tes, tinjauan keamanan |
| Disclosure AI/OSS/dataset/API/pre-existing | `DISCLOSURE.md` diperbarui pada commit yang sama | loop.md langkah 4, penulisan dokumen |
| Jangan publish credentials | env saja; tidak di log/repr/tes/screenshot | `guard.sh`, `gate.sh` (secret-in-repo), tes repr client |
| Jangan misrepresent simulated as real | `simulated: true` di semua respons; banner SIMULATED; kata "practice/simulated" di README/video/post | tes UI, penulisan dokumen, penilaian rubric |
| Jangan interfere access controls / abuse | token bucket 60/mnt **bersama per kunci** (api-facts §A), bukan per instance; kunci `flock` menolak perekam kedua atas kunci yang sama; watchlist dipotong `RECORDER_MAX_SYMBOLS`; follow-up dibatasi `RECORDER_MAX_FOLLOWUPS` dan simbol yang rentang pengamatannya sudah ≥24 j dipensiunkan (tanpa itu beban siklus tumbuh tanpa batas — terukur 29→42 panggilan/siklus dan 32× HTTP 429 dalam sehari sebelum diperbaiki); `RECORDER_MAX_FAILED_CYCLES` menghentikan perekam yang gagal terus alih-alih membakar kuota diam-diam | `core/ryo_client.py`, `core/recorder.py` |
| Tidak ada dana nyata | tidak ada kode transaksi; guard memblokir perintah transfer | `guard.sh`, PRD §5 |
| Submission proprietary, no reuse | repo private; tidak ada salinan kode ke proyek lain; post = screenshot bukan kode | manusia, penulisan dokumen |
| RYO data/confidential | output tool RYO & metrik keandalannya hanya di dalam submission | ADR-008, penulisan dokumen |
| False/misleading info = DQ | setiap angka bawa n/window/sources; README tidak mengklaim yang tidak di laporan | SkillResult, penilaian rubric |
| Submission review (kode, log, AI-use) | log terstruktur tanpa rahasia; `DISCLOSURE.md` menyebut Claude Code | penulisan dokumen |
Pertanyaan yang masih terbuka ke penyelenggara: lihat docs/api-facts.md §E.
