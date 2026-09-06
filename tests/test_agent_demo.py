"""Naskah demo spec §7 (`make demo`, TASKS 3.4): dua run identik, dan tidak ada yang dipalsukan.

Yang dipatok di sini, dan alasannya:

* **Dua run byte-identik.** Itu AC-nya apa adanya. Store append-only membuat "dua run identik"
  mustahil di atas DB yang sama, jadi tes ini sekaligus menjaga cara satu-satunya yang membuatnya
  mungkin: DB SQLite baru dari fixture setiap run, dan jam beku fixture sebagai jam keputusan.
* **`data/ryo.db` tidak dibuka.** Diukur, bukan diklaim: `agent.demo` memasang audit hook atas
  `open`/`sqlite3.connect`. Tes memaku hitungannya nol DAN memaku bahwa hook-nya benar-benar
  menyala (dengan membuka satu berkas di bawah `data/` dengan sengaja).
* **`decision_hash` dicetak apa adanya.** Naskah yang menormalkan hash akan lulus `diff` tanpa
  membuktikan apa pun; hash yang muncul harus hash yang sama dengan yang tercatat di fixture.
* **Langkah 2 berjalan, dan padamnya diakui SINTETIS.** Fixture TASKS 2.2c sekarang ada dan
  dipakai secara bawaan — oleh modul, bukan hanya oleh `scripts/demo.sh`. Karena `status:
  unavailable` belum pernah teramati dari RYO (`docs/api-facts.md`: 0/3645, id <= 3645 per
  6 Sep 2026) envelope padam itu DIBENTUK, dan naskah wajib mengakuinya di layar. Tes di sini
  memaku kedua arahnya: fixture yang mengaku dibentuk tidak boleh disembunyikan, fixture yang
  TIDAK mengaku tidak boleh dicap sintetis. Tanpa fixture, langkah 2 tetap wajib mengaku
  MENUNGGU, bukan dipalsukan.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import agent.demo as agent_demo_module
from agent.demo import (
    BANNER,
    DATA_DIR,
    DEFAULT_STEP2_FIXTURE,
    STEP2_TASK,
    DemoError,
    _step2_disclosure,
    _step2_summary,
    _tamper_one_byte,
    build_parser,
    demo_lines,
    install_data_dir_audit,
    main,
)
from agent.demo_fixture import DEMO_FIXTURE_PATH, load_fixture
from core.store import sha256_hex
from preflight.outage_fixture import SOURCE_HASH_KEY

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def script() -> str:
    """Satu run naskah BAWAAN (langkah 2 ikut), dipakai bersama oleh tes yang membaca keluarannya."""
    return "\n".join(demo_lines())


@pytest.fixture(scope="module")
def script_without_step2() -> str:
    """Naskah tanpa fixture langkah 2 — jalur `--step2-fixture ""`, yang wajib mengaku MENUNGGU."""
    return "\n".join(demo_lines(step2_fixture=None))


@pytest.fixture(scope="module")
def script_with_undeclared_fixture() -> str:
    """Naskah dengan fixture langkah 2 yang TIDAK menyatakan asal-usulnya (tanpa blok `synthetic`).

    Fixture demo biasa dipakai di sini SEBAGAI KABEL, bukan sebagai peragaan langkah 2: ia tidak
    memuat tool `unavailable`, dan tes ini tidak berpura-pura ia memuatnya.
    """
    return "\n".join(demo_lines(step2_fixture=DEMO_FIXTURE_PATH))


def test_two_runs_of_the_script_are_byte_identical() -> None:
    """AC TASKS 3.4: `make demo > a; make demo > b; diff a b` kosong."""
    first = "\n".join(demo_lines())
    second = "\n".join(demo_lines())
    assert first == second


def test_the_script_never_opens_anything_under_data(script: str) -> None:
    """`data/ryo.db` tidak disentuh — diukur oleh audit hook, bukan dijanjikan."""
    audit = install_data_dir_audit()
    assert audit.paths == []
    assert "penjaga: 0 berkas di data/ dibuka proses ini" in script


def test_the_data_dir_guard_actually_fires(tmp_path: Path) -> None:
    """Penjaga yang tidak pernah menyala adalah lencana hijau kosong: buktikan ia menyala.

    Berkas dibuat di dalam `data/` lalu dihapus lagi — yang diuji adalah hook, bukan isinya, dan
    `data/ryo.db` tidak disentuh sama sekali.
    """
    audit = install_data_dir_audit()
    canary = DATA_DIR / "demo-audit-canary.tmp"
    try:
        canary.write_text("x", encoding="utf-8")
        with canary.open(encoding="utf-8") as handle:
            handle.read()
        assert any(entry.endswith("demo-audit-canary.tmp") for entry in audit.paths)
        assert "PELANGGARAN" in audit.line()
    finally:
        canary.unlink(missing_ok=True)
        audit.paths.clear()


def test_the_banner_says_simulated_at_the_top(script: str) -> None:
    """docs/rules-compliance.md: kata SIMULATED harus terlihat, di layar mana pun."""
    assert script.startswith(BANNER)
    assert "SIMULATED" in BANNER
    assert "PRACTICE/SIMULATED" in script


def test_the_normalisation_is_written_out_explicitly(script: str) -> None:
    """AC 2: normalisasi ditulis eksplisit, dan `decision_hash` TIDAK termasuk yang dinormalkan."""
    assert "DINORMALKAN (dibuang dari keluaran): path direktori tmp" in script
    assert "TIDAK dinormalkan, wajib identik apa adanya: decision_hash" in script


def test_step_one_prints_ten_items_a_decision_and_the_frozen_snapshot_ids(script: str) -> None:
    """§7 langkah 1: JSON dengan 10 item, GO/NO_GO, blocking, snapshot_ids."""
    block = script.split("=== §7 langkah 1")[1].split("=== §7 langkah 2")[0]
    assert "n_items=10" in block
    assert re.search(r"decision=(GO|NO_GO)", block)
    assert "blocking=" in block and "snapshot_ids=[" in block
    for item_id in ("01_freshness", "05_verdict_quick", "10_relative"):
        assert item_id in block
    envelope = block.split("envelope kanonik", 1)[1].split("\n", 1)[1].strip()
    payload = json.loads(envelope)
    assert payload["tool"] == "preflight"
    assert len(payload["data"]["items"]) == 10


def test_step_two_runs_by_default_and_shows_a_red_item_two(script: str) -> None:
    """§7 langkah 2 berjalan tanpa argumen apa pun: satu tool wajib padam -> item 02 MERAH.

    Bawaannya harus hidup di modul, bukan hanya di `scripts/demo.sh`: `python -m agent.demo` polos
    dan `make demo` memperagakan naskah yang SAMA, atau salah satunya berbohong tentang yang lain.
    """
    block = script.split("=== §7 langkah 2")[1].split("=== §7 langkah 3")[0]
    assert "outage-one-tool-2026-09-05" in block
    assert f"MENUNGGU {STEP2_TASK}" not in block
    assert "decision=NO_GO" in block
    assert "blocking=02_completeness, 04_sentiment_shift" in block
    assert re.search(r"^    02_completeness\s+red\s", block, re.MULTILINE)
    assert "n_items=10 (green 7, red 1, unspoken 2)" in block
    # `status=partial`, bukan `unavailable`: fixture 'semua tool gagal' memperagakan kalimat LAIN.
    assert "status=partial" in block
    assert "envelope kanonik" in block


def test_step_two_admits_on_screen_that_the_outage_envelope_is_synthetic(script: str) -> None:
    """Kepatuhan: padam yang DIBENTUK harus mengaku dibentuk, di layar yang dilihat juri.

    `preflight` tidak meneruskan `warnings` envelope padam ke envelope hasil, jadi kata `SYNTHETIC`
    milik fixture tidak pernah sampai ke keluaran. Memperagakan pemadaman yang dikarang tanpa
    mengaku dikarang adalah klaim menyesatkan (`docs/rules-compliance.md`).
    """
    block = script.split("=== §7 langkah 2")[1].split("=== §7 langkah 3")[0]
    disclosure = block.split("--- make skill")[0]
    assert "PENGAKUAN" in disclosure
    assert "envelope PADAM di bawah SINTETIS" in disclosure
    assert "TASKS 2.2c" in disclosure
    # Tool mana yang ditulis ulang, dan alasannya apa adanya dari berkas fixture.
    assert "monitor_market_sentiment_shift (source_id 2492)" in disclosure
    assert "has never been observed from RYO" in disclosure
    assert "0/3645 (id <= 3645, 6 Sep 2026" in disclosure
    # Yang MENJAWAB adalah bukti nyata — dan itu DIPERIKSA, bukan disimpulkan sebagai komplemen:
    # sha256 kanonik tiap envelope dibandingkan dengan hash baris asal yang direkam saat penyalinan.
    assert "4 envelope yang MENJAWAB DIVERIFIKASI salinan apa adanya" in disclosure
    assert SOURCE_HASH_KEY in disclosure
    assert "KETIDAKCOCOKAN" not in disclosure
    for source_id in ("2491", "2494", "2495", "2544"):
        assert source_id in disclosure
    # Ringkasan akhir mengulang pengakuannya: juri yang hanya membaca ekor naskah tetap melihatnya.
    summary = script.split("=== ringkasan naskah §7 ===")[1]
    assert "envelope PADAMNYA SINTETIS" in summary


def test_step_two_does_not_reprint_the_step_one_heading(script: str) -> None:
    """Judul langkah 1 hanya boleh muncul sekali; langkah 2 tidak boleh mencuri namanya."""
    assert script.count("=== §7 langkah 1") == 1
    block = script.split("=== §7 langkah 2")[1].split("=== §7 langkah 3")[0]
    assert "langkah 1" not in block
    assert '--- make skill ARGS="preflight SOL" atas fixture langkah 2 ---' in block


def test_step_two_never_calls_a_fixture_synthetic_unless_the_fixture_says_so(
    script_with_undeclared_fixture: str,
) -> None:
    """Fixture tanpa blok `synthetic` tidak dicap sintetis — dan tidak dijamin bukan sintetis."""
    lines = script_with_undeclared_fixture
    block = lines.split("=== §7 langkah 2")[1].split("=== §7 langkah 3")[0]
    assert "TIDAK DINYATAKAN" in block
    assert "SINTETIS" not in block.upper().replace("SYNTHETIC", "")
    assert "PENGAKUAN" not in block
    summary = lines.split("=== ringkasan naskah §7 ===")[1]
    assert "tidak menyatakan asal-usul envelopenya" in summary


def test_step_two_still_admits_it_is_waiting_when_no_fixture_is_given(
    script_without_step2: str,
) -> None:
    """Tanpa fixture, langkah 2 mengaku MENUNGGU: tidak ada peragaan yang dipalsukan."""
    block = script_without_step2.split("=== §7 langkah 2")[1].split("=== §7 langkah 3")[0]
    assert f"MENUNGGU {STEP2_TASK}" in block
    assert "TIDAK dijalankan, dan tidak dipalsukan" in block
    assert "--step2-fixture" in block
    # Tidak ada envelope yang dicetak untuk langkah ini: tidak ada yang diperagakan.
    assert "envelope kanonik" not in block
    assert "SINTETIS" not in block
    assert (
        f"langkah 2 MENUNGGU {STEP2_TASK}" in (script_without_step2.split("=== ringkasan naskah §7 ===")[1])
    )


def test_step_three_runs_three_ticks_with_one_go_and_one_no_go(script: str) -> None:
    """§7 langkah 3: ledger ≥1 GO (trade sesuai plan) + ≥1 NO_GO dengan blocking, tiga tick."""
    block = script.split("=== §7 langkah 3")[1].split("=== §7 langkah 4")[0]
    for number in (1, 2, 3):
        assert f"tick {number}:" in block
    assert "practice trade opened (SIMULATED)" in block
    assert "declined; blocking: 05_verdict_quick, 06_confluence" in block
    assert "syarat langkah 3 terpenuhi" in block
    # Tick kedua dan ketiga harus mengulang keputusan yang sama; "BERBEDA" berarti determinisme rusak.
    assert "(sama dengan tick 1)" in block
    assert "BERBEDA dari tick 1" not in block


def test_step_three_replays_identical_and_a_one_byte_tamper_mismatches(script: str) -> None:
    """`make replay ID=` → IDENTICAL; satu byte snapshot diubah → MISMATCH ber-`snapshot_id`."""
    block = script.split("=== §7 langkah 3")[1].split("=== §7 langkah 4")[0]
    assert block.count("-> IDENTICAL") == 2
    assert "tamper: 1 byte diubah pada SALINAN DB" in block
    assert "atas salinan itu -> MISMATCH" in block
    assert "payload_hash tidak cocok" in block


def test_the_hashes_printed_are_the_hashes_recorded_in_the_fixture(script: str) -> None:
    """Hash dicetak apa adanya, dan hash itu yang tercatat saat fixture disalin (TASKS 3.4a)."""
    fixture = load_fixture(DEMO_FIXTURE_PATH)
    assert fixture.recorded, "fixture tanpa blok `recorded` tidak bisa dipatok"
    for symbol, entry in fixture.recorded.items():
        assert f"{symbol:<6} decision_hash={entry['decision_hash']}" in script
    assert fixture.params_hash in script


def test_step_four_shows_the_card_per_item_and_an_honest_narration(script: str) -> None:
    """§7 langkah 4: panggilan per item + `narration_unavailable` saat env kosong."""
    block = script.split("=== §7 langkah 4")[1].split("=== §7 langkah 5")[0]
    assert "decisions.card_json" in block
    assert len(re.findall(r"^  \d\d_[a-z_]+ ", block, re.MULTILINE)) == 10
    assert "narration.status = narration_unavailable" in block
    assert "n_lines=0 dari n_items=10" in block
    before, after = re.findall(r"decision_hash se[a-z]+ narasi ditempel\s*: ([0-9a-f]{64})", block)
    assert before == after
    assert "-> SAMA: narasi hidup di summary" in block


def test_step_five_renders_both_screens_with_the_simulated_banner(script: str) -> None:
    """§7 langkah 5: Layar A diurutkan daya veto, Layar B kartu panggilan, banner SIMULATED."""
    block = script.split("=== §7 langkah 5")[1].split("=== §7 langkah 6")[0]
    assert "GET /ui                    -> HTTP 200" in block
    assert block.count("banner SIMULATED terlihat: True") == 2
    assert "urutan baris (daya veto lebih dulu): decision_id" in block
    assert "baris panggilan di kartu: 10" in block
    assert "handler hover/onclick: 0" in block
    # Kartu yang dibuka Layar B harus ada di daftar Layar A: dua layar yang saling menunjuk.
    card_id = re.search(r"GET /ui/decision/(\d+)", block).group(1)
    order = re.search(r"decision_id ([\d, ]+)", block).group(1)
    assert card_id in [entry.strip() for entry in order.split(",")]


def test_step_six_shows_the_same_symbol_deciding_differently_on_two_dates(script: str) -> None:
    """§7 langkah 6 = TASKS 4.7, dipanggil apa adanya, bukan ditulis ulang di sini."""
    block = script.split("=== §7 langkah 6")[1]
    assert "two-date roll call" in block
    assert "keputusan: GO -> NO_GO" in block
    assert "<- berubah" in block


def test_no_pnl_anywhere_in_the_script(script: str) -> None:
    """Rubric §8: P&L jangka pendek tidak dinilai, jadi ia tidak ikut dicetak."""
    lowered = script.lower()
    for word in ("profit", "unrealized", "realised pnl", "pnl", "roi"):
        assert word not in lowered
    # P&L hanya boleh muncul sebagai penyangkalannya sendiri, tidak pernah sebagai angka.
    mentions = [line for line in script.splitlines() if "P&L" in line]
    assert mentions
    for line in mentions:
        assert "tidak ada P&L" in line or "tanpa P&L" in line or "tidak dinilai" in line
    assert "tidak ada P&L di keluaran ini" in script


def test_the_exit_facts_are_stated_without_a_number_of_money(script: str) -> None:
    """Yang dilaporkan: apakah plan menyentuh target/stop — fakta, bukan untung/rugi."""
    assert "keluar posisi pada jam beku (harga dari store perekam, tanpa P&L)" in script


def test_a_missing_step_two_fixture_fails_honestly_and_fast(capsys: pytest.CaptureFixture) -> None:
    """Path salah ketik → pesan jujur + exit 2, sebelum tiga tick dijalankan."""
    code = main(["--step2-fixture", str(ROOT / "agent" / "fixtures" / "tidak-ada.json")])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "demo:" in captured.err and "tidak bisa dibaca" in captured.err


def test_the_step_two_hook_runs_the_fixture_it_is_given(script_with_undeclared_fixture: str) -> None:
    """`--step2-fixture PATH` menjalankan fixture yang DITUNJUK, bukan bawaannya."""
    lines = script_with_undeclared_fixture
    block = lines.split("=== §7 langkah 2")[1].split("=== §7 langkah 3")[0]
    assert "envelope kanonik" in block
    assert f"MENUNGGU {STEP2_TASK}" not in block
    assert DEMO_FIXTURE_PATH.stem in block
    assert DEFAULT_STEP2_FIXTURE.stem not in block
    assert f"langkah 2 dijalankan dari {DEMO_FIXTURE_PATH.name}" in lines


def test_main_prints_the_script_and_exits_zero(capsys: pytest.CaptureFixture) -> None:
    code = main([])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.startswith(BANNER)
    assert captured.out.endswith("\n")


def test_the_parser_carries_the_step_two_default_so_the_module_matches_the_shell() -> None:
    """Bawaan langkah 2 hidup di argparse: modul dan `scripts/demo.sh` tidak boleh berbeda naskah."""
    args = build_parser().parse_args([])
    assert args.fixture == str(DEMO_FIXTURE_PATH)
    assert args.step2_fixture == str(DEFAULT_STEP2_FIXTURE)
    assert DEFAULT_STEP2_FIXTURE.is_file(), "fixture bawaan langkah 2 (TASKS 2.2c) harus ada"
    # Fixture 'semua tool gagal' TIDAK boleh jadi bawaan: ia memperagakan kalimat lain.
    assert "all-tools" not in args.step2_fixture
    # String kosong = melewati langkah 2 dengan jujur, bukan memalsukannya.
    assert build_parser().parse_args(["--step2-fixture", ""]).step2_fixture == ""


def test_the_one_byte_tamper_keeps_the_length_and_refuses_a_payload_without_digits() -> None:
    """Tamper deterministik: satu digit, panjang tetap; payload tanpa digit = error jujur."""
    payload = '{"status":"ok","n":42}'
    tampered, index, before, after = _tamper_one_byte(payload)
    assert len(tampered) == len(payload)
    assert tampered != payload
    assert payload[index] == before and tampered[index] == after
    with pytest.raises(DemoError):
        _tamper_one_byte('{"status":"ok"}')


def test_make_demo_is_wired_to_the_script_and_the_script_to_the_module() -> None:
    """`make demo` harus benar-benar menjalankan naskah ini, bukan placeholder yang exit 0."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    demo_target = makefile.split("\ndemo:", 1)[1].split("\n", 2)[1]
    assert "scripts/demo.sh" in demo_target
    script = ROOT / "scripts" / "demo.sh"
    assert script.stat().st_mode & 0o111, "scripts/demo.sh harus executable"
    body = script.read_text(encoding="utf-8")
    assert "python -m agent.demo" in body
    assert "set -euo pipefail" in body
    # `.env` tidak boleh dimuat: §7 langkah 4 menuntut env LLM kosong.
    assert ".env" not in body.split("set -euo pipefail", 1)[1]


def test_the_module_runs_as_a_command(tmp_path: Path) -> None:
    """`python -m agent.demo` harus jalan sebagai perintah, bukan hanya sebagai impor."""
    proc = subprocess.run(
        [sys.executable, "-m", "agent.demo"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.startswith(BANNER)
    assert "=== ringkasan naskah §7 ===" in proc.stdout


# --- pengakuan langkah 2: diaudit, bukan dipercaya; dan tidak bisa ditimpa teks berkas ---------
#
# Dua temuan TINGGI security-reviewer (6 Sep 2026), keduanya terbukti jalan:
#
# 1. pengakuan sintetis diturunkan dari blok `synthetic` berkas yang ia klaim audit — berkas yang
#    envelopenya SUDAH ditulis ulang menjadi `unavailable` tetapi blok pengakuannya dikosongkan
#    membuat naskah mencetak klaim afirmatif yang SALAH: envelope karangan disebut "salinan apa
#    adanya bukti nyata". Lebih buruk daripada diam.
# 2. teks berkas dicetak tanpa sanitizer: `\x1b[2J` menghapus layar, `\x1b[10A\x1b[1G` menimpa
#    baris pengakuan yang asli, `\r\n` menyisipkan baris palsu.
#
# Fixture di bawah dibangun SENDIRI di `tmp_path` dan sengaja tidak menyalin fixture terkirim:
# tes ini tidak boleh pecah setiap kali `preflight/fixtures/*.json` diregenerasi, dan yang diuji
# adalah pemeriksaannya, bukan isi fixture siapa pun.

_HONEST_NOTE = {
    "task": "TES",
    "derived_from": "tests/test_agent_demo.py",
    "envelopes_rewritten_to_unavailable": ["outage_tool (source_id 22)"],
    "why": "one required tool must answer unavailable for spec §7 step 2",
}


def _tiny_step2_fixture(
    tmp_path: Path,
    *,
    note: dict | None = _HONEST_NOTE,
    row_flag: bool = True,
    name: str = "tiny-step2",
    source_db: str = "data/ryo.db",
    tool: str = "outage_tool",
    shaped: bool = True,
    hashed: bool = True,
    drift: bool = False,
) -> Path:
    """Fixture langkah 2 kecil milik tes ini: satu envelope menjawab, satu berbentuk padam.

    `hashed` menanam `source_payload_hash` baris yang menjawab (yang ditanam
    `agent.demo_fixture.capture` saat penyalinan nyata); `drift` mengubah ISI envelope itu
    SESUDAH hashnya direkam — persis bentuk pemalsuan yang tidak menyentuh `status`/`warnings`.
    """
    answering = {
        "status": "ok",
        "as_of": "2026-09-05T02:45:02Z",
        "data_mode": "live",
        "data": {},
        "availability": {},
        "warnings": [],
    }
    outage = {
        "status": "unavailable" if shaped else "ok",
        "as_of": "2026-09-05T02:45:03Z",
        "data_mode": "live",
        "data": None if shaped else {},
        "availability": {},
        "warnings": ["SYNTHETIC: shaped by this test, not RYO output"] if shaped else [],
    }
    raw: dict = {
        "fixture_version": 1,
        "name": name,
        "captured_at": "2026-09-05T03:10:00Z",
        "source_db": source_db,
        "now": "2026-09-05T02:46:00+00:00",
        "symbols": ["SOL"],
        "params_hash": "0" * 64,
        "recorded": {},
        "snapshots": [
            {
                "source_id": 11,
                "tool": "market_overview",
                "args": {"symbol": "SOL"},
                "ts_utc": "2026-09-05T02:45:02Z",
                "envelope": answering,
                "synthetic": False,
            },
            {
                "source_id": 22,
                "tool": tool,
                "args": {"symbol": "SOL"},
                "ts_utc": "2026-09-05T02:45:03Z",
                "envelope": outage,
                "synthetic": row_flag,
            },
        ],
    }
    if hashed:
        raw["snapshots"][0][SOURCE_HASH_KEY] = sha256_hex(answering)
    if drift:
        # Angka diketik ulang di dalam envelope NYATA: bentuknya tetap sah, isinya bukan lagi
        # salinan. Hash direkam SEBELUM baris ini, sama seperti di dunia nyata.
        answering["data"] = {"breadth": 0.0001}
    if note is not None:
        raw["synthetic"] = note
    target = tmp_path / "step2-tiny.json"
    target.write_text(json.dumps(raw), encoding="utf-8")
    return target


def _disclosure_of(path: Path) -> str:
    return "\n".join(_step2_disclosure(path, load_fixture(path)))


def test_the_disclosure_is_computed_from_the_envelopes_not_from_the_files_claim(tmp_path: Path) -> None:
    """Fixture jujur: himpunan sintetis yang dicetak sama dengan yang benar-benar dimuat."""
    path = _tiny_step2_fixture(tmp_path)
    text = _disclosure_of(path)
    assert "PENGAKUAN" in text
    assert "Yang dibentuk: 1 envelope" in text
    assert "outage_tool (source_id 22)" in text
    assert "KETIDAKCOCOKAN" not in text
    copied = next(line for line in text.splitlines() if "bukti nyata" in line)
    assert "11" in copied and "22" not in copied
    assert "1 envelope yang MENJAWAB DIVERIFIKASI salinan apa adanya" in text
    assert "KETIDAKCOCOKAN" not in _step2_summary(path)


def test_a_fixture_whose_synthetic_block_lies_is_caught_not_repeated(tmp_path: Path) -> None:
    """TINGGI-1: envelope padam yang tidak diakui berkas TIDAK boleh disebut bukti nyata."""
    path = _tiny_step2_fixture(
        tmp_path,
        note={
            "task": "RYO LIVE CAPTURE",
            "derived_from": "data/ryo.db (live)",
            "envelopes_rewritten_to_unavailable": [],
            "why": "every envelope here is verbatim RYO evidence",
        },
        row_flag=False,
    )
    text = _disclosure_of(path)
    assert "KETIDAKCOCOKAN" in text
    assert "berkas TIDAK mengaku membentuk source_id 22" in text
    # Klaim afirmatif yang salah adalah temuannya: id 22 tidak boleh masuk kalimat mana pun yang
    # menyebut "salinan apa adanya" — dan di sini kalimat itu hanya lahir untuk id yang terverifikasi.
    for line in text.splitlines():
        if "salinan apa adanya" in line or "bukti nyata" in line:
            assert "22" not in line
    assert "Yang dibentuk: 1 envelope" in text
    # Ekor naskah tidak boleh lebih optimistis daripada badannya.
    assert "KETIDAKCOCOKAN" in _step2_summary(path)


def test_a_fixture_that_claims_more_than_it_shaped_is_caught_too(tmp_path: Path) -> None:
    """Arah sebaliknya: berkas mengaku membentuk envelope yang envelopenya tidak berbentuk padam."""
    path = _tiny_step2_fixture(
        tmp_path,
        note={**_HONEST_NOTE, "envelopes_rewritten_to_unavailable": ["market_overview (source_id 11)"]},
        row_flag=False,
        shaped=False,
    )
    text = _disclosure_of(path)
    assert "berkas mengaku membentuk source_id 11" in text
    assert "Yang dibentuk: 0 envelope" in text


def test_an_undeclared_fixture_with_a_shaped_envelope_is_not_waved_through(tmp_path: Path) -> None:
    """Tanpa blok `synthetic` sama sekali: diam bukan alasan, selisihnya tetap dicetak.

    Kalimat lunak "asal-usul TIDAK DINYATAKAN" TIDAK boleh ikut tercetak di sini: berkas ini punya
    envelope berbentuk padam, jadi keadaannya bukan "tidak dinyatakan" melainkan "tidak diakui".
    """
    path = _tiny_step2_fixture(tmp_path, note=None, row_flag=False)
    text = _disclosure_of(path)
    assert "KETIDAKCOCOKAN" in text
    assert "TIDAK DINYATAKAN" not in text
    assert "berkas TIDAK mengaku membentuk source_id 22" in text
    assert "berkas TIDAK memuat blok `synthetic` sama sekali" in text
    assert "KETIDAKCOCOKAN" in _step2_summary(path)


def test_deleting_the_whole_synthetic_block_does_not_buy_silence(tmp_path: Path) -> None:
    """Lubang laporan juri fase 5: blok `synthetic` DIHAPUS SELURUHNYA, bendera per-baris dibiarkan.

    Kedua selisih lama menjadi nol — bendera `snapshots[*].synthetic` masih mengaku, jadi himpunan
    yang diakui persis sama dengan yang benar-benar dibentuk — dan naskah dulu lolos dengan kalimat
    lunak "asal-usul TIDAK DINYATAKAN", exit 0, nol KETIDAKCOCOKAN. Bukti bahwa envelope itu
    dibentuk ada di berkasnya sendiri; menghapus pengakuannya adalah penyembunyian, bukan diam.
    """
    path = _tiny_step2_fixture(tmp_path, note=None, row_flag=True)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "synthetic" not in raw  # blok atas hilang; bendera per-baris tetap ada
    assert raw["snapshots"][1]["synthetic"] is True
    text = _disclosure_of(path)
    assert "KETIDAKCOCOKAN" in text
    assert "TIDAK DINYATAKAN" not in text
    assert "berkas TIDAK memuat blok `synthetic` sama sekali" in text
    assert "source_id 22 BERBENTUK padam" in text
    # Ekor naskah ikut menyebutnya, sama seperti varian penyembunyian yang lain.
    assert "KETIDAKCOCOKAN" in _step2_summary(path)


def test_a_fixture_with_nothing_shaped_and_no_block_is_still_treated_gently(tmp_path: Path) -> None:
    """Sisi lain penyempitan: berkas TANPA envelope padam DAN tanpa blok `synthetic` tetap lunak.

    Cabang lunak tidak dihapus, hanya dipersempit: berkas pihak ketiga yang tidak memuat satu pun
    envelope berbentuk padam tidak punya apa pun untuk diakui, jadi menuduhnya akan salah.
    """
    path = _tiny_step2_fixture(tmp_path, note=None, row_flag=False, shaped=False)
    text = _disclosure_of(path)
    assert "TIDAK DINYATAKAN" in text
    assert "KETIDAKCOCOKAN" not in text
    assert "PENGAKUAN" not in text
    assert "KETIDAKCOCOKAN" not in _step2_summary(path)


def test_an_answering_envelope_whose_content_changed_is_never_called_a_verbatim_copy(
    tmp_path: Path,
) -> None:
    """TINGGI: yang diaudit harus ISI envelope, bukan hanya BENTUKnya.

    `status`/`warnings` yang rapi tidak mengatakan apa pun tentang angka di dalam envelope. Satu
    nilai yang diketik ulang di dalam envelope NYATA menggeser keputusan dan `decision_hash` tanpa
    satu peringatan pun, sementara baris "N envelope yang MENJAWAB adalah salinan apa adanya bukti
    nyata" tetap tercetak — klaim afirmatif yang SALAH. Yang dituntut di sini: baris itu tidak
    tercetak untuk envelope yang isinya bergeser, dan selisihnya disebut.
    """
    path = _tiny_step2_fixture(tmp_path, drift=True)
    text = _disclosure_of(path)
    assert "KETIDAKCOCOKAN ISI" in text
    assert "source_id 11" in text
    # Envelope yang bergeser TIDAK boleh muncul di kalimat mana pun yang menyebutnya salinan.
    for line in text.splitlines():
        if "salinan apa adanya" in line or "bukti nyata" in line:
            assert "11" not in line
    assert "1 envelope yang MENJAWAB DIVERIFIKASI" not in text
    # Ekor naskah ikut menyebutkannya: juri yang hanya membaca ringkasan tetap melihatnya.
    assert "KETIDAKCOCOKAN" in _step2_summary(path)


def test_a_row_without_a_recorded_hash_is_downgraded_not_waved_through(tmp_path: Path) -> None:
    """Verifikasi yang tidak bisa dijalankan diakui, bukan diganti klaim kuat yang kosong."""
    path = _tiny_step2_fixture(tmp_path, hashed=False)
    text = _disclosure_of(path)
    assert "TIDAK DIVERIFIKASI" in text
    assert "MENGAKU menyalinnya" in text
    assert "DIVERIFIKASI salinan apa adanya" not in text
    assert "KETIDAKCOCOKAN" not in text
    # Baris tanpa hash bukan kebohongan: ringkasannya tidak boleh menuduh.
    assert "KETIDAKCOCOKAN" not in _step2_summary(path)
    assert "TIDAK DIVERIFIKASI" in _step2_summary(path)


def test_the_head_verifies_the_main_fixture_not_only_step_two(script: str) -> None:
    """Fixture UTAMA (yang menggerakkan langkah 1/3/4/5) ikut diperiksa, bukan hanya langkah 2."""
    head = script.split("=== §7 langkah 1")[0]
    fixture = load_fixture(DEMO_FIXTURE_PATH)
    assert f"{len(fixture.snapshots)} envelope yang MENJAWAB DIVERIFIKASI salinan apa adanya" in head
    assert SOURCE_HASH_KEY in head
    assert "KETIDAKCOCOKAN" not in head
    summary = script.split("=== ringkasan naskah §7 ===")[1]
    assert "bukti fixture utama" in summary
    assert "DIVERIFIKASI" in summary


def test_the_main_fixture_cannot_hide_a_retyped_number_either(tmp_path: Path) -> None:
    """Satu angka diketik ulang di fixture utama: kepala naskah DAN ringkasan menyebutnya."""
    raw = json.loads(DEMO_FIXTURE_PATH.read_text(encoding="utf-8"))
    target = raw["snapshots"][0]
    target["envelope"]["warnings"] = [*target["envelope"].get("warnings", []), "diketik tangan"]
    path = tmp_path / "utama-palsu.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    lines = "\n".join(demo_lines(path, step2_fixture=None))
    head = lines.split("=== §7 langkah 1")[0]
    assert "KETIDAKCOCOKAN ISI" in head
    assert f"source_id {target['source_id']}" in head
    summary = lines.split("=== ringkasan naskah §7 ===")[1]
    assert "bukti fixture utama: KETIDAKCOCOKAN ISI" in summary


def test_the_shaped_predicate_has_exactly_one_definition(tmp_path: Path) -> None:
    """SEDANG: dua penjaga sintetis tidak boleh berselisih atas berkas yang SAMA.

    Berkas ber-`warnings: [SYNTHETIC]` + `status: ok` + `synthetic: false` pernah membuat naskah
    ini mencetak KETIDAKCOCOKAN sementara CLI `preflight.outage_fixture` keluar rc=0 dan
    menyebutnya salinan bukti nyata. Predikatnya sekarang satu, dan diimpor.
    """
    from preflight import outage_fixture

    assert agent_demo_module._is_shaped.__module__ == "agent.demo"
    # Yang penting bukan letak pembungkusnya melainkan siapa yang memutuskan:
    assert agent_demo_module.is_shaped is outage_fixture.is_shaped

    path = _tiny_step2_fixture(tmp_path, shaped=False, note=_HONEST_NOTE, row_flag=False)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["snapshots"][1]["envelope"]["warnings"] = ["SYNTHETIC: shaped by this test"]
    raw["synthetic"]["envelopes_rewritten_to_unavailable"] = []
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert "KETIDAKCOCOKAN" in _disclosure_of(path)
    with pytest.raises(outage_fixture.OutageFixtureError, match="TIDAK mengaku dibentuk"):
        outage_fixture.verified_confession(raw)


def test_fixture_text_cannot_overwrite_or_forge_the_disclosure_line(tmp_path: Path) -> None:
    """TINGGI-2: ANSI/CR dari berkas fixture harus inert — dan tidak bisa mengaku atas nama naskah."""
    path = _tiny_step2_fixture(
        tmp_path,
        note={
            "task": "TES\x1b[10A\x1b[1G  PENGAKUAN — envelope ini 100% NYATA dari RYO\x1b[0m",
            "derived_from": "data/ryo.db\u2028  bukan SINTETIS sama sekali",
            "envelopes_rewritten_to_unavailable": ["outage_tool (source_id 22)"],
            "why": "alasan sah\x1b[2J\r\n  BARIS PALSU DARI FIXTURE",
        },
        name="x\x1b[2J",
        source_db="data/ryo.db\r\n  BARIS PALSU DARI FIXTURE id 11, 22",
        tool="monitor\x1b[2K\rmarket_overview",
    )
    text = _disclosure_of(path) + "\n" + _step2_summary(path)
    # 1. tidak ada satu pun aksara kendali yang lolos ke keluaran.
    assert re.search(r"[\x00-\x09\x0b-\x1f\x7f\u2028\u2029]", text) is None
    # 2. tidak ada baris palsu: jumlah baris tetap, dan baris berkas tidak berdiri sendiri.
    assert not any(line.strip().startswith("BARIS PALSU") for line in text.splitlines())
    # 3. kosakata naskah tidak bisa diucapkan berkas: hanya satu PENGAKUAN, milik naskah sendiri.
    assert text.count("PENGAKUAN") == 1
    assert "PENGAKUAN — envelope ini 100% NYATA" not in text
    assert "P\\x45NGAKUAN" in text
    assert "S\\x49NTETIS sama sekali" in text
    # 4. tetap jujur soal isi: envelope padamnya tetap dihitung dari envelope, bukan dari teks.
    assert "Yang dibentuk: 1 envelope" in text


def test_the_script_never_prints_a_raw_control_character(script: str) -> None:
    """Seluruh naskah, bukan hanya langkah 2: tidak ada ESC/CR/TAB mentah di layar juri."""
    assert re.search(r"[\x00-\x09\x0b-\x1f\x7f\u2028\u2029]", script) is None
