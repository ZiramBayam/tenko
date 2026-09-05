"""Fixture bertanggal untuk naskah demo spec §7 langkah 3 (TASKS 3.4a).

Spec §7 menetapkan demo berjalan dari **fixture offline**, dan langkah 3 menuntut ledger yang
memuat "≥1 GO (trade sesuai plan) + ≥1 NO_GO dengan blocking". Modul ini menyediakan bukti
bekunya: 14 baris `snapshots` yang DISALIN dari `data/ryo.db` (id 2491-2547, jendela
2026-09-05T02:45Z..03:03Z) ke `agent/fixtures/demo-2026-09-05.json`, berikut jam beku yang
membuatnya terbaca segar.

**Yang TIDAK dilakukan di sini, dan itu intinya:** tidak satu ambang pun dilonggarkan supaya
ada GO. Keputusannya lahir dari `preflight.skill.preflight` dengan `DEFAULT_PARAMS` apa adanya
(`params_hash` beku `3d39fb1b…`, TASKS 5.0); yang dibekukan hanyalah BUKTInya. Melonggarkan
gerbang demi demo adalah opsi C yang ditolak di TASKS 0.21.

**Kenapa disalin, bukan dibaca dari `data/ryo.db` saat demo jalan** (AC 1): perekam hidup dan
terus menambah baris, jadi `latest_fresh` akan memilih snapshot yang berbeda tiap jam dan demo
yang sama bisa menghasilkan keputusan berbeda. Jendela ini sendiri buktinya TIDAK stabil: 12
tick sebelumnya (4 Sep) seluruhnya NO_GO ber-`blocking: 08_derivatives`.

**Jam.** `preflight` mengukur kesegaran terhadap `now`, jadi fixture bertanggal membawa jamnya
sendiri (`now` = ts_utc snapshot termuda + 1 detik) dan meneruskannya lewat parameter `now=`
yang sudah ada di `preflight` dan `agent.main.tick`. Tidak ada `datetime.now()` di jalur
keputusan demo — itu syarat "dua run identik" (AC 3), bukan kenyamanan.

**Fixture ini TIDAK BOLEH bisa masuk ke store bukti** (AC 4). Penjaganya bukan penjaga baru:
:func:`core.recorder.fixture_refusal` (TASKS 0.19) dipanggil SEBELUM `Store` dibuka, sehingga
DB tujuan yang merupakan `data/ryo.db`, berada di dalam `ROOT/data`, atau sudah memegang buku
besar `decisions` ditolak — dan perintah yang gagal tidak meninggalkan DB kosong berskema
penuh. Satu definisi penjaga, bukan salinan kedua yang bisa menyimpang.

Pemakaian (penyambungan ke `scripts/demo.sh` + `make demo` adalah TASKS 3.4, belum ada):

    python -m agent.demo_fixture                 # DB tmp baru, satu tick, laporan ternormalisasi
    python -m agent.demo_fixture --db /tmp/x.db  # DB tujuan sendiri (di luar ROOT/data)
    python -m agent.demo_fixture --capture --from data/ryo.db --ids 2491,2492,...

Keluaran perintah pertama sengaja TIDAK memuat jam dinding maupun path DB tmp: keduanya
bergeser tiap run tanpa arti apa pun. `decision_hash` justru dicetak apa adanya — ia WAJIB
identik antar-run, dan menyembunyikannya akan membuat "dua run identik" tidak berarti apa-apa.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent.main import BANNER, tick
from agent.params import DEFAULT_AGENT_PARAMS, AgentParams
from core.store import Store, StoreError
from preflight.outage_fixture import SOURCE_HASH_KEY

#: Versi bentuk berkas fixture. Naik bila kunci wajibnya berubah.
FIXTURE_VERSION = 1

#: Fixture bawaan untuk spec §7 langkah 3.
DEMO_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "demo-2026-09-05.json"

#: Jarak jam beku dari snapshot TERMUDA. 1 detik: cukup untuk membuat `now` mendahului seluruh
#: bukti (skew ke masa depan punya toleransi sendiri di `core/store.py`), cukup kecil untuk
#: tidak menuakan apa pun.
CLOCK_LEAD = timedelta(seconds=1)


class DemoFixtureError(Exception):
    """Fixture tidak bisa dibaca, atau DB tujuannya bukan tempat yang sah untuk fixture."""


@dataclass(frozen=True, slots=True)
class FixtureSnapshot:
    """Satu baris `snapshots` yang disalin apa adanya, termasuk `ts_utc` aslinya.

    `ts_utc` ikut di-hash (`core.replay.HASHED_FIELDS`), jadi menyalinnya apa adanya membuat
    `decision_hash` fixture identik dengan yang dihitung atas `data/ryo.db`. `source_id` TIDAK
    ikut di-hash (`id` sengaja di luar `HASHED_FIELDS`), jadi ia murni jejak provenance.
    """

    source_id: int
    tool: str
    args: Mapping[str, Any]
    ts_utc: str
    envelope: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class DemoFixture:
    """Fixture bertanggal: bukti beku + jam beku + keputusan yang teramati saat penyalinan."""

    name: str
    captured_at: str
    source_db: str
    now: datetime
    symbols: tuple[str, ...]
    snapshots: tuple[FixtureSnapshot, ...]
    recorded: Mapping[str, Mapping[str, Any]]
    params_hash: str

    @property
    def source_ids(self) -> tuple[int, ...]:
        return tuple(snapshot.source_id for snapshot in self.snapshots)

    def expected_decision(self, symbol: str) -> str:
        entry = self.recorded.get(symbol)
        return str(entry["decision"]) if entry else ""


def _require(obj: Mapping[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    value = obj.get(key)
    if not isinstance(value, kind):
        raise DemoFixtureError(f"fixture: kunci {key!r} harus {kind}, bukan {type(value).__name__}")
    return value


def load_fixture(path: str | Path = DEMO_FIXTURE_PATH) -> DemoFixture:
    """Baca berkas fixture. Bentuk yang tidak lengkap = error, bukan default diam-diam."""
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DemoFixtureError(f"fixture {target} tidak bisa dibaca: {exc}") from exc
    if not isinstance(raw, dict):
        raise DemoFixtureError(f"fixture {target} bukan objek JSON")
    version = raw.get("fixture_version")
    if version != FIXTURE_VERSION:
        raise DemoFixtureError(f"fixture {target}: versi {version!r} bukan {FIXTURE_VERSION}")
    try:
        now = datetime.fromisoformat(_require(raw, "now", str))
    except ValueError as exc:
        raise DemoFixtureError(f"fixture {target}: `now` bukan ISO-8601 ({exc})") from exc
    if now.tzinfo is None:
        raise DemoFixtureError(f"fixture {target}: `now` wajib bertimezone")
    rows = _require(raw, "snapshots", list)
    if not rows:
        raise DemoFixtureError(f"fixture {target}: nol snapshot — tidak ada yang bisa dibekukan")
    snapshots = tuple(
        FixtureSnapshot(
            source_id=_require(row, "source_id", int),
            tool=_require(row, "tool", str),
            args=_require(row, "args", dict),
            ts_utc=_require(row, "ts_utc", str),
            envelope=_require(row, "envelope", dict),
        )
        for row in rows
    )
    return DemoFixture(
        name=_require(raw, "name", str),
        captured_at=_require(raw, "captured_at", str),
        source_db=_require(raw, "source_db", str),
        now=now.astimezone(UTC),
        symbols=tuple(str(symbol) for symbol in _require(raw, "symbols", list)),
        snapshots=snapshots,
        recorded=dict(_require(raw, "recorded", dict)),
        params_hash=_require(raw, "params_hash", str),
    )


def refuse_evidence_db(db_path: str | Path) -> None:
    """Tolak DB tujuan yang merupakan store bukti (AC 4, penjaga TASKS 0.19).

    Diimpor di dalam fungsi dengan sengaja: `core.recorder` menarik `core.ryo_client`, dan
    `agent/main.py` menyatakan agen ini tidak pernah mengimpor klien RYO. Jalur demo memang
    bukan jalur tick agen, tetapi menaruh impornya di tingkat modul akan membuat pernyataan
    itu tidak lagi bisa dibaca dari daftar impor.
    """
    from core.recorder import fixture_refusal

    reason = fixture_refusal(db_path)
    if reason is not None:
        raise DemoFixtureError(
            f"menolak menulis fixture demo ke {db_path}: {reason}. Fixture bukan bukti; "
            "ia tidak boleh masuk ke store bukti (TASKS 0.19/3.4a)."
        )


def materialize(fixture: DemoFixture, db_path: str | Path) -> dict[int, int]:
    """Tulis snapshot fixture ke DB BARU; kembalikan peta `source_id` → id baru.

    Urutannya `source_id` menaik, jadi id barunya deterministik (1..n) di DB kosong — syarat
    "dua run identik" tanpa normalisasi apa pun.
    """
    refuse_evidence_db(db_path)
    mapping: dict[int, int] = {}
    try:
        with Store(db_path) as store:
            for snapshot in sorted(fixture.snapshots, key=lambda row: row.source_id):
                mapping[snapshot.source_id] = store.insert_snapshot(
                    snapshot.tool,
                    snapshot.args,
                    snapshot.envelope,
                    ts_utc=snapshot.ts_utc,
                )
    except StoreError as exc:
        raise DemoFixtureError(f"fixture demo gagal ditulis ke {db_path}: {exc}") from exc
    return mapping


def run_tick(
    fixture: DemoFixture,
    db_path: str | Path,
    params: AgentParams = DEFAULT_AGENT_PARAMS,
) -> Any:
    """Materialisasi fixture ke `db_path` lalu satu tick agen pada JAM BEKU fixture."""
    materialize(fixture, db_path)
    with Store(db_path) as store:
        return tick(store, fixture.symbols, params, now=fixture.now)


def report_lines(fixture: DemoFixture, report: Any) -> list[str]:
    """Laporan ternormalisasi: tanpa jam dinding, tanpa path DB tmp, `decision_hash` apa adanya.

    Normalisasi ditulis eksplisit karena AC TASKS 3.4 menuntutnya: yang DIBUANG hanya dua hal
    yang bergeser tiap run tanpa arti (path DB sementara dan stempel waktu penulisan baris);
    `decision_id` tidak perlu dinormalkan karena tiap run memulai DB kosong.
    """
    lines = [
        BANNER,
        f"fixture: {fixture.name} (dari {fixture.source_db} id {list(fixture.source_ids)})",
        f"frozen clock: {fixture.now.isoformat()}",
        f"params_hash (decision): {fixture.params_hash}",
    ]
    lines.extend(report.lines)
    for outcome in report.outcomes:
        lines.append(outcome.line())
        if outcome.decision_hash:
            lines.append(f"{outcome.symbol:<6} decision_hash={outcome.decision_hash}")
    lines.append(
        f"tick: {report.n_go} GO, {report.n_no_go} NO_GO, {report.n_rejected} rejected, "
        f"{report.n_insufficient} without evidence — all SIMULATED"
    )
    return lines


def capture(
    source_db: str | Path,
    ids: Sequence[int],
    symbols: Sequence[str],
    *,
    name: str,
    params: AgentParams = DEFAULT_AGENT_PARAMS,
) -> dict[str, Any]:
    """Salin baris `snapshots` ber-id dari store bukti (BACA-SAJA) menjadi berkas fixture.

    Blok `recorded` diisi dari tick nyata atas salinan itu — teramati, bukan diketik tangan.
    """
    with Store(source_db, read_only=True) as store:
        rows = store.get_snapshots(ids)
    found = {row.id for row in rows}
    missing = [snapshot_id for snapshot_id in ids if snapshot_id not in found]
    if missing:
        raise DemoFixtureError(f"{source_db}: snapshot {missing} tidak ada (jendela sudah bergeser?)")
    snapshots = [
        {
            "source_id": row.id,
            "tool": row.tool,
            "args": json.loads(row.args_json),
            "ts_utc": row.ts_utc,
            "envelope": json.loads(row.payload_json),
            # `snapshots.payload_hash` baris ASAL, direkam di sini dan hanya di sini. Tanpa
            # kolom ini, "envelope ini salinan apa adanya `data/ryo.db`" adalah klaim berkas
            # tentang dirinya sendiri: bentuknya bisa rapi sementara satu angka di dalamnya
            # diketik ulang. `preflight.outage_fixture.verify_provenance` menghitung ulang
            # sha256 kanonik envelope yang dimuat dan membandingkannya dengan nilai ini —
            # perbandingannya jatuh di sini, saat penyalinan, supaya demo tidak perlu
            # membuka `data/` lagi (penjaga `sys.addaudithook` di `agent/demo.py`).
            SOURCE_HASH_KEY: row.payload_hash,
        }
        for row in sorted(rows, key=lambda row: row.id)
    ]
    now = max(datetime.fromisoformat(row.ts_utc) for row in rows) + CLOCK_LEAD
    fixture = DemoFixture(
        name=name,
        captured_at=datetime.now(UTC).isoformat(),
        source_db=str(source_db),
        now=now,
        symbols=tuple(symbols),
        snapshots=tuple(
            FixtureSnapshot(row["source_id"], row["tool"], row["args"], row["ts_utc"], row["envelope"])
            for row in snapshots
        ),
        recorded={},
        params_hash=params.decision_params_hash(),
    )
    with tempfile.TemporaryDirectory() as tmp:
        report = run_tick(fixture, Path(tmp) / "demo.db", params)
    recorded = {
        outcome.symbol: {
            "decision": outcome.decision,
            "blocking": list(outcome.blocking),
            "decision_hash": outcome.decision_hash,
        }
        for outcome in report.outcomes
    }
    return {
        "fixture_version": FIXTURE_VERSION,
        "name": name,
        "captured_at": fixture.captured_at,
        "source_db": str(source_db),
        "source_ids": list(fixture.source_ids),
        "now": now.isoformat(),
        "symbols": list(symbols),
        "params_hash": fixture.params_hash,
        "recorded": recorded,
        "snapshots": snapshots,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent.demo_fixture",
        description="Satu tick agen atas fixture demo bertanggal (SIMULATED, offline).",
    )
    parser.add_argument("--fixture", default=str(DEMO_FIXTURE_PATH), help="berkas fixture demo")
    parser.add_argument("--db", default=None, help="DB tujuan (default: DB tmp baru tiap run)")
    parser.add_argument("--capture", action="store_true", help="salin ulang fixture dari store bukti")
    parser.add_argument("--from", dest="source_db", default="data/ryo.db", help="store sumber (--capture)")
    parser.add_argument("--ids", default="", help="id snapshot, dipisah koma (--capture)")
    parser.add_argument("--symbols", default="SOL,BTC,ETH,BNB", help="watchlist fixture (--capture)")
    parser.add_argument("--name", default="", help="nama fixture (--capture)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.capture:
            ids = [int(part) for part in args.ids.split(",") if part.strip()]
            symbols = [part.strip() for part in args.symbols.split(",") if part.strip()]
            payload = capture(
                args.source_db,
                ids,
                symbols,
                name=args.name or f"demo-{datetime.now(UTC).date().isoformat()}",
            )
            Path(args.fixture).write_text(
                json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"fixture ditulis: {args.fixture} ({len(payload['snapshots'])} snapshot)")
            return 0
        fixture = load_fixture(args.fixture)
        if args.db is not None:
            report = run_tick(fixture, args.db)
            print("\n".join(report_lines(fixture, report)))
            return 0
        with tempfile.TemporaryDirectory(prefix="tenko-demo-") as tmp:
            report = run_tick(fixture, Path(tmp) / "demo.db")
            print("\n".join(report_lines(fixture, report)))
        return 0
    except (DemoFixtureError, StoreError) as exc:
        print(f"demo-fixture: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
