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
* **Langkah 2 tidak dipalsukan.** Sampai fixture TASKS 2.2c ada, langkah 2 wajib mengaku MENUNGGU.
  Tes ini akan MERAH begitu seseorang menutupinya dengan fixture "semua tool gagal".
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agent.demo import (
    BANNER,
    DATA_DIR,
    STEP2_TASK,
    DemoError,
    _tamper_one_byte,
    build_parser,
    demo_lines,
    install_data_dir_audit,
    main,
)
from agent.demo_fixture import DEMO_FIXTURE_PATH, load_fixture

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def script() -> str:
    """Satu run naskah, dipakai bersama oleh tes yang hanya membaca keluarannya."""
    return "\n".join(demo_lines())


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


def test_step_two_admits_it_is_waiting_and_does_not_fake_a_red_item_two(script: str) -> None:
    """Langkah 2 menunggu TASKS 2.2c. Menutupinya dengan fixture 'semua tool gagal' = tes merah."""
    block = script.split("=== §7 langkah 2")[1].split("=== §7 langkah 3")[0]
    assert f"MENUNGGU {STEP2_TASK}" in block
    assert "TIDAK dijalankan, dan tidak dipalsukan" in block
    assert "--step2-fixture" in block
    # Tidak ada envelope yang dicetak untuk langkah ini: tidak ada yang diperagakan.
    assert "envelope kanonik" not in block
    assert f"langkah 2 MENUNGGU {STEP2_TASK}" in script.split("=== ringkasan naskah §7 ===")[1]


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


def test_the_step_two_hook_runs_the_fixture_it_is_given() -> None:
    """Titik sambung TASKS 2.2c benar-benar tersambung — diuji dengan fixture yang ADA.

    Fixture demo biasa dipakai di sini SEBAGAI KABEL, bukan sebagai peragaan langkah 2: ia tidak
    memuat tool yang `unavailable`, dan tes ini tidak berpura-pura ia memuatnya. Yang dibuktikan
    hanyalah bahwa `--step2-fixture` menjalankan fixture yang ditunjuk dan mencetak envelopenya.
    """
    lines = "\n".join(demo_lines(step2_fixture=str(DEMO_FIXTURE_PATH)))
    block = lines.split("=== §7 langkah 2")[1].split("=== §7 langkah 3")[0]
    assert "envelope kanonik" in block
    assert f"MENUNGGU {STEP2_TASK}" not in block
    assert "langkah 2 dijalankan dari fixture yang diberikan" in lines


def test_main_prints_the_script_and_exits_zero(capsys: pytest.CaptureFixture) -> None:
    code = main([])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.startswith(BANNER)
    assert captured.out.endswith("\n")


def test_the_parser_exposes_both_connection_points() -> None:
    args = build_parser().parse_args([])
    assert args.fixture == str(DEMO_FIXTURE_PATH)
    assert args.step2_fixture is None


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
