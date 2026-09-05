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
| market_overview | `regime`; `sentiment.fear_greed_index`, `sentiment.label`; `market.breadth`, `market.breadth_details{universe_size,advancing,declining}`; `top_movers.gainers[]/losers[]{symbol,change_24h_pct}` | regime `risk_on`; label `extreme_greed`; breadth 0.8571 (91 listing) — sampel tunggal; kosakata penuh (regime juga `neutral` DAN `risk_off`, label juga `greed`) di "Kosakata terukur". **`risk_on` bukan satu-satunya nilai**: `risk_off` teramati 1× (id 763, 2026-09-01T18:06:48Z) | market_regime, market_totals, market_sentiment, market_breadth, top_movers |
| scan_market | `candidates[]{rank,symbol,name,chain,contract,price_usd,change_24h_pct,market_cap_usd,volume_24h_usd,turnover_ratio,momentum_score,established_asset,reason}`, `selection_method`, `excluded_candidate_count` | top: USELESS, ARB, PONS, CASHCAT, BONK — **memecoin ada**; `contract` terisi (Solana & EVM), `chain` null; method "Ranked by 24-hour momentum and market activity" | ranked_candidates, theme_filter (`not_requested`) |
| analyze_token | `asset{symbol,name,chain,contract,rank}`; `market{price_usd,market_cap_usd,fully_diluted_value_usd,volume_24h_usd}`; `performance{change_1h_pct,change_24h_pct,change_7d_pct,change_30d_pct}`; `technical_analysis{trend,rsi_14,momentum_30d_pct,atr_14_pct}`; `intelligence{narrative,catalysts[],risks[]}`; **`verdict` (string)** | trend `up`; rsi 68.9; verdict **`accumulate`** — sampel tunggal; 4 nilai verdict & 3 nilai trend terukur di "Kosakata terukur" | market_data, technical_analysis, market_intelligence |
| deep_analysis | `market{mcap_usd,fdv_usd,volume_24h_usd,dominance_pct,circulating_supply,max_supply,price_usd}`; `performance{h1,h24,d7,d30}`; `technical_analysis{…sama}`; `confluence{state,score,gates[]{name,passed,detail},status}`; **`verdict{call,headline,bottom_line,key_driver,what_changes_it,confluence_score}`**; `token_profile` (null); `derivatives` (null); **`trade_plan{entry,stop,targets[],atr_14_usd,atr_14_pct,atr_multiplier,risk_reward[],method,preview_only}`**; `intelligence{…}` | status **partial** untuk SOL: token_profile & derivatives `unavailable` meski include_perp=true (warning: "Derivatives analysis is unavailable or was not requested"); confluence `CONFIRMED` 1.0, gates momentum & market_activity; verdict.call **`constructive`**; plan entry 103.90 stop 96.91 targets [110.88,117.86] rr [1,2] method `wilder_atr_14_from_daily_ohlcv` preview_only true | market_data, technical_analysis, token_profile, derivatives, trade_plan |
| compare_tokens | `tokens[]{symbol,rank,price_usd,changes{h1,h24,d7,d30},metrics{market_cap_usd,volume_24h_usd,rsi_14,atr_14_pct,trend},verdict,token_profile}`; `factors[]{key,label,winner,scores{sym:val}}` (momentum, market_activity, volatility); `winner`; `conclusion{pick,headline,rationale,runner_up_case,confidence,method}`; `coverage_by_token{sym:{market_data,token_profile}}` | conclusion.method `deterministic`, confidence `medium`; warning "no narrative model was used" | token_market_data, comparison_factors, token_profiles, conclusion |
| monitor_market_sentiment_shift | `observation_as_of`; `evidence.fear_greed{value,label,baseline_7d_value,change_7d_points,change_direction,material_shift}`; `evidence.funding{latest_bps,current_7d_window.mean_bps,wow_change_bps,current_7d_percentile_90d,crowding_state}`; `evidence.liquidation{total_usd,dominant_liquidated_side,percentile_90d,pressure_state,long_share_pct}`; `evidence.altseason{index,phase}`; `evidence.sentiment_regime` | F&G 75 (−5, material_shift false); funding crowding `normal`; liquidation pressure `normal`; altseason 27 `transition`; regime `extreme_greed_unconfirmed` | research, evidence, diagnostics |
**Kosakata terukur** — DIPERBARUI 1 Sep 2026 16:45Z (menggantikan hitungan 65-snapshot sebelumnya). Sumber: `data/ryo.db`
(perekam kami sendiri, BUKAN spesifikasi RYO), dibuka read-only `sqlite3.connect("file:data/ryo.db?mode=ro", uri=True)`,
satu lintasan penuh atas **697 snapshot** (`id <= 697`, ts 2026-09-01T03:51:43Z–16:42:54Z ≈ 12,9 jam; analyze_token 365 ·
deep_analysis 271 · market_overview 19 · monitor_market_sentiment_shift 19 · compare_tokens 14 · scan_market 9).
DB tidak di-commit dan perekam masih menulis saat dihitung → hitungan naik antar-lintasan, himpunan nilai stabil.
Tidak ada target `make vocab` (dicek di Makefile 1 Sep 2026 — kalimat lama salah); ulangi dengan:
`python3 -c 'import sqlite3,json,collections;c=sqlite3.connect("file:data/ryo.db?mode=ro",uri=True);k=collections.defaultdict(collections.Counter);[k[t].update([json.dumps(json.loads(p).get("data",{}).get("verdict"))]) for t,p in c.execute("SELECT tool,payload_json FROM snapshots")];print(k)'`
(untuk path lain ganti pemetaan: `data.verdict.call`, `data.confluence.state`, `data.regime`).

- `analyze_token.data.verdict` (365): `accumulate` (167) · `caution — overbought` (108) · `neutral` (86) · **`avoid` (4 — NILAI KEEMPAT, baru terlihat 1 Sep 08:45Z, id 349/420/483/688)**.
  **Bentuk string:** 3 dari 4 nilai adalah **kata tunggal tanpa pemisah**; satu-satunya bentuk majemuk yang pernah terlihat
  adalah `caution — overbought`. Di seluruh 365 string, karakter non-alfanumerik yang muncul hanya spasi `U+0020` (216×) dan
  em dash `U+2014` (108×) → pemisahnya persis `" — "` (spasi ASCII + U+2014 + spasi ASCII), bukan `-`, `–`, atau NBSP.
  Kata kepala setelah split di `U+2014`: accumulate 167 · caution 108 · neutral 86 · avoid 4. Kata `caution` **tidak pernah
  muncul telanjang** (0/365). `avoid` mentah = `"avoid"` (escape `avoid`, tanpa pemisah), summary.headline `"STABLE analysis: avoid."`
  — bandingkan `"… analysis: caution — overbought."` → `avoid` adalah **kata kepala yang berdiri sendiri**, sejajar `accumulate`.
  **Untuk `preflight/params.py`:** cocokkan **string penuh** lebih dulu dari tabel eksplisit; split di `—` boleh hanya sebagai
  lapisan kedua (dan kualifikasi seperti `overbought` wajib ikut tercatat, tidak dibuang diam-diam). Memotong-lebih-dulu tidak
  aman: bila kelak muncul `avoid — <kualifikasi>` atau `accumulate — <kualifikasi>`, potongan kepala akan menyembunyikan
  informasi yang belum pernah kita lihat.
- Konteks `avoid` (deskriptif, BUKAN aturan RYO — jangan diturunkan ke kode): keempatnya simbol `STABLE`, `status: ok`,
  `technical_analysis.trend: down`, `rsi_14: 39.7`, `momentum_30d_pct` −10,2…−14,2%. `STABLE` juga pernah `neutral` (2×) →
  verdict adalah fungsi data, bukan properti simbol. Rentang `rsi_14` per verdict: accumulate 53,8–69,3 · caution — overbought
  70,4–93,2 · neutral 39,7–68,9 · avoid 39,7. verdict × trend: `avoid` hanya dengan `down` (4/4), tetapi `neutral` juga muncul
  dengan `down` (4) → `avoid` bukan sekadar fungsi `trend`.
- `deep_analysis.data.verdict.call` (271): `neutral` (131) · `constructive` (126) · `cautious` (14). Semuanya kata tunggal;
  **0/271 mengandung `U+2014`** → tidak ada bentuk majemuk di field ini. `verdict` selalu 6 kunci yang sama
  (`bottom_line, call, confluence_score, headline, key_driver, what_changes_it`) di 271/271.
- `deep_analysis.data.confluence.state` (271): `CONFIRMED` (155) · `MIXED` (116). `confluence.status` = `available` di 271/271.
  `gates` selalu tepat 2 dan berurutan `[momentum, market_activity]` (271/271); yang pernah gagal: `momentum` (86), `market_activity` (30).
- `market_overview.data.regime` — **DIGANTI oleh sapuan `id <= 835` di bawah (n=20): `neutral` 13 · `risk_on` 6 · `risk_off` 1**.
  Angka sapuan 697 dulu: `neutral` (13) · `risk_on` (6). Kalimat lama "`risk_on` (3) — belum pernah melihat
  nilai lain" SALAH per 1 Sep 2026 dan dihapus; `neutral` justru mayoritas. Nilai pertama `neutral` pada id 135, 05:27Z;
  nilai pertama `risk_off` pada id 763, 18:06:48Z.
- `market_overview.data.sentiment.label` (19): `extreme_greed` (12) · **`greed` (7 — nilai kedua; tabel "Field nyata" di atas
  hanya mencatat `extreme_greed` dari sampel tunggal)** — **hitungan diganti sapuan `id <= 835`: `extreme_greed` 12 · `greed` 8**.
- `monitor_market_sentiment_shift.data.evidence.sentiment_regime` (19): `extreme_greed_unconfirmed` (12) · **`mixed_sentiment` (7 — nilai kedua)**
  — **hitungan diganti sapuan `id <= 835`: 12 · 8**.
- `compare_tokens.data.tokens[].verdict` (42 token dari 14 panggilan): `neutral` (31) · `constructive` (11) → field ini memakai
  **kosakata `deep_analysis.verdict.call`**, BUKAN kosakata `analyze_token.verdict` (`accumulate`/`avoid` tidak pernah muncul di sini).
  Dua namespace verdict yang berbeda; jangan disatukan dalam satu tabel pemetaan.
- `technical_analysis.trend`: analyze_token `up` (326) · `sideways` (31) · `down` (8); deep_analysis `up` (247) · `sideways` (22) · `down` (2)
  — **hitungan diganti sapuan `id <= 835`: analyze_token 402 · 37 · 13; deep_analysis 289 · 26 · 4; compare_tokens `up` 42/42**.
  (Tabel "Field nyata" hanya menyebut `up` karena sampelnya satu.)
- **Sapuan menyeluruh 697 baris** atas SEMUA field string kategoris (bukan sampel): tidak ada nilai tambahan berfrekuensi 1–2
  di keempat field di atas — himpunan di atas lengkap terhadap DB ini. Satu-satunya nilai langka di seluruh sapuan ada pada
  `market_overview.data.top_movers.gainers[].symbol` (`ETHFI` 1×, `ENA` 2×), dan itu simbol, bukan enum.

**Sapuan 2 Sep 2026 (BERLAKU) — `id <= 835`.** `data/ryo.db` dibuka read-only saat perekam live masih menulis
(`sqlite3.connect("file:data/ryo.db?mode=ro", uri=True)`), **835 snapshot** (ts 2026-09-01T03:51:43Z–18:25:36Z;
analyze_token 452 · deep_analysis 319 · market_overview 20 · monitor_market_sentiment_shift 20 · compare_tokens 14 · scan_market 10).
Hitungan lengkap sapuan ini (menggantikan semua hitungan sapuan 697 dan 762 di atas; himpunan nilai = daftar di bawah):
`analyze_token.data.verdict` accumulate 203 · caution — overbought 129 · neutral 115 · avoid 5 ·
`deep_analysis.data.verdict.call` neutral 156 · constructive 145 · cautious 18 ·
`deep_analysis.data.confluence.state` CONFIRMED 183 · MIXED 136 ·
`market_overview.data.regime` neutral 13 · risk_on 6 · **risk_off 1** ·
`market_overview.data.sentiment.label` extreme_greed 12 · greed 8 ·
`monitor….evidence.sentiment_regime` extreme_greed_unconfirmed 12 · mixed_sentiment 8 ·
`monitor….evidence.funding.crowding_state` normal 20/20 · `monitor….evidence.liquidation.pressure_state` normal 20/20 ·
`monitor….evidence.fear_greed.change_direction` down 20/20 · `compare_tokens.data.tokens[].verdict` neutral 31 · constructive 11 (42 token) ·
`technical_analysis.trend` analyze_token up 402 · sideways 37 · down 13; deep_analysis up 289 · sideways 26 · down 4.
Hitungan yang dilaporkan quant-engineer (accumulate 195 · caution — overbought 123 · neutral 107 · avoid 5; call neutral 154 ·
constructive 145 · cautious 17; CONFIRMED 182 · MIXED 134) DIVERIFIKASI benar **pada `id <= 810`** (ts ≤ 18:20:14Z) — bukan salah,
hanya lebih tua ~25 baris / ~19 menit. Himpunan nilainya identik; yang berubah hanya frekuensi.

- **DICABUT — klaim "`risk_off` = 0 amatan" (sapuan `id <= 762`, ditulis 2 Sep 2026).** Klaim itu benar saat ditulis dan basi
  satu baris kemudian. Ikut dicabut, sebagai satu paket: "pencarian substring `risk[_ -]?off` atas seluruh payload → 0 baris",
  "ejaan `risk_off` hanya ada di `docs/spec.md` §4 (dokumen KAMI)", dan "bentuk negatifnya belum pernah terlihat sama sekali".
  Ketiganya sekarang **TIDAK BENAR**. Jangan kutip lagi; jangan pakai untuk menghapus baris `risk_off` dari `preflight/params.py`.
- **`market_overview.data.regime` = `risk_off` TERAMATI.** Nilai ketiga field ini. n=20 amatan `market_overview`:
  `neutral` 13 · `risk_on` 6 · **`risk_off` 1**. Amatan tunggal itu, verbatim dari `payload_json`:
  `id=763`, `ts_utc 2026-09-01T18:06:48.538132+00:00`, `as_of 2026-09-01T18:06:48Z`, `status: ok`, `data_mode: live`,
  `warnings: []`, `availability.market_regime: available`, `data.regime: "risk_off"`, `data.sentiment.label: "greed"`,
  `data.sentiment.fear_greed_index: 73.0`, `data.market.breadth: 0.3846` (advancing 35 / declining 56 / universe 91).
  Jadi ia **bukan lagi baris spec-only**: RYO benar-benar mengeluarkan string `risk_off` dengan ejaan snake_case itu.
  Ulangi (satu baris, mencetak sebaran + id kemunculan pertama + max id yang disapu):
  `python3 -c 'import sqlite3,json,collections;c=sqlite3.connect("file:data/ryo.db?mode=ro",uri=True);r=list(c.execute("SELECT id,tool,payload_json FROM snapshots ORDER BY id"));m=[(i,json.loads(p)["data"]["regime"]) for i,t,p in r if t=="market_overview"];print("max id",r[-1][0]);print(collections.Counter(v for _,v in m));print({v:min(i for i,w in m if w==v) for _,v in m})'`
  → `max id 835` · `Counter({'neutral': 13, 'risk_on': 6, 'risk_off': 1})` · `{'risk_on': 1, 'neutral': 135, 'risk_off': 763}`.
- **[`Urutan breadth memang rapi` DICABUT oleh sapuan `id <= 3238`, 5 Sep 2026 — rentang breadth `neutral` (0,2088) dan `risk_off` (0,3846) kini TUMPANG TINDIH; lihat blok "Sapuan 5 Sep 2026" di bawah.]**
- **Konteks `risk_off` — n=1, DESKRIPTIF, BUKAN kontrak.** Amatan itu datang dengan `breadth` 0.3846, terendah dari 20 amatan
  (`risk_on`: 0.8571 ×4 dan 0.9011 ×2; `neutral`: 0.4835–0.8022), dan `sentiment.label` `greed` (bukan `extreme_greed`).
  Urutan breadth memang rapi, **tetapi jangan diturunkan menjadi aturan**: n=1 untuk `risk_off`, dan `label: greed` sama sekali
  tidak diskriminatif — 8 amatan berlabel `greed`, 7 di antaranya `neutral`. Tidak ada ambang breadth yang boleh dikodekan dari
  sini; regime dibaca apa adanya dari `data.regime`, tidak pernah dihitung ulang dari `breadth`.
- `monitor….data.evidence.fear_greed.change_direction` (20): **`down` 20/20 · `up` = 0 amatan** (`id <= 835`). Nol itu STRUKTURAL,
  bukan kebetulan sampel: `baseline_7d_date` = `2026-08-25` dan `baseline_7d_value` = 80 di 20/20, `value` 73–75, `change_7d_points`
  ∈ {−5, −6, −7} (selalu negatif) → jendela ini secara aritmetis tidak bisa menghasilkan arah lain. `fear_greed.data_date`
  = `2026-09-01` di 20/20: **F&G diperbarui sekali sehari**, jadi 20 panggilan ≈ 1 titik data, bukan 20 bukti independen.
  Nilai untuk `change_7d_points == 0` (entah `flat`, `sideways`, `unchanged`, atau lainnya) juga belum pernah terlihat.
  **Jebakan grep:** nilai string `"up"` MEMANG muncul **1022×** di DB (`id <= 835`) — tetapi hanya pada empat path `…trend`:
  `analyze_token.data.technical_analysis.trend` 402 · `deep_analysis.data.technical_analysis.trend` 289 ·
  `deep_analysis.data.confluence.gates[].detail.trend` 289 · `compare_tokens.data.tokens[].metrics.trend` 42;
  **0×** pada `change_direction` (yang di sana `down` 20/20). Namespace berbeda; kemunculan `"up"` di `trend` BUKAN bukti untuk
  `FEAR_GREED_CHANGE_DIRECTION`. Pencabutan baris `up` (provenance = tebakan kami; spec §4 item 4 tidak pernah menulis string itu)
  tetap BERLAKU.
- **[DICABUT oleh sapuan `id <= 3238`, 5 Sep 2026: `material_shift = true` TERAMATI 5×, pertama id=910 `2026-09-02T01:13:09Z`; `change_7d_points` kini −11..0, bukan {−5,−6,−7}. Lihat blok "Sapuan 5 Sep 2026" di bawah.]**
- `monitor….data.evidence.fear_greed.material_shift` = `false` di **20/20** (`id <= 835`) → cabang "pergeseran material" item 4 spec §4 —
  SATU-SATUNYA cabang yang membaca `change_direction` — belum pernah dijalankan oleh data nyata.
- Kunci `monitor` yang lengkap di 20/20 (baris "Field nyata" di atas hanya menyebut yang dipakai item): `data` =
  {observation_as_of, status, source_attribution, summary, evidence, source, diagnostics}; `fear_greed` = {data_date, value,
  label, baseline_7d_date, baseline_7d_value, change_7d_points, change_direction, material_shift}; `funding` juga punya
  {data_date, previous_7d_window, wow_change_pct}; `liquidation` juga punya {data_date, coverage_scope}; `altseason` =
  {data_date, index, phase}.
- **[DICABUT sebagian oleh sapuan `id <= 2165`, 4 Sep 2026 — lihat blok "Sapuan 4 Sep 2026" di bawah: `dominant_liquidated_side`
  juga `longs`/`balanced`, `altseason.phase` juga `bitcoin_season`, `crowding_state` juga `long_crowding`, `pressure_state` juga `extreme`.]**
  Kosakata kategoris monitor lain (20 amatan, semuanya nilai TUNGGAL — jangan dibaca sebagai enum lengkap):
  `liquidation.dominant_liquidated_side` `shorts` (20) · `altseason.phase` `transition` (20), di samping
  `funding.crowding_state` dan `liquidation.pressure_state` `normal` (20) yang sudah tercatat di atas.
- `deep_analysis.data.trade_plan` **long di 319/319** (`id <= 835`) (`stop < entry` selalu). Arah `short` yang disebut spec §4 ("bila plan short
  muncul, arah dibalik") BELUM PERNAH TERAMATI → cabang short adalah cabang tak-teruji-data, bukan cabang biasa.

**Sapuan 4 Sep 2026 (BERLAKU) — `id <= 2165`, MENGGANTIKAN semua hitungan sapuan 697 / 762 / 835 di atas.**
`data/ryo.db` dibuka read-only saat perekam live masih menulis (`sqlite3.connect("file:data/ryo.db?mode=ro", uri=True)`,
tidak ada penulisan), **2165 snapshot**, ts 2026-09-01T03:51:43Z–2026-09-04T13:48:37Z (≈81,9 jam):
analyze_token 1151 · deep_analysis 823 · compare_tokens 74 · market_overview 42 · monitor_market_sentiment_shift 42 · scan_market 33.
Hitungan lengkap (HIMPUNAN nilai = daftar ini; angka akan basi lagi dalam hitungan jam):
`analyze_token.data.verdict` accumulate 483 · caution — overbought 331 · neutral 318 · avoid 19 ·
`deep_analysis.data.verdict.call` neutral 395 · constructive 390 · cautious 38 ·
`deep_analysis.data.confluence.state` CONFIRMED 492 · MIXED 331 (`confluence.status` `available` 823/823) ·
`market_overview.data.regime` risk_on 22 · neutral 15 · **risk_off 5** (bukan lagi n=1) ·
`market_overview.data.sentiment.label` extreme_greed 24 · greed 18 ·
`compare_tokens.data.tokens[].verdict` neutral 105 · constructive 89 (194 token; `cautious` masih **0×** di field ini) ·
`compare_tokens.data.tokens[].metrics.trend` up 194/194 · `compare_tokens.data.intent` swing 74/74 · `conclusion.confidence` medium 74/74 ·
`technical_analysis.trend` analyze_token up 994 · sideways 101 · down 56; deep_analysis up 743 · sideways 62 · down 18.

**EMPAT nilai baru di `monitor_market_sentiment_shift` — TERAMATI, bukan tebakan (sapuan `id <= 2165`, 4 Sep 2026).**
Semuanya `status: ok`, `data_mode: live`, `warnings: []`, `availability.evidence: available`.
| field (path persis) | nilai baru | n | snapshot pertama (id, ts_utc, as_of) | nilai lama di field yang sama |
|---|---|---|---|---|
| `monitor….data.evidence.sentiment_regime` | `complacent_crowding` | **3** | id=1976, 2026-09-04T11:21:24.489160Z, as_of 2026-09-04T11:21:23Z | extreme_greed_unconfirmed 21 (id=2) · mixed_sentiment 18 (id=301) |
| `monitor….data.evidence.liquidation.pressure_state` | `extreme` | **3** | id=1976 (sda) | normal 39 (id=2) |
| `monitor….data.evidence.funding.crowding_state` | `long_crowding` | **3** | id=1976 (sda) | normal 39 (id=2) |
| `monitor….data.evidence.fear_greed.change_direction` | `flat` | **2** | id=1976 (sda) | down 40 (id=2); `up` masih **0 dari id <= 2165** |
Ketiga baris pembawa nilai baru adalah id **1976, 2047, 2106** (11:21Z, 12:31Z, 13:32Z 4 Sep 2026). `flat` hanya di **1976 dan 2047**:
pada id 2106 `change_7d_points` sudah −3 sehingga `change_direction` kembali `down` **sementara** tiga field lain tetap pada nilai barunya
→ keempatnya **bukan satu paket**; jangan diperlakukan sebagai satu peristiwa yang selalu muncul bersama.
Ulangi (mencetak sebaran GABUNGAN keempat field + id/ts kemunculan pertama tiap kombinasi + max id monitor; keluaran diverifikasi 4 Sep 2026):
`python3 -c 'import sqlite3,json,collections;c=sqlite3.connect("file:data/ryo.db?mode=ro",uri=True);r=[(i,ts,json.loads(p)["data"]["evidence"]) for i,ts,p in c.execute("SELECT id,ts_utc,payload_json FROM snapshots WHERE tool=\"monitor_market_sentiment_shift\" ORDER BY id")];print("max monitor id",r[-1][0]);v=[(i,ts,(e["sentiment_regime"],e["liquidation"]["pressure_state"],e["funding"]["crowding_state"],e["fear_greed"]["change_direction"])) for i,ts,e in r];print(collections.Counter(x for _,_,x in v));print({x:min((i,ts) for i,ts,y in v if y==x) for _,_,x in v})'`
→ `max monitor id 2106` · `{('extreme_greed_unconfirmed','normal','normal','down'): 21, ('mixed_sentiment','normal','normal','down'): 18, ('complacent_crowding','extreme','long_crowding','flat'): 2, ('complacent_crowding','extreme','long_crowding','down'): 1}` (id pertama: 2 · 301 · 1976 · 2106). Baris `monitor` terakhir di DB saat sapuan = id 2106; `max id` seluruh tabel = 2165.
Konteks amatan id=1976 (DESKRIPTIF, n=3, BUKAN kontrak dan BUKAN ambang yang boleh dikodekan): F&G `value` 78 = `baseline_7d_value` 78,
`change_7d_points` **0** → `flat`; `funding.current_7d_percentile_90d` 91,11 (vs 56,67 pada sampel `normal`);
`liquidation.percentile_90d` 92,22, `total_usd` 159.942.427,03 (vs 29.810.068,74), `long_share_pct` 6,39.
**Arah/makna keempat nilai ini BELUM DITENTUKAN oleh fakta mana pun** — RYO tidak menerbitkan artinya, dan berkas ini tidak
menyimpulkannya. Pemetaan ke BULLISH/BEARISH/NEUTRAL/ABSTAIN/KNOWN/CALM/ALERT adalah keputusan quant + `docs/spec.md` §4
(item 4 memakai `change_direction`+`material_shift`; item 8 memakai `crowding_state`+`pressure_state`), bukan fakta API.
Yang dijamin baris ini hanya: string itu benar-benar keluar dari RYO, dengan ejaan itu, pada id/ts di atas.

**TIDAK ADA enum resmi RYO untuk keempat field itu — diperiksa 4 Sep 2026, bukan diasumsikan.**
`docs/samples/catalog.json` (katalog live `/tools`, sumber kebenaran final): entri `monitor_market_sentiment_shift` hanya punya
`inputSchema` dengan SATU properti (`time_window`, `enum: ["7d"]`) — **tidak ada skema output sama sekali**, jadi tidak ada tempat
untuk enum `sentiment_regime`/`pressure_state`/`crowding_state`/`change_direction`. `description`-nya menyebut konsepnya secara prosa
("BTC perpetual funding crowding, BTC liquidation pressure and side, altseason phase, the combined regime") tanpa satu pun nilai string.
Pencarian `complacent`, `long_crowding`, `pressure_state`, `sentiment_regime`, `crowding_state`, `change_direction` di seluruh
`docs/samples/catalog.json` → **0 kecocokan**; satu-satunya kata `enum` di berkas itu adalah `time_window`. RYO Builder MCP Guide
(teks lengkap ditempel user 1 Sep 2026) juga tidak memuat daftar nilai untuk field-field ini. Konsisten dengan §A: **tidak ada enum
yang diterbitkan**; satu-satunya sumber kosakata adalah amatan perekam kami.

**Dua klaim "nilai TUNGGAL" 2 Sep DICABUT (sapuan `id <= 2165`)** — keduanya ada di baris "Kosakata kategoris monitor lain" di atas:
`liquidation.dominant_liquidated_side` bukan `shorts` 20/20 melainkan **shorts 25 · longs 5 (pertama id=910, 2026-09-02T01:13:09Z) ·
balanced 12 (pertama id=1208, 2026-09-03T10:37:01Z)**; `altseason.phase` bukan `transition` 20/20 melainkan
**transition 41 · bitcoin_season 1 (id=910)**. Keduanya tidak punya tabel di `preflight/params.py` sehingga tidak membuat kanari merah,
tetapi angka lama di berkas ini salah dan tidak boleh dikutip lagi.

**Nilai `availability` KEEMPAT: `partial` — TERAMATI (sapuan `id <= 2165`).** Tabel "Tipe & kosakata envelope" di bawah mencatat
`available`/`unavailable`/`not_requested` saja; hitungan baru: `available` 6465 · `unavailable` 1736 · `not_requested` 33 · **`partial` 32**.
`partial` hanya pada kunci `technical_analysis`: `analyze_token` 16× (pertama id=1109, 2026-09-02T09:08:14Z) dan `deep_analysis` 16×
(pertama id=1110, 2026-09-02T09:08:58Z), keduanya pada envelope ber-`status: partial`. Envelope lain tetap: `status` ok 1326 · partial 839
(`unavailable` masih 0/2165), `data_mode` `live` 2165/2165, `schema_version` `"1.0"` 2165/2165.

**Baris kosakata dengan NOL amatan tidak boleh ada di `preflight/params.py`.** Aturan §A ini ("nilai di luar tabel → `unspoken`,
tidak ditebak") berlaku dua arah: memasukkan string yang belum pernah kami lihat ke dalam tabel adalah menebak bahwa RYO akan
mengeluarkan string itu, dengan ejaan itu — persis jenis karangan yang dilarang, hanya berpindah tempat. Dua alasan operasional,
bukan estetika: (1) bila ejaan sebenarnya berbeda (`risk-off`, `RISK_OFF`, `risk_off_confirmed`) barisnya tidak cocok dan sama
sekali tidak menolong; (2) di kedua tempat itu barisnya hanya bisa mengubah `unspoken` (yang MEMVETO, karena item 3 & 4 wajib)
menjadi IZIN — `risk_off` + plan short → hijau, `change_direction: up` + plan long → hijau. Menghapusnya gagal-tertutup
(fail closed) dan sama sekali tidak mengubah keputusan pada semua data yang pernah kami lihat. Menambahkannya kembali: lewat
`@agent-api-verifier` dengan hitungan + tanggal, setelah nilainya benar-benar muncul.

**Aturan itu TETAP BERLAKU dan terbukti dua arah dalam satu jam (2 Sep 2026).** Ia mencabut `FEAR_GREED_CHANGE_DIRECTION["up"]`
(0 amatan, dan spec §4 item 4 memang tidak pernah menulis string itu — provenance = tebakan kami) **dan** ia yang memaksa
`MARKET_REGIME["risk_off"]` kembali masuk **dengan provenance amatan** (id 763), bukan dengan provenance spec. Kriterianya bukan
"ada di spec" dan bukan "pernah dihapus", melainkan satu-satunya yang bisa diperiksa: **hitungan amatan > 0 di DB, dengan `id`,
`ts`, dan `status` yang bisa dibuka lagi.**

**Sapuan kosakata kedaluwarsa dalam HITUNGAN JAM, bukan hari — perlakukan seperti data basi, bukan seperti fakta.**
Setiap klaim "0 amatan" WAJIB menyebut `id` maksimum yang disapu (mis. "0 dari `id <= 762`"); tanpa itu klaimnya tidak bisa
diperiksa dan tidak boleh dipakai untuk menghapus baris kosakata. Bukti: klaim "`risk_off` 0 dari 762" benar saat ditulis dan
salah pada baris **763** — kemunculan pertama ada tepat satu baris sesudah batas sapuan, dan sebuah sapuan lain basi ~25 baris
dalam ~19 menit. Konsekuensi praktis: (1) klaim nol punya masa berlaku, sapu ulang sebelum commit yang bersandar padanya;
(2) yang menangkap nilai baru secara otomatis adalah kanari kosakata di `tests/test_params.py` yang membaca DB hidup `mode=ro` —
itu mekanisme deteksi, jangan dilemahkan; (3) hitungan frekuensi di komentar kode akan selalu tertinggal — yang harus benar
adalah HIMPUNAN nilainya, bukan angkanya.

**ENUM DI ATAS TETAP TIDAK LENGKAP** dan tidak bisa dibuktikan lengkap dari sini — dibuktikan lagi 4 Sep 2026 oleh EMPAT nilai baru
sekaligus di `monitor_market_sentiment_shift` (id 1976). Ini pengamatan atas 2165 respons (`id <= 2165`) dalam ~81,9 jam
pada satu rezim pasar (F&G greed/extreme_greed) dengan universe simbol perekam kami — **bukan spesifikasi RYO**; Guide maupun
katalog `/tools` tidak pernah mencantumkan enum untuk `verdict`, `verdict.call`, `confluence.state`, atau `regime`.
Bukti historis bahwa daftar ini bocor: dalam ~14,5 jam muncul TIGA nilai yang sebelumnya "belum pernah terlihat"
(`avoid` id 349 · `regime: neutral` id 135 · `regime: risk_off` id 763) — yang ketiga muncul TEPAT satu baris setelah sebuah
sapuan menyimpulkan nol untuknya.
Konsekuensi wajib (spec §4, ADR-013): nilai di luar tabel → item **`unspoken` + warning**; TIDAK ditebak, TIDAK dipetakan ke
tetangga terdekat, TIDAK dijadikan 0/netral, dan tidak boleh melempar exception. `preflight/params.py` harus menutup cabang
default-nya seperti itu, dan menambah nilai baru ke tabel ini hanya lewat `@agent-api-verifier` dengan hitungan + tanggal.
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

#### Argumen yang DIKIRIM ≠ `request` yang DIKEMBALIKAN — DIVERIFIKASI 3 Sep 2026 (TASKS 2.2)
Server MENGISI default ke `request`, jadi `request` **bukan** kunci yang dipakai mencari baris store
(`snapshots.args_json` = argumen yang benar-benar DIKIRIM). Terukur atas `data/ryo.db` dibuka read-only
(`sqlite3.connect("file:data/ryo.db?mode=ro", uri=True)`), **`id <= 1206`**, ts ≤ 2026-09-02T14:44:12Z:
- `monitor_market_sentiment_shift` dikirim `{}` → `request: {"time_window":"7d"}` di **26/26** baris.
- `market_overview` dikirim `{}` → `request: {}` di **26/26** baris → tidak semua tool punya default yang diisi.
- (instans ketiga sudah tercatat di baris `request` tabel envelope: `scan_market` tanpa argumen → `{"top_n":10}`.)
Ulangi: `python3 -c 'import sqlite3,json,collections;c=sqlite3.connect("file:data/ryo.db?mode=ro",uri=True);k=collections.Counter((t,a,json.dumps(json.loads(p).get("request"))) for t,a,p in c.execute("SELECT tool,args_json,payload_json FROM snapshots"));print([x for x in k.items() if x[0][0] in ("market_overview","monitor_market_sentiment_shift")])'`
**Konsekuensi:** pemanggil store-first WAJIB memakai `{}` untuk `market_overview` dan `monitor_market_sentiment_shift`
(bentuk yang ditulis `core/recorder.py`), BUKAN `request` dari `docs/samples/*.json`. Menyamakan args dengan
`docs/samples/<tool>.json["request"]` benar untuk `analyze_token`/`deep_analysis`/`compare_tokens` SAJA.

#### `compare_tokens.symbols` — batas & simbol berulang — DIVERIFIKASI 3 Sep 2026 (TASKS 2.2), DIPERBARUI 3 Sep 2026 (TASKS 2.0c)
`docs/samples/catalog.json`: `symbols` bertipe **string** dengan deskripsi verbatim "2-4 token symbols to compare, comma- or
space-separated, e.g. 'SOL, AVAX'." — **tidak ada `minItems`/`maxItems`/`pattern`**; batas 2–4 hanya ada di teks deskripsi dan
tidak ditegakkan skema. `required: ["symbols"]`; `intent` enum `swing|hold|spot`, opsional.
**Simbol berulang: masih 0 amatan pada `id <= 1278`** — 18/18 baris `compare_tokens` di `data/ryo.db` dan 1/1
`docs/samples/compare_tokens.json` memakai simbol yang berbeda semua. Perilaku RYO terhadap `"BNB, BNB, ETH"`
**BELUM DIVERIFIKASI**: bentuk itu tidak pernah dikirim, jadi jangan klaim ia ditolak MAUPUN diterima.
(`core/recorder.compare_args()` melakukan dedupe berurutan justru supaya bentuk itu tidak pernah terkirim.)

**Daftar 2 simbol DITERIMA — DIVERIFIKASI 3 Sep 2026 (id 1262–1263).** Sampai 3 Sep 2026 daftar 2 simbol punya **0 amatan**
dan status berkas ini adalah "di dalam rentang 2-4 yang didokumentasikan, tetapi belum pernah dikirim". Sejak siklus perekam
2026-09-03T10:50:49Z–10:55:07Z (≈ 17:50–17:55 WIB) bentuk itu ADA dan RYO membalas `status: "ok"`:
`{"intent":"swing","symbols":"ETH, BNB"}` (id 1262) dan `{"intent":"swing","symbols":"BNB, ETH"}` (id 1263).
**Bentuk respons 2 simbol IDENTIK dengan 3 simbol** — tidak ada kunci yang hilang maupun tambahan:
top-level 10 kunci sama (`schema_version, tool, status, data_mode, as_of, request, data, summary, availability, warnings`);
`data` = `{conclusion, coverage_by_token, factors, intent, tokens, winner}` sama persis;
`availability` sama (`token_market_data/comparison_factors/conclusion = available`, `token_profiles = unavailable`).
Yang berskala dengan cacah simbol hanya ISI, bukan bentuk: `coverage_by_token` = dict ber-kunci simbol dengan **2 entri**
(vs 3 pada daftar 3 simbol), `data.tokens` 2 entri, dan tiap `factors[].scores` 2 kunci. **`factors` tetap 3 entri**
(`momentum`, `market_activity`, `volatility`) dan `winner`/`conclusion.pick` tetap terisi. Konsekuensi untuk item 10:
tidak ada cabang khusus yang perlu ditulis untuk daftar 2 simbol.
Ulangi (mencetak kunci top-level, kunci `data`, dan cacah `coverage_by_token` per baris + max id yang disapu):
`python3 -c 'import sqlite3,json;c=sqlite3.connect("file:data/ryo.db?mode=ro",uri=True);print("max id",c.execute("SELECT max(id) FROM snapshots").fetchone()[0]);[print(i,a,sorted(json.loads(p)),sorted(json.loads(p)["data"]),len(json.loads(p)["data"]["coverage_by_token"])) for i,a,p in c.execute("SELECT id,args_json,payload_json FROM snapshots WHERE tool=? ORDER BY id",("compare_tokens",))]'`

String `symbols` yang benar-benar terekam, disapu pada `id <= 1278` (18 baris, semuanya `intent: "swing"`, semuanya `status: ok`):
* **sesi manual 1 Sep 2026** (14 baris, id 41–211, ts 2026-09-01T04:06:09Z–06:33:21Z, pembanding BTC+ETH):
  `SOL, BTC, ETH` (4) · `BTC, ETH, SOL` (4) · `ETH, BTC, SOL` (3) · `BNB, BTC, ETH` (3);
* **perekam, sejak TASKS 2.0c** (4 baris, id 1260–1263, ts 2026-09-03T10:50:49Z–10:55:07Z, pembanding BNB+ETH):
  `SOL, BNB, ETH` · `BTC, BNB, ETH` · `ETH, BNB` · `BNB, ETH` (masing-masing 1).

**Sampai 3 Sep 2026** `"SOL, BNB, ETH"` HANYA ada di `docs/samples/compare_tokens.json` (**0 baris di DB**) dan `core/recorder.py`
**tidak memanggil `compare_tokens` sama sekali**, sehingga berkas ini menyimpulkan tool itu "praktis selalu `unspoken` pada DB hidup".
**Sejak siklus 2026-09-03T10:50:49Z (≈ 17:50 WIB) keadaan itu berubah** — TASKS 2.0c menambah langkah `compare_tokens` ke
`core/recorder.py` (`MAX_COMPARES_PER_CYCLE = 4`, watchlist saja), jadi `"SOL, BNB, ETH"` kini ADA di DB (id 1260) dan baris
`compare_tokens` baru bertambah ≤4 per siklus. Simpulan lama tentang `unspoken` **tidak lagi berlaku untuk DB hidup**; ia tetap
berlaku untuk DB apa pun yang berhenti pada `id <= 1259`. `_record(` di `core/recorder.py` dicek ulang 3 Sep 2026 — kini enam
tool: scan_market, market_overview, monitor_market_sentiment_shift, analyze_token, deep_analysis, **compare_tokens**.

#### Harga — `analyze_token.data.market.price_usd` — DIVERIFIKASI 4 Sep 2026 (TASKS 2.4)
Field ADA dan bertipe `float`: `docs/samples/analyze_token.json` → `["result"]["data"]["market"]["price_usd"]` = `103.89572922101242`
(`market` = `{price_usd, market_cap_usd, fully_diluted_value_usd, volume_24h_usd}`, `availability.market_data: "available"`),
sesuai baris `analyze_token` tabel "Field nyata" di atas.

**Cakupan pada DB hidup, `id <= 1727`** (ts ≤ 2026-09-03T20:32:21Z; `sqlite3.connect("file:data/ryo.db?mode=ro", uri=True)`):
* `analyze_token`: **929/929** baris punya `data.market.price_usd` numerik **> 0** — 0 null, 0 hilang, 0 non-numerik;
  `availability.market_data` = `available` di **929/929**; himpunan kunci `data.market` identik di 929/929.
  9 baris `status: partial` TIDAK menyentuh harga: yang `partial` adalah `technical_analysis` (semuanya simbol `DGAI`),
  `market_data` tetap `available` dan `price_usd` terisi.
* `deep_analysis.data.market.price_usd`: **653/653** numerik > 0, `market_data` `available` 653/653.
* `compare_tokens.data.tokens[].price_usd` 124/124 dan `scan_market.data.candidates[].price_usd` 536/536 juga non-null —
  **null harga belum pernah teramati di keempat path**, jadi "harga null" bukan mode kegagalan yang pernah kami lihat.
Ulangi: `python3 -c 'import sqlite3,json,collections;c=sqlite3.connect("file:data/ryo.db?mode=ro",uri=True);r=list(c.execute("SELECT id,tool,payload_json FROM snapshots ORDER BY id"));print("max id",r[-1][0]);k=collections.Counter();[k.update([(t,isinstance((json.loads(p).get("data") or {}).get("market",{}).get("price_usd"),(int,float)))]) for i,t,p in r if t in ("analyze_token","deep_analysis")];print(k)'`

**Yang benar-benar membuat harga tidak ada BUKAN field null, melainkan KESEGARAN.** `agent/trade.py:latest_price` memakai
`Store.latest_fresh(..., max_age_min=120)`; harga "tidak ada" bila perekam tidak menulis baris `analyze_token` untuk simbol itu
dalam 120 menit terakhir.

##### Sapuan A — 4 Sep 2026 (TASKS 0.8), `id 3..1723`, jendela `2026-09-01T03:51:43Z..2026-09-03T20:32:21Z` (64,7 j)
Angka ini TETAP BERLAKU untuk jendela itu dan **tidak dihapus**; ia dipakai sebagai KONTROL metodologi di sapuan 5 Sep.
| simbol | n baris analyze_token | jarak antar-baris median | jarak maksimum | jarak > 120 mnt | % waktu ada harga ≤ 120 mnt |
|---|---|---|---|---|---|
| SOL | 36 | 62 mnt | **1194 mnt (19,9 j)** | 7 | 57,5 % |
| BTC | 36 | 63 mnt | 1193 mnt | 7 | 57,5 % |
| ETH | 33 | 63 mnt | **1455 mnt (24,3 j)** | 6 | 54,5 % |
| BNB | 31 | 63 mnt | 1455 mnt | 5 | 51,8 % |
Artinya: pada DB ini, sebuah posisi terbuka menghadapi harga "tidak diketahui" kira-kira **42–48 % waktu**, dan pernah menghadapinya
**24 jam berturut-turut** — sama panjang dengan `DEFAULT_TIMEOUT_MIN` agen, sehingga `timeout` pun (yang wajib punya harga) bisa
gagal tercatat tepat saat ia jatuh tempo. Penyebabnya perekam kami (proses berhenti/macet, mis. macet ~3 jam 3 Sep 2026), BUKAN RYO.
Ulangi: `python3 -c 'import sqlite3,json,collections,datetime;c=sqlite3.connect("file:data/ryo.db?mode=ro",uri=True);r=list(c.execute("SELECT id,args_json,ts_utc FROM snapshots WHERE tool=\'analyze_token\' ORDER BY id"));print("max id",r[-1][0]);d=collections.defaultdict(list);[d[json.loads(a)["symbol"]].append(datetime.datetime.fromisoformat(t)) for _,a,t in r];print({s:(len(v),round(max((v[k+1]-v[k]).total_seconds()/60 for k in range(len(v)-1)))) for s,v in d.items() if s in ("SOL","BTC","ETH","BNB")})'`

##### Sapuan B — 5 Sep 2026 (TASKS 0.8b), DB 3100 baris, baris terbaru `2026-09-05T11:38:55.512961+00:00`
Diverifikasi ulang oleh `api-verifier` 5 Sep 2026 dari `data/ryo.db` yang dibuka **read-only**
(`sqlite3.connect("file:"+quote(path)+"?mode=ro", uri=True)` + `PRAGMA query_only=ON`; nol byte ditulis).
Definisi `% segar` sama persis dengan sapuan 4 Sep: Σ min(jarak, 120 mnt) + min(sisa dari baris terakhir ke ujung jendela, 120 mnt),
dibagi panjang jendela (= gabungan interval `[t, t+120 mnt]` dipotong jendela).

**KONTROL — replikasi jendela 4 Sep** (`2026-09-01T03:51:43Z..2026-09-03T20:32:21Z`, id 3..1723):
SOL 36 / 62 mnt / 1194 mnt / 7 / 57,5 % · BTC 36/63/1193/7/57,5 % · ETH 33/63/1455/6/54,5 % · BNB 31/63/1455/5/51,8 %
— **identik dengan tabel Sapuan A di atas**, jadi metodologi kedua sapuan terbukti sama.

**B1 — jendela "pasca-pendaratan commit"** (commit `e06256e` `2026-09-04T11:38:57Z` = 18:38:57 WIB)
`..2026-09-05T11:13:22.083896Z`, **23,57 j**, id `analyze_token` inti 2049..3060, n total 70.
Tepi terpotong: awal 53–56 mnt, akhir 0–26 mnt.

> **ANGKA 77 % DI BAWAH INI TIDAK BOLEH DIATRIBUSIKAN KE 0.8a.** Anchor "sejak commit mendarat" **keliru**:
> pendaratan commit ≠ kode itu berjalan. Perekam yang sedang hidup saat commit mendarat sudah mulai 03:27 WIB dan
> **tidak pernah di-restart** sampai mati; ia menjalankan kode LAMA sepanjang sisa hidupnya. Anchor yang benar adalah
> **baris `perekam mulai` terakhir** (→ B3). Jendela B1 adalah **campuran tiga rezim**, terverifikasi dari `logs/recorder.log`:
> | rezim | dari → sampai (UTC) | durasi | porsi B1 |
> |---|---|---|---|
> | kode **LAMA** (proses mulai 03:27 WIB, tak pernah restart) | `11:38:57Z` → `2026-09-04T19:22:48Z` | **7,73 j** (7:43:51) | 32,8 % |
> | perekam **MATI** (senyap, tak ada proses) | → `2026-09-05T01:44:59Z` | **6,37 j** (6:22:11) | 27,0 % |
> | kode **0.8a** (`perekam mulai` 08:44:59 WIB) | → `2026-09-05T11:13:22Z` | **9,47 j** (9:28:23) | 40,2 % |
> | total | | **23,57 j** | 100 % |
> Jadi 77,4 % adalah rata-rata tertimbang atas 7,7 j kode lama + 6,4 j mati total + 9,5 j kode baru — **bukan** ukuran 0.8a.
| simbol | n | jarak median | jarak maks | jarak > 120 mnt | % segar |
|---|---|---|---|---|---|
| SOL | 18 | 60 mnt | 387 mnt (6,4 j) | 1 | 77,4 % |
| BTC | 18 | 60 mnt | 387 mnt (6,4 j) | 1 | 77,4 % |
| ETH | 17 | 60 mnt | 386 mnt (6,4 j) | 1 | 77,2 % |
| BNB | 17 | 60 mnt | 387 mnt (6,4 j) | 1 | 77,2 % |

**B2 — 64,70 j sampai 5 Sep 2026 11:13Z** (`2026-09-02T18:31:22Z..2026-09-05T11:13:22.083896Z`, id 1210..3060),
panjang jendela sama dengan Sapuan A tetapi **letaknya bergeser** — lihat peringatan artefak di bawah:
| simbol | n | jarak median | jarak maks | jarak > 120 mnt | % segar |
|---|---|---|---|---|---|
| SOL | 32 | 60 mnt | 714 mnt (11,9 j) | 3 | 51,0 % |
| BTC | 32 | 60 mnt | 714 mnt | 3 | 51,0 % |
| ETH | 30 | 61 mnt | 714 mnt | 3 | 50,9 % |
| BNB | 30 | 60 mnt | 714 mnt | 3 | 50,9 % |

**B3 — sejak baris `perekam mulai` terakhir, yakni perekam berkode 0.8a** (`2026-09-05T01:44:59Z` = 08:44:59 WIB
`..2026-09-05T11:13:22.083896Z`, **hanya 9,47 j — BUKAN 24 j**):
SOL 11/60/**60 mnt**/**0**/100,0 % · BTC 11/60/60/**0**/99,9 % · ETH 10/60/60/**0**/99,6 % · BNB 10/60/60/**0**/99,5 %.

**PERINGATAN ARTEFAK JENDELA (jangan kutip "max gap turun" tanpa kalimat ini).**
Jarak maksimum dibatasi oleh panjang jendela: sebuah jendela sepanjang **<24,3 j secara struktural TIDAK BISA memunculkan
jarak maksimum 1455 mnt (24,3 j)**. Jendela B1 (23,57 j = 1414 mnt) dan B3 (9,47 j = 568 mnt) keduanya lebih pendek dari 1455 mnt,
jadi sebagian penurunan "max gap 1455 → 387 → 60 mnt" adalah **artefak jendela, bukan hanya perbaikan**.

**Max gap dan jumlah celah B2 JUGA artefak — artefak LETAK tepi, bukan panjang jendela.** Panjang B2 memang sama dengan
Sapuan A (64,7 j), tetapi letaknya bergeser, dan dua celah terbesar Sapuan A **hilang dari daftar celah B2 semata-mata karena
keduanya MULAI sebelum tepi awal `2026-09-02T18:31:22Z`**: celah 1194 mnt SOL `2026-09-02T14:43:28Z→2026-09-03T10:37:03Z`
dan celah 1455 mnt ETH `2026-09-02T10:24:21Z→2026-09-03T10:39:22Z`. Sebuah celah yang menembus tepi awal tidak terhitung
sebagai "celah" sama sekali — ia jadi **waktu mati di tepi**. Konsekuensinya, angka yang jujur:
**rentang tanpa-data TERPANJANG yang benar-benar dialami di dalam jendela B2 adalah ≥966 mnt (16,1 j) di tepi awal, BUKAN 714 mnt**
(tepi awal 966–969 mnt untuk keempat simbol). Hal yang sama menjelaskan 7→3 celah: 6 dari 7 celah SOL di Sapuan A mulai sebelum
`18:31:22Z`, jadi jatuh di luar B2. **Jangan kutip "max gap 1455 → 714 mnt" atau "7 → 3 celah" sebagai perbaikan.**

Bukti bahwa itu artefak, bukan perbaikan: **% segar B2 (50,9–51,0 %) justru LEBIH RENDAH daripada Sapuan A (51,8–57,5 %)**
meski max gap dan jumlah celah "turun". Untuk BNB selisihnya hanya **51,8 % → 50,9 % = 0,9 pp**, di dalam sensitivitas letak tepi
yang diperingatkan paragraf ini sendiri; untuk SOL/BTC 57,5 % → 51,0 % = 6,5 pp. (Rentang Sapuan A dibaca langsung dari tabelnya
di atas: BNB 51,8 % terendah, SOL/BTC 57,5 % tertinggi.)

Kesimpulan jujur: **satu-satunya angka yang tidak dipengaruhi letak maupun panjang tepi adalah B3** — 9,47 j sejak baris
`perekam mulai` terakhir, tanpa satu pun celah > 120 mnt, dengan setiap celah terhitung penuh di dalam jendela. Dan ia
menutupi 9,47 j saja. B1 dan B2 tidak boleh dipakai untuk mengklaim perbaikan.

**Atribusi 3 celah > 120 mnt di jendela B2** (angka SOL, mewakili keempat simbol; log `logs/recorder.log` zona lokal WIB = UTC+7):
1. **196 mnt** `2026-09-03T17:10:52Z → 20:26:43Z` (id 1647→1711).
2. **714 mnt (11,9 j)** `2026-09-03T23:27:26Z → 2026-09-04T11:21:26Z` (id 1912→1978).
   **TEROBSERVASI** (pola log, bukan catatan sistem): baris terakhir sebelum senyap
   `2026-09-04 06:45:31,655 core.recorder siklus selesai: 66 snapshot ... durasi=1086s`, baris pertama sesudahnya
   `2026-09-04 18:21:23,097 ... market_overview ... 200 OK` — **11 j 35 mnt 51 dtk tanpa satu baris log**; dan
   **tidak ada baris `perekam mulai`** di antara `2026-09-04 03:27:27,731` dan `2026-09-05 08:44:59,297`, jadi proses yang sama
   menembus celah itu: ia tidak mati dan tidak di-restart.
   **INFERENSI TERBAIK, BELUM DIVERIFIKASI:** host tersuspend. Kami **tidak** punya catatan sistem (dmesg/journal/wtmp) yang
   melihatnya. Pola log yang sama konsisten dengan **request menggantung tanpa timeout**, **`SIGSTOP`**, atau **oversleep
   penjadwal**. Jangan kutip "suspend" sebagai fakta terobservasi.
   Yang **pasti** dan cukup untuk keputusan: proses itu mulai 03:27 WIB dan tidak pernah di-restart, sedangkan perbaikan 0.8a
   baru mendarat 18:38:57 WIB → **celah ini terjadi di bawah kode LAMA**, bukan kode 0.8a.
3. **387 mnt (6,4 j)** `2026-09-04T19:18:29Z → 2026-09-05T01:45:04Z` (id 2419→2435).
   **TEROBSERVASI:** baris log terakhir `2026-09-05 02:22:48,376 ... analyze_token ... 200 OK` **tanpa `siklus selesai`
   sesudahnya** — **senyap 6 j 22 mnt 11 dtk** — lalu `2026-09-05 08:44:59,297 core.recorder perekam mulai: ...`,
   yaitu **proses BARU** (baris `perekam mulai` = start baru), dihidupkan manual.
   **INFERENSI TERBAIK, BELUM DIVERIFIKASI:** proses lama mati di tengah siklus. Tidak ada traceback, exit code, atau catatan
   OOM/sinyal yang kami lihat; yang terobservasi hanya senyap-lalu-proses-baru.
   Terlepas dari sebabnya, remedinya sama dan bukan di `core/`: **supervisi proses + auto-restart (0.8c, manusia)**.

**Baris fixture `id 1597–1643` (TASKS 0.19) ikut terhitung di kedua sapuan.** Rentang itu memuat 4 baris simbol inti
(1600 SOL, 1602 BTC, 1604 ETH, 1606 BNB — semuanya `2026-09-03T16:31:16Z`) dan jendela Sapuan A pun memuatnya.
Mengecualikan `id 1597..1643` mengubah n B2 dari 32/32/30/30 → 31/31/29/29 dan n Sapuan A dari 36/36/33/31 → 35/35/32/30,
tetapi **% segar tidak bergeser sama sekali** (B2 51,0/51,0/50,9/50,9 %; Sapuan A 57,5/57,5/54,5/51,8 %) dan max gap juga tidak;
yang bergeser hanya median (+1–2 mnt). Jadi fixture tidak mengarang kesegaran.

Ketiga jendela **tertutup** (ujung tetap `2026-09-05T11:13:22.083896Z`), jadi tabel di atas **tetap reproducible walau DB terus tumbuh**:
perekam hidup menambah baris ber-`ts_utc` di atas ujung itu, di luar semua jendela. Diuji: DB tumbuh 3100 → 3134 baris
saat verifikasi berlangsung dan keluaran resep di bawah tidak berubah sedikit pun.

**Ujung jendela WAJIB ditulis lengkap dengan mikrodetik `...11:13:22.083896`.** id 3060 (BTC) ber-`ts_utc`
`2026-09-05T11:13:22.083896+00:00`; memotongnya jadi `11:13:22` membuang baris itu dan resep ini **tidak** mereproduksi
tabel di atas — BTC menjadi n=17/31/10 (bukan 18/32/11) dengan id terakhir 2995 (bukan 3060) di B1/B2/B3.

Ulangi (ganti jendela sesuai B1/B2/B3):
```
python3 -c 'import sqlite3,json,statistics,datetime as D
from urllib.parse import quote
c=sqlite3.connect("file:"+quote("data/ryo.db",safe="/")+"?mode=ro",uri=True);c.execute("PRAGMA query_only=ON")
W=(D.datetime.fromisoformat("2026-09-04T11:38:57+00:00"),D.datetime.fromisoformat("2026-09-05T11:13:22.083896+00:00"))
d={}
for i,a,t in c.execute("SELECT id,args_json,ts_utc FROM snapshots WHERE tool=\"analyze_token\" ORDER BY id"):
 s=json.loads(a).get("symbol");t=D.datetime.fromisoformat(t)
 if s in ("SOL","BTC","ETH","BNB") and W[0]<=t<=W[1]: d.setdefault(s,[]).append((i,t))
for s in ("SOL","BTC","ETH","BNB"):
 v=[t for _,t in d[s]];g=[(v[k+1]-v[k]).total_seconds()/60 for k in range(len(v)-1)]
 cov=(sum(min(x,120) for x in g)+min((W[1]-v[-1]).total_seconds()/60,120))/((W[1]-W[0]).total_seconds()/60)*100
 print(s,d[s][0][0],d[s][-1][0],len(v),round(statistics.median(g)),round(max(g)),sum(1 for x in g if x>120),"%.1f"%cov)'
```
Kolom DB yang dipakai hanya `snapshots.id/tool/args_json/ts_utc` dan `args.symbol` — **tidak ada field, endpoint, atau paket baru**
(skema nyata: `id, tool, args_json, args_hash, ts_utc, as_of, status, payload_json, payload_hash`).
`max_age_min=120` di `agent/trade.py`/`preflight` **tidak diubah** oleh sapuan ini.

**Tidak ada field harga yang lebih baik di envelope** (dicek 4 Sep 2026, `id <= 1727`):
`deep_analysis.data.market.price_usd` sama-sama 100 % terisi tetapi barisnya LEBIH SEDIKIT (653 vs 929) dan ditulis pada siklus
yang sama, jadi tidak pernah lebih segar; pada 648 pasang analyze/deep dalam siklus yang sama selisih harganya **median 0,0000 %,
p90 0,0000 %, maksimum 0,6057 %** (OP, id 380/381, jeda 492 dtk) → keduanya harga yang sama. `scan_market.data.candidates[].symbol`
**tidak pernah** memuat SOL/BTC/ETH/BNB (0 dari 536 kandidat, 63 simbol berbeda) → tidak bisa dipakai untuk watchlist inti.
`compare_tokens.data.tokens[].price_usd` hanya ada bila simbolnya kebetulan dibandingkan (ETH 46 · BNB 35 · SOL 22 · BTC 21 entri).
**Simpulan: `analyze_token.data.market.price_usd` adalah pilihan yang benar; yang perlu diperbaiki adalah uptime perekam, bukan pilihan field.**

### Tipe & kosakata envelope (10 field) — DIVERIFIKASI 1 Sep 2026 (TASKS 1.3, verifikasi `core/skill.py` = cermin envelope, ADR-013/015)
Sumber: keenam `docs/samples/*.json` (`response["result"]`) + **470 envelope perekam** di `data/ryo.db` (dibuka read-only,
`sqlite3.connect("file:data/ryo.db?mode=ro", uri=True)`, hitung 1 Sep 2026; DB tidak di-commit, ulangi dengan `make record`).
Semua 470 payload memakai **himpunan 10 kunci yang sama** — tidak ada satu pun envelope dengan kunci lebih/kurang.
| Field | Tipe yang benar-benar muncul | Nilai teramati (6 sampel + 470 snapshot) |
|---|---|---|
| `schema_version` | `str` | `"1.0"` di **6/6 sampel DAN 470/470 snapshot** — bukan hanya `analyze_token`; tidak pernah `"1"` |
| `tool` | `str` | nama tool, sama dengan `tool` di level respons REST |
| `status` | `str` | **hanya `ok` & `partial`** (snapshot: ok 278/partial 192; sampel: ok 5/partial 1 = `deep_analysis`); `unavailable` **BELUM PERNAH TERAMATI** (0/476 — **angka itu BASI; sapuan 6 Sep 2026 `id <= 3645`: ok 2229 · partial 1416 · `unavailable` masih 0, jadi 0/3645. Kutip 0/3645, bukan 0/476**). Enum bertiga berasal dari RYO Builder MCP Guide, bukan dari sampel |
| `data_mode` | `str` | **hanya `live` (476/476 → sapuan 6 Sep 2026: 3645/3645)**; `mixed`, `simulated`, `unknown` **BELUM PERNAH TERAMATI** (0/3645). Enum berempat berasal dari Guide |
| `as_of` | `str`, **tidak pernah `null`** (0/476 → 0/3645, sapuan 6 Sep 2026) | selalu presisi DETIK: `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$` — tidak pernah ada mikrodetik |
| `request` | `dict` | argumen ternormalisasi; nilai bertipe sesuai inputSchema (`symbol:str`, `include_perp:bool`, `top_n:int`, `intent:str`, `symbols:str`, `time_window:str`) — `scan_market` mengembalikan `{"top_n":10}` meski dipanggil tanpa argumen → server MENGISI default ke `request` |
| `data` | `dict`, **tidak pernah `null`** (0/476 → 0/3645, sapuan 6 Sep 2026) | kunci per tool: lihat tabel "Field nyata" |
| `summary` | `dict` dengan **persis** `{headline, key_points}` di 476/476 (→ **3645/3645**, sapuan 6 Sep 2026) | `headline: str`, `key_points: list[str]` (tidak pernah list kosong-bertipe-lain) |
| `availability` | `dict[str, str]` | kosakata NILAI teramati: `available` (1430) · `unavailable` (398) · `not_requested` (5, `scan_market.theme_filter`). **DIPERBARUI sapuan `id <= 2165` (4 Sep 2026): ada nilai KEEMPAT `partial` (32×, hanya kunci `technical_analysis`, pertama id=1109); hitungan baru 6465 · 1736 · 33 · 32; **sapuan 6 Sep 2026 `id <= 3645`: 10892 · 2976 · 57 · 41** — himpunan nilai tetap berempat.** Kunci = nama SEKSI tool, bukan id item |
| `warnings` | `list[str]` | 0–2 entri di sampel; **DB `id <= 3645` mencapai 4 entri**: panjang teramati 0 (2063) · 1 (21) · 2 (1541) · **4 (20)**; ke-20 baris ber-4-warning semuanya `deep_analysis` simbol `DGAI` (baris yang sama dengan `availability.trade_plan != available`). Semua elemen `str`. Pembaca yang mengasumsikan ≤2 akan memotong bukti |

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

### 47 baris FIXTURE di `data/ryo.db` (`id 1597-1643`) — DIVERIFIKASI 5 Sep 2026 (TASKS 2.2d dicabut, lihat 0.19)
**Tidak ada bukti RYO mengabaikan argumen `symbols`, dan tidak ada bukti RYO melayani cache lintas argumen.** Premis TASKS 2.2d
("BTC tidak akan pernah hijau di item 10", bukti `id=1629`) SALAH dan dicabut 5 Sep 2026. Berkas ini tidak pernah memuat klaim
"RYO mengabaikan symbols" maupun klaim cache — diperiksa dengan `grep -niE "cache|mengabaikan|ignore|1629|stale" docs/api-facts.md`
(nol hasil pada 5 Sep 2026), jadi tidak ada yang perlu dicabut di sini; yang perlu dicabut ada di `TASKS.md` (bukan berkas saya).

Fakta yang diverifikasi ulang secara independen (sapuan **`max(id) = 2431`**, `mode=ro`, tanpa satu pun penulisan):
1. **47/47** baris `id 1597-1643` (rentang kontinu; 47 baris di rentang, 47 cocok) **byte-identik** dengan
   `docs/samples/<tool>.json` di bawah `json.dumps(json.loads(payload_json), sort_keys=True, ensure_ascii=False)`
   (envelope sampel = `d["result"]` bila ada, selain itu `d`). Tidak ada baris cocok DI LUAR rentang itu.
2. Ditulis dalam **0,692 detik**: `ts_utc` `2026-09-03T16:31:16.581640+00:00` s.d. `2026-09-03T16:31:17.273380+00:00`.
3. `request.symbols` pada id 1629/1630/1631 (dan 1628) berbunyi `'SOL, BNB, ETH'` untuk SETIAP `args_json`, karena itulah isi
   `docs/samples/compare_tokens.json`. Ini artefak berkas sampel, BUKAN perilaku server.
4. Sebaran ke-47: `analyze_token` 26 · `deep_analysis` 14 · `compare_tokens` 4 (id 1628–1631) · `market_overview` 1 ·
   `monitor_market_sentiment_shift` 1 (**id 1598**) · `scan_market` 1.
5. Dari **23** baris `compare_tokens` ber-args diawali `BTC`, hanya **satu** yang tidak memuat BTC di `data.tokens` — id=1629.
   Menyeluruh: **89 dari 90** baris `compare_tokens` memuat SEMUA simbol yang diminta; satu-satunya pengecualian adalah id=1629.
6. **Nol** dari 40 baris `decisions` membekukan satu pun dari ke-47 baris itu.

Perintah reproduksi (read-only; ganti `<tool>` otomatis dari kolom `tool`):
```
python3 -c 'import sqlite3,json,glob,os,collections;c=sqlite3.connect("file:data/ryo.db?mode=ro",uri=True);\
S={os.path.basename(f)[:-5]:json.dumps((lambda d: d["result"] if isinstance(d,dict) and "result" in d else d)(json.load(open(f))),sort_keys=True,ensure_ascii=False) for f in glob.glob("docs/samples/*.json")};\
m=[(i,t) for i,t,p in c.execute("select id,tool,payload_json from snapshots order by id") if t in S and json.dumps(json.loads(p),sort_keys=True,ensure_ascii=False)==S[t]];\
print("max id",c.execute("select max(id) from snapshots").fetchone()[0],"cocok",len(m),min(x[0] for x in m),max(x[0] for x in m),collections.Counter(t for _,t in m))'
```

**Konsekuensi yang wajib diingat: SETIAP hitungan amatan yang menyapu `id < 1644` memuat ke-47 baris fixture ini** — termasuk
angka provenance yang ditempel ke `preflight/params.py` pada TASKS 0.12 (`SWEEP = "sapuan id <= 2165"`). Hanya SATU baris
fixture (id=1598) yang bertool `monitor_market_sentiment_shift`, sehingga setiap hitungan `monitor…` di params.py
**terlalu besar tepat 1** untuk nilai yang muncul di id=1598. Rekapitulasi (sapuan `id <= 2165`, 42 baris `monitor…`,
1 di antaranya fixture):

| Tabel params.py | Nilai | Tertulis (termasuk fixture) | Tanpa 47 baris fixture | Selisih |
|---|---|---|---|---|
| `FUNDING_CROWDING_STATE` | `normal` | 39× | **38×** | −1 (id 1598) |
| `FUNDING_CROWDING_STATE` | `long_crowding` | 3× ("sebelumnya 0 dari 39") | **3×** ("0 dari **38**") | 0 |
| `LIQUIDATION_PRESSURE_STATE` | `normal` | 39× | **38×** | −1 (id 1598) |
| `LIQUIDATION_PRESSURE_STATE` | `extreme` | 3× ("sebelumnya 0 dari 39") | **3×** ("0 dari **38**") | 0 |
| `SENTIMENT_REGIME` | `extreme_greed_unconfirmed` | 21× | **20×** | −1 (id 1598) |
| `SENTIMENT_REGIME` | `mixed_sentiment` | 18× | **18×** | 0 |
| `SENTIMENT_REGIME` | `complacent_crowding` | 3× ("sebelumnya 0 dari 39") | **3×** ("0 dari **38**") | 0 |
| `FEAR_GREED_CHANGE_DIRECTION` | `down` | 40× | **39×** | −1 (id 1598) |
| `FEAR_GREED_CHANGE_DIRECTION` | `flat` | 2× ("sebelumnya 0 dari 40") | **2×** ("0 dari **39**") | 0 |

Keempat nilai TASKS 0.12 (`complacent_crowding`, `extreme`, `long_crowding`, `flat`) **tidak berubah**: semuanya pertama muncul
di `id=1976` (`ts 2026-09-04T11:21:24Z`), jauh setelah 1643 — `complacent_crowding`/`extreme`/`long_crowding` di id
1976/2047/2106 (dan 2167/2231/2291/2356/2417 di luar sapuan), `flat` di id 1976/2047. Yang bergeser hanya **angka
pembandingnya**. Tabel `MARKET_REGIME` dan `SENTIMENT_LABEL` (sapuan `id <= 835`) serta `_OBS` (697 snapshot, 1 Sep 2026)
**tidak terpengaruh**: keduanya berhenti sebelum id 1597.
Ulangi hitungan tanpa fixture: sapu `tool='monitor_market_sentiment_shift' AND id <= 2165 AND id NOT BETWEEN 1597 AND 1643`.

### Skew `as_of` vs `ts_utc` — DIVERIFIKASI 5 Sep 2026 (TASKS 2.2e AC(1); klaim "`as_of` membeku ~2,5 hari" DICABUT)
**`as_of` RYO TIDAK membeku. Ia praktis sama dengan waktu panggilan.** Klaim TASKS 2.2e ("`as_of` membeku ~2,5 hari di belakang
`ts_utc`; keempat envelope hari ini membawa `as_of` sekitar `2026-09-01T03:48Z` sementara direkam `2026-09-03T16:31Z`")
**SALAH dan dicabut 5 Sep 2026.** Sebabnya sudah tercatat di bagian sebelumnya: "keempat envelope" itu **persis id 1628–1631**,
empat baris `compare_tokens` dari blok FIXTURE `id 1597-1643` — keempatnya membawa `as_of` `2026-09-01T03:48:28Z` karena itulah
isi `docs/samples/compare_tokens.json`, dan `ts_utc` `2026-09-03T16:31Z` adalah detik saat blok fixture ditulis. Ini temuan KEDUA
yang lahir dari 47 baris yang sama (yang pertama: TASKS 2.2d).

**Bukan salah baca field (AC(1)):** kolom `snapshots.as_of` **byte-identik** dengan `json.loads(payload_json)["as_of"]` di
**2490/2490** baris (0 selisih, 0 baris tanpa field). Pada `monitor_market_sentiment_shift`, `as_of` juga sama persis dengan
`data.observation_as_of` dan `data.source.timestamp` di 48/48 baris. Jadi yang kita simpan memang field envelope RYO.

Sapuan **`max(id) = 2490`** (`mode=ro`, tanpa penulisan), selisih = `ts_utc − as_of`:

| kelompok | n | median | min | maks | p95 |
|---|---|---|---|---|---|
| baris FIXTURE (`id 1597-1643`) | **47** | 60,734 j | 60,712 j | 60,735 j | 60,734 j |
| baris NYATA (sisanya) | **2443** | **+0,48 dtk** | **−2,33 dtk** | **+71,04 dtk** | +2,6 dtk |
| 200 baris terbaru | **200** | +0,13 dtk | −1,11 dtk | +14,33 dtk | +1,0 dtk |

Per tool (baris nyata saja, detik):

| tool | n | median | min | maks (id) | % `as_of` di DEPAN `ts_utc` |
|---|---|---|---|---|---|
| `analyze_token` | 1281 | +0,42 | −2,33 | +36,01 (id 1346) | 28,8 % |
| `deep_analysis` | 940 | +0,49 | −2,03 | **+71,04 (id 451)** | 26,1 % |
| `compare_tokens` | 90 | +0,49 | −1,86 | +15,22 (id 2100) | 25,6 % |
| `market_overview` | 47 | +0,90 | −2,08 | +55,39 (id 1279) | 17,0 % |
| `monitor_market_sentiment_shift` | 47 | +1,03 | −0,59 | +3,93 (id 360) | 12,8 % |
| `scan_market` | 38 | +0,46 | −1,53 | +2,25 (id 1145) | 23,7 % |

**`as_of` sedikit di DEPAN `ts_utc` itu NYATA, dan kecil: 660 dari 2443 baris NYATA (27,0 %; nol di antara 47 baris fixture), median −0,46 dtk, ekor terjauh −2,33 dtk
(id=976, `analyze_token`).** Ini bukan artefak pembulatan: `as_of` berpresisi DETIK sedangkan `ts_utc` bermikrodetik, sehingga
pemotongan presisi hanya bisa membuat selisih **positif** (0–1 dtk). Nilai negatif berarti jam server RYO benar-benar mendahului
jam kita hingga ~2,3 dtk. Batas atas kesegaran di `core/store.py` (TASKS 2.0d) karena itu harus menoleransi **≥ 3 detik**
`as_of` di masa depan; menolak `as_of > ts_utc` akan membuang seperempat store.

**Pernah membekukah `as_of` di jendela mana pun (AC pertanyaan 3)? TIDAK.** Nilai `as_of` yang dipakai ulang oleh tool yang sama
hanya muncul berpasangan (n=2), semuanya dua panggilan dalam siklus perekam yang sama; **rentang waktu pemakaian ulang terpanjang
= 1,2 detik** (`market_overview` `2026-09-01T04:31:42Z`, id 80/81). Tidak ada satu pun jendela di mana `as_of` bertahan sementara
`ts_utc` maju.

**Fakta yang LEBIH menarik daripada ketiadaan itu — kebasian yang nyata ada di DALAM `data`, bukan di `as_of`.** Pada 47 baris
nyata `monitor_market_sentiment_shift`, tanggal data per bukti berhubungan TETAP dengan tanggal perekaman:

| field | relasi terhadap tanggal `ts_utc` | n |
|---|---|---|
| `data.evidence.fear_greed.data_date` | **D-0** | 47/47 |
| `data.evidence.altseason.data_date` | **D-0** | 47/47 |
| `data.evidence.funding.data_date` | **D-1** | 47/47 |
| `data.evidence.liquidation.data_date` | **D-1** | 47/47 |

Umur `funding.data_date` pada saat direkam (dihitung dari tengah malam UTC tanggal itu): **min 25,2 j · median 35,8 j · maks
47,5 j** (n=47). Jadi `as_of` envelope yang segar **tidak menjamin** bukti di dalamnya segar: funding & liquidation selalu data
KEMARIN, hingga ~2 hari saat direkam menjelang akhir hari UTC. `funding.current_7d_window` = `data_date−6 … data_date`, dan
`fear_greed.baseline_7d_date` = D-7. Siapa pun yang mengukur kesegaran dari `as_of` saja akan melewatkan ini.

Perintah reproduksi (read-only):
```
python3 -c 'import sqlite3,json,datetime as dt,statistics;c=sqlite3.connect("file:data/ryo.db?mode=ro",uri=True);\
p=lambda s:(lambda d: d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc))(dt.datetime.fromisoformat(s.replace("Z","+00:00")));\
R=c.execute("select id,tool,ts_utc,as_of,payload_json from snapshots").fetchall();\
print("max id",max(r[0] for r in R),"mismatch kolom vs payload",sum(1 for r in R if json.loads(r[4])["as_of"]!=r[3]));\
D=[(r[0],(p(r[2])-p(r[3])).total_seconds()) for r in R];\
F=[v for i,v in D if 1597<=i<=1643];N=[v for i,v in D if not 1597<=i<=1643];\
print("fixture n",len(F),"median_j",statistics.median(F)/3600);\
print("nyata n",len(N),"median_dtk",statistics.median(N),"min",min(N),"max",max(N),"n_negatif",sum(1 for v in N if v<0))'
```

**Tidak ada klaim "`as_of` membeku"/"data usang" yang perlu dicabut DI BERKAS INI** — diperiksa dengan
`grep -niE "membeku|usang|stale|basi|tertinggal" docs/api-facts.md` (5 Sep 2026): satu-satunya hasil bernuansa itu adalah
pencabutan `risk_off` (baris ~92) dan peringatan bahwa hitungan kosakata cepat basi — keduanya soal lain. Yang perlu dicabut ada
di `TASKS.md` 2.2e (bukan berkas saya).

### Sapuan 5 Sep 2026 — `id <= 3238` (lahir dari verifikasi TASKS 2.1b), MENGGANTIKAN hitungan sapuan 697/762/835/2165
`data/ryo.db` dibuka read-only saat perekam live masih menulis (`sqlite3.connect("file:data/ryo.db?mode=ro", uri=True)` +
`PRAGMA query_only=ON`; nol byte ditulis), **3238 snapshot**, ts `2026-09-01T03:51:43Z`–`2026-09-05T13:31:36Z` (≈ 105,7 j):
analyze_token 1680 · deep_analysis 1245 · compare_tokens 142 · market_overview 60 · monitor_market_sentiment_shift 60 · scan_market 51.
**HIMPUNAN nilai tidak bertambah untuk verdict/call/confluence/regime/label — yang berubah hanya frekuensinya:**
`analyze_token.data.verdict` accumulate 714 · caution — overbought 530 · neutral 415 · avoid 21 ·
`deep_analysis.data.verdict.call` constructive 593 · neutral 590 · cautious 62 ·
`deep_analysis.data.confluence.state` CONFIRMED 731 · MIXED 514 ·
`market_overview.data.regime` neutral 30 · risk_on 22 · risk_off 8 · `market_overview.data.sentiment.label` extreme_greed 41 · greed 19 ·
`monitor….sentiment_regime` extreme_greed_unconfirmed 33 · mixed_sentiment 19 · complacent_crowding 8 ·
`monitor….funding.crowding_state` normal 52 · long_crowding 8 · `monitor….liquidation.pressure_state` normal 52 · extreme 8 ·
`monitor….fear_greed.change_direction` down 58 · flat 2 (**`up` masih 0 dari 3238**) ·
`monitor….liquidation.dominant_liquidated_side` shorts 30 · longs 18 · balanced 12 · `monitor….altseason.phase` transition 59 · bitcoin_season 1.

**NILAI BARU: `monitor….data.evidence.fear_greed.material_shift = true` TERAMATI — 5 dari 60.**
**DICABUT: klaim "`material_shift` = `false` di 20/20 (`id <= 835`) → cabang 'pergeseran material' item 4 spec §4 — SATU-SATUNYA
cabang yang membaca `change_direction` — belum pernah dijalankan oleh data nyata."** Klaim itu benar untuk jendelanya dan salah
sejak `id=910`. Kelima baris (semuanya `status: ok`, `data_mode: live`, `availability.evidence: available`, `change_direction: down`):
id **910** (`2026-09-02T01:13:09Z`, `change_7d_points` −10) · **1081** (`2026-09-02T08:58:46Z`, −11) · **1144** (`2026-09-02T10:13:28Z`, −11) ·
**1208** (`2026-09-03T10:37:01Z`, −10) · **1280** (`2026-09-03T11:39:08Z`, −10). Cabang itu bukan lagi teori: pada tabel veto TASKS 2.1b
(201 amatan pada run 2026-09-05T19:21Z — `n` bergeser turun dari 226 seiring perekam tumbuh karena `FRESH_SCAN_LIMIT` relatif ke ujung DB, lihat peringatan di docstring `preflight/items.py`; jangkar `analyze_token` 4 simbol inti) item 4 merah **20×**, dan **20/20 bernilai `material_shift=true` + `change_direction=down` + plan long**.
**Ikut DICABUT: `change_7d_points ∈ {−5, −6, −7}` dan kalimat "jendela ini secara aritmetis tidak bisa menghasilkan arah lain".**
Sebaran teramati (`id <= 3238`, n=60): −11 (2) · −10 (3) · −9 (3) · −8 (2) · −7 (3) · −6 (6) · −5 (15) · −4 (6) · −3 (17) · −2 (1) · **0 (2)**.
`baseline_7d_value` tidak lagi tetap 80 dan `baseline_7d_date` tidak lagi tetap `2026-08-25`; hanya nilai `up` yang tetap nol amatan.
Ulangi (mencetak max id, sebaran `material_shift`, baris `true`, dan sebaran `change_7d_points`):
```
python3 -c 'import sqlite3,json,collections;c=sqlite3.connect("file:data/ryo.db?mode=ro",uri=True);c.execute("PRAGMA query_only=ON");R=[(i,ts,json.loads(p)["data"]["evidence"]) for i,ts,p in c.execute("select id,ts_utc,payload_json from snapshots where tool=\"monitor_market_sentiment_shift\" order by id")];print("max id",c.execute("select max(id) from snapshots").fetchone()[0],"n monitor",len(R));print(collections.Counter(e["fear_greed"]["material_shift"] for _,_,e in R));print([(i,ts[:19],e["fear_greed"]["change_7d_points"],e["fear_greed"]["change_direction"]) for i,ts,e in R if e["fear_greed"]["material_shift"]]);print(collections.Counter(e["fear_greed"]["change_7d_points"] for _,_,e in R))'
```

**`funding` + `liquidation` adalah KONSTANTA PER `data_date` — 60 panggilan `monitor…` = 5 titik data, bukan 60 amatan independen.**
Tuple `(funding.data_date, funding.crowding_state, liquidation.pressure_state, funding.current_7d_percentile_90d, liquidation.percentile_90d)`
hanya punya **lima** nilai berbeda di seluruh 60 baris, dan tiap nilai dipetakan satu-lawan-satu ke satu `data_date`:

| `funding.data_date` | crowding_state | pressure_state | funding pct90d | liq pct90d | n baris |
|---|---|---|---|---|---|
| 2026-08-31 | normal | normal | 56,666667 | 32,222222 | 22 |
| 2026-09-01 | normal | normal | 68,888889 | 63,333333 | 5 |
| 2026-09-02 | normal | normal | 82,222222 | 36,666667 | 12 |
| **2026-09-03** | **long_crowding** | **extreme** | **91,111111** | **92,222222** | **8** |
| 2026-09-04 | normal | normal | 53,333333 | 82,222222 | 13 |

Konsekuensi yang WAJIB ikut dikutip setiap kali angka `long_crowding`/`extreme` disebut: kedelapan baris itu
(id 1976 · 2047 · 2106 · 2167 · 2231 · 2291 · 2356 · 2417, `2026-09-04T11:21:24Z`–`19:18:28Z`) membawa persentil yang **identik
sampai enam desimal** dan `data_date` yang sama → mereka adalah **delapan kali polling atas SATU hari data funding**, bukan delapan
peristiwa pasar. Ini penajaman dari fakta yang sudah tercatat di bagian skew (`funding`/`liquidation` selalu D-1): yang baru di sini
adalah bahwa NILAI-nya pun tidak bergerak di dalam satu `data_date`. Siapa pun yang menulis "32 veto `08_derivatives`" harus
menyebut bahwa 32 = 8 baris `monitor…` × 4 simbol inti = **1 hari data funding**. Perhatikan juga `data_date` 2026-09-04:
`liquidation.percentile_90d` 82,2 TETAPI `pressure_state` `normal` → persentil tinggi tidak menentukan state; jangan mengarang ambang.
Ulangi: `python3 -c 'import sqlite3,json,collections;c=sqlite3.connect("file:data/ryo.db?mode=ro",uri=True);c.execute("PRAGMA query_only=ON");print(collections.Counter((e["funding"]["data_date"],e["funding"]["crowding_state"],e["liquidation"]["pressure_state"],e["funding"]["current_7d_percentile_90d"],e["liquidation"]["percentile_90d"]) for e in (json.loads(p)["data"]["evidence"] for (p,) in c.execute("select payload_json from snapshots where tool=\"monitor_market_sentiment_shift\""))))'`

**DICABUT sebagian: "urutan breadth memang rapi" (blok konteks `risk_off`, 2 Sep 2026).** Dengan n=60 `market_overview`
(`risk_off` kini 8, bukan 1), rentang `market.breadth` per regime **TUMPANG TINDIH**: risk_on 0,5604 (id 2105)–0,9565 (id 1843) ·
neutral **0,2088 (id 2230)**–0,8043 (id 3173) · risk_off **0,1758 (id 2416)**–0,3846 (id 763). `neutral` 0,2088 berada DI BAWAH
`risk_off` 0,3846 → breadth tidak memisahkan kedua regime itu. Larangan yang sudah ada ("tidak ada ambang breadth yang boleh
dikodekan; regime dibaca apa adanya dari `data.regime`, tidak pernah dihitung ulang dari `breadth`") sekarang punya bukti
langsung, bukan hanya peringatan n=1. Minimum breadth baru **0,1758** menggantikan 0,3846.

**Cakupan `compare_tokens` per `args_hash` simbol inti (dipakai `preflight/skill.tool_args`), `id <= 3238`.**
Empat args yang benar-benar dipakai jalur preflight: `{"symbols":"SOL, BNB, ETH","intent":"swing"}` ·
`{"symbols":"BTC, BNB, ETH",…}` · `{"symbols":"ETH, BNB",…}` · `{"symbols":"BNB, ETH",…}` — **32 baris masing-masing**,
baris pertama id **1260/1261/1262/1263** (`2026-09-03T10:50:49Z`–`10:55:07Z`). 14 baris `compare_tokens` sesi manual 1 Sep
(id 41–211) memakai args LAIN (pembanding BTC+ETH) sehingga `args_hash`-nya tidak pernah cocok untuk jalur ini.
**Konsekuensi untuk klaim mana pun tentang "lubang `compare_tokens`":** ketiadaan sebelum `2026-09-03T10:50:49Z` menjelaskan
**97** dari 110 amatan TASKS 2.1b yang ber-`missing_tools=[compare_tokens]`; **13 sisanya** jatuh SESUDAH tanggal itu dan
disebabkan perekam mati/senyap — baris `compare_tokens` terdekat berumur 181–700 mnt, di atas `max_age_min=120`
(tiga celah yang sama dengan yang sudah didokumentasikan di "Atribusi 3 celah > 120 mnt": 3 Sep 20:26Z, 4 Sep 11:21Z, 5 Sep 01:45Z).
Jadi "compare_tokens baru direkam sejak 2026-09-03" TIDAK menjelaskan ke-110; kalimat yang benar adalah 97 + 13.

### Sapuan 6 Sep 2026 — `id <= 3645` (review batch 14 commit), MENGGANTIKAN hitungan sapuan 697/762/835/2165/3238
`data/ryo.db` dibuka read-only saat perekam live masih menulis (`sqlite3.connect("file:data/ryo.db?mode=ro", uri=True)` +
`PRAGMA query_only=ON`; nol byte ditulis), **3645 snapshot**, ts `2026-09-01T03:51:43Z`–`2026-09-05T19:29:22Z`:
analyze_token 1895 · deep_analysis 1395 · compare_tokens 166 · market_overview 66 · monitor_market_sentiment_shift 66 · scan_market 57.
**HIMPUNAN nilai TIDAK bertambah dibanding sapuan `id <= 3238` untuk satu pun field kategoris** — yang berubah hanya frekuensi:
`analyze_token.data.verdict` accumulate 815 · caution — overbought 601 · neutral 457 · avoid 22 ·
`deep_analysis.data.verdict.call` constructive 662 · neutral 660 · cautious 73 ·
`deep_analysis.data.confluence.state` CONFIRMED 806 · MIXED 589 ·
`market_overview.data.regime` neutral 32 · risk_on 26 · risk_off 8 · `market_overview.data.sentiment.label` extreme_greed 47 · greed 19 ·
`monitor….sentiment_regime` extreme_greed_unconfirmed 39 · mixed_sentiment 19 · complacent_crowding 8 ·
`monitor….funding.crowding_state` normal 58 · long_crowding 8 · `monitor….liquidation.pressure_state` normal 58 · extreme 8 ·
`monitor….fear_greed.change_direction` down 64 · flat 2 (**`up` masih 0 dari 3645**) ·
`monitor….liquidation.dominant_liquidated_side` shorts 30 · longs 24 · balanced 12 · `monitor….altseason.phase` transition 65 · bitcoin_season 1 ·
`compare_tokens.data.tokens[].verdict` constructive 287 · neutral 137 (424 token; `cautious` masih **0×** di field ini) ·
`technical_analysis.trend` analyze_token up 1655 · sideways 152 · down 88; deep_analysis up 1269 · sideways 94 · down 32; compare_tokens up 424/424.
Kedua pencabutan yang dilakukan commit `1de5a30` **DIREPRODUKSI PERSIS** dari DB hari ini: `material_shift = true` 5 dari 60
(`id <= 3238`), id 910/1081/1144/1208/1280 dengan `change_7d_points` −10/−11/−11/−10/−10 dan `change_direction: down` di kelimanya;
rentang breadth `neutral` 0,2088 (id 2230) – 0,8043 (id 3173) vs `risk_off` 0,1758 (id 2416) – 0,3846 (id 763) → tumpang tindih nyata.
Pada `id <= 3645` `material_shift` = true tetap **5**, jadi 5 dari 66; `change_7d_points` −11…0; `neutral` max naik ke 0,8242 (id 3377).

**Invarian envelope yang diperiksa ulang di 3645 baris (semuanya masih berlaku).** Himpunan 10 kunci top-level identik di
3645/3645 (nol envelope dengan kunci lebih/kurang); `schema_version` `"1.0"` 3645/3645; `data` non-null 3645/3645; `as_of`
non-null dan berpresisi DETIK di 3645/3645 (nol mikrodetik, `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`); kolom `snapshots.as_of`
byte-identik dengan `json.loads(payload_json)["as_of"]` di 3645/3645; `summary` persis `{headline, key_points}` di 3645/3645.
Ulangi:
```
python3 -c 'import sqlite3,json,collections,re;c=sqlite3.connect("file:data/ryo.db?mode=ro",uri=True);c.execute("PRAGMA query_only=ON")
st=collections.Counter();dm=collections.Counter();av=collections.Counter();ks=collections.Counter();R=list(c.execute("select payload_json from snapshots"))
for (p,) in R:
 d=json.loads(p);st[d["status"]]+=1;dm[d["data_mode"]]+=1;ks[tuple(sorted(d))]+=1
 [av.__setitem__(v,av[v]+1) for v in (d.get("availability") or {}).values()]
print(len(R),st,dm,av,[(len(k),n) for k,n in ks.items()])'
```

**BARU (belum pernah tercatat di berkas ini): `deep_analysis` TIDAK PERNAH `status: ok`.** `status` = `partial` di
**1395/1395** baris `deep_analysis` (`id <= 3645`) — 100 %, sejak baris pertama. Sampel `docs/samples/deep_analysis.json`
(`partial`) bukan kebetulan, ia bentuk normal tool itu. Konsekuensinya untuk siapa pun yang membaca `status` sebagai kualitas:
`partial` pada `deep_analysis` TIDAK berarti ada yang rusak — ia berarti seksi opsional (`token_profile`, `derivatives`) tidak
terjawab, sementara `market_data`/`technical_analysis`/`trade_plan` biasanya `available`. Konsisten dengan hitungan lama:
`id <= 2165` mencatat partial 839 = deep_analysis 823 + analyze_token 16.
`analyze_token` sebaliknya `ok` 1874 / `partial` 21, dan **ke-21 baris `partial` itu semuanya simbol `DGAI`** dengan
`availability.technical_analysis: "partial"` (baris lama "9 baris `status: partial` … semuanya `DGAI`" adalah sapuan
`id <= 1727`; angkanya 9 → 21, himpunan simbolnya tidak berubah).

**BARU: 20 baris `deep_analysis` tanpa `trade_plan` — semuanya `DGAI`.** `availability.trade_plan != "available"` pada
**20 dari 1395** baris, `id` 1110 · 1175 · 1241 · 1309 · 1369 · 1425 · 1495 · 1558 · 1668 · 1744 · 1808 · 1869 · 1935 · 1993 ·
2070 · 2133 · 2208 · 2278 · 2342 · 2407 — 20/20 simbol `DGAI`, dan 20/20 adalah baris yang sama dengan yang membawa 4 `warnings`.
Ini satu-satunya lubang seksi WAJIB yang pernah kami rekam. Pada 1375 baris sisanya `trade_plan` ada dan **long di 1375/1375**
(`stop < entry`): arah `short` yang disebut spec §4 **masih 0 amatan dari 3645** (klaim lama "long di 319/319, `id <= 835`"
tetap benar untuk jendelanya; angka yang berlaku sekarang adalah 1375/1375).

**Cakupan harga diperiksa ulang** (`id <= 3645`, menggantikan sapuan `id <= 1727`): `analyze_token.data.market.price_usd`
numerik > 0 di **1895/1895**, `deep_analysis` **1395/1395**, `compare_tokens.data.tokens[].price_usd` **424/424**,
`scan_market.data.candidates[].price_usd` **1166/1166** — harga null masih **0 amatan di keempat path**.
`scan_market` masih **tidak pernah** mengembalikan SOL/BTC/ETH/BNB (0 dari 1166 kandidat, 88 simbol berbeda).
`analyze_token.args_json` berbentuk **persis `{"symbol": …}` di 1895/1895** dan `args_hash` = `sha256(canonical(args))`
cocok di 1895/1895 (0 selisih) — ini yang membuat `grading/outcomes.series_args_hash` menemukan serinya.

**Skew `as_of` vs `ts_utc` diperiksa ulang di `id <= 3645`:** 3598 baris NYATA (di luar fixture 1597–1643), median **+0,48 dtk**,
minimum **−2,334 dtk** (id 976, `analyze_token`) — **nol baris di bawah −3 dtk**, maksimum +71,04 dtk (id 451). Toleransi
"`as_of` boleh mendahului `ts_utc` sampai ≥3 detik" di `core/store.py` karena itu MASIH cukup dan masih belum tergigit.

**`compare_tokens` diperiksa ulang di 166 baris:** simbol berulang masih **0 amatan** (0/166) — perilaku RYO terhadap
`"BNB, BNB, ETH"` tetap BELUM DIVERIFIKASI; **165 dari 166** baris memuat semua simbol yang diminta, dan satu-satunya
pengecualian masih id=1629 (baris FIXTURE). Empat args jalur preflight kini **38 baris masing-masing** (dulu 32 pada `id <= 3238`).
`funding.data_date` masih memetakan satu-lawan-satu ke tuple (crowding_state, pressure_state, kedua persentil): lima tanggal
2026-08-31 (22) · 09-01 (5) · 09-02 (12) · **09-03 `long_crowding`/`extreme` (8)** · 09-04 (19). Cacah baris 09-04 naik 13 → 19
**tanpa** menambah tanggal keenam → "n panggilan `monitor…` ≠ n peristiwa" makin kuat, bukan makin lemah.

#### Bentuk envelope PADAM (`status: "unavailable"`) adalah HIPOTESIS, bukan amatan — dipakai oleh dua fixture
`preflight/fixtures/outage-one-tool-2026-09-05.json` dan `preflight/fixtures/outage-all-tools-2026-09-05.json` (TASKS 2.2c,
commit `46d7ff8`) memuat envelope yang **TIDAK pernah dikeluarkan RYO**. Dicatat di sini supaya sapuan berikutnya
membandingkannya bila RYO benar-benar mengirim `status: unavailable` — bukan supaya bentuk ini dianggap fakta.

Yang **BERDASAR** (tiap nama field dan tiap nilai enum di kedua fixture diperiksa satu per satu 6 Sep 2026, nol yang di luar
berkas ini): himpunan 10 kunci top-level identik dengan envelope RYO (ADR-013); `status: "unavailable"` dan
`data_mode: "unknown"` adalah anggota enum yang tercatat di §A dari RYO Builder MCP Guide (`status ∈ {ok, partial,
unavailable}`, `data_mode ∈ {live, mixed, simulated, unknown}`); nilai `availability` `"unavailable"` teramati 2976× di DB;
kunci `availability` tiap tool = nama seksi yang sama persis dengan tabel "Field nyata" (market_overview 5 kunci ·
monitor 3 · analyze_token 3 · deep_analysis 5 · compare_tokens 4); `summary` tetap `{headline, key_points}`; `request` dan
`as_of` **disalin apa adanya dari baris DB sumbernya**, tidak dikarang. Provenansnya juga diperiksa: kelima `source_id`
(2491, 2492, 2494, 2495, 2544) ADA di `data/ryo.db`, dan setiap envelope yang **tidak** ditulis ulang **byte-identik**
dengan `payload_json` barisnya — jadi item hijau di layar demo dinilai atas angka RYO yang benar-benar direkam.

Yang **HIPOTESIS** (jangan dikutip sebagai perilaku RYO): (1) bahwa envelope padam **mempertahankan** `as_of` alih-alih
mengosongkannya; (2) bahwa `data` menjadi `null` — `data` non-null di **3645/3645**; (3) bahwa **setiap** seksi `availability`
serentak menjadi `"unavailable"`; (4) bahwa `data_mode` menjadi `"unknown"` dan bukan tetap `"live"` — `data_mode` = `live` di
**3645/3645**; (5) bahwa kunci `availability` tetap lengkap saat `data` hilang. **Nol** dari kelimanya punya satu pun amatan.
Ini bukan halusinasi: bentuknya diturunkan dari enum yang terdokumentasi, kedua berkas mengaku sendiri lewat blok `synthetic`,
bendera `synthetic: true` per baris, kata `SYNTHETIC` di `warnings`, `summary.headline`, dan `summary.key_points`, dan
`agent/demo.py` mencetak pengakuan itu ke layar. Yang dilarang adalah membaca bentuk ini balik sebagai fakta API.

**Angka yang dikutip fixture & demo sudah basi: keduanya menulis "0/476".** Yang benar per 6 Sep 2026 adalah **0 dari 3645**.
Kalimatnya tidak salah arah (nolnya tetap nol) tetapi denominatornya tujuh kali terlalu kecil, dan aturan §A menuntut setiap
klaim nol menyebut `id` maksimum yang disapu. Perbaikan ada di `preflight/outage_fixture.py` (teks yang ditanam ke berkas
fixture) → tugas `@agent-core-engineer`/`@agent-quant-engineer`, bukan berkas ini.

**Catatan pendamping: `agent/fixtures/*.json` TIDAK memuat envelope sintetis.** Ketiga berkas (`demo-2026-09-05.json` 14
snapshot, `twodate-a-2026-09-03.json` 7, `twodate-b-2026-09-04.json` 7) diperiksa 6 Sep 2026: **28 dari 28** envelope
byte-identik dengan `payload_json` baris `data/ryo.db` ber-`source_id` yang disebutkan, dengan `tool`, `args`, dan `ts_utc`
yang cocok. Nol baris ber-`synthetic`.
Ulangi (mencetak mismatch per berkas; nol baris keluaran = semua cocok):
```
python3 -c 'import sqlite3,json,glob;c=sqlite3.connect("file:data/ryo.db?mode=ro",uri=True);c.execute("PRAGMA query_only=ON")
for f in sorted(glob.glob("agent/fixtures/*.json"))+sorted(glob.glob("preflight/fixtures/*.json")):
 for s in json.load(open(f))["snapshots"]:
  r=c.execute("select tool,args_json,ts_utc,payload_json from snapshots where id=?",(s["source_id"],)).fetchone()
  ok=r and r[0]==s["tool"] and r[2]==s["ts_utc"] and json.loads(r[1])==s["args"] and json.dumps(json.loads(r[3]),sort_keys=True)==json.dumps(s["envelope"],sort_keys=True)
  if not ok: print(f,s["source_id"],s["tool"],"synthetic" if s.get("synthetic") else "MISMATCH-TAK-DIAKUI")'
```

#### `--until` SAJA tidak mereproduksi tabel veto — DIVERIFIKASI DUA ARAH 6 Sep 2026
Peringatan di docstring `preflight/items.py` dan `preflight/veto_table.py` **BENAR dan sekarang punya bukti konstruktif**,
bukan hanya dua run yang berselisih. `core.store.FRESH_SCAN_LIMIT` (=64) memindai 64 baris TERBARU per `(tool, args_hash)`
tanpa batas SQL atas `ts_utc`, jadi batas pindai bergerak relatif terhadap **ujung DB**, bukan terhadap `--until`.
Karena `snapshots` append-only, keadaan DB pada waktu T bisa direkonstruksi persis dari salinan hari ini dengan membuang
baris ber-`ts_utc > T` (dikerjakan atas SALINAN di `$TMPDIR`; `data/ryo.db` tidak pernah ditulis). Hasil, perintah
`uv run python -m preflight.veto_table --until 2026-09-05T12:32:00Z` yang SAMA PERSIS:
| keadaan DB | baris | jangkar dipakai | dibuang batas pindai | kedalaman terdalam | 08_derivatives merah |
|---|---|---|---|---|---|
| s.d. `2026-09-05T14:21Z` | 3264 | **226** | **0** | 61/64 | 32 (8 snapshot) |
| s.d. `2026-09-05T15:30Z` | 3367 | **226** | **0** | 62/64 | 32 (8 snapshot) |
| s.d. `2026-09-05T19:21Z` | 3608 | **201** | **25** | 62/64 | 32 (8 snapshot) |
| DB hidup 6 Sep, `max id 3645` | 3645 | **201** | **25** | 62/64 | 32 (8 snapshot) |
Angka `n=226 → n=201` yang ditulis docstring karena itu **terkonfirmasi sebagai perilaku, bukan anekdot**, dan seluruh tabel
veto TASKS 2.1b (n=201; `01_freshness` 201 hijau / 0 merah; `08_derivatives` 166/32/3 dengan `8/32` snapshot; jendela
`2026-09-01T06:29:28Z .. 2026-09-05T12:15:26Z`; kedalaman 62/64; 14 snapshot skew-skipped; `params_hash 3d39fb1b…`)
**direproduksi baris demi baris** atas salinan beku keadaan 19:21Z. Dua run atas salinan beku yang sama: byte-identik.
Resep yang benar untuk mengutip tabel itu = jalankan atas salinan beku, bukan `--until` saja. Perhatikan juga bahwa
**cacah merah `08_derivatives` = 32 di keempat keadaan** meski `n` bergerak 226↔201: ia terikat ke 8 baris `monitor…`
ber-`funding.data_date 2026-09-03` × 4 simbol inti, yaitu **satu hari data funding** (lihat blok "KONSTANTA PER `data_date`").

#### Jalur harga grading gagal-KOSONG, bukan gagal-SALAH — DIVERIFIKASI 6 Sep 2026
`grading/outcomes.py` memilih seri harga dengan `args_hash = sha256(canonical({"symbol": <sym>}))`, dan perekam menulis
persis bentuk itu (`core/recorder.py:669,685` → `_record("analyze_token", {"symbol": symbol}, …)`; 1895/1895 baris cocok).
Klaim "bila perekam kelak menambah argumen ke `analyze_token`, seri harga grading akan KOSONG, bukan SALAH" **diuji, bukan
disimpulkan**: atas SALINAN `data/ryo.db` yang 1895 baris `analyze_token`-nya ditulis ulang menjadi `{"symbol":…,"chain":null}`
(hash ikut dihitung ulang), `uv run python -m grading.grader --db <salinan>` mencetak `sampel BNB=0, BTC=0, ETH=0, SOL=0`,
`outcome NO_GO: no_prices=13`, dan `n` tiap item jatuh ke **0** — nol outcome palsu, nol angka yang bertahan tanpa bukti.
Sebabnya struktural: `args_hash` adalah hash SELURUH dict argumen, jadi argumen tambahan memindahkan seluruh seri ke hash lain
dan `SELECT … WHERE args_hash = ?` tidak menemukan apa pun; `resolve_outcome` menjawab `no_prices`/`window_not_covered`,
keduanya TIDAK `resolved` sehingga dibuang dari `n`. (Sisi lain dari fakta yang sama, dan ini yang perlu diawasi: perubahan
bentuk argumen perekam **membunuh seluruh grading dalam diam kecuali headernya dibaca**. Baris `sampel …=0` adalah
satu-satunya alarmnya.)

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
