"""Skill palsu TASKS 1.5(a): jalur produksi RYO tanpa retry, supaya kegagalan JARINGAN
sampai ke batas CLI dalam hitungan milidetik. Dipakai `tests/test_skill_cli.py`.

Beda dengan `helpers_skill_gated`: yang itu menguji gate ADR-005 (sumber belum approved),
yang ini menguji jalur setelah gate lolos tetapi host tidak menjawab — satu-satunya alasan
gagal yang berguna bagi pengguna ("host tidak terjangkau", "HTTP 429").
"""

from __future__ import annotations

from core.ryo_client import RyoClient
from core.skill import SkillResult, register_skill


def live(symbol: str) -> SkillResult:
    """Panggil RYO sungguhan; `max_retries=0` supaya tes tidak menunggu backoff."""
    client = RyoClient.from_env(max_retries=0)
    try:
        envelope = client.call("analyze_token", {"symbol": symbol})
    finally:
        client.close()
    return SkillResult.unavailable(
        "live",
        request={"symbol": symbol},
        warnings=[f"envelope status: {envelope['status']}"],
    )


register_skill("live", live)
