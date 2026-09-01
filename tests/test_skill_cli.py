"""Tes TASKS 1.3 — `core/skill_cli.py`: satu envelope JSON ke stdout, exit 0, deterministik.

AC yang dijaga di sini: `make skill ARGS="preflight SOL"` menghasilkan envelope
`unavailable` yang JUJUR (skill belum terdaftar — itu TASKS 2.2), bukan GO palsu dan bukan
traceback; dua run byte-identik; sumber yang tidak approved (ADR-005) menjadi envelope
`unavailable`, bukan exception yang lolos ke pengguna.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import skill as skill_module
from core import skill_cli
from core.ryo_client import ENV_KEY, ENV_URL
from core.skill import SkillResult, register_skill
from core.skill import _reset_registry as reset_registry
from core.sources import ENV_SOURCES_PATH
from core.store import canonical_json

ROOT = Path(__file__).resolve().parent.parent
# Host sengaja tidak bisa dirutekan: bila gate ADR-005 regresi, tes TIDAK boleh
# mengirim satu request pun ke API penyelenggara (Official Rules: "interfere/abuse").
BASE_URL = "https://127.0.0.1:1/"
FAKE_KEY = "ryo_mcp_TESTONLY_do_not_log"


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def run_cli(argv: list[str]) -> tuple[int, dict]:
    """Jalankan CLI di dalam proses; kembalikan (exit code, envelope terurai)."""
    out = io.StringIO()
    code = skill_cli.main(argv, out=out)
    text = out.getvalue()
    assert text.endswith("\n")
    return code, json.loads(text)


def run_subprocess(args: list[str], env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(ROOT), "LC_ALL": "C.UTF-8"}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "core.skill_cli", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=120,
    )


# --- AC 6: dua run byte-identik ----------------------------------------------------------
def test_two_runs_are_byte_identical() -> None:
    first = run_subprocess(["preflight", "SOL"])
    second = run_subprocess(["preflight", "SOL"])
    assert first.returncode == 0 and second.returncode == 0
    assert first.stdout == second.stdout
    assert first.stdout.strip() != b""


def test_stdout_is_canonical_json_only() -> None:
    proc = run_subprocess(["preflight", "SOL"])
    text = proc.stdout.decode("utf-8")
    assert text.endswith("\n")
    assert text[:-1] == canonical_json(json.loads(text))
    assert b"Traceback" not in proc.stderr


# --- AC 5 (bagian CLI) + kejujuran -------------------------------------------------------
def test_unregistered_skill_is_reported_not_faked() -> None:
    code, envelope = run_cli(["preflight", "SOL"])
    assert code == 0
    assert envelope["tool"] == "preflight"
    assert envelope["status"] == "unavailable"
    assert envelope["data"] is None
    assert envelope["as_of"] is None
    assert envelope["simulated"] is True
    assert envelope["warnings"] == ["skill 'preflight' not registered"]
    assert envelope["request"] == {"skill": "preflight", "args": ["SOL"]}
    assert "GO" not in json.dumps(envelope)


def test_missing_skill_name_is_reported() -> None:
    code, envelope = run_cli([])
    assert code == 0
    assert envelope["status"] == "unavailable"
    assert envelope["data"] is None
    assert any("usage" in w for w in envelope["warnings"])


def test_source_not_approved_becomes_unavailable_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-005: mematikan sumber tidak boleh menyebabkan exception di batas produk."""
    path = tmp_path / "sources.yaml"
    path.write_text(
        "sources:\n  ryo_tools:\n    enabled: true\n    approved: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(ENV_URL, BASE_URL)
    monkeypatch.setenv(ENV_KEY, FAKE_KEY)
    monkeypatch.setenv(ENV_SOURCES_PATH, str(path))
    monkeypatch.setitem(skill_module.SKILL_MODULES, "gated", "tests.helpers_skill_gated")

    code, envelope = run_cli(["gated", "SOL"])
    assert code == 0
    assert envelope["status"] == "unavailable"
    assert envelope["data"] is None
    assert envelope["tool"] == "gated"
    assert any("ryo_tools" in w for w in envelope["warnings"])
    assert FAKE_KEY not in json.dumps(envelope)


def test_subprocess_with_unapproved_registry_still_exits_zero(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text("sources:\n  ryo_tools:\n    enabled: true\n    approved: null\n", encoding="utf-8")
    proc = run_subprocess(["preflight", "SOL"], {ENV_SOURCES_PATH: str(path)})
    assert proc.returncode == 0
    envelope = json.loads(proc.stdout)
    assert envelope["status"] == "unavailable"
    assert b"Traceback" not in proc.stderr


# --- kegagalan skill ---------------------------------------------------------------------
def test_unexpected_exception_never_leaks_its_message() -> None:
    """Pesan exception bisa memuat apa saja (mis. kredensial) → hanya kelasnya yang dilaporkan."""

    def boom(symbol: str) -> SkillResult:
        raise RuntimeError(f"secret={FAKE_KEY} bocor")

    register_skill("boom", boom)
    code, envelope = run_cli(["boom", "SOL"])
    assert code == 0
    assert envelope["status"] == "unavailable"
    assert envelope["data"] is None
    text = json.dumps(envelope)
    assert FAKE_KEY not in text
    assert "RuntimeError" in text


def test_wrong_argument_count_is_reported() -> None:
    def needs_two(symbol: str, intent: str) -> SkillResult:  # pragma: no cover - dipanggil salah
        raise AssertionError

    register_skill("needs_two", needs_two)
    code, envelope = run_cli(["needs_two"])
    assert code == 0
    assert envelope["status"] == "unavailable"
    assert any("argument" in w for w in envelope["warnings"])


def test_skill_returning_wrong_type_is_rejected() -> None:
    def liar(symbol: str) -> SkillResult:
        return {"status": "ok", "decision": "GO"}  # type: ignore[return-value]

    register_skill("liar", liar)
    code, envelope = run_cli(["liar", "SOL"])
    assert code == 0
    assert envelope["status"] == "unavailable"
    assert "GO" not in json.dumps(envelope)


# --- jalur sukses (kerangka untuk TASKS 2.2) ---------------------------------------------
def test_registered_skill_output_is_passed_through() -> None:
    def echo(symbol: str) -> SkillResult:
        return SkillResult(
            tool="echo",
            status="ok",
            data_mode="simulated",
            as_of="2026-09-01T03:47:14Z",
            request={"symbol": symbol},
            data={"items": [{"id": "a", "status": "green", "value": 1}]},
            summary={"headline": "echo ok", "key_points": []},
            availability={"a": "available"},
        )

    register_skill("echo", echo)
    code, envelope = run_cli(["echo", "SOL"])
    assert code == 0
    assert envelope["status"] == "ok"
    assert envelope["as_of"] == "2026-09-01T03:47:14Z"
    assert envelope["data"]["items"][0]["id"] == "a"
    assert envelope["simulated"] is True


def test_lazy_module_registration_is_used_by_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """TASKS 2.2 mendaftarkan `preflight` lewat jalur ini; core tidak mengimpor preflight."""
    monkeypatch.setitem(skill_module.SKILL_MODULES, "lazy", "tests.helpers_skill_lazy")
    code, envelope = run_cli(["lazy", "SOL"])
    assert code == 0
    assert envelope["tool"] == "lazy"
    assert envelope["request"] == {"symbol": "SOL"}
    assert envelope["warnings"] == ["lazy skill: no evidence"]


# --- ronde perbaikan 1.3: envelope yang ditambal tidak boleh sampai ke stdout -------------
def _tampered(patch) -> SkillResult:
    """Envelope sah yang lalu ditambal isinya, persis pola yang akan dipakai TASKS 2.2."""
    result = SkillResult(
        tool="tamper",
        status="partial",
        data_mode="live",
        as_of="2026-09-01T03:47:14Z",
        request={"symbol": "SOL"},
        data={"items": [{"id": "rr", "status": "unspoken", "value": None, "reason": "null"}]},
        summary={"headline": "tamper", "key_points": []},
        availability={"rr": "unspoken"},
    )
    patch(result)
    return result


def _run_tampered(patch) -> dict:
    def skill(symbol: str) -> SkillResult:
        return _tampered(patch)

    register_skill("tamper", skill)
    code, envelope = run_cli(["tamper", "SOL"])
    assert code == 0
    return envelope


def test_value_patched_into_unspoken_item_is_caught_before_stdout() -> None:
    envelope = _run_tampered(lambda r: r.data["items"][0].__setitem__("value", 1.8))
    assert envelope["status"] == "unavailable"
    assert envelope["data"] is None
    assert "1.8" not in json.dumps(envelope)
    assert any("final validation" in w for w in envelope["warnings"])


def test_availability_flipped_after_validation_is_caught() -> None:
    envelope = _run_tampered(lambda r: r.availability.__setitem__("rr", "available"))
    assert envelope["status"] == "unavailable"
    assert envelope["data"] is None


def test_simulated_false_can_never_reach_stdout() -> None:
    """docs/rules-compliance.md: respons tidak boleh menyamar sebagai bukan simulasi."""
    envelope = _run_tampered(lambda r: object.__setattr__(r, "simulated", False))
    assert envelope["simulated"] is True
    assert envelope["status"] == "unavailable"


def test_model_construct_bypass_is_caught() -> None:
    def skill(symbol: str) -> SkillResult:
        return SkillResult.model_construct(
            tool="tamper",
            status="ok",
            data={"items": [{"id": "rr", "status": "unspoken", "value": 1.8}]},
            availability={"rr": "available"},
            simulated=False,
        )

    register_skill("tamper", skill)
    code, envelope = run_cli(["tamper", "SOL"])
    assert code == 0
    assert envelope["simulated"] is True
    assert envelope["status"] == "unavailable"
    assert envelope["data"] is None


def test_non_finite_number_never_becomes_null() -> None:
    def skill(symbol: str) -> SkillResult:
        result = _tampered(lambda r: None)
        result.data["plan"] = {"stop": float("nan")}
        return result

    register_skill("tamper", skill)
    code, envelope = run_cli(["tamper", "SOL"])
    assert code == 0
    assert envelope["status"] == "unavailable"
    assert envelope["data"] is None


# --- argv yang bukan UTF-8 ---------------------------------------------------------------
def test_non_utf8_argument_still_yields_one_json_document() -> None:
    """`python -m core.skill_cli $'\\xff' SOL` — chaos test 4.5: nol traceback, exit 0."""
    proc = subprocess.run(
        [sys.executable, "-m", "core.skill_cli", b"\xff", b"SOL"],
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", "HOME": str(ROOT), "PYTHONIOENCODING": "utf-8:strict"},
        capture_output=True,
        timeout=120,
    )
    assert proc.returncode == 0
    assert b"Traceback" not in proc.stderr
    text = proc.stdout.decode("utf-8")  # harus UTF-8 sah, kalau tidak ini melempar
    envelope = json.loads(text)
    assert envelope["status"] == "unavailable"
    assert envelope["data"] is None


def test_non_utf8_argument_output_is_deterministic() -> None:
    def once() -> bytes:
        return subprocess.run(
            [sys.executable, "-m", "core.skill_cli", b"\xff", b"SOL"],
            cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", "HOME": str(ROOT)},
            capture_output=True,
            timeout=120,
        ).stdout

    assert once() == once()


def test_run_itself_returns_a_revalidated_copy() -> None:
    """Bukan hanya stdout: `run()` pun tidak boleh mengembalikan envelope yang ditambal."""
    holder: list[SkillResult] = []

    def skill(symbol: str) -> SkillResult:
        result = _tampered(lambda r: None)
        holder.append(result)
        return result

    register_skill("tamper", skill)
    returned = skill_cli.run(["tamper", "SOL"])
    assert returned is not holder[0]
    assert returned.status == "partial"
    assert returned.data["items"][0]["value"] is None

    # penambalan SETELAH validasi tidak ikut terbawa: yang dikembalikan adalah salinan
    holder[0].data["items"][0]["value"] = 1.8
    assert returned.data["items"][0]["value"] is None
