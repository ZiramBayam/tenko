"""Klien REST untuk 6 tool RYO-CHAN (docs/api-facts.md §A).

Fakta yang mengikat modul ini (semuanya dari docs/api-facts.md §A + docs/samples):

* Endpoint call: ``POST {RYO_MCP_URL}/tools/{tool}/call`` dengan body = objek argumen
  telanjang (mis. ``{"symbol": "SOL"}``); envelope = ``response.json()["result"]``.
* Endpoint tanpa kuota: ``GET /health`` (tanpa auth), ``GET /whoami``, ``GET /tools``.
* Auth: header ``Authorization: Bearer $RYO_MCP_KEY``; env resmi ``RYO_MCP_URL`` / ``RYO_MCP_KEY``.
* Rate limit 60 panggilan/menit per key; header huruf kecil ``x-ratelimit-limit`` /
  ``x-ratelimit-remaining`` / ``x-ratelimit-reset``; ``Retry-After`` saat 429.
* Backoff eksponensial + jitter untuk 429/503; argumen invalid (4xx selain 429) TIDAK diretry.

Kontrak kejujuran (ADR-004, guide RYO "jangan ubah null/unavailable jadi nol"):

* Envelope dikembalikan APA ADANYA. Klien tidak pernah mengisi default, tidak pernah
  mengganti ``null``/``unavailable`` dengan 0, dan tidak pernah membaca ``summary``.
* Kegagalan selalu EKSPLISIT lewat exception turunan :class:`RyoError` — tidak pernah
  berupa dict kosong atau envelope palsu yang menyamar sebagai sukses:
  - :class:`RyoNetworkError`  — jaringan/timeout gagal setelah semua percobaan;
  - :class:`RyoHTTPError`     — HTTP >= 400 (termasuk 429 yang tetap gagal setelah retry);
  - :class:`RyoProtocolError` — respons 2xx tetapi bukan envelope sah (bukan JSON, tanpa
    kunci ``result``, ``result`` bukan objek, atau tanpa kunci ``status``).
  Pemanggil (perekam/skill) menangkap ``RyoError`` per panggilan dan mencatatnya sebagai
  komponen yang gagal, bukan sebagai data.

Kredensial (klaim di bawah ini punya tes regresi di ``tests/test_ryo_client.py``):

* kunci hanya masuk lewat parameter/``from_env``; disimpan sebagai :class:`Secret` yang
  ``repr``/``str``-nya ``***`` → aman di ``repr(vars(client))``, ``pprint``, dan
  ``--showlocals``;
* semua teks yang kita bentuk lewat :func:`_redact` lalu :func:`_printable` lalu dipotong
  (urutan itu ditulis satu kali di :meth:`BaseRyoClient._server_text`);
* ada **LIMA** jalur teks yang dikendalikan server, dan kelimanya diperlakukan sebagai input
  tidak tepercaya (daftar ini adalah kontrak; tesnya `test_key_is_redacted_on_all_five_
  server_text_paths` memparametrisasi kelimanya):

  1. header ``x-trace-id`` — selain diredaksi, harus COCOK pola bentuk (allowlist), sehingga
     pantulan verbatim maupun perakitan ulang karakter tidak lolos;
  2. ``code`` dari body error;
  3. cuplikan body error (``_body_excerpt``);
  4. ``result.status`` yang di luar enum api-facts §A — **HTTP 200**;
  5. ``result.tool`` yang tidak cocok dengan tool yang diminta — **HTTP 200**.

  Dua yang terakhir dulu TIDAK diredaksi sama sekali: ``_check_envelope`` adalah fungsi
  tingkat modul, jadi ia tidak punya akses ke kunci dan secara struktural tidak bisa
  meredaksi; ia membocorkan 31 karakter pertama kunci ke stdout dan ke ``logs/*.log``
  (temuan tinjauan keamanan TASKS 1.5). Sekarang ia method, dan :meth:`BaseRyoClient._safe`
  dibuat **abstrak** supaya klien baru tidak bisa diam-diam tidak meredaksi.
  Selain bentuk PERSIS, :func:`_redact_fragments` juga menelan **potongan** berurutan
  ≥ :data:`MIN_SECRET_FRAGMENT` (12) karakter dari tiap bentuk — server yang memantulkan
  "20 karakter pertama kunci" tanpa encoding apa pun adalah evasi termurah, dan pesan itu
  ikut ke ``warnings[]`` publik lewat `core/skill_cli.py`.
  **Batas yang diakui** (tiga, semuanya punya tes yang mengunci kenyataannya):

  1. kunci yang di-encode ulang dengan cara yang TIDAK kita enumerasi (rot13, gzip, sisipan
     yang tidak bisa dirakit ulang) tetap lolos di kelima jalur — server memang menerima
     kunci lewat ``Authorization`` dan bisa memantulkannya dalam bentuk apa pun;
  2. potongan yang lebih PENDEK dari 12 karakter tidak diredaksi. Ambang itu ada supaya
     kecocokan kebetulan tidak memakan teks diagnostik yang sah; konsekuensinya, 11 karakter
     pertama kunci bisa tercetak. Potongan dari bentuk hex/base64 ikut dicakup karena
     `_redact_fragments` dijalankan atas SETIAP bentuk, bukan hanya bentuk verbatim;
  3. :func:`_secret_forms` MEMBUANG bentuk yang lebih pendek dari 8 karakter, jadi kunci 7
     karakter tidak diredaksi verbatim dan kunci ≤3 karakter tidak punya bentuk sama sekali
     (base64-nya pun < 8). Kunci RYO nyata jauh lebih panjang (34 karakter di
     `.env.example`), tetapi ambang itu asumsi, bukan jaminan.

  Menutup kelas ini sepenuhnya menuntut "jangan simpan teks dari server sama sekali"
  (kehilangan diagnostik) dan BELUM dilakukan;
* ``base_url`` wajib ``https`` — Bearer key tidak pernah dikirim tanpa TLS;
* :class:`Secret` menolak pickle (blob pickle menyimpan nilai polos);
* exception httpx tidak pernah lolos: dibungkus ulang DAN dilempar di luar blok ``except``
  sehingga ``__context__``/``__cause__`` kosong (objek request httpx memuat header auth);
* header ``Authorization`` dibuat sesaat sebelum request, tidak disimpan di variabel lokal
  maupun sebagai header default ``httpx.Client``.

Kuota: token bucket 60/mnt dibagi per KEY di tingkat modul (indeks = sha256 kunci, TANPA
laju), jadi dua instance di proses yang sama (perekam + skill) tidak melipatgandakan laju
panggilan; laju yang lebih ketat menurunkan kapasitas bucket bersama.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import re
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from core.sources import RYO_TOOLS, require_source

log = logging.getLogger("core.ryo_client")

# --- konstanta dari docs/api-facts.md §A -------------------------------------------------
TOOLS: tuple[str, ...] = (
    "market_overview",
    "scan_market",
    "analyze_token",
    "deep_analysis",
    "compare_tokens",
    "monitor_market_sentiment_shift",
)
RATE_LIMIT_PER_MIN = 60
RETRYABLE_STATUS: frozenset[int] = frozenset({429, 503})
ENVELOPE_STATUSES: frozenset[str] = frozenset({"ok", "partial", "unavailable"})
DEFAULT_TIMEOUT_S = 90.0  # deep_analysis 17–34 dtk, compare_tokens ~40 dtk (api-facts §A)
DEFAULT_MAX_RETRIES = 3
MAX_BACKOFF_S = 60.0
MAX_BODY_EXCERPT_CHARS = 200
MAX_ERROR_MESSAGE_CHARS = 300
MAX_ERROR_CODE_CHARS = 64
# Panjang minimum POTONGAN kunci yang diredaksi (lihat `_redact_fragments`). 12 karakter
# dari kunci acak = entropi jauh di atas apa pun yang muncul kebetulan di pesan galat, jadi
# ambang ini menutup evasi tanpa memakan teks diagnostik yang sah.
MIN_SECRET_FRAGMENT = 12
MAX_BODY_READ_BYTES = 4 * 1024  # cukup untuk cuplikan 200 char; hindari salinan body raksasa
# Batas 8 MiB menolak SETELAH body ada di memori (httpx membacanya lebih dulu); gunanya
# mencegah parse+simpan payload absurd, bukan mencegah alokasinya.
# Sampel terbesar: docs/samples/scan_market.json = 4.552 byte.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
ENV_URL = "RYO_MCP_URL"
ENV_KEY = "RYO_MCP_KEY"

_SAMPLES_DIR = Path(__file__).resolve().parent.parent / "docs" / "samples"


# --- exception ---------------------------------------------------------------------------
class RyoError(Exception):
    """Akar semua kegagalan klien RYO. Pesan tidak pernah memuat kredensial."""


class RyoNetworkError(RyoError):
    """Jaringan/timeout gagal setelah semua percobaan habis."""


class RyoHTTPError(RyoError):
    """Server membalas HTTP >= 400."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code  # `code` UPPER_SNAKE dari body error (api-facts §A)
        self.trace_id = trace_id


class RyoProtocolError(RyoError):
    """Respons 2xx tetapi bukan envelope sah (api-facts §A)."""


# --- util --------------------------------------------------------------------------------
class Secret:
    """Pembungkus kredensial: `repr`/`str` selalu `***`.

    Tanpa ini, kunci polos muncul di `repr(vars(client))`, `pprint.pformat(vars(client))`,
    dan di frame locals saat `pytest --showlocals` / halaman debug framework.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """Satu-satunya jalan keluar nilai; hanya dipakai saat menyusun header Authorization."""
        return self._value

    def __repr__(self) -> str:
        return "***"

    __str__ = __repr__

    def __bool__(self) -> bool:
        return bool(self._value)

    def __reduce__(self) -> Any:
        """Menolak pickle: `pickle.dumps` menaruh nilai polos di blob."""
        raise TypeError("Secret tidak boleh di-pickle/di-serialisasi")

    def __getstate__(self) -> Any:
        raise TypeError("Secret tidak boleh di-pickle/di-serialisasi")

    def __copy__(self) -> Secret:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> Secret:
        return self


def _secret_forms(secret: str) -> tuple[str, ...]:
    """Bentuk kunci yang diredaksi: verbatim + hex + base64 (std/urlsafe, dengan & tanpa padding).

    Server MENERIMA kunci di header `Authorization`, jadi ia bisa memantulkannya kembali
    dalam bentuk apa pun. Yang bisa kita lakukan hanyalah menutup bentuk-bentuk yang kita
    enumerasi di sini. Encoding lain (rot13, gzip, per-karakter dengan sisipan yang tidak
    bisa dirakit ulang, dst.) TIDAK tertutup — lihat catatan batasan di docstring modul.
    """
    raw = secret.encode("utf-8")
    forms = {
        secret,
        raw.hex(),
        base64.b64encode(raw).decode("ascii"),
        base64.b64encode(raw).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(raw).decode("ascii"),
        base64.urlsafe_b64encode(raw).decode("ascii").rstrip("="),
    }
    # terpanjang dulu supaya bentuk tanpa padding tidak memotong bentuk berpadding;
    # bentuk < 8 karakter dibuang agar pola tidak terlalu longgar.
    # AMBANG YANG DIAKUI (docstring modul, "Batas yang diakui"): kunci 7 karakter tidak
    # diredaksi verbatim, kunci <= 3 karakter tidak punya bentuk sama sekali. Ini asumsi
    # tentang panjang kunci RYO (34 karakter), bukan jaminan — dan keluaran jalur ini
    # sekarang publik (masuk `warnings[]` lewat core/skill_cli.py).
    return tuple(sorted((form for form in forms if len(form) >= 8), key=len, reverse=True))


def _redact_fragments(text: str, secret: str, min_len: int) -> str:
    """Ganti setiap POTONGAN berurutan >= `min_len` karakter dari `secret`.

    Menutup evasi termurah yang tidak butuh encoding apa pun: server memantulkan sebagian
    kunci yang ia terima lewat header ``Authorization`` (mis. 20 karakter pertama di
    ``{"message": ...}``). Pencocokan bentuk PERSIS tidak menangkapnya, dan pesan itu
    `speakable` (`core/skill_cli.py`) sehingga mendarat di ``warnings[]`` publik dan
    ``logs/*.log``.

    Rakus-terpanjang, bukan n-gram tetap: mengganti n-gram 12 karakter yang tidak tumpang
    tindih akan menyisakan ekor 8 karakter dari pantulan 20 karakter. Case-insensitive,
    sama seperti :func:`_redact`, karena proxy pernah terlihat memantulkan header dalam
    bentuk yang sudah di-lowercase.
    """
    if len(secret) < min_len or len(text) < min_len:
        return text
    haystack = secret.lower()
    lowered = text.lower()
    out: list[str] = []
    i = 0
    while i < len(text):
        best = 0
        span = min_len
        while i + span <= len(text) and lowered[i : i + span] in haystack:
            best = span
            span += 1
        if best:
            out.append("***")
            i += best
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _redact(text: str, secret: str | None) -> str:
    """Ganti setiap bentuk kunci yang dikenal dengan penanda, tanpa memedulikan besar-kecil huruf.

    Case-insensitive karena proxy/server pernah terlihat memantulkan header dalam bentuk
    yang sudah di-lowercase/uppercase (dan hex bisa datang huruf besar). Sengaja TIDAK
    di-cache: cache tingkat modul akan menyimpan kunci polos lebih lama daripada perlu.
    """
    if not secret:
        return text
    for form in _secret_forms(secret):
        text = re.sub(re.escape(form), "***", text, flags=re.IGNORECASE)
        # Ronde kedua: potongan bentuk itu, untuk pantulan SEBAGIAN (prefiks/substring).
        text = _redact_fragments(text, form, MIN_SECRET_FRAGMENT)
    return text


def _printable(text: str) -> str:
    """Whitelist karakter printable: buang kontrol (`\n`, `\r`, `\x1b`, dst).

    Tanpa ini, nilai yang dikendalikan server bisa menyuntikkan baris log palsu atau
    escape ANSI (mis. pembersih layar) ke `logs/recorder.log` dan ke terminal.
    """
    return "".join(ch if ch.isprintable() else " " for ch in text)


# Allowlist BENTUK, bukan blocklist karakter. Trace id yang benar-benar diamati di
# docs/samples/*.headers: 6 dari 6 = tepat 32 hex huruf kecil (mis. e8fefd4d5e4c47a5b2d602b2e3027c1c);
# batas 8..64 hanya kelonggaran berjaga-jaga. Ini pengamatan atas 6 sampel, BUKAN spesifikasi
# yang dijanjikan RYO.
#
# Kenapa bukan "buang karakter yang tidak diizinkan": filter yang MENGHAPUS karakter merakit
# ulang kunci yang sengaja dipecah server (`r y o _ m c p _ ...` → bentuk utuh) sehingga
# mengalahkan redaksi yang mencocokkan persis. Pola bentuk menutup kelas itu.
#
# BATAS YANG DIAKUI (jangan menghapus kalimat ini): pola ini TIDAK membuat trace id kebal.
# Kunci yang di-encode ulang oleh server bisa saja berbentuk hex dan lolos `fullmatch`
# (mis. `secret.encode().hex()` = 48–64 karakter [0-9a-f]). Yang menutupnya adalah
# `_redact`/`_secret_forms` (verbatim + hex + base64), BUKAN pola ini — dan itu pun hanya
# untuk bentuk yang kita enumerasi. Jalur ini tidak lebih aman maupun lebih bocor daripada
# `_body_excerpt` (200 char) dan `_error_code` (64 char): ketiganya sama-sama menyimpan
# teks dari server dan sama-sama bergantung pada `_redact`.
_TRACE_ID_RE = re.compile(r"[0-9a-f]{8,64}")


def _clean_trace_id(raw: str | None, secret: str | None) -> str | None:
    """Trace id dari header `x-trace-id` diperlakukan sebagai input tidak tepercaya.

    Nilai yang tidak berbentuk trace id → `None` (kehilangan diagnostik, bukan kehilangan
    data: arah kegagalan yang benar). `_redact` tetap dijalankan lebih dulu sebagai lapisan
    kedua. Dipakai untuk pesan exception MAUPUN atribut `RyoHTTPError.trace_id`.
    """
    if raw is None:
        return None
    candidate = _redact(raw.strip(), secret)
    return candidate if _TRACE_ID_RE.fullmatch(candidate) else None


def _drop_none(args: dict[str, Any]) -> dict[str, Any]:
    """Argumen opsional yang None tidak dikirim.

    Alasannya BUKAN `additionalProperties: false` (itu soal properti asing), melainkan
    tipe properti di inputSchema: semuanya `string`/`number`/`boolean` dan tidak ada yang
    nullable, jadi mengirim `null` melanggar `type` (docs/api-facts.md §A, catalog.json).
    """
    return {k: v for k, v in args.items() if v is not None}


def _join_symbols(symbols: str | Sequence[str]) -> str:
    """`compare_tokens.symbols` bertipe string di katalog; list dirapikan jadi 'A, B'."""
    if isinstance(symbols, str):
        return symbols
    return ", ".join(str(s) for s in symbols)


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


@dataclass(frozen=True)
class RateLimitInfo:
    """Bacaan terakhir header rate-limit (huruf kecil, api-facts §A)."""

    limit: int | None = None
    remaining: int | None = None
    reset: int | None = None

    @classmethod
    def from_headers(cls, headers: Any) -> RateLimitInfo:
        get = headers.get
        return cls(
            limit=_int_or_none(get("x-ratelimit-limit")),
            remaining=_int_or_none(get("x-ratelimit-remaining")),
            reset=_int_or_none(get("x-ratelimit-reset")),
        )


class TokenBucket:
    """Token bucket 60 panggilan/menit per key (api-facts §A), aman untuk banyak thread.

    Jam & tidur bisa disuntik supaya tes berjalan tanpa `sleep` sungguhan.
    """

    def __init__(
        self,
        rate_per_min: int = RATE_LIMIT_PER_MIN,
        *,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_min <= 0:
            raise ValueError("rate_per_min harus > 0")
        self._capacity = float(rate_per_min)
        self._tokens = float(rate_per_min)
        self._rate = rate_per_min / 60.0
        self._time = time_fn
        self._sleep = sleep_fn
        self._updated = time_fn()
        self._lock = threading.Lock()

    @property
    def tokens(self) -> float:
        with self._lock:
            return self._tokens

    @property
    def rate_per_min(self) -> int:
        with self._lock:
            return round(self._rate * 60.0)

    def tighten(self, rate_per_min: int) -> None:
        """Turunkan kapasitas/laju bila pemakai lain meminta batas yang lebih ketat.

        Batas yang lebih longgar diabaikan: bucket bersama tidak boleh dilonggarkan oleh
        instance yang datang belakangan.
        """
        if rate_per_min <= 0:
            raise ValueError("rate_per_min harus > 0")
        with self._lock:
            if rate_per_min >= round(self._rate * 60.0):
                return
            self._capacity = float(rate_per_min)
            self._rate = rate_per_min / 60.0
            self._tokens = min(self._tokens, self._capacity)

    def take(self, amount: float = 1.0) -> float:
        """Ambil satu token; menunggu bila perlu. Kembalikan total detik menunggu."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._time()
                self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
                self._updated = now
                if self._tokens >= amount:
                    self._tokens -= amount
                    return waited
                delay = (amount - self._tokens) / self._rate
            # tidur di luar lock; setelah bangun keadaan dihitung ulang
            self._sleep(delay)
            waited += delay


_SHARED_BUCKETS: dict[str, TokenBucket] = {}
_SHARED_BUCKETS_LOCK = threading.Lock()


def _shared_bucket(
    api_key: str,
    rate_per_min: int,
    *,
    time_fn: Callable[[], float],
    sleep_fn: Callable[[float], None],
) -> TokenBucket:
    """Satu bucket per KEY, bukan per instance dan bukan per laju.

    Kuota 60/mnt berlaku per key (api-facts §A): dua `RyoClient` di proses yang sama
    (mis. perekam + `make skill`) harus berbagi jatah. Indeks HANYA sha256 dari kunci —
    kunci mentah tidak pernah menjadi key dict yang bisa ikut ter-dump, dan `rate_per_min`
    yang berbeda TIDAK boleh melahirkan bucket kedua (itu jalan pintas melewati kuota).
    Laju yang lebih kecil MENURUNKAN kapasitas bucket bersama; laju yang lebih besar
    diabaikan (yang paling ketat menang).

    Catatan jujur: `time_fn`/`sleep_fn` hanya dipakai saat bucket PERTAMA kali dibuat untuk
    sebuah kunci. Instance berikutnya memakai jam bucket yang sudah ada — di produksi
    keduanya `time.monotonic`/`time.sleep`, jadi tidak ada bedanya; di tes, suntikkan
    `bucket=` supaya isolasinya eksplisit.
    """
    index = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    with _SHARED_BUCKETS_LOCK:
        bucket = _SHARED_BUCKETS.get(index)
        if bucket is None:
            bucket = TokenBucket(rate_per_min, time_fn=time_fn, sleep_fn=sleep_fn)
            _SHARED_BUCKETS[index] = bucket
        else:
            bucket.tighten(rate_per_min)
        return bucket


def _reset_shared_buckets() -> None:
    """Hanya untuk tes: kosongkan registry agar jam palsu tidak bocor antar tes."""
    with _SHARED_BUCKETS_LOCK:
        _SHARED_BUCKETS.clear()


# --- antarmuka bersama -------------------------------------------------------------------
class BaseRyoClient(ABC):
    """Enam metode tool + health/whoami/catalog; dipakai RyoClient maupun FixtureClient."""

    @abstractmethod
    def _safe(self, text: str) -> str:
        """Redaksi kredensial dari teks yang DIKENDALIKAN SERVER.

        Sengaja abstrak, bukan default "kembalikan apa adanya": klien baru harus MENYATAKAN
        kebijakan redaksinya. Default yang aman-terlihat adalah cara temuan ini lahir —
        `_check_envelope` dulu fungsi tingkat modul tanpa akses ke kunci, sehingga dua jalur
        (`result.status`, `result.tool`) secara STRUKTURAL tidak bisa diredaksi dan
        membocorkan kunci lewat respons HTTP 200 (tinjauan keamanan, TASKS 1.5).
        """

    def _server_text(self, value: Any, limit: int) -> str:
        """Teks dari server untuk pesan: redaksi → whitelist printable → potong.

        Urutannya MENGIKAT. Semua jalur yang MENGUTIP teks server ke dalam pesan memanggil
        method ini — `_message`, `_body_excerpt`, `_error_code`, dan kedua cek di
        `_check_envelope` — sehingga tidak ada yang menyalin urutannya sendiri; potong-lalu-
        redaksi akan memotong bentuk kunci menjadi pecahan yang tidak lagi cocok pola, lalu
        mencetaknya. Dijaga `test_no_client_method_reorders_the_redaction_chain`.

        Jalur kelima, ``x-trace-id``, sengaja TIDAK lewat sini: ia tidak pernah dikutip apa
        adanya melainkan harus COCOK pola bentuk (`_clean_trace_id`) — allowlist yang lebih
        ketat, bukan pengecualian yang lebih longgar.
        """
        return _printable(self._safe(str(value)))[:limit]

    def _check_envelope(self, tool: str, body: Any) -> dict[str, Any]:
        """Validasi BENTUK envelope; isinya tidak disentuh sama sekali (ADR-004).

        Yang divalidasi hanya yang tertulis di api-facts §A: ada `result` berupa objek,
        `status` ∈ {ok, partial, unavailable}, dan `tool` cocok dengan yang diminta (envelope
        dengan atribusi tool salah akan tersimpan sebagai snapshot yang salah). Envelope yang
        sah dikembalikan sebagai objek yang sama persis — tanpa normalisasi/penambahan default.

        Method, bukan fungsi tingkat modul: `status`/`tool` yang dikutip di pesan galat
        adalah teks dari server, jadi ia WAJIB bisa memanggil `self._safe`.
        """
        if not isinstance(body, dict) or "result" not in body:
            raise RyoProtocolError(f"{tool}: respons tanpa kunci 'result'")
        envelope = body["result"]
        if not isinstance(envelope, dict):
            raise RyoProtocolError(f"{tool}: 'result' bukan objek envelope")
        if "status" not in envelope:
            raise RyoProtocolError(f"{tool}: envelope tanpa kunci 'status'")
        status = envelope["status"]
        if status not in ENVELOPE_STATUSES:
            raise RyoProtocolError(
                f"{tool}: status di luar enum api-facts §A: {self._server_text(status, 32)!r}"
            )
        reported = envelope.get("tool")
        if reported != tool:
            raise RyoProtocolError(f"{tool}: envelope mengaku tool {self._server_text(reported, 32)!r}")
        if envelope.get("isError"):
            # api-facts §A: "kegagalan tool di `result.isError`" (bisa HTTP 200 + isError true)
            raise RyoProtocolError(f"{tool}: envelope menandai isError")
        return envelope

    @abstractmethod
    def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Panggil satu tool, kembalikan envelope apa adanya. Gagal → RyoError."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """GET /health (tanpa auth, tanpa kuota)."""

    @abstractmethod
    def whoami(self) -> dict[str, Any]:
        """GET /whoami (auth, tanpa kuota)."""

    @abstractmethod
    def catalog(self) -> list[dict[str, Any]]:
        """GET /tools (auth, tanpa kuota) — katalog nama/deskripsi/inputSchema."""

    # Nama & tipe parameter PERSIS dari docs/samples/catalog.json.
    def market_overview(self) -> dict[str, Any]:
        """market_overview — tanpa argumen."""
        return self.call("market_overview", {})

    def scan_market(
        self,
        chain: str | None = None,
        theme: str | None = None,
        top_n: int | None = None,
    ) -> dict[str, Any]:
        """scan_market {chain, theme, top_n} — semuanya opsional."""
        return self.call("scan_market", {"chain": chain, "theme": theme, "top_n": top_n})

    def analyze_token(self, symbol: str, chain: str | None = None) -> dict[str, Any]:
        """analyze_token {symbol (wajib), chain} — input simbol, BUKAN alamat mint."""
        return self.call("analyze_token", {"symbol": symbol, "chain": chain})

    def deep_analysis(
        self,
        symbol: str,
        chain: str | None = None,
        include_perp: bool | None = None,
    ) -> dict[str, Any]:
        """deep_analysis {symbol (wajib), chain, include_perp}."""
        return self.call(
            "deep_analysis",
            {"symbol": symbol, "chain": chain, "include_perp": include_perp},
        )

    def compare_tokens(
        self,
        symbols: str | Sequence[str],
        intent: str | None = None,
    ) -> dict[str, Any]:
        """compare_tokens {symbols (wajib, 2-4 simbol), intent ∈ swing|hold|spot}."""
        return self.call("compare_tokens", {"symbols": _join_symbols(symbols), "intent": intent})

    def monitor_market_sentiment_shift(self, time_window: str | None = None) -> dict[str, Any]:
        """monitor_market_sentiment_shift {time_window} — enum katalog: hanya '7d'."""
        return self.call("monitor_market_sentiment_shift", {"time_window": time_window})


def _check_tool(tool: str) -> str:
    """Hanya 6 tool yang ada di katalog (api-facts §A) — juga menutup path traversal."""
    if tool not in TOOLS:
        raise RyoError(f"tool tidak dikenal: {_printable(str(tool))[:64]!r}")
    return tool


# --- klien nyata -------------------------------------------------------------------------
class RyoClient(BaseRyoClient):
    """Klien HTTP untuk endpoint RYO. Kunci hanya dari parameter/env, tidak pernah dicetak."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        rate_per_min: int = RATE_LIMIT_PER_MIN,
        http_client: httpx.Client | None = None,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        bucket: TokenBucket | None = None,
    ) -> None:
        """`bucket` HANYA untuk tes (isolasi jam palsu); jalur produksi wajib memakai
        registry per-key lewat `from_env`, yang menolak parameter ini."""
        base_url = (base_url or "").strip()
        if not base_url:
            raise RyoError(f"{ENV_URL} kosong")
        if not api_key:
            raise RyoError(f"{ENV_KEY} kosong")
        parts = urlsplit(base_url)
        # `app-ryochan.com/api/mcp` tanpa skema lolos cek `not base_url` lalu meledak
        # sebagai ValueError httpx pada panggilan pertama — ditangkap di sini.
        # HTTPS diwajibkan: kredensial Bearer tidak boleh dikirim polos. api-facts §A dan
        # .env.example hanya mengenal https://app-ryochan.com/api/mcp — tidak ada
        # pengecualian localhost karena tidak ada yang membutuhkannya (tes memakai
        # MockTransport, bukan server sungguhan).
        if parts.scheme != "https" or not parts.netloc:
            raise RyoError(f"{ENV_URL} harus https://<host>/... (lihat .env.example)")
        self.base_url = base_url.rstrip("/")
        self._key = Secret(api_key)
        self._timeout = timeout
        self._max_retries = max(0, max_retries)
        # Supresi di baris berikut disengaja: jitter backoff BUKAN kripto, dan `secrets`
        # tidak bisa disuntik jam palsu — tes rate limiter butuh rng yang deterministik.
        self._rng = rng or random.Random()  # noqa: S311 - jitter backoff, bukan kripto
        self._sleep = sleep_fn
        # kuota per key → bucket dibagi antar instance dengan kunci yang sama (api-facts §A)
        self._bucket = bucket or _shared_bucket(api_key, rate_per_min, time_fn=time_fn, sleep_fn=sleep_fn)
        self._owns_http = http_client is None
        # timeout dikirim per-request juga: klien httpx yang DISUNTIK bisa punya default
        # 5 dtk, padahal compare_tokens terukur 42,4 dtk sisi server (api-facts §A)
        self._http = http_client or httpx.Client(timeout=timeout)
        self.last_rate_limit: RateLimitInfo | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, **kwargs: Any) -> RyoClient:
        """Baca RYO_MCP_URL/RYO_MCP_KEY dari environment (satu-satunya sumber kredensial).

        Gate ADR-005 berdiri di sini, SEBELUM kredensial dibaca: `sources.yaml` harus
        menyatakan `ryo_tools` `enabled` DAN `approved: true` sebelum klien terbentuk —
        sumber yang belum disetujui tidak boleh menerima satu request pun. `FixtureClient`
        sengaja tidak lewat sini (ADR-005: fixture tetap boleh). Penolakan berupa
        :class:`core.sources.SourceNotApprovedError`; batas produk (`core/skill_cli.py`)
        yang mengubahnya jadi `SkillResult{status:"unavailable"}`.

        **Cakupan:** sejak TASKS 0.5 (1 Sep 2026) ini benar-benar jalur produksi satu-satunya
        di repo — `scripts/bootstrap_recorder.py`, yang dulu memanggil RYO dengan `httpx`
        langsung dan karenanya melewati gate ini maupun token bucket bersama per-key, sudah
        dihapus dan digantikan `core/recorder.py` yang memakai klien ini. Setiap pemanggil
        RYO baru WAJIB lewat sini; menambah jalur `httpx` langsung membuka kembali lubang
        yang sama.
        """
        if "bucket" in kwargs:
            # kesalahan program, bukan kondisi lingkungan: tetap didahulukan
            raise RyoError("parameter 'bucket' hanya untuk tes; jalur produksi memakai bucket per-key")
        # Gate DULU, kredensial belakangan. Urutan sebaliknya membuat gate tidak pernah
        # terjangkau di mesin tanpa kredensial: pengguna diberi tahu "belum diset" padahal
        # sebab sebenarnya bisa jadi sumber yang belum disetujui, dan AC 1.3(5) hanya lolos
        # karena kredensial kebetulan ada (temuan tinjauan keamanan fase 1).
        require_source(RYO_TOOLS, env=None if env is None else dict(env))
        source = os.environ if env is None else env
        base_url = source.get(ENV_URL, "")
        api_key = source.get(ENV_KEY, "")
        if not base_url or not api_key:
            raise RyoError(f"{ENV_URL}/{ENV_KEY} belum diset (lihat .env.example)")
        return cls(base_url, api_key, **kwargs)

    # -- housekeeping --
    def __repr__(self) -> str:
        """Tidak pernah menampilkan kredensial (str() memakai repr yang sama)."""
        return f"RyoClient(base_url={self.base_url!r}, max_retries={self._max_retries})"

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> RyoClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- internal --
    def _auth_headers(self) -> dict[str, str]:
        """Dibuat sesaat sebelum dipakai; jangan simpan hasilnya di variabel lokal (f_locals)."""
        return {"Authorization": f"Bearer {self._key.reveal()}", "Content-Type": "application/json"}

    def _safe(self, text: str) -> str:
        """Kebijakan redaksi klien live: setiap bentuk kunci yang dienumerasi `_secret_forms`."""
        return _redact(text, self._key.reveal())

    def _backoff(self, attempt: int) -> float:
        """Eksponensial + jitter, dibatasi MAX_BACKOFF_S (api-facts §A)."""
        return min(MAX_BACKOFF_S, 2.0**attempt + self._rng.uniform(0.0, 1.0))

    def _retry_after(self, response: httpx.Response, attempt: int) -> float:
        """Hormati header Retry-After (detik); bentuk lain → backoff biasa."""
        raw = response.headers.get("retry-after")
        if raw is not None:
            try:
                return max(0.0, min(MAX_BACKOFF_S, float(raw.strip())))
            except ValueError:
                pass
        return self._backoff(attempt)

    def _note_headers(self, response: httpx.Response) -> None:
        self.last_rate_limit = RateLimitInfo.from_headers(response.headers)

    def _message(self, label: str, exc: BaseException) -> str:
        """Pesan kegagalan. Teks exception bisa berasal dari server → lewat `_server_text`."""
        return self._server_text(f"{label}: {type(exc).__name__}: {exc}", MAX_ERROR_MESSAGE_CHARS)

    def _unexpected(self, label: str, exc: BaseException) -> RyoError:
        """Kegagalan di luar dugaan tetap menjadi RyoError.

        Pemanggil (perekam, skill) menangkap `RyoError` per komponen (ADR-004); membiarkan
        `TypeError`/`ValueError`/`httpx.InvalidURL` lolos akan menghentikan satu siklus penuh.
        """
        return RyoError(self._message(label, exc))

    def _parse_json(self, label: str, response: httpx.Response) -> Any:
        failure: RyoError
        size = len(response.content)
        if size > MAX_RESPONSE_BYTES:
            # ditolak setelah fakta (body sudah di memori) — lihat catatan di MAX_RESPONSE_BYTES
            raise RyoProtocolError(f"{label}: respons {size} byte melebihi batas {MAX_RESPONSE_BYTES}")
        try:
            return response.json()
        except ValueError:
            failure = RyoProtocolError(f"{label}: respons bukan JSON")
        except Exception as exc:  # noqa: BLE001 — semua kegagalan harus jadi RyoError
            failure = self._unexpected(label, exc)
        raise failure  # di luar blok except → __context__ tetap None

    def _request(self, method: str, path: str, *, with_auth: bool, json_body: Any = None) -> Any:
        """Satu percobaan HTTP tanpa retry; dipakai endpoint tanpa kuota."""
        failure: RyoError | None = None
        try:
            return self._request_once(method, path, with_auth=with_auth, json_body=json_body)
        except RyoError as exc:
            failure = exc
        except Exception as exc:  # noqa: BLE001 — pagar terakhir, lihat `call`
            failure = self._unexpected(path, exc)
        raise failure

    def _request_once(self, method: str, path: str, *, with_auth: bool, json_body: Any = None) -> Any:
        url = f"{self.base_url}{path}"
        response: httpx.Response | None = None
        failure: RyoError | None = None
        try:
            response = self._http.request(
                method,
                url,
                headers=self._auth_headers() if with_auth else {},
                json=json_body,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            failure = RyoNetworkError(self._message(path, exc))
        except Exception as exc:  # noqa: BLE001 — semua kegagalan harus jadi RyoError
            failure = self._unexpected(path, exc)
        if failure is not None:
            raise failure
        # failure None ⇒ response pasti terisi (satu-satunya jalur lain melempar)
        try:
            self._note_headers(response)
            self._raise_for_status(path, response)
        except RyoError as exc:
            failure = exc
        except Exception as exc:  # noqa: BLE001
            failure = self._unexpected(path, exc)
        if failure is not None:
            raise failure
        return self._parse_json(path, response)

    def _trace_id(self, response: httpx.Response) -> str | None:
        """`x-trace-id` ada di setiap respons (api-facts §A) tetapi tetap tidak tepercaya."""
        return _clean_trace_id(response.headers.get("x-trace-id"), self._key.reveal())

    def _body_head(self, response: httpx.Response) -> str:
        """Ambil MAX_BODY_READ_BYTES byte pertama saja.

        Jujur soal batasnya: httpx sudah menarik seluruh body ke memori sebelum kita
        dipanggil, jadi ini TIDAK mencegah body raksasa masuk memori — yang dicegah hanya
        salinan kedua (decode + redaksi + saring) atas body raksasa itu.
        """
        raw = response.content[:MAX_BODY_READ_BYTES]
        if not raw:
            return ""
        return raw.decode(response.encoding or "utf-8", errors="replace")

    def _body_excerpt(self, response: httpx.Response) -> str:
        """Cuplikan body error; urutannya milik `_server_text`, bukan disalin ke sini."""
        head = self._body_head(response)
        if not head:
            return ""
        return self._server_text(head, MAX_BODY_EXCERPT_CHARS)

    def _error_code(self, response: httpx.Response) -> str | None:
        """`code` UPPER_SNAKE dari body error `{code, message, details, trace_id}` (api-facts §A).

        Body error dikendalikan server juga, jadi tetap diredaksi + disaring + dipotong.
        """
        head = self._body_head(response)
        if not head:
            return None
        try:
            body = json.loads(head)
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        code = body.get("code")
        if not isinstance(code, str):
            return None
        return self._server_text(code, MAX_ERROR_CODE_CHARS).strip() or None

    def _raise_for_status(self, label: str, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        trace_id = self._trace_id(response)
        code = self._error_code(response)
        excerpt = self._body_excerpt(response)
        raise RyoHTTPError(
            f"{label}: HTTP {response.status_code}"
            + (f" code={code}" if code else "")
            + (f" trace={trace_id}" if trace_id else "")
            + (f" body={excerpt}" if excerpt else ""),
            status_code=response.status_code,
            code=code,
            trace_id=trace_id,
        )

    # -- API --
    def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST /tools/{tool}/call → envelope apa adanya; gagal → RyoError (lihat docstring modul).

        Semua exception dilempar DI LUAR blok `except` supaya `__context__` tetap None:
        exception httpx membawa objek request (dan header Authorization di dalamnya).
        """
        failure: RyoError | None = None
        try:
            return self._call_with_retry(tool, args)
        except RyoError as exc:
            failure = exc
        except Exception as exc:  # noqa: BLE001 — pagar terakhir: token bucket, logging, dan
            # sleep_fn yang disuntik pun tidak boleh lolos sebagai non-RyoError. KeyboardInterrupt/
            # SystemExit/CancelledError adalah BaseException → sengaja TIDAK tertangkap.
            failure = self._unexpected(tool, exc)
        raise failure

    def _call_with_retry(self, tool: str, args: dict[str, Any] | None) -> dict[str, Any]:
        _check_tool(tool)
        url = f"{self.base_url}/tools/{tool}/call"
        failure: RyoError | None = None
        try:
            payload = _drop_none(dict(args or {}))
        except Exception as exc:  # noqa: BLE001 — argumen aneh tetap jadi RyoError
            failure = self._unexpected(tool, exc)
        if failure is not None:
            raise failure

        attempt = 0
        while True:
            self._bucket.take()
            response: httpx.Response | None = None
            failure = None
            retryable = False
            kind = ""
            try:
                # header dibuat inline: jangan simpan kredensial di variabel lokal (f_locals)
                response = self._http.post(
                    url,
                    headers=self._auth_headers(),
                    json=payload,
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                failure = RyoNetworkError(self._message(tool, exc))
                retryable = True
                kind = type(exc).__name__
            except Exception as exc:  # noqa: BLE001 — mis. httpx.InvalidURL, TypeError JSON
                failure = self._unexpected(tool, exc)
            if failure is not None:
                error = failure
                if not retryable or attempt >= self._max_retries:
                    raise error
                delay = self._backoff(attempt)
                log.warning("%s: jaringan gagal (%s), backoff %.1fs", tool, kind, delay)
                self._sleep(delay)
                attempt += 1
                continue

            delay = None
            try:
                self._note_headers(response)
                if response.status_code in RETRYABLE_STATUS and attempt < self._max_retries:
                    delay = self._retry_after(response, attempt)
                    kind = str(response.status_code)
            except Exception as exc:  # noqa: BLE001
                failure = self._unexpected(tool, exc)
            if failure is not None:
                raise failure
            if delay is not None:
                log.warning("%s: HTTP %s, backoff %.1fs", tool, kind, delay)
                self._sleep(delay)
                attempt += 1
                continue

            envelope: dict[str, Any] | None = None
            try:
                # 4xx selain 429 (argumen invalid) berhenti di sini — tidak diretry.
                self._raise_for_status(tool, response)
                envelope = self._check_envelope(tool, self._parse_json(tool, response))
            except RyoError as exc:
                failure = exc
            except Exception as exc:  # noqa: BLE001
                failure = self._unexpected(tool, exc)
            if failure is not None:
                raise failure
            return envelope

    def health(self) -> dict[str, Any]:
        """GET /health — tanpa auth, tanpa kuota (api-facts §A)."""
        body = self._request("GET", "/health", with_auth=False)
        if not isinstance(body, dict):
            raise RyoProtocolError("/health: respons bukan objek")
        return body

    def whoami(self) -> dict[str, Any]:
        """GET /whoami — auth, tanpa kuota; memuat scopes/expires_at/rate_limit_per_min."""
        body = self._request("GET", "/whoami", with_auth=True)
        if not isinstance(body, dict):
            raise RyoProtocolError("/whoami: respons bukan objek")
        return body

    def catalog(self) -> list[dict[str, Any]]:
        """GET /tools — auth, tanpa kuota; daftar {name, description, inputSchema}."""
        body = self._request("GET", "/tools", with_auth=True)
        if not isinstance(body, list):
            raise RyoProtocolError("/tools: respons bukan daftar")
        return body


# --- klien fixture (nol jaringan) --------------------------------------------------------
class FixtureClient(BaseRyoClient):
    """Antarmuka sama dengan :class:`RyoClient`, dibaca dari ``docs/samples/*.json``.

    Dipakai tes dan `make demo` offline. Tidak pernah menyentuh jaringan dan tidak pernah
    memegang kredensial. Envelope fixture (termasuk ``status: partial`` dan seksi
    ``unavailable``) diteruskan apa adanya.
    """

    def __init__(self, samples_dir: str | Path = _SAMPLES_DIR) -> None:
        self.samples_dir = Path(samples_dir)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.last_rate_limit: RateLimitInfo | None = None

    def __repr__(self) -> str:
        return f"FixtureClient(samples_dir={str(self.samples_dir)!r}, calls={len(self.calls)})"

    def _safe(self, text: str) -> str:
        """Tidak memegang kredensial: tidak ada yang bisa diredaksi, dan tidak ada yang
        perlu. Ditulis eksplisit (bukan diwarisi) supaya kebijakan tiap klien terbaca."""
        return text

    def _read_json(self, name: str) -> Any:
        path = self.samples_dir / f"{name}.json"
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            raise RyoError(f"fixture tidak ada: {name}.json") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise RyoProtocolError(f"fixture {name}.json bukan JSON sah") from None

    def _read_rate_limit(self, tool: str) -> None:
        """Ambil header rate-limit dari `<tool>.headers` bila sampelnya ada."""
        path = self.samples_dir / f"{tool}.headers"
        if not path.exists():
            return
        headers: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
        self.last_rate_limit = RateLimitInfo.from_headers(headers)

    def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Kembalikan envelope tersimpan untuk `tool`; argumen dicatat di `self.calls`."""
        _check_tool(tool)
        payload = _drop_none(dict(args or {}))
        self.calls.append((tool, payload))
        self._read_rate_limit(tool)
        return self._check_envelope(tool, self._read_json(tool))

    def health(self) -> dict[str, Any]:
        body = self._read_json("health")
        if not isinstance(body, dict):
            raise RyoProtocolError("/health: fixture bukan objek")
        return body

    def whoami(self) -> dict[str, Any]:
        body = self._read_json("whoami")
        if not isinstance(body, dict):
            raise RyoProtocolError("/whoami: fixture bukan objek")
        return body

    def catalog(self) -> list[dict[str, Any]]:
        body = self._read_json("catalog")
        if not isinstance(body, list):
            raise RyoProtocolError("/tools: fixture bukan daftar")
        return body
