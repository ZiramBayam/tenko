# Tenko — panduan untuk Claude Code
Tenko (点呼) untuk RYO-CHAN Hackathon 2026 — ADR-012 (deadline 8 Sep 2026 23:59 JST). SEMUA aktivitas = simulasi read-only. Tidak ada dana, wallet, atau eksekusi nyata.

## Baca dulu, setiap sesi, sebelum menulis kode
1. `docs/api-facts.md` — SATU-SATUNYA daftar endpoint/tool/paket eksternal yang boleh dipanggil. §E = aturan lomba yang sudah dijawab tertulis.
2. `docs/versions.md` — versi yang dipin.
3. `docs/spec.md` — desain (§3 kontrak skill, §4 model biaya, §5 data flow, §6 definisi outcome, §7 naskah demo, §8 rubric).
4. `docs/decisions.md` — ADR. Jangan buka ulang tanpa ADR baru.
5. `docs/rules-compliance.md` — Official Rules → kewajiban kode. Pelanggaran = diskualifikasi, bukan bug biasa.
6. `TASKS.md` — kerjakan sesuai urutan fase & AC.

## Aturan anti-halusinasi (tidak bisa ditawar)
- Endpoint/field/param/versi yang TIDAK ada di `docs/api-facts.md`/`docs/versions.md`: BERHENTI, panggil `@agent-api-verifier`.
- Field respons tool RYO hanya dari `docs/samples/*.json` (TASKS 0.3). Jangan mengarang nama field.
- Fakta tetap (api-facts §A): 6 tool, input **simbol** bukan mint, **tidak ada tool safety**, REST `POST {RYO_MCP_URL}/tools/{tool}/call` → `["result"]` = envelope `{status, data_mode, as_of, data, availability, warnings}`; `null`/unavailable tidak pernah jadi 0. ADR-012 DITERIMA: produk = Tenko (点呼); jangan usulkan ide lain.
- Sumber eksternal hanya yang `enabled: true` DAN `approved: true` di `sources.yaml`. `approved: null` = belum boleh dipakai di jalur nyata (fixture boleh).
- Jangan menulis "TODO: verifikasi nanti" di kode yang memanggil sumber eksternal.

## Aturan "tunjuk & sebut" (ini produknya)
- Semua output skill lewat `core.skill.SkillResult` yang berbentuk envelope RYO (ADR-013). Field null/unavailable → item `unspoken`, BUKAN nilai default. Tidak ada angka agregat tanpa `n`.
- Tidak ada LLM di jalur keputusan (ADR-003). Keputusan agen = fungsi deterministik atas snapshot beku; `make replay` harus identik.
- `core/store.py` append-only: tidak ada UPDATE/DELETE. Tes menjaganya; jangan hapus.

## Aturan kepatuhan lomba (docs/rules-compliance.md)
- Kredensial hanya dari env; tidak pernah di log/repr/tes/screenshot. `guard.sh` memblokir pencetakan.
- Setiap respons API UI memuat `simulated: true`; banner SIMULATED di semua layar; kata "practice/simulated" di README/video/post.
- Output tool RYO = data RYO: hanya untuk submission; tidak dipublikasikan di post.
- Hormati rate limit (api-facts §A). Perekam ≤ `RECORDER_MAX_SYMBOLS` per siklus.
- Setiap paket/dataset/sumber baru → baris di `DISCLOSURE.md` pada commit yang sama.

## Batas folder
- `core/` → `@agent-core-engineer`. `preflight/`, `grading/` → `@agent-quant-engineer`. `agent/` → `@agent-agent-engineer`. `ui/` → `@agent-frontend-engineer`.
- `docs/api-facts.md`, `docs/versions.md` → hanya `@agent-api-verifier`. `README.md`, `DISCLOSURE.md`, `docs/posts/`, `demo/` → `@agent-pitch-writer`.
- `docs/spec.md`, `PRD.md` → tidak diubah oleh agen; perubahan lewat ADR + manusia.

## Perintah
`make doctor` · `make test` · `make lint` · `make record` · `make skill ARGS=...` · `make validate` · `make agent-tick` · `make replay ID=` · `make grade` · `make ui` · `make demo` · `make autopilot N=`

## Definisi selesai (per task)
AC terbukti dengan output ditempel; `api-verifier` nol HALUSINASI; `security-reviewer` tanpa kritis/tinggi untuk core/skill.py, core/store.py, agent/, dan apa pun yang menyentuh sumber eksternal/kredensial; baris DISCLOSURE.md bila ada dependency baru. Akhir fase: `hackathon-judge`.

## Gaya
Kode & identifier Inggris; komentar/dokumen Indonesia. Commit: **Conventional Commits** (`docs/commit-convention.md`),
satu task = satu commit, footer `Task: <id>`, **tanpa trailer `Co-Authored-By`**; hook `.githooks/commit-msg` menegakkannya
(aktifkan sekali dengan `make hooks`). Jangan pakai `git commit --no-verify`. Tanpa library di luar docs/versions.md tanpa ADR. Python murni (ADR-001): tidak ada Node/npm.

## Tim subagent (`.claude/agents/`)
product-manager (scope), core-engineer, quant-engineer, agent-engineer, frontend-engineer, api-verifier, security-reviewer (keamanan + kepatuhan aturan), hackathon-judge (rubric T1/T2/T3 + kode), pitch-writer. Detail: `docs/team.md`.
