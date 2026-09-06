"""Tes TASKS 1.3 — `core/skill_cli.py`: satu envelope JSON ke stdout, exit 0, deterministik.

AC yang dijaga di sini adalah KONTRAK CLI-nya: nama skill yang tidak terdaftar menghasilkan
envelope `unavailable` yang JUJUR, bukan GO palsu dan bukan traceback; dua run byte-identik;
sumber yang tidak approved (ADR-005) menjadi envelope `unavailable`, bukan exception yang
lolos ke pengguna. Skill nyata `preflight` (TASKS 2.2) diuji end-to-end di
`tests/test_preflight_skill.py` — lihat :data:`ABSENT`.
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
from core.ryo_client import ENV_KEY, ENV_URL, RyoError, RyoHTTPError, RyoNetworkError
from core.skill import SkillError, SkillResult, register_skill
from core.skill import _reset_registry as reset_registry
from core.sources import ENV_SOURCES_PATH, SourcesError
from core.store import canonical_json

ROOT = Path(__file__).resolve().parent.parent
# Host sengaja tidak bisa dirutekan: bila gate ADR-005 regresi, tes TIDAK boleh
# mengirim satu request pun ke API penyelenggara (Official Rules: "interfere/abuse").
BASE_URL = "https://127.0.0.1:1/"
FAKE_KEY = "ryo_mcp_TESTONLY_do_not_log"
# Nama skill yang TIDAK ada di registry maupun `SKILL_MODULES`. Sejak TASKS 2.2 `preflight`
# benar-benar terdaftar dan membaca `data/ryo.db`, jadi memakai namanya di berkas ini akan
# menjadikan kontrak CLI bergantung pada isi DB mesin yang menjalankan tes (dan pada apakah
# perekam menulis di antara dua subprocess). Yang diuji di sini murni CLI-nya.
ABSENT = "no_such_skill"


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
    first = run_subprocess([ABSENT, "SOL"])
    second = run_subprocess([ABSENT, "SOL"])
    assert first.returncode == 0 and second.returncode == 0
    assert first.stdout == second.stdout
    assert first.stdout.strip() != b""


def test_stdout_is_canonical_json_only() -> None:
    proc = run_subprocess([ABSENT, "SOL"])
    text = proc.stdout.decode("utf-8")
    assert text.endswith("\n")
    assert text[:-1] == canonical_json(json.loads(text))
    assert b"Traceback" not in proc.stderr


def test_bare_process_finds_a_registered_skill_through_the_module_map(tmp_path: Path) -> None:
    """`core.skill.SKILL_MODULES` harus benar-benar menghubungkan CLI polos ke skill nyata.

    Tanpa entri peta itu, `make skill ARGS="preflight SOL"` di proses yang belum mengimpor
    paket pemiliknya hanya menjawab "not registered" (envelope sah, tetapi bukan skill-nya).
    `DB_PATH` sengaja menunjuk berkas yang tidak ada: yang dipatok di sini adalah
    PENYAMBUNGANNYA — request dirakit oleh skill, bukan oleh CLI — bukan isi keputusannya,
    dan tes ini tidak boleh menyentuh `data/ryo.db` mesin siapa pun.
    """
    proc = run_subprocess(["preflight", "SOL"], {"DB_PATH": str(tmp_path / "absent.db")})
    assert proc.returncode == 0
    assert b"Traceback" not in proc.stderr
    envelope = json.loads(proc.stdout)
    assert envelope["tool"] == "preflight"
    assert not any("not registered" in warning for warning in envelope["warnings"])
    assert "symbol" in envelope["request"]  # {skill, args} = request rakitan CLI
    assert envelope["simulated"] is True


# --- AC 5 (bagian CLI) + kejujuran -------------------------------------------------------
def test_unregistered_skill_is_reported_not_faked() -> None:
    code, envelope = run_cli([ABSENT, "SOL"])
    assert code == 0
    assert envelope["tool"] == ABSENT
    assert envelope["status"] == "unavailable"
    assert envelope["data"] is None
    assert envelope["as_of"] is None
    assert envelope["simulated"] is True
    assert envelope["warnings"] == [f"skill {ABSENT!r} not registered"]
    assert envelope["request"] == {"skill": ABSENT, "args": ["SOL"]}
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
    proc = run_subprocess([ABSENT, "SOL"], {ENV_SOURCES_PATH: str(path)})
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


# --- TASKS 1.5(a): sebab kegagalan RYO adalah pesan KAMI ----------------------------------
def test_ryo_failure_reason_reaches_the_user() -> None:
    """`RyoError` masuk allowlist: pesannya kita tulis dan sudah lewat redaksi klien.

    Tanpa ini pengguna hanya melihat "failed: RyoNetworkError" dan kehilangan satu-satunya
    alasan gagal yang bisa ditindaklanjuti (host mati, 429, kredensial belum diset).
    """

    def dead(symbol: str) -> SkillResult:
        raise RyoNetworkError("analyze_token: ConnectError: [Errno 111] Connection refused")

    register_skill("dead", dead)
    code, envelope = run_cli(["dead", "SOL"])
    assert code == 0
    assert envelope["status"] == "unavailable"
    assert envelope["data"] is None
    assert any("Connection refused" in warning for warning in envelope["warnings"])


def test_ryo_http_failure_reason_reaches_the_user() -> None:
    """429 adalah informasi operasional, bukan sekadar nama kelas."""

    def throttled(symbol: str) -> SkillResult:
        raise RyoHTTPError("analyze_token: HTTP 429 code=RATE_LIMITED", status_code=429, code="RATE_LIMITED")

    register_skill("throttled", throttled)
    code, envelope = run_cli(["throttled", "SOL"])
    assert code == 0
    assert any("HTTP 429" in warning for warning in envelope["warnings"])


def test_unreachable_host_is_reported_as_a_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC 1.5(1): server uji mati → warnings memuat sebab jaringan, exit 0, satu baris JSON."""
    monkeypatch.setenv(ENV_URL, BASE_URL)
    monkeypatch.setenv(ENV_KEY, FAKE_KEY)
    monkeypatch.delenv(ENV_SOURCES_PATH, raising=False)
    monkeypatch.setitem(skill_module.SKILL_MODULES, "live", "tests.helpers_skill_live")

    code, envelope = run_cli(["live", "SOL"])
    assert code == 0
    assert envelope["status"] == "unavailable"
    assert envelope["tool"] == "live"
    joined = " ".join(envelope["warnings"])
    assert "analyze_token" in joined
    assert "127.0.0.1:1" in joined or "Connect" in joined or "connect" in joined
    assert FAKE_KEY not in json.dumps(envelope)


def test_third_party_exception_message_is_still_hidden() -> None:
    """Allowlist tetap SEMPIT: yang bukan RyoError/SkillError/SourcesError hanya kelasnya.

    SDK pihak ketiga (mis. LLM di TASKS 2.3) tidak punya mesin redaksi seperti
    `core/ryo_client.py`; melonggarkan allowlist menjadi `Exception` membuka jalur bocor.
    """

    class VendorError(Exception):
        pass

    def vendor(symbol: str) -> SkillResult:
        raise VendorError(f"auth failed for {FAKE_KEY}")

    register_skill("vendor", vendor)
    code, envelope = run_cli(["vendor", "SOL"])
    assert code == 0
    assert FAKE_KEY not in json.dumps(envelope)
    assert any("VendorError" in warning for warning in envelope["warnings"])


# --- TASKS 1.5(c): argumen raksasa / bermuatan kontrol ------------------------------------
def test_huge_argument_does_not_become_a_huge_envelope() -> None:
    """AC 1.5(3): argumen 100 kB → envelope < 4 kB, satu baris, exit 0."""
    proc = run_subprocess(["a" * 100_000])
    assert proc.returncode == 0
    assert b"Traceback" not in proc.stderr
    assert proc.stdout.count(b"\n") == 1
    assert len(proc.stdout) < 4096
    envelope = json.loads(proc.stdout)
    assert envelope["status"] == "unavailable"
    assert envelope["data"] is None
    assert len(envelope["tool"]) < 200
    assert "truncated" in envelope["tool"]


def test_many_arguments_do_not_become_a_huge_envelope() -> None:
    """Banyak argumen pendek juga dibatasi; jumlah aslinya tetap disebut, tidak disembunyikan."""
    code, envelope = run_cli(["nosuch", *[f"arg{i}" for i in range(500)]])
    assert code == 0
    assert len(envelope["request"]["args"]) <= 8
    assert envelope["request"]["args_total"] == 500
    assert len(canonical_json(envelope)) < 4096


def test_control_characters_in_skill_name_never_reach_stdout_or_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 1.5(3): kontrol/ANSI/RTL/lone surrogate di nama skill tidak muncul mentah.

    Nama diarahkan ke modul yang tidak ada supaya jalur yang MENULIS LOG ikut teruji:
    `scripts/daemons.sh` mengarahkan stderr ke `logs/*.log`, jadi baris log palsu dan
    escape ANSI di sana sama berbahayanya dengan di stdout.
    """
    name = "pre\x1b[2Jflight\r\n‮SOL\ud800"
    monkeypatch.setitem(skill_module.SKILL_MODULES, name, "tests.helpers_skill_missing")
    with caplog.at_level("DEBUG", logger="core.skill_cli"):
        code, envelope = run_cli([name])
    assert code == 0
    text = canonical_json(envelope)
    logged = " ".join(record.getMessage() for record in caplog.records)
    for raw in ("\x1b", "\r", "\n", "‮"):
        assert raw not in text
        assert raw not in logged
    assert "\\u001b" in envelope["tool"]
    assert "\\u202e" in envelope["tool"]


def test_huge_skill_name_does_not_reach_the_load_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pesan `SkillError` dari `load_skill` memuat nama skill; namanya harus sudah dipotong."""
    name = "b" * 100_000
    monkeypatch.setitem(skill_module.SKILL_MODULES, name, "tests.helpers_skill_missing")
    code, envelope = run_cli([name])
    assert code == 0
    assert len(canonical_json(envelope)) < 4096


# --- kontrak allowlist pesan (TASKS 1.5, catatan tinjauan keamanan) -----------------------
def test_speakable_errors_is_an_explicit_closed_list() -> None:
    """Menambah kelas ke `SPEAKABLE_ERRORS` = memberi hak cetak-ke-stdout kepadanya.

    Daftar ini dikunci di tes supaya penambahan berikutnya adalah keputusan SADAR, bukan
    efek samping. Syarat masuk: pesannya dibentuk oleh kode kita DAN melewati mesin redaksi
    (untuk `RyoError`: `BaseRyoClient._safe` di kelima jalur teks server). Kelas tanpa
    jaminan itu — mis. SDK LLM di TASKS 2.3 — hanya boleh dilaporkan nama kelasnya.
    """
    assert skill_cli.SPEAKABLE_ERRORS == (SkillError, SourcesError, RyoError)


def test_envelope_from_a_skill_is_bounded_even_when_the_skill_is_careless() -> None:
    """Batas hidup di MODEL: skill 2.2 merakit envelope sendiri dan melewati batas CLI.

    Pola di bawah persis `tests/helpers_skill_live.py` (argumen mentah ke `request` dan
    `warnings`); sebelum pembatasan model, ini menghasilkan envelope 300.289 byte.
    """
    payload = "z" * 100_000

    def careless(symbol: str) -> SkillResult:
        return SkillResult.unavailable(
            "careless",
            request={"symbol": symbol},
            warnings=[f"gagal untuk {symbol}"],
            availability={symbol: "unspoken"},
        )

    register_skill("careless", careless)
    code, envelope = run_cli(["careless", payload])
    assert code == 0
    assert len(canonical_json(envelope)) < 4096
    assert payload not in canonical_json(envelope)


def test_rtl_override_from_a_skill_never_reaches_stdout() -> None:
    """`canonical_json` memakai `ensure_ascii=False`; U+202E bukan kontrol JSON.

    `json.dumps` meng-escape ESC/CR/LF tetapi TIDAK U+202E, jadi satu-satunya yang menutup
    jalur ini adalah `clean_text` di model.
    """

    def flipped(symbol: str) -> SkillResult:
        return SkillResult.unavailable("flipped", request={"symbol": symbol}, warnings=[symbol])

    register_skill("flipped", flipped)
    code, envelope = run_cli(["flipped", "GO‮OG"])
    assert code == 0
    assert "‮" not in canonical_json(envelope)
