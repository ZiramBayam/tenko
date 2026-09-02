"""Kontrak skill Tenko — `SkillResult`/`ItemResult` berbentuk envelope RYO (ADR-013/015).

Bentuk (docs/spec.md §3, docs/api-facts.md §A, diverifikasi terhadap `docs/samples/*.json`):
sepuluh field top-level yang SAMA dengan envelope tool RYO — ``schema_version, tool, status,
data_mode, as_of, request, data, summary, availability, warnings`` — ditambah TEPAT SATU
field, ``simulated``, yang diwajibkan docs/rules-compliance.md dan tidak ada di RYO.
``schema_version`` bernilai ``"1.0"`` mengikuti sampel nyata, bukan ``"1"`` yang tertulis di
spec §3 (ADR-015; bila manusia menolak ADR itu, yang berubah hanya :data:`SCHEMA_VERSION`).

Modul ini adalah KONTRAK, bukan logika keputusan: tidak ada daftar item, ambang, mapping
kosakata verdict, maupun panggilan tool di sini (itu TASKS 2.1/2.2).

Yang ditegakkan validator (tiap klaim punya tes di ``tests/test_skill.py``):

1. **Item `unspoken` tidak boleh membawa nilai.** ``ItemResult.value`` wajib ``None`` saat
   ``status == "unspoken"`` — termasuk ``0`` dan ``0.0``, yang justru bentuk kebohongan yang
   dilarang guide RYO ("null/unavailable tidak pernah jadi 0").
2. **`availability` konsisten dua arah.** Untuk tiap item: ``availability[item.id] ==
   "unspoken"`` JIKA DAN HANYA JIKA item itu ``unspoken``; setiap item wajib punya entri.
   Kunci lain (nama seksi tool, seperti ``derivatives`` di `deep_analysis`) tetap boleh ada.
3. **`status == "unavailable"` ⇒ `data is None`** (dan sebaliknya: ``data`` berisi sesuatu
   dengan status ``unavailable`` ditolak).

Larangan "angka dari item unspoken bocor ke `data`" (spec §3 kalimat terakhir) dijaga secara
STRUKTURAL, bukan dengan pemindai numerik generik: satu-satunya tempat angka per item berada
adalah ``data["items"][*]["value"]``, dan invarian 1 mengosongkannya. Angka turunan
(mis. ``plan``) baru muncul di TASKS 2.2 dan dibangun dari item yang tersebut saja.

**Batas yang diakui, harus dibaca sebelum menaruh angka agregat di sini** (temuan review
TASKS 1.5, dipatok TASKS 2.2a; scope, bukan cacat yang belum diperbaiki): karena penjagaan
di atas bersifat struktural, ia berhenti tepat di kunci ``items``. ``data`` yang TIDAK punya
kunci ``items`` melewati SELURUH invarian item — ``SkillResult(tool="t", status="ok",
data={"foo": 1}, availability={})`` diterima apa adanya, dengan nol item dan ``availability``
kosong. Artinya: **angka agregat di luar ``items`` tidak punya penjaga ``n`` di lapisan ini**,
dan aturan "tidak ada angka agregat tanpa ``n``" (CLAUDE.md, spec §3) bukan sesuatu yang
model ini tegakkan untuk Anda. TASKS 2.2-lah yang wajib menyediakan ``n``-nya sendiri —
menghitungnya dari item yang benar-benar tersebut, dan menolak merakit ``plan``/``decision``
saat ``n`` nol — bukan mengandalkan validator di sini. Pengakuan ini dipatok tes yang
MENGHARAPKAN bentuk tanpa ``items`` itu diterima
(``test_acknowledged_gap_ok_without_items_key_passes_every_item_invariant``), supaya celah
itu tidak pernah ditutup — atau diperlebar — tanpa pembahasan.

Konvensi ``warnings`` untuk penulis TASKS 2.2 (supaya tidak perlu menebak): preseden repo
adalah **"batasi + sebutkan jumlah aslinya"**, bukan membuang diam-diam. Contohnya
`core/skill_cli.py` (``MAX_ARGS=8``: argumen yang dicetak dipotong, lalu ``args_total``
menyebut berapa banyak yang sebenarnya ada; dipatok
``test_many_arguments_do_not_become_a_huge_envelope``). Di model ini,
:meth:`SkillResult._bounded_warnings` memotong PANJANG tiap warning tetapi tidak pernah
mengurangi JUMLAHNYA — membuang satu warning sama dengan menyembunyikan satu kegagalan.
Skill yang perlu membatasi cacah warning harus menambahkan hitungan aslinya (mis.
``warnings_total``) di tempat yang terbaca, mengikuti pola yang sama.

``as_of`` = **waktu data, bukan waktu pemanggilan**. :meth:`SkillResult.from_items`
mengambil as_of TERTUA dari envelope bukti (paling konservatif untuk item kesegaran); tanpa
bukti sama sekali nilainya ``None`` — bukan ``now()`` — sehingga tidak ada kesegaran yang
dikarang dan output CLI tetap deterministik antar run.
"""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

log = logging.getLogger("core.skill")

# ADR-015: nilai dari docs/samples/*.json, bukan dari teks spec §3.
SCHEMA_VERSION = "1.0"

ItemStatus = Literal["green", "red", "unspoken"]
SkillStatus = Literal["ok", "partial", "unavailable"]
DataMode = Literal["live", "mixed", "simulated", "unknown"]

# --- batas teks yang IKUT KE ENVELOPE (temuan juri fase 1 no. 6) -------------------------
# Argumen CLI 100 kB pernah masuk utuh ke `tool` dan `summary.headline`, lalu ke stdout, ke
# log, dan ke kartu panggilan. Angka di bawah sejalan dengan `preflight/items.py`
# (KEY_LIMIT=32, TEXT_LIMIT=240) supaya repo punya satu gaya sanitasi; `tool` diberi ruang
# lebih dari KEY_LIMIT karena nama skill majemuk (mis. "preflight_compare") masih wajar.
TOOL_LIMIT = 64
HEADLINE_LIMIT = 240
# `KEY_LIMIT` juga dipakai untuk `ItemResult.id`: id dan kunci `availability` HARUS dipotong
# dengan batas yang sama, kalau tidak invarian dua arah (`availability[item.id]`) pecah untuk
# id panjang. 64 (bukan 32 seperti `preflight.items.KEY_LIMIT`) karena id terpanjang yang
# nyata adalah "04_sentiment_shift" dan skill 2.2 boleh memakai kunci majemuk.
KEY_LIMIT = 64
TEXT_LIMIT = 240
WARNING_LIMIT = 400
# Kedalaman `request`/`summary` yang ditelusuri. Di luar itu nilainya dibiarkan apa adanya —
# BATAS YANG DIAKUI, bukan klaim kekebalan; `request` nyata (argumen CLI ternormalisasi) dan
# `summary` ({headline, key_points}) tidak pernah sedalam itu.
MAX_CLEAN_DEPTH = 4
TRUNCATED_MARK = "...(truncated)"

UNSPOKEN = "unspoken"
AVAILABLE = "available"
OK = "ok"
PARTIAL = "partial"
UNAVAILABLE = "unavailable"

# kosakata status envelope RYO (docs/api-facts.md §A)
ENVELOPE_STATUSES: frozenset[str] = frozenset({OK, PARTIAL, UNAVAILABLE})
ANSWERED_STATUSES: frozenset[str] = frozenset({OK, PARTIAL})
DATA_MODES: frozenset[str] = frozenset({"live", "mixed", "simulated", "unknown"})


class SkillError(Exception):
    """Kegagalan kontrak skill yang bukan kesalahan validasi pydantic."""


def clean_text(text: str, limit: int) -> str:
    """Escape setiap karakter tak tercetak lalu potong ke `limit`.

    Yang di-escape (menjadi ``\\uXXXX`` yang TERLIHAT, bukan dibuang diam-diam): kontrol
    C0/C1 termasuk CR/LF/TAB dan escape ANSI, kontrol arah teks (U+202E dkk.), pemisah baris
    Unicode, dan **lone surrogate**. Alasannya sama untuk ketiganya:

    * ``model_dump(mode="json")`` melempar ``UnicodeEncodeError`` untuk lone surrogate —
      satu nama skill aneh menggagalkan perakitan SELURUH envelope;
    * `tool`/`headline` ikut ke stdout, ke `logs/*.log` (`scripts/daemons.sh`), dan ke kartu
      panggilan: teks dari luar tidak boleh menyisipkan baris palsu atau membalik arah teks;
    * panjangnya dibatasi supaya argumen 100 kB tidak menjadi envelope 100 kB.

    **`limit` bukan batas keras.** Saat teks dipotong, :data:`TRUNCATED_MARK` DITAMBAHKAN di
    belakang, jadi panjang maksimum sebenarnya ``limit + len(TRUNCATED_MARK)`` (mis. `tool`
    78, bukan 64). Ini disengaja: pemotongan harus terlihat oleh pembaca, dan penanda yang
    ikut terpotong tidak akan terbaca. Batasnya tetap konstan dan itulah yang diuji.

    **Satu-satunya sanitizer teks di repo (TASKS 1.5b).** Dua pemakai: `core/skill.py` sendiri
    (`tool`, `headline`, `request`, `warnings`, `ItemResult.id`) dan `preflight/items.py`
    (`_quote`/`_text_value`/`_key`). Ia tinggal di `core/` karena arah impor hanya boleh
    `preflight` → `core` (ADR-002); **batas panjangnya tidak** — angka `TEXT_LIMIT`/
    `QUOTE_LIMIT`/`KEY_LIMIT` milik kartu panggilan tetap di `preflight/params`-nya sendiri.
    Menduplikasi fungsi ini lagi berarti satu sisi diperbaiki dan sisi lain tidak, tepat di
    batas kepercayaan yang dipakai security review.
    """
    parts: list[str] = []
    length = 0
    for char in text:
        piece = char if char.isprintable() else f"\\u{ord(char):04x}"
        if length + len(piece) > limit:
            return "".join(parts) + TRUNCATED_MARK
        parts.append(piece)
        length += len(piece)
    return "".join(parts)


def _clean_tree(value: Any, limit: int, depth: int = 0) -> Any:
    """Bersihkan teks di dalam struktur JSON kecil (`request`, `summary`).

    Hanya `str` yang disentuh — kunci dict memakai :data:`KEY_LIMIT`, nilai teks memakai
    `limit`. Angka/boolean/None dibiarkan: mengubahnya menjadi teks adalah kebohongan
    tersendiri (aturan yang sama dengan `_text_value` di `preflight/items.py`).

    Jumlah entri TIDAK dibatasi dengan sengaja: membuang satu argumen atau satu warning
    adalah bentuk penghilangan bukti yang justru dilarang spec §3. Yang dibatasi adalah
    panjang tiap teks, dan pemanggil (mis. `core/skill_cli.py`) yang menyebutkan berapa
    banyak yang tidak ia cetak.
    """
    if depth > MAX_CLEAN_DEPTH:
        return value
    if isinstance(value, str):
        return clean_text(value, limit)
    if isinstance(value, dict):
        return {
            (clean_text(key, KEY_LIMIT) if isinstance(key, str) else key): _clean_tree(item, limit, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_clean_tree(item, limit, depth + 1) for item in value]
    return value


def _to_utc(value: datetime) -> datetime:
    """Semua waktu disimpan UTC; datetime tanpa timezone ditolak, bukan ditebak."""
    if value.tzinfo is None:
        raise ValueError("datetime harus tz-aware (UTC); waktu tanpa zona tidak bisa dibandingkan")
    return value.astimezone(UTC)


def _iso_z(value: datetime | None) -> str | None:
    """Bentuk teks yang sama dengan envelope RYO: `2026-09-01T03:47:14Z`.

    Dipotong ke DETIK dengan sengaja: RYO memakai presisi detik di seluruh amatan
    (api-verifier, 476/476), sedangkan `snapshots.ts_utc` menyimpan mikrodetik — tanpa
    pemotongan, `ItemSource.as_of` akan mengeluarkan bentuk yang tidak pernah dipakai RYO
    (ADR-015 poin 5).
    """
    if value is None:
        return None
    text = _to_utc(value).replace(microsecond=0).isoformat()
    return text.replace("+00:00", "Z")


def _unrepresentable(value: Any) -> str | None:
    """Alasan singkat bila `value` tidak bisa jadi JSON JUJUR; None bila aman.

    Memakai aturan yang sama persis dengan :func:`core.store.canonical_json`
    (``allow_nan=False``). Ini menutup kebohongan diam-diam: pydantic `mode="json"`
    mengubah ``NaN``/``Infinity`` menjadi ``null``, sehingga item HIJAU bisa tampil sebagai
    "tersebut" dengan nilai ``null`` — persis bentuk yang dilarang ADR-004/spec §3. Objek
    non-JSON diabaikan di sini (bukan urusan cek ini) supaya penelusuran tidak berhenti
    sebelum menemukan angka non-finit di belakangnya.
    """
    try:
        json.dumps(value, allow_nan=False, default=lambda _obj: None)
    except ValueError:
        return "non-finite number (NaN/Infinity) or circular reference"
    except RecursionError:
        return "nested too deeply"
    return None


class ItemSource(BaseModel):
    """Dari mana satu item dibaca — tool, field persis, snapshot beku, dan waktu datanya."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str
    field: str
    snapshot_id: int | None = None
    as_of: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _to_utc(value)

    @field_serializer("as_of")
    def _dump_as_of(self, value: datetime | None) -> str | None:
        return _iso_z(value)


class ItemResult(BaseModel):
    """Satu baris panggilan: tunjuk (source) → sebut (value) → alasan (reason)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    status: ItemStatus
    value: Any = None
    source: ItemSource | None = None
    reason: str = ""

    @field_validator("id")
    @classmethod
    def _bounded_id(cls, value: str) -> str:
        """Dipotong dengan batas yang SAMA seperti kunci `availability` (lihat KEY_LIMIT)."""
        return clean_text(value, KEY_LIMIT)

    @field_validator("reason")
    @classmethod
    def _bounded_reason(cls, value: str) -> str:
        """`reason` ikut ke kartu panggilan; teks RYO tidak boleh menyisipkan baris palsu."""
        return clean_text(value, TEXT_LIMIT)

    @model_validator(mode="after")
    def _unspoken_carries_no_value(self) -> ItemResult:
        if self.status == UNSPOKEN and self.value is not None:
            raise ValueError(
                f"item {self.id!r} unspoken tetapi membawa value={self.value!r}: "
                "yang tidak terjawab tidak boleh punya angka (spec §3, guide RYO)"
            )
        reason = _unrepresentable(self.value)
        if reason is not None:
            # DITOLAK, bukan diam-diam diubah jadi `unspoken`: yang tahu bahwa sumbernya
            # tidak menjawab adalah pembaca field (TASKS 2.1), dan dialah yang harus menulis
            # `reason`-nya. Menebak status di sini justru mengarang alasan.
            raise ValueError(
                f"item {self.id!r} membawa value yang tidak bisa dilaporkan jujur ({reason}); "
                "sumber seperti itu harus dijadikan item unspoken dengan reason yang menyebutkannya"
            )
        return self


def derive_status(statuses: Iterable[str | None]) -> SkillStatus:
    """Status skill dari status envelope tool sumber (aturan docs/spec.md §3).

    `ok` hanya bila SEMUA tool `ok`; ada yang `partial` (atau campur ok+unavailable) →
    `partial`; tidak ada satu pun yang menjawab (`ok`/`partial`) → `unavailable`.
    """
    values = list(statuses)
    for value in values:
        if value not in ENVELOPE_STATUSES:
            raise SkillError(f"unknown envelope status: {value!r} (api-facts §A)")
    if not any(value in ANSWERED_STATUSES for value in values):
        return UNAVAILABLE
    if all(value == OK for value in values):
        return OK
    return PARTIAL


def derive_data_mode(modes: Iterable[str | None]) -> DataMode:
    """`data_mode` gabungan: kosong/tak dikenal → `unknown`, campuran → `mixed`."""
    seen = {mode if mode in DATA_MODES else "unknown" for mode in modes}
    if not seen:
        return "unknown"
    if len(seen) == 1:
        return seen.pop()  # type: ignore[return-value]
    return "mixed"


def _parse_as_of(value: Any) -> datetime | None:
    """Parse `as_of` envelope RYO (`...Z`); bentuk tak terbaca → None, tidak ditebak."""
    if isinstance(value, datetime):
        try:
            return _to_utc(value)
        except ValueError:
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def oldest_as_of(envelopes: Iterable[dict[str, Any]]) -> datetime | None:
    """as_of tertua dari bukti (konservatif untuk item kesegaran); tanpa bukti → None."""
    stamps = [
        parsed
        for envelope in envelopes
        if (parsed := _parse_as_of(envelope.get("as_of") if isinstance(envelope, dict) else None))
    ]
    return min(stamps) if stamps else None


class SkillResult(BaseModel):
    """Envelope keluaran skill Tenko: sepuluh field RYO + `simulated` (ADR-013/015)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SCHEMA_VERSION
    tool: str
    status: SkillStatus
    data_mode: DataMode = "unknown"
    as_of: datetime | None = None
    request: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    availability: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    simulated: Literal[True] = True

    @field_validator("as_of")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _to_utc(value)

    @field_validator("tool")
    @classmethod
    def _bounded_tool(cls, value: str) -> str:
        """`tool` datang dari argumen CLI: dibatasi panjang & charset, bukan dipercaya."""
        return clean_text(value, TOOL_LIMIT)

    @field_validator("request")
    @classmethod
    def _bounded_request(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Argumen ternormalisasi datang dari shell/agen: dibatasi, bukan dipercaya."""
        return _clean_tree(value, TEXT_LIMIT)

    @field_validator("summary")
    @classmethod
    def _bounded_summary(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Kalimat yang dibaca manusia di kartu & stdout (`headline` + `key_points`).

        Nilai bukan-str dibiarkan apa adanya dengan sengaja: mengonversi angka/objek menjadi
        teks di sini adalah kebohongan tersendiri (aturan yang sama dengan `_text_value`
        di `preflight/items.py`).
        """
        cleaned = _clean_tree(value, TEXT_LIMIT)
        headline = cleaned.get("headline")
        if isinstance(headline, str):
            cleaned["headline"] = clean_text(headline, HEADLINE_LIMIT)
        return cleaned

    @field_validator("availability")
    @classmethod
    def _bounded_availability(cls, value: dict[str, str]) -> dict[str, str]:
        """Kunci = id item (batas sama dengan `ItemResult.id`), nilai = kosakata status."""
        return {clean_text(key, KEY_LIMIT): clean_text(marker, KEY_LIMIT) for key, marker in value.items()}

    @field_validator("warnings")
    @classmethod
    def _bounded_warnings(cls, value: list[str]) -> list[str]:
        """Tiap warning dipotong; JUMLAHNYA tidak — membuang warning = menyembunyikan gagal."""
        return [clean_text(text, WARNING_LIMIT) for text in value]

    @field_serializer("as_of")
    def _dump_as_of(self, value: datetime | None) -> str | None:
        return _iso_z(value)

    @model_validator(mode="after")
    def _honesty_invariants(self) -> SkillResult:
        # (c) tidak ada tool yang menjawab → tidak ada data yang boleh ditampilkan
        if self.status == UNAVAILABLE and self.data is not None:
            raise ValueError("status 'unavailable' wajib disertai data=None (spec §3)")
        # arah sebaliknya: 'ok'/'partial' berarti ADA tool yang menjawab, jadi harus ada isinya
        if self.status != UNAVAILABLE and not self.data:
            raise ValueError(f"status {self.status!r} tanpa data: keputusan tanpa bukti tidak boleh dibentuk")
        reason = _unrepresentable(self.data)
        if reason is not None:
            raise ValueError(f"data tidak bisa dilaporkan jujur ({reason})")
        items = self._parsed_items()
        if items is None:
            return self
        # Yang TERSIMPAN di `data` disamakan dengan yang DIVALIDASI. Tanpa ini, item yang
        # dirakit tangan (dict biasa, bukan ItemResult) menyimpan bentuk mentahnya di `data`
        # sementara hanya salinan terurainya yang dibersihkan — id 100 kB tetap ikut ke
        # stdout. `self.data` adalah dict hasil validasi pydantic, bukan objek pemanggil,
        # jadi penulisan ini tidak menyentuh milik siapa pun di luar model.
        self.data["items"] = [item.model_dump(mode="json") for item in items]
        seen: set[str] = set()
        for item in items:
            if item.id in seen:
                raise ValueError(f"item {item.id!r} muncul dua kali; id item harus unik")
            seen.add(item.id)
            # (b) availability konsisten dua arah dengan status item
            marker = self.availability.get(item.id)
            if marker is None:
                raise ValueError(f"item {item.id!r} tidak punya entri di availability")
            if item.status == UNSPOKEN and marker != UNSPOKEN:
                raise ValueError(
                    f"item {item.id!r} unspoken tetapi availability={marker!r}: "
                    "yang tidak terjawab harus terlihat tidak terjawab"
                )
            if item.status != UNSPOKEN and marker == UNSPOKEN:
                raise ValueError(f"item {item.id!r} berstatus {item.status!r} tetapi availability='unspoken'")
        return self

    def _parsed_items(self) -> list[ItemResult] | None:
        """`data["items"]` sebagai ItemResult (validator item ikut berlaku di jalur dict)."""
        if self.data is None:
            return None
        raw = self.data.get("items")
        if raw is None:
            return None
        if not isinstance(raw, list):
            raise ValueError("data['items'] harus berupa daftar ItemResult")
        if not raw and self.status != UNAVAILABLE:
            raise ValueError(f"status {self.status!r} dengan items kosong: tidak ada satu pun bukti")
        return [ItemResult.model_validate(entry) for entry in raw]

    @property
    def items(self) -> list[ItemResult]:
        """Item yang tersimpan di `data` (kosong bila tidak ada data)."""
        return self._parsed_items() or []

    # --- konstruktor ---------------------------------------------------------------------
    @classmethod
    def unavailable(
        cls,
        tool: str,
        request: dict[str, Any] | None = None,
        warnings: Sequence[str] = (),
        *,
        headline: str | None = None,
        availability: dict[str, str] | None = None,
    ) -> SkillResult:
        """Envelope jujur saat tidak ada bukti sama sekali: `data=None`, `as_of=None`."""
        return cls(
            tool=tool,
            status=UNAVAILABLE,
            data_mode="unknown",
            as_of=None,
            request=dict(request or {}),
            data=None,
            summary={"headline": headline or f"{tool}: no evidence available.", "key_points": []},
            availability=dict(availability or {}),
            warnings=list(warnings),
        )

    @classmethod
    def from_items(
        cls,
        *,
        tool: str,
        request: dict[str, Any],
        items: Sequence[ItemResult],
        envelopes: Sequence[dict[str, Any]] = (),
        summary: dict[str, Any] | None = None,
        warnings: Sequence[str] = (),
        data_extra: dict[str, Any] | None = None,
        availability_extra: dict[str, str] | None = None,
    ) -> SkillResult:
        """Rakit envelope dari item + envelope tool sumber (dipakai TASKS 2.2).

        `status`/`data_mode`/`as_of` diturunkan dari envelope bukti, bukan dari item, supaya
        "tidak ada tool yang menjawab" tidak bisa disamarkan sebagai keputusan.
        """
        status = derive_status([envelope.get("status") for envelope in envelopes])
        availability = {item.id: (UNSPOKEN if item.status == UNSPOKEN else AVAILABLE) for item in items}
        availability.update(availability_extra or {})
        if status == UNAVAILABLE:
            spoken = [item.id for item in items if item.status != UNSPOKEN]
            if spoken:
                raise SkillError(
                    f"no source tool answered, yet items {spoken} still carry a verdict; "
                    "evidence and conclusion may not contradict each other"
                )
            return cls.unavailable(
                tool,
                request=request,
                warnings=warnings,
                headline=(summary or {}).get("headline"),
                availability=availability,
            )
        answered = [envelope for envelope in envelopes if envelope.get("status") in ANSWERED_STATUSES]
        data: dict[str, Any] = dict(data_extra or {})
        data["items"] = [item.model_dump(mode="json") for item in items]
        return cls(
            tool=tool,
            status=status,
            data_mode=derive_data_mode([envelope.get("data_mode") for envelope in answered]),
            as_of=oldest_as_of(answered),
            request=dict(request),
            data=data,
            summary=dict(summary or {"headline": f"{tool}: {status}.", "key_points": []}),
            availability=availability,
            warnings=list(warnings),
        )


# --- registry skill ----------------------------------------------------------------------
SkillFn = Callable[..., SkillResult]

# Diisi oleh paket pemilik skill (TASKS 2.2), BUKAN oleh core yang mengimpor preflight:
# core/ tidak boleh bergantung pada lapisan di atasnya (ADR-002, tests/test_scaffold.py).
# Nama modul di sini diimpor secara malas oleh `load_skill`, dan modulnya memanggil
# `register_skill` saat diimpor.
SKILL_MODULES: dict[str, str] = {}

_REGISTRY: dict[str, SkillFn] = {}


def register_skill(name: str, fn: SkillFn) -> None:
    """Daftarkan implementasi skill. Nama ganda = kesalahan program, bukan penimpaan diam."""
    if not name:
        raise SkillError("empty skill name")
    if name in _REGISTRY:
        raise SkillError(f"skill {name!r} is already registered")
    _REGISTRY[name] = fn


def skill_names() -> tuple[str, ...]:
    """Nama skill yang sudah terdaftar (terurut)."""
    return tuple(sorted(_REGISTRY))


def load_skill(name: str) -> SkillFn | None:
    """Ambil skill; impor modul pemiliknya bila perlu. Belum ada → None (bukan exception)."""
    fn = _REGISTRY.get(name)
    if fn is not None:
        return fn
    module_name = SKILL_MODULES.get(name)
    if module_name is None:
        return None
    try:
        importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 — modul skill pihak lain: apa pun boleh meledak
        # Laporkan KELASNYA saja; pesan impor bisa memuat path atau teks pihak ketiga.
        raise SkillError(
            f"skill {name!r} failed to load from {module_name!r}: {type(exc).__name__}"
        ) from None
    return _REGISTRY.get(name)


def _reset_registry() -> None:
    """Hanya untuk tes: kosongkan registry proses ini."""
    _REGISTRY.clear()
