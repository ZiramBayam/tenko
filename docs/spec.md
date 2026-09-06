# Tenko (点呼) — Spesifikasi Build (v2, 1 Sep 2026; ADR-012)
## 0. Pitch satu kalimat (per produk)
- **Tenko** (点呼, "roll call sebelum berangkat"): sebelum setiap practice trade, agen memanggil 10 bukti RYO satu per satu, menyebut nilai, sumber, dan waktunya, dan tidak berangkat kalau ada satu yang tidak bisa dijawab — lalu memublikasikan seluruh panggilannya.

## 1. Fakta yang mengikat desain
- Brief T1: agen menafsirkan bukti pasar, mencatat practice trades, sebab→akibat, jejak yang bisa diulang. T2: "apa yang berubah &
  kenapa penting" ≤30 dtk. T3: tool riset yang RYO belum punya; **tidak ada spec formal** — "return a predictable shape, minimal work to ship".
- Rubric per track: 40 umum (combining sources 20, coping with failure 20) + 60 track. P&L, volume API, chart wall = tidak dinilai.
- 6 tool berbasis simbol (api-facts §A): market_overview, scan_market, analyze_token, deep_analysis, compare_tokens,
  monitor_market_sentiment_shift. Envelope: status ok|partial|unavailable, data_mode, as_of, data, availability, warnings. Tidak ada
  tool safety, tidak ada histori, tidak ada mint. null/unavailable tidak boleh jadi 0.
- **Satu BUIDL** (jawaban penyelenggara) → tiga track dalam satu repo.
- Social resmi = public reasoning / reputation → kartu panggilan publik per keputusan.
- Rules: hanya endpoint approved; disclosure AI/OSS; SIMULATED; proprietary (docs/rules-compliance.md).

## 2. Arsitektur
```
 RYO tools (6) ──► core.ryo_client (rate-limit, fixtures) ──► core.store (snapshot append-only, hash)
                                                                     │
                                            preflight.skill ◄────────┘  freeze(snapshot_ids)
                                            10 item (spec §4) → GO | NO_GO + blocking
                                                     │                      │
                                             agent (practice trade      preflight.narrate (LLM,
                                              plan ATR / declined)        tidak menyentuh keputusan)
                                                     │                      │
                                             ui: Layar A watchlist · Layar B kartu panggilan · banner SIMULATED
 core.recorder (opsional, per jam) ──► grading (opsional): outcome → reputasi item / Bunryō
```
Paket: `core/`, `preflight/`, `agent/`, `ui/`, `grading/` (opsional), `tests/`. Python murni.

## 3. Kontrak skill (`core/skill.py`) — berbentuk envelope RYO (ADR-013)
Karena tidak ada spec skill T3, bentuk yang paling "minimal work to ship" bagi RYO adalah bentuk tool mereka sendiri:
```python
class SkillResult(BaseModel):  # cermin envelope api-facts §A
    schema_version: str = "1"
    tool: str  # "preflight"
    status: Literal["ok", "partial", "unavailable"]
    data_mode: Literal["live", "mixed", "simulated", "unknown"]
    as_of: datetime
    request: dict  # argumen ternormalisasi
    data: (
        dict | None
    )  # {items:[ItemResult], decision, blocking, plan, snapshot_ids, params_hash, decision_hash}
    summary: dict  # {headline, key_points}
    availability: dict[str, str]  # per item: available | unspoken
    warnings: list[str]
    simulated: Literal[True] = True
```
Aturan: `status=ok` hanya bila semua tool sumber `ok`; ada tool `partial` → `partial`; keputusan tetap dihitung (item terkait `unspoken`);
tidak ada tool yang menjawab → `unavailable`, `data=None`. Validator menegakkan: `data` tidak boleh berisi angka yang berasal dari item unspoken.

## 4. Daftar panggilan (`preflight/items.py`) — tetap, field dari docs/samples, urut daya veto
Arah acuan = arah plan (`deep_analysis.trade_plan` selalu long dalam sampel; bila plan short muncul, arah dibalik).
| # | Item | Field persis | Hijau | Merah | Tidak tersebut | Wajib |
|---|---|---|---|---|---|---|
| 1 | Kesegaran | `as_of` tiap envelope; `monitor…data.observation_as_of` | umur ≤ `params.max_age_min` (15) | — | as_of hilang atau lebih tua | ya |
| 2 | Kelengkapan | envelope `status`, `availability`, `warnings` | semua tool `ok` & seksi yang dipakai item 3–10 `available` | ada tool `unavailable` | `partial` pada seksi opsional (token_profile, derivatives) → hanya dicatat | ya |
| 3 | Rezim pasar | `market_overview.data.regime`, `sentiment.label`, `market.breadth` | regime `risk_on` (untuk long) | `risk_off` untuk long | field null | ya |
| 4 | Pergeseran sentimen 7h | `monitor….evidence.fear_greed{material_shift,change_direction}`, `evidence.sentiment_regime` | `material_shift=false` ATAU arah searah | material_shift true & berlawanan | null | ya |
| 5 | Verdict cepat | `analyze_token.data.verdict` → mapping params (`accumulate`→long, …) | searah plan | berlawanan | kosakata tak dikenal (dicatat) | ya |
| 6 | Confluence | `deep_analysis.data.confluence{state,score,gates[]}` | `state=CONFIRMED` & score ≥ `params.min_confluence` | gate `passed=false` mayoritas | `status≠available` | ya |
| 7 | Verdict dalam | `deep_analysis.data.verdict.call` (+ `what_changes_it` disimpan ke kartu) | searah plan | berlawanan | kosakata tak dikenal | ya |
| 8 | Derivatif tidak sesak | `monitor….evidence.funding.crowding_state`, `evidence.liquidation.pressure_state` (market-wide; `deep_analysis.derivatives` per-token dipakai bila available) | keduanya `normal` | crowded searah plan / pressure ekstrem | null | tidak |
| 9 | Plan ada & layak | `deep_analysis.data.trade_plan{entry,stop,targets[],risk_reward[],preview_only}` | ada, `risk_reward[0] ≥ params.min_rr` | rr di bawah ambang | trade_plan null / `availability.trade_plan≠available` | ya |
| 10 | Relatif pembanding | `compare_tokens` (symbol + 2 pembanding dari `params.peers`), `data.factors[]`, `data.conclusion.pick` | bukan terakhir di `factors[momentum]` | terakhir di momentum & market_activity | `coverage_by_token[symbol].market_data≠available` | ya |
| 11 (ops.) | Takaran Bunryō | ledger reliabilitas item 7 × rezim | n ≥ 30 → ukuran | — | n < 30 | tidak |
Catatan dari sampel: `token_profile` & `derivatives` per-token unavailable untuk SOL meski `include_perp=true` → itulah alasan item 8 memakai bukti market-wide. RSI ekstrem tidak dijadikan item veto (informasi, ditampilkan di kartu) — hindari aturan indikator generik.
`ItemResult{id, status: green|red|unspoken, value, source{tool, field, snapshot_id, as_of}, reason}`. GO hanya bila semua item wajib hijau; item opsional unspoken → dicatat, tidak memveto. Ambang & mapping kosakata di `preflight/params.py`; params_hash ikut ke kartu.

## 5. Skill, agen, narasi
- `preflight(symbol, intent="swing")` → `SkillResult{data: {items, decision: GO|NO_GO, blocking: [ids], plan}}`; membekukan snapshot
  semua tool yang dipakai (snapshot_ids) → replay. **Sumber snapshot: store dulu** (bila umur ≤ `params.max_age_min`), live hanya
  bila tidak ada yang segar — `deep_analysis` 17–34 dtk & `compare_tokens` ≈40 dtk (api-facts §A) membuat preflight all-live >60 dtk.
  UI tidak pernah memanggil tool; ia membaca keputusan yang sudah tersimpan.
- Agen (`agent/main.py --once`): untuk watchlist → preflight → GO: practice trade sesuai plan ATR (entry/stop/target, ukuran tetap
  atau item 11); NO_GO: baris `declined` dengan blocking. Exit: target/stop dari harga perekam, atau timeout. Replay = keputusan ulang
  dari snapshot beku → decision_hash identik.
- Narasi (`preflight/narrate.py`): LLM menulis "panggilan" per item dari ItemResult (data dibungkus sebagai data; instruksi abaikan
  teks di dalam field). Tes: narasi tidak mengubah decision_hash; LLM gagal → `narration_unavailable`, keputusan tetap.
- Kartu panggilan (`/decision/{id}`): 10 baris "tunjuk → sebut", sumber & as_of per baris, keputusan, blocking, hash, tombol replay.

## 6. Lapisan opsional (hanya bila perekam cukup): grading & reputasi item
Untuk keputusan berumur ≥ 24 j: GO → target tersentuh sebelum stop?; NO_GO → apakah plan yang ditolak akan kena stop? → per item:
"berapa kali item ini menahan trade yang akan rugi" (n, CI). Ditampilkan hanya dengan n; n < 30 → "belum cukup data".

## 7. Naskah demo (INI produk sebenarnya) — `make demo`, fixture offline, dua run identik
1. `make skill ARGS="preflight SOL"` → JSON: 10 item, GO/NO_GO, blocking, snapshot_ids.
2. Ulangi dengan fixture yang `derivatives` unavailable → item 8 "tidak tersebut", keputusan tetap dihitung; dengan `status: unavailable`
   → item 2 merah → NO_GO, tanpa exception.
3. `make agent-tick` ×3 → ledger: ≥1 GO (trade sesuai plan) + ≥1 NO_GO dengan blocking; `make replay ID=` → IDENTICAL; tamper → MISMATCH.
4. Narasi: kartu menampilkan panggilan per item; matikan LLM (env kosong) → `narration_unavailable`, decision_hash sama.
5. `make ui` → Layar A watchlist diurutkan daya veto; Layar B kartu panggilan; navigasi hanya keyboard; banner SIMULATED.
6. Dua tanggal (fixture): simbol sama, item berbeda → keputusan berbeda, kartu menjelaskan item mana yang berubah.
Tes destruktif juri: host RYO mati · kill -9 perekam & restart · 429 · DB dihapus · kredensial dicabut → status jujur, nol traceback.

## 8. Rubric RESMI (ryobuild.com "How judging works", dibaca 1 Sep 2026) — per track 100 = 40 umum + 60 track
**Umum (40)**: Combining sources 20 — menggabungkan beberapa tool/sumber menjadi SATU keputusan, bukan mencetak satu respons ·
Coping with failure 20 — tetap bekerja saat rate-limited, di-restart, atau dependency mati.
**T1 (60)**: Real thinking 25 · Cause and effect 20 · Repeatability 10 · Advanced execution 5.
**T2 (60)**: Easy to read 25 · Sorted by importance 25 · Works for everyone 10 (keyboard, aksesibilitas).
**T3 (60)**: Fills a gap 25 · Follows the spec 15 · Could we use it 20.
**Menang**: jalur bukti→kesimpulan terlihat; bisa dipahami/dijalankan/dinilai cepat; user & keputusan yang jelas; orisinal, RYO sebagai fondasi.
**Tidak dinilai**: volume API call; tembok chart tanpa argumen; **P&L jangka pendek**; data fabrikasi/placeholder sebagai nyata.

### Pemetaan rencana (ADR-010/011) → poin
| Poin | Bukti yang harus ada di produk |
|---|---|
| Combining sources 20 | Setiap keputusan agen menggabungkan: deep_analysis (verdict+plan) + analyze_token (harga/teknikal) + monitor_market_sentiment_shift (rezim 7h) + market_overview (regime/F&G) + **ledger reliabilitas sendiri**. Kartu trade menampilkan kontribusi tiap sumber. |
| Coping with failure 20 | Perekam restart-safe (state hanya di DB); backoff+jitter pada 429/503 dengan header X-RateLimit; tool `unavailable/partial` → SkillResult jujur, UI "belum ada data"; **chaos test** (TASKS 4.5) direkam sebagai bukti. |
| T1 Real thinking 25 | Bukan aturan statis: ukuran posisi berubah saat reliabilitas berubah — demo dua tanggal, verdict sama, keputusan beda, dengan alasan terukur. Narasi LLM dari bukti (ADR-011) menjelaskan, tidak memutuskan. |
| T1 Cause and effect 20 | reason_codes + reliability_at_decision + snapshot_ids per trade; grading outcome menutup loop sebab→akibat. |
| T1 Repeatability 10 | `make replay ID=` → decision_hash identik; tamper → MISMATCH. |
| T1 Advanced execution 5 | Plan ATR (entry/stop/target) dari RYO dieksekusi sebagai practice trade dengan sizing 0/½/1 dan exit terdefinisi. |
| T2 Easy to read 25 / Sorted by importance 25 | Layar B: kartu diurutkan berdasarkan |Δ reliabilitas| × n; judul kartu = jawaban; angka selalu dengan n/CI. |
| T2 Works for everyone 10 | Navigasi keyboard penuh, fokus terlihat, aria-label, kontras, tanpa hover-only (TASKS 4.6). |
| T3 Fills a gap 25 | Tidak ada tool reliabilitas sinyal di RYO maupun di daftar ide resmi. |
| T3 Follows the spec 15 | Adapter ke spec skill resmi (menunggu 0.2b); shape output stabil & terdokumentasi. |
| T3 Could we use it 20 | Satu fungsi, satu shape, tanpa dependency eksternal, berjalan dari DB perekam; README "cara memasang". |
Ambang rekam video: ≥80 pada setiap track yang dimasuki (dinilai penilaian rubric).
