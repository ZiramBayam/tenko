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

Rekursi dan biayanya (TASKS 0.22). Gate menjalankan `uv run pytest -q`, yang menjalankan berkas
ini lagi. `TENKO_GATE_SELFTEST=1` menghentikan rekursi tak hingga, tetapi tidak menghentikan
BIAYANYA: proses dalam tetap menjalankan ~1590 tes lain, dan `test_gate_is_green_on_this_tree`
sendirian memakan 295 dtk dari 583 dtk (koordinator, 5 Sep 2026).

Cara yang dipilih, dan alasannya. `TENKO_GATE_SELFTEST=1` sekarang berarti satu hal saja bagi
`gate.sh`: "sebuah `pytest` atas repo ini SEDANG berjalan di atas kamu", dan gate melewati leg
pytest-nya. Bukan stub `pytest` lewat `PATH` (gate memanggil `uv run pytest`; uv menaruh
`.venv/bin` di depan PATH, jadi stub semacam itu bohong yang tidak bekerja), bukan penanda
pra-commit (tes yang tidak jalan di `make test` tidak menjaga apa pun di antara commit). Ini
yang paling jujur karena klaim yang hilang memang nol: leg pytest gate adalah "suite ini
hijau" — dan suite ini justru sedang berjalan, di proses yang membaca baris ini. Menjalankannya
lagi di dalam bukan bukti tambahan, hanya salinan pekerjaan yang sama.

Yang tetap harus dibuktikan supaya percepatan ini bukan sekadar memindahkan lubang:
- leg pytest masih ada dan masih MERAH kalau suite gagal (`test_gate_pytest_leg_...`, dua arah,
  di sandbox bertes-satu — murah, dan bukan suite repo);
- yang dilewati HANYA leg pytest: ruff + grep tetap jalan, dibuktikan oleh tes format di bawah
  yang seluruhnya berjalan dengan `TENKO_GATE_SELFTEST=1`;
- gate pada repo tidak lagi memakan menit: ada anggaran waktu yang gagal bila nesting kembali.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

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


class GateRun(NamedTuple):
    """Hasil satu kali jalan gate, plus lama jalannya (dipakai anggaran waktu 0.22)."""

    proc: subprocess.CompletedProcess[str]
    seconds: float


def _run_gate(script: Path, env: dict[str, str] | None = None, selftest: bool = True) -> GateRun:
    """Jalankan gate. `selftest=False` = gate tidak tahu ada pytest di atasnya → leg pytest jalan."""
    full = {**os.environ, **(env or {})}
    if selftest:
        full["TENKO_GATE_SELFTEST"] = "1"
    else:
        full.pop("TENKO_GATE_SELFTEST", None)
    started = time.monotonic()
    proc = subprocess.run(
        [_exe("bash"), str(script)],
        input=STDIN,
        capture_output=True,
        text=True,
        timeout=1800,
        env=full,
    )
    return GateRun(proc, time.monotonic() - started)


@pytest.fixture(scope="module")
def gate_on_repo() -> GateRun:
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


#: Anggaran gate-pada-repo. Isinya dua `uv run ruff` + beberapa grep = hitungan detik; longgar
#: 20x supaya mesin lambat/cold cache tidak bikin merah palsu, tetapi tetap mustahil dilewati
#: kalau suite bersarang kembali (yang berarti ratusan detik).
GATE_BUDGET_SECONDS = 60.0


@skip_if_nested
def test_gate_is_green_on_this_tree(gate_on_repo: GateRun) -> None:
    """Pohon kerja bersih → exit 0, stderr tanpa 'GATE MERAH'.

    Gate dipanggil dengan `TENKO_GATE_SELFTEST=1`, jadi ia melewati leg pytest-nya: suite yang
    sedang menjalankan tes ini ADALAH leg itu. Yang dibuktikan di sini karena itu persis
    paritas gate↔`make lint` pada pohon nyata (ruff check + ruff format) berikut semua grep
    kepatuhan — bukan "seluruh tes lulus", yang sudah/sedang dijawab oleh proses ini sendiri.
    Leg pytest yang dilewati diuji terpisah, dua arah, di sandbox bertes-satu.
    """
    assert gate_on_repo.proc.returncode == 0, gate_on_repo.proc.stderr
    assert "GATE MERAH" not in gate_on_repo.proc.stderr


@skip_if_nested
def test_gate_on_this_tree_does_not_rerun_the_suite(gate_on_repo: GateRun) -> None:
    """Alarm regresi 0.22: kalau nesting kembali, angka ini melonjak dari detik ke menit."""
    assert gate_on_repo.seconds < GATE_BUDGET_SECONDS, (
        f"gate pada repo makan {gate_on_repo.seconds:.1f} dtk (>{GATE_BUDGET_SECONDS:.0f}); "
        "curigai leg pytest berjalan bersarang lagi"
    )


# --- (3) pemeriksa format benar-benar menggigit ---------------------------------------


PASSING_TEST = "def test_ok():\n    assert True\n"
FAILING_TEST = (
    "def test_ok():\n    assert 1 == 2  # gagal, tapi bukan `assert False` yang ditolak ruff B011\n"
)


def _sandbox(tmp_path: Path, body: str, test_body: str = PASSING_TEST) -> Path:
    """Salinan minimal repo di tmp_path; `gate.sh` di dalamnya ber-`cd` ke sandbox, bukan repo.

    Suite sandbox = satu berkas satu tes (`testpaths = ["tests"]` di pyproject yang disalin),
    jadi leg pytest gate bisa diuji sungguhan tanpa membayar suite repo.
    """
    if shutil.which("uv") is None or not (ROOT / ".venv").is_dir():  # pragma: no cover
        pytest.skip("butuh uv + .venv proyek untuk menjalankan ruff di sandbox")
    (tmp_path / "scripts").mkdir()
    for name in ("gate.sh", "_json.sh"):
        shutil.copy(ROOT / "scripts" / name, tmp_path / "scripts" / name)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy(ROOT / name, tmp_path / name)
    (tmp_path / ".venv").symlink_to(ROOT / ".venv")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sandbox.py").write_text(test_body)
    (tmp_path / "mod.py").write_text(body)
    # Sandbox dijadikan repo git supaya `CHANGED_PY` gate punya arti di sini: `mod.py` dan
    # `tests/` menjadi berkas `.py` yang belum dilacak, jadi `git ls-files --others` menemukan
    # mereka dan leg pytest terpicu SEPERTI di repo nyata. Tanpa ini `CHANGED_PY` selalu kosong
    # dan satu-satunya cara memicu leg pytest adalah `TENKO_GATE_FULL=1` — yang membuat cabang
    # "berkas .py bergerak" tidak pernah teruji sama sekali (temuan TINGGI security-reviewer).
    subprocess.run([_exe("git"), "init", "-q"], cwd=tmp_path, check=True, capture_output=True, text=True)
    return tmp_path / "scripts" / "gate.sh"


FORMATTED = "x = 1\n"
UNFORMATTED = "x   =  1\n"  # lolos `ruff check`, ditolak `ruff format --check`


@skip_if_nested
def test_sandbox_control_is_green(tmp_path: Path) -> None:
    """Kontrol: sandbox yang sama tanpa drift format → HIJAU (jadi (b) memang soal format)."""
    result = _run_gate(_sandbox(tmp_path, FORMATTED)).proc
    assert result.returncode == 0, result.stderr


@skip_if_nested
def test_unformatted_file_turns_the_gate_red(tmp_path: Path) -> None:
    """Satu berkas unformat → exit 2 dan stderr menyebut `ruff-format`."""
    result = _run_gate(_sandbox(tmp_path, UNFORMATTED)).proc
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


# --- (5) leg pytest yang dilewati: masih ada, masih menggigit (TASKS 0.22) ------------
#
# `test_gate_is_green_on_this_tree` tidak lagi menjalankan suite di dalam gate. Supaya itu
# percepatan dan bukan pelemahan, leg pytest gate diuji di sini pada sandbox bertes-satu:
# murah, dan verdictnya tentang suite sandbox, bukan suite repo.


@skip_if_nested
def test_gate_pytest_leg_turns_red_on_a_failing_suite(tmp_path: Path) -> None:
    """Arah (a): tanpa `TENKO_GATE_SELFTEST`, suite gagal → exit 2 dengan label `pytest`.

    `TENKO_GATE_FULL=1` dipakai karena sandbox bukan repo git, jadi "berkas .py berubah sejak
    HEAD" kosong dan leg pytest tidak akan dipicu oleh perubahan berkas.
    """
    gate = _sandbox(tmp_path, FORMATTED, test_body=FAILING_TEST)
    result = _run_gate(gate, env={"TENKO_GATE_FULL": "1"}, selftest=False).proc
    assert result.returncode == 2, result.stdout + result.stderr
    assert "pytest" in result.stderr
    assert "GATE MERAH" in result.stderr


@skip_if_nested
def test_gate_pytest_leg_is_green_on_a_passing_suite(tmp_path: Path) -> None:
    """Kontrol untuk (a): sandbox yang sama dengan suite LULUS → HIJAU.

    Tanpa kontrol ini, label `pytest` di (a) bisa saja berarti "pytest tidak bisa dijalankan di
    sandbox" alih-alih "suite merah". Pasangan ini mengunci: labelnya mengikuti verdict suite.
    """
    gate = _sandbox(tmp_path, FORMATTED, test_body=PASSING_TEST)
    result = _run_gate(gate, env={"TENKO_GATE_FULL": "1"}, selftest=False).proc
    assert result.returncode == 0, result.stdout + result.stderr


@skip_if_nested
def test_gate_pytest_leg_is_skipped_inside_a_running_suite(tmp_path: Path) -> None:
    """Arah (b): sandbox yang PERSIS sama + `TENKO_GATE_SELFTEST=1` → HIJAU.

    Satu-satunya beda dengan (a) adalah variabel itu, jadi ini mengunci artinya: gate melewati
    leg pytest ketika sebuah pytest sudah berjalan di atasnya — dan hanya leg itu.

    Sengaja TANPA `TENKO_GATE_FULL`: sejak temuan TINGGI security-reviewer (6 Sep 2026) `FULL`
    diperiksa LEBIH DULU dan mengalahkan `SELFTEST`, jadi memakainya di sini justru akan
    menjalankan leg pytest dan menguji kebalikan dari yang dimaksud. Yang memicu leg pytest di
    sini adalah `CHANGED_PY` — sandbox kini repo git ber-`mod.py` yang belum dilacak.
    """
    gate = _sandbox(tmp_path, FORMATTED, test_body=FAILING_TEST)
    result = _run_gate(gate, selftest=True).proc
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GATE MERAH" not in result.stderr
    # Dilewati TAPI diumumkan (temuan SEDANG security-reviewer 6 Sep 2026): sebelumnya leg
    # pytest mati tanpa satu baris pun yang mengatakannya, jadi stop hook bisa hijau di atas
    # suite merah tanpa suara. Yang dijaga di sini bukan kesenyapannya, melainkan suaranya.
    assert "leg pytest DILEWATI" in result.stderr


@skip_if_nested
def test_gate_full_beats_selftest_so_one_knob_always_wins(tmp_path: Path) -> None:
    """`TENKO_GATE_FULL=1` menjalankan leg pytest MESKI `TENKO_GATE_SELFTEST=1` ada.

    Temuan TINGGI security-reviewer 6 Sep 2026: sebelumnya `SELFTEST` mematikan leg pytest tanpa
    syarat dan `FULL` tidak bisa mengalahkannya, sehingga variabel yang bocor ke shell operator
    berarti stop hook HIJAU di atas suite MERAH, tanpa suara. Ini pasangan langsung dari tes di
    atas: sandbox dan suite yang sama, hanya `FULL` yang ditambahkan.
    """
    gate = _sandbox(tmp_path, FORMATTED, test_body=FAILING_TEST)
    result = _run_gate(gate, env={"TENKO_GATE_FULL": "1"}, selftest=True).proc
    assert result.returncode == 2, result.stdout + result.stderr
    assert "pytest" in result.stderr


@skip_if_nested
def test_selftest_skips_only_pytest_not_the_cheap_checks(tmp_path: Path) -> None:
    """Sandbox yang lolos pytest tapi drift format, dengan selftest=1 → tetap MERAH `ruff-format`.

    Ini yang menjaga jaminan 0.7 tidak ikut terlewat bersama leg pytest.
    """
    gate = _sandbox(tmp_path, UNFORMATTED, test_body=PASSING_TEST)
    result = _run_gate(gate, env={"TENKO_GATE_FULL": "1"}, selftest=True).proc
    assert result.returncode == 2, result.stdout + result.stderr
    assert "ruff-format" in result.stderr
    assert "pytest" not in result.stderr


# --- (4) verdict yang sama pada tree yang sama ----------------------------------------


@skip_if_nested
def test_make_lint_and_gate_agree_on_this_tree(gate_on_repo: GateRun) -> None:
    """`make lint` hijau ⇔ gate tidak menyebut label ruff mana pun."""
    lint = subprocess.run([_exe("make"), "lint"], cwd=ROOT, capture_output=True, text=True, timeout=600)
    stderr = gate_on_repo.proc.stderr
    gate_lint_ok = "ruff" not in stderr
    assert (lint.returncode == 0) == gate_lint_ok, (
        f"make lint rc={lint.returncode}\n{lint.stdout}{lint.stderr}\ngate: {stderr}"
    )
