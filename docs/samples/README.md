# Sampel respons RYO (1 Sep 2026 03:47–03:48Z) — sumber kebenaran nama field
`<tool>.json` = respons REST penuh (`{"result": envelope}`), `<tool>.headers` = header (x-ratelimit-*). `whoami.json` diredaksi (key_id/label).
`catalog.json` = GET /tools. Argumen: analyze_token {symbol:SOL}; deep_analysis {symbol:SOL, include_perp:true}; compare_tokens {symbols:"SOL, BNB, ETH", intent:swing}; scan_market {top_n:10}.
Dipakai sebagai fixture tes (FixtureClient). Jangan edit; ambil ulang dengan skrip (TASKS 1.1) bila perlu sampel baru.
