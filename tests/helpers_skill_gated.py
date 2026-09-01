"""Skill palsu TASKS 1.3: memanggil jalur produksi (`RyoClient.from_env`) sehingga gate
ADR-005 di `core/sources.py` yang menentukan hasilnya. Dipakai `tests/test_skill_cli.py`."""

from __future__ import annotations

from core.ryo_client import RyoClient
from core.skill import SkillResult, register_skill


def gated(symbol: str) -> SkillResult:
    """Membangun klien live; bila sumber tidak approved, gate melempar sebelum ada request."""
    client = RyoClient.from_env()
    try:
        envelope = client.call("analyze_token", {"symbol": symbol})
    finally:
        client.close()
    return SkillResult.unavailable(
        "gated",
        request={"symbol": symbol},
        warnings=[f"envelope status: {envelope['status']}"],
    )


register_skill("gated", gated)
