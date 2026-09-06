"""Registry sumber eksternal (`sources.yaml`) — gate ADR-005 di jalur eksekusi.

Kenapa modul ini ada: Official Rules hanya mengizinkan endpoint/sumber yang approved
(docs/rules-compliance.md baris pertama), dan ADR-005 menetapkan `sources.yaml` sebagai
registry-nya. Sampai TASKS 1.2 tidak ada satu pun kode yang MEMBACA berkas itu, jadi
`approved:` hanyalah dokumentasi. Di sini ia menjadi penegakan.

Sifat modul ini (tiap klaim punya tes di ``tests/test_sources.py``):

* **Approved berarti boolean `true`, titik.** ``approved: null``, ``approved: "true"``,
  ``approved: 1`` — semuanya BUKAN persetujuan (`approved is True`). ADR-005: "`approved:
  null` = belum boleh dipakai di jalur nyata (fixture boleh)". Perlu diketahui: PyYAML
  memakai YAML 1.1, sehingga ``yes``/``on``/``TRUE``/``True`` DIURAI menjadi boolean `True`
  dan karena itu ikut diterima (``no``/``off`` menjadi `False` dan ditolak). Itu keputusan
  parser, bukan kelonggaran kita — dan ada tesnya supaya tidak jadi kejutan saat audit.
* **Fail closed.** Berkas hilang, YAML rusak, sumber tidak terdaftar → gagal, bukan
  "anggap saja boleh". Yang gagal-tertutup adalah jalur NYATA; fixture tidak lewat sini.
* **Hanya `yaml.safe_load`.** `sources.yaml` diperlakukan sebagai input: tag
  ``!!python/object/apply`` harus ditolak, bukan dieksekusi.
* **Tanpa cache.** Berkasnya ~15 baris dan dibaca sekali per konstruksi klien; cache hanya
  akan membuat "sumber dimatikan" tidak berlaku sampai proses di-restart.
* **`is_usable` tidak pernah melempar**, `require_source` selalu melempar
  :class:`SourcesError` (dan hanya itu) saat menolak. Batas produk (`core/skill_cli.py`)
  yang mengubahnya menjadi ``SkillResult{status:"unavailable"}`` — ADR-005 "mematikan sumber
  tidak boleh menyebabkan exception" dipenuhi di sana, bukan dengan menelan error di dalam
  klien.

Yang SENGAJA tidak ada: penulisan/perubahan `sources.yaml` (persetujuan sumber adalah
keputusan manusia + baris DISCLOSURE.md, bukan efek samping kode).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("core.sources")


class _StrictLoader(yaml.SafeLoader):
    """`SafeLoader` (tetap menolak tag Python) + penolakan kunci ganda.

    PyYAML default membiarkan kunci ganda dan memenangkan yang TERAKHIR: entri `ryo_tools`
    dengan `approved: false` yang diikuti entri kedua `approved: true` akan lolos, padahal
    auditor yang membaca dari atas melihat "false". Registry persetujuan tidak boleh punya
    dua kebenaran.
    """


def _mapping_without_duplicates(loader: _StrictLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r} in source registry", key_node.start_mark
            )
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping_without_duplicates)

ENV_SOURCES_PATH = "SOURCES_PATH"
DEFAULT_SOURCES_PATH = Path(__file__).resolve().parent.parent / "sources.yaml"

# nama entri di sources.yaml (dipakai jalur produksi)
RYO_TOOLS = "ryo_tools"
LLM_NARRATION = "llm_narration"


class SourcesError(Exception):
    """Akar kegagalan registry sumber. Pesan tidak pernah memuat kredensial."""


class SourcesFileError(SourcesError):
    """`sources.yaml` tidak bisa dibaca atau bentuknya bukan registry yang sah."""


class SourceNotApprovedError(SourcesError):
    """Sumber tidak terdaftar, dimatikan, atau `approved` bukan boolean `true` (ADR-005)."""


@dataclass(frozen=True)
class SourceEntry:
    """Satu entri `sources.yaml`. `approved` sudah dinormalisasi menjadi boolean ketat."""

    name: str
    enabled: bool
    approved: bool
    used_for: tuple[str, ...] = ()
    approved_raw: Any = None  # nilai mentah dari YAML, hanya untuk pesan error

    @property
    def usable(self) -> bool:
        """Boleh dipakai di jalur nyata (ADR-005)."""
        return self.enabled and self.approved


def _resolve_path(path: str | Path | None, env: dict[str, str] | None) -> Path:
    """Urutan: argumen eksplisit → env `SOURCES_PATH` → `sources.yaml` di akar repo."""
    if path is not None:
        return Path(path)
    source = os.environ if env is None else env
    from_env = source.get(ENV_SOURCES_PATH, "").strip()
    return Path(from_env) if from_env else DEFAULT_SOURCES_PATH


def _kind(value: Any) -> str:
    """Sebut TIPE nilai, bukan isinya: pesan ini dipublikasikan (spec §7)."""
    return "null" if value is None else type(value).__name__


def _as_bool(value: Any) -> bool:
    """Hanya boolean `True` yang dihitung; string "true"/1/None → False."""
    return value is True


def _entry(name: str, raw: Any) -> SourceEntry:
    if not isinstance(raw, dict):
        raise SourcesFileError(f"source {name!r}: entry must be a mapping, not {type(raw).__name__}")
    used_for = raw.get("used_for") or ()
    if not isinstance(used_for, (list, tuple)):
        raise SourcesFileError(f"source {name!r}: 'used_for' must be a list")
    return SourceEntry(
        name=name,
        enabled=_as_bool(raw.get("enabled")),
        approved=_as_bool(raw.get("approved")),
        used_for=tuple(str(item) for item in used_for),
        approved_raw=raw.get("approved"),
    )


def load_sources(path: str | Path | None = None, env: dict[str, str] | None = None) -> dict[str, SourceEntry]:
    """Baca registry. Bentuk yang tidak sah → :class:`SourcesFileError` (fail closed)."""
    resolved = _resolve_path(path, env)
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        # Pesan ini ikut ke `warnings[]` yang dicetak ke stdout dan (spec §7) ke kartu
        # panggilan publik: hanya NAMA berkas, tidak pernah jalur absolut — jalur memuat
        # nama pengguna OS dan tata letak direktori. Jalur lengkapnya ke log lokal saja.
        log.debug("registry sumber tidak bisa dibaca (%s): %s", resolved, type(exc).__name__)
        raise SourcesFileError(f"source registry unreadable: {resolved.name}") from None
    try:
        # `_StrictLoader` adalah turunan `SafeLoader`: tag Python tetap ditolak (ada tesnya).
        document = yaml.load(text, Loader=_StrictLoader)  # noqa: S506 - loader turunan SafeLoader
    except yaml.YAMLError as exc:
        log.debug("registry sumber tidak bisa diurai (%s): %s", resolved, exc)
        raise SourcesFileError(f"source registry is not valid YAML: {resolved.name}") from None
    if document is None:
        raise SourcesFileError(f"source registry is empty: {resolved.name}")
    if not isinstance(document, dict):
        raise SourcesFileError(f"source registry must be a mapping with a 'sources' key: {resolved.name}")
    raw_sources = document.get("sources")
    if not isinstance(raw_sources, dict):
        raise SourcesFileError(f"source registry has no 'sources' mapping: {resolved.name}")
    return {str(name): _entry(str(name), raw) for name, raw in raw_sources.items()}


def require_source(
    name: str, path: str | Path | None = None, env: dict[str, str] | None = None
) -> SourceEntry:
    """Kembalikan entri hanya bila `enabled` DAN `approved is True`; selain itu melempar.

    Dipanggil SEBELUM klien live terbentuk (`RyoClient.from_env`), sehingga sumber yang
    belum disetujui tidak pernah menerima satu request pun.
    """
    entries = load_sources(path, env)
    entry = entries.get(name)
    if entry is None:
        raise SourceNotApprovedError(f"source {name!r} is not listed in sources.yaml (ADR-005)")
    if not entry.enabled:
        raise SourceNotApprovedError(f"source {name!r} is disabled in sources.yaml (enabled=false, ADR-005)")
    if not entry.approved:
        # Hanya TIPE nilainya yang disebut. Nilai mentahnya adalah isi berkas; pesan ini
        # dipublikasikan, jadi ia tidak boleh menjadi saluran pembaca berkas.
        log.debug("sumber %r: approved bertipe %s", name, type(entry.approved_raw).__name__)
        raise SourceNotApprovedError(
            f"source {name!r} is not approved in sources.yaml "
            f"(approved is {_kind(entry.approved_raw)}; only boolean true counts, ADR-005); "
            "the fixture path stays available"
        )
    return entry


def is_usable(name: str, path: str | Path | None = None, env: dict[str, str] | None = None) -> bool:
    """`True` hanya bila sumber `enabled` dan `approved is True`. Tidak pernah melempar."""
    try:
        require_source(name, path, env)
    except SourcesError as exc:
        log.debug("sumber tidak bisa dipakai: %s", exc)
        return False
    return True


def usable_source_names(path: str | Path | None = None, env: dict[str, str] | None = None) -> tuple[str, ...]:
    """Nama sumber yang boleh dipakai di jalur nyata, terurut (dasar baris DISCLOSURE.md)."""
    try:
        entries = load_sources(path, env)
    except SourcesError as exc:
        log.debug("registry sumber tidak terbaca: %s", exc)
        return ()
    return tuple(sorted(name for name, entry in entries.items() if entry.usable))
