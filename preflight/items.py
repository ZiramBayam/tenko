"""Sepuluh item panggilan Tenko (TASKS 2.1, docs/spec.md §4 "Daftar panggilan").

Satu item = satu baris kartu: **tunjuk** (`source`: tool, field PERSIS, snapshot, as_of) →
**sebut** (`value`) → **alasan** (`reason`). Modul ini murni: ia hanya membaca envelope RYO
yang SUDAH dibekukan (tidak ada HTTP, tidak ada store, tidak ada jam lokal) dan mengembalikan
:class:`core.skill.ItemResult`. Perakitan envelope, pemanggilan tool, dan penulisan baris
``decisions`` adalah TASKS 2.2/2.4.

Enam aturan yang ditegakkan di sini, masing-masing punya tes di ``tests/test_items.py``:

1. **`null`/`unavailable`/basi → `unspoken`, tidak pernah 0 dan tidak pernah default.**
   Tidak ada satu pun angka yang diisi kalau sumbernya diam (guide RYO, ADR-004).
2. **`NaN`/`Infinity` → `unspoken` ber-`reason` jujur.** :class:`core.skill.ItemResult`
   MENOLAK nilai non-finit, jadi tanpa pemetaan ini satu ``NaN`` di satu field akan
   menggagalkan konstruksi SELURUH envelope (seluruh keputusan jadi ``unavailable``) alih-alih
   membuat satu item tidak tersebut. :func:`_item` menjaganya dua lapis: tiap pembacaan angka
   lewat :func:`_read_number`, dan sebelum ``ItemResult`` dibuat seluruh ``value``
   diuji-kanonikalisasi sekali lagi.
3. **Kosakata di luar tabel `preflight/params.py` → `unspoken` + warning.** Tidak ditebak,
   tidak dipetakan ke tetangga terdekat, tidak jadi 0/netral, tidak melempar exception
   (docs/api-facts.md §A: enum RYO terbukti bocor — dua nilai baru dalam 13 jam).
4. **Arah acuan = arah plan.** Arah diturunkan dari ``deep_analysis.data.trade_plan``
   (``stop < entry`` → long, ``stop > entry`` → short; 294/294 baris nyata long), BUKAN
   diasumsikan long. Tanpa plan, arah tidak diketahui → item yang butuh arah menjadi
   ``unspoken``, kecuali sikap ``abstain`` (``avoid``/``caution …``/``cautious``) yang
   menentang entry ke arah mana pun dan karena itu tetap merah.
5. **Jam item 1 datang dari data, bukan dari mesin.** Acuan kesegaran = ``as_of`` TERBARU di
   antara bukti beku itu sendiri. Memakai ``datetime.now()`` akan membuat keputusan yang sama
   berubah statusnya saat di-replay besok — persis yang dilarang ADR-003. **Batas yang
   diakui:** karena acuannya internal, item 1 mengukur jarak antar-bukti, BUKAN umur bundle
   terhadap dunia — seluruh bukti bertanggal 2021 tetap hijau. `reason`-nya menyebut batas itu
   dengan kalimatnya sendiri; jam perekam yang ikut dibekukan adalah pekerjaan TASKS 2.2.
6. **Lantai konsistensi keputusan** (:func:`check_decision_floor`): ``blocking`` = tepat
   himpunan penyebab veto, ``GO`` ⇒ tidak ada item merah DAN ada bukti (snapshot + item yang
   tersebut). ``core/replay.py`` sengaja tidak menjaganya — bundle kosong menghasilkan hash
   yang sah — jadi TASKS 2.2 wajib memanggil fungsi ini sebelum menulis baris ``decisions``.

Bentuk `reason` (dibaca manusia di kartu, ditulis kami sendiri — bukan teks RYO):
``"<fakta yang ditunjuk> -> <akibatnya bagi keputusan>"``.

**Batas kepercayaan teks RYO.** Setiap string dari envelope yang masuk ``value``, ``reason``,
``warnings``, atau ``ItemSource.field`` melewati :func:`_quote`, :func:`_text_value`, atau
:func:`_key` — ketiganya memanggil :func:`_clean`, yang meng-escape karakter tak tercetak
(kontrol, CR/LF, ANSI, kontrol arah teks, lone surrogate) dan memotong panjangnya. Termasuk
teks yang dipakai sebagai **kunci** dict (simbol token di item 10) dan jumlah entri/warning
per item (:data:`MAX_ENTRIES`, :data:`MAX_WARNINGS`), supaya envelope 1 MB tidak menjadi
kartu 3 MB. Yang TIDAK dijanjikan di sini: teks bersih tetap teks pihak lain. ``<``/``>``,
kata "assistant:", dan penutup tag apa pun tetap lolos sebagai karakter biasa — melindungi
prompt LLM (TASKS 2.3, bungkus sebagai data) dan render kartu (TASKS 3.2, escape HTML) adalah
tugas task itu, bukan modul ini. Nilai yang SUDAH cocok dengan tabel kosakata
`preflight/params.py` disalin apa adanya: string itu milik kami, bukan milik envelope.

Item 11 (Bunryō) spec §4 TIDAK ada di sini: ia butuh ledger reliabilitas dari ``grading/``
(TASKS 3.3, opsional) yang belum dibangun; item yang tidak punya sumber hanya akan menambah
baris kosong di kartu.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal

from core.skill import ENVELOPE_STATUSES, ItemResult, ItemSource
from core.store import DECISIONS
from preflight.params import (
    ABSTAIN,
    ANALYZE_TOKEN_VERDICT,
    BEARISH,
    BULLISH,
    CALM,
    COMPARE_TOKENS_VERDICT,
    CONFIRMED,
    CONFLUENCE_STATE,
    DEEP_ANALYSIS_VERDICT_CALL,
    DEFAULT_PARAMS,
    FEAR_GREED_CHANGE_DIRECTION,
    FUNDING_CROWDING_STATE,
    ITEM_COMPLETENESS,
    ITEM_CONFLUENCE,
    ITEM_DERIVATIVES,
    ITEM_FRESHNESS,
    ITEM_ORDER,
    ITEM_PLAN,
    ITEM_REGIME,
    ITEM_RELATIVE,
    ITEM_SENTIMENT_SHIFT,
    ITEM_VERDICT_DEEP,
    ITEM_VERDICT_QUICK,
    LIQUIDATION_PRESSURE_STATE,
    LONG,
    MARKET_REGIME,
    NEUTRAL,
    OPTIONAL_SECTIONS,
    REQUIRED_SECTIONS,
    REQUIRED_TOOLS,
    SENTIMENT_LABEL,
    SENTIMENT_REGIME,
    SHORT,
    TECHNICAL_TREND,
    Params,
    Vocabulary,
)

GREEN = "green"
RED = "red"
UNSPOKEN = "unspoken"

TOOL_MARKET_OVERVIEW = "market_overview"
TOOL_MONITOR = "monitor_market_sentiment_shift"
TOOL_ANALYZE = "analyze_token"
TOOL_DEEP = "deep_analysis"
TOOL_COMPARE = "compare_tokens"

#: `tool` untuk item yang MEMANG agregat lintas envelope (item 2). Bukan tool RYO: kalau
#: ditulis nama salah satu tool, kartu akan menunjuk sumber yang tidak menentukan.
TOOL_AGGREGATE = "preflight"

#: Batas panjang string RYO yang disalin ke `value`/`reason` (utang TASKS 1.5: argumen
#: 100 kB tidak boleh masuk utuh ke envelope).
TEXT_LIMIT = 240
QUOTE_LIMIT = 64
#: Panjang maksimum teks yang dipakai sebagai KUNCI/label (simbol token, nama gate).
KEY_LIMIT = 32
#: Berapa banyak entri per koleksi yang boleh masuk `value`/`reason` (sisanya diringkas).
#: `compare_tokens` menerima 2–4 simbol (catalog.json), jadi 8 sudah longgar; batas ini yang
#: membuat envelope berisi 5.000 token tidak menjadi 5.000 baris kartu.
MAX_ENTRIES = 8
#: Berapa banyak warning per item yang boleh dicetak sebelum diringkas jadi satu kalimat.
MAX_WARNINGS = 3

_MISSING = object()


# --- pembacaan aman ----------------------------------------------------------------------
def _dig(obj: Any, *path: str) -> Any:
    """Telusuri path dict; level yang hilang/bukan mapping → :data:`_MISSING`."""
    current = obj
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _clean(text: str, limit: int) -> str:
    """Escape setiap karakter tak tercetak lalu potong ke `limit`.

    Yang di-escape (menjadi ``\\uXXXX`` yang terlihat, bukan dibuang diam-diam): kontrol
    C0/C1 termasuk CR/LF/TAB dan escape ANSI, kontrol arah teks (U+202E dkk.), pemisah baris
    Unicode, dan **lone surrogate**. Tiga alasan, semuanya nyata:

    * `ItemResult.model_dump(mode="json")` melempar ``UnicodeEncodeError`` untuk lone
      surrogate — satu string aneh dari satu simbol akan menggagalkan perakitan SELURUH
      envelope, mode kegagalan yang sama dengan NaN dan ditutup dengan cara yang sama;
    * `reason`/`warnings` adalah kalimat KAMI: teks RYO tidak boleh menyisipkan baris baru,
      penutup tag, atau pembalik arah teks ke dalamnya (bahan prompt TASKS 2.3, kartu 3.2);
    * panjangnya dibatasi supaya satu envelope 1 MB tidak menjadi keputusan 3 MB.

    Ini BUKAN pengganti batas kepercayaan di 2.3/3.2: teks yang sudah bersih tetap teks dari
    pihak lain, dan prompt LLM tetap wajib membungkusnya sebagai data, bukan instruksi.
    """
    parts: list[str] = []
    length = 0
    for char in text:
        piece = char if char.isprintable() else f"\\u{ord(char):04x}"
        if length + len(piece) > limit:
            return "".join(parts) + "...(truncated)"
        parts.append(piece)
        length += len(piece)
    return "".join(parts)


def _quote(value: Any, limit: int = QUOTE_LIMIT) -> str:
    """Nilai mentah RYO untuk pesan: `repr` lalu :func:`_clean` (escape + potong)."""
    if value is _MISSING:
        return "<absent>"
    return _clean(repr(value), limit)


def _text_value(value: Any, limit: int = TEXT_LIMIT) -> str | None:
    """String RYO yang boleh masuk `value`: di-escape dan dipotong; bukan str → None.

    Bukan str dikembalikan sebagai None dengan sengaja — tanpa konversi diam-diam, karena
    angka/objek yang menyamar sebagai teks adalah bentuk kebohongan tersendiri.
    """
    if type(value) is not str:
        return None
    return _clean(value, limit)


def _key(value: Any, limit: int = KEY_LIMIT) -> str:
    """Teks RYO yang dipakai sebagai KUNCI dict (mis. simbol token di item 10).

    Kunci ikut dibersihkan, bukan hanya nilainya: simbol sepanjang 1 MB atau ber-CR/LF akan
    masuk ke `value` sebagai kunci JSON dan ke kartu sebagai label baris.
    """
    cleaned = _text_value(value, limit)
    return cleaned if cleaned is not None else _quote(value, limit)


def _read_number(field_path: str, value: Any) -> tuple[float | None, str | None]:
    """Angka WAJIB: (nilai, None) atau (None, alasan `unspoken`).

    ``NaN``/``Infinity`` punya alasannya sendiri karena penyebabnya beda dari "field kosong"
    dan pembaca kartu berhak tahu bedanya.
    """
    if value is _MISSING or value is None:
        return None, f"{field_path} is null/absent -> nothing to speak"
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None, f"{field_path}={_quote(value)} is not a number -> nothing honest to speak"
    try:
        number = float(value)
    except OverflowError:
        # JSON boleh memuat integer sebesar apa pun; `float()` menyerah di ~1,8e308. Ini
        # BUKAN null dan BUKAN NaN — dan bukan pula alasan untuk meledakkan keputusan
        # (spec §7: nol traceback). Satu item tidak tersebut, sisanya tetap dinilai.
        return None, (
            f"{field_path} is an integer too large for a 64-bit float ({len(str(value))} digits) "
            "-> it cannot be reported as a number, and rounding it would be fabrication"
        )
    if not math.isfinite(number):
        return None, (
            f"{field_path} is NaN/Infinity -> cannot be reported as a number, and a "
            "substitute would be fabricated (ADR-004)"
        )
    return number, None


def _context_number(field_path: str, value: Any) -> tuple[float | None, str | None]:
    """Angka KONTEKS: kosong itu sah (None, None); non-finit tetap memaksa `unspoken`."""
    if value is _MISSING or value is None:
        return None, None
    return _read_number(field_path, value)


def _unrepresentable(value: Any) -> str | None:
    """Alasan singkat bila `value` tidak bisa jadi JSON jujur (aturan `core.store`)."""
    try:
        json.dumps(value, allow_nan=False, default=lambda _obj: None)
    except ValueError:
        return "a non-finite number (NaN/Infinity)"
    except RecursionError:
        return "a structure nested too deeply"
    return None


def _labels(values: Sequence[Any], limit: int = MAX_ENTRIES) -> tuple[list[str], int]:
    """Daftar label bersih & TERBATAS dari teks RYO + jumlah aslinya.

    `compare_tokens` menerima 2–4 simbol menurut katalog; envelope dengan 5.000 token adalah
    envelope yang tidak sehat, dan kartu tidak boleh tumbuh mengikutinya.
    """
    return [_key(value) for value in list(values)[:limit]], len(values)


def _score_view(scores: Mapping[str, float], focus: str, limit: int = MAX_ENTRIES) -> dict[str, float]:
    """Potret skor untuk kartu: simbol yang dinilai + skor TERENDAH lainnya, kunci dibersihkan.

    Perbandingan "terakhir atau bukan" tetap dihitung atas SELURUH skor; yang dibatasi hanya
    apa yang ikut ke `value`. Skor terendah yang ditampilkan karena itulah yang menentukan.
    """
    others = [name for name, _ in sorted(scores.items(), key=lambda kv: (kv[1], str(kv[0]))) if name != focus]
    return {_key(name): scores[name] for name in [focus, *others][:limit]}


def _cap_warnings(item_id: str, warnings: Sequence[str], limit: int = MAX_WARNINGS) -> list[str]:
    """Warning per item dibatasi; sisanya diringkas satu kalimat (bukan dibuang diam-diam)."""
    if len(warnings) <= limit:
        return list(warnings)
    return [
        *warnings[:limit],
        f"{item_id}: and {len(warnings) - limit} more warnings of the same kind (truncated)",
    ]


def _section_ok(tool_evidence: ToolEvidence, section: str) -> bool:
    """Apakah seksi `availability` yang menaungi field ini menyatakan dirinya ada?

    Envelope RYO menyertakan seluruh kunci seksi (476/476 amatan), jadi penanda yang HILANG
    berarti envelope buatan/fixture — dibaca apa adanya. Penanda yang ADA dan bukan
    `available` mengikat: item tidak boleh hijau sambil menunjuk seksi yang sumbernya sendiri
    nyatakan tidak tersedia (itu kebalikan dari tesis produk).
    """
    marker = tool_evidence.availability.get(section)
    return marker is None or marker == "available"


def _parse_ts(raw: Any) -> datetime | None:
    """`as_of` RYO (`...Z`) → datetime UTC; bentuk tak terbaca → None (tidak ditebak)."""
    if isinstance(raw, datetime):
        return raw.astimezone(UTC) if raw.tzinfo is not None else None
    if type(raw) is not str or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _iso(value: datetime | None) -> str | None:
    """Bentuk teks presisi detik seperti envelope RYO (ADR-015 poin 5)."""
    if value is None:
        return None
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- bukti beku --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ToolEvidence:
    """Satu envelope RYO beku + id snapshot asalnya (petunjuk kartu & replay)."""

    tool: str
    envelope: Mapping[str, Any]
    snapshot_id: int | None = None

    @property
    def data(self) -> Any:
        return self.envelope.get("data") if isinstance(self.envelope, Mapping) else None

    @property
    def availability(self) -> Mapping[str, Any]:
        value = self.envelope.get("availability") if isinstance(self.envelope, Mapping) else None
        return value if isinstance(value, Mapping) else {}

    @property
    def status(self) -> Any:
        return self.envelope.get("status") if isinstance(self.envelope, Mapping) else None

    @property
    def warnings(self) -> Sequence[Any]:
        value = self.envelope.get("warnings") if isinstance(self.envelope, Mapping) else None
        return value if isinstance(value, list) else []

    @property
    def as_of(self) -> datetime | None:
        return _parse_ts(self.envelope.get("as_of") if isinstance(self.envelope, Mapping) else None)

    def source(self, field_path: str) -> ItemSource:
        """`ItemSource` yang menunjuk field PERSIS di envelope ini."""
        return ItemSource(tool=self.tool, field=field_path, snapshot_id=self.snapshot_id, as_of=self.as_of)


@dataclass(frozen=True, slots=True)
class Evidence:
    """Seluruh bukti beku untuk SATU keputusan + parameternya.

    `reference_as_of` = jam acuan item 1. Default (None) berarti "pakai `as_of` terbaru di
    dalam bukti ini sendiri" — deterministik saat replay (ADR-003). Pemanggil boleh
    menyuntikkan jam lain (mis. jam perekam) selama jam itu ikut dibekukan bersama keputusan.
    """

    symbol: str
    tools: Mapping[str, ToolEvidence]
    params: Params = DEFAULT_PARAMS
    reference_as_of: datetime | None = None

    @classmethod
    def from_envelopes(
        cls,
        symbol: str,
        envelopes: Mapping[str, Mapping[str, Any]],
        *,
        params: Params = DEFAULT_PARAMS,
        snapshot_ids: Mapping[str, int] | None = None,
        reference_as_of: datetime | None = None,
    ) -> Evidence:
        """Rakit dari `{tool: envelope}` (dipakai TASKS 2.2 dan tes fixture)."""
        ids = snapshot_ids or {}
        tools = {
            tool: ToolEvidence(tool=tool, envelope=envelope, snapshot_id=ids.get(tool))
            for tool, envelope in envelopes.items()
        }
        return cls(
            symbol=symbol,
            tools=MappingProxyType(tools),
            params=params,
            reference_as_of=reference_as_of,
        )

    def get(self, tool: str) -> ToolEvidence | None:
        return self.tools.get(tool)

    @property
    def snapshot_ids(self) -> tuple[int, ...]:
        """Id snapshot yang dipakai (terurut) — bahan `freeze()`/kolom `snapshot_ids_json`."""
        return tuple(sorted({e.snapshot_id for e in self.tools.values() if e.snapshot_id is not None}))


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    """Hasil satu item: `ItemResult` + warning envelope (ItemResult tidak punya field itu)."""

    result: ItemResult
    warnings: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return self.result.id

    @property
    def status(self) -> str:
        return self.result.status


def _item(
    item_id: str,
    status: str,
    *,
    reason: str,
    source: ItemSource | None = None,
    value: Any = None,
    warnings: Sequence[str] = (),
) -> ItemOutcome:
    """Bangun `ItemResult`; `value` yang tidak bisa dilaporkan jujur → `unspoken`.

    Lapis kedua untuk aturan NaN (TASKS 2.1 warisan review 1.4): kalau ada satu angka
    non-finit yang lolos dari :func:`_read_number`, item ini yang menjadi `unspoken` — bukan
    seluruh envelope yang gagal dibangun.
    """
    notes = list(warnings)
    if status != UNSPOKEN and value is not None:
        broken = _unrepresentable(value)
        if broken is not None:
            notes.append(f"{item_id}: value contains {broken}; reported as unspoken (ADR-004)")
            reason = (
                f"the evidence contains {broken} -> reported as unspoken instead of a "
                f"fabricated number; original finding: {reason}"
            )
            status, value = UNSPOKEN, None
    if status == UNSPOKEN:
        value = None
    return ItemOutcome(
        result=ItemResult(id=item_id, status=status, value=value, source=source, reason=reason),
        warnings=tuple(notes),
    )


def _unknown_value(item_id: str, vocab: Vocabulary, raw: Any, source: ItemSource | None) -> ItemOutcome:
    """Field yang tidak bisa dibaca → `unspoken`. Dua sebab yang berbeda, dibedakan:

    * **kosong/`null`** — tidak ada yang bisa disebut; bukan masalah kosakata, jadi tanpa
      warning (dan tetap tidak pernah dijadikan 0);
    * **string di luar tabel** — kosakata RYO yang belum pernah diverifikasi: `unspoken` +
      warning yang mengutip nilainya, TIDAK ditebak dan TIDAK dipetakan ke tetangga terdekat
      (docs/api-facts.md §A).
    """
    if raw is _MISSING or raw is None:
        return _item(
            item_id,
            UNSPOKEN,
            reason=f"{vocab.field} is null/absent -> nothing to speak (null never becomes 0)",
            source=source,
        )
    known = ", ".join(sorted(vocab.entries))
    message = (
        f"{vocab.field}={_quote(raw)} is outside the verified vocabulary [{known}] "
        "-> reported as unspoken; the enum is known to be incomplete (docs/api-facts.md §A)"
    )
    return _item(item_id, UNSPOKEN, reason=message, source=source, warnings=[f"{item_id}: {message}"])


def _table(evidence: Evidence, default: Vocabulary) -> Vocabulary:
    """Tabel kosakata yang BERLAKU untuk field ini (dari params, bukan konstanta modul).

    Dengan begitu tabel yang dipakai memutuskan selalu = tabel yang masuk `params_hash`;
    override untuk kalibrasi/tes tidak bisa lolos tanpa mengubah hash.
    """
    return evidence.params.vocabulary(default.field)


def _classify(meaning: str, direction: str | None, params: Params) -> tuple[str, str]:
    """Sikap bukti vs arah plan → (status item, kalimat akibat).

    `abstain` (``avoid``, ``caution — overbought``, ``cautious``) menentang entry ke arah
    MANA PUN, jadi ia merah bahkan ketika arah plan belum diketahui. Keadaan "netral" —
    tidak berlawanan tetapi juga bukan konfirmasi — diputuskan
    :attr:`preflight.params.Params.neutral_stance_status`, bukan oleh kode ini.
    """
    if meaning == ABSTAIN:
        return RED, "the source argues against opening a position in any direction"
    if direction is None:
        return UNSPOKEN, "the plan direction is unknown, so alignment cannot be spoken"
    if meaning == NEUTRAL:
        note = "neutral: not opposed to the plan, but not a confirmation either"
        return params.neutral_stance_status, note
    if (meaning == BULLISH and direction == LONG) or (meaning == BEARISH and direction == SHORT):
        return GREEN, f"aligned with the {direction} plan"
    return RED, f"opposed to the {direction} plan"


# --- item 1: kesegaran -------------------------------------------------------------------
def evaluate_freshness(evidence: Evidence) -> ItemOutcome:
    """spec §4 item 1 — `as_of` tiap envelope + `monitor….data.observation_as_of`.

    Hijau bila bukti TERTUA ≤ ``params.max_age_min`` dari jam acuan. Spec tidak memberi
    kondisi merah untuk item ini: bukti basi = pertanyaan yang tidak bisa dijawab dengan
    jujur, jadi `unspoken` (dan karena item wajib, ia tetap memveto).
    """
    item_id = ITEM_FRESHNESS
    params = evidence.params
    if not evidence.tools:
        return _item(
            item_id,
            UNSPOKEN,
            reason="no tool envelope was frozen for this decision -> there is no as_of to point at",
        )
    answering = [tool for tool in sorted(evidence.tools) if evidence.tools[tool].status in ("ok", "partial")]
    if not answering:
        # Umur envelope yang tidak menjawab tidak berarti apa-apa: yang basi bukan datanya,
        # datanya memang tidak ada. `unspoken` juga yang dituntut kontrak
        # `core.skill.SkillResult.from_items` ketika tidak ada tool yang menjawab.
        return _item(
            item_id,
            UNSPOKEN,
            reason=(
                "no envelope reports status ok/partial -> there is no answered evidence whose "
                "age could be spoken"
            ),
            source=evidence.tools[sorted(evidence.tools)[0]].source("as_of"),
        )
    stamps: dict[str, tuple[datetime, str]] = {}
    missing: list[str] = []
    for tool in answering:
        tool_evidence = evidence.tools[tool]
        candidates: list[tuple[datetime, str]] = []
        envelope_as_of = tool_evidence.as_of
        if envelope_as_of is not None:
            candidates.append((envelope_as_of, "as_of"))
        if tool == TOOL_MONITOR:
            observed = _parse_ts(_dig(tool_evidence.data, "observation_as_of"))
            if observed is not None:
                candidates.append((observed, "data.observation_as_of"))
        if not candidates:
            missing.append(tool)
            continue
        stamps[tool] = min(candidates, key=lambda pair: pair[0])

    if missing:
        offender = evidence.tools[missing[0]]
        return _item(
            item_id,
            UNSPOKEN,
            reason=(
                f"{', '.join(missing)}: as_of is missing or unreadable -> freshness cannot be "
                "spoken (spec §4 item 1 has no red state)"
            ),
            source=offender.source("as_of"),
        )

    reference = evidence.reference_as_of or max(stamp for stamp, _ in stamps.values())
    ages = {tool: (reference - stamp).total_seconds() / 60.0 for tool, (stamp, _) in stamps.items()}
    ahead = sorted(tool for tool, age in ages.items() if age < 0)
    if ahead:
        return _item(
            item_id,
            UNSPOKEN,
            reason=(
                f"{', '.join(ahead)}: as_of is newer than the reference clock "
                f"{_iso(reference)} -> the age of this evidence cannot be spoken"
            ),
            source=evidence.tools[ahead[0]].source("as_of"),
        )

    oldest = max(sorted(ages), key=lambda tool: ages[tool])
    stamp, field_path = stamps[oldest]
    value = {
        "reference_as_of": _iso(reference),
        "max_age_min": params.max_age_min,
        "oldest_tool": oldest,
        "oldest_as_of": _iso(stamp),
        "age_minutes": {tool: round(age, 3) for tool, age in sorted(ages.items())},
    }
    source = ItemSource(
        tool=oldest,
        field=field_path,
        snapshot_id=evidence.tools[oldest].snapshot_id,
        as_of=stamp,
    )
    if ages[oldest] <= params.max_age_min:
        return _item(
            item_id,
            GREEN,
            reason=(
                f"oldest evidence is {oldest} at {ages[oldest]:.1f} min (limit "
                f"{params.max_age_min} min) -> every envelope is within the limit OF EACH OTHER; "
                f"the clock is the newest as_of in this bundle ({_iso(reference)}), not the wall "
                "clock, so this says nothing about how old the bundle itself is"
            ),
            source=source,
            value=value,
        )
    return _item(
        item_id,
        UNSPOKEN,
        reason=(
            f"{oldest} evidence is {ages[oldest]:.1f} min old, past the "
            f"{params.max_age_min} min limit -> stale evidence is not spoken"
        ),
        source=source,
    )


# --- item 2: kelengkapan -----------------------------------------------------------------
def evaluate_completeness(evidence: Evidence) -> ItemOutcome:
    """spec §4 item 2 — `status`, `availability`, `warnings` tiap envelope.

    Merah bila ada tool yang hilang/`unavailable` atau seksi WAJIB (yang dipakai item 3–10)
    tidak `available`. Seksi opsional (`token_profile`, `derivatives`, `token_profiles`)
    hanya dicatat: 294/294 baris `deep_analysis` nyata `unavailable` di keduanya meski
    `include_perp=true` — kalau itu memerahkan item 2, tidak ada satu pun keputusan yang
    pernah bisa GO.
    """
    item_id = ITEM_COMPLETENESS
    if not evidence.tools:
        return _item(
            item_id,
            UNSPOKEN,
            reason="no tool answered at all -> completeness cannot be spoken, not even as red",
        )
    statuses: dict[str, str | None] = {}
    unknown: list[tuple[str, Any]] = []
    for tool in REQUIRED_TOOLS:
        tool_evidence = evidence.get(tool)
        if tool_evidence is None:
            continue
        raw_status = tool_evidence.status
        statuses[tool] = raw_status if type(raw_status) is str else None
        # `type(...) is str` DULU: `status` berupa dict/list dari fixture atau store akan
        # meledakkan `in <frozenset>` dengan `TypeError: unhashable type`, dan traceback dari
        # jalur keputusan dilarang spec §7.
        if type(raw_status) is not str or raw_status not in ENVELOPE_STATUSES:
            unknown.append((tool, raw_status))
    if unknown:
        tool, raw_status = unknown[0]
        message = (
            f"{tool}.status={_quote(raw_status)} is outside the RYO envelope vocabulary "
            f"[{', '.join(sorted(ENVELOPE_STATUSES))}] -> completeness reported as unspoken"
        )
        return _item(
            item_id,
            UNSPOKEN,
            reason=message,
            source=evidence.tools[tool].source("status"),
            warnings=[f"{item_id}: {message}"],
        )

    answered = [tool for tool, status in sorted(statuses.items()) if status in ("ok", "partial")]
    seen_statuses = (
        ", ".join(sorted({str(status) for status in statuses.values()})) or "no required tool present"
    )
    if not answered:
        # Tidak ada satu pun tool yang menjawab: itu BUKAN "merah" melainkan panggilan yang
        # tidak terjawab. Juga kontrak `core.skill.SkillResult.from_items`, yang menolak item
        # bervonis ketika seluruh envelope `unavailable` (bukti & kesimpulan tidak boleh
        # bertentangan) — item merah di sini akan meledakkan perakitan envelope 2.2.
        return _item(
            item_id,
            UNSPOKEN,
            reason=(
                f"no required tool answered (status seen: {seen_statuses}) -> completeness "
                "cannot be spoken, not even as red"
            ),
            source=evidence.tools[sorted(evidence.tools)[0]].source("status"),
        )

    missing_tools = [tool for tool in REQUIRED_TOOLS if tool not in evidence.tools]
    unavailable_tools = [tool for tool, status in sorted(statuses.items()) if status == "unavailable"]
    sections_unavailable: dict[str, list[str]] = {}
    optional_unavailable: dict[str, list[str]] = {}
    for tool, sections in sorted(REQUIRED_SECTIONS.items()):
        tool_evidence = evidence.get(tool)
        if tool_evidence is None:
            continue
        availability = tool_evidence.availability
        bad = [section for section in sections if availability.get(section) != "available"]
        if bad:
            sections_unavailable[tool] = bad
    for tool, sections in sorted(OPTIONAL_SECTIONS.items()):
        tool_evidence = evidence.get(tool)
        if tool_evidence is None:
            continue
        availability = tool_evidence.availability
        noted = [section for section in sections if availability.get(section) not in ("available", None)]
        if noted:
            optional_unavailable[tool] = noted

    value: dict[str, Any] = {
        "tool_status": {tool: statuses.get(tool) for tool in REQUIRED_TOOLS},
        "missing_tools": missing_tools,
        "unavailable_tools": unavailable_tools,
        "required_sections_unavailable": sections_unavailable,
        "optional_sections_unavailable": optional_unavailable,
        "envelope_warning_count": {
            tool: len(evidence.tools[tool].warnings) for tool in sorted(evidence.tools)
        },
    }
    oldest = min(
        (e.as_of for e in evidence.tools.values() if e.as_of is not None),
        default=None,
    )
    aggregate_source = ItemSource(
        tool=TOOL_AGGREGATE,
        field="status + availability + warnings of every frozen envelope",
        as_of=oldest,
    )
    if missing_tools or unavailable_tools or sections_unavailable:
        blame = missing_tools or unavailable_tools or sorted(sections_unavailable)
        offender = evidence.get(blame[0])
        parts = []
        if missing_tools:
            parts.append(f"no envelope for {', '.join(missing_tools)}")
        if unavailable_tools:
            parts.append(f"status=unavailable for {', '.join(unavailable_tools)}")
        for tool, bad in sorted(sections_unavailable.items()):
            parts.append(f"{tool}: sections {', '.join(bad)} not available")
        return _item(
            item_id,
            RED,
            reason="; ".join(parts) + " -> the roll call has a hole, the plan does not depart",
            source=offender.source("availability") if offender is not None else aggregate_source,
            value=value,
        )
    noted = "; ".join(
        f"{tool}: {', '.join(sections)} unavailable (optional)"
        for tool, sections in sorted(optional_unavailable.items())
    )
    reason = (
        f"all {len(REQUIRED_TOOLS)} tools answered and every section used by items 3-10 reports "
        "available -> no hole in the roll call at envelope level (what the sections CONTAIN is "
        "each item's own business)"
    )
    if noted:
        reason = f"{reason}; recorded only: {noted}"
    return _item(item_id, GREEN, reason=reason, source=aggregate_source, value=value)


# --- item 3: rezim pasar -----------------------------------------------------------------
def evaluate_regime(evidence: Evidence) -> ItemOutcome:
    """spec §4 item 3 — `market_overview.data.regime` (+ `sentiment.label`, `market.breadth`).

    Yang MENENTUKAN status hanya `regime` (seksi `market_regime`); label sentimen
    (`market_sentiment`) dan breadth (`market_breadth`) ikut ke kartu sebagai konteks. Label
    di luar tabel hanya menghasilkan warning, sedangkan `regime` di luar tabel membuat item
    ini `unspoken`.

    Penanda `availability` diperiksa seperti pada item 6/9/10: seksi yang menaungi `regime`
    tidak `available` → `unspoken`; seksi KONTEKS yang tidak `available` tidak dibaca sama
    sekali (nilainya `null`) — kartu tidak boleh menampilkan baris hijau yang menyebut angka
    dari seksi yang sumbernya sendiri nyatakan tidak tersedia.
    """
    item_id = ITEM_REGIME
    overview = evidence.get(TOOL_MARKET_OVERVIEW)
    if overview is None:
        return _item(
            item_id,
            UNSPOKEN,
            reason=f"no {TOOL_MARKET_OVERVIEW} envelope was frozen -> market regime cannot be spoken",
            source=ItemSource(tool=TOOL_MARKET_OVERVIEW, field="data.regime"),
        )
    source = overview.source("data.regime")
    if not _section_ok(overview, "market_regime"):
        return _item(
            item_id,
            UNSPOKEN,
            reason=(
                "market_overview.availability.market_regime="
                f"{_quote(overview.availability.get('market_regime'))} (not 'available') -> the "
                "regime cannot be spoken even though data.regime still carries a value"
            ),
            source=source,
        )
    raw = _dig(overview.data, "regime")
    regime_table = _table(evidence, MARKET_REGIME)
    entry = regime_table.lookup(raw)
    if entry is None:
        return _unknown_value(item_id, regime_table, raw, source)

    warnings: list[str] = []
    sentiment_ok = _section_ok(overview, "market_sentiment")
    breadth_ok = _section_ok(overview, "market_breadth")
    label_raw = _dig(overview.data, "sentiment", "label") if sentiment_ok else _MISSING
    label_table = _table(evidence, SENTIMENT_LABEL)
    label_entry = label_table.lookup(label_raw)
    if label_raw is not _MISSING and label_raw is not None and label_entry is None:
        warnings.append(
            f"{item_id}: {label_table.field}={_quote(label_raw)} is outside the verified "
            "vocabulary; recorded verbatim as context, it does not move this item"
        )
    fear_greed, bad = _context_number(
        "market_overview.data.sentiment.fear_greed_index",
        _dig(overview.data, "sentiment", "fear_greed_index") if sentiment_ok else _MISSING,
    )
    if bad is not None:
        return _item(item_id, UNSPOKEN, reason=bad, source=source)
    breadth, bad = _context_number(
        "market_overview.data.market.breadth",
        _dig(overview.data, "market", "breadth") if breadth_ok else _MISSING,
    )
    if bad is not None:
        return _item(item_id, UNSPOKEN, reason=bad, source=source)
    unavailable_sections = [
        section
        for section, marker_ok in (("market_sentiment", sentiment_ok), ("market_breadth", breadth_ok))
        if not marker_ok
    ]
    for section in unavailable_sections:
        warnings.append(
            f"{item_id}: market_overview.availability.{section}="
            f"{_quote(overview.availability.get(section))}; that context is left null instead of "
            "being read from a section its own source calls unavailable"
        )

    direction = plan_direction(evidence)
    status, note = _classify(entry.meaning, direction, evidence.params)
    value = {
        "regime": raw,
        "stance": entry.meaning,
        "plan_direction": direction,
        "sentiment_label": _text_value(label_raw),
        "sentiment_label_known": label_entry is not None,
        "sections_unavailable": unavailable_sections,
        "fear_greed_index": fear_greed,
        "breadth": breadth,
    }
    return _item(
        item_id,
        status,
        reason=f"market regime {_quote(raw)} ({entry.meaning}) -> {note}",
        source=source,
        value=value,
        warnings=warnings,
    )


# --- item 4: pergeseran sentimen 7h ------------------------------------------------------
def evaluate_sentiment_shift(evidence: Evidence) -> ItemOutcome:
    """spec §4 item 4 — `monitor….evidence.fear_greed{material_shift,change_direction}`.

    Hijau bila `material_shift=false` (tidak ada yang mendorong melawan plan) ATAU arah
    pergeserannya searah plan; merah bila pergeseran material DAN berlawanan.
    """
    item_id = ITEM_SENTIMENT_SHIFT
    monitor = evidence.get(TOOL_MONITOR)
    field_path = "data.evidence.fear_greed.material_shift"
    if monitor is None:
        return _item(
            item_id,
            UNSPOKEN,
            reason=f"no {TOOL_MONITOR} envelope was frozen -> the 7d sentiment shift cannot be spoken",
            source=ItemSource(tool=TOOL_MONITOR, field=field_path),
        )
    source = monitor.source(field_path)
    if not _section_ok(monitor, "evidence"):
        return _item(
            item_id,
            UNSPOKEN,
            reason=(
                "monitor_market_sentiment_shift.availability.evidence="
                f"{_quote(monitor.availability.get('evidence'))} (not 'available') -> the 7d shift "
                "cannot be spoken from a section its own source calls unavailable"
            ),
            source=source,
        )
    fear_greed = _dig(monitor.data, "evidence", "fear_greed")
    material = _dig(fear_greed, "material_shift")
    if not isinstance(material, bool):
        detail = (
            "is null/absent -> nothing to speak (null never becomes 0)"
            if material is _MISSING or material is None
            else f"={_quote(material)} is not a boolean -> the shift cannot be spoken"
        )
        return _item(
            item_id,
            UNSPOKEN,
            reason=f"monitor_market_sentiment_shift.{field_path} {detail}".replace(" =", "="),
            source=source,
        )

    warnings: list[str] = []
    regime_raw = _dig(monitor.data, "evidence", "sentiment_regime")
    if (
        regime_raw is not _MISSING
        and regime_raw is not None
        and _table(evidence, SENTIMENT_REGIME).lookup(regime_raw) is None
    ):
        warnings.append(
            f"{item_id}: {SENTIMENT_REGIME.field}={_quote(regime_raw)} is outside the verified "
            "vocabulary; recorded verbatim as context, it does not move this item"
        )
    points, bad = _context_number(
        "monitor_market_sentiment_shift.data.evidence.fear_greed.change_7d_points",
        _dig(fear_greed, "change_7d_points"),
    )
    if bad is not None:
        return _item(item_id, UNSPOKEN, reason=bad, source=source)
    index, bad = _context_number(
        "monitor_market_sentiment_shift.data.evidence.fear_greed.value", _dig(fear_greed, "value")
    )
    if bad is not None:
        return _item(item_id, UNSPOKEN, reason=bad, source=source)

    direction_raw = _dig(fear_greed, "change_direction")
    direction_table = _table(evidence, FEAR_GREED_CHANGE_DIRECTION)
    entry = direction_table.lookup(direction_raw)
    plan = plan_direction(evidence)
    value = {
        "material_shift": material,
        "change_direction": _text_value(direction_raw),
        "change_7d_points": points,
        "fear_greed_value": index,
        "sentiment_regime": _text_value(regime_raw),
        "plan_direction": plan,
    }
    if not material:
        return _item(
            item_id,
            GREEN,
            reason=(
                f"fear & greed moved {_quote(direction_raw)} but material_shift=false -> "
                "nothing pushes against the plan"
            ),
            source=source,
            value=value,
            warnings=warnings,
        )
    if entry is None:
        outcome = _unknown_value(item_id, direction_table, direction_raw, source)
        return ItemOutcome(outcome.result, tuple(warnings) + outcome.warnings)
    status, note = _classify(entry.meaning, plan, evidence.params)
    value["stance"] = entry.meaning
    return _item(
        item_id,
        status,
        reason=(f"material 7d shift, direction {_quote(direction_raw)} ({entry.meaning}) -> {note}"),
        source=source,
        value=value,
        warnings=warnings,
    )


# --- item 5: verdict cepat ---------------------------------------------------------------
def evaluate_verdict_quick(evidence: Evidence) -> ItemOutcome:
    """spec §4 item 5 — `analyze_token.data.verdict` lewat tabel `analyze_token` SAJA."""
    item_id = ITEM_VERDICT_QUICK
    analyze = evidence.get(TOOL_ANALYZE)
    if analyze is None:
        return _item(
            item_id,
            UNSPOKEN,
            reason=f"no {TOOL_ANALYZE} envelope was frozen -> the quick verdict cannot be spoken",
            source=ItemSource(tool=TOOL_ANALYZE, field="data.verdict"),
        )
    source = analyze.source("data.verdict")
    raw = _dig(analyze.data, "verdict")
    verdict_table = _table(evidence, ANALYZE_TOKEN_VERDICT)
    entry = verdict_table.lookup(raw)
    if entry is None:
        return _unknown_value(item_id, verdict_table, raw, source)

    warnings: list[str] = []
    # `verdict` tidak punya seksi `availability` sendiri (kunci yang ada: market_data,
    # technical_analysis, market_intelligence), jadi status item ini tetap ditentukan
    # verdict. Yang diikat penanda adalah KONTEKS-nya: trend & RSI tidak dibaca dari seksi
    # yang sumbernya sendiri nyatakan tidak tersedia.
    technical_ok = _section_ok(analyze, "technical_analysis")
    trend_raw = _dig(analyze.data, "technical_analysis", "trend") if technical_ok else _MISSING
    if (
        trend_raw is not _MISSING
        and trend_raw is not None
        and _table(evidence, TECHNICAL_TREND).lookup(trend_raw) is None
    ):
        warnings.append(
            f"{item_id}: {TECHNICAL_TREND.field}={_quote(trend_raw)} is outside the verified "
            "vocabulary; recorded verbatim as context, it does not move this item"
        )
    rsi, bad = _context_number(
        "analyze_token.data.technical_analysis.rsi_14",
        _dig(analyze.data, "technical_analysis", "rsi_14") if technical_ok else _MISSING,
    )
    if bad is not None:
        return _item(item_id, UNSPOKEN, reason=bad, source=source)
    if not technical_ok:
        warnings.append(
            f"{item_id}: analyze_token.availability.technical_analysis="
            f"{_quote(analyze.availability.get('technical_analysis'))}; trend/RSI left null "
            "instead of being read from a section its own source calls unavailable"
        )

    direction = plan_direction(evidence)
    status, note = _classify(entry.meaning, direction, evidence.params)
    value = {
        "verdict": raw,
        "stance": entry.meaning,
        # Kualifikasi ("overbought") ikut tercatat, tidak dibuang diam-diam (api-facts §A).
        "qualifier": entry.qualifier,
        "plan_direction": direction,
        "trend": _text_value(trend_raw),
        # RSI ekstrem BUKAN veto (spec §4 catatan): angkanya ditampilkan, tidak menghakimi.
        "rsi_14": rsi,
    }
    return _item(
        item_id,
        status,
        reason=f"quick verdict {_quote(raw)} ({entry.meaning}) -> {note}",
        source=source,
        value=value,
        warnings=warnings,
    )


# --- item 6: confluence ------------------------------------------------------------------
def evaluate_confluence(evidence: Evidence) -> ItemOutcome:
    """spec §4 item 6 — `deep_analysis.data.confluence{state,score,gates[],status}`.

    Hijau = `CONFIRMED` DAN `score ≥ params.min_confluence`. `MIXED` (mayoritas gate tidak
    lulus: 0.5 dari 2 gate di 125/125 baris nyata) adalah jawaban yang jelas — merah, bukan
    `unspoken`; yang `unspoken` adalah confluence yang tidak bisa dibaca.
    """
    item_id = ITEM_CONFLUENCE
    deep = evidence.get(TOOL_DEEP)
    if deep is None:
        return _item(
            item_id,
            UNSPOKEN,
            reason=f"no {TOOL_DEEP} envelope was frozen -> confluence cannot be spoken",
            source=ItemSource(tool=TOOL_DEEP, field="data.confluence.state"),
        )
    source = deep.source("data.confluence.state")
    confluence = _dig(deep.data, "confluence")
    if not isinstance(confluence, Mapping):
        return _item(
            item_id,
            UNSPOKEN,
            reason=f"deep_analysis.data.confluence={_quote(confluence)} -> confluence cannot be spoken",
            source=source,
        )
    section_status = confluence.get("status")
    if section_status != "available":
        return _item(
            item_id,
            UNSPOKEN,
            reason=(
                f"deep_analysis.data.confluence.status={_quote(section_status)} (not 'available') "
                "-> confluence cannot be spoken"
            ),
            source=source,
        )
    state_raw = confluence.get("state")
    state_table = _table(evidence, CONFLUENCE_STATE)
    entry = state_table.lookup(state_raw)
    if entry is None:
        return _unknown_value(item_id, state_table, state_raw, source)
    score, bad = _read_number("deep_analysis.data.confluence.score", confluence.get("score"))
    if bad is not None:
        return _item(item_id, UNSPOKEN, reason=bad, source=source)

    gates = confluence.get("gates")
    gates = gates if isinstance(gates, list) else []
    failed = [
        _text_value(gate.get("name")) or _quote(gate.get("name"))
        for gate in gates
        if isinstance(gate, Mapping) and gate.get("passed") is False
    ]
    passed = [
        _text_value(gate.get("name")) or _quote(gate.get("name"))
        for gate in gates
        if isinstance(gate, Mapping) and gate.get("passed") is True
    ]
    value = {
        "state": state_raw,
        "score": score,
        "min_confluence": float(evidence.params.min_confluence),
        "gates_total": len(gates),
        "gates_failed": failed,
        "gates_passed": passed,
    }
    if entry.meaning == CONFIRMED and score >= evidence.params.min_confluence:
        return _item(
            item_id,
            GREEN,
            reason=(
                f"confluence {state_raw} at {score:g} (limit {evidence.params.min_confluence:g}), "
                f"gates passed: {', '.join(passed) or 'none listed'} -> the evidence agrees with itself"
            ),
            source=source,
            value=value,
        )
    if entry.meaning == CONFIRMED:
        detail = f"confluence {state_raw} but score {score:g} < {evidence.params.min_confluence:g}"
    else:
        detail = f"confluence {state_raw} at {score:g}, gates failed: {', '.join(failed) or 'none listed'}"
    return _item(
        item_id,
        RED,
        reason=f"{detail} -> the evidence does not agree with itself",
        source=source,
        value=value,
    )


# --- item 7: verdict dalam ---------------------------------------------------------------
def evaluate_verdict_deep(evidence: Evidence) -> ItemOutcome:
    """spec §4 item 7 — `deep_analysis.data.verdict.call` (+ `what_changes_it` ke kartu)."""
    item_id = ITEM_VERDICT_DEEP
    deep = evidence.get(TOOL_DEEP)
    if deep is None:
        return _item(
            item_id,
            UNSPOKEN,
            reason=f"no {TOOL_DEEP} envelope was frozen -> the deep verdict cannot be spoken",
            source=ItemSource(tool=TOOL_DEEP, field="data.verdict.call"),
        )
    source = deep.source("data.verdict.call")
    raw = _dig(deep.data, "verdict", "call")
    call_table = _table(evidence, DEEP_ANALYSIS_VERDICT_CALL)
    entry = call_table.lookup(raw)
    if entry is None:
        return _unknown_value(item_id, call_table, raw, source)
    score, bad = _context_number(
        "deep_analysis.data.verdict.confluence_score", _dig(deep.data, "verdict", "confluence_score")
    )
    if bad is not None:
        return _item(item_id, UNSPOKEN, reason=bad, source=source)

    direction = plan_direction(evidence)
    status, note = _classify(entry.meaning, direction, evidence.params)
    value = {
        "call": raw,
        "stance": entry.meaning,
        "qualifier": entry.qualifier,
        "plan_direction": direction,
        # Teks RYO, disalin apa adanya (dipotong) untuk kartu — bukan instruksi bagi siapa pun.
        "what_changes_it": _text_value(_dig(deep.data, "verdict", "what_changes_it")),
        "key_driver": _text_value(_dig(deep.data, "verdict", "key_driver")),
        "confluence_score": score,
    }
    return _item(
        item_id,
        status,
        reason=f"deep verdict {_quote(raw)} ({entry.meaning}) -> {note}",
        source=source,
        value=value,
    )


# --- item 8: derivatif tidak sesak (OPSIONAL) --------------------------------------------
def evaluate_derivatives(evidence: Evidence) -> ItemOutcome:
    """spec §4 item 8 (opsional) — `monitor….evidence.funding.crowding_state` +
    `evidence.liquidation.pressure_state`, bukti MARKET-WIDE.

    `deep_analysis.derivatives` per-token dipakai bila available — tetapi ia `null` di
    294/294 baris nyata, sehingga BENTUKNYA belum pernah kami lihat. Kalau suatu saat ia
    tersedia, item ini menuliskan warning dan tetap memakai bukti market-wide alih-alih
    menebak skema (api-facts §A).

    Kosakata "sesak"/"ekstrem" BELUM ADA di :mod:`preflight.params`: satu-satunya nilai yang
    pernah terlihat untuk kedua field ini adalah ``normal``. Karena itu jalur merah hanya
    hidup lewat tabel yang di-override (tes) atau setelah api-verifier menambahkan nilai
    nyata — mengarang ``"crowded_long"`` di jalur produksi berarti memvonis dengan kosakata
    yang tidak pernah dikeluarkan RYO. Item ini OPSIONAL, jadi `unspoken` tidak memveto.
    """
    item_id = ITEM_DERIVATIVES
    monitor = evidence.get(TOOL_MONITOR)
    field_path = "data.evidence.funding.crowding_state"
    if monitor is None:
        return _item(
            item_id,
            UNSPOKEN,
            reason=f"no {TOOL_MONITOR} envelope was frozen -> derivatives crowding cannot be spoken",
            source=ItemSource(tool=TOOL_MONITOR, field=field_path),
        )
    source = monitor.source(field_path)
    if not _section_ok(monitor, "evidence"):
        return _item(
            item_id,
            UNSPOKEN,
            reason=(
                "monitor_market_sentiment_shift.availability.evidence="
                f"{_quote(monitor.availability.get('evidence'))} (not 'available') -> derivatives "
                "crowding cannot be spoken from a section its own source calls unavailable"
            ),
            source=source,
        )
    warnings: list[str] = []
    deep = evidence.get(TOOL_DEEP)
    per_token = "absent"
    if deep is not None:
        marker = deep.availability.get("derivatives")
        per_token = _text_value(marker) or "absent"
        if marker == "available" and _dig(deep.data, "derivatives") not in (None, _MISSING):
            warnings.append(
                f"{item_id}: deep_analysis.data.derivatives is available for the first time; its "
                "shape has never been observed (null in 294/294 rows) so market-wide evidence is "
                "used and the per-token payload is left to api-verifier"
            )

    crowding_raw = _dig(monitor.data, "evidence", "funding", "crowding_state")
    crowding_table = _table(evidence, FUNDING_CROWDING_STATE)
    crowding = crowding_table.lookup(crowding_raw)
    if crowding is None:
        return _unknown_value(item_id, crowding_table, crowding_raw, source)
    pressure_raw = _dig(monitor.data, "evidence", "liquidation", "pressure_state")
    pressure_table = _table(evidence, LIQUIDATION_PRESSURE_STATE)
    pressure = pressure_table.lookup(pressure_raw)
    if pressure is None:
        outcome = _unknown_value(
            item_id,
            pressure_table,
            pressure_raw,
            monitor.source("data.evidence.liquidation.pressure_state"),
        )
        return ItemOutcome(outcome.result, tuple(warnings) + outcome.warnings)

    funding_pct, bad = _context_number(
        "monitor_market_sentiment_shift.data.evidence.funding.current_7d_percentile_90d",
        _dig(monitor.data, "evidence", "funding", "current_7d_percentile_90d"),
    )
    if bad is not None:
        return _item(item_id, UNSPOKEN, reason=bad, source=source, warnings=warnings)
    liq_pct, bad = _context_number(
        "monitor_market_sentiment_shift.data.evidence.liquidation.percentile_90d",
        _dig(monitor.data, "evidence", "liquidation", "percentile_90d"),
    )
    if bad is not None:
        return _item(item_id, UNSPOKEN, reason=bad, source=source, warnings=warnings)

    value = {
        "funding_crowding_state": crowding_raw,
        "funding_crowding_meaning": crowding.meaning,
        "liquidation_pressure_state": pressure_raw,
        "liquidation_pressure_meaning": pressure.meaning,
        "funding_percentile_90d": funding_pct,
        "liquidation_percentile_90d": liq_pct,
        "per_token_derivatives": per_token,
        "plan_direction": plan_direction(evidence),
    }
    if crowding.meaning == CALM and pressure.meaning == CALM:
        return _item(
            item_id,
            GREEN,
            reason=(
                f"market-wide funding crowding {_quote(crowding_raw)} and liquidation pressure "
                f"{_quote(pressure_raw)} -> derivatives are not crowded against the plan"
            ),
            source=source,
            value=value,
            warnings=warnings,
        )
    hot = [
        f"{name}={_quote(raw)}"
        for name, raw, meaning in (
            ("funding.crowding_state", crowding_raw, crowding.meaning),
            ("liquidation.pressure_state", pressure_raw, pressure.meaning),
        )
        if meaning != CALM
    ]
    return _item(
        item_id,
        RED,
        reason=f"{'; '.join(hot)} -> derivatives are crowded/stressed, entering here is late",
        source=source,
        value=value,
        warnings=warnings,
    )


# --- item 9: plan ada & layak ------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PlanRead:
    """Hasil pembacaan `trade_plan`: outcome item 9 + arah acuan untuk item 3/4/5/7/8."""

    outcome: ItemOutcome
    direction: str | None


def _read_plan(evidence: Evidence) -> PlanRead:
    item_id = ITEM_PLAN
    deep = evidence.get(TOOL_DEEP)
    field_path = "data.trade_plan"
    if deep is None:
        return PlanRead(
            _item(
                item_id,
                UNSPOKEN,
                reason=f"no {TOOL_DEEP} envelope was frozen -> there is no trade plan to check",
                source=ItemSource(tool=TOOL_DEEP, field=field_path),
            ),
            None,
        )
    source = deep.source(field_path)
    marker = deep.availability.get("trade_plan")
    if marker != "available":
        return PlanRead(
            _item(
                item_id,
                UNSPOKEN,
                reason=(
                    f"deep_analysis.availability.trade_plan={_quote(marker)} (not 'available') "
                    "-> there is no plan to speak of"
                ),
                source=source,
            ),
            None,
        )
    plan = _dig(deep.data, "trade_plan")
    if not isinstance(plan, Mapping):
        return PlanRead(
            _item(
                item_id,
                UNSPOKEN,
                reason=f"deep_analysis.data.trade_plan={_quote(plan)} -> there is no plan to speak of",
                source=source,
            ),
            None,
        )
    numbers: dict[str, float] = {}
    for name in ("entry", "stop"):
        number, bad = _read_number(f"deep_analysis.data.trade_plan.{name}", plan.get(name))
        if bad is not None:
            return PlanRead(_item(item_id, UNSPOKEN, reason=bad, source=source), None)
        numbers[name] = number  # type: ignore[assignment]

    raw_targets = plan.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        return PlanRead(
            _item(
                item_id,
                UNSPOKEN,
                reason=(
                    f"deep_analysis.data.trade_plan.targets={_quote(raw_targets)} -> a plan "
                    "without targets has no exit to check"
                ),
                source=source,
            ),
            None,
        )
    targets: list[float] = []
    for index, raw in enumerate(raw_targets):
        number, bad = _read_number(f"deep_analysis.data.trade_plan.targets[{index}]", raw)
        if bad is not None:
            return PlanRead(_item(item_id, UNSPOKEN, reason=bad, source=source), None)
        targets.append(number)  # type: ignore[arg-type]

    raw_rr = plan.get("risk_reward")
    if not isinstance(raw_rr, list) or not raw_rr:
        return PlanRead(
            _item(
                item_id,
                UNSPOKEN,
                reason=(
                    f"deep_analysis.data.trade_plan.risk_reward={_quote(raw_rr)} -> the plan's "
                    "risk/reward cannot be read"
                ),
                source=source,
            ),
            None,
        )
    risk_reward: list[float] = []
    for index, raw in enumerate(raw_rr):
        number, bad = _read_number(f"deep_analysis.data.trade_plan.risk_reward[{index}]", raw)
        if bad is not None:
            return PlanRead(_item(item_id, UNSPOKEN, reason=bad, source=source), None)
        risk_reward.append(number)  # type: ignore[arg-type]

    entry_price, stop = numbers["entry"], numbers["stop"]
    if stop < entry_price:
        direction: str | None = LONG
    elif stop > entry_price:
        direction = SHORT
    else:
        return PlanRead(
            _item(
                item_id,
                UNSPOKEN,
                reason=(
                    f"trade_plan.stop ({stop:g}) equals entry ({entry_price:g}) -> the plan has "
                    "no direction and no risk to size"
                ),
                source=source,
            ),
            None,
        )

    warnings: list[str] = []
    preview_only = plan.get("preview_only")
    if preview_only is not True:
        warnings.append(
            f"{item_id}: deep_analysis.data.trade_plan.preview_only={_quote(preview_only)} "
            "(True in 294/294 observed rows); Tenko never executes anything either way"
        )
    context: dict[str, float | None] = {}
    for name in ("atr_14_usd", "atr_14_pct", "atr_multiplier"):
        number, bad = _context_number(f"deep_analysis.data.trade_plan.{name}", plan.get(name))
        if bad is not None:
            return PlanRead(_item(item_id, UNSPOKEN, reason=bad, source=source, warnings=warnings), None)
        context[name] = number

    value: dict[str, Any] = {
        "entry": entry_price,
        "stop": stop,
        "targets": targets,
        "risk_reward": risk_reward,
        "risk_reward_first": risk_reward[0],
        "min_rr": float(evidence.params.min_rr),
        "direction": direction,
        "preview_only": preview_only if isinstance(preview_only, bool) else None,
        "method": _text_value(plan.get("method")),
        **context,
    }
    if risk_reward[0] >= evidence.params.min_rr:
        outcome = _item(
            item_id,
            GREEN,
            reason=(
                f"{direction} plan entry {entry_price:g} / stop {stop:g} / targets "
                f"{', '.join(f'{t:g}' for t in targets)}, risk_reward[0]={risk_reward[0]:g} >= "
                f"{evidence.params.min_rr:g} -> the plan is present and worth its risk"
            ),
            source=source,
            value=value,
            warnings=warnings,
        )
    else:
        outcome = _item(
            item_id,
            RED,
            reason=(
                f"risk_reward[0]={risk_reward[0]:g} is below the {evidence.params.min_rr:g} "
                "limit -> the plan risks more than it offers"
            ),
            source=source,
            value=value,
            warnings=warnings,
        )
    return PlanRead(outcome, direction)


def evaluate_plan(evidence: Evidence) -> ItemOutcome:
    """spec §4 item 9 — `deep_analysis.data.trade_plan{entry,stop,targets[],risk_reward[]}`."""
    return _read_plan(evidence).outcome


def plan_direction(evidence: Evidence) -> str | None:
    """Arah acuan seluruh keputusan: `long`/`short` dari plan, atau None bila tak terbaca.

    None BUKAN "long secara default" (spec §4: "bila plan short muncul, arah dibalik"); item
    yang butuh arah menjadi `unspoken`, kecuali sikap `abstain` yang menentang entry ke arah
    mana pun.
    """
    return _read_plan(evidence).direction


# --- item 10: relatif pembanding ---------------------------------------------------------
def _factor_scores(factors: Any, key: str) -> Mapping[str, Any] | None:
    if not isinstance(factors, list):
        return None
    for factor in factors:
        if isinstance(factor, Mapping) and factor.get("key") == key:
            scores = factor.get("scores")
            return scores if isinstance(scores, Mapping) else None
    return None


def evaluate_relative(evidence: Evidence) -> ItemOutcome:
    """spec §4 item 10 — `compare_tokens.data.factors[]` + `coverage_by_token`.

    Hijau bila simbol BUKAN yang terakhir pada faktor `momentum`; merah bila terakhir pada
    `momentum` DAN `market_activity`. Keadaan ketiga (terakhir hanya pada momentum) tidak
    punya baris di spec §4 dan mengikuti `params.neutral_stance_status` (ADR-017).

    Verdict per token dibaca lewat tabel `compare_tokens` — BUKAN tabel `analyze_token`
    (dua namespace berbeda, api-facts §A); verdict di sini hanya konteks kartu.

    Item ini satu-satunya yang membaca teks RYO **sebagai kunci** (simbol token), jadi tiap
    simbol lewat :func:`_key` dan tiap koleksi dibatasi :data:`MAX_ENTRIES` /
    :data:`MAX_WARNINGS`. Perbandingannya sendiri tetap dihitung atas seluruh skor.
    """
    item_id = ITEM_RELATIVE
    compare = evidence.get(TOOL_COMPARE)
    field_path = "data.factors[momentum].scores"
    if compare is None:
        return _item(
            item_id,
            UNSPOKEN,
            reason=f"no {TOOL_COMPARE} envelope was frozen -> the peer comparison cannot be spoken",
            source=ItemSource(tool=TOOL_COMPARE, field=field_path),
        )
    source = compare.source(field_path)
    symbol = evidence.symbol
    tokens = _dig(compare.data, "tokens")
    token_symbols = [
        token.get("symbol")
        for token in (tokens if isinstance(tokens, list) else [])
        if isinstance(token, Mapping) and type(token.get("symbol")) is str
    ]
    matched = next((name for name in token_symbols if name.upper() == symbol.upper()), None)
    if matched is None:
        shown, total = _labels(token_symbols)
        listed = ", ".join(shown) + (f", ... ({total} tokens in total)" if total > len(shown) else "")
        return _item(
            item_id,
            UNSPOKEN,
            reason=(
                f"{_key(symbol)} is not among the compared tokens [{listed}] -> there is no "
                "relative standing to speak of"
            ),
            source=source,
        )
    label = _key(matched)
    coverage = _dig(compare.data, "coverage_by_token", matched, "market_data")
    if coverage != "available":
        return _item(
            item_id,
            UNSPOKEN,
            reason=(
                f"compare_tokens.data.coverage_by_token.{label}.market_data={_quote(coverage)} "
                "(not 'available') -> the comparison has no market data for this symbol"
            ),
            source=compare.source(f"data.coverage_by_token.{label}.market_data"),
        )

    warnings: list[str] = []
    peer_symbols = [name for name in token_symbols if name != matched]
    peers, peers_total = _labels(peer_symbols)
    if {_key(name) for name in peer_symbols} != {_key(name) for name in evidence.params.peers}:
        warnings.append(
            f"{item_id}: compared against {peers} ({peers_total} peers in the envelope), "
            f"params.peers says {list(evidence.params.peers)}; the frozen envelope is used as-is"
        )
    compare_table = _table(evidence, COMPARE_TOKENS_VERDICT)
    verdicts: dict[str, str | None] = {}
    stances: dict[str, str | None] = {}
    unknown_verdicts: list[str] = []
    for token in (tokens if isinstance(tokens, list) else [])[:MAX_ENTRIES]:
        if not isinstance(token, Mapping):
            continue
        name = token.get("symbol")
        if type(name) is not str:
            continue
        raw_verdict = token.get("verdict")
        entry = compare_table.lookup(raw_verdict)
        verdicts[_key(name)] = _text_value(raw_verdict, KEY_LIMIT)
        stances[_key(name)] = entry.meaning if entry is not None else None
        if raw_verdict is not None and entry is None:
            unknown_verdicts.append(
                f"{item_id}: {compare_table.field}={_quote(raw_verdict)} for {_key(name)} is "
                "outside that namespace's verified vocabulary (it is NOT the analyze_token "
                "vocabulary); recorded verbatim as context only"
            )
    warnings.extend(_cap_warnings(item_id, unknown_verdicts))

    factors = _dig(compare.data, "factors")
    momentum = _factor_scores(factors, "momentum")
    if not isinstance(momentum, Mapping) or matched not in momentum or len(momentum) < 2:
        return _item(
            item_id,
            UNSPOKEN,
            reason=(
                f"compare_tokens.data.factors[momentum].scores has no comparable score for "
                f"{label} ({_quote(momentum)}) -> relative standing cannot be spoken"
            ),
            source=source,
            warnings=warnings,
        )
    momentum_scores: dict[str, float] = {}
    for name, raw in sorted(momentum.items(), key=lambda pair: str(pair[0])):
        number, bad = _read_number(f"compare_tokens.data.factors[momentum].scores.{_key(name)}", raw)
        if bad is not None:
            return _item(item_id, UNSPOKEN, reason=bad, source=source, warnings=warnings)
        momentum_scores[str(name)] = number  # type: ignore[arg-type]
    last_momentum = momentum_scores[matched] == min(momentum_scores.values())

    activity = _factor_scores(factors, "market_activity")
    activity_scores: dict[str, float] = {}
    last_activity: bool | None = None
    if isinstance(activity, Mapping) and matched in activity and len(activity) >= 2:
        for name, raw in sorted(activity.items(), key=lambda pair: str(pair[0])):
            number, bad = _read_number(
                f"compare_tokens.data.factors[market_activity].scores.{_key(name)}", raw
            )
            if bad is not None:
                return _item(item_id, UNSPOKEN, reason=bad, source=source, warnings=warnings)
            activity_scores[str(name)] = number  # type: ignore[arg-type]
        last_activity = activity_scores[matched] == min(activity_scores.values())

    value = {
        "symbol": label,
        "peers": peers,
        "peers_total": peers_total,
        "momentum_scores": _score_view(momentum_scores, matched),
        "momentum_scores_total": len(momentum_scores),
        "market_activity_scores": _score_view(activity_scores, matched) if activity_scores else None,
        "market_activity_scores_total": len(activity_scores),
        "last_in_momentum": last_momentum,
        "last_in_market_activity": last_activity,
        "pick": _text_value(_dig(compare.data, "conclusion", "pick"), KEY_LIMIT),
        "verdicts": verdicts,
        "verdict_stances": stances,
    }
    if not last_momentum:
        return _item(
            item_id,
            GREEN,
            reason=(
                f"{label} is not last on momentum ({momentum_scores[matched]:g} vs "
                f"{min(momentum_scores.values()):g} lowest of {len(momentum_scores)}) -> nothing "
                "better is being ignored"
            ),
            source=source,
            value=value,
            warnings=warnings,
        )
    if last_activity is None:
        return _item(
            item_id,
            UNSPOKEN,
            reason=(
                f"{label} is last on momentum, but factor market_activity is missing or has no "
                "comparable score -> the red condition of spec §4 item 10 cannot be checked"
            ),
            source=source,
            warnings=warnings,
        )
    if last_activity:
        return _item(
            item_id,
            RED,
            reason=(
                f"{label} is last on BOTH momentum and market_activity -> the peers are simply "
                "better right now"
            ),
            source=source,
            value=value,
            warnings=warnings,
        )
    note = "last on momentum but not on market activity: not opposed, not a confirmation either"
    return _item(
        item_id,
        evidence.params.neutral_stance_status,
        reason=f"{label} is last on momentum only -> {note}",
        source=source,
        value=value,
        warnings=warnings,
    )


# --- daftar item -------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ItemDef:
    """Deklarasi satu baris panggilan (spec §4) + fungsi penilainya."""

    id: str
    order: int
    tool: str
    field_path: str
    required: bool
    title: str
    evaluate: Callable[[Evidence], ItemOutcome]


def _definition(
    item_id: str, tool: str, field_path: str, title: str, evaluate: Callable[[Evidence], ItemOutcome]
) -> ItemDef:
    # `required` diturunkan dari DEFAULT_PARAMS.optional_items — satu sumber kebenaran; yang
    # dipakai `decide()` tetap params yang dioper pemanggil.
    return ItemDef(
        id=item_id,
        order=ITEM_ORDER.index(item_id) + 1,
        tool=tool,
        field_path=field_path,
        required=DEFAULT_PARAMS.is_required(item_id),
        title=title,
        evaluate=evaluate,
    )


ITEMS: tuple[ItemDef, ...] = (
    _definition(
        ITEM_FRESHNESS,
        TOOL_AGGREGATE,
        "as_of (every envelope) + monitor_market_sentiment_shift.data.observation_as_of",
        "Freshness",
        evaluate_freshness,
    ),
    _definition(
        ITEM_COMPLETENESS,
        TOOL_AGGREGATE,
        "status + availability + warnings (every envelope)",
        "Completeness",
        evaluate_completeness,
    ),
    _definition(
        ITEM_REGIME,
        TOOL_MARKET_OVERVIEW,
        "data.regime (+ data.sentiment.label, data.market.breadth)",
        "Market regime",
        evaluate_regime,
    ),
    _definition(
        ITEM_SENTIMENT_SHIFT,
        TOOL_MONITOR,
        "data.evidence.fear_greed.{material_shift,change_direction} + data.evidence.sentiment_regime",
        "7d sentiment shift",
        evaluate_sentiment_shift,
    ),
    _definition(
        ITEM_VERDICT_QUICK,
        TOOL_ANALYZE,
        "data.verdict",
        "Quick verdict",
        evaluate_verdict_quick,
    ),
    _definition(
        ITEM_CONFLUENCE,
        TOOL_DEEP,
        "data.confluence.{state,score,gates[],status}",
        "Confluence",
        evaluate_confluence,
    ),
    _definition(
        ITEM_VERDICT_DEEP,
        TOOL_DEEP,
        "data.verdict.call (+ data.verdict.what_changes_it)",
        "Deep verdict",
        evaluate_verdict_deep,
    ),
    _definition(
        ITEM_DERIVATIVES,
        TOOL_MONITOR,
        "data.evidence.funding.crowding_state + data.evidence.liquidation.pressure_state",
        "Derivatives not crowded",
        evaluate_derivatives,
    ),
    _definition(
        ITEM_PLAN,
        TOOL_DEEP,
        "data.trade_plan.{entry,stop,targets[],risk_reward[],preview_only}",
        "Plan present and worth its risk",
        evaluate_plan,
    ),
    _definition(
        ITEM_RELATIVE,
        TOOL_COMPARE,
        "data.factors[].scores + data.coverage_by_token",
        "Relative to peers",
        evaluate_relative,
    ),
)

ITEMS_BY_ID: Mapping[str, ItemDef] = MappingProxyType({item.id: item for item in ITEMS})


def evaluate_all(evidence: Evidence) -> tuple[ItemOutcome, ...]:
    """Nilai kesepuluh item atas bukti beku — urut daya veto spec §4, fungsi murni."""
    return tuple(item.evaluate(evidence) for item in ITEMS)


def results_of(outcomes: Iterable[ItemOutcome]) -> tuple[ItemResult, ...]:
    return tuple(outcome.result for outcome in outcomes)


def warnings_of(outcomes: Iterable[ItemOutcome]) -> tuple[str, ...]:
    """Warning item (terurut, tanpa duplikat) untuk `SkillResult.warnings`."""
    seen: list[str] = []
    for outcome in outcomes:
        for warning in outcome.warnings:
            if warning not in seen:
                seen.append(warning)
    return tuple(seen)


# --- keputusan ---------------------------------------------------------------------------
class DecisionFloorError(Exception):
    """Keputusan melanggar lantai konsistensi (bukan bukti jelek — ini bug pemanggil)."""


@dataclass(frozen=True, slots=True)
class Decision:
    """Keputusan deterministik atas item + params. Tidak ada LLM di jalur ini (ADR-003)."""

    decision: Literal["GO", "NO_GO"]
    blocking: tuple[str, ...]
    item_status: Mapping[str, str]
    #: Hash params yang BENAR-BENAR dipakai menilai item ini (bukan params apa pun yang
    #: kebetulan dipegang pemeriksa). Tanpa ini, item yang dinilai dengan ambang ketat bisa
    #: lolos lantai konsistensi yang diperiksa dengan ambang longgar — dan kolom
    #: `decisions.params_hash` akan mencatat ambang yang tidak pernah menilai apa pun.
    #: Default kosong hanya untuk objek yang dirakit tangan; :func:`check_decision_floor`
    #: menolaknya.
    params_hash: str = ""

    def as_material(self) -> dict[str, Any]:
        """Bentuk yang diminta :func:`core.replay.decision_hash` (tiga kunci, tanpa narasi)."""
        return {
            "decision": self.decision,
            "blocking": list(self.blocking),
            "item_status": dict(self.item_status),
        }


def _ordered(results: Sequence[ItemResult]) -> list[ItemResult]:
    def key(result: ItemResult) -> tuple[int, str]:
        try:
            return (ITEM_ORDER.index(result.id), result.id)
        except ValueError:
            return (len(ITEM_ORDER), result.id)

    return sorted(results, key=key)


def decide(results: Sequence[ItemResult], params: Params = DEFAULT_PARAMS) -> Decision:
    """GO hanya bila TIDAK ada item merah dan tidak ada item WAJIB yang `unspoken`.

    Fungsi murni atas (items, params): dua panggilan atas snapshot yang sama menghasilkan
    keputusan — dan karenanya `decision_hash` — yang identik. Item opsional yang `unspoken`
    (item 8) dicatat di `item_status` tetapi TIDAK masuk `blocking`.
    """
    ordered = _ordered(results)
    seen: set[str] = set()
    for result in ordered:
        if result.id in seen:
            raise DecisionFloorError(f"item {result.id!r} muncul dua kali; id item harus unik")
        seen.add(result.id)
    blocking = tuple(
        result.id
        for result in ordered
        if result.status == RED or (result.status == UNSPOKEN and params.is_required(result.id))
    )
    return Decision(
        decision="NO_GO" if blocking else "GO",
        blocking=blocking,
        item_status=MappingProxyType({result.id: result.status for result in ordered}),
        params_hash=params.params_hash(),
    )


def check_decision_floor(
    decision: Decision,
    results: Sequence[ItemResult],
    params: Params = DEFAULT_PARAMS,
    snapshot_ids: Sequence[int] = (),
) -> None:
    """Lantai konsistensi yang `core/replay.py` sengaja TIDAK jaga (warisan review 1.4).

    Hash yang sah tidak berarti buktinya ada: `decision_hash` atas bundle kosong tetap
    terhitung. Empat hal yang diperiksa di sini, semuanya wajib dipanggil TASKS 2.2 sebelum
    menulis baris `decisions`:

    0. `params` yang memeriksa = `params` yang menilai (`Decision.params_hash`);
    1. `blocking ⊆ item_status` dan tiap id di `blocking` memang merah/unspoken-wajib;
    2. `blocking` = TEPAT himpunan penyebab veto (tidak ada penyebab yang disembunyikan);
    3. `GO` ⇒ tidak ada item merah dan tidak ada item wajib yang `unspoken`;
    4. `GO` ⇒ ada bukti: kesepuluh item dinilai, minimal satu item tersebut, dan minimal satu
       snapshot dibekukan. (`NO_GO` tanpa bukti sama sekali justru sah — itu penolakan jujur
       saat semua tool mati.)
    """
    if decision.decision not in DECISIONS:
        raise DecisionFloorError(
            f"decision harus salah satu {sorted(DECISIONS)}, bukan {decision.decision!r}"
        )
    expected_params = params.params_hash()
    if decision.params_hash != expected_params:
        raise DecisionFloorError(
            f"params_hash keputusan {decision.params_hash!r} bukan {expected_params!r}: ambang "
            "yang MENILAI item dan ambang yang memeriksanya harus sama, kalau tidak lantai ini "
            "memeriksa aturan yang tidak pernah dipakai"
        )
    expected = decide(results, params)
    statuses = dict(decision.item_status)
    result_statuses = {result.id: result.status for result in results}
    if statuses != result_statuses:
        raise DecisionFloorError(
            f"item_status {statuses} tidak cocok dengan item yang dinilai {result_statuses}"
        )
    unknown = [item_id for item_id in decision.blocking if item_id not in statuses]
    if unknown:
        raise DecisionFloorError(f"blocking menyebut item yang tidak dinilai: {unknown}")
    if sorted(decision.blocking) != sorted(expected.blocking):
        raise DecisionFloorError(
            f"blocking {sorted(decision.blocking)} bukan himpunan penyebab veto "
            f"{sorted(expected.blocking)} atas params ini"
        )
    if decision.decision != expected.decision:
        raise DecisionFloorError(
            f"decision {decision.decision!r} tidak konsisten dengan item (seharusnya {expected.decision!r})"
        )
    if decision.decision == "GO":
        missing = [item_id for item_id in ITEM_ORDER if item_id not in statuses]
        if missing:
            raise DecisionFloorError(f"GO tanpa menilai item {missing}: panggilan tidak lengkap")
        if all(status == UNSPOKEN for status in statuses.values()):
            raise DecisionFloorError("GO tanpa satu pun item yang tersebut: keputusan tanpa bukti")
        if not list(snapshot_ids):
            raise DecisionFloorError("GO tanpa snapshot beku: keputusan tanpa bukti yang bisa di-replay")
