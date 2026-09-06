"""DISCLOSURE.md harus mendaftar SETIAP paket yang benar-benar terkunci di `uv.lock`.

Kenapa tes ini ada: Official Rules §5 mewajibkan disclosure dependency, dan
penilaian rubric (`docs/judge-reports/fase-1.md`) menandai daftar yang tidak lengkap
sebagai satu-satunya kategori temuan yang berisiko DISKUALIFIKASI, bukan sekadar
kehilangan poin. Saat task 2.3 nanti benar-benar memakai `anthropic`, atau saat
dependency baru ikut lewat `uv sync`, tes inilah yang gagal lebih dulu — bukan juri.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "uv.lock"
DISCLOSURE = ROOT / "DISCLOSURE.md"

# Proyek ini sendiri, bukan dependency.
SELF = {"tenko"}

# Nama yang sah disebut DISCLOSURE.md tetapi memang TIDAK ada di uv.lock.
# Aset frontend di-vendor manual tanpa npm (ADR-014), bukan paket Python:
NON_PYTHON = {"three", "gsap", "lenis", "scrolltrigger"}
# Model & layanan yang wajib didisclose (AI tools, external services) tetapi bukan
# distribusi Python — tanpa ini tes menuntut DISCLOSURE membuang informasi yang justru
# diwajibkan Official Rules §5.
NON_PACKAGE = {"claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5", "ryo-chan", "tavily"}


def _lock_packages() -> set[str]:
    text = LOCK.read_text(encoding="utf-8")
    return {n for n in re.findall(r'^name = "([^"]+)"', text, re.M)} - SELF


def _disclosure_text() -> str:
    return DISCLOSURE.read_text(encoding="utf-8").lower()


def test_lock_is_not_empty() -> None:
    """Kalau parsing lock gagal, tes lain akan lulus palsu."""
    assert len(_lock_packages()) > 10


@pytest.mark.parametrize("package", sorted(_lock_packages()))
def test_every_locked_package_is_disclosed(package: str) -> None:
    """Tiap paket di uv.lock harus muncul di DISCLOSURE.md."""
    assert package.lower() in _disclosure_text(), (
        f"'{package}' terkunci di uv.lock tetapi tidak disebut di DISCLOSURE.md. "
        "Official Rules §5: setiap dependency wajib didaftar."
    )


MARK_START = "<!-- python-deps:start -->"
MARK_END = "<!-- python-deps:end -->"


def _disclosed_python_packages() -> list[str]:
    """Nama paket dari kolom pertama tabel di antara penanda python-deps.

    Penanda itu ada supaya tes ini tidak perlu menebak baris mana yang tabel dependency:
    di luar penanda, dokumen bebas menyebut identifier kode (`SkillResult`), model
    (`claude-opus-5`), dan aset npm (three/gsap/lenis) tanpa membuat tes rapuh.
    """
    text = DISCLOSURE.read_text(encoding="utf-8")
    assert any(ln.strip() == MARK_START for ln in text.splitlines()) and any(
        ln.strip() == MARK_END for ln in text.splitlines()
    ), (
        f"DISCLOSURE.md kehilangan penanda {MARK_START}/{MARK_END}; tanpa itu tes ini "
        "tidak bisa tahu baris mana yang tabel dependency Python."
    )
    # Penanda hanya dikenali bila BERDIRI SENDIRI di satu baris: dokumen menjelaskan
    # penanda ini dalam prosa (di dalam backtick), dan split naif akan menangkap
    # penjelasannya, bukan tabelnya — blok kosong yang lolos diam-diam.
    lines = text.splitlines()
    try:
        first = next(i for i, ln in enumerate(lines) if ln.strip() == MARK_START)
        last = next(i for i, ln in enumerate(lines) if ln.strip() == MARK_END)
    except StopIteration:  # pragma: no cover - dijaga assert di atas
        raise AssertionError("penanda python-deps tidak berdiri sendiri di satu baris") from None
    block = "\n".join(lines[first + 1 : last])
    names: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        first = stripped.strip("|").split("|")[0].strip().strip("`* ")
        if not first or set(first) <= set("-: ") or first.lower() in {"paket", "package", "nama"}:
            continue
        names.append(first.lower())
    return names


def test_disclosure_lists_no_package_absent_from_the_lock() -> None:
    """Daftar yang memuat paket tidak terpasang sama menyesatkannya dengan yang kurang."""
    extra = sorted(set(_disclosed_python_packages()) - {p.lower() for p in _lock_packages()})
    assert extra == [], f"DISCLOSURE.md mendaftar paket yang tidak ada di uv.lock: {extra}."


def test_disclosure_block_covers_every_locked_package() -> None:
    """Penanda harus memuat SEMUA paket, bukan sekadar menyebutnya di prosa."""
    missing = sorted({p.lower() for p in _lock_packages()} - set(_disclosed_python_packages()))
    assert missing == [], f"paket ini terkunci di uv.lock tetapi tidak ada di tabel python-deps: {missing}."


def test_vendored_assets_are_not_claimed_before_they_exist() -> None:
    """ADR-014: three/gsap/lenis baru boleh disebut 'di-vendor' bila berkasnya ada.

    Temuan juri Fase 1: DISCLOSURE menyatakan aset sudah disalin ke
    `ui/static/vendor/` padahal direktori itu tidak ada.
    """
    if (ROOT / "ui" / "static" / "vendor").is_dir():
        pytest.skip("aset sudah di-vendor; klaim 'sudah' menjadi sah")
    text = _disclosure_text()
    for claim in ("disalin apa adanya ke `ui/static", "di-vendor ke `ui/static"):
        assert claim not in text, (
            f"DISCLOSURE.md mengklaim aset sudah ada di ui/static/vendor/ ({claim!r}), "
            "tetapi direktori itu tidak ada. Tulis sebagai 'direncanakan, belum di-vendor'."
        )
