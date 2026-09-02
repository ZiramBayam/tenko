"""`python -m core.skill_cli <skill> [args...]` — satu envelope JSON ke stdout, exit 0.

Dipakai `make skill ARGS="preflight SOL"` dan naskah demo (docs/spec.md §7 langkah 1).
Kerangka TASKS 1.3: registry masih kosong, jadi `preflight` menghasilkan envelope
``unavailable`` yang JUJUR ("skill 'preflight' not registered") — bukan GO palsu dan bukan
traceback. TASKS 2.2 yang mengisinya lewat :data:`core.skill.SKILL_MODULES`.

Sifat yang dijaga ``tests/test_skill_cli.py``:

* **Exit code selalu 0 selama satu envelope berhasil ditulis.** Gagal adalah DATA di sini
  (``status``/``warnings``), bukan sinyal proses; `make demo` dan chaos test (TASKS 4.5)
  menuntut nol traceback.
* **stdout = tepat satu baris JSON kanonik** (``sort_keys``, separator rapat) memakai
  :func:`core.store.canonical_json` supaya hanya ada SATU definisi kanonikalisasi di repo.
  Diagnostik apa pun pergi ke stderr lewat ``logging``, tidak pernah ke stdout.
* **Deterministik byte-per-byte antar run.** Tidak ada `now()` di jalur ini: ``as_of``
  diturunkan dari bukti (``None`` bila tidak ada bukti — lihat `core/skill.py`), sehingga
  dua run atas keadaan yang sama menghasilkan byte yang sama.
* **Pesan exception tidak pernah ikut keluar — ke stdout MAUPUN ke log.** Exception dari
  skill bisa memuat apa saja, termasuk kredensial yang tidak sengaja di-format ke dalamnya,
  dan `scripts/daemons.sh` mengarahkan stderr ke `logs/*.log`; yang dilaporkan hanya nama
  KELAS-nya, tanpa traceback. Pengecualian: kegagalan yang pesannya kita tulis sendiri
  (:class:`core.skill.SkillError`, :class:`core.sources.SourcesError`) — itu justru
  informasi yang dibutuhkan pengguna (mis. "sumber belum approved", "host tidak
  terjangkau", "HTTP 429") — lihat :data:`SPEAKABLE_ERRORS`.
* **Teks yang ikut ke envelope dibatasi panjang & charset.** Nama skill dan argumen datang
  dari shell: keduanya di-escape dan dipotong (:func:`core.skill.clean_text`) sebelum masuk
  ``tool``/``request``/``warnings``/log, sehingga argumen 100 kB tidak menjadi envelope
  100 kB dan escape ANSI tidak pernah sampai ke terminal atau `logs/*.log`.
* **Envelope divalidasi ULANG sebelum dikembalikan/dicetak.** Model sudah `frozen`, tetapi
  ``data`` adalah dict biasa yang isinya masih bisa ditambal setelah validasi, dan
  ``model_construct()`` melewatkan validator sama sekali. Yang keluar dari :func:`run`
  adalah salinan tervalidasi; gagal validasi → envelope `unavailable` yang jujur.
* **stdout selalu satu dokumen JSON yang bisa di-decode.** Argumen yang bukan UTF-8 sah
  (``sys.argv`` memakai `surrogateescape`) dibersihkan di batas, dan bila locale stdout
  tidak sanggup UTF-8 keluarannya di-escape ASCII — bukan traceback, bukan exit bukan-nol.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, TextIO

from core.ryo_client import RyoError
from core.skill import TOOL_LIMIT, WARNING_LIMIT, SkillError, SkillResult, clean_text, load_skill
from core.sources import SourcesError
from core.store import canonical_json

log = logging.getLogger("core.skill_cli")

USAGE = "usage: python -m core.skill_cli <skill> [args...]"
CLI_TOOL = "skill_cli"

# Kegagalan yang pesannya KITA tulis sendiri, jadi aman dicetak apa adanya:
# `SkillError`/`SourcesError` (teks kami, tanpa bahan dari luar) dan `RyoError` (teks kami
# yang sudah melewati `_redact` → `_printable` → potong di `core/ryo_client.py`). Justru di
# situlah satu-satunya alasan gagal yang bisa ditindaklanjuti berada: "host tidak
# terjangkau", "HTTP 429", "RYO_MCP_URL/RYO_MCP_KEY belum diset".
# JANGAN melebarkan tuple ini menjadi `Exception`: SDK pihak ketiga (mis. LLM di TASKS 2.3)
# tidak punya mesin redaksi, dan pesannya bisa memuat kredensial.
SPEAKABLE_ERRORS: tuple[type[BaseException], ...] = (SkillError, SourcesError, RyoError)

# Batas teks CLI. Batas SEBENARNYA ada di model (`core/skill.py` membersihkan `tool`,
# `request`, `summary`, `availability`, `warnings`), karena skill 2.2 merakit envelope-nya
# sendiri dan pembatasan sisi CLI tidak ikut ke sana. Yang di bawah ini hanya untuk teks yang
# dirakit CLI: berapa banyak argumen yang DICETAK di `request` dan berapa panjang per
# argumen. `WARNING_LIMIT` diimpor, bukan disalin, supaya angkanya cuma ada satu.
ARG_LIMIT = 120
MAX_ARGS = 8


def _safe_text(value: str) -> str:
    """Teks yang PASTI bisa ditulis sebagai UTF-8: surrogate/byte liar diganti.

    `sys.argv` memakai `surrogateescape`, jadi `core.skill_cli $'\xff'` menghasilkan
    ``'\udcff'`` yang meledak saat ditulis ke stdout; payload RYO juga bisa memuat lone
    surrogate (lihat catatan `core/store.py`). Keduanya harus menjadi karakter pengganti,
    bukan traceback: kontrak CLI adalah exit 0 + satu dokumen JSON.
    """
    return value.encode("utf-8", "replace").decode("utf-8")


def _label(name: str) -> str:
    """Nama skill yang boleh masuk envelope DAN log: di-escape lalu dipotong.

    Nama aslinya tetap dipakai untuk mencari skill di registry — yang dibatasi hanya
    bentuk yang DILAPORKAN, supaya nama sah sepanjang berapa pun tetap bisa ditemukan.
    """
    return clean_text(name, TOOL_LIMIT)


def _speakable(exc: BaseException) -> str:
    """Pesan dari :data:`SPEAKABLE_ERRORS`: sudah aman, tinggal dibatasi panjang & charset.

    `SkillError` dari `load_skill` menyisipkan nama skill mentah, jadi pemotongan di sini
    bukan formalitas: tanpa ia, nama 100 kB kembali masuk ke `warnings`.
    """
    return clean_text(str(exc), WARNING_LIMIT)


def _request(name: str, args: list[str]) -> dict[str, Any]:
    """`request` yang JUJUR tetapi terbatas: argumen dipotong, jumlah aslinya disebut."""
    request: dict[str, Any] = {
        "skill": _label(name),
        "args": [clean_text(value, ARG_LIMIT) for value in args[:MAX_ARGS]],
    }
    if len(args) > MAX_ARGS:
        # bukan penyembunyian: pembaca tahu ada berapa, hanya isinya yang tidak dicetak
        request["args_total"] = len(args)
    return request


def _fallback(tool: Any, reason: str) -> SkillResult:
    """Envelope minimal saat hasil skill sendiri tidak bisa dilaporkan."""
    return SkillResult.unavailable(
        tool if isinstance(tool, str) and tool else CLI_TOOL,
        warnings=[reason],
    )


def _envelope_text(result: SkillResult) -> str:
    """Satu baris JSON kanonik, DIVALIDASI ULANG tepat sebelum dicetak.

    Validasi ulang bukan paranoia berlebih: `data` adalah dict biasa, jadi skill (TASKS 2.2)
    bisa merakit envelope sah lalu menambal isinya — mis. menaruh angka pada item
    `unspoken`, atau membalik `availability` — tanpa validator pernah dipanggil lagi. Model
    yang `frozen` menutup penggantian field; ronde ini menutup penambalan isi `data`.
    """
    try:
        checked = SkillResult.model_validate(result.model_dump())
        payload = checked.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 — validasi akhir tidak boleh punya jalur keluar
        log.warning("hasil skill ditolak validasi akhir: %s", type(exc).__name__)
        tool = getattr(result, "tool", CLI_TOOL)
        payload = _fallback(tool, f"result failed final validation: {type(exc).__name__}").model_dump(
            mode="json"
        )
    try:
        return _safe_text(canonical_json(payload))
    except Exception as exc:  # noqa: BLE001 — stdout wajib tetap satu baris JSON yang sah
        log.warning("hasil skill tidak bisa dikanonikalisasi: %s", type(exc).__name__)
        fallback = _fallback(payload.get("tool"), f"result could not be serialised: {type(exc).__name__}")
        return _safe_text(canonical_json(fallback.model_dump(mode="json")))


def run(argv: list[str]) -> SkillResult:
    """Jalankan satu skill dan kembalikan envelope-nya. Tidak pernah melempar."""
    if not argv:
        return SkillResult.unavailable(
            CLI_TOOL,
            request={"skill": None, "args": []},
            warnings=[f"missing skill name; {USAGE}"],
        )
    name, args = argv[0], list(argv[1:])
    label = _label(name)
    request = _request(name, args)
    try:
        fn = load_skill(name)
    except SkillError as exc:
        message = _speakable(exc)
        log.warning("skill %r gagal dimuat: %s", label, message)
        return SkillResult.unavailable(label, request=request, warnings=[message])
    if fn is None:
        return SkillResult.unavailable(label, request=request, warnings=[f"skill {label!r} not registered"])
    return _invoke(label, fn, args, request)


def _invoke(label: str, fn: Any, args: list[str], request: dict[str, Any]) -> SkillResult:
    """Panggil skill; setiap kegagalan menjadi envelope, bukan exception yang lolos."""
    try:
        result = fn(*args)
    except TypeError as exc:
        # Bisa berarti argumen CLI salah jumlah, bisa juga bug di dalam skill; keduanya
        # dilaporkan sebagai argumen karena CLI tidak bisa membedakannya dengan jujur.
        log.warning("skill %r menolak argumen: %s", label, type(exc).__name__)
        return SkillResult.unavailable(
            label, request=request, warnings=[f"skill {label!r} rejected the arguments; {USAGE}"]
        )
    except SPEAKABLE_ERRORS as exc:
        # Pesan yang kita tulis sendiri: aman DAN justru berguna — gate ADR-005
        # (`SourcesError`) maupun sebab jaringan/HTTP dari `core/ryo_client.py` (`RyoError`,
        # sudah diredaksi di sana). Menyembunyikannya berarti pengguna hanya melihat nama
        # kelas dan tidak tahu harus berbuat apa.
        message = _speakable(exc)
        log.warning("skill %r gagal: %s", label, message)
        return SkillResult.unavailable(label, request=request, warnings=[message])
    except Exception as exc:  # noqa: BLE001 — skill pihak lain; kontrak CLI: selalu exit 0
        # Pesan pihak lain tidak pernah dicetak: bisa memuat kredensial atau teks server.
        # Juga TIDAK ke log (scripts/daemons.sh mengarahkan stderr ke logs/*.log, dan SDK
        # pihak ketiga — mis. LLM di TASKS 2.3 — tidak punya mesin redaksi seperti
        # core/ryo_client.py). Yang dicatat hanya nama kelasnya; traceback pun tidak.
        log.warning("skill %r melempar %s", label, type(exc).__name__)
        return SkillResult.unavailable(
            label, request=request, warnings=[f"skill {label!r} failed: {type(exc).__name__}"]
        )
    if not isinstance(result, SkillResult):
        log.error("skill %r mengembalikan %s, bukan SkillResult", label, type(result).__name__)
        return SkillResult.unavailable(
            label,
            request=request,
            warnings=[f"skill {label!r} returned {type(result).__name__}, expected SkillResult"],
        )
    try:
        # Validasi ULANG di batas: `isinstance` tidak membuktikan apa pun tentang isi.
        # `data` adalah dict biasa, jadi skill bisa merakit envelope sah lalu menambalnya
        # (angka pada item `unspoken`, `availability` dibalik), dan `model_construct()`
        # melewatkan validator sama sekali. Yang dikembalikan adalah SALINAN tervalidasi,
        # bukan objek skill, supaya penambalan setelah ini pun tidak ikut terbawa.
        return SkillResult.model_validate(result.model_dump())
    except Exception as exc:  # noqa: BLE001 — envelope bohong tidak boleh lolos ke pemanggil
        log.warning("envelope dari skill %r ditolak validasi akhir: %s", label, type(exc).__name__)
        return SkillResult.unavailable(
            label,
            request=request,
            warnings=[f"skill {label!r} produced an envelope that failed final validation"],
        )


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    """Titik masuk `python -m core.skill_cli`. Selalu 0 bila envelope berhasil ditulis."""
    raw = list(sys.argv[1:] if argv is None else argv)
    args = [_safe_text(value) for value in raw]
    if args != raw:
        # Argumen yang bukan UTF-8 sah tetap diproses (nama skill begitu tidak akan
        # ditemukan → envelope "not registered"), tetapi tidak boleh membawa byte liar
        # ke stdout maupun ke pencarian registry.
        log.warning("argumen memuat byte yang bukan UTF-8 sah; diganti")
    stream = sys.stdout if out is None else out
    text = _envelope_text(run(args))
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:
        # Locale stdout tidak bisa UTF-8 (mis. LC_ALL=C dengan PYTHONIOENCODING=ascii):
        # tetap keluarkan JSON yang sah, kali ini di-escape ASCII murni.
        log.warning("stdout tidak bisa menulis UTF-8; memakai escape ASCII")
        stream.write(json.dumps(json.loads(text), sort_keys=True, separators=(",", ":")) + "\n")
    stream.flush()
    return 0


def _configure_logging() -> None:
    """Diagnostik ke stderr saja; stdout milik envelope (dan harus tetap deterministik)."""
    level = os.environ.get("LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.WARNING),
        stream=sys.stderr,
        format="%(levelname)s %(name)s %(message)s",
    )


if __name__ == "__main__":  # pragma: no cover - dijalankan lewat subprocess di tes
    _configure_logging()
    raise SystemExit(main())
