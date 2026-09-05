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

**§7 langkah 2 berjalan dari fixture TASKS 2.2c — dan padamnya diakui SINTETIS.** Langkah 2
menuntut item 02 MERAH, dan itu hanya terjadi bila SATU tool wajib menjawab `unavailable`
sementara yang lain menjawab. Fixture berbentuk itu sekarang ada (`preflight/fixtures/
outage-one-tool-2026-09-05.json`) dan dipakai SECARA BAWAAN. Tetapi `status: "unavailable"` BELUM
PERNAH teramati dari RYO (`docs/api-facts.md`: 0 dari 3645 envelope, id <= 3645 per 6 Sep 2026;
enumnya dari Builder MCP Guide, bukan dari sampel), jadi envelope padam itu **dibentuk**, bukan
diterima. Karena `preflight`
tidak meneruskan `warnings` envelope padam ke envelope hasil, kata `SYNTHETIC` milik fixture tidak
pernah sampai ke layar juri — maka naskah ini mencetak pengakuannya sendiri.

**Pengakuan itu TIDAK diturunkan dari klaim berkas yang ia audit.** Berkas yang envelopenya
benar-benar sudah ditulis ulang menjadi `status: unavailable` tetapi blok `synthetic`-nya diedit
kosong akan membuat naskah menyebut envelope karangan sebagai "salinan apa adanya bukti nyata" —
klaim afirmatif yang SALAH, lebih buruk daripada diam. Karena itu himpunan envelope sintetis
dihitung dari envelope yang BENAR-BENAR dimuat (:func:`preflight.outage_fixture.is_shaped`:
`status: unavailable`, atau `warnings` memuat `SYNTHETIC`), lalu DIBANDINGKAN dengan blok
`synthetic` berkas; tiap selisih dicetak sebagai KETIDAKCOCOKAN. Predikatnya diimpor, bukan
disalin: naskah ini dan CLI `preflight.outage_fixture` menghakimi berkas yang sama, dan dua
definisi yang menyimpang berarti dua vonis atas satu berkas.

**BENTUK yang jujur tidak menjamin ISI yang jujur — jadi isinya ikut diperiksa.** `status` dan
`warnings` yang rapi tidak mengatakan apa pun tentang angka di dalam envelope: mengubah
`risk_reward` pada envelope NYATA menggeser item 09 menjadi merah dan `decision_hash` ikut
bergeser, tanpa satu peringatan pun, sementara baris "N envelope yang MENJAWAB adalah salinan apa
adanya" tetap tercetak — kembali menjadi klaim afirmatif yang salah. Karena itu tiap baris fixture
membawa `source_payload_hash` (kolom `snapshots.payload_hash` baris asalnya, direkam saat
penyalinan oleh `agent.demo_fixture.capture`), dan
:func:`preflight.outage_fixture.verify_provenance` menghitung ulang sha256 kanonik envelope yang
dimuat untuk membandingkannya. Hash yang dibandingkan, bukan `data/ryo.db` yang dibuka ulang:
penjaga audit di bawah menuntut NOL berkas `data/` dibuka, dan verifikasi yang melanggarnya akan
menukar satu klaim dengan klaim lain. Berlaku untuk fixture UTAMA (kepala naskah) maupun fixture
langkah 2. Baris tanpa hash asal tidak dicap bohong: klaimnya DITURUNKAN menjadi "berkas MENGAKU
menyalin, TIDAK DIVERIFIKASI di sini".

Kata-kata pengakuan (`task`, `derived_from`, `why`) tetap dikutip dari berkas, tetapi
lewat :func:`_safe` — teks berkas tidak boleh menimpa atau memalsukan baris pengakuan. Fixture yang
tidak mengaku sintetis tidak pernah dicetak sebagai sintetis; yang mengaku tidak pernah
disembunyikan; yang berbohong ketahuan.

Pemakaian::

    make demo                                  # scripts/demo.sh -> perintah ini
    python -m agent.demo                        # sama, tanpa make: langkah 2 ikut jalan
    python -m agent.demo --step2-fixture X.json # fixture langkah 2 sendiri
    python -m agent.demo --step2-fixture ""     # tanpa langkah 2: dicetak MENUNGGU, bukan dipalsukan

Tidak ada P&L di keluaran ini: rubric §8 tidak menilainya, dan yang diperagakan naskah §7 adalah
keputusan, penahannya, serta apakah plan menyentuh target/stop sebagai FAKTA.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.demo_fixture import (
    DEMO_FIXTURE_PATH,
    DemoFixture,
    DemoFixtureError,
    FixtureSnapshot,
    load_fixture,
    materialize,
    run_tick,
)
from agent.ledger import LedgerError, read_decision
from agent.params import DEFAULT_AGENT_PARAMS
from agent.replay import VERDICT_IDENTICAL, VERDICT_MISMATCH, replay
from core.skill import clean_text
from core.store import Store, StoreError
from preflight.outage_fixture import is_shaped, provenance_lines, verify_provenance

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

#: Fixture bawaan langkah 2 (TASKS 2.2c, milik `preflight/`): satu tool wajib padam, sisanya
#: menjawab. Bawaannya hidup DI SINI, bukan hanya di `scripts/demo.sh`, supaya `python -m agent.demo`
#: polos dan `make demo` memperagakan naskah yang sama.
DEFAULT_STEP2_FIXTURE = ROOT / "preflight" / "fixtures" / "outage-one-tool-2026-09-05.json"

#: Judul bawaan langkah 1. Judulnya parameter karena langkah 2 memakai fungsi yang sama; tanpa itu
#: layar juri membaca "§7 langkah 1" dua kali dan langkah 2 kehilangan namanya.
STEP1_HEADING = '=== §7 langkah 1 — make skill ARGS="preflight SOL" ==='


#: Panjang maksimum teks asal-BERKAS yang boleh dicetak naskah ini. Bukan gaya: satu nilai
#: 100 kB di `name`/`why` menenggelamkan pengakuan yang justru harus terbaca.
FIELD_LIMIT = 200
WHY_LIMIT = 800

#: Kosakata yang hanya boleh diucapkan NASKAH ini sendiri. Berkas fixture tidak boleh mengucapkan
#: "PENGAKUAN" atau "KETIDAKCOCOKAN": setelah aksara kendali dinetralkan sebuah nilai memang
#: tinggal satu baris, tetapi mata juri (dan `grep -q PENGAKUAN` yang dipakai sebagai bukti AC)
#: masih bisa dibelokkan oleh kalimat yang ditanam di dalam nilai.
_RESERVED = re.compile(r"PENGAKUAN|KETIDAKCOCOKAN|SINTETIS|PELANGGARAN|MENUNGGU", re.IGNORECASE)

#: `source_id` yang diklaim blok `synthetic` berkas, mis. `"foo (source_id 2492)"`.
_CLAIMED_SOURCE_ID = re.compile(r"source_id\s*(\d+)")


class DemoError(Exception):
    """Naskah tidak bisa dijalankan sampai selesai — sebabnya disebut, bukan disembunyikan."""


def _safe(value: object, limit: int = FIELD_LIMIT) -> str:
    """Teks asal-BERKAS dalam bentuk yang tidak bisa menimpa maupun memalsukan baris naskah ini.

    Sama tiga lapisnya dengan :func:`agent.replay._safe`, dan sebabnya sama: nilai di berkas
    fixture tidak dilindungi hash apa pun, sedangkan ia dicetak ke layar yang dibaca juri.

    1. :func:`core.skill.clean_text` — sanitizer tunggal repo (TASKS 1.5b): setiap aksara tak
       tercetak menjadi ``\\uXXXX`` yang TERLIHAT. Itu mematikan `\\x1b[2J` (menghapus layar),
       `\\x1b[10A\\x1b[1G` (menimpa baris pengakuan di atasnya), dan `\\r\\n` (menyisipkan baris
       palsu) — mekanisme yang sama dengan temuan KRITIS di jalur payload tool.
    2. batas panjang, dibawa `clean_text` sendiri berikut penanda potongnya.
    3. kosakata naskah dinetralkan: `PENGAKUAN` di dalam sebuah NILAI ditulis ``P\\x45NGAKUAN``,
       supaya berkas tidak bisa mengarang kalimat kepatuhan atas nama naskah ini.
    """
    text = clean_text(str(value), limit)
    return _RESERVED.sub(lambda m: f"{m.group()[0]}\\x{ord(m.group()[1]):02x}{m.group()[2:]}", text)


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


def _head(fixture: DemoFixture, fixture_path: str | Path) -> list[str]:
    """Kepala naskah: banner, sumber bukti (DIPERIKSA), jam beku, dan normalisasi yang eksplisit.

    "14 snapshot disalin dari `data/ryo.db`" adalah klaim provenans, dan sampai kalimat ini
    diperiksa ia hanya klaim berkas tentang dirinya sendiri: satu angka yang diketik ulang di
    dalam envelope nyata (mis. `risk_reward`) menggeser keputusan dan `decision_hash` tanpa
    satu peringatan pun, sementara barisnya tetap tercetak. Karena itu tiap envelope yang
    menjawab dibandingkan dengan sha256 baris asalnya (`preflight.outage_fixture`) dan yang
    dicetak adalah HASIL pemeriksaan — "DIVERIFIKASI" hanya untuk yang cocok, "TIDAK
    DIVERIFIKASI" untuk yang tidak bisa diperiksa, KETIDAKCOCOKAN untuk yang berselisih.
    Hash-nya direkam saat penyalinan, jadi pemeriksaan ini tidak membuka satu berkas pun di
    bawah `data/` (penjaga :func:`install_data_dir_audit` yang mengukurnya, bukan janji ini).

    Hitungan penjaga TIDAK dicetak di sini melainkan di ringkasan akhir: di kepala naskah ia
    belum mengukur apa pun, dan "0 berkas dibuka" yang dicetak sebelum apa pun berjalan adalah
    lencana hijau yang tidak menjamin apa-apa.
    """
    raw = _fixture_json(fixture_path)
    return [
        BANNER,
        "naskah docs/spec.md §7 langkah 1-6 — seluruhnya dari fixture offline, nol panggilan RYO",
        "",
        f"fixture   : {_safe(fixture.name)}",
        f"bukti     : {len(fixture.snapshots)} snapshot yang berkasnya sebut disalin dari "
        f"{_safe(fixture.source_db)} id {list(fixture.source_ids)}",
        *provenance_lines(verify_provenance(raw), _safe(fixture.source_db), indent="            "),
        f"jam beku  : {fixture.now.isoformat()} (jam keputusan; BUKAN jam dinding)",
        f"params    : params_hash {_safe(fixture.params_hash)}",
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


def step1(
    fixture: DemoFixture,
    db_path: Path,
    symbol: str = STEP1_SYMBOL,
    heading: str = STEP1_HEADING,
) -> list[str]:
    """§7 langkah 1: satu envelope `preflight` — 10 item, GO/NO_GO, blocking, snapshot_ids.

    Dijalankan lewat fungsi skill yang sama dengan `make skill ARGS="preflight SOL"`, dengan satu
    perbedaan yang disebut di keluaran: jam kesegaran diambil dari fixture, bukan jam dinding.
    Tanpa itu keputusannya akan berubah setiap hari dan "dua run identik" tidak bisa dituntut.

    `heading` ada karena langkah 2 memakai fungsi ini apa adanya: tanpa parameter itu, layar juri
    membaca judul "§7 langkah 1" untuk kedua langkah dan langkah 2 kehilangan namanya.
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
        heading,
        f"satu envelope RYO untuk {_safe(symbol)}; jam kesegaran = jam beku fixture, bukan jam dinding",
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


def _fixture_json(path: str | Path) -> dict[str, Any]:
    """Isi mentah berkas fixture. Gagal baca = dict kosong; `load_fixture` yang bersuara duluan."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _synthetic_note(path: str | Path) -> dict[str, Any] | None:
    """Blok `synthetic` berkas fixture bila fixture itu mengaku dibentuk; `None` bila tidak."""
    note = _fixture_json(path).get("synthetic")
    return note if isinstance(note, dict) else None


def _is_shaped(snapshot: FixtureSnapshot) -> bool:
    """Apakah envelope INI berbentuk padam yang dibentuk — satu predikat, dipakai bersama.

    Isinya SENGAJA tidak ditulis di sini melainkan di :func:`preflight.outage_fixture.is_shaped`:
    naskah ini dan CLI `preflight.outage_fixture` menghakimi berkas yang SAMA, dan dua salinan
    predikat yang menyimpang pernah membuat berkas ber-warning `SYNTHETIC` divonis KETIDAKCOCOKAN
    di sini sementara CLI itu keluar rc=0 dan menyebutnya salinan bukti nyata. Arah impornya
    `agent` → `preflight` (bukan sebaliknya), jadi definisinya tinggal di sana.
    """
    return is_shaped(snapshot.envelope)


def _claimed_synthetic_ids(raw: dict[str, Any], note: dict[str, Any] | None) -> set[int]:
    """`source_id` yang BERKASnya akui dibentuk — dari dua tempat berkas itu menyatakannya.

    Keduanya diambil (union) karena keduanya adalah klaim: daftar
    `synthetic.envelopes_rewritten_to_unavailable` dan bendera `snapshots[*].synthetic`. Berkas
    yang mengaku di satu tempat dan menyangkal di tempat lain tetap terhitung mengaku, sehingga
    selisihnya terhadap envelope yang benar-benar dimuat tetap bisa dihitung.
    """
    claimed: set[int] = set()
    for entry in (note or {}).get("envelopes_rewritten_to_unavailable") or []:
        claimed.update(int(found) for found in _CLAIMED_SOURCE_ID.findall(str(entry)))
    rows = raw.get("snapshots")
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("synthetic") and isinstance(row.get("source_id"), int):
            claimed.add(int(row["source_id"]))
    return claimed


def _disclosure_mismatch(shaped: set[int], claimed: set[int]) -> list[str]:
    """Selisih antara envelope yang DIMUAT dan yang DIAKUI berkas — dicetak mencolok, bukan diam.

    Blok `synthetic` sebuah berkas tidak bisa mengaudit berkas itu sendiri: fixture yang envelope
    `unavailable`-nya ada tetapi blok pengakuannya diedit menjadi kosong akan membuat naskah ini
    mencetak klaim afirmatif yang SALAH ("id 2492 adalah salinan apa adanya bukti nyata") — lebih
    buruk daripada diam. Karena itu himpunan sintetis dihitung dari envelope yang benar-benar
    dimuat (:func:`_is_shaped`), lalu DIBANDINGKAN dengan klaim berkas; tiap selisih dicetak.
    """
    undisclosed = sorted(shaped - claimed)
    overclaimed = sorted(claimed - shaped)
    if not undisclosed and not overclaimed:
        return []
    lines = [
        "  KETIDAKCOCOKAN — blok `synthetic` BERKAS ini tidak cocok dengan envelope yang benar-benar",
        "  dimuat; yang dipakai naskah di bawah adalah hasil PEMERIKSAAN envelope, bukan klaim berkas:",
    ]
    if undisclosed:
        lines.append(
            "    berkas TIDAK mengaku membentuk source_id "
            f"{', '.join(str(entry) for entry in undisclosed)} — padahal envelopenya berstatus "
            "unavailable atau ber-warning SYNTHETIC"
        )
    if overclaimed:
        lines.append(
            "    berkas mengaku membentuk source_id "
            f"{', '.join(str(entry) for entry in overclaimed)} — padahal envelope itu tidak berbentuk padam"
        )
    return lines


def _step2_disclosure(path: str | Path, fixture: DemoFixture) -> list[str]:
    """Pengakuan asal-usul envelope padam langkah 2 — DIBACA dari fixture, bukan ditulis di sini.

    Ini kepatuhan, bukan kosmetik. §7 langkah 2 hanya bisa diperagakan bila satu tool wajib
    menjawab `unavailable`, dan `docs/api-facts.md` mencatat status itu BELUM PERNAH teramati dari
    RYO (0 dari 3645 envelope, id <= 3645 per 6 Sep 2026; enumnya dari Builder MCP Guide).
    Fixture TASKS 2.2c sudah menandai
    dirinya jujur — blok `synthetic`, bendera per baris snapshot, kata `SYNTHETIC` di `warnings`
    envelope sumber — tetapi `preflight` tidak meneruskan `warnings` envelope padam ke envelope
    hasil, jadi tanpa baris-baris ini layar juri tidak pernah melihat bahwa padamnya dibuat-buat.
    Memperagakan pemadaman yang dibentuk tanpa mengaku dibentuk adalah klaim menyesatkan
    (`docs/rules-compliance.md`), dan itu menikam tesis produk ini sendiri.

    Kalimatnya tidak dikarang di sini, dan juga TIDAK dipercaya begitu saja. Kata-kata pengakuan
    (`task`, `derived_from`, `why`) memang dikutip dari berkas — tetapi HIMPUNAN envelope yang
    dibentuk dihitung dari envelope yang benar-benar dimuat (:func:`_is_shaped`), lalu dibandingkan
    dengan klaim berkas (:func:`_disclosure_mismatch`). Berkas yang berbohong tidak bisa membuat
    naskah ini menyebut envelope karangan sebagai "salinan apa adanya bukti nyata"; selisihnya
    dicetak mencolok. Setiap teks asal-berkas lewat :func:`_safe` lebih dulu.

    Kalimat tentang envelope yang MENJAWAB tidak lagi disimpulkan sebagai komplemen dari yang
    dibentuk — komplemen tidak pernah memeriksa apa pun. Ia datang dari
    :func:`preflight.outage_fixture.provenance_lines` atas hasil `verify_provenance`, jadi
    "DIVERIFIKASI salinan apa adanya" hanya tercetak untuk `source_id` yang sha256 envelopenya
    cocok dengan hash baris asal yang direkam saat penyalinan.

    Fixture yang TIDAK mengaku dibentuk tetap tidak dicap sintetis — kecuali envelopenya sendiri
    berbentuk padam, dan itu justru disebut sebagai ketidakcocokan.
    """
    raw = _fixture_json(path)
    note = _synthetic_note(path)
    shaped = [snapshot for snapshot in fixture.snapshots if _is_shaped(snapshot)]
    shaped_ids = {snapshot.source_id for snapshot in shaped}
    claimed = _claimed_synthetic_ids(raw, note)
    provenance = verify_provenance(raw)
    label = _safe(raw.get("source_db", "?"))
    if note is None:
        lines = [
            "  asal-usul envelope di fixture ini TIDAK DINYATAKAN: berkasnya tidak memuat blok",
            "  `synthetic`, jadi naskah ini tidak mengarang pengakuan apa pun tentangnya.",
        ]
        lines.extend(provenance_lines(provenance, label, indent="    ", lead="yang TIDAK dibentuk: "))
        lines.extend(_disclosure_mismatch(shaped_ids, claimed))
        return lines
    rewritten = [f"{_safe(snapshot.tool)} (source_id {snapshot.source_id})" for snapshot in shaped]
    lines = [
        "  PENGAKUAN — dibaca dari blok `synthetic` BERKAS fixture, bukan ditulis oleh naskah ini:",
        f"    envelope PADAM di bawah SINTETIS: ditulis ulang menjadi status=unavailable oleh "
        f"{_safe(note.get('task', '?'))},",
        f"    dari {_safe(note.get('derived_from', '?'))}. Yang dibentuk: {len(rewritten)} envelope, yaitu",
    ]
    lines.extend(f"      {entry}" for entry in rewritten)
    lines.append("    alasannya, apa adanya dari berkas itu:")
    lines.extend(f"      {chunk}" for chunk in textwrap.wrap(_safe(note.get("why", ""), WHY_LIMIT), width=94))
    lines.extend(provenance_lines(provenance, label, indent="    ", lead="yang TIDAK dibentuk: "))
    lines.extend(
        [
            "    yang dikarang hanyalah BENTUK padamnya; bila RYO kelak benar-benar mengirim",
            "    `status: unavailable`, bentuk ini wajib dibandingkan ulang (api-verifier).",
        ]
    )
    lines.extend(_disclosure_mismatch(shaped_ids, claimed))
    return lines


def step2(step2_fixture: str | Path | None) -> list[str]:
    """§7 langkah 2: satu tool wajib `unavailable` -> item 02 merah, keputusan tetap dihitung.

    Fixture TASKS 2.2c dipakai secara bawaan. Sebelum envelopenya dicetak, naskah mengaku dari
    mana bentuk padam itu berasal (:func:`_step2_disclosure`) — pengakuan itu bagian dari
    peragaannya, bukan catatan kaki. Tanpa fixture (`--step2-fixture ""`) langkah ini DICETAK
    SEBAGAI MENUNGGU: fixture "semua tool gagal" TIDAK dipakai sebagai gantinya karena ia
    menghasilkan envelope `unavailable` dengan item 02 `unspoken` — kalimat lain, bukan naskah ini.
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
                f"  yang tidak diberikan ke perintah ini: fixture berbentuk itu ({STEP2_TASK}).",
                "  kenapa fixture 'semua tool gagal' tidak dipakai sebagai pengganti: ia menghasilkan",
                "  envelope status=unavailable dengan item 02 `unspoken` — kalimat lain, bukan naskah ini.",
                "  titik sambungnya: `python -m agent.demo --step2-fixture PATH` (bawaannya sudah dipasang).",
            ]
        )
        return lines
    fixture = load_fixture(step2_fixture)
    symbol = fixture.symbols[0] if fixture.symbols else STEP1_SYMBOL
    lines.append(f"fixture   : {_safe(fixture.name)}")
    lines.extend(_step2_disclosure(step2_fixture, fixture))
    with tempfile.TemporaryDirectory(prefix="tenko-demo-step2-") as tmp:
        db_path = Path(tmp) / "step2.db"
        materialize(fixture, db_path)
        lines.extend(
            step1(
                fixture,
                db_path,
                symbol,
                heading=f'--- make skill ARGS="preflight {_safe(symbol)}" atas fixture langkah 2 ---',
            )
        )
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


def demo_lines(
    fixture_path: str | Path = DEMO_FIXTURE_PATH,
    step2_fixture: str | Path | None = DEFAULT_STEP2_FIXTURE,
) -> list[str]:
    """Seluruh naskah §7 sebagai daftar baris. Deterministik atas fixture yang sama.

    Bawaan `step2_fixture` sama dengan bawaan CLI: memanggil fungsi ini tanpa argumen harus
    memperagakan naskah yang sama dengan `make demo`, bukan naskah yang lebih pendek. `None` (atau
    string kosong) tetap tersedia dan berarti "langkah 2 tidak dijalankan", dicetak MENUNGGU.
    """
    audit = install_data_dir_audit()
    fixture = load_fixture(fixture_path)
    if step2_fixture:
        # Gagal cepat: fixture langkah 2 dibaca SEBELUM tiga tick berjalan, supaya path yang salah
        # ketik tidak baru ketahuan setelah setengah naskah tercetak.
        load_fixture(step2_fixture)
    with tempfile.TemporaryDirectory(prefix="tenko-demo-") as tmp_name:
        tmp = Path(tmp_name)
        db_path = tmp / "demo.db"
        lines = _head(fixture, fixture_path)
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
            _provenance_summary(fixture_path),
            _step2_summary(step2_fixture),
            f"  {audit.line()}",
            "  dua run `make demo` harus byte-identik:",
            "    make demo > a; make demo > b; diff a b",
            "  semua trade di atas adalah PRACTICE/SIMULATED: nol dana, nol wallet, nol eksekusi",
        ]
    )
    return lines


def _provenance_summary(fixture_path: str | Path) -> str:
    """Satu baris ringkasan provenans fixture utama — ekor naskah tidak boleh lebih optimistis.

    Juri yang hanya membaca ringkasan tetap melihat berapa envelope yang benar-benar DIPERIKSA
    isinya, bukan hanya berapa yang berkasnya klaim disalin.
    """
    report = verify_provenance(_fixture_json(fixture_path))
    if report.mismatched:
        return (
            "  bukti fixture utama: KETIDAKCOCOKAN ISI pada source_id "
            f"{', '.join(str(entry) for entry in report.mismatched)} — envelopenya BUKAN salinan "
            "apa adanya baris asalnya"
        )
    if report.unverified:
        return (
            f"  bukti fixture utama: {len(report.verified)} envelope DIVERIFIKASI cocok dengan sha256 "
            f"baris asalnya, {len(report.unverified)} TIDAK DIVERIFIKASI (baris tanpa hash asal)"
        )
    return (
        f"  bukti fixture utama: {len(report.verified)} dari {report.answering} envelope yang menjawab "
        "DIVERIFIKASI cocok dengan sha256 baris asalnya"
    )


def _step2_summary(step2_fixture: str | Path | None) -> str:
    """Satu baris ringkasan langkah 2 — termasuk pengakuan sintetisnya, bila fixture mengaku.

    Ekor naskah tidak boleh lebih optimistis daripada badannya: bila klaim `synthetic` berkas tidak
    cocok dengan envelope yang benar-benar dimuat, baris ini ikut menyebutkannya. Juri yang hanya
    membaca ringkasan tetap melihat bahwa ada yang tidak cocok.
    """
    if not step2_fixture:
        return f"  langkah 2 MENUNGGU {STEP2_TASK} (fixture 'satu tool wajib unavailable' tidak diberikan)"
    name = _safe(Path(step2_fixture).name)
    note = _synthetic_note(step2_fixture)
    flag = ""
    if _mismatched(step2_fixture):
        flag = " — KETIDAKCOCOKAN: berkas tidak cocok dengan envelopenya sendiri (bentuk atau isi)"
    report = verify_provenance(_fixture_json(step2_fixture))
    if report.mismatched:
        verdict = f"{len(report.mismatched)} envelope yang menjawab TIDAK cocok dengan hash asalnya"
    elif report.unverified:
        verdict = (
            f"{len(report.verified)} envelope yang menjawab DIVERIFIKASI salinan bukti nyata, "
            f"{len(report.unverified)} TIDAK DIVERIFIKASI"
        )
    else:
        verdict = f"{len(report.verified)} envelope yang menjawab DIVERIFIKASI salinan bukti nyata"
    if note is None:
        return (
            f"  langkah 2 dijalankan dari {name} (berkas itu tidak menyatakan asal-usul "
            f"envelopenya); {verdict}{flag}"
        )
    return (
        f"  langkah 2 dijalankan dari {name} — envelope PADAMNYA SINTETIS "
        f"({_safe(note.get('task', '?'))}); {verdict}{flag}"
    )


def _mismatched(step2_fixture: str | Path) -> bool:
    """`True` bila berkas berselisih dengan dirinya sendiri — dua cara, keduanya dihitung.

    1. klaim `synthetic` tidak cocok dengan envelope yang benar-benar dimuat (BENTUK), dan
    2. isi sebuah envelope yang menjawab tidak cocok dengan sha256 baris asalnya (ISI).

    Yang kedua ada karena yang pertama tidak pernah menyentuh angka di dalam envelope: berkas
    dengan `status`/`warnings` yang rapi tetap bisa membawa satu angka yang diketik ulang.
    """
    try:
        fixture = load_fixture(step2_fixture)
    except DemoFixtureError:
        return False
    raw = _fixture_json(step2_fixture)
    shaped = {snapshot.source_id for snapshot in fixture.snapshots if _is_shaped(snapshot)}
    if verify_provenance(raw).mismatched:
        return True
    return bool(_disclosure_mismatch(shaped, _claimed_synthetic_ids(raw, _synthetic_note(step2_fixture))))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent.demo",
        description="Naskah demo spec §7 langkah 1-6 dari fixture offline (SIMULATED).",
    )
    parser.add_argument("--fixture", default=str(DEMO_FIXTURE_PATH), help="fixture demo langkah 1/3/4/5")
    parser.add_argument(
        "--step2-fixture",
        default=str(DEFAULT_STEP2_FIXTURE),
        help=(
            f"fixture 'satu tool wajib unavailable' ({STEP2_TASK}); bawaannya "
            f"{DEFAULT_STEP2_FIXTURE.relative_to(ROOT)}. Beri string kosong untuk melewati langkah 2 "
            "(dicetak MENUNGGU, bukan dipalsukan)"
        ),
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
