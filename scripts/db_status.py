"""Ringkasan isi data/ryo.db tanpa bergantung paket aplikasi (dipakai scripts/daemons.sh status)."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    path = Path(os.environ.get("DB_PATH", "./data/ryo.db"))
    if not path.exists():
        print(f"  data: belum ada ({path})")
        return 0
    c = sqlite3.connect(str(path))

    def has(table: str) -> bool:
        row = c.execute("SELECT 1 FROM sqlite_master WHERE type=? AND name=?", ("table", table)).fetchone()
        return row is not None

    if has("snapshots"):
        total = c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        per = dict(c.execute("SELECT tool, COUNT(*) FROM snapshots GROUP BY tool"))
        last = c.execute("SELECT MAX(ts_utc) FROM snapshots").fetchone()[0]
        print(f"  snapshots: {total} {per}")
        print(f"  terbaru  : {last}")
    for table, label in (("decisions", "keputusan"), ("trade_events", "trade event")):
        if has(table):
            print(f"  {label}: {c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
