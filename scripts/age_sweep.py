"""Kanari kesegaran: sebaran `ts_utc − as_of` di `data/ryo.db` (TASKS 0.20).

**Kenapa ini ada, dan kenapa ia BUKAN item preflight.** Item 1 mengukur umur bukti relatif
**di dalam satu bundel** (acuan = `as_of` terbaru di bundel), bukan terhadap jam dinding. Jadi
bila RYO menyajikan seluruh bundel yang usang seragam, item 1 tetap hijau dan `max_age_min`
berapa pun tidak akan menangkapnya (TASKS 2.2e).

Batas itu diterima secara SADAR, bukan terlewat, dan alasannya empiris: pada sapuan 5 Sep 2026
(n=2443 baris nyata) selisihnya bermedian +0,48 detik dengan maksimum +71 detik. Menambah gigi
jam dinding ke jalur keputusan berarti item ke-12 + ADR + `params_hash` yang bergeser — seluruh
buku besar `decisions` menjadi `STALE_PARAMS` — demi gejala yang 0 dari 2443 baris pernah
tunjukkan (keputusan penjagaan ruang lingkup, 5 Sep 2026).

Yang menjaga supaya penerimaan itu tetap BERDASAR adalah berkas ini: sebuah kanari di LUAR
jalur keputusan. Ia tidak menyentuh `preflight/params.py`, tidak menggeser `params_hash`, dan
tidak bisa mengubah satu keputusan pun. Bila selisihnya kelak melonjak, ia merah — dan saat itu
barulah percakapan tentang gigi jam dinding punya bukti untuk berdiri.

Baris fixture `id 1597-1643` DIKECUALIKAN: 47 baris itu salinan `docs/samples` yang sempat
mencemari store (TASKS 0.19), selisihnya 60,7 jam, dan memasukkannya berarti kanari ini merah
selamanya karena sesuatu yang sudah didiagnosis.

Stdlib saja dan `mode=ro`, mengikuti `scripts/db_status.py`: nol byte ditulis ke DB.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "ryo.db"

#: Baris fixture yang sempat masuk store bukti (TASKS 0.19); dikecualikan dari sapuan.
FIXTURE_IDS = range(1597, 1644)

#: Ambang merah, dalam jam. 6 jam adalah dua kali lipat celah terlebar yang masih masuk akal
#: bagi perekam per jam, dan ~300x lebih besar daripada maksimum yang pernah teramati (71 dtk).
#: Ia dibuat untuk menangkap PERGESERAN REZIM, bukan untuk menyetel apa pun.
MAX_LAG_HOURS = 6.0


def _parse(raw: str | None) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def sweep(path: Path) -> tuple[list[float], int, int]:
    """`(selisih dalam jam, jumlah baris fixture dilewati, jumlah baris tanpa as_of)`."""
    connection = sqlite3.connect("file:" + quote(path.as_posix(), safe="/") + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute("SELECT id, ts_utc, payload_json FROM snapshots").fetchall()
    finally:
        connection.close()

    lags: list[float] = []
    skipped = missing = 0
    for row_id, ts_utc, payload_json in rows:
        if row_id in FIXTURE_IDS:
            skipped += 1
            continue
        recorded = _parse(ts_utc)
        try:
            as_of = _parse(json.loads(payload_json).get("as_of"))
        except (TypeError, ValueError):
            as_of = None
        if recorded is None or as_of is None:
            # Baris tanpa `as_of` DISEBUT, tidak dihitung sebagai selisih 0 (null != default).
            missing += 1
            continue
        lags.append((recorded - as_of).total_seconds() / 3600.0)
    return lags, skipped, missing


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else DB_PATH
    if not path.exists():
        print(f"age-sweep: {path} tidak ada — jalankan `make record` dulu", file=sys.stderr)
        return 2
    lags, skipped, missing = sweep(path)
    if not lags:
        print(f"age-sweep: nol baris ber-`as_of` di {path} (fixture dilewati: {skipped})", file=sys.stderr)
        return 2

    lags.sort()
    worst = max(lags, key=abs)
    print(
        f"age-sweep {path}: n={len(lags)} "
        f"median={statistics.median(lags) * 3600:+.2f} dtk "
        f"min={lags[0] * 3600:+.2f} dtk "
        f"max={lags[-1] * 3600:+.2f} dtk "
        f"(fixture dilewati: {skipped}; tanpa as_of: {missing})"
    )
    if abs(worst) > MAX_LAG_HOURS:
        print(
            f"age-sweep MERAH: selisih terbesar {worst:+.2f} jam melewati ambang "
            f"{MAX_LAG_HOURS:g} jam. Item 1 TIDAK akan menangkap ini — ia mengukur umur relatif "
            f"di dalam bundel, bukan terhadap jam dinding (TASKS 2.2e). Periksa apakah RYO mulai "
            f"menyajikan data usang, lalu bawa buktinya ke penjagaan ruang lingkup sebelum menyentuh "
            f"`preflight/params.py`.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
