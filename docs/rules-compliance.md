# Official Rules §5 → kewajiban kode (ringkasan operasional; analisis lengkap: ../ryo-rules-impact.md)
Sumber: teks Official Rules draft yang ditempel user 1 Sep 2026. Produk: Tenko (ADR-012). Final Rules/Participant Terms/technical requirements menang bila beda.

| Klausul | Kewajiban di repo ini | Ditegakkan oleh |
|---|---|---|
| Hanya endpoint/dependency approved | `sources.yaml` `approved: true` sebelum dipakai di jalur nyata | `core/sources.py`, tes, security-reviewer |
| Disclosure AI/OSS/dataset/API/pre-existing | `DISCLOSURE.md` diperbarui pada commit yang sama | loop.md langkah 4, pitch-writer |
| Jangan publish credentials | env saja; tidak di log/repr/tes/screenshot | `guard.sh`, `gate.sh` (secret-in-repo), tes repr client |
| Jangan misrepresent simulated as real | `simulated: true` di semua respons; banner SIMULATED; kata "practice/simulated" di README/video/post | tes UI, pitch-writer, hackathon-judge |
| Jangan interfere access controls / abuse | token bucket dari api-facts §A; perekam ≤ RECORDER_MAX_TOKENS | `core/ryo_client.py` |
| Tidak ada dana nyata | tidak ada kode transaksi; guard memblokir perintah transfer | `guard.sh`, PRD §5 |
| Submission proprietary, no reuse | repo private; tidak ada salinan kode ke proyek lain; post = screenshot bukan kode | manusia, pitch-writer |
| RYO data/confidential | output tool RYO & metrik keandalannya hanya di dalam submission | ADR-008, pitch-writer |
| False/misleading info = DQ | setiap angka bawa n/window/sources; README tidak mengklaim yang tidak di laporan | SkillResult, hackathon-judge |
| Submission review (kode, log, AI-use) | log terstruktur tanpa rahasia; `DISCLOSURE.md` menyebut Claude Code | pitch-writer |
Pertanyaan yang masih terbuka ke penyelenggara: lihat docs/api-facts.md §E.
