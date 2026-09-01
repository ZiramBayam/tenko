"""Perekam sementara — hanya httpx + stdlib, tidak bergantung paket `core/`.

Dipakai selama `core/recorder.py` belum dibangun ulang (TASKS 0.5) supaya tidak ada
lubang di data pasar: lapisan grading butuh outcome T+24 jam, dan jam yang terlewat
tidak bisa dibuat ulang. Menulis ke skema `snapshots` yang sama persis.

HAPUS berkas ini begitu TASKS 0.5 selesai; `scripts/daemons.sh` otomatis beralih
ke `core.recorder` kalau modul itu sudah ada.

Pakai: uv run python scripts/bootstrap_recorder.py [--once] [--interval 3600]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import sqlite3
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

log = logging.getLogger("bootstrap.recorder")

CORE_SYMBOLS = ("SOL", "BTC", "ETH", "BNB")
SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tool TEXT NOT NULL,
  args_json TEXT NOT NULL,
  args_hash TEXT NOT NULL,
  ts_utc TEXT NOT NULL,
  as_of TEXT,
  status TEXT,
  payload_json TEXT NOT NULL,
  payload_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snap_tool_ts ON snapshots(tool, ts_utc);
CREATE INDEX IF NOT EXISTS ix_snap_args ON snapshots(args_hash, ts_utc);
"""


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(obj) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def load_env(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.split("#")[0].strip())


class Bucket:
    """60 panggilan/menit per key (docs/api-facts.md §A)."""

    def __init__(self, per_min: int = 60):
        self.cap = float(per_min)
        self.tokens = float(per_min)
        self.rate = per_min / 60.0
        self.t = time.monotonic()
        self.lock = threading.Lock()

    def take(self) -> None:
        with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.cap, self.tokens + (now - self.t) * self.rate)
                self.t = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                time.sleep((1 - self.tokens) / self.rate)


class Client:
    def __init__(self, base: str, key: str, timeout: float = 90.0, retries: int = 3):
        self.base = base.rstrip("/")
        self._h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        self.http = httpx.Client(timeout=timeout)
        self.bucket = Bucket()
        self.retries = retries

    def __repr__(self) -> str:  # tidak pernah menampilkan kredensial
        return f"Client(base={self.base!r})"

    def call(self, tool: str, args: dict) -> dict | None:
        url = f"{self.base}/tools/{tool}/call"
        for attempt in range(self.retries + 1):
            self.bucket.take()
            try:
                r = self.http.post(url, headers=self._h, json=args)
            except httpx.HTTPError as e:
                log.warning("%s: jaringan gagal (%s)", tool, type(e).__name__)
                if attempt >= self.retries:
                    return None
                time.sleep(min(60, 2**attempt + random.uniform(0, 1)))
                continue
            if r.status_code in (429, 503) and attempt < self.retries:
                ra = r.headers.get("Retry-After")
                delay = float(ra) if ra and ra.isdigit() else min(60, 2**attempt + random.uniform(0, 1))
                log.warning("%s: HTTP %s, backoff %.1fs", tool, r.status_code, delay)
                time.sleep(delay)
                continue
            if r.status_code >= 400:
                log.warning("%s: HTTP %s", tool, r.status_code)
                return None
            try:
                env = r.json().get("result")
            except json.JSONDecodeError:
                log.warning("%s: respons bukan JSON", tool)
                return None
            return env if isinstance(env, dict) and "status" in env else None
        return None


def store(db: sqlite3.Connection, tool: str, args: dict, env: dict) -> None:
    db.execute(
        "INSERT INTO snapshots(tool,args_json,args_hash,ts_utc,as_of,status,payload_json,payload_hash)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (
            tool,
            canonical(args),
            sha256_hex(args),
            datetime.now(UTC).isoformat(),
            env.get("as_of"),
            env.get("status"),
            canonical(env),
            sha256_hex(env),
        ),
    )
    db.commit()


def followups(db: sqlite3.Connection, hours: int = 26) -> list[str]:
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    rows = db.execute(
        "SELECT DISTINCT json_extract(args_json,'$.symbol') s FROM snapshots"
        " WHERE tool='analyze_token' AND ts_utc >= ?",
        (cutoff,),
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def cycle(c: Client, db: sqlite3.Connection, max_symbols: int) -> dict[str, int]:
    n: dict[str, int] = {}

    def rec(tool: str, args: dict) -> None:
        env = c.call(tool, args)
        if env is None:
            return
        store(db, tool, args, env)
        n[tool] = n.get(tool, 0) + 1

    rec("market_overview", {})
    rec("monitor_market_sentiment_shift", {})

    symbols = [
        s.strip().upper() for s in os.environ.get("WATCHLIST", ",".join(CORE_SYMBOLS)).split(",") if s.strip()
    ]
    env = c.call("scan_market", {"top_n": max(1, max_symbols - len(symbols))})
    if env:
        store(db, "scan_market", {"top_n": max(1, max_symbols - len(symbols))}, env)
        n["scan_market"] = n.get("scan_market", 0) + 1
        for cand in (env.get("data") or {}).get("candidates") or []:
            s = str(cand.get("symbol", "")).upper()
            if s and s not in symbols:
                symbols.append(s)
    symbols = symbols[:max_symbols]

    for s in symbols:
        rec("analyze_token", {"symbol": s})
        rec("deep_analysis", {"symbol": s, "include_perp": True})
    for s in followups(db):
        if s not in symbols:
            rec("analyze_token", {"symbol": s})
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=int(os.environ.get("RECORDER_INTERVAL_S", "3600")))
    ap.add_argument("--db", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    load_env()

    base, key = os.environ.get("RYO_MCP_URL", ""), os.environ.get("RYO_MCP_KEY", "")
    if not base or not key:
        log.error("RYO_MCP_URL/RYO_MCP_KEY belum diset (lihat .env.example)")
        return 2
    dbpath = Path(a.db or os.environ.get("DB_PATH", "./data/ryo.db"))
    dbpath.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(dbpath))
    db.executescript(SCHEMA)
    c = Client(base, key)
    max_symbols = int(os.environ.get("RECORDER_MAX_SYMBOLS", "20"))
    log.info("perekam sementara mulai db=%s interval=%ss max_symbols=%d", dbpath, a.interval, max_symbols)

    while True:
        t0 = time.monotonic()
        try:
            n = cycle(c, db, max_symbols)
            total = db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            log.info("siklus selesai %s total=%d durasi=%.0fs", n, total, time.monotonic() - t0)
        except Exception:  # satu siklus gagal tidak boleh menghentikan perekaman
            log.exception("siklus gagal, lanjut ke siklus berikutnya")
        if a.once:
            return 0
        time.sleep(max(1, a.interval - (time.monotonic() - t0)))


if __name__ == "__main__":
    sys.exit(main())
