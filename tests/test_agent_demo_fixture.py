"""Fixture demo bertanggal untuk spec §7 langkah 3 (TASKS 3.4a).

Yang dibuktikan berkas ini, satu tes per AC:

* AC(1) bukti DISALIN — fixture membawa envelope-nya sendiri dan satu tick berhasil dari
  direktori kerja yang tidak punya `data/ryo.db` sama sekali;
* AC(2) satu tick menghasilkan ≥1 GO dan ≥1 NO_GO ber-`blocking` yang tidak kosong;
* AC(3) dua run identik baris demi baris, `decision_hash` termasuk (dan sama dengan hash yang
  tercatat saat penyalinan — kalau penilaian bergeser, tes ini yang bicara lebih dulu);
* AC(4) fixture ditolak masuk ke store bukti, lewat penjaga TASKS 0.19 yang sudah ada.

Blok terakhir menambah satu hal yang tidak bisa dilakukan berkas fixture atas dirinya sendiri:
JANGKAR provenans ke `data/ryo.db` (baca-saja, dilewati bila berkasnya tidak ada). Tanpa itu,
"salinan apa adanya" hanya konsistensi-diri berkas.

Tidak ada tes di sini yang MENULIS ke `data/ryo.db` — jalur tulis justru diuji DITOLAK, dan
penolakannya terjadi sebelum store dibuka; jangkar provenans membukanya `read_only=True`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent.demo_fixture import (
    DEMO_FIXTURE_PATH,
    DemoFixtureError,
    load_fixture,
    materialize,
    report_lines,
    run_tick,
)
from agent.main import tick
from agent.params import DEFAULT_AGENT_PARAMS
from core.store import DEFAULT_DB_PATH, Store, sha256_hex
from preflight.outage_fixture import SOURCE_HASH_KEY, is_shaped

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ROOT / "data"


@pytest.fixture(scope="module")
def demo():
    return load_fixture(DEMO_FIXTURE_PATH)


def test_fixture_carries_its_own_evidence(demo) -> None:
    """AC(1): bukti ada DI DALAM berkas fixture, bukan ditunjuk ke store hidup."""
    assert demo.snapshots, "fixture tanpa snapshot tidak membekukan apa pun"
    for snapshot in demo.snapshots:
        assert snapshot.envelope.get("status"), f"{snapshot.tool}: envelope tanpa status"
        assert snapshot.ts_utc, f"{snapshot.tool}: ts_utc kosong — ia ikut di-hash"
    assert demo.source_ids, "provenance id sumber wajib tercatat"


def test_tick_runs_without_the_live_store(tmp_path, monkeypatch, demo) -> None:
    """AC(1): dari cwd tanpa `data/ryo.db` dan `DB_PATH` yang tidak ada, tick tetap jalan."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tidak-ada.db"))
    assert not (tmp_path / "data" / "ryo.db").exists()
    report = run_tick(demo, tmp_path / "demo.db")
    assert report.n_go + report.n_no_go == len(demo.symbols)


def test_one_tick_gives_at_least_one_go_and_one_blocked_no_go(tmp_path, demo) -> None:
    """AC(2): syarat spec §7 langkah 3, apa adanya."""
    report = run_tick(demo, tmp_path / "demo.db")
    assert report.n_rejected == 0, [outcome.rejected for outcome in report.outcomes]
    assert report.n_insufficient == 0, [outcome.insufficient for outcome in report.outcomes]
    assert report.n_go >= 1, "spec §7 langkah 3 menuntut ≥1 GO"
    blocked = [outcome for outcome in report.outcomes if outcome.decision == "NO_GO" and outcome.blocking]
    assert blocked, "spec §7 langkah 3 menuntut ≥1 NO_GO DENGAN blocking"
    for outcome in report.outcomes:
        if outcome.decision == "GO":
            assert outcome.plan is not None, f"{outcome.symbol}: GO tanpa practice trade"
            assert outcome.trade_event_id is not None


def test_two_runs_are_identical(tmp_path, demo) -> None:
    """AC(3): dua run, dua DB baru, laporan ternormalisasi sama baris demi baris."""
    first = report_lines(demo, run_tick(demo, tmp_path / "a.db"))
    second = report_lines(demo, run_tick(demo, tmp_path / "b.db"))
    assert first == second
    assert any("decision_hash=" in line for line in first), "hash tidak boleh dinormalisasi hilang"


def test_decisions_match_what_was_recorded_at_capture(tmp_path, demo) -> None:
    """AC(3): keputusan, blocking, DAN `decision_hash` sama dengan saat fixture disalin.

    Kalau tes ini merah, yang bergeser adalah PENILAIAN (item/params), bukan fixture-nya:
    fixture beku, jadi hash yang berubah berarti keputusan lama tidak lagi bisa di-replay.
    """
    report = run_tick(demo, tmp_path / "demo.db")
    observed = {
        outcome.symbol: {
            "decision": outcome.decision,
            "blocking": list(outcome.blocking),
            "decision_hash": outcome.decision_hash,
        }
        for outcome in report.outcomes
    }
    assert observed == {symbol: dict(entry) for symbol, entry in demo.recorded.items()}
    assert demo.params_hash == DEFAULT_AGENT_PARAMS.decision_params_hash()


def test_frozen_clock_is_what_makes_the_fixture_readable(tmp_path, demo) -> None:
    """Jam beku bukan kenyamanan: tanpa `now=` fixture terbaca basi dan nol baris ditulis."""
    age_min = (datetime.now(UTC) - demo.now).total_seconds() / 60
    if age_min <= DEFAULT_AGENT_PARAMS.preflight.max_age_min:
        pytest.skip(f"fixture masih segar terhadap jam dinding ({age_min:.0f} mnt)")
    materialize(demo, tmp_path / "demo.db")
    with Store(tmp_path / "demo.db") as store:
        report = tick(store, demo.symbols, DEFAULT_AGENT_PARAMS)
    assert report.n_insufficient == len(demo.symbols)
    assert report.n_go == 0


def test_refuses_the_repo_evidence_store(demo) -> None:
    """AC(4): `data/ryo.db` ditolak dengan namanya sendiri."""
    with pytest.raises(DemoFixtureError) as excinfo:
        materialize(demo, DEFAULT_DB_PATH)
    assert "store bukti" in str(excinfo.value)


def test_refuses_a_new_file_inside_the_evidence_dir(demo) -> None:
    """AC(4): ditolak SEBELUM store dibuka — tidak ada DB kosong yang tertinggal."""
    target = EVIDENCE_DIR / "tenko-demo-fixture-tidak-boleh-ada.db"
    assert not target.exists()
    with pytest.raises(DemoFixtureError):
        materialize(demo, target)
    assert not target.exists(), "penolakan tidak boleh meninggalkan berkas"


def test_refuses_a_db_that_already_holds_decisions(tmp_path, demo) -> None:
    """AC(4): DB yang sudah memegang buku besar keputusan adalah store bukti juga."""
    target = tmp_path / "demo.db"
    run_tick(demo, target)
    with pytest.raises(DemoFixtureError) as excinfo:
        materialize(demo, target)
    assert "buku besar keputusan" in str(excinfo.value)


def test_load_rejects_a_malformed_fixture(tmp_path, demo) -> None:
    """Bentuk yang tidak lengkap = error, bukan default diam-diam."""
    raw = json.loads(DEMO_FIXTURE_PATH.read_text(encoding="utf-8"))
    raw["fixture_version"] = 999
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DemoFixtureError):
        load_fixture(broken)
    raw["fixture_version"] = 1
    raw["snapshots"] = []
    broken.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DemoFixtureError):
        load_fixture(broken)


# --- Jangkar provenans: DI LUAR berkas yang diaudit -------------------------------------------
#
# `preflight.outage_fixture.verify_provenance` membandingkan sha256 kanonik tiap envelope dengan
# `source_payload_hash` yang direkam DI BARIS YANG SAMA. Itu menangkap drift dan edit tak sengaja
# secara fail-closed, tetapi jangkarnya tinggal di dalam berkas yang ia lindungi: siapa pun yang
# mengedit envelope DAN menulis ulang hashnya membuat berkas konsisten-diri, sementara kalimat
# demo menjanjikan sesuatu yang lebih kuat — identitas dengan `data/ryo.db`.
#
# Tes di bawah memindahkan jangkarnya ke luar: ia membuka store bukti BACA-SAJA dan menuntut
# `snapshots.payload_hash` baris asal sama dengan hash yang dibawa berkas DAN dengan hash yang
# dihitung ulang dari envelope yang benar-benar dimuat. Hash palsu tidak bisa dipalsukan dua kali.
#
# PEMISAHAN dari naskah demo: ini TES, bukan demo. `agent.demo` tetap tidak boleh membuka apa pun
# di bawah `data/` (penjaga `sys.addaudithook`, "0 berkas"), dan tidak ada satu pun impor `agent.demo`
# di blok ini. Store dibuka `read_only=True`: perekam hidup, tes tidak boleh menulis satu byte pun.

EVIDENCE_DB = EVIDENCE_DIR / "ryo.db"

#: Setiap fixture bertanggal yang MENGAKU menyalin baris dari `data/ryo.db`, ketiga keluarganya.
ANCHORED_FIXTURES = (
    ROOT / "agent" / "fixtures" / "demo-2026-09-05.json",
    ROOT / "agent" / "fixtures" / "twodate-a-2026-09-03.json",
    ROOT / "agent" / "fixtures" / "twodate-b-2026-09-04.json",
    ROOT / "preflight" / "fixtures" / "outage-one-tool-2026-09-05.json",
    ROOT / "preflight" / "fixtures" / "outage-all-tools-2026-09-05.json",
)


def _evidence_rows(path: Path) -> tuple[dict, dict]:
    """(baris fixture, baris asal dari store bukti) — atau `skip` bila jangkarnya tidak ada.

    Dua alasan skip, dan keduanya sengaja BUKAN kegagalan: clone bersih memang tidak membawa
    `data/ryo.db` (9 MB bukti RYO tidak dipublikasikan, `docs/rules-compliance.md`), dan store
    yang tidak memuat SATU pun id fixture jelas bukan store asal fixture ini. Yang gagal adalah
    store yang memuat SEBAGIAN id: di situ "baris asal tidak ada" berarti provenansnya bohong.
    """
    if not EVIDENCE_DB.exists():
        pytest.skip(f"{EVIDENCE_DB} tidak ada — jangkar hanya bisa diuji di mesin yang memegang bukti")
    fixture = json.loads(path.read_text(encoding="utf-8"))
    # Label `source_db` dicetak DALAM kalimat afirmatif ("bukti nyata {source_label} id …",
    # `preflight.outage_fixture.provenance_lines`), tetapi ia teks bebas: berkas bisa menulis
    # "data/ryo.db (production evidence, audited)" tanpa perlawanan. Menuntutnya menunjuk store
    # yang BENAR-BENAR diperiksa di bawah membuat kalimat yang tercetak dan store yang
    # diverifikasi menjadi satu hal yang sama. Dinormalkan lebih dulu: relatif, `./`, dan absolut
    # menunjuk berkas yang sama, dan tes yang merah karena kosmetik akan dilonggarkan orang
    # berikutnya — yang justru menghapus jaminannya.
    labelled = (ROOT / Path(str(fixture["source_db"]))).resolve()
    assert labelled == EVIDENCE_DB.resolve(), (
        f"{path.name}: label `source_db` menunjuk {labelled}, tetapi jangkar ini memeriksa "
        f'{EVIDENCE_DB.resolve()} — kalimat "bukti nyata {fixture["source_db"]}" karena itu '
        "tidak berdasar"
    )
    rows = {int(row["source_id"]): row for row in fixture["snapshots"]}
    with Store(EVIDENCE_DB, read_only=True) as store:
        found = {row.id: row for row in store.get_snapshots(sorted(rows))}
    if not found:
        pytest.skip(f"{EVIDENCE_DB}: nol dari {len(rows)} id fixture — store lain, bukan store asal")
    missing = sorted(set(rows) - set(found))
    assert not missing, (
        f"{path.name}: source_id {missing} TIDAK ADA di {EVIDENCE_DB} — provenans mengarang id"
    )
    return rows, found


@pytest.mark.parametrize("path", ANCHORED_FIXTURES, ids=lambda p: p.stem)
def test_every_answering_envelope_is_identical_to_the_row_in_the_evidence_store(path: Path) -> None:
    """SEDANG: "salinan apa adanya `data/ryo.db`" diperiksa terhadap `data/ryo.db`, bukan terhadap berkas.

    Tiga kolom baris asal dituntut sekaligus, karena ketiganya ikut menentukan keputusan:
    `payload_hash` (isi envelope), `args_hash` (baris mana yang dipilih `latest_fresh`), dan
    `ts_utc` (seberapa tua ia terbaca, dan ia ikut di-hash `core.replay.HASHED_FIELDS`).
    """
    rows, found = _evidence_rows(path)
    anchored = 0
    for source_id, row in sorted(rows.items()):
        if is_shaped(row["envelope"]):
            continue
        origin = found[source_id]
        assert row["ts_utc"] == origin.ts_utc, f"{path.name} id {source_id}: ts_utc bergeser dari bukti"
        assert sha256_hex(row["args"]) == origin.args_hash, f"{path.name} id {source_id}: args bergeser"
        assert sha256_hex(row["envelope"]) == origin.payload_hash, (
            f"{path.name} id {source_id}: ISI envelope BUKAN salinan apa adanya baris {source_id} "
            f"di {EVIDENCE_DB}"
        )
        if SOURCE_HASH_KEY in row:
            # Jangkarnya dua arah: hash yang DIREKAM berkas juga harus sama dengan kolom aslinya,
            # jadi menulis ulang catatan provenans agar cocok dengan envelope yang diedit tidak
            # lagi cukup — ia harus juga cocok dengan store yang tidak dipegang berkas itu.
            assert row[SOURCE_HASH_KEY] == origin.payload_hash, (
                f"{path.name} id {source_id}: {SOURCE_HASH_KEY} yang direkam berkas tidak sama "
                f"dengan snapshots.payload_hash baris asal"
            )
        anchored += 1
    if any(not is_shaped(row["envelope"]) for row in rows.values()):
        assert anchored, f"{path.name}: nol envelope yang menjawab terjangkar"


@pytest.mark.parametrize("path", ANCHORED_FIXTURES, ids=lambda p: p.stem)
def test_every_shaped_envelope_really_differs_from_the_row_it_replaced(path: Path) -> None:
    """Sisi lain jangkar: envelope yang DIBENTUK wajib BERBEDA dari baris asalnya.

    Kalau sebuah baris mengaku sintetis tetapi isinya identik dengan bukti, "padam" itu tidak
    dibentuk — dan pengakuan yang salah arah sama menyesatkannya dengan klaim yang salah.
    """
    rows, found = _evidence_rows(path)
    for source_id, row in sorted(rows.items()):
        if not is_shaped(row["envelope"]):
            continue
        assert sha256_hex(row["envelope"]) != found[source_id].payload_hash, (
            f"{path.name} id {source_id}: envelope 'sintetis' identik dengan bukti"
        )
        assert SOURCE_HASH_KEY not in row, (
            f"{path.name} id {source_id}: baris yang dibentuk membawa {SOURCE_HASH_KEY} — "
            "itu klaim provenans atas envelope karangan"
        )
