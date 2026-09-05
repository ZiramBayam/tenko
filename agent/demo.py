"""`python -m agent.demo` — naskah demo spec §7 langkah 1-6 dari fixture offline (TASKS 3.4).

Perintah ini TIDAK membangun mesin baru. Ia merangkai yang sudah terbukti: fixture bertanggal
:mod:`agent.demo_fixture` (TASKS 3.4a), buku besar + `preflight` yang sudah ada, `agent.replay`
(TASKS 2.4), narasi `preflight.narrate` (TASKS 2.3), layar `ui.app` (TASKS 3.2), dan dua tanggal
:mod:`agent.two_dates` (TASKS 4.7) — lalu mencetaknya sebagai satu naskah yang bisa dibaca juri
dalam satu layar gulung.

**Kenapa "dua run identik" mungkin di sini padahal store append-only (ADR-006).** Menjalankan
naskah ini dua kali di atas DB yang SAMA memang mustahil identik: tiap tick menulis baris baru,
jadi `decisions.id` dan `ts_utc` bergeser. Karena itu tiap run membangun **DB SQLite baru** di
direktori sementara dari fixture JSON. `data/ryo.db` tidak dibuka sama sekali — dan itu tidak
diklaim, melainkan diukur: :func:`install_data_dir_audit` memasang `sys.addaudithook` yang mencatat
SETIAP `open`/`sqlite3.connect` yang jatuh di dalam `ROOT/data`, dan jumlahnya dicetak di keluaran.

**Normalisasi ditulis eksplisit, dan yang penting justru TIDAK dinormalkan.** Yang dibuang hanya
hal yang bergeser tiap run tanpa membawa arti: path direktori tmp, stempel waktu penulisan baris,
jam render layar UI, dan frasa umur relatif di layar ("… menit lalu") yang diukur terhadap jam
dinding. Yang dicetak apa adanya dan WAJIB identik antar-run: `decision_hash`, `params_hash`,
`decision_id`, `blocking`, status kesepuluh item, dan vonis replay. Menyembunyikan `decision_hash`
akan membuat "dua run identik" tidak berarti apa-apa.

**Batas yang tidak bisa ditutup di sini: §7 langkah 2 belum bisa dijalankan.** Langkah 2 menuntut
item 2 MERAH, dan itu hanya terjadi bila SATU tool wajib menjawab `unavailable` sementara yang lain
menjawab. Fixture "semua tool gagal" menghasilkan envelope `unavailable` + item 2 `unspoken` — itu
kalimat lain, dan memakainya berarti memperagakan sesuatu yang bukan naskahnya. Fixture yang benar
adalah TASKS 2.2c (milik `preflight/`, belum dikerjakan). Titik sambungnya sudah ada dan sudah
dites: `--step2-fixture PATH`. Sampai fixture itu lahir, langkah 2 dicetak sebagai MENUNGGU dengan
alasannya, bukan dipalsukan.

Pemakaian::

    make demo                                  # scripts/demo.sh -> perintah ini
    python -m agent.demo                        # sama, tanpa make
    python -m agent.demo --step2-fixture X.json # bila fixture TASKS 2.2c sudah ada

Tidak ada P&L di keluaran ini: rubric §8 tidak menilainya, dan yang diperagakan naskah §7 adalah
keputusan, penahannya, serta apakah plan menyentuh target/stop sebagai FAKTA.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.demo_fixture import (
    DEMO_FIXTURE_PATH,
    DemoFixture,
    DemoFixtureError,
    load_fixture,
    materialize,
    run_tick,
)
from agent.ledger import LedgerError, read_decision
from agent.params import DEFAULT_AGENT_PARAMS
from agent.replay import VERDICT_IDENTICAL, VERDICT_MISMATCH, replay
from core.store import Store, StoreError

#: Akar repo. Dipakai penjaga audit: apa pun di bawah `ROOT/data` tidak boleh dibuka demo ini.
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

#: Banner wajib (docs/rules-compliance.md): kata "SIMULATED" harus terlihat di setiap layar.
BANNER = "tenko demo — SIMULATED practice trading: no funds, no wallet, no execution"

#: Simbol yang dipakai langkah 1 dan langkah 4. Harus ada di watchlist fixture.
STEP1_SYMBOL = "SOL"

#: Berapa tick yang dijalankan langkah 3. Spec §7 menulis `make agent-tick` ×3.
N_TICKS = 3

#: Jumlah tool yang harus menjawab supaya langkah 2 BISA diperagakan (satu unavailable, sisanya
#: menjawab). Dipakai hanya untuk kalimat penjelas, bukan untuk melonggarkan apa pun.
STEP2_TASK = "TASKS 2.2c"


class DemoError(Exception):
    """Naskah tidak bisa dijalankan sampai selesai — sebabnya disebut, bukan disembunyikan."""


@dataclass(slots=True)
class DataDirAudit:
    """Pembukaan berkas di bawah `ROOT/data` yang tertangkap `sys.addaudithook`.

    Ini pengganti yang lebih kuat daripada membandingkan `mtime_ns`: perekam bisa hidup dan
    menulis `data/ryo.db` DI TENGAH demo tanpa demo ini menyentuhnya, sehingga `mtime` yang
    berubah bukan bukti apa-apa tentang demo. Yang dicatat di sini adalah perbuatan proses ini
    sendiri.
    """

    paths: list[str] = field(default_factory=list)

    def note(self, path: str) -> None:
        if path not in self.paths:
            self.paths.append(path)

    def line(self) -> str:
        if not self.paths:
            return (
                "penjaga: 0 berkas di data/ dibuka proses ini (sys.addaudithook atas open + sqlite3.connect)"
            )
        return "penjaga: PELANGGARAN — demo membuka " + ", ".join(sorted(self.paths))


_AUDIT = DataDirAudit()
_AUDIT_INSTALLED = False


def install_data_dir_audit() -> DataDirAudit:
    """Pasang audit hook sekali; kembalikan catatannya.

    Hook tidak bisa dicabut (itu memang gunanya), jadi pemasangannya dijaga bendera modul agar
    memanggil :func:`main` dua kali dalam satu proses (tes) tidak menumpuk hook.
    """
    global _AUDIT_INSTALLED
    if _AUDIT_INSTALLED:
        return _AUDIT
    prefix = str(DATA_DIR)

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event not in ("open", "sqlite3.connect"):
            return
        try:
            target = args[0]
            if isinstance(target, bytes):
                target = target.decode("utf-8", "replace")
            if not isinstance(target, str):
                return
            # `Store` membuka lewat URI (`file:...?mode=ro`); prefiksnya ikut dibandingkan
            # setelah dibuang, supaya jalur baca-saja tidak lolos dari penjaga.
            plain = target[5:].split("?", 1)[0] if target.startswith("file:") else target
            if str(Path(plain).resolve()).startswith(prefix):
                _AUDIT.note(plain)
        except Exception:  # noqa: BLE001 — exception di audit hook membunuh proses; diamkan
            return

    sys.addaudithook(hook)
    _AUDIT_INSTALLED = True
    return _AUDIT


def _head(fixture: DemoFixture) -> list[str]:
    """Kepala naskah: banner, sumber bukti, jam beku, dan normalisasi yang ditulis eksplisit.

    Hitungan penjaga TIDAK dicetak di sini melainkan di ringkasan akhir: di kepala naskah ia
    belum mengukur apa pun, dan "0 berkas dibuka" yang dicetak sebelum apa pun berjalan adalah
    lencana hijau yang tidak menjamin apa-apa.
    """
    return [
        BANNER,
        "naskah docs/spec.md §7 langkah 1-6 — seluruhnya dari fixture offline, nol panggilan RYO",
        "",
        f"fixture   : {fixture.name}",
        f"bukti     : {len(fixture.snapshots)} snapshot disalin dari {fixture.source_db} "
        f"id {list(fixture.source_ids)}",
        f"jam beku  : {fixture.now.isoformat()} (jam keputusan; BUKAN jam dinding)",
        f"params    : params_hash {fixture.params_hash}",
        "",
        "determinisme, ditulis eksplisit:",
        "  - tiap run membangun DB SQLite BARU di direktori sementara dari fixture JSON;",
        "    store append-only berarti dua run di atas DB yang sama TIDAK MUNGKIN identik.",
        "  - penjaga: sys.addaudithook mencatat setiap open/sqlite3.connect di bawah data/;",
        "    hitungannya dicetak di ringkasan akhir, setelah seluruh naskah selesai berjalan.",
        "  - DINORMALKAN (dibuang dari keluaran): path direktori tmp, stempel waktu penulisan baris,",
        "    jam render layar UI, dan frasa umur relatif di layar yang diukur ke jam dinding.",
        "  - TIDAK dinormalkan, wajib identik apa adanya: decision_hash, params_hash, decision_id,",
        "    blocking, status kesepuluh item, vonis replay.",
        "  - decision_id tidak perlu dinormalkan: DB baru tiap run, jadi id selalu mulai dari 1.",
        "  - tidak ada P&L di keluaran ini (rubric §8: P&L jangka pendek tidak dinilai).",
    ]


# --- langkah 1 ---------------------------------------------------------------------------


def step1(fixture: DemoFixture, db_path: Path, symbol: str = STEP1_SYMBOL) -> list[str]:
    """§7 langkah 1: satu envelope `preflight` — 10 item, GO/NO_GO, blocking, snapshot_ids.

    Dijalankan lewat fungsi skill yang sama dengan `make skill ARGS="preflight SOL"`, dengan satu
    perbedaan yang disebut di keluaran: jam kesegaran diambil dari fixture, bukan jam dinding.
    Tanpa itu keputusannya akan berubah setiap hari dan "dua run identik" tidak bisa dituntut.
    """
    # Impor lokal: `_envelope_text` SENGAJA fungsi privat milik `core.skill_cli` — memakai fungsi
    # yang sama dengan `make skill` adalah intinya, supaya baris JSON di bawah tidak bisa
    # menyimpang dari yang benar-benar dicetak CLI-nya.
    from core.skill_cli import _envelope_text
    from preflight.skill import preflight

    with Store(db_path, read_only=True) as store:
        result = preflight(symbol, store=store, now=fixture.now)
    data = result.data or {}
    items = data.get("items") or []
    lines = [
        "",
        '=== §7 langkah 1 — make skill ARGS="preflight SOL" ===',
        f"satu envelope RYO untuk {symbol}; jam kesegaran = jam beku fixture, bukan jam dinding",
        "",
        f"status={result.status}  data_mode={result.data_mode}  "
        f"as_of={result.as_of.isoformat() if result.as_of else None}",
        f"decision={data.get('decision')}  blocking={', '.join(data.get('blocking') or []) or '-'}",
        f"snapshot_ids={data.get('snapshot_ids')}  decision_hash={data.get('decision_hash')}",
        f"n_items={data.get('n_items')} (green {data.get('n_green')}, red {data.get('n_red')}, "
        f"unspoken {data.get('n_unspoken')})",
        "",
        "  sepuluh baris panggilan (tunjuk -> sebut):",
    ]
    for item in items:
        source = item.get("source") or {}
        pointer = f"{source.get('tool', '?')}.{source.get('field', '?')}"
        lines.append(
            f"    {str(item.get('id')):<20}{str(item.get('status')):<10}"
            f"{pointer:<70}snapshot_id={source.get('snapshot_id')}"
        )
    envelope = _envelope_text(result)
    lines.extend(
        [
            "",
            f"  envelope kanonik, persis byte yang ditulis `make skill` ({len(envelope)} byte):",
            f"  {envelope}",
        ]
    )
    return lines


# --- langkah 2 ---------------------------------------------------------------------------


def step2(step2_fixture: str | None) -> list[str]:
    """§7 langkah 2: satu tool wajib `unavailable` -> item 2 merah, keputusan tetap dihitung.

    Tanpa fixture TASKS 2.2c langkah ini DICETAK SEBAGAI MENUNGGU. Yang tidak dilakukan di sini,
    dan itu intinya: fixture "semua tool gagal" TIDAK dipakai sebagai gantinya — ia menghasilkan
    envelope `unavailable` dengan item 2 `unspoken`, bukan item 2 merah, dan memperagakannya
    berarti mengaku memperagakan naskah yang sebenarnya belum bisa diperagakan.
    """
    lines = [
        "",
        "=== §7 langkah 2 — satu tool wajib unavailable, sisanya menjawab ===",
    ]
    if not step2_fixture:
        lines.extend(
            [
                f"MENUNGGU {STEP2_TASK} — langkah ini TIDAK dijalankan, dan tidak dipalsukan.",
                "  yang dituntut naskah: SATU tool wajib menjawab `unavailable` sementara yang lain",
                "  menjawab, sehingga item 08 menjadi `unspoken` dan item 02 (kelengkapan) MERAH,",
                "  lalu keputusan tetap dihitung tanpa exception.",
                f"  yang belum ada: fixture bertanggal dengan bentuk itu ({STEP2_TASK}, milik preflight/).",
                "  kenapa fixture 'semua tool gagal' tidak dipakai sebagai pengganti: ia menghasilkan",
                "  envelope status=unavailable dengan item 02 `unspoken` — kalimat lain, bukan naskah ini.",
                "  titik sambung sudah ada dan sudah dites: `python -m agent.demo --step2-fixture PATH`.",
            ]
        )
        return lines
    fixture = load_fixture(step2_fixture)
    with tempfile.TemporaryDirectory(prefix="tenko-demo-step2-") as tmp:
        db_path = Path(tmp) / "step2.db"
        materialize(fixture, db_path)
        lines.extend(step1(fixture, db_path, fixture.symbols[0] if fixture.symbols else STEP1_SYMBOL))
    return lines


# --- langkah 3 ---------------------------------------------------------------------------


def _tamper_one_byte(payload: str) -> tuple[str, int, str, str]:
    """Ganti SATU karakter angka di dalam payload — deterministik, panjang tetap.

    Deterministik karena posisinya dicari dari isi payload beku, bukan diacak: digit pertama yang
    bukan `7` menjadi `7`. Itu cukup untuk menggeser `payload_hash` yang dijaga `core/replay.py`.
    """
    for index, char in enumerate(payload):
        if char.isdigit() and char != "7":
            return payload[:index] + "7" + payload[index + 1 :], index, char, "7"
    raise DemoError("payload beku tidak memuat satu digit pun; tamper 1 byte tidak bisa diperagakan")


def _tamper_report(db_path: Path, decision_id: int, snapshot_id: int, tmp: Path) -> list[str]:
    """Salin DB, ubah satu byte pada satu snapshot beku, lalu putar ulang keputusan yang sama."""
    copy_path = tmp / "tampered.db"
    copy_path.write_bytes(db_path.read_bytes())
    connection = sqlite3.connect(str(copy_path))
    try:
        row = connection.execute("SELECT payload_json FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        if row is None:
            raise DemoError(f"snapshot {snapshot_id} tidak ada di salinan DB")
        tampered, index, before, after = _tamper_one_byte(row[0])
        connection.execute("UPDATE snapshots SET payload_json = ? WHERE id = ?", (tampered, snapshot_id))
        connection.commit()
    finally:
        connection.close()
    with Store(copy_path, read_only=True) as store:
        report = replay(decision_id, store, DEFAULT_AGENT_PARAMS.preflight)
    return [
        f"  tamper: 1 byte diubah pada SALINAN DB — snapshot_id={snapshot_id}, "
        f"payload_json[{index}] {before!r} -> {after!r} (panjang tetap)",
        f"  make replay ID={decision_id} atas salinan itu -> {report.verdict}",
        f"    {report.detail}",
    ]


def step3(fixture: DemoFixture, db_path: Path, tmp: Path) -> tuple[list[str], list[Any]]:
    """§7 langkah 3: `make agent-tick` ×3 -> ledger ≥1 GO + ≥1 NO_GO; replay IDENTICAL; tamper MISMATCH."""
    lines = [
        "",
        f"=== §7 langkah 3 — make agent-tick ×{N_TICKS}, lalu make replay ID= ===",
        "tiga tick di atas bukti beku yang sama: keputusan yang sama harus lahir tiga kali",
    ]
    reports: list[Any] = []
    first_hash: dict[str, str] = {}
    for number in range(1, N_TICKS + 1):
        if number == 1:
            report = run_tick(fixture, db_path)
        else:
            with Store(db_path) as store:
                from agent.main import tick

                report = tick(store, fixture.symbols, DEFAULT_AGENT_PARAMS, now=fixture.now)
        reports.append(report)
        lines.append("")
        lines.append(
            f"  tick {number}: {report.n_go} GO, {report.n_no_go} NO_GO, "
            f"{report.n_rejected} rejected, {report.n_insufficient} tanpa bukti — semua SIMULATED"
        )
        for note in report.lines:
            lines.append(f"    {note}")
        for outcome in report.outcomes:
            lines.append(f"    {outcome.line()}")
            if not outcome.decision_hash:
                continue
            previous = first_hash.setdefault(outcome.symbol, outcome.decision_hash)
            if number == 1:
                note = ""
            else:
                same = previous == outcome.decision_hash
                note = " (sama dengan tick 1)" if same else " (BERBEDA dari tick 1 — determinisme rusak)"
            lines.append(f"    {outcome.symbol:<6} decision_hash={outcome.decision_hash}{note}")

    go = next((o for o in reports[0].outcomes if o.decision == "GO" and o.decision_id), None)
    no_go = next((o for o in reports[0].outcomes if o.decision == "NO_GO" and o.decision_id), None)
    if go is None or no_go is None:
        raise DemoError(
            "langkah 3 menuntut ≥1 GO DAN ≥1 NO_GO dalam satu tick; fixture ini tidak menghasilkan keduanya"
        )
    # Fakta keluar posisi, BUKAN P&L (rubric §8): yang dilaporkan adalah apakah harga perekam
    # menyentuh target atau stop pada jam beku, bukan berapa untung/ruginya.
    settlements = [entry for report in reports for entry in report.settlements]
    closed = [entry for entry in settlements if entry.closed]
    lines.extend(
        [
            "",
            f"  keluar posisi pada jam beku (harga dari store perekam, tanpa P&L): {len(closed)} ditutup, "
            f"{reports[-1].open_after} masih terbuka",
        ]
    )
    for entry in closed:
        lines.append(f"    decision_id={entry.decision_id} {entry.symbol}: {entry.reason}")
    lines.extend(
        [
            "",
            f"  ledger: GO {go.symbol} (decision_id={go.decision_id}) + "
            f"NO_GO {no_go.symbol} (decision_id={no_go.decision_id}, blocking: "
            f"{', '.join(no_go.blocking) or '-'}) — syarat langkah 3 terpenuhi",
        ]
    )
    for outcome in (go, no_go):
        with Store(db_path, read_only=True) as store:
            report = replay(int(outcome.decision_id or 0), store, DEFAULT_AGENT_PARAMS.preflight)
        lines.append(f"  make replay ID={outcome.decision_id} ({outcome.symbol}) -> {report.verdict}")
        lines.append(f"    {report.detail}")
        if report.verdict != VERDICT_IDENTICAL:
            raise DemoError(f"replay decision_id={outcome.decision_id} bukan {VERDICT_IDENTICAL}")
    frozen = read_decision(db_path, int(go.decision_id or 0))
    if frozen is None:
        raise DemoError(f"baris decisions {go.decision_id} tidak terbaca kembali")
    ids = frozen.snapshot_ids()
    if not ids:
        raise DemoError("keputusan GO tidak menyebut satu snapshot beku pun")
    tamper_lines = _tamper_report(db_path, int(go.decision_id or 0), ids[0], tmp)
    lines.extend(tamper_lines)
    if VERDICT_MISMATCH not in tamper_lines[1]:
        raise DemoError(f"tamper 1 byte tidak menghasilkan {VERDICT_MISMATCH}")
    return lines, reports


# --- langkah 4 ---------------------------------------------------------------------------


def step4(fixture: DemoFixture, db_path: Path, decision_id: int) -> list[str]:
    """§7 langkah 4: kartu menampilkan panggilan per item; LLM mati -> `narration_unavailable`.

    `env={}` bukan kenyamanan tes: ia adalah "matikan LLM (env kosong)" apa adanya, dan gerbangnya
    menolak SEBELUM klien dibangun, jadi tidak ada satu pun panggilan jaringan dari naskah ini.
    """
    from preflight.narrate import attach, narrate
    from preflight.skill import preflight

    row = read_decision(db_path, decision_id)
    if row is None:
        raise DemoError(f"kartu decision_id={decision_id} tidak ada")
    card = row.card()
    skill = card.get("skill") if isinstance(card, dict) else {}
    data = (skill or {}).get("data") or {}
    items = data.get("items") or []
    lines = [
        "",
        "=== §7 langkah 4 — kartu per item, dan narasi yang jujur saat LLM dimatikan ===",
        f"kartu decision_id={decision_id} ({row.symbol}) dibaca dari decisions.card_json — sumber yang",
        "sama dengan yang dibaca UI; kartunya tidak dihitung ulang untuk dicetak",
        "",
        f"  {'item':<20}{'status':<10}{'tunjuk':<70}sebut",
    ]
    for item in items:
        source = item.get("source") or {}
        pointer = f"{source.get('tool', '?')}.{source.get('field', '?')}"
        reason = str(item.get("reason") or "")
        head = reason.split(" -> ")[0][:96]
        lines.append(f"  {str(item.get('id')):<20}{str(item.get('status')):<10}{pointer:<70}{head}")

    with Store(db_path, read_only=True) as store:
        result = preflight(row.symbol, store=store, now=fixture.now)
    # Jam beku, bukan jam dinding: `decision_hash` ikut menghitung status item, dan item 01
    # mengukur kesegaran — envelope dengan jam dinding akan menghasilkan hash yang berbeda
    # setiap hari, dan baris perbandingan di bawah berhenti bisa dibaca.
    narration = narrate(result, env={})
    attached = attach(result, narration)
    before = (result.data or {}).get("decision_hash")
    after = (attached.data or {}).get("decision_hash")
    lines.extend(
        [
            "",
            f"  matikan LLM (env kosong) -> narration.status = {narration.status}",
            f"    n_lines={narration.n_lines} dari n_items={narration.n_items}; sebab disebut apa adanya:",
            f"    {narration.reason}",
            f"  decision_hash sebelum narasi ditempel: {before}",
            f"  decision_hash sesudah narasi ditempel : {after}",
            f"  -> {'SAMA' if before == after else 'BERGESER'}: narasi hidup di summary, keputusan di data",
        ]
    )
    if before != after:
        raise DemoError("narasi menggeser decision_hash — itu melanggar ADR-003")
    return lines


# --- langkah 5 ---------------------------------------------------------------------------


def step5(db_path: Path, decision_id: int) -> list[str]:
    """§7 langkah 5: Layar A + Layar B dirender dari aplikasi yang sama dengan `make ui`.

    Dirender in-process (klien tes ASGI) alih-alih lewat uvicorn: yang diperagakan naskah adalah
    ISI layar, dan menyalakan port di tengah demo hanya menambah bagian yang bisa gagal. HTML-nya
    sendiri TIDAK dicetak — di dalamnya ada jam render dan frasa umur relatif terhadap jam dinding.
    Yang dicetak adalah fakta yang tidak bergeser antar-run.
    """
    import re

    from fastapi.testclient import TestClient

    import ui.app as ui_app

    previous = ui_app.app.state.db_path
    ui_app.app.state.db_path = str(db_path)
    try:
        with TestClient(ui_app.app) as client:
            screen_a = client.get("/ui")
            screen_b = client.get(f"/ui/decision/{decision_id}")
    finally:
        ui_app.app.state.db_path = previous

    def title(html: str) -> str:
        found = re.findall(r"<title>(.*?)</title>", html)
        return found[0] if found else "-"

    order = re.findall(r'href="/ui/decision/(\d+)"', screen_a.text)
    item_ids = sorted(set(re.findall(r"\b\d\d_[a-z_]+\b", screen_b.text)))
    lines = [
        "",
        "=== §7 langkah 5 — make ui: Layar A (watchlist) + Layar B (kartu panggilan) ===",
        "dirender dari ui.app yang sama dengan `make ui`, in-process, atas DB fixture;",
        "HTML tidak dicetak: di dalamnya ada jam render dan umur relatif terhadap jam dinding",
        "",
        f"  Layar A  GET /ui                    -> HTTP {screen_a.status_code}  "
        f"{screen_a.headers.get('content-type')}",
        f"    judul   : {title(screen_a.text)}",
        f"    banner SIMULATED terlihat: {'SIMULATED' in screen_a.text}",
        f"    urutan baris (daya veto lebih dulu): decision_id {', '.join(order) or '-'}",
        f"  Layar B  GET /ui/decision/{decision_id}         -> HTTP {screen_b.status_code}",
        f"    judul   : {title(screen_b.text)}",
        f"    banner SIMULATED terlihat: {'SIMULATED' in screen_b.text}",
        f"    baris panggilan di kartu: {len(item_ids)} -> {', '.join(item_ids)}",
        f"    aria-label: {screen_b.text.count('aria-label')} · tautan fokusable: "
        f"{screen_b.text.count('<a ')} · handler hover/onclick: "
        f"{screen_b.text.count('onclick') + screen_b.text.count('onmouseover')}",
    ]
    if screen_a.status_code != 200 or screen_b.status_code != 200:
        raise DemoError("layar UI tidak menjawab 200 atas DB fixture")
    return lines


# --- langkah 6 ---------------------------------------------------------------------------


def step6() -> list[str]:
    """§7 langkah 6: dua tanggal, simbol sama, keputusan berbeda — `make two-dates` (TASKS 4.7)."""
    from agent.two_dates import TwoDatesError, build_pair, render

    lines = [
        "",
        "=== §7 langkah 6 — dua tanggal, simbol sama, keputusan berbeda (make two-dates) ===",
    ]
    try:
        early, late = build_pair()
    except TwoDatesError as exc:
        raise DemoError(f"langkah 6: {exc}") from exc
    # Baris pertama `render` adalah banner tick; naskah ini sudah punya bannernya sendiri.
    lines.extend(render(early, late)[1:])
    return lines


# --- naskah ------------------------------------------------------------------------------


def demo_lines(fixture_path: str | Path = DEMO_FIXTURE_PATH, step2_fixture: str | None = None) -> list[str]:
    """Seluruh naskah §7 sebagai daftar baris. Deterministik atas fixture yang sama."""
    audit = install_data_dir_audit()
    fixture = load_fixture(fixture_path)
    if step2_fixture:
        # Gagal cepat: fixture langkah 2 dibaca SEBELUM tiga tick berjalan, supaya path yang salah
        # ketik tidak baru ketahuan setelah setengah naskah tercetak.
        load_fixture(step2_fixture)
    with tempfile.TemporaryDirectory(prefix="tenko-demo-") as tmp_name:
        tmp = Path(tmp_name)
        db_path = tmp / "demo.db"
        lines = _head(fixture)
        step3_lines, reports = step3(fixture, db_path, tmp)
        # Kartu untuk langkah 4 & 5 diambil dari tick TERAKHIR, bukan tick pertama: Layar A
        # menampilkan keputusan terbaru, jadi kartu tick pertama akan dibuka di Layar B tanpa
        # pernah muncul di daftar Layar A — dua layar yang tidak saling menunjuk.
        go = next((o for o in reports[-1].outcomes if o.decision == "GO" and o.decision_id), None)
        if go is None:
            raise DemoError("tidak ada keputusan GO di tick terakhir")
        card_id = int(go.decision_id or 0)
        lines.extend(step1(fixture, db_path))
        lines.extend(step2(step2_fixture))
        lines.extend(step3_lines)
        lines.extend(step4(fixture, db_path, card_id))
        lines.extend(step5(db_path, card_id))
    lines.extend(step6())
    lines.extend(
        [
            "",
            "=== ringkasan naskah §7 ===",
            "  langkah 1 dijalankan · langkah 3 dijalankan · langkah 4 dijalankan · "
            "langkah 5 dijalankan · langkah 6 dijalankan",
            (
                "  langkah 2 dijalankan dari fixture yang diberikan --step2-fixture"
                if step2_fixture
                else f"  langkah 2 MENUNGGU {STEP2_TASK} (fixture 'satu tool wajib unavailable' belum ada)"
            ),
            f"  {audit.line()}",
            "  dua run `make demo` harus byte-identik:",
            "    make demo > a; make demo > b; diff a b",
            "  semua trade di atas adalah PRACTICE/SIMULATED: nol dana, nol wallet, nol eksekusi",
        ]
    )
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent.demo",
        description="Naskah demo spec §7 langkah 1-6 dari fixture offline (SIMULATED).",
    )
    parser.add_argument("--fixture", default=str(DEMO_FIXTURE_PATH), help="fixture demo langkah 1/3/4/5")
    parser.add_argument(
        "--step2-fixture",
        default=None,
        help=f"fixture 'satu tool wajib unavailable' ({STEP2_TASK}); tanpa ini langkah 2 dicetak MENUNGGU",
    )
    return parser


def _db_watch() -> tuple[int, int] | None:
    """`(mtime_ns, size)` `data/ryo.db` bila ada — hanya untuk peringatan ke STDERR.

    Sengaja TIDAK masuk stdout: perekam yang hidup menulis berkas itu kapan saja, jadi angkanya
    bergeser tanpa ada hubungannya dengan demo. Bukti bahwa demo tidak menyentuhnya adalah audit
    hook, bukan `mtime`.
    """
    target = DATA_DIR / "ryo.db"
    try:
        stat = target.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    before = _db_watch()
    try:
        lines = demo_lines(args.fixture, args.step2_fixture)
    except (DemoError, DemoFixtureError, LedgerError, StoreError) as exc:
        print(f"demo: {exc}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    after = _db_watch()
    if before is not None and after is not None and before != after:
        print(
            "demo: data/ryo.db berubah selama demo berjalan (perekam menulis?); demo ini tidak "
            "membukanya — lihat baris penjaga audit di stdout",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
