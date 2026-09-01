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
    except Exception as exc:  # modul skill rusak: laporkan kelasnya, jangan pesannya
        raise SkillError(
            f"skill {name!r} failed to load from {module_name!r}: {type(exc).__name__}"
        ) from None
    return _REGISTRY.get(name)


def _reset_registry() -> None:
    """Hanya untuk tes: kosongkan registry proses ini."""
    _REGISTRY.clear()
