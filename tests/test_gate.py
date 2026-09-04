"""Tes TASKS 0.7 — `scripts/gate.sh` harus memeriksa apa yang `make lint` periksa.

Sampai 4 Sep 2026 baris pemeriksa gate hanya `uv run ruff check .`, sedangkan `make lint`
menjalankan `ruff check .` **dan** `ruff format --check .`. Akibatnya drift format lolos gate
dan baru terlihat saat seseorang menjalankan `make lint` dengan tangan — persis yang terjadi
pada `tests/test_store.py` (unformatted beberapa commit, diperbaiki `ab60e2e`). Selama celah
itu ada, "gate hijau" bukan bukti AC.

Yang dijaga di sini:

1. **Paritas perintah.** Setiap `ruff ...` di target `lint` Makefile harus muncul di `gate.sh`.
2. **Gate hijau nyata.** Gate pada repo ini, dengan stdin `{"stop_hook_active": false}`,
   keluar 0.
3. **Pemeriksanya menggigit.** Berkas yang sengaja di-unformat membuat gate MERAH (exit 2)
   dengan label `ruff-format` di stderr — label terpisah dari `ruff` supaya penyebabnya jelas.
4. **Verdict sama.** `make lint` dan gate sepakat pada tree yang sama.

Batas yang dipegang tes ini: **tidak ada berkas unformat yang ditinggalkan di repo**. `gate.sh`
melakukan `cd "$(dirname "$0")/.."`, jadi bukti (3) dikerjakan di salinan minimal repo di
`tmp_path` (scripts + pyproject.toml + uv.lock + `.venv` yang di-symlink), bukan di pohon kerja.
Repo hanya dibaca.

Rekursi: gate menjalankan `uv run pytest -q`, yang akan menjalankan berkas ini lagi. `gate.sh`
menyetel `TENKO_GATE_SELFTEST=1` untuk pytest-nya, dan tes yang memanggil gate meneruskan
variabel itu ke anaknya. Jadi: `make test` oleh manusia menjalankan gate sekali (satu suite
bersarang), sedangkan gate/stop hook tidak menjalankan suite dua kali.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "scripts" / "gate.sh"
STDIN = '{"stop_hook_active": false}'
NESTED = os.environ.get("TENKO_GATE_SELFTEST") == "1"
skip_if_nested = pytest.mark.skipif(NESTED, reason="dijalankan oleh gate itu sendiri; hindari rekursi")


def _makefile_lint_recipe() -> str:
    """Baris resep target `lint:` di Makefile (tanpa TAB pemimpin)."""
    lines = (ROOT / "Makefile").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("lint:"))
    body = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        body.append(line.lstrip("\t"))
    return "\n".join(body)


def _ruff_commands(text: str) -> set[str]:
    """Himpunan sub-perintah ruff (`check`/`format --check`) yang muncul di `text`."""
    found = set()
    for match in re.finditer(r"ruff\s+(check|format)((?:\s+--[\w-]+)*)", text):
        found.add((match.group(1) + " " + match.group(2)).split("#")[0].strip())
    return found


def _exe(name: str) -> str:
    """Path absolut executable (S607: jangan bergantung pada PATH saat proses dijalankan)."""
    found = shutil.which(name)
    if found is None:  # pragma: no cover
        pytest.skip(f"{name} tidak ada di PATH")
    return found


def _run_gate(script: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full = {**os.environ, "TENKO_GATE_SELFTEST": "1", **(env or {})}
    return subprocess.run(
        [_exe("bash"), str(script)],
        input=STDIN,
        capture_output=True,
        text=True,
        timeout=1800,
        env=full,
    )


@pytest.fixture(scope="module")
def gate_on_repo() -> subprocess.CompletedProcess[str]:
    """Satu kali jalan gate pada repo nyata; dipakai bersama oleh beberapa tes."""
    if NESTED:
        pytest.skip("dijalankan oleh gate itu sendiri; hindari rekursi")
    return _run_gate(GATE)


# --- (1) paritas perintah -------------------------------------------------------------


def test_gate_runs_every_ruff_command_that_make_lint_runs() -> None:
    """Kalau `make lint` memeriksanya, gate juga — tidak ada celah yang lolos diam-diam."""
    lint = _ruff_commands(_makefile_lint_recipe())
    gate = _ruff_commands(GATE.read_text())
    assert lint, "target lint: di Makefile tidak memanggil ruff sama sekali"
    assert lint <= gate, f"hanya diperiksa `make lint`, tidak oleh gate: {sorted(lint - gate)}"


def test_gate_labels_format_failure_separately() -> None:
    """Label `ruff-format` terpisah dari `ruff` supaya pesan MERAH menyebut penyebabnya."""
    text = GATE.read_text()
    assert 'FAIL="$FAIL ruff-format"' in text
    assert 'FAIL="$FAIL ruff"' in text


# --- (2) gate hijau pada working tree bersih ------------------------------------------


@skip_if_nested
def test_gate_is_green_on_this_tree(gate_on_repo: subprocess.CompletedProcess[str]) -> None:
    """Pohon kerja bersih → exit 0, stderr tanpa 'GATE MERAH'."""
    assert gate_on_repo.returncode == 0, gate_on_repo.stderr
    assert "GATE MERAH" not in gate_on_repo.stderr


# --- (3) pemeriksa format benar-benar menggigit ---------------------------------------


def _sandbox(tmp_path: Path, body: str) -> Path:
    """Salinan minimal repo di tmp_path; `gate.sh` di dalamnya ber-`cd` ke sandbox, bukan repo."""
    if shutil.which("uv") is None or not (ROOT / ".venv").is_dir():  # pragma: no cover
        pytest.skip("butuh uv + .venv proyek untuk menjalankan ruff di sandbox")
    (tmp_path / "scripts").mkdir()
    for name in ("gate.sh", "_json.sh"):
        shutil.copy(ROOT / "scripts" / name, tmp_path / "scripts" / name)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy(ROOT / name, tmp_path / name)
    (tmp_path / ".venv").symlink_to(ROOT / ".venv")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sandbox.py").write_text("def test_ok():\n    assert True\n")
    (tmp_path / "mod.py").write_text(body)
    return tmp_path / "scripts" / "gate.sh"


FORMATTED = "x = 1\n"
UNFORMATTED = "x   =  1\n"  # lolos `ruff check`, ditolak `ruff format --check`


@skip_if_nested
def test_sandbox_control_is_green(tmp_path: Path) -> None:
    """Kontrol: sandbox yang sama tanpa drift format → HIJAU (jadi (b) memang soal format)."""
    result = _run_gate(_sandbox(tmp_path, FORMATTED))
    assert result.returncode == 0, result.stderr


@skip_if_nested
def test_unformatted_file_turns_the_gate_red(tmp_path: Path) -> None:
    """Satu berkas unformat → exit 2 dan stderr menyebut `ruff-format`."""
    result = _run_gate(_sandbox(tmp_path, UNFORMATTED))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "ruff-format" in result.stderr
    assert "GATE MERAH" in result.stderr


@skip_if_nested
def test_unformatted_file_is_invisible_to_ruff_check_alone(tmp_path: Path) -> None:
    """Bukti celah lama: `ruff check` sendirian menganggap berkas itu bersih."""
    (tmp_path / "mod.py").write_text(UNFORMATTED)
    shutil.copy(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    check = subprocess.run(
        [_exe("uv"), "run", "ruff", "check", "mod.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )
    fmt = subprocess.run(
        [_exe("uv"), "run", "ruff", "format", "--check", "mod.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert check.returncode == 0, check.stdout
    assert fmt.returncode != 0


# --- (4) verdict yang sama pada tree yang sama ----------------------------------------


@skip_if_nested
def test_make_lint_and_gate_agree_on_this_tree(gate_on_repo: subprocess.CompletedProcess[str]) -> None:
    """`make lint` hijau ⇔ gate tidak menyebut label ruff mana pun."""
    lint = subprocess.run([_exe("make"), "lint"], cwd=ROOT, capture_output=True, text=True, timeout=600)
    gate_lint_ok = "ruff" not in gate_on_repo.stderr
    assert (lint.returncode == 0) == gate_lint_ok, (
        f"make lint rc={lint.returncode}\n{lint.stdout}{lint.stderr}\ngate: {gate_on_repo.stderr}"
    )
