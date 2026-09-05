"""Dua fixture kegagalan yang BERBEDA untuk spec §7 langkah 2 (TASKS 2.2c).

Naskah §7 langkah 2 menuntut item 2 (kelengkapan) **MERAH**, dan itu hanya lahir bila **SATU**
tool wajib menjawab `unavailable` sementara yang lain menjawab. Fixture "semua tool gagal"
menghasilkan kalimat yang LAIN — envelope `status: unavailable`, `data: null`, item 2
`unspoken` — dan memakainya sebagai pengganti berarti memperagakan naskah yang bukan naskahnya.
Membedakan kedua rupa kegagalan itu adalah inti produk (ADR-013): "panggilan yang bolong" bukan
"panggilan yang tidak terjawab sama sekali".

Karena itu modul ini melahirkan DUA berkas, bukan satu:

* :data:`ONE_TOOL_FIXTURE_PATH` — 4 dari 5 tool wajib menjawab, `monitor_market_sentiment_shift`
  `unavailable` → item 02 **merah**, item 04 & 08 `unspoken`, tujuh item lain tetap hijau,
  `status="partial"`, `data` TIDAK null, `NO_GO` dengan `02_completeness` di `blocking`.
* :data:`ALL_TOOLS_FIXTURE_PATH` — kelima tool wajib `unavailable` → `status="unavailable"`,
  `data=None`, kesepuluh item `unspoken` (termasuk item 02), tanpa exception, dan warning yang
  menyebut **"no required tool answered"**.

**Envelope `unavailable`-nya SINTETIS, dan itu ditulis di berkasnya sendiri.** `docs/api-facts.md`
(tabel "Tipe & kosakata envelope") mencatat `status: unavailable` **BELUM PERNAH TERAMATI**
(0/3645 envelope; sapuan `id <= 3645`, 6 Sep 2026): enumnya berasal dari RYO Builder MCP
Guide, bukan dari sampel. Jadi bentuk envelope padam di sini adalah **hipotesis dari enum
yang terdokumentasi**, bukan bukti yang pernah kami terima. Konsekuensinya ditandai eksplisit
di setiap envelope sintetis (`warnings` membawa kata `SYNTHETIC`, `summary.headline` menyebut
TASKS 2.2c) dan di blok `synthetic` berkas fixture — supaya tidak ada yang membacanya sebagai
perilaku RYO yang terverifikasi.

**Yang TIDAK disintesis:** keempat envelope yang MENJAWAB adalah salinan apa adanya dari bukti
nyata `data/ryo.db` (id 2491/2492/2494/2495/2544, jendela 2026-09-05T02:45Z..03:01Z) lewat
fixture bertanggal TASKS 3.4a — jadi tujuh item hijau di fixture (1) benar-benar dinilai atas
angka RYO, bukan atas angka yang diketik tangan. Tidak satu ambang pun dilonggarkan: `params_hash`
tetap `3d39fb1b…` (TASKS 5.0).

**Fixture ini tidak boleh bisa masuk ke store bukti.** Penjaganya bukan penjaga baru:
:func:`core.recorder.fixture_refusal` (TASKS 0.19) dipanggil SEBELUM `Store` dibuka, sama seperti
`agent.demo_fixture` — satu definisi, bukan salinan kedua yang bisa menyimpang.

**CLI-nya mengaku sebelum mencetak.** `preflight` tidak meneruskan `warnings` envelope padam ke
envelope hasil, jadi byte kanonik di stdout — byte yang SAMA dengan `make skill`, yang di-screenshot
dan masuk submission — tidak memuat satu jejak pun bahwa padamnya dibuat-buat. Karena itu
:func:`main` mencetak :func:`confession_lines` ke **stderr** lebih dulu (stdout tetap satu baris
JSON kanonik yang bisa di-pipe, bentuknya tidak disentuh) DAN menolak mencetak sama sekali bila
:func:`verified_confession` menemukan envelope padam yang tidak mengaku dibentuk. Dua-duanya,
bukan salah satu: pengakuan saja bisa hilang saat stderr dibuang, penolakan saja membuat pemakai
tidak pernah membaca alasannya.

**Yang diaudit BUKAN hanya bentuk envelope, tetapi ISInya.** Sebelumnya `status` dan `warnings`
saja yang diperiksa, sehingga himpunan "yang TIDAK dibentuk" hanyalah komplemen — dan kalimat
"salinan apa adanya bukti nyata" tercetak tanpa satu baris pun membandingkannya dengan sumber.
Mengedit satu angka (mis. `risk_reward`) di dalam envelope NYATA menggeser keputusan dan
`decision_hash` tanpa peringatan apa pun. Karena itu tiap baris membawa :data:`SOURCE_HASH_KEY` =
kolom `snapshots.payload_hash` baris asalnya, direkam saat penyalinan (`agent.demo_fixture.capture`),
dan :func:`verify_provenance` menghitung ulang sha256 kanonik envelope yang benar-benar dimuat
untuk membandingkannya. Hash dibandingkan, bukan `data/ryo.db` dibuka ulang: naskah demo mengukur
bahwa ia tidak menyentuh `data/`. Baris tanpa hash tidak dicap bohong — ia dicetak sebagai
pengakuan yang **TIDAK DIVERIFIKASI**, bukan sebagai fakta.

**Predikat "envelope ini dibentuk" hidup di SATU tempat** (:func:`is_shaped`) dan `agent/demo.py`
mengimpornya. Dua salinan yang menyimpang pernah membuat berkas yang sama divonis berbeda oleh
demo dan oleh CLI ini.

Pemakaian::

    python -m preflight.outage_fixture                 # jalankan fixture (1), cetak envelopenya
    python -m preflight.outage_fixture --mode all      # jalankan fixture (2)
    python -m preflight.outage_fixture --write         # tulis ulang kedua berkas dari sumbernya
    make demo                                          # langkah 2 memakai fixture (1)

Bentuk berkasnya sengaja SAMA dengan fixture demo TASKS 3.4a (`fixture_version: 1`), supaya
`agent.demo.step2` bisa memuatnya lewat `agent.demo_fixture.load_fixture` tanpa satu baris pun
kode demo berubah — modul ini TIDAK mengimpor `agent` (arah impornya `agent` → `preflight`,
membalikkannya berarti lingkaran).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from core.skill import SkillResult, clean_text
from core.store import Store, StoreError, sha256_hex
from preflight.params import DEFAULT_PARAMS, REQUIRED_TOOLS, Params
from preflight.skill import preflight

ROOT = Path(__file__).resolve().parent.parent

#: Sumber bukti nyata: fixture bertanggal TASKS 3.4a (dibaca sebagai DATA, bukan diimpor).
SOURCE_FIXTURE_PATH = ROOT / "agent" / "fixtures" / "demo-2026-09-05.json"

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

#: (1) satu tool wajib padam — satu-satunya bentuk yang membuat item 02 MERAH.
ONE_TOOL_FIXTURE_PATH = FIXTURE_DIR / "outage-one-tool-2026-09-05.json"

#: (2) semua tool wajib padam — item 02 `unspoken`, envelope `unavailable`.
ALL_TOOLS_FIXTURE_PATH = FIXTURE_DIR / "outage-all-tools-2026-09-05.json"

#: Simbol yang dipanggil kedua fixture (bukti nyata untuk SOL ada di fixture sumber).
SYMBOL = "SOL"

#: Baris `snapshots` fixture sumber yang dipakai: tepat satu per tool wajib untuk SOL.
SOURCE_IDS: tuple[int, ...] = (2491, 2492, 2494, 2495, 2544)

#: Tool yang dipadamkan di fixture (1). `monitor_market_sentiment_shift` dipilih karena ia
#: satu-satunya tool yang MENGGERAKKAN dua item sekaligus (04 wajib, 08 opsional): kartunya
#: memperagakan bahwa item opsional yang `unspoken` TIDAK ikut menahan, sementara item wajib
#: yang `unspoken` ikut. Memadamkan `analyze_token` hanya akan mematikan satu item.
DOWN_TOOL = "monitor_market_sentiment_shift"

MODES: Mapping[str, tuple[str, ...]] = {
    "one": (DOWN_TOOL,),
    "all": tuple(REQUIRED_TOOLS),
}

#: Berkas per mode. Nama fixture = nama berkasnya, supaya keluaran demo menyebut berkas yang
#: benar-benar dibaca.
PATHS: Mapping[str, Path] = {
    "one": ONE_TOOL_FIXTURE_PATH,
    "all": ALL_TOOLS_FIXTURE_PATH,
}

FIXTURE_VERSION = 1

#: Penanda di `warnings` sebuah envelope yang menyatakan envelope itu DIBENTUK, bukan diterima.
#: Ditulis :func:`outage_envelope`, dibaca :func:`is_shaped`.
SYNTHETIC_WARNING = "SYNTHETIC"

#: Kunci per baris `snapshots`: nilai kolom `snapshots.payload_hash` baris ASAL, direkam saat
#: penyalinan (`agent.demo_fixture.capture`). Ia satu-satunya cara memeriksa ISI sebuah envelope
#: fixture tanpa membuka `data/` saat demo berjalan — dan tanpa itu, "salinan apa adanya" hanya
#: klaim berkas tentang dirinya sendiri.
SOURCE_HASH_KEY = "source_payload_hash"


class OutageFixtureError(Exception):
    """Fixture tidak bisa dibaca/dibangun, atau DB tujuannya bukan tempat yang sah."""


# --- envelope padam ----------------------------------------------------------------------


def outage_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Envelope RYO yang menyatakan dirinya `unavailable`, dibentuk dari envelope nyata.

    Yang dipertahankan dari aslinya: `schema_version`, `tool`, `request`, `as_of`, dan DAFTAR
    KUNCI `availability` — nama seksinya milik tool, bukan karangan kami. Yang berubah:
    `status` → `unavailable`, `data_mode` → `unknown`, `data` → `null`, tiap seksi
    `availability` → `unavailable`, dan `summary`/`warnings` diganti kalimat yang mengaku
    sintetis.

    `as_of` disalin apa adanya (bukan `null`) karena kolom `ts_utc` dan `as_of` baris yang sama
    dijaga saling konsisten oleh `core/store.py`; `as_of` di sini adalah waktu PANGGILAN yang
    gagal, dan `data` tetap `null` — tidak ada satu angka pun yang dibaca dari envelope ini.
    """
    if not isinstance(envelope, Mapping):
        raise OutageFixtureError(f"envelope harus objek, bukan {type(envelope).__name__}")
    tool = envelope.get("tool")
    if not isinstance(tool, str):
        raise OutageFixtureError("envelope sumber tidak menyebut `tool`")
    availability = envelope.get("availability")
    sections = sorted(availability) if isinstance(availability, Mapping) else []
    return {
        "schema_version": envelope.get("schema_version", "1.0"),
        "tool": tool,
        "status": "unavailable",
        "data_mode": "unknown",
        "as_of": envelope.get("as_of"),
        "request": dict(envelope["request"]) if isinstance(envelope.get("request"), Mapping) else {},
        "data": None,
        "summary": {
            "headline": (
                f"{tool} is unavailable; no data was returned for this call "
                "(SYNTHETIC outage fixture, TASKS 2.2c)"
            ),
            "key_points": [
                "status=unavailable with data=null: nothing here may be read as a number",
                "shape follows the RYO envelope enum in docs/api-facts.md; status=unavailable "
                "has never been observed from RYO: 0/3645 (id <= 3645, 6 Sep 2026), so this "
                "envelope is a hypothesis",
            ],
        },
        "availability": {section: "unavailable" for section in sections},
        "warnings": [
            f"SYNTHETIC: {tool} did not answer; this envelope was written by "
            "preflight/outage_fixture.py, it is not RYO output"
        ],
    }


# --- provenans: SATU predikat + SATU verifikator, dipakai modul ini DAN `agent/demo.py` ----


def is_shaped(envelope: Mapping[str, Any] | None) -> bool:
    """Apakah envelope INI dibentuk, dinilai dari envelopenya sendiri — bukan dari klaim berkas.

    Dua tanda, `or` bukan `and`, dan itu disengaja:

    * ``status == "unavailable"`` — status itu BELUM PERNAH teramati dari RYO (`docs/api-facts.md`:
      0/3645, `id <= 3645`, 6 Sep 2026; enumnya dari Builder MCP Guide, bukan dari sampel), jadi
      envelope berstatus itu tidak mungkin salinan apa adanya bukti nyata;
    * ``warnings`` memuat :data:`SYNTHETIC_WARNING` — penanda yang ditulis :func:`outage_envelope`
      saat ia membentuk envelope.

    `and` akan membuat berkas yang MENGHAPUS salah satu tandanya lolos sebagai "salinan apa adanya".
    Fungsi ini hidup di sini, dan `agent/demo.py` mengimpornya: dua penjaga dengan dua definisi
    berbeda atas berkas yang SAMA pernah membuat demo mencetak KETIDAKCOCOKAN sementara CLI ini
    keluar rc=0 — dua vonis, satu berkas.
    """
    if not isinstance(envelope, Mapping):
        return False
    if str(envelope.get("status") or "") == "unavailable":
        return True
    warnings = envelope.get("warnings")
    if not isinstance(warnings, list):
        return False
    return any(SYNTHETIC_WARNING in str(entry).upper() for entry in warnings)


@dataclass(frozen=True, slots=True)
class Provenance:
    """Hasil PEMERIKSAAN isi tiap envelope fixture terhadap hash baris asalnya.

    Empat himpunan `source_id`, dan semuanya perlu dibedakan karena kalimat yang boleh dicetak
    untuk masing-masing berbeda: `verified` boleh disebut salinan apa adanya, `unverified` hanya
    boleh disebut PENGAKUAN berkas, `mismatched` wajib disebut sebagai ketidakcocokan, dan
    `shaped` memang tidak diklaim menyalin apa pun.
    """

    verified: tuple[int, ...] = ()
    unverified: tuple[int, ...] = ()
    mismatched: tuple[int, ...] = ()
    shaped: tuple[int, ...] = ()

    @property
    def answering(self) -> int:
        """Berapa envelope yang MENJAWAB (bukan padam) — terverifikasi atau tidak."""
        return len(self.verified) + len(self.unverified) + len(self.mismatched)


def verify_provenance(fixture: Mapping[str, Any]) -> Provenance:
    """Bandingkan ISI tiap envelope yang menjawab dengan sha256 baris asal yang direkam berkas.

    Kenapa hash, bukan pembacaan ulang `data/ryo.db`: naskah demo menjamin (dan MENGUKUR, lewat
    `sys.addaudithook`) bahwa ia tidak membuka satu berkas pun di bawah `data/`. Karena itu
    perbandingannya dipindah ke waktu PENYALINAN: `agent.demo_fixture.capture` merekam kolom
    `snapshots.payload_hash` baris asal ke :data:`SOURCE_HASH_KEY`, dan di sini sha256 kanonik
    envelope yang benar-benar dimuat dihitung ulang dan dibandingkan dengannya. Mengedit satu
    angka di dalam envelope (mis. `risk_reward`) menggeser sha256-nya dan langsung jatuh ke
    `mismatched`.

    Baris tanpa :data:`SOURCE_HASH_KEY` TIDAK dianggap salah — ia `unverified`, dan yang boleh
    dicetak untuknya adalah pengakuan berkas, bukan klaim afirmatif.
    """
    rows = fixture.get("snapshots")
    rows = rows if isinstance(rows, list) else []
    verified: list[int] = []
    unverified: list[int] = []
    mismatched: list[int] = []
    shaped: list[int] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        source_id = row.get("source_id")
        if not isinstance(source_id, int):
            continue
        envelope = row.get("envelope")
        if is_shaped(envelope if isinstance(envelope, Mapping) else None):
            shaped.append(source_id)
            continue
        recorded = row.get(SOURCE_HASH_KEY)
        if not isinstance(recorded, str) or not recorded.strip():
            unverified.append(source_id)
            continue
        try:
            actual = sha256_hex(dict(envelope) if isinstance(envelope, Mapping) else envelope)
        except StoreError:
            # Envelope yang tidak bisa dikanonikalisasi tidak bisa dibandingkan; itu bukan
            # "cocok", dan juga bukan bukti bohong — ia tidak terverifikasi.
            unverified.append(source_id)
            continue
        (verified if actual == recorded.strip() else mismatched).append(source_id)
    return Provenance(tuple(verified), tuple(unverified), tuple(mismatched), tuple(shaped))


def provenance_lines(report: Provenance, source_label: str, indent: str = "  ", lead: str = "") -> list[str]:
    """Kalimat provenans yang KEKUATANNYA mengikuti hasil pemeriksaan, bukan niat baik penulisnya.

    Ini satu-satunya tempat kalimat "salinan apa adanya" boleh lahir, dan ia hanya lahir untuk
    `source_id` yang sha256 envelopenya benar-benar cocok. Yang tidak bisa diperiksa dicetak
    sebagai PENGAKUAN berkas dengan kata "TIDAK DIVERIFIKASI": klaim kuat yang verifikasinya
    tidak bisa dijalankan lebih buruk daripada diam.

    `source_label` sudah harus dibersihkan pemanggilnya (teks berkas): modul ini tidak tahu
    sanitizer mana yang dipakai layar tujuan. `lead` hanya kata pengantar milik pemanggil
    (mis. "yang TIDAK dibentuk: ") dan menempel pada baris pertama yang benar-benar tercetak.
    """

    def wrap(first: str, rest: str) -> list[str]:
        """Satu kalimat, dibungkus supaya daftar id yang panjang tidak keluar layar."""
        body = textwrap.wrap(rest, width=88) or [""]
        return [f"{indent}{first}"] + [f"{indent}  {chunk}" for chunk in body]

    def ids(values: tuple[int, ...]) -> str:
        return ", ".join(str(value) for value in values) or "-"

    lines: list[str] = []
    if report.mismatched:
        lines.extend(
            wrap(
                "KETIDAKCOCOKAN ISI — envelope yang MENJAWAB tidak cocok dengan sha256 baris asal",
                f"yang direkam berkas ini (`{SOURCE_HASH_KEY}`): source_id {ids(report.mismatched)}. "
                "Isinya sudah berubah sejak disalin, jadi ia BUKAN salinan apa adanya bukti nyata.",
            )
        )
    if report.verified:
        lines.extend(
            wrap(
                f"{lead}{len(report.verified)} envelope yang MENJAWAB DIVERIFIKASI salinan apa adanya",
                f"bukti nyata {source_label} id {ids(report.verified)} — sha256 kanonik tiap envelope "
                f"sama dengan `{SOURCE_HASH_KEY}` yang direkam saat penyalinan, jadi angka yang "
                "dinilai di bawah adalah angka RYO, bukan angka yang diketik tangan.",
            )
        )
        lead = ""
    if report.unverified:
        lines.extend(
            wrap(
                f"{lead}{len(report.unverified)} envelope yang MENJAWAB: berkas MENGAKU menyalinnya,",
                f"dari {source_label} id {ids(report.unverified)} — TIDAK DIVERIFIKASI di sini: "
                f"barisnya tidak membawa `{SOURCE_HASH_KEY}` yang bisa dibandingkan.",
            )
        )
    if not report.answering:
        lines.append(f"{indent}nol envelope yang menjawab di berkas ini — tidak ada isi yang bisa diperiksa.")
    return lines


# --- membangun berkas --------------------------------------------------------------------


def _read_json(path: str | Path) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OutageFixtureError(f"fixture {path} tidak bisa dibaca: {exc}") from exc
    if not isinstance(raw, dict):
        raise OutageFixtureError(f"fixture {path} bukan objek JSON")
    return raw


def build(mode: str, source_path: str | Path = SOURCE_FIXTURE_PATH) -> dict[str, Any]:
    """Bangun isi berkas fixture untuk `mode` ("one"/"all") — transformasi murni, tanpa DB.

    `recorded` sengaja dikosongkan di sini: ia diisi :func:`write` dari hasil yang BENAR-BENAR
    teramati saat fixture dijalankan, bukan diketik tangan.
    """
    down = MODES.get(mode)
    if down is None:
        raise OutageFixtureError(f"mode {mode!r} bukan salah satu {sorted(MODES)}")
    source = _read_json(source_path)
    rows = source.get("snapshots")
    if not isinstance(rows, list):
        raise OutageFixtureError(f"fixture sumber {source_path}: `snapshots` bukan daftar")
    by_id = {row.get("source_id"): row for row in rows if isinstance(row, Mapping)}
    missing = [source_id for source_id in SOURCE_IDS if source_id not in by_id]
    if missing:
        raise OutageFixtureError(f"fixture sumber {source_path}: snapshot {missing} tidak ada")
    snapshots: list[dict[str, Any]] = []
    synthetic: list[str] = []
    for source_id in SOURCE_IDS:
        row = dict(by_id[source_id])
        tool = str(row.get("tool"))
        envelope = row.get("envelope")
        if tool in down:
            row["envelope"] = outage_envelope(envelope if isinstance(envelope, Mapping) else {})
            row["synthetic"] = True
            # Hash baris asal DIBUANG bersama envelopenya: baris ini bukan lagi salinan apa pun,
            # dan hash yang tertinggal akan menjadi klaim provenans atas envelope yang dikarang.
            row.pop(SOURCE_HASH_KEY, None)
            synthetic.append(f"{tool} (source_id {source_id})")
        else:
            row["synthetic"] = False
        snapshots.append(row)
    return {
        "fixture_version": FIXTURE_VERSION,
        "name": PATHS[mode].stem,
        "captured_at": str(source.get("captured_at", "")),
        "source_db": str(source.get("source_db", "")),
        "source_ids": list(SOURCE_IDS),
        "now": str(source.get("now", "")),
        "symbols": [SYMBOL],
        "params_hash": str(source.get("params_hash", "")),
        "synthetic": {
            "task": "TASKS 2.2c",
            "derived_from": str(Path(source_path).relative_to(ROOT))
            if Path(source_path).is_absolute()
            else str(source_path),
            "envelopes_rewritten_to_unavailable": synthetic,
            "why": (
                "spec §7 step 2 needs one required tool unavailable while the others answer; "
                "status=unavailable has never been observed from RYO: 0/3645 "
                "(id <= 3645, 6 Sep 2026, docs/api-facts.md), "
                "so these envelopes are shaped from the documented enum, not from evidence"
            ),
        },
        "recorded": {},
        "snapshots": snapshots,
    }


# --- menjalankan fixture -----------------------------------------------------------------


def _refuse_evidence_db(db_path: str | Path) -> None:
    """Tolak DB tujuan yang merupakan store bukti (penjaga TASKS 0.19, satu definisi).

    Diimpor di dalam fungsi: `core.recorder` menarik `core.ryo_client`, dan `preflight/` adalah
    jalur store-first yang tidak pernah memanggil RYO — impor tingkat modul akan membuat
    pernyataan itu tidak lagi bisa dibaca dari daftar impor.
    """
    from core.recorder import fixture_refusal

    reason = fixture_refusal(db_path)
    if reason is not None:
        raise OutageFixtureError(
            f"menolak menulis fixture kegagalan ke {db_path}: {reason}. Fixture bukan bukti; "
            "ia tidak boleh masuk ke store bukti (TASKS 0.19/2.2c)."
        )


def materialize(fixture: Mapping[str, Any], db_path: str | Path) -> dict[int, int]:
    """Tulis snapshot fixture ke DB BARU; kembalikan peta `source_id` → id baru."""
    _refuse_evidence_db(db_path)
    rows = fixture.get("snapshots")
    if not isinstance(rows, list) or not rows:
        raise OutageFixtureError("fixture: nol snapshot — tidak ada yang bisa dibekukan")
    mapping: dict[int, int] = {}
    try:
        with Store(db_path) as store:
            for row in sorted(rows, key=lambda item: int(item["source_id"])):
                mapping[int(row["source_id"])] = store.insert_snapshot(
                    str(row["tool"]),
                    dict(row["args"]),
                    dict(row["envelope"]),
                    ts_utc=str(row["ts_utc"]),
                )
    except (KeyError, TypeError, ValueError) as exc:
        raise OutageFixtureError(f"fixture berbentuk salah: {type(exc).__name__}: {exc}") from exc
    except StoreError as exc:
        raise OutageFixtureError(f"fixture gagal ditulis ke {db_path}: {exc}") from exc
    return mapping


def frozen_now(fixture: Mapping[str, Any]) -> datetime:
    """Jam beku fixture — jam keputusan, BUKAN jam dinding (syarat "dua run identik")."""
    raw = fixture.get("now")
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise OutageFixtureError(f"fixture: `now` {raw!r} bukan ISO-8601 ({exc})") from exc
    if moment.tzinfo is None:
        raise OutageFixtureError("fixture: `now` wajib bertimezone")
    return moment


def run(
    fixture_path: str | Path = ONE_TOOL_FIXTURE_PATH,
    params: Params = DEFAULT_PARAMS,
) -> SkillResult:
    """Materialisasi fixture ke DB tmp BARU lalu satu `preflight` pada jam beku fixture.

    Tidak pernah melempar untuk bukti yang jelek: fixture "semua tool padam" pun menghasilkan
    envelope penolakan yang jujur, bukan exception (itu AC 2.2c).
    """
    fixture = _read_json(fixture_path)
    now = frozen_now(fixture)
    symbols = fixture.get("symbols")
    symbol = str(symbols[0]) if isinstance(symbols, list) and symbols else SYMBOL
    with tempfile.TemporaryDirectory(prefix="tenko-outage-") as tmp:
        db_path = Path(tmp) / "outage.db"
        materialize(fixture, db_path)
        with Store(db_path, read_only=True) as store:
            return preflight(symbol, store=store, params=params, now=now)


def observed(result: SkillResult) -> dict[str, Any]:
    """Apa yang benar-benar terjadi saat fixture dijalankan — untuk blok `recorded`.

    `decision`/`blocking` dibaca dari `data` bila ada, dan dari `summary` bila `data` `None`
    (envelope `unavailable` wajib `data=None`, jadi keputusannya dibawa `summary` — spec §3).
    """
    data = result.data or {}
    summary = result.summary or {}
    source: Mapping[str, Any] = data if result.data is not None else summary
    return {
        "decision": source.get("decision"),
        "blocking": list(source.get("blocking") or []),
        "envelope_status": result.status,
        "data_is_null": result.data is None,
        "item_status": {str(item.get("id")): str(item.get("status")) for item in (data.get("items") or [])}
        or dict(result.availability),
        "decision_hash": data.get("decision_hash"),
    }


def write(mode: str, path: str | Path | None = None, source_path: str | Path = SOURCE_FIXTURE_PATH) -> Path:
    """Tulis berkas fixture `mode`, dengan `recorded` diisi dari run yang benar-benar terjadi."""
    if mode not in PATHS:
        raise OutageFixtureError(f"mode {mode!r} bukan salah satu {sorted(MODES)}")
    target = Path(path) if path is not None else PATHS[mode]
    fixture = build(mode, source_path)
    with tempfile.TemporaryDirectory(prefix="tenko-outage-write-") as tmp:
        probe = Path(tmp) / "probe.json"
        probe.write_text(json.dumps(fixture), encoding="utf-8")
        result = run(probe)
    fixture["recorded"] = {SYMBOL: observed(result)}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


# --- pengakuan (CLI) ---------------------------------------------------------------------

#: Batas panjang tiap potong teks fixture yang dicetak ke stderr (teks berkas = teks pihak lain:
#: `--fixture` datang dari argv).
CONFESSION_TEXT_LIMIT = 400


def _labels(fixture: Mapping[str, Any]) -> list[str]:
    """`tool (source_id N)` untuk tiap baris snapshot yang MENGAKU ditulis ulang."""
    rows = fixture.get("snapshots")
    rows = rows if isinstance(rows, list) else []
    return [
        f"{row.get('tool')} (source_id {row.get('source_id')})"
        for row in rows
        if isinstance(row, Mapping) and row.get("synthetic") is True
    ]


def verified_confession(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Blok `synthetic` fixture, DIPERIKSA terhadap snapshotnya; lempar bila tidak cocok.

    Dua arah diperiksa, karena keduanya adalah cara berbohong yang berbeda:

    * setiap envelope yang BERBENTUK padam (:func:`is_shaped`: `status: unavailable`, atau
      `warnings` memuat `SYNTHETIC`) WAJIB ditandai `synthetic: true` dan disebut di blok
      `synthetic` — RYO belum pernah mengirim status itu (0/3645, `id <= 3645`, 6 Sep 2026),
      jadi envelope padam yang tidak mengaku dibentuk adalah klaim yang tidak bisa diperiksa
      siapa pun. Predikatnya SAMA dengan yang dipakai `agent/demo.py`; sebelumnya fungsi ini
      hanya menguji `status`, sehingga berkas ber-warning `SYNTHETIC` + `status: ok` membuat
      demo mencetak KETIDAKCOCOKAN sementara CLI ini keluar rc=0 atas berkas yang sama;
    * daftar `envelopes_rewritten_to_unavailable` WAJIB persis sama dengan baris yang ditandai —
      blok pengakuan yang menyebut lebih sedikit (atau lebih banyak) dari yang benar-benar
      ditulis ulang sama menyesatkannya dengan tidak mengaku sama sekali;
    * setiap envelope yang MENJAWAB dan membawa :data:`SOURCE_HASH_KEY` wajib COCOK dengan hash
      itu (:func:`verify_provenance`). Bentuk envelope yang rapi tidak menjamin apa-apa tentang
      ISInya: satu angka yang diketik ulang di dalam envelope nyata menggeser keputusan tanpa
      satu peringatan pun, dan berkasnya tetap menyebut dirinya salinan bukti nyata.

    Fixture tanpa satu pun baris sintetis sah: yang dikembalikan blok kosong.
    """
    rows = fixture.get("snapshots")
    rows = rows if isinstance(rows, list) else []
    unconfessed = [
        f"{row.get('tool')} (source_id {row.get('source_id')})"
        for row in rows
        if isinstance(row, Mapping)
        and is_shaped(row.get("envelope") if isinstance(row.get("envelope"), Mapping) else None)
        and row.get("synthetic") is not True
    ]
    if unconfessed:
        raise OutageFixtureError(
            "envelope berbentuk padam (status unavailable atau warning SYNTHETIC) yang TIDAK "
            f"mengaku dibentuk: {', '.join(unconfessed)}. RYO belum pernah mengirim status itu "
            "(0/3645, `id <= 3645`, 6 Sep 2026), jadi envelope ini tidak bisa dicetak sebagai bukti."
        )
    provenance = verify_provenance(fixture)
    if provenance.mismatched:
        raise OutageFixtureError(
            "isi envelope tidak cocok dengan sha256 baris asal yang direkam berkas ini "
            f"(`{SOURCE_HASH_KEY}`): source_id "
            f"{', '.join(str(entry) for entry in provenance.mismatched)}. Berkas ini menyebut "
            "dirinya salinan bukti nyata, padahal isinya sudah berubah sejak disalin."
        )
    labels = _labels(fixture)
    if not labels:
        return {}
    note = fixture.get("synthetic")
    if not isinstance(note, Mapping):
        raise OutageFixtureError(
            f"{len(labels)} envelope ditandai `synthetic: true` tetapi berkasnya tidak punya blok "
            "`synthetic` yang menerangkannya"
        )
    listed = note.get("envelopes_rewritten_to_unavailable")
    listed = [str(entry) for entry in listed] if isinstance(listed, list) else []
    if listed != labels:
        raise OutageFixtureError(
            f"blok `synthetic` menyebut {listed or 'nol'} envelope ditulis ulang, sementara "
            f"snapshotnya menandai {labels}"
        )
    if not str(note.get("why") or "").strip():
        raise OutageFixtureError("blok `synthetic` tidak menyebut ALASAN envelope itu dibentuk")
    return dict(note)


def confession_lines(fixture: Mapping[str, Any], path: str | Path) -> list[str]:
    """Pengakuan asal-usul envelope padam — DIBACA dari blok `synthetic` berkasnya sendiri.

    Kalimatnya tidak dikarang di sini; yang ditambah CLI hanyalah kerangkanya. Teks berkas
    dibersihkan :func:`core.skill.clean_text` seperti teks luar lainnya (`--fixture` argv).

    Yang TIDAK dikutip dari berkas: kalimat tentang envelope yang MENJAWAB. Ia datang dari
    :func:`provenance_lines` atas hasil :func:`verify_provenance`, jadi "salinan apa adanya"
    hanya tercetak untuk baris yang sha256 envelopenya benar-benar cocok dengan hash baris asal;
    sisanya tercetak sebagai pengakuan yang TIDAK DIVERIFIKASI.
    """
    note = verified_confession(fixture)

    def safe(text: Any) -> str:
        return clean_text(str(text), CONFESSION_TEXT_LIMIT)

    head = f"PENGAKUAN — dibaca dari blok `synthetic` berkas {safe(path)}:"
    label = safe(fixture.get("source_db") or "?")
    if not note:
        return [
            head,
            "  berkas ini tidak menyatakan satu pun envelope sintetis.",
            *provenance_lines(verify_provenance(fixture), label),
        ]
    lines = [
        head,
        f"  envelope PADAM di bawah SINTETIS: ditulis ulang menjadi status=unavailable oleh "
        f"{safe(note.get('task'))}, dari {safe(note.get('derived_from'))}. "
        f"Yang dibentuk: {len(_labels(fixture))} envelope, yaitu",
    ]
    lines += [f"    {safe(label)}" for label in _labels(fixture)]
    lines.append("  alasannya, apa adanya dari berkas itu:")
    lines.append(f"    {safe(note.get('why'))}")
    lines.extend(provenance_lines(verify_provenance(fixture), label))
    lines.append(
        "  envelope JSON di stdout TIDAK membawa kata ini: `preflight` tidak meneruskan "
        "`warnings` envelope padam ke envelope hasil (lihat agent/demo.py)."
    )
    return lines


# --- CLI ---------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="preflight.outage_fixture",
        description="Fixture kegagalan spec §7 langkah 2 (SIMULATED, offline, nol panggilan RYO).",
    )
    parser.add_argument("--mode", choices=sorted(MODES), default="one", help="fixture yang dipakai")
    parser.add_argument("--fixture", default=None, help="berkas fixture (default: sesuai --mode)")
    parser.add_argument("--write", action="store_true", help="tulis ulang KEDUA berkas dari sumbernya")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from core.skill_cli import _envelope_text

    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.write:
            for mode in sorted(MODES):
                print(f"ditulis: {write(mode).relative_to(ROOT)}")
            return 0
        path = args.fixture or PATHS[args.mode]
        # Pengakuan LEBIH DULU, dan ia bisa menolak: envelope padam yang tidak mengaku dibentuk
        # tidak pernah sampai ke stdout.
        confession = confession_lines(_read_json(path), path)
        result = run(path)
    except OutageFixtureError as exc:
        print(f"outage_fixture: {exc}", file=sys.stderr)
        return 2
    for line in confession:
        print(line, file=sys.stderr)
    print(_envelope_text(result))
    return 0


if __name__ == "__main__":  # pragma: no cover - dijalankan sebagai perintah
    raise SystemExit(main())
