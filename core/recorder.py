"""Perekam snapshot RYO — orkestrator TIPIS di atas `core.ryo_client` + `core.store` (ADR-006).

Alasan modul ini ada: grading membutuhkan outcome T+24 jam, dan jam pasar yang terlewat
tidak bisa direkam ulang. Karena itu perekam berjalan terus, satu siklus per
``RECORDER_INTERVAL_S``, dan menyimpan setiap envelope APA ADANYA.

Yang SENGAJA tidak ada di berkas ini: HTTP (semua di :mod:`core.ryo_client`), SQL (semua di
:mod:`core.store`), cache, agregasi, dan "nilai sementara". Perekam hanya memilih apa yang
dipanggil, dalam urutan apa, lalu menyerahkannya untuk disimpan.

Satu siklus:

1. market-wide: ``market_overview`` + ``monitor_market_sentiment_shift``;
2. ``scan_market`` → watchlist = simbol inti (``WATCHLIST``) + kandidat hasil scan,
   dipotong ``RECORDER_MAX_SYMBOLS``;
3. per simbol: ``analyze_token`` + ``deep_analysis`` (``include_perp=True``);
4. follow-up ``analyze_token`` untuk simbol yang pernah direkam ≤26 jam, TIDAK ada di
   watchlist siklus ini, dan **belum punya rentang pengamatan ≥24 jam**. Satu-satunya alasan
   langkah 4 ada: tanpa pembacaan kedua, outcome T+24 jam untuk simbol yang keluar dari
   daftar momentum tidak bisa dinilai.

**Ratchet follow-up dan cara mematikannya.** Setiap follow-up menulis baris `analyze_token`
ber-`ts_utc` = sekarang, sehingga simbol itu selalu masih berada di jendela 26 jam pada siklus
berikutnya: himpunan follow-up hanya bisa MEMBESAR sampai sebesar universe. Itu terbukti pada
perekam sementara (`analyze_token` per siklus 29 → 42, durasi 936 → 1239 dtk, 32× HTTP 429
dalam sehari). Dua pengaman di modul ini membuat himpunan bisa MENYUSUT:

* **Pensiun.** Simbol yang sudah punya pembacaan berjarak ≥ ``FOLLOWUP_DONE_AFTER_H`` (24 jam)
  dikeluarkan: outcome T+24 jam-nya sudah bisa dihitung, memanggilnya lagi hanya membakar
  kuota. Jadi tiap simbol diikuti paling lama ~24 jam sejak kemunculan pertamanya.
* **Anggaran.** ``RECORDER_MAX_FOLLOWUPS`` per siklus, dipotong SETELAH pengurutan "yang
  pembacaan terakhirnya paling lama duluan", sehingga yang paling mendesak tetap didahulukan.

Sifat lain yang dijaga tes (``tests/test_recorder.py``):

* **Kegagalan per komponen (ADR-004).** Satu tool yang melempar :class:`RyoError` dicatat
  lalu dilewati; tool lain di siklus yang sama tetap terekam. Tidak ada envelope palsu,
  tidak ada nilai default, tidak ada baris kosong sebagai pengganti data yang gagal.
* **:class:`RyoProtocolError` dicatat KERAS.** Envelope di luar bentuk api-facts §A
  (mis. `status` baru yang belum terdaftar) berarti 100% data tool itu hilang sampai
  api-facts diperbarui — kegagalan seperti itu tidak boleh terlihat seperti gangguan
  jaringan biasa.
* **Satu siklus gagal tidak menghentikan loop, tetapi kegagalan BERUNTUN menghentikannya.**
  Setelah ``RECORDER_MAX_FAILED_CYCLES`` siklus berturut-turut tanpa satu pun snapshot,
  perekam keluar dengan kode ≠ 0. Berkas DB yang dihapus (satu-satunya cara membuang data
  menurut ADR-006) membuat setiap tulisan gagal selamanya; berputar terus dalam keadaan itu
  berarti membakar kuota RYO dan membuang envelope-nya, sementara `daemons.sh status` tetap
  melaporkan "JALAN". Berisik lalu berhenti lebih jujur daripada pura-pura pulih.
* **Satu instance per mesin.** ``main()`` memegang ``flock`` pada ``logs/recorder.lock``;
  instance kedua keluar dengan kode ≠ 0. Dua perekam = dua token bucket atas satu kunci =
  2×60 panggilan/menit, yaitu cara paling cepat menghabiskan kuota. (POSIX; repo ini
  Linux/WSL, lihat docs/versions.md.)
* **Restart-safe.** Tidak ada state selain isi DB dan berkas kunci: daftar follow-up dihitung
  ulang dari tabel ``snapshots`` setiap siklus, jadi `kill -9` + start ulang tidak kehilangan
  apa pun selain sisa siklus yang sedang berjalan.
* **Commit per snapshot.** :meth:`core.store.Store.insert_snapshot` commit per baris, jadi
  siklus yang terpotong di tengah tetap menyisakan semua baris yang sudah diambil.
* **Nol kredensial di log.** Perekam tidak pernah memegang kunci (kunci hanya hidup di
  dalam :class:`core.ryo_client.RyoClient`), untuk exception yang BUKAN milik kita hanya nama
  tipenya yang dicatat, dan ``main()`` menahan logger pihak ketiga (`httpx`, `httpcore`) di
  level INFO supaya ``LOG_LEVEL=DEBUG`` tidak pernah mencetak header respons mentah.

Klien dibentuk lewat :meth:`core.ryo_client.RyoClient.from_env`, jadi perekam otomatis
tunduk pada gate sumber ADR-005 (`sources.yaml`), token bucket 60/menit BERSAMA per kunci,
retry/backoff, dan redaksi kredensial.

Pakai::

    uv run python -m core.recorder                      # loop, interval dari env
    uv run python -m core.recorder --once                # satu siklus lalu keluar
    DB_PATH=/tmp/t.db uv run python -m core.recorder --once --client fixture   # offline
"""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import re
import sys
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from core.ryo_client import BaseRyoClient, FixtureClient, RyoClient, RyoError, RyoProtocolError
from core.sources import SourcesError
from core.store import Store, StoreError

log = logging.getLogger("core.recorder")

ROOT = Path(__file__).resolve().parent.parent

#: Simbol inti bila `WATCHLIST` tidak diset (sama dengan .env.example).
CORE_SYMBOLS: tuple[str, ...] = ("SOL", "BTC", "ETH", "BNB")

ENV_WATCHLIST = "WATCHLIST"
ENV_MAX_SYMBOLS = "RECORDER_MAX_SYMBOLS"
ENV_INTERVAL_S = "RECORDER_INTERVAL_S"
ENV_MAX_FOLLOWUPS = "RECORDER_MAX_FOLLOWUPS"
ENV_MAX_FAILED_CYCLES = "RECORDER_MAX_FAILED_CYCLES"
ENV_LOCK_PATH = "RECORDER_LOCK_PATH"

DEFAULT_MAX_SYMBOLS = 20
DEFAULT_INTERVAL_S = 3600.0
#: Batas atas interval. Tanpa ini, salah ketik `1e9` membuat perekam tidur 31 tahun sementara
#: `daemons.sh status` tetap melaporkan "JALAN" — kegagalan paling senyap yang mungkin ada.
MAX_INTERVAL_S = 86_400.0
#: Anggaran follow-up per siklus (lihat "Ratchet follow-up" di docstring modul).
DEFAULT_MAX_FOLLOWUPS = 20
#: Siklus beruntun tanpa satu pun snapshot sebelum perekam menyerah dengan kode ≠ 0.
DEFAULT_MAX_FAILED_CYCLES = 3

#: Umur maksimum sebuah simbol untuk ikut follow-up. 26 jam, bukan 24: siklus per jam butuh
#: kelonggaran agar pembacaan T+24 jam tidak terlewat gara-gara satu siklus yang molor.
FOLLOWUP_MAX_AGE_H = 26.0
#: Rentang pengamatan yang membuat kewajiban follow-up sebuah simbol SELESAI.
FOLLOWUP_DONE_AFTER_H = 24.0
#: Sejauh mana ke belakang pembacaan lama dicari saat memeriksa "sudah selesai" (7 hari).
FOLLOWUP_DONE_LOOKBACK_H = 7 * 24.0

#: Batas panjang simbol yang diterima dari `scan_market`. Simbol RYO pendek (lihat
#: docs/samples/scan_market.json); nilai aneh dilewati dan dicatat, bukan dikirim ke API.
MAX_SYMBOL_LEN = 64
#: Kategori Unicode yang ditolak pada simbol: kontrol (Cc), format tak terlihat (Cf — termasuk
#: U+202E yang membalik arah teks di kartu panggilan UI), dan surrogate (Cs — tidak bisa
#: di-encode UTF-8, jadi akan menggagalkan `canonical_json`). Non-ASCII yang SAH tetap lolos:
#: `牛来` adalah simbol RYO nyata dan sudah punya baris di DB perekam.
FORBIDDEN_SYMBOL_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})

#: Nama variabel env yang sah (bentuk POSIX) — dipakai `load_dotenv`.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Argumen per tool ditulis sebagai dict literal di `Recorder.run_cycle` dan dikirim lewat
# `client.call(tool, args)` — bukan lewat metode bertipe — supaya argumen yang DIKIRIM dan
# argumen yang DISIMPAN adalah objek yang sama, sehingga `args_hash` baris baru sebangun
# dengan baris lama di DB perekam (`{}`, `{"top_n":21}`, `{"symbol":"SOL"}`,
# `{"include_perp":true,"symbol":"SOL"}`).


class RecorderConfigError(Exception):
    """Konfigurasi env tidak sah — perekam berhenti dengan pesan, bukan diam-diam menebak."""


class RecorderLockError(Exception):
    """Perekam lain sudah berjalan di mesin ini (lihat penjaga instance-tunggal di `main`)."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_symbol(raw: Any) -> str | None:
    """Simbol yang bisa dipakai, atau None (dengan alasan dicatat pemanggil).

    Ditolak: bukan str, kosong, terlalu panjang, mengandung spasi, atau mengandung karakter
    kontrol/format/surrogate. Simbol seperti itu hanya membakar kuota (API menolaknya),
    ikut ratchet follow-up selamanya, dan bisa mendarat di kartu panggilan UI.
    """
    if not isinstance(raw, str):
        return None
    symbol = raw.strip().upper()
    if not symbol or len(symbol) > MAX_SYMBOL_LEN or any(ch.isspace() for ch in symbol):
        return None
    if any(unicodedata.category(ch) in FORBIDDEN_SYMBOL_CATEGORIES for ch in symbol):
        return None
    return symbol


def _positive_int(raw: Any, label: str, *, maximum: int | None = None) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise RecorderConfigError(f"{label} harus bilangan bulat, dapat {str(raw)[:32]!r}") from None
    if value <= 0:
        raise RecorderConfigError(f"{label} harus > 0, dapat {value}")
    if maximum is not None and value > maximum:
        raise RecorderConfigError(f"{label} harus ≤ {maximum}, dapat {value}")
    return value


def _positive_float(raw: Any, label: str, *, maximum: float | None = None) -> float:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        raise RecorderConfigError(f"{label} harus angka, dapat {str(raw)[:32]!r}") from None
    if value != value or value in (float("inf"), float("-inf")) or value <= 0:
        raise RecorderConfigError(f"{label} harus angka positif berhingga, dapat {value}")
    if maximum is not None and value > maximum:
        raise RecorderConfigError(f"{label} harus ≤ {maximum:.0f}, dapat {value}")
    return value


@dataclass(frozen=True)
class RecorderConfig:
    """Semua tombol perekam. Dibaca dari env satu kali; tidak ada default yang disembunyikan."""

    watchlist: tuple[str, ...] = CORE_SYMBOLS
    max_symbols: int = DEFAULT_MAX_SYMBOLS
    interval_s: float = DEFAULT_INTERVAL_S
    max_followups: int = DEFAULT_MAX_FOLLOWUPS
    max_failed_cycles: int = DEFAULT_MAX_FAILED_CYCLES
    followup_max_age_h: float = FOLLOWUP_MAX_AGE_H
    followup_done_after_h: float = FOLLOWUP_DONE_AFTER_H

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RecorderConfig:
        """Baca WATCHLIST / RECORDER_MAX_SYMBOLS / RECORDER_INTERVAL_S / RECORDER_MAX_FOLLOWUPS
        / RECORDER_MAX_FAILED_CYCLES.

        Nilai yang tidak terbaca → :class:`RecorderConfigError`. Perekam sengaja TIDAK
        mundur ke default diam-diam: interval yang salah ketik akan menghabiskan kuota.
        """
        source: Mapping[str, str] = os.environ if env is None else env
        raw_watchlist = source.get(ENV_WATCHLIST, "")
        watchlist: list[str] = []
        for chunk in raw_watchlist.split(","):
            symbol = _normalize_symbol(chunk)
            if symbol is not None and symbol not in watchlist:
                watchlist.append(symbol)
        if raw_watchlist.strip() and not watchlist:
            raise RecorderConfigError(f"{ENV_WATCHLIST} tidak memuat satu pun simbol yang sah")
        return cls(
            watchlist=tuple(watchlist) if watchlist else CORE_SYMBOLS,
            max_symbols=_positive_int(source.get(ENV_MAX_SYMBOLS, DEFAULT_MAX_SYMBOLS), ENV_MAX_SYMBOLS),
            interval_s=_positive_float(
                source.get(ENV_INTERVAL_S, DEFAULT_INTERVAL_S), ENV_INTERVAL_S, maximum=MAX_INTERVAL_S
            ),
            max_followups=_positive_int(
                source.get(ENV_MAX_FOLLOWUPS, DEFAULT_MAX_FOLLOWUPS), ENV_MAX_FOLLOWUPS
            ),
            max_failed_cycles=_positive_int(
                source.get(ENV_MAX_FAILED_CYCLES, DEFAULT_MAX_FAILED_CYCLES), ENV_MAX_FAILED_CYCLES
            ),
        )


@dataclass
class CycleReport:
    """Hasil satu siklus — dipakai log dan tes; bukan bagian dari data yang disimpan."""

    recorded: dict[str, int] = field(default_factory=dict)
    failed: dict[str, int] = field(default_factory=dict)
    snapshot_ids: list[int] = field(default_factory=list)
    symbols: tuple[str, ...] = ()
    followups: tuple[str, ...] = ()

    @property
    def n_recorded(self) -> int:
        return len(self.snapshot_ids)

    @property
    def n_failed(self) -> int:
        return sum(self.failed.values())

    @property
    def is_total_failure(self) -> bool:
        """Tidak satu pun snapshot tersimpan padahal ada yang dicoba."""
        return self.n_recorded == 0 and self.n_failed > 0

    def note_success(self, tool: str, snapshot_id: int) -> None:
        self.recorded[tool] = self.recorded.get(tool, 0) + 1
        self.snapshot_ids.append(snapshot_id)

    def note_failure(self, tool: str) -> None:
        self.failed[tool] = self.failed.get(tool, 0) + 1


@dataclass(frozen=True)
class RunSummary:
    """Hasil :meth:`Recorder.run` — `aborted` = perekam menyerah, bukan selesai normal."""

    cycles: int
    recorded: int
    consecutive_failures: int
    aborted: bool


class Recorder:
    """Satu siklus = beberapa panggilan tool; setiap envelope langsung disimpan.

    `client` cukup memenuhi :class:`core.ryo_client.BaseRyoClient`, jadi tes memakai
    :class:`core.ryo_client.FixtureClient` dan tidak pernah menyentuh jaringan. `now_fn`
    disuntik tes untuk mensimulasikan berhari-hari siklus tanpa menunggu.
    """

    def __init__(
        self,
        client: BaseRyoClient,
        store: Store,
        config: RecorderConfig | None = None,
        *,
        now_fn: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._client = client
        self._store = store
        self.config = config or RecorderConfig()
        self._now_fn = now_fn

    def __repr__(self) -> str:
        """Tidak memuat kredensial: perekam tidak pernah memegang kunci."""
        return (
            f"Recorder(client={type(self._client).__name__}, store={self._store!r}, "
            f"max_symbols={self.config.max_symbols})"
        )

    # -- satu panggilan -------------------------------------------------------------------
    def _record(self, tool: str, args: dict[str, Any], report: CycleReport) -> dict[str, Any] | None:
        """Panggil satu tool dan simpan envelope-nya; gagal → None (siklus tetap lanjut).

        `args` yang dikirim ke klien dan yang disimpan ke store adalah objek YANG SAMA,
        sehingga `args_hash` selalu menggambarkan permintaan yang benar-benar dikirim.
        """
        label = f"{tool}{_args_label(args)}"
        try:
            envelope = self._client.call(tool, args)
        except RyoProtocolError as exc:
            # Bentuk envelope di luar api-facts §A: SELURUH data tool ini akan hilang sampai
            # api-facts §A diperbarui. Jangan pernah sekadar warning seperti hiccup jaringan.
            report.note_failure(tool)
            log.error(
                "%s: envelope di luar bentuk api-facts §A — %s; data tool ini TIDAK terekam "
                "sampai api-facts/klien diperbarui",
                label,
                exc,
            )
            return None
        except RyoError as exc:
            # Pesan RyoError dibentuk core/ryo_client.py dan melewati redaksi di KELIMA jalur
            # teks yang dikendalikan server (tes regresi di sana, diparametrisasi per jalur).
            # Klaim ini pernah SALAH: `result.status`/`result.tool` dulu tidak diredaksi dan
            # bocor ke berkas log ini lewat HTTP 200 (security-reviewer, TASKS 1.5).
            report.note_failure(tool)
            log.warning("%s: panggilan gagal — %s: %s", label, type(exc).__name__, exc)
            return None
        except Exception as exc:  # noqa: BLE001 — kontrak klien menjanjikan RyoError; apa pun
            # di luar itu adalah bug, dan bug tidak boleh mematikan perekaman tool lain.
            # Hanya NAMA TIPE yang dicatat: pesan exception pihak ketiga belum tentu bebas
            # kredensial, dan perekam tidak punya kunci untuk meredaksinya.
            report.note_failure(tool)
            log.error(
                "%s: kegagalan tak terduga dari klien (%s) — pesan sengaja tidak dilog",
                label,
                type(exc).__name__,
            )
            return None

        try:
            snapshot_id = self._store.insert_snapshot(tool, args, envelope)
        except StoreError as exc:
            report.note_failure(tool)
            log.error("%s: envelope diterima tapi gagal disimpan — %s", label, exc)
            return None
        except Exception as exc:  # noqa: BLE001 — sama alasannya dengan blok di atas
            report.note_failure(tool)
            log.error("%s: gagal menyimpan (%s) — pesan sengaja tidak dilog", label, type(exc).__name__)
            return None

        report.note_success(tool, snapshot_id)
        log.debug("%s: snapshot %d status=%s", label, snapshot_id, envelope.get("status"))
        return envelope

    # -- pemilihan simbol -----------------------------------------------------------------
    def _scan_candidates(self, envelope: Mapping[str, Any], exclude: set[str]) -> list[str]:
        """Simbol kandidat dari envelope `scan_market`; bentuk tak terduga → daftar kosong."""
        data = envelope.get("data")
        if not isinstance(data, Mapping):
            log.warning("scan_market: envelope tanpa objek 'data'; tidak ada kandidat siklus ini")
            return []
        raw = data.get("candidates")
        if not isinstance(raw, list):
            log.warning("scan_market: 'data.candidates' bukan daftar; tidak ada kandidat siklus ini")
            return []
        out: list[str] = []
        seen = set(exclude)
        skipped = 0
        for item in raw:
            symbol = _normalize_symbol(item.get("symbol")) if isinstance(item, Mapping) else None
            if symbol is None:
                skipped += 1
                continue
            if symbol in seen:
                continue
            seen.add(symbol)
            out.append(symbol)
        if skipped:
            log.warning("scan_market: %d kandidat tanpa simbol yang bisa dipakai, dilewati", skipped)
        return out

    def _cycle_symbols(self, report: CycleReport) -> list[str]:
        """Simbol inti + kandidat scan, dipotong `RECORDER_MAX_SYMBOLS`.

        `scan_market` yang gagal TIDAK membatalkan siklus: simbol inti tetap direkam.
        """
        symbols = list(self.config.watchlist)
        top_n = max(1, self.config.max_symbols - len(symbols))
        envelope = self._record("scan_market", {"top_n": top_n}, report)
        if envelope is not None:
            symbols.extend(self._scan_candidates(envelope, set(symbols)))
        return symbols[: self.config.max_symbols]

    def _symbols_from_args(self, rows: list[dict[str, Any]]) -> list[str]:
        out: list[str] = []
        for args in rows:
            symbol = _normalize_symbol(args.get("symbol"))
            if symbol is not None:
                out.append(symbol)
        return out

    def _followup_symbols(self, exclude: set[str]) -> list[str]:
        """Simbol yang masih butuh pembacaan kedua untuk outcome T+24 jam.

        State-nya HANYA tabel `snapshots` (restart-safe). Tiga saringan, berurutan:

        1. punya `analyze_token` ≤ `followup_max_age_h` (26 j) — di luar itu, pembacaan kedua
           sudah terlambat dan tidak bisa dipasangkan;
        2. tidak ada di watchlist siklus ini (sudah dipanggil di langkah 3);
        3. **rentang pengamatannya belum ≥ `followup_done_after_h` (24 j)**. Rentang diuji
           sebagai irisan dua himpunan: punya pembacaan BARU (<24 j) DAN punya pembacaan LAMA
           (≥24 j). Simbol yang memenuhi keduanya sudah bisa dinilai T+24 jam, jadi pensiun.
           Irisan — bukan sekadar "punya pembacaan ≥24 j" — karena simbol yang HANYA punya
           satu baris berumur 25 jam justru yang paling butuh pembacaan kedua sekarang.
           Tanpa saringan ini himpunan follow-up hanya bisa membesar (lihat docstring modul).

        Urutannya "yang pembacaan terakhirnya paling lama, duluan": simbol yang hampir keluar
        dari jendela 26 jam paling mendesak, jadi anggaran per siklus memotong dari ekor.
        """
        now = self._now_fn()
        done_after_min = self.config.followup_done_after_h * 60.0
        try:
            in_window = self._store.recent_args(
                "analyze_token", self.config.followup_max_age_h * 60.0, now=now
            )
            fresh = self._store.recent_args("analyze_token", done_after_min, now=now)
            settled = self._store.recent_args(
                "analyze_token",
                FOLLOWUP_DONE_LOOKBACK_H * 60.0,
                min_age_min=done_after_min,
                now=now,
            )
        except StoreError as exc:
            log.warning("follow-up dilewati: gagal membaca snapshot terakhir — %s", exc)
            return []

        done = set(self._symbols_from_args(fresh)) & set(self._symbols_from_args(settled))
        out: list[str] = []
        seen = set(exclude)
        for symbol in self._symbols_from_args(in_window):  # store mengembalikan yang terbaru dulu
            if symbol in seen or symbol in done:
                continue
            seen.add(symbol)
            out.append(symbol)
        out.reverse()  # yang pembacaan terakhirnya paling lama = paling mendesak

        budget = self.config.max_followups
        if len(out) > budget:
            log.warning(
                "follow-up: %d simbol membutuhkan pembacaan kedua, dipotong ke anggaran %s=%d; "
                "sisanya menunggu siklus berikutnya",
                len(out),
                ENV_MAX_FOLLOWUPS,
                budget,
            )
            out = out[:budget]
        log.info(
            "follow-up: %d di jendela %.0fj, %d sudah punya rentang ≥%.0fj (pensiun), %d dipanggil",
            len(in_window),
            self.config.followup_max_age_h,
            len(done),
            self.config.followup_done_after_h,
            len(out),
        )
        return out

    # -- siklus & loop --------------------------------------------------------------------
    def run_cycle(self) -> CycleReport:
        """Satu siklus penuh. Tidak melempar untuk kegagalan tool — lihat :meth:`_record`."""
        report = CycleReport()
        self._record("market_overview", {}, report)
        self._record("monitor_market_sentiment_shift", {}, report)

        symbols = self._cycle_symbols(report)
        report.symbols = tuple(symbols)
        for symbol in symbols:
            self._record("analyze_token", {"symbol": symbol}, report)
            self._record("deep_analysis", {"symbol": symbol, "include_perp": True}, report)

        followups = self._followup_symbols(set(symbols))
        report.followups = tuple(followups)
        for symbol in followups:
            self._record("analyze_token", {"symbol": symbol}, report)
        return report

    def run(
        self,
        *,
        max_cycles: int | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> RunSummary:
        """Loop perekaman. `max_cycles=1` = mode `--once`.

        `sleep_fn`/`time_fn` disuntik supaya tes tidak pernah benar-benar tidur. Loop hanya
        berhenti karena tiga sebab, semuanya dicatat: jumlah siklus tercapai,
        `RECORDER_MAX_FAILED_CYCLES` siklus beruntun tanpa snapshot, atau `sleep_fn` gagal
        (berputar tanpa jeda akan membakar kuota lebih cepat daripada berhenti).
        """
        total = 0
        cycles = 0
        consecutive = 0
        while True:
            started = time_fn()
            try:
                report = self.run_cycle()
            except Exception as exc:  # noqa: BLE001 — pagar terakhir: satu siklus yang meledak
                # tidak boleh menghentikan perekaman. Nama tipe saja (lihat docstring modul).
                log.error("siklus gagal seluruhnya (%s); lanjut ke siklus berikutnya", type(exc).__name__)
                consecutive += 1
            else:
                total += report.n_recorded
                consecutive = consecutive + 1 if report.is_total_failure else 0
                log.info(
                    "siklus selesai: %d snapshot %s; gagal %d %s; simbol=%d follow-up=%d durasi=%.0fs",
                    report.n_recorded,
                    report.recorded,
                    report.n_failed,
                    report.failed,
                    len(report.symbols),
                    len(report.followups),
                    time_fn() - started,
                )
            cycles += 1

            if consecutive >= self.config.max_failed_cycles:
                log.error(
                    "%d siklus berturut-turut tanpa satu pun snapshot tersimpan; perekam berhenti "
                    "agar kegagalan terlihat (berkas DB hilang/tidak bisa ditulis? host RYO mati?)",
                    consecutive,
                )
                return RunSummary(cycles, total, consecutive, aborted=True)
            if max_cycles is not None and cycles >= max_cycles:
                return RunSummary(cycles, total, consecutive, aborted=False)

            delay = max(1.0, self.config.interval_s - (time_fn() - started))
            try:
                sleep_fn(delay)
            except Exception as exc:  # noqa: BLE001 — sleep yang gagal (mis. nilai di luar
                # jangkauan OS) tidak boleh berubah menjadi loop tanpa jeda.
                log.error(
                    "gagal menunggu %.0f dtk antar siklus (%s); perekam berhenti daripada "
                    "berputar tanpa jeda",
                    delay,
                    type(exc).__name__,
                )
                return RunSummary(cycles, total, consecutive, aborted=True)


def _args_label(args: Mapping[str, Any]) -> str:
    """Label singkat untuk log: simbol saja, tanpa isi envelope."""
    symbol = args.get("symbol")
    return f"[{symbol}]" if isinstance(symbol, str) else ""


def _dotenv_value(raw: str) -> str:
    """Nilai satu baris berkas env, meniru `set -a; . ./.env` untuk bentuk yang lazim.

    Nilai berkutip diambil utuh (termasuk `#` dan spasi di dalamnya); tanpa kutip, ` #`
    memulai komentar. `#` yang menempel pada karakter lain BUKAN komentar: memotong
    kredensial diam-diam adalah kegagalan yang paling mahal untuk didiagnosis.
    """
    value = raw.strip()
    if value[:1] in ('"', "'"):
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:] if end == -1 else value[1:end]
    head, sep, _ = value.partition(" #")
    return head.strip() if sep else value


def load_dotenv(path: str | Path | None = None) -> None:
    """Isi variabel env dari berkas `.env` untuk yang BELUM diset (dipakai `make record`).

    Nilai tidak pernah dicetak/dilog. Variabel yang sudah ada di environment menang, jadi
    `scripts/daemons.sh` (yang sudah mem-`source` .env) tidak terpengaruh. Bila satu nama
    muncul dua kali, baris TERAKHIR menang — sama dengan `set -a; . ./.env`, supaya
    `make record` dan daemon tidak pernah memakai kredensial yang berbeda.
    """
    env_path = Path(path) if path is not None else ROOT / ".env"
    try:
        # utf-8-sig: berkas .env yang disunting editor Windows berawalan BOM, dan tanpa ini
        # variabel pertama akan hilang dengan senyap (namanya menjadi "﻿NAMA").
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    except UnicodeDecodeError:
        log.warning("berkas env %s bukan UTF-8; dilewati", env_path)
        return

    pending: dict[str, str] = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        entry = line.strip()
        if entry.startswith("export "):
            entry = entry[len("export ") :].strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        name, _, raw = entry.partition("=")
        name = name.strip()
        if not _ENV_NAME_RE.match(name):
            log.warning("berkas env baris %d: nama variabel tidak sah, dilewati", lineno)
            continue
        value = _dotenv_value(raw)
        if "\x00" in value or "\x00" in name:
            # Nilai berisi NUL akan membuat os.environ melempar ValueError; nama variabelnya
            # boleh dicatat, isinya tidak pernah.
            log.warning("berkas env baris %d: nilai %s memuat NUL, dilewati", lineno, name)
            continue
        pending[name] = value

    for name, value in pending.items():
        if name in os.environ:
            continue
        try:
            os.environ[name] = value
        except (ValueError, OSError):
            log.warning("berkas env: %s tidak bisa dipasang ke environment", name)


def acquire_single_instance_lock(path: str | Path | None = None) -> IO[str]:
    """Kunci `flock` non-blocking; instance kedua → :class:`RecorderLockError`.

    Dua perekam atas satu kunci API = dua token bucket = 2×60 panggilan/menit, yaitu cara
    tercepat menghabiskan kuota (dan sumber 429 yang sudah terlihat di log). Berkas PID saja
    tidak cukup: `scripts/daemons.sh` menyimpan pid pembungkus `uv run`, bukan pid proses
    Python yang benar-benar memanggil RYO. Kunci dilepas otomatis saat proses berakhir.
    """
    raw = path if path is not None else os.environ.get(ENV_LOCK_PATH) or ROOT / "logs" / "recorder.lock"
    lock_path = Path(raw)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
    except OSError as exc:
        raise RecorderLockError(f"tidak bisa membuka kunci {lock_path}: {exc}") from None
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RecorderLockError(
            f"perekam lain sudah memegang {lock_path}; menjalankan dua perekam berarti "
            "dua kali laju panggilan atas satu kunci API"
        ) from None
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
    except OSError:  # menulis pid hanya untuk kenyamanan; kuncinya sudah dipegang
        log.warning("kunci %s tidak bisa ditulisi; kunci tetap dipegang", lock_path)
    return handle


def build_client(kind: str = "live") -> BaseRyoClient:
    """`live` → :meth:`RyoClient.from_env` (gate ADR-005 + bucket per-kunci); `fixture` → offline."""
    if kind == "fixture":
        return FixtureClient()
    if kind != "live":
        raise RecorderConfigError(f"jenis klien tidak dikenal: {kind!r}")
    return RyoClient.from_env()


def _close(obj: Any) -> None:
    closer = getattr(obj, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception as exc:  # noqa: BLE001 — penutupan tidak boleh menutupi hasil siklus
            log.warning("gagal menutup %s (%s)", type(obj).__name__, type(exc).__name__)


def _configure_logging(level_name: str) -> None:
    """basicConfig + tahan logger pihak ketiga di INFO.

    `logging.basicConfig` menyetel level ROOT. Pada DEBUG, `httpx`/`httpcore` mencetak header
    respons MENTAH — termasuk header yang bisa memantulkan kredensial — tanpa melewati
    redaksi di `core/ryo_client.py`. Perekam berumur panjang dan lognya disimpan, jadi jalur
    itu ditutup di sini, bukan diserahkan ke kedisiplinan operator.
    """
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # `basicConfig` DIAM SAJA bila root sudah punya handler (supervisor yang meng-embed kita,
    # pytest). Level root karena itu disetel eksplisit supaya `--log-level` selalu berarti.
    logging.getLogger().setLevel(level)
    for noisy in ("httpx", "httpcore", "hpack", "anthropic"):
        logging.getLogger(noisy).setLevel(max(level, logging.INFO))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m core.recorder",
        description="Perekam snapshot tool RYO (SIMULASI, read-only).",
    )
    parser.add_argument("--once", action="store_true", help="satu siklus lalu keluar")
    parser.add_argument("--interval", type=float, default=None, help="detik antar siklus (default: env)")
    parser.add_argument("--db", default=None, help="path SQLite (default: env DB_PATH)")
    parser.add_argument(
        "--client",
        choices=("live", "fixture"),
        default="live",
        help="live = tool RYO sungguhan; fixture = docs/samples (offline, tanpa kredensial)",
    )
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Exit code: 0 = ada snapshot terekam; 1 = tidak menghasilkan apa pun / menyerah;
    2 = tidak bisa mulai (konfigurasi, kredensial, store, atau perekam lain sudah jalan)."""
    args = _parse_args(argv)
    _configure_logging(args.log_level)
    load_dotenv()

    try:
        config = RecorderConfig.from_env()
        if args.interval is not None:
            config = replace(
                config, interval_s=_positive_float(args.interval, "--interval", maximum=MAX_INTERVAL_S)
            )
    except RecorderConfigError as exc:
        log.error("konfigurasi perekam tidak sah: %s", exc)
        return 2

    try:
        lock = acquire_single_instance_lock()
    except RecorderLockError as exc:
        log.error("perekam tidak dimulai: %s", exc)
        return 2

    try:
        client = build_client(args.client)
    except (RecorderConfigError, RyoError, SourcesError) as exc:
        # Pesan ketiga exception ini dibuat oleh kode kita sendiri dan bebas kredensial.
        log.error("klien RYO tidak bisa dibentuk — %s: %s", type(exc).__name__, exc)
        _close(lock)
        return 2

    try:
        store = Store(args.db)
    except StoreError as exc:
        log.error("store tidak bisa dibuka — %s", exc)
        _close(client)
        _close(lock)
        return 2

    log.info(
        "perekam mulai: db=%s client=%s interval=%.0fs max_symbols=%d max_followups=%d watchlist=%s",
        store.path,
        args.client,
        config.interval_s,
        config.max_symbols,
        config.max_followups,
        ",".join(config.watchlist),
    )
    recorder = Recorder(client, store, config)
    try:
        summary = recorder.run(max_cycles=1 if args.once else None)
    except KeyboardInterrupt:
        log.info("perekam berhenti atas permintaan pengguna")
        return 0
    finally:
        _close(store)
        _close(client)
        _close(lock)
    if summary.aborted:
        return 1
    if summary.recorded == 0:
        log.error("siklus selesai tanpa satu pun snapshot; lihat peringatan di atas")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
