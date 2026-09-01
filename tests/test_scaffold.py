"""Tes dummy TASKS 0.1: struktur paket ada dan runtime sesuai docs/versions.md."""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ("core", "preflight", "agent", "ui", "grading")


def test_python_version_matches_pin() -> None:
    """docs/versions.md memin Python 3.12 (.python-version)."""
    pinned = (ROOT / ".python-version").read_text().strip()
    assert pinned.startswith("3.12")
    assert sys.version_info[:2] == (3, 12)


@pytest.mark.parametrize("name", PACKAGES)
def test_package_importable(name: str) -> None:
    assert importlib.import_module(name).__doc__


@pytest.mark.parametrize("name", PACKAGES)
def test_package_has_init(name: str) -> None:
    assert (ROOT / name / "__init__.py").is_file()


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_core_does_not_depend_on_upper_layers() -> None:
    """ADR-002: core/ tidak boleh mengimpor lapisan di atasnya."""
    forbidden = {"preflight", "agent", "ui", "grading"}
    offenders = {
        str(path.relative_to(ROOT)): sorted(_imported_top_level_modules(path) & forbidden)
        for path in sorted((ROOT / "core").rglob("*.py"))
        if _imported_top_level_modules(path) & forbidden
    }
    assert offenders == {}
