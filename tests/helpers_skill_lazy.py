"""Skill palsu TASKS 1.3 untuk membuktikan impor malas `SKILL_MODULES` di CLI.

Modul terpisah dari `helpers_skill_demo` dengan sengaja: registrasi terjadi saat modul
diimpor PERTAMA kali, sehingga dua tes yang memakai modul yang sama akan saling merusak
setelah registry dikosongkan.
"""

from __future__ import annotations

from core.skill import SkillResult, register_skill


def lazy(symbol: str) -> SkillResult:
    """Skill contoh: tidak punya bukti apa pun, jadi jujur `unavailable`."""
    return SkillResult.unavailable(
        "lazy",
        request={"symbol": symbol},
        warnings=["lazy skill: no evidence"],
    )


register_skill("lazy", lazy)
