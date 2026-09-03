"""Bantu tes agen: store berisi envelope RYO NYATA dari `docs/samples/*.json` (TASKS 2.4).

Sama seperti `tests/test_preflight_skill.py`, bukti berasal dari sampel yang direkam TASKS 0.3
dan ditulis ke store SQLite di `tmp_path`. **Tidak satu tes pun menyentuh `data/ryo.db`.**

Jam: agen produksi memakai jam dinding untuk resolusi kesegaran store (`latest_fresh`), jadi
baris fixture ditulis relatif terhadap `datetime.now(UTC)` — dengan jam tetap seluruh baris
terbaca basi dan yang diuji berubah diam-diam menjadi jalur "tidak ada bukti".
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core.store import Store
from preflight.params import DEFAULT_PARAMS
from preflight.skill import tool_args

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "docs" / "samples"

MARKET_OVERVIEW = "market_overview"
MONITOR = "monitor_market_sentiment_shift"
ANALYZE = "analyze_token"
DEEP = "deep_analysis"
COMPARE = "compare_tokens"
TOOLS: tuple[str, ...] = (MARKET_OVERVIEW, MONITOR, ANALYZE, DEEP, COMPARE)

#: Simbol yang punya baris di fixture default (watchlist inti).
CORE = ("SOL", "BTC", "ETH", "BNB")

_CACHE: dict[str, dict[str, Any]] = {}


def sample(tool: str) -> dict[str, Any]:
    """Envelope RYO nyata (`response["result"]`) dari docs/samples, salinan segar tiap kali."""
    if tool not in _CACHE:
        _CACHE[tool] = json.loads((SAMPLES / f"{tool}.json").read_text(encoding="utf-8"))["result"]
    return json.loads(json.dumps(_CACHE[tool]))


def envelope_for(tool: str, symbol: str) -> dict[str, Any]:
    """Sampel yang simbolnya diganti supaya envelope tidak menyebut simbol lain.

    Yang diganti hanya `request` dan nama simbol di `data.asset`/`data.tokens[]`: sisanya
    (angka, verdict, availability) tetap seperti yang benar-benar dikeluarkan RYO.
    """
    envelope = sample(tool)
    request = envelope.get("request")
    if isinstance(request, dict) and "symbol" in request:
        request["symbol"] = symbol
    data = envelope.get("data")
    if isinstance(data, dict):
        asset = data.get("asset")
        if isinstance(asset, dict):
            asset["symbol"] = symbol
    return envelope


def build_store(
    tmp_path: Path,
    *,
    symbols: Sequence[str] = CORE,
    tools: Sequence[str] = TOOLS,
    mutate: Callable[[str, str, dict[str, Any]], None] | None = None,
    age_min: float = 2.0,
    name: str = "agent.db",
    at: datetime | None = None,
) -> Store:
    """Store berisi satu baris per (tool, simbol), berumur `age_min` menit terhadap `at`.

    `mutate(tool, symbol, envelope)` dipanggil sebelum baris ditulis — dipakai membuat satu
    simbol NO_GO tanpa menyentuh simbol lain (mis. `confluence.state = "MIXED"`).
    """
    moment = at if at is not None else datetime.now(UTC)
    ts = (moment - timedelta(minutes=age_min)).isoformat()
    store = Store(tmp_path / name)
    written: set[tuple[str, str]] = set()
    for symbol in symbols:
        args = tool_args(symbol, "swing", DEFAULT_PARAMS)
        for tool in tools:
            key = (tool, json.dumps(args[tool], sort_keys=True))
            if key in written:
                # `market_overview`/`monitor` memakai args `{}` untuk SEMUA simbol: satu baris
                # saja, persis seperti perekam menulisnya.
                continue
            written.add(key)
            envelope = envelope_for(tool, symbol)
            if mutate is not None:
                mutate(tool, symbol, envelope)
            store.insert_snapshot(tool, args[tool], envelope, ts_utc=ts)
    return store


def mixed_confluence(symbol_to_break: str) -> Callable[[str, str, dict[str, Any]], None]:
    """Mutator: buat SATU simbol NO_GO lewat `deep_analysis.data.confluence.state = "MIXED"`.

    `deep_analysis` dicari dengan args ber-simbol, jadi mutasi ini tidak menyentuh simbol lain
    — beda dengan `market_overview` yang args-nya `{}` dan dipakai bersama.
    """

    def mutate(tool: str, symbol: str, envelope: dict[str, Any]) -> None:
        if tool != DEEP or symbol != symbol_to_break:
            return
        data = envelope.get("data")
        if isinstance(data, dict) and isinstance(data.get("confluence"), dict):
            data["confluence"]["state"] = "MIXED"
            data["confluence"]["score"] = 0.5

    return mutate


def price_snapshot(
    store: Store,
    symbol: str,
    price: float,
    *,
    age_min: float = 0.5,
    at: datetime | None = None,
) -> int:
    """Tulis satu snapshot `analyze_token` baru dengan `data.market.price_usd = price`.

    Inilah "harga perekam" yang dipakai menutup posisi (`agent/trade.py`); nama field-nya
    PERSIS seperti docs/api-facts.md §A, tidak dikarang di sini.
    """
    envelope = envelope_for(ANALYZE, symbol)
    envelope["data"]["market"]["price_usd"] = price
    moment = at if at is not None else datetime.now(UTC)
    ts = (moment - timedelta(minutes=age_min)).isoformat()
    return store.insert_snapshot(ANALYZE, {"symbol": symbol}, envelope, ts_utc=ts)
