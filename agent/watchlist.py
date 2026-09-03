"""Daftar simbol yang dipanggil agen tiap tick (TASKS 2.4).

Empat simbol inti (`agent.params.CORE_SYMBOLS`), boleh diganti lewat `--symbols` atau env
`AGENT_WATCHLIST`/`WATCHLIST` (`scripts/daemons.sh` memakai nama kedua). Watchlist TIDAK ikut
ke `params_hash` mana pun: ia memilih pertanyaan mana yang diajukan, bukan bagaimana jawabannya
dinilai.

`scan_market` **tidak** dipakai untuk menyusun watchlist di sini (spec §5 menyebutnya opsional):
agen ini store-first murni dan tidak memanggil satu tool pun, jadi "top_n dari perekam" hanya
akan memindahkan pilihan simbol ke baris store yang belum tentu ada. Empat simbol inti adalah
simbol yang benar-benar direkam `core/recorder.py`, sehingga tiap baris watchlist punya bukti.

Normalisasi memakai `preflight.skill.normalize_symbol` — SATU definisi, karena simbol di sini
dipakai persis sebagai kunci pencarian store. Entri yang tidak bisa dinormalkan **dibuang dan
disebut** (log + daftar `dropped`), bukan diperbaiki diam-diam.

**Batas yang diakui (security-reviewer 4 Sep 2026; task tersendiri, TIDAK ditambal di sini).**
`normalize_symbol` menjawab "bisakah teks ini dipakai sebagai kunci pencarian store", BUKAN
"apakah RYO mengenal simbol ini" — dan tidak ada satu pun daftar simbol sah yang diterbitkan
RYO (docs/api-facts.md §A: input adalah **simbol**, tanpa enum). Akibatnya `--symbols` menerima
teks seperti `--`, `"BTC"` (dengan tanda kutip ikut), `ＳＯＬ` (bentuk lebar), atau `BNB😀`:
semuanya lolos normalisasi, tidak menemukan snapshot apa pun, dan — sejak
`agent.main.InsufficientEvidence` — **tidak lagi meninggalkan baris `decisions`**. Yang tersisa
adalah jejak di log dan (untuk baris yang terlanjur ditulis sebelum perbaikan itu, 7 baris di
`data/ryo.db`) baris ledger yang tidak bisa dihapus karena store append-only. Penutup yang
benar adalah allowlist simbol yang punya bukti di store; itu perubahan tersendiri.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agent.params import CORE_SYMBOLS
from preflight.skill import normalize_symbol

log = logging.getLogger("agent.watchlist")

#: Nama env yang dibaca, berurutan. `WATCHLIST` sudah dipakai `scripts/daemons.sh`.
ENV_NAMES: tuple[str, ...] = ("AGENT_WATCHLIST", "WATCHLIST")

#: Batas atas cacah simbol per tick. Bukan kuota jaringan (agen tidak memanggil apa pun),
#: melainkan batas kerja: tiap simbol = 5 pembacaan store + satu bundle beku.
MAX_SYMBOLS = 8

#: Pemisah daftar: koma/titik-koma/baris baru SAJA. Spasi **bukan** pemisah — simbol RYO
#: tidak pernah mengandung spasi, jadi `"A B"` adalah satu entri yang tidak sah (dan dibuang
#: dengan disebut), bukan dua simbol yang sah.
SEPARATORS = ",;\n\r\t"


@dataclass(frozen=True, slots=True)
class Watchlist:
    """Simbol yang dipakai + jejak entri yang dibuang (tidak ada yang hilang diam-diam)."""

    symbols: tuple[str, ...]
    dropped: tuple[str, ...] = ()
    truncated: int = 0
    source: str = "default"

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.symbols)

    def __len__(self) -> int:
        return len(self.symbols)


def _split(raw: str) -> list[str]:
    for sep in SEPARATORS[1:]:
        raw = raw.replace(sep, SEPARATORS[0])
    return [entry.strip() for entry in raw.split(SEPARATORS[0]) if entry.strip()]


def watchlist(
    argument: str | Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> Watchlist:
    """Simbol tick ini: `argument` > env (:data:`ENV_NAMES`) > :data:`CORE_SYMBOLS`.

    Duplikat dibuang dengan mempertahankan urutan (urutan = urutan pemanggilan, dan itu
    terlihat di ledger). Cacah dipotong ke :data:`MAX_SYMBOLS` dengan jumlah aslinya tetap
    disebut lewat :attr:`Watchlist.truncated`.
    """
    environ = os.environ if env is None else env
    source = "argument"
    raw_entries: list[str]
    if argument is None:
        raw_text = ""
        for name in ENV_NAMES:
            value = environ.get(name)
            if isinstance(value, str) and value.strip():
                raw_text, source = value, f"env:{name}"
                break
        if not raw_text:
            raw_entries, source = list(CORE_SYMBOLS), "default"
        else:
            raw_entries = _split(raw_text)
    elif isinstance(argument, str):
        raw_entries = _split(argument)
    else:
        raw_entries = [str(entry).strip() for entry in argument if str(entry).strip()]

    symbols: list[str] = []
    dropped: list[str] = []
    for entry in raw_entries:
        symbol = normalize_symbol(entry)
        if symbol is None:
            dropped.append(entry[:32])
            continue
        if symbol not in symbols:
            symbols.append(symbol)
    if dropped:
        log.warning("watchlist: %d entri tidak bisa dibaca sebagai simbol dan dibuang", len(dropped))
    truncated = max(0, len(symbols) - MAX_SYMBOLS)
    if truncated:
        log.warning("watchlist: %d simbol melebihi batas %d dan tidak dipanggil", truncated, MAX_SYMBOLS)
    return Watchlist(
        symbols=tuple(symbols[:MAX_SYMBOLS]),
        dropped=tuple(dropped),
        truncated=truncated,
        source=source,
    )
