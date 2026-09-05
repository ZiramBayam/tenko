"""Dua rupa kegagalan yang BERBEDA (TASKS 2.2c) — dan kenapa keduanya harus dipisah.

Spec §7 langkah 2 menuntut item 02 **MERAH**. Merah hanya lahir bila SATU tool wajib menjawab
`unavailable` sementara yang lain menjawab: panggilan yang BOLONG. Bila kelimanya padam, tidak
ada yang bisa disebut sama sekali — envelope `unavailable`, `data=None`, item 02 `unspoken`.
Menyamakan keduanya berarti mengubur perbedaan yang justru dijual produk ini (ADR-013), jadi
dua AC-nya dipaku di dua tes yang namanya menyebut bedanya:

* :func:`test_one_required_tool_unavailable_makes_item_two_red_and_the_envelope_partial`
* :func:`test_every_required_tool_unavailable_leaves_item_two_unspoken_and_data_null`

Sisa tes di berkas ini menjaga bahwa fixturenya sendiri jujur: envelope yang MENJAWAB adalah
salinan apa adanya dari bukti nyata (TASKS 3.4a), envelope yang padam mengaku SINTETIS (RYO
belum pernah mengeluarkan `status: unavailable` — `docs/api-facts.md` 0/3645, `id <= 3645`
per 6 Sep 2026), fixture tidak
bisa masuk ke store bukti (penjaga TASKS 0.19), dan `make demo` benar-benar memakai fixture (1)
untuk langkah 2 — bukan fixture (2), yang memperagakan kalimat lain.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.skill import SkillResult
from core.store import sha256_hex
from preflight.outage_fixture import (
    ALL_TOOLS_FIXTURE_PATH,
    DOWN_TOOL,
    MODES,
    ONE_TOOL_FIXTURE_PATH,
    SOURCE_FIXTURE_PATH,
    SOURCE_HASH_KEY,
    SOURCE_IDS,
    OutageFixtureError,
    build,
    confession_lines,
    is_shaped,
    main,
    materialize,
    observed,
    outage_envelope,
    run,
    verified_confession,
    verify_provenance,
)
from preflight.params import ITEM_COMPLETENESS, ITEM_DERIVATIVES, ITEM_SENTIMENT_SHIFT, REQUIRED_TOOLS

ROOT = Path(__file__).resolve().parent.parent

#: Kesepuluh kunci envelope RYO (`docs/api-facts.md`, "Tipe & kosakata envelope").
ENVELOPE_KEYS = {
    "schema_version",
    "tool",
    "status",
    "data_mode",
    "as_of",
    "request",
    "data",
    "summary",
    "availability",
    "warnings",
}


def _fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _without_recorded(fixture: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fixture.items() if key != "recorded"}


def _items(result: SkillResult) -> dict[str, str]:
    return {str(item["id"]): str(item["status"]) for item in (result.data or {}).get("items", [])}


# --- AC 1 & 2 ----------------------------------------------------------------------------


def test_one_required_tool_unavailable_makes_item_two_red_and_the_envelope_partial() -> None:
    """AC 1: item 02 merah, NO_GO ber-`blocking` item 02, `status="partial"`, `data` bukan None."""
    result = run(ONE_TOOL_FIXTURE_PATH)
    data = result.data or {}

    assert result.status == "partial"
    assert result.data is not None
    assert data["decision"] == "NO_GO"
    assert ITEM_COMPLETENESS in data["blocking"]

    items = _items(result)
    assert items[ITEM_COMPLETENESS] == "red"
    # Yang membuatnya merah harus DISEBUT, bukan disimpulkan: nama toolnya ada di kartu.
    reason = next(item["reason"] for item in data["items"] if item["id"] == ITEM_COMPLETENESS)
    assert DOWN_TOOL in reason
    assert "status=unavailable" in reason
    source = next(item["source"] for item in data["items"] if item["id"] == ITEM_COMPLETENESS)
    assert source["tool"] == DOWN_TOOL

    # Empat tool lain benar-benar menjawab — kalau tidak, ini bukan "panggilan yang bolong".
    reads = data["store_reads"]
    assert reads["n_tools_answered"] == len(REQUIRED_TOOLS) - 1
    assert reads["tools"][DOWN_TOOL]["status"] == "unavailable"
    assert sum(1 for status in items.values() if status == "green") == 7

    # Item yang kehilangan sumbernya `unspoken`, BUKAN 0: satu wajib (ikut menahan), satu
    # opsional (tidak ikut menahan) — itu bedanya, dan ia dipaku di sini.
    assert items[ITEM_SENTIMENT_SHIFT] == "unspoken"
    assert items[ITEM_DERIVATIVES] == "unspoken"
    assert ITEM_SENTIMENT_SHIFT in data["blocking"]
    assert ITEM_DERIVATIVES not in data["blocking"]
    for item in data["items"]:
        if item["status"] == "unspoken":
            assert item["value"] is None


def test_every_required_tool_unavailable_leaves_item_two_unspoken_and_data_null() -> None:
    """AC 2: `status="unavailable"`, `data=None`, item 02 `unspoken`, tanpa `SkillError`.

    Tidak ada `pytest.raises` di sini dengan sengaja: melempar ADALAH kegagalannya. Bukti yang
    tidak menjawab harus menghasilkan penolakan yang bisa dibaca, bukan traceback (spec §7).
    """
    result = run(ALL_TOOLS_FIXTURE_PATH)

    assert isinstance(result, SkillResult)
    assert result.status == "unavailable"
    assert result.data is None
    assert result.as_of is None

    # `data` wajib None, jadi kesepuluh baris panggilan hanya bisa disebut lewat `availability`.
    assert result.availability[ITEM_COMPLETENESS] == "unspoken"
    assert set(result.availability.values()) == {"unspoken"}
    assert len(result.availability) == 10

    assert result.summary["decision"] == "NO_GO"
    assert ITEM_COMPLETENESS in result.summary["blocking"]
    assert any("no required tool answered" in warning for warning in result.warnings)


def test_the_two_failure_fixtures_do_not_say_the_same_thing() -> None:
    """Inti TASKS 2.2c: "tidak lengkap" dan "tidak terjawab sama sekali" adalah dua kalimat."""
    partial = run(ONE_TOOL_FIXTURE_PATH)
    nothing = run(ALL_TOOLS_FIXTURE_PATH)

    assert (partial.status, partial.data is None) == ("partial", False)
    assert (nothing.status, nothing.data is None) == ("unavailable", True)
    assert _items(partial)[ITEM_COMPLETENESS] == "red"
    assert nothing.availability[ITEM_COMPLETENESS] == "unspoken"
    # Keduanya NO_GO — yang berbeda bukan keputusannya melainkan APA yang bisa diucapkan.
    assert partial.summary["decision"] == nothing.summary["decision"] == "NO_GO"


# --- fixturenya sendiri harus jujur ------------------------------------------------------


@pytest.mark.parametrize("mode", sorted(MODES))
def test_the_committed_fixture_is_exactly_what_the_builder_derives(mode: str) -> None:
    """Fixture tidak boleh hasil ketikan tangan: ia harus bisa dilahirkan ulang dari sumbernya."""
    path = ONE_TOOL_FIXTURE_PATH if mode == "one" else ALL_TOOLS_FIXTURE_PATH
    committed = _fixture(path)
    derived = build(mode)
    # `recorded` diisi dari run yang benar-benar terjadi (tes berikutnya), jadi ia dikecualikan
    # di sini — sisanya WAJIB sama persis, termasuk setiap envelope.
    assert _without_recorded(committed) == _without_recorded(derived)


@pytest.mark.parametrize("mode", sorted(MODES))
def test_the_recorded_block_matches_what_running_the_fixture_produces_today(mode: str) -> None:
    """`recorded` adalah yang TERAMATI, bukan yang diharapkan: kalau bergeser, tes ini merah."""
    path = ONE_TOOL_FIXTURE_PATH if mode == "one" else ALL_TOOLS_FIXTURE_PATH
    committed = _fixture(path)
    assert committed["recorded"]["SOL"] == observed(run(path))


def test_every_answering_envelope_is_copied_verbatim_from_real_evidence() -> None:
    """Yang disintesis HANYA envelope yang padam; sisanya bukti RYO apa adanya (TASKS 3.4a)."""
    source = {row["source_id"]: row for row in _fixture(SOURCE_FIXTURE_PATH)["snapshots"]}
    fixture = _fixture(ONE_TOOL_FIXTURE_PATH)
    assert [row["source_id"] for row in fixture["snapshots"]] == list(SOURCE_IDS)
    rewritten = []
    for row in fixture["snapshots"]:
        original = source[row["source_id"]]
        # `args` dan `ts_utc` TIDAK PERNAH disentuh: keduanya menentukan baris mana yang dipilih
        # `latest_fresh` dan seberapa tua ia terbaca.
        assert row["args"] == original["args"]
        assert row["ts_utc"] == original["ts_utc"]
        if row["synthetic"]:
            rewritten.append(row["tool"])
        else:
            assert row["envelope"] == original["envelope"]
    assert rewritten == [DOWN_TOOL]
    assert fixture["params_hash"] == _fixture(SOURCE_FIXTURE_PATH)["params_hash"]


def test_every_answering_row_carries_the_hash_of_the_row_it_was_copied_from() -> None:
    """ "Salinan apa adanya" harus bisa DIPERIKSA, bukan sekadar diklaim berkasnya sendiri.

    Tanpa `source_payload_hash`, satu angka yang diketik ulang di dalam envelope nyata (mis.
    `risk_reward`) lolos tanpa sepatah kata: `status` dan `warnings`-nya tetap sah. Hash-nya
    direkam saat PENYALINAN (`agent.demo_fixture.capture` membaca kolom `snapshots.payload_hash`),
    jadi memeriksanya tidak perlu membuka `data/` lagi.
    """
    for path in (SOURCE_FIXTURE_PATH, ONE_TOOL_FIXTURE_PATH):
        fixture = _fixture(path)
        report = verify_provenance(fixture)
        assert not report.mismatched, path
        assert not report.unverified, f"{path}: baris yang menjawab tanpa {SOURCE_HASH_KEY}"
        assert report.verified
        for row in fixture["snapshots"]:
            if row["source_id"] in report.verified:
                assert sha256_hex(row["envelope"]) == row[SOURCE_HASH_KEY]
            else:
                # Baris yang DIBENTUK tidak boleh membawa hash: ia bukan salinan apa pun, dan
                # hash yang tertinggal akan menjadi klaim provenans atas envelope karangan.
                assert SOURCE_HASH_KEY not in row


def test_the_command_refuses_to_print_an_envelope_whose_content_drifted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """TINGGI: bentuk envelope yang rapi tidak menjamin ISInya — dan CLI ini tidak boleh diam.

    Sebelumnya hanya `status`/`warnings` yang diaudit, jadi mengedit `risk_reward` pada envelope
    NYATA menggeser item 09 menjadi merah dan `decision_hash` ikut bergeser, sementara stdout tetap
    keluar rc=0 dan stderr tetap menyebutnya "salinan apa adanya bukti nyata".
    """
    fixture = _fixture(ONE_TOOL_FIXTURE_PATH)
    for row in fixture["snapshots"]:
        if row["source_id"] == 2495:
            row["envelope"]["data"]["risk_reward"] = [0.4, 0.8]
    path = tmp_path / "isi-bergeser.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")

    assert main(["--fixture", str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert SOURCE_HASH_KEY in captured.err
    assert "source_id 2495" in captured.err


def test_a_fixture_without_recorded_hashes_is_downgraded_not_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Yang tidak bisa diverifikasi diakui apa adanya: berkas MENGAKU menyalin, bukan "terbukti"."""
    fixture = _fixture(ONE_TOOL_FIXTURE_PATH)
    for row in fixture["snapshots"]:
        row.pop(SOURCE_HASH_KEY, None)
    path = tmp_path / "tanpa-hash.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")

    assert main(["--fixture", str(path)]) == 0
    captured = capsys.readouterr()
    err = captured.err
    assert "TIDAK DIVERIFIKASI" in err
    assert "MENGAKU menyalinnya" in err
    assert "DIVERIFIKASI salinan apa adanya" not in err
    assert json.loads(captured.out)["status"] == "partial"


def test_one_predicate_decides_what_counts_as_shaped(tmp_path: Path) -> None:
    """SEDANG: berkas ber-warning `SYNTHETIC` + `status: ok` tidak boleh lolos di sini saja.

    `agent/demo.py` sudah memvonisnya KETIDAKCOCOKAN; fungsi ini dulu hanya menguji `status`,
    sehingga berkas yang SAMA keluar rc=0 dari CLI ini dan disebut salinan bukti nyata.
    """
    assert is_shaped({"status": "ok", "warnings": ["SYNTHETIC: shaped by a test"]})
    assert is_shaped({"status": "unavailable", "warnings": []})
    assert not is_shaped({"status": "ok", "warnings": []})
    assert not is_shaped(None)

    fixture = _fixture(ONE_TOOL_FIXTURE_PATH)
    for row in fixture["snapshots"]:
        if row.get("synthetic"):
            row["synthetic"] = False
            row["envelope"]["status"] = "ok"
    fixture["synthetic"]["envelopes_rewritten_to_unavailable"] = []
    with pytest.raises(OutageFixtureError, match="TIDAK mengaku dibentuk"):
        verified_confession(fixture)


def test_the_synthetic_envelopes_admit_that_ryo_has_never_sent_one() -> None:
    """`status: unavailable` belum pernah teramati (api-facts 0/3645) — fixture harus mengakuinya."""
    for path in (ONE_TOOL_FIXTURE_PATH, ALL_TOOLS_FIXTURE_PATH):
        fixture = _fixture(path)
        note = fixture["synthetic"]
        assert note["task"] == "TASKS 2.2c"
        assert "never been observed" in note["why"]
        assert note["envelopes_rewritten_to_unavailable"]
        for row in fixture["snapshots"]:
            envelope = row["envelope"]
            if not row["synthetic"]:
                continue
            assert set(envelope) == ENVELOPE_KEYS, "envelope sintetis harus berbentuk envelope RYO"
            assert envelope["status"] == "unavailable"
            assert envelope["data"] is None
            assert envelope["data_mode"] == "unknown"
            assert any("SYNTHETIC" in warning for warning in envelope["warnings"])
            assert set(envelope["availability"].values()) == {"unavailable"}
    assert [row["tool"] for row in _fixture(ALL_TOOLS_FIXTURE_PATH)["snapshots"]] == [
        "market_overview",
        DOWN_TOOL,
        "analyze_token",
        "deep_analysis",
        "compare_tokens",
    ]


def test_an_outage_envelope_keeps_the_section_names_of_the_tool_it_replaces() -> None:
    """Nama seksi `availability` milik tool, bukan karangan: yang berubah hanya NILAInya."""
    original = {
        "schema_version": "1.0",
        "tool": "market_overview",
        "status": "ok",
        "data_mode": "live",
        "as_of": "2026-09-05T02:45:02Z",
        "request": {"top_n": 10},
        "data": {"regime": "neutral"},
        "summary": {"headline": "x", "key_points": []},
        "availability": {"market_regime": "available", "market_breadth": "available"},
        "warnings": [],
    }
    padam = outage_envelope(original)
    assert padam["availability"] == {"market_breadth": "unavailable", "market_regime": "unavailable"}
    assert padam["request"] == original["request"]
    assert padam["as_of"] == original["as_of"], "as_of dipertahankan: ts_utc dan as_of harus rukun"
    assert padam["data"] is None
    with pytest.raises(OutageFixtureError):
        outage_envelope({"status": "ok"})


def test_the_fixture_cannot_be_written_into_the_evidence_store() -> None:
    """Penjaga TASKS 0.19 berlaku sama untuk fixture ini — dan ia tidak meninggalkan DB kosong."""
    target = ROOT / "data" / "outage-fixture-must-not-land-here.db"
    assert not target.exists()
    with pytest.raises(OutageFixtureError) as excinfo:
        materialize(_fixture(ONE_TOOL_FIXTURE_PATH), target)
    assert "menolak menulis fixture" in str(excinfo.value)
    assert not target.exists(), "DB kosong berskema penuh tidak boleh tertinggal di data/"


def test_two_runs_over_the_same_fixture_produce_the_same_decision_hash() -> None:
    """Keputusan = fungsi murni (items, params): dua run atas bukti beku yang sama = hash sama."""
    first = run(ONE_TOOL_FIXTURE_PATH)
    second = run(ONE_TOOL_FIXTURE_PATH)
    assert (first.data or {})["decision_hash"] == (second.data or {})["decision_hash"]
    assert (first.data or {})["blocking"] == (second.data or {})["blocking"]
    assert _items(first) == _items(second)


@pytest.mark.parametrize("mode", sorted(MODES))
def test_the_module_runs_as_a_command_and_exits_zero_for_both_fixtures(
    mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC 1 & 2 menuntut exit 0: bukti yang padam bukan kegagalan program."""
    assert main(["--mode", mode]) == 0
    envelope = json.loads(capsys.readouterr().out.strip())
    assert envelope["tool"] == "preflight"
    assert envelope["simulated"] is True
    assert envelope["status"] == ("partial" if mode == "one" else "unavailable")


@pytest.mark.parametrize("mode", sorted(MODES))
def test_the_command_confesses_on_stderr_while_stdout_stays_the_canonical_envelope(
    mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Byte di stdout sama dengan `make skill` — jadi pengakuannya harus ada, tapi bukan di sana.

    `preflight` tidak meneruskan `warnings` envelope padam, jadi envelope hasil tidak memuat
    satu kata pun tentang asal-usul padamnya (`agent/demo.py` sudah menutup itu untuk jalur
    demo; CLI ini punya lubang yang sama). Yang dituntut: stderr mengaku dengan kalimat milik
    berkas fixture sendiri, stdout tetap satu baris JSON yang bisa di-pipe.
    """
    assert main(["--mode", mode]) == 0
    captured = capsys.readouterr()
    envelope = json.loads(captured.out.strip())
    assert captured.out.count("\n") == 1
    assert "PENGAKUAN" not in captured.out and "SINTETIS" not in captured.out
    err = captured.err
    assert "PENGAKUAN" in err
    assert "envelope PADAM di bawah SINTETIS" in err
    assert "TASKS 2.2c" in err
    assert "0/3645 (id <= 3645, 6 Sep 2026" in err
    assert f"{DOWN_TOOL} (source_id 2492)" in err
    assert envelope["status"] == ("partial" if mode == "one" else "unavailable")


def test_the_command_refuses_to_print_an_outage_that_does_not_confess(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Envelope `unavailable` tanpa pengakuan = klaim yang tidak bisa diperiksa: exit 2, stdout kosong."""
    fixture = _fixture(ONE_TOOL_FIXTURE_PATH)
    fixture.pop("synthetic")
    for row in fixture["snapshots"]:
        row["synthetic"] = False
    path = tmp_path / "diam-diam.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")

    assert main(["--fixture", str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "TIDAK mengaku dibentuk" in captured.err
    assert f"{DOWN_TOOL} (source_id 2492)" in captured.err


def test_the_confession_must_match_the_snapshots_it_claims_to_describe(tmp_path: Path) -> None:
    """Blok `synthetic` yang menyebut lebih sedikit dari yang ditulis ulang juga ditolak."""
    fixture = _fixture(ALL_TOOLS_FIXTURE_PATH)
    assert len(fixture["synthetic"]["envelopes_rewritten_to_unavailable"]) == len(SOURCE_IDS)
    fixture["synthetic"]["envelopes_rewritten_to_unavailable"] = ["market_overview (source_id 2491)"]
    with pytest.raises(OutageFixtureError, match="menandai"):
        verified_confession(fixture)

    honest = _fixture(ONE_TOOL_FIXTURE_PATH)
    honest["synthetic"]["why"] = "   "
    with pytest.raises(OutageFixtureError, match="ALASAN"):
        verified_confession(honest)

    # Fixture tanpa satu pun baris sintetis sah: pengakuannya kosong, bukan karangan.
    plain = _fixture(SOURCE_FIXTURE_PATH)
    assert verified_confession(plain) == {}
    assert "tidak menyatakan satu pun envelope sintetis" in "\n".join(
        confession_lines(plain, SOURCE_FIXTURE_PATH)
    )


def test_make_demo_uses_the_one_tool_fixture_for_step_two() -> None:
    """AC 4: langkah 2 memakai fixture (1) SECARA BAWAAN, bukan hanya bila dilewatkan tangan."""
    body = (ROOT / "scripts" / "demo.sh").read_text(encoding="utf-8")
    # Komentar dibuang: yang mengikat adalah baris yang BERJALAN, bukan yang menjelaskan.
    script = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    assert f'--step2-fixture "{ONE_TOOL_FIXTURE_PATH.relative_to(ROOT)}"' in script
    assert "--step2-fixture" in script
    # Fixture (2) TIDAK boleh dipakai demo: ia memperagakan kalimat lain (item 02 `unspoken`).
    assert str(ALL_TOOLS_FIXTURE_PATH.relative_to(ROOT)) not in script
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "scripts/demo.sh" in makefile.split("\ndemo:", 1)[1].split("\n", 2)[1]
