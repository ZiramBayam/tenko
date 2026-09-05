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
4. ``compare_tokens`` untuk simbol **watchlist** saja (bukan kandidat scan), dipotong
   ``MAX_COMPARES_PER_CYCLE`` — simbol yang terpotong disebut namanya di WARNING dan di
   ``CycleReport.compares_dropped``;
5. follow-up ``analyze_token`` untuk simbol yang pernah direkam ≤26 jam, TIDAK ada di
   watchlist siklus ini, dan **belum punya rentang pengamatan ≥24 jam**. Satu-satunya alasan
   langkah 5 ada: tanpa pembacaan kedua, outcome T+24 jam untuk simbol yang keluar dari
   daftar momentum tidak bisa dinilai.

**Kenapa langkah 4 ada (TASKS 2.0c).** Perekam dulu memanggil 5 tool dan tidak pernah
``compare_tokens``; 14 baris ``compare_tokens`` di ``data/ryo.db`` adalah warisan sesi manual
1 Sep dengan pembanding BTC+ETH, sedangkan preflight mencari pembanding BNB+ETH. Akibatnya
item 10 (WAJIB) selalu ``unspoken`` dan setiap keputusan NO_GO — satu dari lima tool tidak
pernah ikut menyatu ke keputusan. Bentuk argumennya dikunci: ``{"symbols": "<SIMBOL>[, BNB]
[, ETH]", "intent": "swing"}``, dengan dedupe berurutan terhadap pembanding (``"BNB"`` tidak
dibandingkan dengan dirinya sendiri). Kesamaan bentuk dengan pembaca store dijaga oleh TES
(``tests/test_recorder.py``), BUKAN oleh impor: ``core/`` tidak boleh mengimpor ``preflight/``
(ADR-002). Biaya terukur: +4 panggilan × ~42 dtk ≈ +170 dtk per siklus, jauh di bawah
``RECORDER_INTERVAL_S`` = 3600.

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
* **Satu instance per mesin.** ``main()`` memegang ``flock`` pada ``logs/recorder.lock`` lewat
  :func:`core.livelock.acquire_single_instance_lock`; instance kedua keluar dengan kode ≠ 0.
  Dua perekam = dua token bucket atas satu kunci = 2×60 panggilan/menit, yaitu cara paling
  cepat menghabiskan kuota. (POSIX; repo ini Linux/WSL, lihat docs/versions.md.)
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
import logging
import os
import re
import sqlite3
import sys
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

# Kunci instance-tunggal hidup di `core.livelock` (TASKS 2.0a): jalur `flock` hanya ada di
# satu berkas, dan pemanggil live lain memakai `try_acquire` dari modul yang sama. Ketiga nama
# di-re-export di sini karena perekam dan tesnya adalah pengguna historisnya.
from core.livelock import (  # noqa: F401 — ENV_LOCK_PATH di-re-export untuk kompatibilitas
    ENV_LOCK_PATH,
    RecorderLockError,
    acquire_single_instance_lock,
)
from core.ryo_client import BaseRyoClient, FixtureClient, RyoClient, RyoError, RyoProtocolError
from core.sources import SourcesError

# `_resolve_path` diimpor SENGAJA meski privat: penjaga fixture harus menormalkan path
# dengan penormal yang SAMA dengan penulisnya. Penormal sendiri berarti `--db "x.db "`
# (satu spasi di ujung) dilihat penjaga sebagai berkas lain sementara `Store` membuang
# spasinya dan menulis ke berkas aslinya — tembus, terbukti oleh security-reviewer 0.19.
from core.store import (
    DEFAULT_DB_PATH,
    Store,
    StoreError,
    _resolve_path,
)

log = logging.getLogger("core.recorder")

ROOT = Path(__file__).resolve().parent.parent

#: Simbol inti bila `WATCHLIST` tidak diset (sama dengan .env.example).
CORE_SYMBOLS: tuple[str, ...] = ("SOL", "BTC", "ETH", "BNB")

ENV_WATCHLIST = "WATCHLIST"
ENV_MAX_SYMBOLS = "RECORDER_MAX_SYMBOLS"
ENV_INTERVAL_S = "RECORDER_INTERVAL_S"
ENV_MAX_FOLLOWUPS = "RECORDER_MAX_FOLLOWUPS"
ENV_MAX_FAILED_CYCLES = "RECORDER_MAX_FAILED_CYCLES"

DEFAULT_MAX_SYMBOLS = 20
DEFAULT_INTERVAL_S = 3600.0
#: Batas atas interval. Tanpa ini, salah ketik `1e9` membuat perekam tidur 31 tahun sementara
#: `daemons.sh status` tetap melaporkan "JALAN" — kegagalan paling senyap yang mungkin ada.
MAX_INTERVAL_S = 86_400.0
#: Anggaran follow-up per siklus (lihat "Ratchet follow-up" di docstring modul).
DEFAULT_MAX_FOLLOWUPS = 20
#: Siklus beruntun tanpa satu pun snapshot sebelum perekam menyerah dengan kode ≠ 0.
DEFAULT_MAX_FAILED_CYCLES = 3

# -- jam dinding, suspend host, dan watchdog siklus (TASKS 0.8a) ---------------------------
#: Jeda antar siklus ditidurkan dalam potongan sependek ini, lalu sisa waktu dihitung ULANG
#: dari jam dinding. Alasannya ada di "Kenapa jam dinding" pada docstring modul.
SLEEP_SLICE_S = 60.0
#: Jeda terpendek yang masih dianggap jeda: perekam tidak boleh berputar tanpa tidur sama
#: sekali walau siklusnya sendiri sudah lebih lama dari interval.
MIN_SLEEP_S = 1.0
#: Satu potongan tidur yang memakan waktu jam dinding sebanyak ini LEBIH LAMA daripada yang
#: diminta = host tersuspend (atau jam dinding dimajukan). Satu interval, bukan dua: lompatan
#: sebesar satu interval berarti setidaknya satu siklus hilang, dan celah yang tidak dicatat
#: adalah celah yang tidak diperbaiki.
SUSPEND_FACTOR = 1.0
#: Anggaran jam dinding satu siklus = `CYCLE_BUDGET_FACTOR` x interval, minimal
#: `MIN_CYCLE_BUDGET_S`. Siklus nyata memakan 990-1268 dtk dari interval 3600 dtk (log 1-4 Sep),
#: jadi 2x interval adalah kelonggaran besar yang hanya tersentuh oleh kejadian tak wajar.
CYCLE_BUDGET_FACTOR = 2.0
MIN_CYCLE_BUDGET_S = 1800.0

#: Umur maksimum sebuah simbol untuk ikut follow-up. 26 jam, bukan 24: siklus per jam butuh
#: kelonggaran agar pembacaan T+24 jam tidak terlewat gara-gara satu siklus yang molor.
FOLLOWUP_MAX_AGE_H = 26.0
#: Rentang pengamatan yang membuat kewajiban follow-up sebuah simbol SELESAI.
FOLLOWUP_DONE_AFTER_H = 24.0
#: Sejauh mana ke belakang pembacaan lama dicari saat memeriksa "sudah selesai" (7 hari).
FOLLOWUP_DONE_LOOKBACK_H = 7 * 24.0

#: Pembanding tetap untuk `compare_tokens` — sama dengan `preflight.params.Params.peers`.
#: Disalin, tidak diimpor: `core/` tidak boleh bergantung pada `preflight/` (ADR-002). Yang
#: menjaga kedua nilai tetap sama adalah tes, bukan impor.
COMPARE_PEERS: tuple[str, ...] = ("BNB", "ETH")
#: `intent` untuk `compare_tokens` — enum `swing|hold|spot` (docs/samples/catalog.json).
COMPARE_INTENT = "swing"
#: Pemisah daftar `symbols` (`"SOL, BNB, ETH"`,
#: docs/samples/compare_tokens.json["result"]["request"]).
COMPARE_SEPARATOR = ", "
#: Anggaran `compare_tokens` per siklus. Empat = jumlah simbol inti; tool ini ~42 dtk per
#: panggilan, jadi kandidat scan sengaja TIDAK ikut dibandingkan.
MAX_COMPARES_PER_CYCLE = 4

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


def compare_args(
    symbol: str, peers: Sequence[str] = COMPARE_PEERS, intent: str = COMPARE_INTENT
) -> dict[str, Any]:
    """Argumen `compare_tokens` untuk satu simbol: `{"symbols": "SOL, BNB, ETH", "intent": "swing"}`.

    Bentuk kunci `symbols`/`intent` diambil verbatim dari
    `docs/samples/compare_tokens.json["result"]["request"]` (top-level sampel adalah
    `['tool', 'result', 'latency_ms']`, jadi `request` ada DI DALAM `result`).

    Dedupe **mempertahankan urutan** (bukan `set`): `args_hash` dihitung dari teks `symbols`,
    jadi himpunan yang sama dengan urutan berbeda adalah baris store yang BERBEDA dan tidak
    akan ditemukan pembaca store-first.

    **JANGAN menghapus dedupe-nya.** Ia bukan kerapian: `"BNB, BNB, ETH"` — bentuk yang akan
    terkirim tanpa dedupe untuk simbol yang ADALAH pembanding — tetap **BELUM DIVERIFIKASI**
    (0 amatan pada `id <= 1278`, api-facts §A), dan dedupe inilah satu-satunya yang menjaga
    bentuk tak-terverifikasi itu tidak pernah dikirim ke RYO. "Menyederhanakannya" berarti
    menebak perilaku API atas argumen yang belum pernah ada amatannya.

    Cacah simbolnya 2–3. Cacah 3 dan cacah 2 sama-sama **terbukti empiris diterima** RYO
    (`status: ok`; cacah 2 pada snapshot id 1262–1263, siklus 2026-09-03T10:50:49Z — lihat
    api-facts §A) — bukan sekadar "di dalam rentang 2-4" pada deskripsi `catalog.json`.
    Bentuk respons daftar 2 simbol **identik** dengan daftar 3 simbol (10 kunci top-level sama,
    `factors` tetap 3 entri, `winner`/`conclusion.pick` tetap terisi; yang menyusut hanya isi
    `data.tokens`, `coverage_by_token`, dan `factors[].scores`), jadi pembaca item 10 tidak
    membutuhkan cabang khusus untuk ETH/BNB.
    """
    out: list[str] = []
    for name in (symbol, *peers):
        if name not in out:
            out.append(name)
    return {"symbols": COMPARE_SEPARATOR.join(out), "intent": intent}


class RecorderConfigError(Exception):
    """Konfigurasi env tidak sah — perekam berhenti dengan pesan, bukan diam-diam menebak."""


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
    compares: tuple[str, ...] = ()
    #: Simbol watchlist yang TIDAK dibandingkan karena `MAX_COMPARES_PER_CYCLE`. Kosong di
    #: produksi hari ini; bila terisi, itulah sebab item 10 bisu untuk simbol tersebut.
    compares_dropped: tuple[str, ...] = ()
    followups: tuple[str, ...] = ()
    #: Siklus dipotong watchdog karena melewati anggaran jam dinding (TASKS 0.8a). Snapshot
    #: yang SUDAH tersimpan tetap tersimpan; sisanya tidak dipanggil.
    aborted: bool = False

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
           Sisi "pembacaan BARU" dibaca dengan `drop_future=True` (TASKS 2.0f): baris
           ber-`ts_utc` masa depan tidak boleh mempensiunkan simbol, sedangkan jendela
           `in_window` tetap memuatnya.
           Tanpa saringan ini himpunan follow-up hanya bisa membesar (lihat docstring modul).

        Urutannya "yang pembacaan terakhirnya paling lama, duluan": simbol yang hampir keluar
        dari jendela 26 jam paling mendesak, jadi anggaran per siklus memotong dari ekor.
        """
        now = self._now_fn()
        done_after_min = self.config.followup_done_after_h * 60.0
        try:
            # `drop_future=False` DITULIS EKSPLISIT: tabel A/B memutuskan KEDUA call-site,
            # jadi keduanya harus terbaca sebagai pilihan. Di sini baris masa depan sengaja
            # IKUT supaya simbolnya tidak lenyap dari jendela (security-reviewer 2.0f).
            in_window = self._store.recent_args(
                "analyze_token",
                self.config.followup_max_age_h * 60.0,
                now=now,
                drop_future=False,
            )
            # `drop_future=True` HANYA di sini: baris ber-`ts_utc` masa depan (jam melompat)
            # tidak boleh dihitung sebagai "pembacaan baru" yang mempensiunkan simbol dari
            # follow-up; di `in_window` baris itu tetap ikut supaya simbolnya tidak lenyap
            # (tabel A/B di docstring `Store.recent_args`, TASKS 2.0f).
            fresh = self._store.recent_args("analyze_token", done_after_min, now=now, drop_future=True)
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

    def _compare_symbols(self, symbols: Sequence[str]) -> tuple[list[str], list[str]]:
        """`(dibandingkan, dipotong)` untuk siklus ini: watchlist saja, dipotong anggaran.

        Kandidat `scan_market` sengaja DIKELUARKAN: `compare_tokens` ~42 dtk per panggilan,
        dan yang dibutuhkan item 10 adalah baris segar untuk simbol inti — bukan cakupan luas.

        **Simbol yang dipotong disebut NAMANYA, di level WARNING, dan dikembalikan ke
        :class:`CycleReport`.** Anggaran ini belum pernah menggigit di produksi (watchlist 4
        simbol → pembatasnya `len(watchlist)`), tetapi operator bisa memperlebar `WATCHLIST`
        lewat env tanpa review kode. Begitu itu terjadi, simbol ke-5 dst. TIDAK PERNAH
        mendapat baris `compare_tokens`, sehingga item 10 (WAJIB) menjadi `unspoken`
        selamanya dan setiap keputusan untuk simbol itu NO_GO. Degradasinya jujur
        (`unspoken`, bukan nilai default), tetapi SEBABNYA tidak terlihat di mana pun kecuali
        di sini — jadi "ARB tidak pernah dibandingkan" harus bisa dibaca dari satu baris log,
        bukan disimpulkan dari cacah yang hilang (temuan SEDANG security-reviewer, TASKS 2.0c;
        pola yang sama dengan 1.13: celah yang tidak terlihat = celah yang tidak diperbaiki).
        """
        watchlist = set(self.config.watchlist)
        wanted = [symbol for symbol in symbols if symbol in watchlist]
        out, dropped = wanted[:MAX_COMPARES_PER_CYCLE], wanted[MAX_COMPARES_PER_CYCLE:]
        if dropped:
            log.warning(
                "compare_tokens: %d simbol watchlist, anggaran %d; TIDAK dibandingkan siklus "
                "ini: %s — item 10 (relatif terhadap pembanding) akan 'unspoken' untuk simbol "
                "itu sampai WATCHLIST dipersempit atau MAX_COMPARES_PER_CYCLE dinaikkan",
                len(wanted),
                MAX_COMPARES_PER_CYCLE,
                ", ".join(dropped),
            )
        return out, dropped

    # -- siklus & loop --------------------------------------------------------------------
    def run_cycle(
        self,
        *,
        deadline: float | None = None,
        time_fn: Callable[[], float] = time.time,
    ) -> CycleReport:
        """Satu siklus penuh. Tidak melempar untuk kegagalan tool — lihat :meth:`_record`.

        `deadline` (detik jam dinding, sama satuan dengan `time_fn`) adalah watchdog siklus:
        sebelum setiap panggilan berikutnya perekam memeriksa apakah anggarannya sudah lewat
        dan, bila ya, MENGHENTIKAN siklus ini dengan `CycleReport.aborted = True`. Ia tidak
        membunuh proses apa pun dan tidak menyentuh snapshot yang sudah tersimpan (batas 1.13
        utuh).

        `report.symbols` / `.compares` / `.followups` diisi SESUDAH panggilannya berhasil
        dijalankan, bukan di depan dari daftar niat: pada siklus yang dipotong, cacah yang
        dilaporkan log harus cacah yang benar-benar terekam. Langkah yang tidak pernah
        dijalankan muncul sebagai daftar kosong karena memang tidak ada amatannya — bukan
        karena nilainya nol (CLAUDE.md, "tunjuk & sebut").
        """
        report = CycleReport()

        def over_budget(stage: str) -> bool:
            if deadline is None or time_fn() < deadline:
                return False
            report.aborted = True
            log.warning(
                "siklus melewati anggaran jam dinding sebelum %s; siklus dipotong dengan %d "
                "snapshot tersimpan, siklus baru dimulai dari data terkini",
                stage,
                report.n_recorded,
            )
            return True

        self._record("market_overview", {}, report)
        if over_budget("monitor_market_sentiment_shift"):
            return report
        self._record("monitor_market_sentiment_shift", {}, report)

        if over_budget("scan_market"):
            return report
        symbols = self._cycle_symbols(report)
        for symbol in symbols:
            if over_budget(f"analyze_token [{symbol}]"):
                return report
            self._record("analyze_token", {"symbol": symbol}, report)
            self._record("deep_analysis", {"symbol": symbol, "include_perp": True}, report)
            report.symbols += (symbol,)

        compares, compares_dropped = self._compare_symbols(symbols)
        report.compares_dropped = tuple(compares_dropped)
        for symbol in compares:
            if over_budget(f"compare_tokens [{symbol}]"):
                return report
            self._record("compare_tokens", compare_args(symbol), report)
            report.compares += (symbol,)

        followups = self._followup_symbols(set(symbols))
        for symbol in followups:
            if over_budget(f"follow-up [{symbol}]"):
                return report
            self._record("analyze_token", {"symbol": symbol}, report)
            report.followups += (symbol,)
        return report

    def _sleep_until(
        self,
        next_start: float,
        *,
        sleep_fn: Callable[[float], None],
        time_fn: Callable[[], float],
    ) -> None:
        """Tidur sampai `next_start` (jam dinding) dalam potongan `SLEEP_SLICE_S`.

        Satu `sleep_fn(3600)` tidak bisa dibangunkan lebih awal: bila host tersuspend di
        tengahnya, jam dinding melompat berjam-jam sementara tidurnya tetap berjalan sampai
        habis. Memotongnya dan menghitung ulang sisa waktu dari `time_fn` membuat siklus
        berikutnya jalan SEGERA setelah host bangun.
        """
        slept = 0.0
        while True:
            remaining = next_start - time_fn()
            # Jam dinding mundur (koreksi NTP) tidak boleh berubah menjadi tidur panjang.
            remaining = min(remaining, self.config.interval_s)
            if remaining <= 0.0 and slept >= MIN_SLEEP_S:
                return
            if slept >= self.config.interval_s and remaining > 0.0:
                # Jam dinding mundur (koreksi NTP) memindahkan tenggatnya menjauh; total tidur
                # tetap dipatok satu interval supaya jeda tidak ikut tumbuh sebesar koreksinya.
                log.warning(
                    "jam dinding mundur selama jeda; jeda ditutup setelah %.0f dtk (satu interval) "
                    "daripada mengikuti tenggat yang ikut mundur",
                    slept,
                )
                return
            chunk = max(min(remaining, SLEEP_SLICE_S), MIN_SLEEP_S - slept)
            before = time_fn()
            sleep_fn(chunk)
            slept += chunk
            drift = (time_fn() - before) - chunk
            if drift >= SUSPEND_FACTOR * self.config.interval_s:
                log.warning(
                    "jam dinding melompat %.0f menit selama jeda %.0f dtk — host tampaknya "
                    "tersuspend; siklus berikutnya dijalankan segera, jam yang terlewat tidak "
                    "bisa direkam ulang",
                    drift / 60.0,
                    chunk,
                )

    def run(
        self,
        *,
        max_cycles: int | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        time_fn: Callable[[], float] = time.time,
    ) -> RunSummary:
        """Loop perekaman. `max_cycles=1` = mode `--once`.

        `sleep_fn`/`time_fn` disuntik supaya tes tidak pernah benar-benar tidur. Loop hanya
        berhenti karena tiga sebab, semuanya dicatat: jumlah siklus tercapai,
        `RECORDER_MAX_FAILED_CYCLES` siklus beruntun tanpa snapshot, atau `sleep_fn` gagal
        (berputar tanpa jeda akan membakar kuota lebih cepat daripada berhenti).

        **Kenapa jam dinding, bukan `time.monotonic` (TASKS 0.8a).** `CLOCK_MONOTONIC` — dan
        `time.sleep`, yang memakainya — tidak menghitung waktu host tersuspend. Di
        `logs/recorder.log` satu siklus mulai 2026-09-02 02:08:25 dan selesai 05:44:15 (3j36m
        jam dinding) sambil melaporkan `durasi=1054s`; celah 3j19m-nya jatuh di dalam satu
        `backoff 60.0s`. Semua `durasi=` di log ada di 990-1268 dtk, jadi tidak ada satu pun
        siklus yang benar-benar menggantung dan `DEFAULT_TIMEOUT_S` di `core.ryo_client`
        bekerja: sebab celahnya adalah jamnya, bukan requestnya. Jadwal dan watchdog di sini
        memakai `time_fn` = `time.time`, dan durasi yang dicatat log adalah durasi jam dinding
        — angka yang sama dengan yang dilihat `daemons.sh status` dan sapuan `data/ryo.db`.
        """
        total = 0
        cycles = 0
        consecutive = 0
        cut_short = 0
        while True:
            started = time_fn()
            budget = max(CYCLE_BUDGET_FACTOR * self.config.interval_s, MIN_CYCLE_BUDGET_S)
            aborted_cycle = False
            try:
                report = self.run_cycle(deadline=started + budget, time_fn=time_fn)
            except Exception as exc:  # noqa: BLE001 — pagar terakhir: satu siklus yang meledak
                # tidak boleh menghentikan perekaman. Nama tipe saja (lihat docstring modul).
                log.error("siklus gagal seluruhnya (%s); lanjut ke siklus berikutnya", type(exc).__name__)
                consecutive += 1
            else:
                aborted_cycle = report.aborted
                cut_short = cut_short + 1 if report.aborted else 0
                total += report.n_recorded
                consecutive = consecutive + 1 if report.is_total_failure else 0
                log.info(
                    "siklus %s: %d snapshot %s; gagal %d %s; simbol=%d banding=%d%s "
                    "follow-up=%d durasi=%.0fs",
                    "DIPOTONG watchdog" if report.aborted else "selesai",
                    report.n_recorded,
                    report.recorded,
                    report.n_failed,
                    report.failed,
                    len(report.symbols),
                    len(report.compares),
                    (
                        ""
                        if not report.compares_dropped
                        else f" (tanpa banding: {', '.join(report.compares_dropped)})"
                    ),
                    len(report.followups),
                    time_fn() - started,
                )
            cycles += 1

            if cut_short >= self.config.max_failed_cycles:
                # Siklus terpotong SELALU menyimpan beberapa snapshot, jadi rem
                # `is_total_failure` tidak akan pernah menggigitnya: `daemons.sh status` dan
                # `db_status.py` tetap melihat data segar sementara `compare_tokens` dan
                # follow-up — ekor siklus, satu-satunya sumber item 10 dan pembacaan T+24 jam —
                # tidak pernah dijalankan lagi. Rem terpisah supaya kelaparan itu berisik,
                # bukan senyap (temuan TINGGI security-reviewer 0.8a, 4 Sep 2026).
                log.error(
                    "%d siklus berturut-turut dipotong watchdog; ekor siklus (compare_tokens, "
                    "follow-up) tidak pernah dijalankan lagi sehingga item 10 akan 'unspoken' dan "
                    "outcome T+24 jam kehilangan pembacaan kedua; perekam berhenti agar terlihat",
                    cut_short,
                )
                return RunSummary(cycles, total, consecutive, aborted=True)
            if consecutive >= self.config.max_failed_cycles:
                log.error(
                    "%d siklus berturut-turut tanpa satu pun snapshot tersimpan; perekam berhenti "
                    "agar kegagalan terlihat (berkas DB hilang/tidak bisa ditulis? host RYO mati?)",
                    consecutive,
                )
                return RunSummary(cycles, total, consecutive, aborted=True)
            if max_cycles is not None and cycles >= max_cycles:
                return RunSummary(cycles, total, consecutive, aborted=False)

            # Siklus yang dipotong sudah melewati anggarannya (≥ 2x interval), jadi
            # `started + interval` sudah lewat dan jedanya akan jatuh ke lantai 1 detik: siklus
            # penuh baru langsung dimulai, berulang-ulang. Terukur 2703 panggilan/jam vs 11/jam
            # normal — polling rapat yang dilarang api-facts §A dan docs/rules-compliance.md
            # (temuan TINGGI security-reviewer 0.8a). Karena itu jedanya dihitung dari SEKARANG.
            resume_at = (time_fn() if aborted_cycle else started) + self.config.interval_s
            try:
                self._sleep_until(resume_at, sleep_fn=sleep_fn, time_fn=time_fn)
            except Exception as exc:  # noqa: BLE001 — sleep yang gagal (mis. nilai di luar
                # jangkauan OS) tidak boleh berubah menjadi loop tanpa jeda.
                log.error(
                    "gagal menunggu jeda antar siklus (%s); perekam berhenti daripada berputar tanpa jeda",
                    type(exc).__name__,
                )
                return RunSummary(cycles, total, consecutive, aborted=True)


def _args_label(args: Mapping[str, Any]) -> str:
    """Label singkat untuk log: simbol saja, tanpa isi envelope.

    `compare_tokens` tidak punya `symbol` melainkan `symbols` (daftar ber-koma); keduanya
    argumen yang kita susun sendiri, jadi aman dicatat.
    """
    symbol = args.get("symbol")
    if isinstance(symbol, str):
        return f"[{symbol}]"
    symbols = args.get("symbols")
    return f"[{symbols}]" if isinstance(symbols, str) else ""


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

    **Penting untuk uji "kredensial dicabut" (TASKS 4.5).** "Belum diset" berarti namanya
    TIDAK ADA di `os.environ`. Konsekuensi yang harus disadari sebelum menulis tes chaos:

    * ``RYO_MCP_KEY=`` (ada, tetapi kosong) -> TIDAK diisi ulang; perekam berhenti dengan
      kredensial kosong, persis seperti yang diharapkan tes;
    * ``unset RYO_MCP_KEY`` (namanya dihapus) -> **DIISI ULANG dari `.env`**, sehingga perekam
      tetap memanggil RYO. Ini bukan cacat: daemon memang harus bisa hidup dari `.env`.
      Tetapi artinya `unset` BUKAN cara mencabut kredensial. Pakai ``--no-dotenv``,
      kosongkan nilainya, atau pindahkan berkas `.env`.

    hackathon-judge menemukan ini dengan cara mahal: percobaan "cabut kredensial"-nya
    ternyata tetap memanggil API sungguhan dan ikut menyumbang beberapa HTTP 429.

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


def _repo_evidence_db() -> Path:
    """Store bukti milik repo ini — satu berkas yang tidak boleh menerima fixture, titik.

    Dijangkarkan ke akar repo, BUKAN ke cwd: `Path("./data/ryo.db").resolve()` dari `scripts/`
    menunjuk `scripts/data/ryo.db`, dan penjaga yang memakainya mati persis pada bentuk insiden
    `id 1597-1643` (temuan KRITIS security-reviewer 0.19).

    `DB_PATH` SENGAJA tidak ikut di sini. Ia justru cara yang didokumentasikan untuk mengarahkan
    perekam ke DB coretan (`DB_PATH=/tmp/t.db … --client fixture`, docstring modul ini), jadi
    memperlakukannya sebagai "store bukti" akan melarang satu-satunya jalan fixture yang benar.
    DB kustom tetap terlindung — oleh :func:`_real_evidence_rows`, yang bertanya soal ISI.
    """
    return ROOT / Path(DEFAULT_DB_PATH)


def _inside_evidence_dir(target: Path) -> bool:
    """Apakah `target` berada di dalam `ROOT/data`, direktori bukti repo?

    Menutup store bukti KUSTOM (`DB_PATH=./data/tenko.db`) sejak hari pertama, bukan sejak
    keputusan pertamanya — tanpa memerahkan satu tes pun, karena tidak ada tes yang menulis ke
    `data/` (`tests/helpers_agent.py`). Diusulkan security-reviewer 0.19 dan diverifikasi atas
    suite penuh sebelum diambil.
    """
    evidence_dir = _repo_evidence_db().parent
    try:
        return evidence_dir in target.resolve().parents
    except OSError:  # pragma: no cover
        return False


def _same_file(left: Path, right: Path) -> bool:
    """Dua path menunjuk berkas yang sama — termasuk lewat hardlink dan bind mount.

    `resolve()` mengikuti symlink tetapi TIDAK menembus hardlink: `ln data/ryo.db alias.db`
    memberi dua path yang `resolve()`-nya berbeda dengan inode yang sama. Karena itu identitas
    inode diperiksa lebih dulu bila kedua berkas ada, dan perbandingan path menjadi cadangan
    untuk berkas yang belum ada (temuan SEDANG security-reviewer 0.19).
    """
    try:
        if left.exists() and right.exists():
            return left.stat().st_ino == right.stat().st_ino and left.stat().st_dev == right.stat().st_dev
    except OSError:  # pragma: no cover — stat gagal (izin, mount hilang)
        pass
    try:
        return left.resolve() == right.resolve()
    except OSError:  # pragma: no cover
        return False


def _ledger_rows(path: Path) -> int | None:
    """Berapa baris `decisions` di `path`; None bila tidak bisa dibaca.

    Kenapa `decisions` dan BUKAN "snapshot yang bukan salinan sampel": store coretan mana pun
    mengumpulkan snapshot, termasuk baris buatan tangan di tes offline. Memakainya sebagai
    sinyal akan melarang pola yang justru didokumentasikan modul ini
    (`DB_PATH=/tmp/t.db … --client fixture`) — dan penjaga yang menghalangi pekerjaan sah akan
    dimatikan orang, bukan dipatuhi. Baris `decisions` berbeda: ia buku besar permanen, ditulis
    hanya oleh `agent.main`, dan keberadaannya berarti berkas ini SUDAH menjadi store bukti.
    """
    if not path.exists():
        return 0
    try:
        connection = sqlite3.connect("file:" + quote(path.as_posix(), safe="/") + "?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        connection.execute("PRAGMA query_only=ON")
        return int(connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0])
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def fixture_refusal(db_arg: str | Path | None) -> str | None:
    """Alasan menolak menulis payload fixture ke `db_arg`, atau None bila aman (TASKS 0.19).

    Dijalankan SEBELUM `Store(...)` dibuka: membuka store lebih dulu akan membuat berkas dan
    skemanya, sehingga perintah yang GAGAL meninggalkan "DB bukti" kosong yang membuat
    `agent.main` berhenti berkata "jalankan `make record` dulu" (temuan TINGGI reviewer 0.19).

    Dua alasan yang saling bebas: berkasnya adalah store bukti repo — dikenali lewat identitas
    inode, jadi cwd lain, symlink, dan hardlink semuanya tertangkap, dan berlaku walau berkasnya
    belum ada — atau ia memegang buku besar `decisions`.

    TIGA alasan yang saling bebas: berkasnya adalah store bukti repo (identitas inode, jadi cwd
    lain, symlink, dan hardlink tertangkap, dan berlaku walau berkasnya belum ada); ia berada di
    dalam direktori bukti `ROOT/data`; atau ia memegang buku besar `decisions`.

    **Celah yang diakui, ditulis supaya tidak ditemukan orang lain sebagai kejutan:** store
    bukti kustom DI LUAR `ROOT/data` yang belum punya satu pun baris `decisions` tidak
    tertangkap. Menutupnya dengan melarang fixture ke DB mana pun yang memuat snapshot
    non-sampel sudah dicoba dan DITOLAK: itu memerahkan tiga tes offline yang sah, dan penjaga
    yang menghalangi pekerjaan sah akan dimatikan, bukan dipatuhi.
    """
    try:
        target = _resolve_path(db_arg)
    except StoreError as exc:
        return f"path DB tidak bisa dibaca ({exc})"
    if _same_file(target, _repo_evidence_db()):
        return f"{target} adalah store bukti repo ini ({DEFAULT_DB_PATH})"
    if _inside_evidence_dir(target):
        return f"{target} berada di dalam direktori bukti {_repo_evidence_db().parent}"
    ledger = _ledger_rows(target)
    if ledger is None:
        return f"{target} tidak bisa diperiksa isinya, jadi ia tidak dianggap aman untuk fixture"
    if ledger > 0:
        return f"{target} memegang buku besar keputusan ({ledger} baris), jadi ia store bukti"
    return None


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
    parser.add_argument(
        "--no-dotenv",
        action="store_true",
        help="jangan membaca .env; pakai environment apa adanya (uji 'kredensial dicabut')",
    )
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Exit code: 0 = ada snapshot terekam; 1 = tidak menghasilkan apa pun / menyerah;
    2 = tidak bisa mulai (konfigurasi, kredensial, store, atau perekam lain sudah jalan)."""
    args = _parse_args(argv)
    _configure_logging(args.log_level)
    if not args.no_dotenv:
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

    if args.client == "fixture":
        refusal = fixture_refusal(args.db)
        if refusal is not None:
            log.error(
                "menolak menulis payload fixture: %s. Payload fixture adalah salinan "
                "docs/samples, bukan jawaban RYO, dan store hanya-tambah — barisnya tidak bisa "
                "dihapus lagi. Tunjuk berkas lain dengan --db (mis. --db /tmp/fixture.db), atau "
                "pakai --client live.",
                refusal,
            )
            _close(client)
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
