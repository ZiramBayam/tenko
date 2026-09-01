# API FACTS — satu-satunya sumber kebenaran untuk endpoint/tool/paket eksternal
Aturan: kode HANYA boleh memanggil yang tercantum di sini. Butuh yang lain → BERHENTI, verifikasi dari sumber primer,
tambahkan bersama URL/perintah + tanggal, baru pakai. Sel status berisi `HALUSINASI` (dalam tabel) memblokir gate.sh sampai dibereskan.
Status 1 Sep 2026: §A terisi dari RYO Builder MCP Guide (ditempel user); field `data` & kuota menunggu sampel live (TASKS 0.3). §B–D dan sisa §E masih BELUM.

## A. Tool RYO-CHAN (MCP + REST) — sumber: "RYO Builder MCP Guide" (Last updated 13 Aug 2026), teks lengkap ditempel user 1 Sep 2026.
## Katalog live `GET /api/mcp/tools` adalah sumber kebenaran final → TASKS 0.3 menyimpannya ke docs/samples/catalog.json.
Endpoint: `https://app-ryochan.com/api/mcp` · protocol `2024-11-05` · auth `Authorization: Bearer $RYO_MCP_KEY` (env resmi: `RYO_MCP_URL`, `RYO_MCP_KEY`).
- `GET {URL}/health` (tanpa auth) → `{status, protocol_version, server:"ryo-chan", tools:6}`
- `GET {URL}/whoami` (auth; TIDAK memakai kuota) → key id, label, scope, expiry, **kuota saat ini**, nama tool.
- `GET {URL}/tools` (auth; tidak memakai kuota) → katalog: name, description, JSON input schema.
- REST call: `POST {URL}/tools/{tool}/call` body = objek argumen telanjang (mis. `{"symbol":"SOL"}`) → `response.json()["result"]` = envelope.
- MCP JSON-RPC: `POST {URL}` method `initialize` / `tools/list` / `tools/call {name, arguments}`; hasil = content block text berisi JSON envelope (parse sekali); cek `result.isError` (bisa HTTP 200 + isError true).
- Rate limit: **60 panggilan/menit per key** (email kredensial, 1 Sep 2026: label hackathon_key-061, key id baad2086, expires 2026-12-17T23:20Z). Header `X-RateLimit-Limit/Remaining/Reset`, `Retry-After` saat 429. Backoff eksponensial + jitter untuk 429/503; jangan retry argumen invalid. Kuota harian/bulanan: lihat whoami (docs/samples/whoami.json).
- Read-only: tidak ada wallet/balance/portfolio/eksekusi; **input = simbol aset, BUKAN alamat mint/wallet**; **tidak ada tool safety** (symbol-only safety tidak dipublikasikan).

### Field nyata (docs/samples/*.json, diambil 1 Sep 2026 03:47–03:48Z; envelope = `response["result"]`)
Katalog live (`/tools`): market_overview {} · scan_market {chain, theme, top_n} · analyze_token {symbol, **chain**} · deep_analysis {symbol, **chain**, include_perp} · compare_tokens {symbols, intent} · monitor_market_sentiment_shift {time_window}. Rate limit header huruf kecil: `x-ratelimit-limit: 60`, `x-ratelimit-remaining`, `x-ratelimit-reset` (epoch). whoami: `scopes:["tools:read"]`, `rate_limit_per_min: 60`, `expires_at`.
| Tool | `data` field yang dipakai (path persis) | Nilai yang terlihat | availability keys |
|---|---|---|---|
| market_overview | `regime`; `sentiment.fear_greed_index`, `sentiment.label`; `market.breadth`, `market.breadth_details{universe_size,advancing,declining}`; `top_movers.gainers[]/losers[]{symbol,change_24h_pct}` | regime `risk_on`; label `extreme_greed`; breadth 0.8571 (91 listing) | market_regime, market_totals, market_sentiment, market_breadth, top_movers |
| scan_market | `candidates[]{rank,symbol,name,chain,contract,price_usd,change_24h_pct,market_cap_usd,volume_24h_usd,turnover_ratio,momentum_score,established_asset,reason}`, `selection_method`, `excluded_candidate_count` | top: USELESS, ARB, PONS, CASHCAT, BONK — **memecoin ada**; `contract` terisi (Solana & EVM), `chain` null; method "Ranked by 24-hour momentum and market activity" | ranked_candidates, theme_filter (`not_requested`) |
| analyze_token | `asset{symbol,name,chain,contract,rank}`; `market{price_usd,market_cap_usd,fully_diluted_value_usd,volume_24h_usd}`; `performance{change_1h_pct,change_24h_pct,change_7d_pct,change_30d_pct}`; `technical_analysis{trend,rsi_14,momentum_30d_pct,atr_14_pct}`; `intelligence{narrative,catalysts[],risks[]}`; **`verdict` (string)** | trend `up`; rsi 68.9; verdict **`accumulate`** | market_data, technical_analysis, market_intelligence |
| deep_analysis | `market{mcap_usd,fdv_usd,volume_24h_usd,dominance_pct,circulating_supply,max_supply,price_usd}`; `performance{h1,h24,d7,d30}`; `technical_analysis{…sama}`; `confluence{state,score,gates[]{name,passed,detail},status}`; **`verdict{call,headline,bottom_line,key_driver,what_changes_it,confluence_score}`**; `token_profile` (null); `derivatives` (null); **`trade_plan{entry,stop,targets[],atr_14_usd,atr_14_pct,atr_multiplier,risk_reward[],method,preview_only}`**; `intelligence{…}` | status **partial** untuk SOL: token_profile & derivatives `unavailable` meski include_perp=true (warning: "Derivatives analysis is unavailable or was not requested"); confluence `CONFIRMED` 1.0, gates momentum & market_activity; verdict.call **`constructive`**; plan entry 103.90 stop 96.91 targets [110.88,117.86] rr [1,2] method `wilder_atr_14_from_daily_ohlcv` preview_only true | market_data, technical_analysis, token_profile, derivatives, trade_plan |
| compare_tokens | `tokens[]{symbol,rank,price_usd,changes{h1,h24,d7,d30},metrics{market_cap_usd,volume_24h_usd,rsi_14,atr_14_pct,trend},verdict,token_profile}`; `factors[]{key,label,winner,scores{sym:val}}` (momentum, market_activity, volatility); `winner`; `conclusion{pick,headline,rationale,runner_up_case,confidence,method}`; `coverage_by_token{sym:{market_data,token_profile}}` | conclusion.method `deterministic`, confidence `medium`; warning "no narrative model was used" | token_market_data, comparison_factors, token_profiles, conclusion |
| monitor_market_sentiment_shift | `observation_as_of`; `evidence.fear_greed{value,label,baseline_7d_value,change_7d_points,change_direction,material_shift}`; `evidence.funding{latest_bps,current_7d_window.mean_bps,wow_change_bps,current_7d_percentile_90d,crowding_state}`; `evidence.liquidation{total_usd,dominant_liquidated_side,percentile_90d,pressure_state,long_share_pct}`; `evidence.altseason{index,phase}`; `evidence.sentiment_regime` | F&G 75 (−5, material_shift false); funding crowding `normal`; liquidation pressure `normal`; altseason 27 `transition`; regime `extreme_greed_unconfirmed` | research, evidence, diagnostics |
**Kosakata terukur** dari 65 snapshot perekam (1 Sep 2026 03:47–04:10Z; ulangi dengan `make vocab`):
- `analyze_token.data.verdict`: `accumulate` (12) · `caution — overbought` (9) · `neutral` (7) — **bentuk majemuk** "kepala — kualifikasi"; arah dibaca dari kata kepala.
- `deep_analysis.data.verdict.call`: `constructive` (13) · `neutral` (12) · `cautious` (2).
- `deep_analysis.data.confluence.state`: `CONFIRMED` (16) · `MIXED` (11); gate yang pernah gagal: `momentum` (7), `market_activity` (4).
- `market_overview.data.regime`: `risk_on` (3) — belum pernah melihat nilai lain.
Enum lengkap tetap tidak diketahui → nilai di luar tabel `preflight/params.py` menjadikan item `unspoken` (tidak ditebak) dan tercatat.
`as_of` ISO-8601 Z; `chain` null di semua sampel; `contract` hanya di scan_market.
**Latensi terukur (1 Sep 2026, WIB siang):** `deep_analysis` **17–34 dtk**; `analyze_token`, `market_overview`, `scan_market`, `monitor_market_sentiment_shift` ≈1–3 dtk; `compare_tokens` ≈40 dtk (3 simbol). Siklus perekam 6 simbol = 281 dtk. **Konsekuensi desain: yang mengikat adalah LATENSI, bukan kuota 60/mnt.** UI tidak boleh memanggil tool secara live; `preflight` memakai snapshot store yang masih segar (item 1) dan hanya memanggil live bila perlu — sebuah preflight penuh yang semuanya live bisa >60 dtk.

Tool independen: tidak ada prasyarat `market_overview`.

Envelope sukses (semua tool): `schema_version, tool, status ∈ {ok, partial, unavailable}, data_mode ∈ {live, mixed, simulated, unknown}, as_of, request (argumen ternormalisasi), data, summary{headline, key points}, availability (per seksi), warnings`.
Aturan pemakaian dari guide: logika dari `data`, bukan `summary`; **jangan ubah null/unavailable jadi nol**; optional profile unavailable tidak menurunkan evidence utama.
Error REST: HTTP status + `code` stabil + message + trace id. Error protokol MCP di `error`; kegagalan tool di `result.isError`.
Nama field di dalam `data`: DIVERIFIKASI dari docs/samples (tabel "Field nyata" di atas). Enum verdict lengkap belum.

### Bentuk transport & error REST — DIVERIFIKASI 1 Sep 2026 (TASKS 1.1, verifikasi klien `core/ryo_client.py`)
Sumber: `docs/samples/*.json`, `docs/samples/*.headers` (rekaman 1 Sep 2026 03:47–03:48Z) + probe langsung tanpa auth 1 Sep 2026 08:34Z
(`curl -sS -i https://app-ryochan.com/api/mcp/health`, `.../tools`, `-X POST .../tools/market_overview/call -d '{}'`).
- **Top-level respons REST sukses = `{tool, result, latency_ms}`** — bukan hanya `result`. Envelope tetap `response.json()["result"]`
  (10 kunci, urutan: `schema_version, tool, status, data_mode, as_of, request, data, summary, availability, warnings`). `schema_version` = `"1.0"` (string) di keenam sampel.
- `latency_ms` = latensi sisi server. Terukur: `compare_tokens` 42409 · `deep_analysis` 28770 · `monitor_market_sentiment_shift` 618 ·
  `market_overview` 431 · `analyze_token` 307 · `scan_market` 158. → **timeout klien 90 dtk aman** (>2× terburuk yang pernah terlihat).
- **`x-trace-id` (huruf kecil) ada di SETIAP respons** — 200, 401, dan `/health` tanpa auth. Ini header yang dibaca untuk trace id, bukan `trace_id` body saja.
- **Error REST**: body `{code, message, details, trace_id}`; `code` UPPER_SNAKE. Terlihat: `401 {"code":"UNAUTHENTICATED","message":"Missing credential. Send 'Authorization: Bearer ryo_mcp_...'.","details":null,"trace_id":"…"}` + header `www-authenticate: Bearer`.
- `GET /health` **200 tanpa auth** dan **tanpa header `x-ratelimit-*`**. `/tools` dan `/tools/{tool}/call` tanpa auth → 401 (bukan 404) → kedua path terkonfirmasi ada.
- `x-ratelimit-reset` = **epoch detik**: `1788234492` = 2026-09-01T03:48:12Z, ±58 dtk setelah header `Date` respons → jendela per menit.
  `x-ratelimit-limit: 60` di keenam sampel. `x-ratelimit-remaining` turun 1 tiap panggilan tool di dalam satu jendela
  (59→58→57→56) lalu naik lagi saat jendela berganti. **Header dihitung saat request DITERIMA, bukan saat respons dikirim**:
  `compare_tokens.headers` punya `Date: 03:48:28` tetapi `x-ratelimit-reset` = 03:48:12 (sudah lewat), karena panggilan itu
  masuk ~03:47:46 dan berjalan 42 dtk → jangan pakai `Date` respons untuk menghitung sisa jendela.
- **inputSchema (docs/samples/catalog.json)**: keenam tool `additionalProperties: false`. `required`: `analyze_token`/`deep_analysis` = `["symbol"]`,
  `compare_tokens` = `["symbols"]`; tiga tool lain tanpa `required`. Tipe: `top_n` **number**, `include_perp` **boolean**, `symbols` **string** ("2-4 simbol,
  dipisah koma/spasi"), `intent` enum `swing|hold|spot`, `time_window` enum `["7d"]` default `"7d"`, sisanya string.
  **Catatan presisi:** `additionalProperties:false` melarang properti *tidak dikenal*, BUKAN nilai `null`. Alasan sah untuk tidak mengirim argumen
  `None` adalah **tipe** properti (string/number/boolean, tak ada yang nullable) — bukan `additionalProperties`.

### Tipe & kosakata envelope (10 field) — DIVERIFIKASI 1 Sep 2026 (TASKS 1.3, verifikasi `core/skill.py` = cermin envelope, ADR-013/015)
Sumber: keenam `docs/samples/*.json` (`response["result"]`) + **470 envelope perekam** di `data/ryo.db` (dibuka read-only,
`sqlite3.connect("file:data/ryo.db?mode=ro", uri=True)`, hitung 1 Sep 2026; DB tidak di-commit, ulangi dengan `make record`).
Semua 470 payload memakai **himpunan 10 kunci yang sama** — tidak ada satu pun envelope dengan kunci lebih/kurang.
| Field | Tipe yang benar-benar muncul | Nilai teramati (6 sampel + 470 snapshot) |
|---|---|---|
| `schema_version` | `str` | `"1.0"` di **6/6 sampel DAN 470/470 snapshot** — bukan hanya `analyze_token`; tidak pernah `"1"` |
| `tool` | `str` | nama tool, sama dengan `tool` di level respons REST |
| `status` | `str` | **hanya `ok` & `partial`** (snapshot: ok 278/partial 192; sampel: ok 5/partial 1 = `deep_analysis`); `unavailable` **BELUM PERNAH TERAMATI** (0/476). Enum bertiga berasal dari RYO Builder MCP Guide, bukan dari sampel |
| `data_mode` | `str` | **hanya `live` (476/476)**; `mixed`, `simulated`, `unknown` **BELUM PERNAH TERAMATI**. Enum berempat berasal dari Guide |
| `as_of` | `str`, **tidak pernah `null`** (0/476) | selalu presisi DETIK: `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$` — tidak pernah ada mikrodetik |
| `request` | `dict` | argumen ternormalisasi; nilai bertipe sesuai inputSchema (`symbol:str`, `include_perp:bool`, `top_n:int`, `intent:str`, `symbols:str`, `time_window:str`) — `scan_market` mengembalikan `{"top_n":10}` meski dipanggil tanpa argumen → server MENGISI default ke `request` |
| `data` | `dict`, **tidak pernah `null`** (0/476) | kunci per tool: lihat tabel "Field nyata" |
| `summary` | `dict` dengan **persis** `{headline, key_points}` di 476/476 | `headline: str`, `key_points: list[str]` (tidak pernah list kosong-bertipe-lain) |
| `availability` | `dict[str, str]` | kosakata NILAI teramati: `available` (1430) · `unavailable` (398) · `not_requested` (5, `scan_market.theme_filter`). Kunci = nama SEKSI tool, bukan id item |
| `warnings` | `list[str]` | 0–2 entri di sampel; semua elemen `str` |

**Penyimpangan Tenko yang disengaja terhadap envelope di atas** (dicatat di sini supaya tidak terbaca sebagai fakta RYO):
1. Field ke-11 `simulated: true` — diwajibkan `docs/rules-compliance.md`, tidak ada di RYO (ADR-015).
2. `SkillResult.as_of` boleh `null` dan `data` boleh `null` saat `status == "unavailable"` (spec §3). RYO **tidak pernah**
   mengeluarkan ketiganya; cabang ini karena itu TIDAK BISA diverifikasi terhadap RYO. Sah, bukan kontradiksi: Guide tidak
   pernah menyatakan `as_of` non-null, dan `as_of` adalah waktu DATA — tanpa `data` tidak ada waktu data yang jujur untuk ditulis
   (menulis `now()` justru mengarang kesegaran). Bila RYO kelak mengeluarkan `status: unavailable`, bandingkan lagi di sini.
3. Nilai `availability` `"unspoken"` adalah kosakata TENKO (spec §3), bukan RYO; kunci kita = id item, kunci RYO = nama seksi.
   Keduanya `dict[str,str]` sehingga klien RYO tetap bisa membacanya.
4. `as_of` kita di-serialisasi lewat `datetime.isoformat()` → **mikrodetik ikut tercetak bila datetime-nya punya mikrodetik**
   (`2026-09-01T03:47:12.123456Z`), bentuk yang tidak pernah dipakai RYO. Jalur `from_items` aman (as_of diparse dari envelope
   RYO, presisi detik), tetapi `ItemSource.as_of` yang diisi dari `snapshots.ts_utc` (mikrodetik) akan menyimpang.

## B–D. Sumber eksternal — TIDAK DIPAKAI (ADR-012/013)
Jalur nyata hanya memakai tool RYO (§A) + satu LLM untuk narasi (SDK/model dipin di docs/versions.md saat TASKS 2.3). Sumber lain
(Helius, Jupiter, DexScreener, GeckoTerminal) dihapus dari desain; bila suatu saat dibutuhkan, verifikasi dulu di sini.

## E. Aturan lomba yang sudah dijawab tertulis (kutipan + tanggal + kanal) — TASKS 0.2
| Pertanyaan | Jawaban | Sumber/tanggal |
|---|---|---|
| Bolehkah skill memanggil API pihak ketiga dengan disclosure? (tidak lagi relevan — ADR-012 hanya tool RYO + LLM) | **Tersirat YA**: ide resmi T3 "Scam Wallet Detection — use an external wallet-history source"; "What you bring": Tavily untuk berita, "your own AI model". Jawaban tertulis eksplisit untuk kasus kita: BELUM | ryobuild.com landing (dibaca 1 Sep 2026) |
| Repo yang disetujui: org GitHub tertentu / template / repo pribadi private boleh? | BELUM DIJAWAB | |
| Format skill T3? | **Tidak ada spec formal** — T3 = tool yang menurut builder dibutuhkan RYO tapi belum ada; rubric: "return a predictable shape, minimal work to ship". → ADR-013: output skill = envelope RYO §A. | user + landing, 1 Sep 2026 |
| Satu tim boleh mengirim >1 BUIDL? | **TIDAK — maksimal 1 BUIDL** (jawaban penyelenggara, disampaikan user 1 Sep 2026). Satu BUIDL boleh masuk 3 track, tiap track dinilai terpisah. | user, 1 Sep 2026 |
| Rate limit tool RYO & apakah perekam periodik dapat diterima? | Sebagian: kuota per key ada, dibaca via whoami; guide: "avoid tight polling loops". Angka & penerimaan perekam periodik: BELUM DIJAWAB | RYO Builder MCP Guide, 13 Aug 2026 |
| Kriteria Social Media prize (platform, tag, jumlah post)? | BELUM DIJAWAB | |
| Bobot penilaian | **40 umum (Combining sources 20, Coping with failure 20) + 60 track** — T1: Real thinking 25/Cause&effect 20/Repeatability 10/Advanced execution 5 · T2: Easy to read 25/Sorted by importance 25/Works for everyone 10 · T3: Fills a gap 25/Follows the spec 15/Could we use it 20. Tidak dinilai: volume API call, chart tanpa argumen, P&L jangka pendek, data placeholder. Per track yang dimasuki; hadiah T1 $6.000 · T2 $3.500 · T3 $3.500; 16 award; judging 9–16 Sep; pengumuman 17–19 Sep | ryobuild.com landing, 1 Sep 2026 |
| Definisi "Agentic SocialFi" resmi | Agentic = kumpulkan bukti → nalar → putuskan → simpan jalurnya. **Social = shareable/kolaboratif: debat, public reasoning, group discovery, reputation, community-native market experiences.** Finance = pasar live & keputusan yang penting bagi trader. | ryobuild.com landing, 1 Sep 2026 |
| Ide resmi (zona jenuh hackathon ini) | T1: Multi-KOL Narrative Agent (spotlight, tanpa bonus otomatis), News Checker, Bull/Bear Debate, Meme/Token Hunter, Whale Tracker, Buy-the-Dip. T2: News Dashboard, Market Screener, Tech Indicator Interface. T3: Tech Indicator, Scam Wallet Detection, "bring your own gap". | ryobuild.com landing, 1 Sep 2026 |
| Bolehkah proyek disebut/ditampilkan (tanpa kode) di portfolio setelah lomba? | BELUM DIJAWAB | |

## F. Dataset (referensi sitasi, bukan komponen runtime)
- RED-PUMP-2026-v1 — Zenodo doi 10.5281/zenodo.20633486, CC BY 4.0, arXiv 2607.02823 (860K launch pump.fun Mei–Jun 2026). Dipakai hanya bila ada task yang membutuhkannya; saat ini TIDAK.
- MELT (eks-MemeTrans) — github.com/git-disl/MELT, **CC BY-NC 4.0** (dicek 1 Sep 2026) → TIDAK dipakai (komersialisasi oleh RYO dilarang lisensi).
