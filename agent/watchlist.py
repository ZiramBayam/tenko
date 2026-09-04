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

**Bentuk simbol disaring saat DIBACA di sini, bukan di `normalize_symbol` (TASKS 0.11).**
`normalize_symbol` menjawab "bisakah teks ini dipakai sebagai kunci pencarian store" dan harus
tetap KEMBAR dengan `core.recorder._normalize_symbol` (paritasnya dipatok tes) — jadi ia tidak
boleh diperketat. Yang ditambahkan di sini adalah :func:`plausible_symbol`: pertanyaan yang
berbeda, "apakah teks ini berbentuk seperti ticker sama sekali". Tanpanya `--symbols` menerima
`--`, `"BTC"` (tanda kutip ikut), `{"SYMBOL":"SOL"}`, `123`, `ＳＯＬ` (bentuk lebar), `BNB😀`,
dan `SОL` (huruf О Kiril — tak bisa dibedakan mata dari `SOL` di Layar A dan di kartu).

**Apa yang saringan ini TIDAK lakukan — baca sebelum mengklaim apa pun** (security-reviewer
4 Sep 2026):

* Ia **bukan** daftar simbol sah RYO. Daftar seperti itu tidak diterbitkan (docs/api-facts.md
  §A: input adalah **simbol**, tanpa enum).
* Simbol yang berbentuk wajar tetapi TIDAK ADA — `ZZZQQQ`, `NOTAREALTOKEN` — tetap lolos dan
  **masih menulis baris `decisions` permanen**, karena `agent.main` hanya menuntut
  `snapshot_ids` tidak kosong, sementara `market_overview`/`monitor_market_sentiment_shift`
  dicari dengan args `{}` sehingga selalu ada bukti market-wide untuk simbol apa pun. `replay`
  atas baris itu bahkan mencetak `IDENTICAL`. Penutupnya adalah **TASKS 0.13**, bukan modul
  ini: syaratnya harus "ada ≥1 snapshot dari tool ber-simbol untuk simbol ini" —
  perhitungan yang `ui/app.py::_symbol_evidence` sudah punya, jadi hari ini agen memakai
  standar yang lebih rendah daripada UI-nya sendiri.
* Ia lebih ketat daripada perekam, dan asimetri itu diakui: `core.recorder._normalize_symbol`
  AKAN merekam `$WIF`, `ETHER.FI`, `BTC-PERP`, dan `ＳＯＬ` ke store, sementara agen tidak akan
  pernah bisa memutuskannya. Hari ini dampaknya nol (0 dari 103 simbol nyata di `data/ryo.db`
  tertolak), dan `$`/`.` justru karakter yang dipakai bentuk sampah — tetapi bila kelak muncul
  ticker meme ber-`$`, itu bug fungsional, bukan kejutan.
* Ia dipasang di tepi pembacaan (`--symbols`/env), BUKAN di batas tulis: `agent.main.tick()`
  dan `core.store.insert_decision()` menerima simbol apa pun bila dipanggil langsung. Hari
  ini tidak ada pemanggil produksi kedua, jadi invariannya berlaku secara praktik — tetapi
  bukan secara struktur.

Catatan angka: klaim "7 baris `decisions` untuk simbol yang tidak ada" yang sempat ditulis di
sini TIDAK bisa ditunjuk barisnya. Sapuan `data/ryo.db` + 13 backup (`mode=ro`) memberi 27
baris `decisions`, simbolnya hanya SOL/BTC/ETH/BNB. Yang benar-benar ada adalah 4 baris
ber-`snapshot_ids_json='[]'` (id 20-23) dengan simbol nyata.
"""

from __future__ import annotations

import logging
import os
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agent.params import CORE_SYMBOLS
from core.skill import TRUNCATED_MARK
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

#: Kategori Unicode yang boleh muncul di sebuah simbol: huruf apa pun (aksara apa pun) dan
#: angka desimal. Bukan ASCII saja — `data/ryo.db` memuat simbol nyata dari `scan_market`
#: seperti `牛来` dan `龙虾`, dan menolaknya berarti agen tidak akan pernah bisa memutuskan
#: token yang benar-benar dikembalikan RYO. Yang dibuang adalah tanda baca, simbol, emoji,
#: dan mark — bentuk yang tidak pernah menjadi ticker.
SYMBOL_CATEGORIES: frozenset[str] = frozenset({"Lu", "Ll", "Lt", "Lm", "Lo", "Nd"})
#: Kategori huruf; sebuah simbol harus memuat setidaknya satu. Ini yang menolak `123`.
LETTER_CATEGORIES: frozenset[str] = frozenset({"Lu", "Ll", "Lt", "Lm", "Lo"})

#: Berapa nama entri yang ditolak yang ikut dicetak ke satu baris log. Tanpa batas ini,
#: `--symbols` berisi 200.000 entri sampah menghasilkan SATU baris log 1,2 MB (terukur,
#: security-reviewer 0.11) — input operator tidak boleh menentukan ukuran baris log.
MAX_NAMED_DROPS = 20

#: Kanji dan kana dihitung sebagai SATU aksara. Tanpa ini `ドージ` ditolak hanya karena chōonpu
#: `ー` bernama `KATAKANA-HIRAGANA PROLONGED SOUND MARK`, dan `犬コイン` ditolak karena mencampur
#: kanji dengan katakana — keduanya ejaan Jepang yang normal, bukan serangan, dan ini lomba
#: Jepang (temuan SEDANG security-reviewer 0.11). Unicode sendiri memberi U+30FC
#: `Script_Extensions = {Hira, Kana}`; ember ini adalah versi kasarnya.
JAPANESE_SCRIPTS: dict[str, str] = {
    "CJK": "JAPANESE-OR-HAN",
    "HIRAGANA": "JAPANESE-OR-HAN",
    "KATAKANA": "JAPANESE-OR-HAN",
    "KATAKANA-HIRAGANA": "JAPANESE-OR-HAN",
}


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


def plausible_symbol(symbol: str) -> bool:
    """Apakah `symbol` berbentuk seperti ticker (TASKS 0.11)?

    Dijalankan SESUDAH :func:`preflight.skill.normalize_symbol`, jadi teksnya sudah dipangkas,
    di-uppercase, dan bebas spasi/koma/kontrol. Tiga syarat tambahan:

    1. setiap karakter ada di :data:`SYMBOL_CATEGORIES` — menolak `--`, `"BTC"`,
       `{"SYMBOL":"SOL"}`, `BNB😀`;
    2. ada setidaknya satu huruf — menolak `123`, tetapi `1INCH` (ticker nyata) tetap lolos;
    3. teksnya stabil di bawah NFKC — menolak bentuk lebar `ＳＯＬ`;
    4. semua hurufnya berasal dari SATU aksara — menolak CAMPURAN seperti `SОL` (О Kiril di
       tengah huruf Latin) dan `BTCאב123`.

    Syarat (3) sengaja MENOLAK, bukan menormalkan diam-diam menjadi `SOL`: operator yang
    mengetik bentuk lebar sedang menyalin dari sumber yang aneh, dan menebak maksudnya akan
    menulis baris ledger permanen atas tebakan itu.

    Syarat (4) menutup **campur-aksara** (temuan SEDANG security-reviewer 0.11): homoglyph
    lintas-aksara seperti `SOL` vs `SОL`, yang menjadi dua baris ledger tidak bisa dibedakan
    mata di Layar A dan di kartu, dan RTL implisit tanpa satu pun karakter Cf.

    **Yang TIDAK ditutupnya: confusable ber-aksara TUNGGAL.** `ВТС` (Kiril penuh), `ЅОЛ`,
    Cherokee `ᏚᎢᏞ`, dan Yunani `ΒΤΑ` lolos syarat ini dan tetap bisa menjadi baris ledger yang
    tampil sama dengan `BTC` (terbukti: `--symbols 'BTC,ВТС'` menulis dua baris). Menutupnya
    butuh allowlist aksara yang punya bukti di store, bukan sekadar "satu aksara" — dan itu
    keputusan tersendiri.

    Ia sengaja TIDAK menolak aksara non-Latin: `牛来`, `龙虾`, `币安人生`, `비트` ada di
    `data/ryo.db` sebagai simbol yang benar-benar dikembalikan RYO.
    """
    if not symbol:
        return False
    if unicodedata.normalize("NFKC", symbol) != symbol:
        return False
    categories = [unicodedata.category(ch) for ch in symbol]
    if any(category not in SYMBOL_CATEGORIES for category in categories):
        return False
    if not any(category in LETTER_CATEGORIES for category in categories):
        return False
    return len(_letter_scripts(symbol, categories)) == 1


def _letter_scripts(symbol: str, categories: list[str]) -> set[str]:
    """Aksara tiap huruf di `symbol`, ditaksir dari kata pertama nama Unicode-nya.

    `unicodedata` tidak menerbitkan properti Script, tetapi nama karakternya memuatnya:
    `LATIN CAPITAL LETTER S`, `CYRILLIC CAPITAL LETTER O`, `CJK UNIFIED IDEOGRAPH-725B`.
    Angka tidak ikut dihitung — `1INCH` adalah ticker nyata, dan digit ASCII wajar muncul
    bersama aksara mana pun. Huruf tanpa nama (tidak seharusnya ada sesudah `normalize_symbol`)
    dihitung sebagai aksaranya sendiri, sehingga ia MEMPERKETAT, bukan melonggarkan.
    """
    scripts: set[str] = set()
    for char, category in zip(symbol, categories, strict=True):
        if category not in LETTER_CATEGORIES:
            continue
        try:
            script = unicodedata.name(char).split()[0]
        except ValueError:
            scripts.add(f"UNNAMED-{ord(char)}")
            continue
        scripts.add(JAPANESE_SCRIPTS.get(script, script))
    return scripts


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
        if symbol is None or not plausible_symbol(symbol):
            # Potongan DITANDAI: tanpa `TRUNCATED_MARK`, entri `"A"*32 + "BBBB!"` dicetak
            # sebagai `'AAAA…AAAA'` — potongan yang justru BERBENTUK simbol sah, sehingga
            # baris lognya membingungkan operator (temuan RENDAH security-reviewer 0.11).
            dropped.append(entry[:32] + TRUNCATED_MARK if len(entry) > 32 else entry)
            continue
        if symbol not in symbols:
            symbols.append(symbol)
    if dropped:
        # Disebut dengan NAMANYA, bukan hanya dihitung (TASKS 0.11 AC 2): operator yang salah
        # ketik satu simbol harus tahu simbol MANA yang tidak jadi dipanggil. `repr` meng-escape
        # kontrol/baris baru, jadi entri buatan penyerang tidak bisa memalsukan baris log.
        named = ", ".join(repr(entry) for entry in dropped[:MAX_NAMED_DROPS])
        rest = len(dropped) - MAX_NAMED_DROPS
        log.warning(
            "watchlist: %d entri tidak berbentuk simbol dan tidak dipanggil: %s%s",
            len(dropped),
            named,
            f", dan {rest} lainnya" if rest > 0 else "",
        )
    truncated = max(0, len(symbols) - MAX_SYMBOLS)
    if truncated:
        log.warning("watchlist: %d simbol melebihi batas %d dan tidak dipanggil", truncated, MAX_SYMBOLS)
    return Watchlist(
        symbols=tuple(symbols[:MAX_SYMBOLS]),
        dropped=tuple(dropped),
        truncated=truncated,
        source=source,
    )
