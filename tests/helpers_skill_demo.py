"""Modul skill palsu untuk tes registry TASKS 1.3 (mendaftarkan dirinya saat diimpor)."""

from __future__ import annotations

from core.skill import SkillResult, register_skill


def demo(symbol: str) -> SkillResult:
    """Skill contoh: selalu jujur `unavailable` (tidak ada bukti sama sekali)."""
    return SkillResult.unavailable(
        "demo",
        request={"symbol": symbol},
        warnings=["demo skill: no evidence"],
    )


register_skill("demo", demo)
