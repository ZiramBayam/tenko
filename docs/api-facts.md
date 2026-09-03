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
- `monitor….data.evidence.fear_greed.material_shift` = `false` di **20/20** (`id <= 835`) → cabang "pergeseran material" item 4 spec §4 —
  SATU-SATUNYA cabang yang membaca `change_direction` — belum pernah dijalankan oleh data nyata.
- Kunci `monitor` yang lengkap di 20/20 (baris "Field nyata" di atas hanya menyebut yang dipakai item): `data` =
  {observation_as_of, status, source_attribution, summary, evidence, source, diagnostics}; `fear_greed` = {data_date, value,
  label, baseline_7d_date, baseline_7d_value, change_7d_points, change_direction, material_shift}; `funding` juga punya
  {data_date, previous_7d_window, wow_change_pct}; `liquidation` juga punya {data_date, coverage_scope}; `altseason` =
  {data_date, index, phase}.
- Kosakata kategoris monitor lain (20 amatan, semuanya nilai TUNGGAL — jangan dibaca sebagai enum lengkap):
  `liquidation.dominant_liquidated_side` `shorts` (20) · `altseason.phase` `transition` (20), di samping
  `funding.crowding_state` dan `liquidation.pressure_state` `normal` (20) yang sudah tercatat di atas.
- `deep_analysis.data.trade_plan` **long di 319/319** (`id <= 835`) (`stop < entry` selalu). Arah `short` yang disebut spec §4 ("bila plan short
  muncul, arah dibalik") BELUM PERNAH TERAMATI → cabang short adalah cabang tak-teruji-data, bukan cabang biasa.

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

**ENUM DI ATAS TETAP TIDAK LENGKAP** dan tidak bisa dibuktikan lengkap dari sini. Ini pengamatan atas 835 respons (`id <= 835`) dalam ~14,5 jam
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
dalam 120 menit terakhir. Sapuan `id <= 1727` atas 4 simbol inti (jendela 2026-09-01T03:51Z–2026-09-03T20:32Z, ≈64,7 jam):
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
