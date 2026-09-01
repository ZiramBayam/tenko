"""Tes TASKS 1.3 — gate ADR-005 (`core/sources.py`) di konstruksi klien.

Konteks: sampai 1.2 tidak ada satu pun kode yang membaca `sources.yaml`, jadi `approved`
hanyalah dokumentasi. Tes di bawah menuntut penegakan di JALUR EKSEKUSI: `RyoClient.from_env`
menolak sebelum klien live terbentuk, sementara `FixtureClient` tetap jalan (ADR-005:
sumber yang belum approved boleh dipakai lewat fixture).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.ryo_client import ENV_KEY, ENV_URL, FixtureClient, RyoClient
from core.sources import (
    ENV_SOURCES_PATH,
    RYO_TOOLS,
    SourceNotApprovedError,
    SourcesError,
    SourcesFileError,
    is_usable,
    load_sources,
    require_source,
    usable_source_names,
)

ROOT = Path(__file__).resolve().parent.parent
REAL_SOURCES = ROOT / "sources.yaml"
# Host sengaja tidak bisa dirutekan: bila gate ADR-005 regresi, tes TIDAK boleh
# mengirim satu request pun ke API penyelenggara (Official Rules: "interfere/abuse").
BASE_URL = "https://127.0.0.1:1/"
KEY = "ryo_mcp_TESTKEY_do_not_log"


def _write_sources(tmp_path: Path, approved: str, *, enabled: str = "true") -> Path:
    """Salinan sources.yaml dengan satu nilai `approved` yang diganti (tes AC 5)."""
    path = tmp_path / "sources.yaml"
    path.write_text(
        "sources:\n"
        "  ryo_tools:\n"
        f"    enabled: {enabled}\n"
        f"    approved: {approved}\n"
        "    used_for: [analyze_token]\n",
        encoding="utf-8",
    )
    return path


# --- registry nyata ----------------------------------------------------------------------
def test_real_sources_yaml_declares_ryo_tools_approved() -> None:
    entry = require_source(RYO_TOOLS, path=REAL_SOURCES)
    assert entry.enabled is True
    assert entry.approved is True
    assert entry.usable is True


def test_usable_source_names_is_sorted_and_from_registry() -> None:
    names = usable_source_names(path=REAL_SOURCES)
    assert names == tuple(sorted(names))
    assert RYO_TOOLS in names


# --- aturan approved ---------------------------------------------------------------------
@pytest.mark.parametrize("approved", ["false", "null", '"true"', "'true'", "1", "~"])
def test_approved_must_be_boolean_true(tmp_path: Path, approved: str) -> None:
    """`approved: null` dan string "true" BUKAN persetujuan (CLAUDE.md, ADR-005)."""
    path = _write_sources(tmp_path, approved)
    assert is_usable(RYO_TOOLS, path=path) is False
    with pytest.raises(SourceNotApprovedError):
        require_source(RYO_TOOLS, path=path)


def test_disabled_source_is_not_usable(tmp_path: Path) -> None:
    path = _write_sources(tmp_path, "true", enabled="false")
    assert is_usable(RYO_TOOLS, path=path) is False
    with pytest.raises(SourceNotApprovedError):
        require_source(RYO_TOOLS, path=path)


def test_unknown_source_fails_closed(tmp_path: Path) -> None:
    path = _write_sources(tmp_path, "true")
    assert is_usable("coingecko", path=path) is False
    with pytest.raises(SourceNotApprovedError):
        require_source("coingecko", path=path)


def test_missing_file_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "tidak-ada.yaml"
    assert is_usable(RYO_TOOLS, path=missing) is False
    with pytest.raises(SourcesFileError):
        require_source(RYO_TOOLS, path=missing)


def test_malformed_yaml_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text("sources: [bukan, peta]\n", encoding="utf-8")
    assert is_usable(RYO_TOOLS, path=path) is False
    with pytest.raises(SourcesFileError):
        load_sources(path=path)


def test_yaml_is_parsed_with_safe_loader(tmp_path: Path) -> None:
    """`sources.yaml` adalah input: tag Python arbitrer harus ditolak, bukan dieksekusi."""
    path = tmp_path / "sources.yaml"
    path.write_text("sources: !!python/object/apply:os.system ['echo pwned']\n", encoding="utf-8")
    with pytest.raises(SourcesFileError):
        load_sources(path=path)


def test_source_not_approved_error_is_sources_error() -> None:
    assert issubclass(SourceNotApprovedError, SourcesError)
    assert issubclass(SourcesFileError, SourcesError)


def test_error_message_names_source_and_reason(tmp_path: Path) -> None:
    path = _write_sources(tmp_path, "null")
    with pytest.raises(SourceNotApprovedError) as excinfo:
        require_source(RYO_TOOLS, path=path)
    assert RYO_TOOLS in str(excinfo.value)


# --- gate di konstruksi klien (AC 5) -----------------------------------------------------
def test_from_env_refuses_when_source_not_approved(tmp_path: Path) -> None:
    path = _write_sources(tmp_path, "false")
    env = {ENV_URL: BASE_URL, ENV_KEY: KEY, ENV_SOURCES_PATH: str(path)}
    with pytest.raises(SourceNotApprovedError):
        RyoClient.from_env(env)


def test_from_env_refuses_when_registry_missing(tmp_path: Path) -> None:
    env = {ENV_URL: BASE_URL, ENV_KEY: KEY, ENV_SOURCES_PATH: str(tmp_path / "hilang.yaml")}
    with pytest.raises(SourcesError):
        RyoClient.from_env(env)


def test_from_env_passes_with_approved_registry(tmp_path: Path) -> None:
    path = _write_sources(tmp_path, "true")
    env = {ENV_URL: BASE_URL, ENV_KEY: KEY, ENV_SOURCES_PATH: str(path)}
    client = RyoClient.from_env(env)
    try:
        assert client.base_url == BASE_URL.rstrip("/")
    finally:
        client.close()


def test_gate_message_never_contains_key(tmp_path: Path) -> None:
    path = _write_sources(tmp_path, "false")
    env = {ENV_URL: BASE_URL, ENV_KEY: KEY, ENV_SOURCES_PATH: str(path)}
    with pytest.raises(SourceNotApprovedError) as excinfo:
        RyoClient.from_env(env)
    assert KEY not in str(excinfo.value)
    assert KEY not in repr(excinfo.value)


def test_gate_runs_before_client_construction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Klien live tidak boleh terbentuk sama sekali bila sumber tidak approved."""
    path = _write_sources(tmp_path, "false")
    built: list[str] = []

    class Tripwire(RyoClient):
        def __init__(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - tidak boleh jalan
            built.append("constructed")
            raise AssertionError("klien live terbentuk meski sumber tidak approved")

    env = {ENV_URL: BASE_URL, ENV_KEY: KEY, ENV_SOURCES_PATH: str(path)}
    with pytest.raises(SourceNotApprovedError):
        Tripwire.from_env(env)
    assert built == []


def test_fixture_client_bypasses_the_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-005: sumber yang belum approved TETAP boleh dipakai lewat fixture."""
    path = _write_sources(tmp_path, "false")
    monkeypatch.setenv(ENV_SOURCES_PATH, str(path))
    client = FixtureClient()
    envelope = client.call("analyze_token", {"symbol": "SOL"})
    assert envelope["status"] == "ok"
    assert client.calls == [("analyze_token", {"symbol": "SOL"})]


def test_env_var_overrides_default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_sources(tmp_path, "false")
    monkeypatch.setenv(ENV_SOURCES_PATH, str(path))
    assert is_usable(RYO_TOOLS) is False
    monkeypatch.delenv(ENV_SOURCES_PATH)
    assert is_usable(RYO_TOOLS) is True  # sources.yaml repo: approved: true


# --- ronde perbaikan 1.3: kebenaran YAML 1.1 didokumentasikan & diuji --------------------
@pytest.mark.parametrize("approved", ["yes", "Yes", "YES", "on", "On", "true", "True", "TRUE"])
def test_yaml_1_1_truthy_words_are_parsed_as_boolean_true(tmp_path: Path, approved: str) -> None:
    """PyYAML (YAML 1.1) mengurai `yes`/`on`/`TRUE` menjadi boolean True → ikut diterima.

    Perilaku parser, bukan kelonggaran kita; didokumentasikan di docstring `core/sources.py`
    supaya audit manusia tidak menemukannya sebagai kejutan.
    """
    path = _write_sources(tmp_path, approved)
    assert is_usable(RYO_TOOLS, path=path) is True


@pytest.mark.parametrize("approved", ["no", "No", "off", "Off", "n", "y"])
def test_yaml_1_1_falsy_and_ambiguous_words_are_refused(tmp_path: Path, approved: str) -> None:
    path = _write_sources(tmp_path, approved)
    assert is_usable(RYO_TOOLS, path=path) is False


def test_duplicate_source_keys_are_refused(tmp_path: Path) -> None:
    """Last-wins diam-diam: auditor membaca `false` di atas, kode memakai `true` di bawah."""
    path = tmp_path / "sources.yaml"
    path.write_text(
        "sources:\n"
        "  ryo_tools:\n    enabled: true\n    approved: false\n"
        "  ryo_tools:\n    enabled: true\n    approved: true\n",
        encoding="utf-8",
    )
    assert is_usable(RYO_TOOLS, path=path) is False
    with pytest.raises(SourcesFileError):
        load_sources(path=path)


def test_duplicate_keys_inside_one_entry_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text(
        "sources:\n  ryo_tools:\n    enabled: true\n    approved: false\n    approved: true\n",
        encoding="utf-8",
    )
    with pytest.raises(SourcesFileError):
        load_sources(path=path)


# --- pesan yang dipublikasikan (masuk warnings → stdout → kartu panggilan) ---------------
def test_message_does_not_leak_absolute_path(tmp_path: Path) -> None:
    missing = tmp_path / "rahasia" / "sources.yaml"
    with pytest.raises(SourcesFileError) as excinfo:
        load_sources(path=missing)
    message = str(excinfo.value)
    assert "sources.yaml" in message
    assert str(tmp_path) not in message
    assert "rahasia" not in message


def test_message_does_not_leak_file_contents(tmp_path: Path) -> None:
    secret = "SECRET-CONTENT-that-must-not-be-echoed"
    path = tmp_path / "sources.yaml"
    path.write_text(
        f"sources:\n  ryo_tools:\n    enabled: true\n    approved: '{secret}'\n",
        encoding="utf-8",
    )
    with pytest.raises(SourceNotApprovedError) as excinfo:
        require_source(RYO_TOOLS, path=path)
    message = str(excinfo.value)
    assert secret not in message
    assert "str" in message  # tipe boleh disebut, isinya tidak


def test_python_tag_still_refused_by_strict_loader(tmp_path: Path) -> None:
    """Loader ketat adalah turunan SafeLoader: penolakan tag Python tidak boleh regresi."""
    marker = tmp_path / "PWNED"
    path = tmp_path / "sources.yaml"
    path.write_text(
        f"sources: !!python/object/apply:os.system ['touch {marker}']\n",
        encoding="utf-8",
    )
    with pytest.raises(SourcesFileError):
        load_sources(path=path)
    assert not marker.exists()
