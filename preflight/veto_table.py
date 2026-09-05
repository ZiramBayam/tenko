"""Tabel daya veto empiris per item, ber-`n` (TASKS 2.1b) — menjawab pertanyaan juri #2:
*item mana yang benar-benar pernah menahan sesuatu?*

Alat UKUR, bukan alat putus. Ia tidak punya ambang sendiri, tidak punya pemilih bukti sendiri,
dan tidak menulis satu baris pun. Yang dilakukannya hanya: menjalankan jalur produksi berkali-
kali atas bukti yang sudah ada di `data/ryo.db`, lalu MENCACAH hasilnya.

Metode (satu paragraf, supaya angkanya bisa dibantah)
----------------------------------------------------
* **Amatan** = satu pasangan (simbol inti, jangkar waktu). Jangkar = `ts_utc` tiap baris
  ``analyze_token`` untuk simbol itu — seri terpadat per simbol, jadi ia menentukan cacah
  amatan; simbolnya :data:`CORE_SYMBOLS`.
* **Bukti** dirakit oleh :func:`preflight.skill.gather` APA ADANYA, dengan ``now=<jangkar>``
  dan :data:`preflight.params.DEFAULT_PARAMS`. Tidak ada satu baris pun di modul ini yang
  memilih snapshot: kalau ia memilih sendiri, tabel ini mengukur fungsi lain dan karena itu
  bohong. Penilaian lewat :func:`preflight.items.evaluate_all` + :func:`~preflight.items.results_of`.
* **`n` per item** = cacah amatan yang BERHASIL merakit bundel. Jangkar yang gagal merakit
  sama sekali dilaporkan terpisah di header, bukan disembunyikan.
* **Nol panggilan RYO, nol tulisan.** Store dibuka ``Store(read_only=True)`` (URI ``mode=ro``
  ber-``quote`` + ``PRAGMA query_only=ON`` + authorizer allowlist, pola TASKS 0.10).

Batas yang diakui (baca sebelum mengutip angkanya; gaya ``core/replay.py``)
--------------------------------------------------------------------------
* **:data:`core.store.FRESH_SCAN_LIMIT` bisa membuat tabel ini berbohong, dan itulah jebakan
  utamanya.** ``latest_fresh`` memilih dengan ``ORDER BY ts_utc DESC LIMIT 64`` **tanpa batas
  SQL atas `ts_utc`**: untuk jangkar yang lebih tua dari 64 siklus, ke-64 baris yang dipindai
  semuanya lebih baru dari jangkar, semuanya dibuang oleh batas masa depan, dan hasilnya
  `unspoken` **karena batas pemindaian, bukan karena buktinya tidak ada**. Itu akan terbaca
  sebagai "item ini sering diam" yang 100 % artefak. Modul ini TIDAK menaikkan konstanta itu
  (ia milik ``core/``, dan menaikkannya mengubah perilaku produksi demi sebuah laporan).
  Yang dilakukannya: menghitung **kedalaman pindai yang dibutuhkan** tiap jangkar
  (:func:`scan_depth`) = jumlah baris seri yang lebih baru dari jangkar + baris yang jatuh di
  dalam jendela kesegaran + baris ber-`ts_utc` tak terbaca; jangkar yang butuh lebih dari 64
  **dibuang, dicacah, dan disebut di header** (``scan_limited``). Header juga menulis
  kedalaman terdalam yang benar-benar dipakai, supaya jarak ke batas itu terlihat angkanya.
* **Umur tabel ini terbatas selama perekam hidup.** Setiap siklus perekam menambah satu baris
  per seri, jadi jangkar hari ini akan melewati batas 64 dalam beberapa hari. Menjalankan ulang
  perintah yang sama minggu depan (`--until` sama) TIDAK dijamin memberi `n` yang sama: yang
  dijamin adalah alatnya akan **memberi tahu** — `scan_limited` naik dan `n` turun, keduanya
  tercetak di header. Angka yang mengecil dengan jujur lebih berguna daripada angka yang
  bertahan karena batasnya disembunyikan.
* **Ini BUKAN akurasi.** "Merah" di sini berarti item itu menahan keberangkatan, bukan bahwa
  menahan itu benar. Apakah veto membayar (outcome ≥24 j) adalah TASKS 3.3 (`grading/`).
* **Bukan sampel acak.** Jangkarnya adalah jadwal perekam atas empat simbol inti selama
  jendela yang tercetak di header — satu rezim pasar, bukan populasi.
* **Baris ber-`ts_utc` yang bertengkar dengan `as_of`** (fixture TASKS 0.19) dibuang oleh
  ``latest_fresh`` sendiri, sesuai jalur produksi. Modul ini tidak menambah pengecualian ber-id
  untuknya — ia hanya mencacahnya (``skew-skipped``) supaya `unspoken` yang lahir dari sana
  tidak terbaca sebagai sifat item.

Reproduksi::

    uv run python -m preflight.veto_table --until 2026-09-05T12:32:00Z
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import textwrap
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core.store import (
    EVIDENCE_SKEW_TOLERANCE_S,
    FRESH_SCAN_LIMIT,
    FUTURE_SKEW_TOLERANCE_S,
    Store,
    StoreError,
    sha256_hex,
)
from preflight.items import ITEMS, Evidence, evaluate_all, results_of
from preflight.params import DEFAULT_PARAMS, Params
from preflight.skill import gather, tool_args

#: Simbol inti — salinan sadar dari `core.recorder.CORE_SYMBOLS`/`agent.params.CORE_SYMBOLS`.
#: TIDAK di-import: `preflight/` tidak boleh punya jalan menuju modul yang meng-import klien
#: HTTP (tes `test_module_never_imports_a_live_client`). Kesamaannya dijaga tes, bukan harapan.
CORE_SYMBOLS: tuple[str, ...] = ("SOL", "BTC", "ETH", "BNB")

#: Tool yang barisnya menjadi jangkar. Seri terpadat per simbol (api-facts §A: perekam menulis
#: satu `analyze_token` per simbol per siklus), jadi ia yang menentukan cacah amatan.
ANCHOR_TOOL = "analyze_token"

GREEN = "green"
RED = "red"
UNSPOKEN = "unspoken"
STATUSES: tuple[str, ...] = (GREEN, RED, UNSPOKEN)

#: Batas panjang satu nilai teks di dalam kunci penyebab merah (nilai mentah dipotong, bukan
#: dibulatkan — pemotongannya ditandai `…` supaya tidak terbaca sebagai nilai penuh).
CAUSE_TEXT_LIMIT = 48
#: Kedalaman perataan `ItemResult.value`; lebih dalam dari ini diringkas jadi `…`.
CAUSE_DEPTH = 3
#: Daftar skalar sampai panjang ini dicetak utuh; lebih panjang jadi `n=<len>` (item 9 pernah
#: membawa 50.000 target — mencetaknya berarti tabel yang tidak bisa dibaca siapa pun).
CAUSE_LIST_LIMIT = 6

#: Nota `core.store.latest_fresh` untuk baris yang `ts_utc`-nya bertengkar dengan `as_of`.
#: Dicocokkan dari teks karena itulah satu-satunya kanal yang `gather` sediakan; tes
#: `test_veto_table.py` memakai baris ber-skew sungguhan, jadi perubahan kata di `core/` akan
#: memerahkan tes, bukan diam-diam membuat cacahnya nol.
SKEW_NOTE = re.compile(r"snapshot_id=(\d+) skipped, ts_utc and as_of disagree")

log = logging.getLogger(__name__)


# --- pencacah -----------------------------------------------------------------------------
@dataclass
class ItemTally:
    """Cacah status satu item + distribusi nilai mentah penyebab merahnya."""

    item_id: str
    status: Counter[str] = field(default_factory=Counter)
    red_causes: Counter[str] = field(default_factory=Counter)

    @property
    def n(self) -> int:
        """Cacah amatan yang menilai item ini (green+red+unspoken) — selalu dicetak."""
        return sum(self.status[name] for name in STATUSES)

    def add(self, status: str, value: Any) -> None:
        self.status[status] += 1
        if status == RED:
            self.red_causes[cause_key(value)] += 1

    def never_held_back(self) -> bool:
        """0× merah DAN 0× unspoken atas `n` penuh — kalimat AC(3) lahir dari sini."""
        return self.n > 0 and self.status[RED] == 0 and self.status[UNSPOKEN] == 0


@dataclass
class Sweep:
    """Hasil satu sapuan + SELURUH provenance yang dibutuhkan untuk membantahnya."""

    db_path: Path
    until: datetime
    symbols: tuple[str, ...]
    params: Params
    max_snapshot_id: int | None = None
    rows_total: int = 0
    rows_until: int = 0
    max_id_until: int | None = None
    anchors_seen: int = 0
    anchors_used: int = 0
    anchors_scan_limited: int = 0
    anchors_after_until: int = 0
    anchors_unreadable_ts: int = 0
    anchors_no_bundle: int = 0
    deepest_scan: int = 0
    deepest_scan_at: str = ""
    skew_skipped_ids: set[int] = field(default_factory=set)
    skew_skipped_hits: int = 0
    window_first: str = ""
    window_last: str = ""
    tallies: dict[str, ItemTally] = field(default_factory=dict)
    anchors_per_symbol: Counter[str] = field(default_factory=Counter)
    log_future_rows: int = 0
    log_skew_rows: int = 0
    log_other: Counter[str] = field(default_factory=Counter)


# --- perataan nilai penyebab --------------------------------------------------------------
def _scalar(value: Any) -> str | None:
    """Bentuk teks untuk skalar JSON; None bila `value` bukan skalar."""
    if isinstance(value, str):
        text = value[:CAUSE_TEXT_LIMIT]
        return text + "…" if len(value) > CAUSE_TEXT_LIMIT else text
    if value is None or isinstance(value, bool | int | float):
        return json.dumps(value)
    return None


def _flatten(value: Any, prefix: str, depth: int, out: dict[str, str]) -> None:
    if isinstance(value, Mapping):
        if not value:
            if prefix:
                out[prefix] = "{}"
            return
        for raw_key, item in value.items():
            key = f"{prefix}.{raw_key}" if prefix else str(raw_key)
            if depth >= CAUSE_DEPTH:
                out[key] = "…"
            else:
                _flatten(item, key, depth + 1, out)
        return
    if isinstance(value, list | tuple):
        if not value:
            return
        parts = [_scalar(entry) for entry in value]
        if len(parts) <= CAUSE_LIST_LIMIT and all(part is not None for part in parts):
            out[prefix] = "[" + "|".join(part for part in parts if part is not None) + "]"
        else:
            out[prefix] = f"n={len(value)}"
        return
    scalar = _scalar(value)
    out[prefix] = scalar if scalar is not None else type(value).__name__


def cause_key(value: Any) -> str:
    """Sidik jari NILAI MENTAH penyebab merah, diratakan jadi satu baris `k=v, k=v`.

    Aturannya sengaja generik (tidak ada daftar field pilihan per item): apa pun yang dibawa
    `ItemResult.value` ikut, hanya bentuknya yang dipadatkan. Memilih "field penting" per item
    berarti menulis ulang penyebab veto dengan tangan — persis yang tidak boleh dilakukan alat
    ukur.
    """
    if value is None:
        return "null"
    flat: dict[str, str] = {}
    _flatten(value, "", 0, flat)
    if not flat:
        return "{}"
    return ", ".join(f"{key}={flat[key]}" for key in sorted(flat))


# --- pembacaan store (baca-saja) ----------------------------------------------------------
def _rows(store: Store, sql: str, params: Sequence[Any] = ()) -> list[Any]:
    """SELECT lewat koneksi `Store` yang SUDAH baca-saja.

    Sengaja memakai `_select` milik `Store` alih-alih membuka koneksi kedua: koneksi itu satu-
    satunya yang membawa ketiga lapis proteksi sekaligus (URI `mode=ro` ber-`quote`,
    `PRAGMA query_only=ON`, authorizer allowlist). Salinan lapisan itu di modul ini akan
    menjadi salinan yang bisa melenceng — dan yang melenceng dari pola baca-saja adalah persis
    temuan TINGGI TASKS 0.10.
    """
    return store._select(sql, params)


def parse_ts(raw: Any) -> datetime | None:
    """`ts_utc` kolom → datetime UTC; yang tidak terbaca jadi None (dicacah, bukan ditebak)."""
    if not isinstance(raw, str):
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def series_timestamps(store: Store, tool: str, args_hash: str) -> tuple[list[datetime], int]:
    """Seluruh `ts_utc` satu seri (tool, args_hash), menaik, + cacah baris yang tak terbaca."""
    stamps: list[datetime] = []
    unreadable = 0
    for (raw,) in _rows(
        store,
        "SELECT ts_utc FROM snapshots WHERE tool = ? AND args_hash = ?",
        (tool, args_hash),
    ):
        moment = parse_ts(raw)
        if moment is None:
            unreadable += 1
        else:
            stamps.append(moment)
    stamps.sort()
    return stamps, unreadable


def scan_depth(stamps: Sequence[datetime], unreadable: int, anchor: datetime, max_age_min: float) -> int:
    """Berapa baris ke bawah `latest_fresh` harus memindai seri ini untuk jangkar `anchor`.

    = baris yang lebih baru dari horizon masa depan (semuanya dibuang, tetapi tetap memakan
    slot pindai) + baris di dalam jendela kesegaran (kandidatnya) + baris ber-`ts_utc` tak
    terbaca (juga memakan slot). Lebih besar dari :data:`core.store.FRESH_SCAN_LIMIT` berarti
    kandidat terdalam berada DI LUAR ``LIMIT 64`` dan jawabannya bisa `unspoken` palsu.
    """
    horizon = anchor + timedelta(seconds=FUTURE_SKEW_TOLERANCE_S)
    cutoff = anchor - timedelta(minutes=max_age_min)
    newer = sum(1 for moment in stamps if moment > horizon)
    in_window = sum(1 for moment in stamps if cutoff <= moment <= horizon)
    return newer + in_window + unreadable


# --- sapuan -------------------------------------------------------------------------------
class _LogSink(logging.Handler):
    """Menampung log `core.*` selama sapuan: dicacah, bukan dicetak, bukan dibuang.

    Sapuan mundur membuat SETIAP jangkar melihat baris yang lebih baru dari dirinya, dan
    `core.store` mencatat satu warning untuk tiap baris seperti itu (TASKS 2.0d). Membiarkannya
    tercetak berarti ribuan baris kebisingan; membuangnya diam-diam berarti alat ukur yang
    menyembunyikan keberatan lapisan di bawahnya. Jadi: dicacah, dan yang BUKAN "baris masa
    depan" dicetak contohnya di header.
    """

    def __init__(self, sweep: Sweep) -> None:
        super().__init__(level=logging.WARNING)
        self.sweep = sweep

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - handler tidak boleh menjatuhkan sapuan
            message = record.msg if isinstance(record.msg, str) else "<unformattable>"
        if "di depan now" in message:
            self.sweep.log_future_rows += 1
        elif SKEW_NOTE.search(message):
            self.sweep.log_skew_rows += 1
        else:
            # Sisa kategori = kejutan. Contohnya dicetak di header, bukan ditelan.
            self.sweep.log_other[f"{record.name}: {message[:120]}"] += 1


def sweep(
    store: Store,
    *,
    until: datetime,
    symbols: Sequence[str] = CORE_SYMBOLS,
    params: Params = DEFAULT_PARAMS,
    intent: str = "swing",
) -> Sweep:
    """Jalankan jalur produksi atas tiap (simbol, jangkar) dan cacah hasilnya."""
    result = Sweep(db_path=store.path, until=until, symbols=tuple(symbols), params=params)
    result.tallies = {item.id: ItemTally(item_id=item.id) for item in ITEMS}

    row = _rows(store, "SELECT COUNT(*), MAX(id) FROM snapshots")
    result.rows_total, result.max_snapshot_id = (row[0][0], row[0][1]) if row else (0, None)
    counted = 0
    max_id_until: int | None = None
    for snapshot_id, raw_ts in _rows(store, "SELECT id, ts_utc FROM snapshots"):
        moment = parse_ts(raw_ts)
        if moment is not None and moment <= until:
            counted += 1
            if isinstance(snapshot_id, int) and (max_id_until is None or snapshot_id > max_id_until):
                max_id_until = snapshot_id
    result.rows_until, result.max_id_until = counted, max_id_until

    series_cache: dict[tuple[str, str], tuple[list[datetime], int]] = {}
    sink = _LogSink(result)
    core_log = logging.getLogger("core")
    core_log.addHandler(sink)
    previous = core_log.propagate
    core_log.propagate = False
    try:
        for symbol in symbols:
            args = tool_args(symbol, intent, params)
            hashes = {tool: sha256_hex(value) for tool, value in args.items()}
            for tool, args_hash in hashes.items():
                key = (tool, args_hash)
                if key not in series_cache:
                    series_cache[key] = series_timestamps(store, tool, args_hash)
            anchors, unreadable_anchor_rows = series_cache[(ANCHOR_TOOL, hashes[ANCHOR_TOOL])]
            result.anchors_unreadable_ts += unreadable_anchor_rows
            result.anchors_seen += len(anchors) + unreadable_anchor_rows
            for anchor in anchors:
                if anchor > until:
                    result.anchors_after_until += 1
                    continue
                depth = max(
                    scan_depth(*series_cache[(tool, args_hash)], anchor, params.max_age_min)
                    for tool, args_hash in hashes.items()
                )
                if depth > FRESH_SCAN_LIMIT:
                    result.anchors_scan_limited += 1
                    continue
                _observe(store, result, symbol, intent, anchor, params, depth)
    finally:
        core_log.removeHandler(sink)
        core_log.propagate = previous
    return result


def _observe(
    store: Store,
    result: Sweep,
    symbol: str,
    intent: str,
    anchor: datetime,
    params: Params,
    depth: int,
) -> None:
    """Satu amatan: `gather` → `evaluate_all` → cacah. Nol pemilihan bukti di sini."""
    gathered = gather(store, symbol, intent, params, now=anchor)
    for note in gathered.warnings:
        for found in SKEW_NOTE.finditer(note):
            result.skew_skipped_hits += 1
            result.skew_skipped_ids.add(int(found.group(1)))
    if not gathered.envelopes:
        result.anchors_no_bundle += 1
        return
    evidence = Evidence.from_envelopes(
        symbol, gathered.envelopes, params=params, snapshot_ids=gathered.snapshot_ids
    )
    for item in results_of(evaluate_all(evidence)):
        tally = result.tallies.get(item.id)
        if tally is None:  # pragma: no cover - id item di luar ITEMS tidak mungkin dari evaluate_all
            tally = result.tallies.setdefault(item.id, ItemTally(item_id=item.id))
        tally.add(item.status, item.value)
    result.anchors_used += 1
    result.anchors_per_symbol[symbol] += 1
    stamp = anchor.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if not result.window_first:
        result.window_first = stamp
    result.window_first = min(result.window_first, stamp)
    result.window_last = max(result.window_last, stamp)
    if depth > result.deepest_scan:
        result.deepest_scan = depth
        result.deepest_scan_at = f"{symbol} @ {stamp}"


# --- laporan ------------------------------------------------------------------------------
def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def render(result: Sweep, *, max_causes: int = 4) -> str:
    """Header provenance + tabel + distribusi nilai penyebab merah + kalimat AC(3)."""
    lines: list[str] = []
    add = lines.append
    add("tabel daya veto empiris per item — TASKS 2.1b (alat ukur, bukan alat putus)")
    add("")
    add("provenance")
    add(f"  --until               : {_iso(result.until)}")
    add(f"  db                    : {result.db_path} (read_only=True, PRAGMA query_only=ON)")
    add(f"  max(snapshots.id)     : {result.max_snapshot_id} (SELURUH db; bergerak — perekam hidup)")
    add(
        f"  baris <= --until      : {result.rows_until} dari {result.rows_total}; "
        f"max(id) <= --until = {result.max_id_until}"
    )
    add(
        f"  baris skew-skipped    : {len(result.skew_skipped_ids)} snapshot berbeda "
        f"({result.skew_skipped_hits} kali dibuang saat merakit; |ts_utc - as_of| > "
        f"{EVIDENCE_SKEW_TOLERANCE_S} dtk)"
    )
    add(f"  simbol                : {', '.join(result.symbols)} (jangkar = ts_utc tiap baris {ANCHOR_TOOL})")
    add(
        f"  jangkar               : {result.anchors_used} dipakai / {result.anchors_scan_limited} dibuang "
        f"batas pindai / {result.anchors_no_bundle} gagal merakit bundel / "
        f"{result.anchors_unreadable_ts} ts_utc tak terbaca / {result.anchors_after_until} setelah --until "
        f"(= {result.anchors_seen} baris jangkar)"
    )
    per_symbol = ", ".join(f"{symbol}={result.anchors_per_symbol[symbol]}" for symbol in result.symbols)
    add(f"  jangkar per simbol    : {per_symbol or '-'}")
    add(f"  jendela terpakai      : {result.window_first or '-'} .. {result.window_last or '-'}")
    add(
        f"  kedalaman pindai      : terdalam {result.deepest_scan} dari batas "
        f"{FRESH_SCAN_LIMIT} (core.store.FRESH_SCAN_LIMIT)"
        + (f", di {result.deepest_scan_at}" if result.deepest_scan_at else "")
    )
    add(f"  params_hash           : {result.params.params_hash()} (max_age_min={result.params.max_age_min})")
    add(
        f"  log core.* ditekan    : {result.log_future_rows} baris masa depan relatif jangkar "
        f"(normal untuk sapuan mundur) + {result.log_skew_rows} skew + "
        f"{sum(result.log_other.values())} lain"
    )
    for message, count in sorted(result.log_other.items(), key=lambda pair: (-pair[1], pair[0]))[:3]:
        add(f"      {count}x {message}")
    add("")

    add(f"{'item':<20}{'n':>5}{'green':>7}{'red':>6}{'unspoken':>10}  nilai penyebab merah")
    add("-" * 100)
    for item in ITEMS:
        tally = result.tallies[item.id]
        causes = tally.red_causes
        if not causes:
            column = "—  (tidak pernah merah)"
        else:
            top_count = causes.most_common(1)[0][1]
            column = f"{len(causes)} nilai berbeda; teratas {top_count}x (rincian di bawah)"
        add(
            f"{item.id:<20}{tally.n:>5}{tally.status[GREEN]:>7}{tally.status[RED]:>6}"
            f"{tally.status[UNSPOKEN]:>10}  {column}"
        )
    add("")

    add("nilai penyebab merah — MENTAH, dari ItemResult.value (dipotong per field, bukan dipilih)")
    any_red = False
    for item in ITEMS:
        tally = result.tallies[item.id]
        if not tally.red_causes:
            continue
        any_red = True
        add(f"  {item.id} ({tally.status[RED]}x merah, {len(tally.red_causes)} nilai berbeda)")
        for cause, count in sorted(tally.red_causes.items(), key=lambda pair: (-pair[1], pair[0]))[
            :max_causes
        ]:
            body = textwrap.wrap(cause, width=92) or [""]
            add(f"      {count:>4}x  {body[0]}")
            for extra in body[1:]:
                add(f"            {extra}")
        rest = sorted(tally.red_causes.values(), reverse=True)[max_causes:]
        if rest:
            add(f"      (+{len(rest)} nilai berbeda lagi, {sum(rest)} amatan)")
    if not any_red:
        add("  (tidak ada satu pun item merah dalam sapuan ini)")
    add("")

    add("item yang belum pernah menahan apa pun (0x merah DAN 0x unspoken atas n penuh)")
    silent = [item.id for item in ITEMS if result.tallies[item.id].never_held_back()]
    if silent:
        for item_id in silent:
            tally = result.tallies[item_id]
            add(f"  - {item_id}: belum pernah menahan apa pun dalam n={tally.n} amatan (green={tally.n})")
    else:
        add("  - (tidak ada: setiap item pernah merah atau pernah unspoken setidaknya sekali)")
    add("")

    add("item yang TIDAK PERNAH merah tetapi pernah diam (unspoken > 0 — diam bukan hijau)")
    quiet = [
        item.id
        for item in ITEMS
        if result.tallies[item.id].status[RED] == 0 and result.tallies[item.id].status[UNSPOKEN] > 0
    ]
    if quiet:
        for item_id in quiet:
            tally = result.tallies[item_id]
            add(
                f"  - {item_id}: 0x merah dalam n={tally.n} amatan, tetapi {tally.status[UNSPOKEN]}x "
                "tidak tersebut (bukti tidak ada / dibuang), jadi ia tidak memenuhi kalimat di atas"
            )
    else:
        add("  - (tidak ada)")
    add("")
    add(
        "catatan: 'merah' = item ini menahan keberangkatan, BUKAN bahwa menahan itu benar; "
        "apakah veto membayar adalah TASKS 3.3."
    )
    return "\n".join(lines)


# --- CLI ----------------------------------------------------------------------------------
def _parse_until(raw: str) -> datetime:
    text = raw.strip().replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m preflight.veto_table",
        description="Tabel daya veto empiris per item, ber-n (TASKS 2.1b). Baca-saja.",
    )
    parser.add_argument("--until", default=None, help="batas atas jangkar (ISO UTC); default: sekarang")
    parser.add_argument("--db", default=None, help="path DB; default: DB_PATH / data/ryo.db")
    parser.add_argument("--symbols", default=",".join(CORE_SYMBOLS), help="simbol inti, dipisah koma")
    parser.add_argument("--max-causes", type=int, default=4, help="nilai penyebab merah teratas per item")
    args = parser.parse_args(argv)

    if args.until is None:
        until = datetime.now(UTC)
    else:
        try:
            until = _parse_until(args.until)
        except ValueError:
            print(f"--until bukan waktu ISO yang terbaca: {args.until!r}", file=sys.stderr)
            return 2
    symbols = tuple(part.strip().upper() for part in args.symbols.split(",") if part.strip())
    if not symbols:
        print("--symbols kosong", file=sys.stderr)
        return 2

    try:
        store = Store(args.db, read_only=True)
    except StoreError as exc:
        print(f"tidak bisa membuka store baca-saja: {exc}", file=sys.stderr)
        return 2
    try:
        result = sweep(store, until=until, symbols=symbols)
    except StoreError as exc:
        print(f"gagal membaca store: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()
    print(render(result, max_causes=max(1, args.max_causes)))
    return 0


if __name__ == "__main__":  # pragma: no cover - dijalankan lewat `python -m`
    sys.exit(main())
