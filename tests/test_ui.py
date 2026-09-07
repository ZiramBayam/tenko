"""Tes klien untuk `ui/app.py` — TASKS 3.1 (empat route) + 3.1a (dua generasi + nol bukti).

Yang dipatok di sini, dan mengapa masing-masing ada:

* **`simulated: true` di SETIAP respons**, termasuk 400/404/422/503. Ini kewajiban aturan lomba
  (docs/rules-compliance.md), bukan field kosmetik: layar yang lupa mengatakannya adalah layar
  yang mengklaim uang sungguhan.
* **Urutan daya veto** Layar A: penahan terberat (peringkat `preflight.params.ITEM_ORDER`, spec
  §4) di atas, simbol tanpa penahan menyusul.
* **Dua generasi tidak pernah dicampur** (ADR-016): baris warisan tidak muncul di watchlist/daftar,
  dan `blocking_json`-nya (isinya integer) tidak pernah dirender.
* **Nol bukti ≠ terverifikasi**: baris ber-`snapshot_ids=[]` yang params-nya masih berlaku
  menjawab `NO_EVIDENCE` dengan `verified: false`. Empat id NYATA (`data/ryo.db` 20-23) ikut
  diuji apa adanya, BACA-SAJA; sejak `params_hash` bergeser (TASKS 0.12) vonis keempatnya
  `STALE_PARAMS` — kami tidak lagi bisa MEMBUKTIKAN mereka ditulis tanpa bukti, dan tes ini
  memaksa UI mengatakan itu, bukan menebak. Yang tidak berubah: bukan lencana hijau.
* **Temuan ≠ ketidaktahuan** (AC 0.14 butir 4): nada `MISMATCH` (`finding`) tidak pernah sama
  dengan nada `STALE_ALGO`/`STALE_PARAMS` (`not_assessed`), di kartu maupun di daftar.
* **Teks asal-DB tidak bisa menutup tag**: simbol bermuatan `</script>` keluar sebagai escape
  JSON, dan `json.loads` tetap mengembalikan teks aslinya (escape-nya lossless).
* **UI tidak menulis dan tidak memanggil apa pun**: yang diukur adalah perbuatan PROSES INI
  (`sys.addaudithook`), bukan hash berkas — lihat `WriteWatch` di bawah untuk sebabnya. Jalur
  klien RYO maupun narasi LLM dipasangi ranjau yang meledak bila disentuh.

Fixture memakai `tests/helpers_agent.build_store` (envelope RYO nyata dari `docs/samples`).
Satu-satunya tes yang menyentuh `data/ryo.db` adalah kelompok "DB hidup" di bawah, dan hanya
lewat route yang membukanya BACA-SAJA; berkasnya tidak pernah ditulis (dipatok `WriteWatch`).
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from html import escape, unescape
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import ui.app as ui_app
import ui.screens as ui_screens
from agent.ledger import recent_decisions
from agent.main import tick
from agent.params import DEFAULT_AGENT_PARAMS
from agent.replay import (
    VERDICT_IDENTICAL,
    VERDICT_NO_EVIDENCE,
    VERDICT_STALE_ALGO,
    VERDICT_STALE_PARAMS,
)
from agent.watchlist import plausible_symbol
from core.replay import ALGO
from core.skill import REASON_LIMIT, TEXT_LIMIT, TRUNCATED_MARK, ItemResult, clean_text
from core.store import Store, StoreReadOnlyError
from preflight.params import DEFAULT_PARAMS, ITEM_ORDER
from preflight.skill import symbol_evidence, symbol_tools
from tests.helpers_agent import ANALYZE, CORE, DEEP, build_store
from ui.app import (
    BANNER,
    HTML_HEADERS,
    KAGE_HTML_HEADERS,
    TONE_FINDING,
    TONE_NO_EVIDENCE,
    TONE_NOT_ASSESSED,
    app,
)
from ui.app import JST as UI_JST
from ui.screens import (
    FOREIGN_MARK_NOTE,
    LEGACY_REASON_CUT_NOTE,
    NARRATION_EMPTY,
    NARRATION_LABEL,
    NARRATION_NOTE,
    NEUTRAL_MARK,
    REASON_CUT_NOTE,
    SCREEN_CUT_NOTE,
    UNSPOKEN_MARK,
    VALUE_LIMIT,
    age_phrase,
    render_card,
    render_watchlist,
)

ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = ROOT / "data" / "ryo.db"

#: Id nyata di `data/ryo.db` yang ditulis dengan `snapshot_ids_json='[]'` (TASKS 3.1a AC);
#: sejak TASKS 0.12 params-nya usang, jadi vonisnya `STALE_PARAMS` (TASKS 0.14), bukan `NO_EVIDENCE`.
LIVE_ZERO_EVIDENCE_IDS = (20, 21, 22, 23)
#: Id nyata generasi lama (kartu tanpa `algo`); barisnya ada, jadi jawabannya 200.
LIVE_LEGACY_ID = 13
#: Keputusan generasi baru yang pertama di DB hidup — batas bawah `min_id` /api/decisions.
LIVE_FIRST_CURRENT_ID = 20


def ryo_now() -> datetime:
    """Jam penyemai fixture: sekarang, DIPOTONG ke detik seperti jam yang dipakai RYO.

    Dua alasan, keduanya soal MEMBUAT BARIS UJI REALISTIS, bukan melonggarkan penjaganya:

    * `build_store` menyelaraskan `as_of` baris dengan `ts_utc`-nya (TASKS 2.2b: `ts_utc` tidak
      dijaga hash apa pun, `as_of` dijaga `payload_hash`, jadi selisih > 300 dtk kini membuang
      barisnya). Selarasnya diurus helper; presisinya tidak.
    * `as_of` RYO berpresisi DETIK di seluruh amatan (476/476, ADR-015 poin 5), dan itu yang
      diasumsikan `core.skill._iso_z`/`preflight.items._iso`: envelope yang keluar dipotong ke
      detik, sedangkan `agent.anchor.recompute` membandingkannya dengan `ItemSource` mentah dari
      bukti beku. Jam fixture ber-mikrodetik membuat kesepuluh item "tidak cocok dengan bukti
      beku" dan agen menolak menulis satu baris pun — bentuk jam yang tidak pernah datang dari
      RYO. Jadi jam uji dibuat seperti jam nyata; ambang skew tidak disentuh.
    """
    return datetime.now(UTC).replace(microsecond=0)


def align_inner_clocks(tool: str, symbol: str, envelope: dict[str, Any]) -> None:
    """Jam DI DALAM `data` mengikuti `as_of` envelope yang sama — seperti envelope RYO nyata.

    `build_store` sudah menyelaraskan `as_of` dengan `ts_utc` baris (TASKS 2.2b). Tetapi
    `monitor_market_sentiment_shift` membawa DUA cap waktu lagi di dalam `data`, dan item 1
    (`preflight/items.py`) membaca `data.observation_as_of` sebagai kandidat umur di samping
    `as_of` lalu mengambil yang TERTUA. Dibiarkan pada nilai sampel (1 Sep), umur bukti
    terbaca ~4 hari terhadap jam acuan bundle, item 1 jadi `unspoken`, dan SETIAP simbol
    ditahan `01_freshness` — penahan yang tidak diuji tes mana pun di berkas ini.

    Di sampel nyata ketiganya SAMA persis (`docs/samples/monitor_market_sentiment_shift.json`:
    `as_of` = `data.observation_as_of` = `data.source.timestamp` = 2026-09-01T03:48:32Z), jadi
    menyelaraskannya membuat baris uji berbentuk seperti baris nyata — bukan melonggarkan item 1.
    """
    data = envelope.get("data")
    as_of = envelope.get("as_of")
    if not isinstance(data, dict) or not isinstance(as_of, str):
        return
    if "observation_as_of" in data:
        data["observation_as_of"] = as_of
    source = data.get("source")
    if isinstance(source, dict) and "timestamp" in source:
        source["timestamp"] = as_of


def break_two_symbols(tool: str, symbol: str, envelope: dict[str, Any]) -> None:
    """BTC ditahan item 6 (+10), ETH ditahan item 5 — dua penahan dengan daya veto BERBEDA.

    Nilai yang dipasang ada di tabel kosakata `preflight/params.py` (`avoid` = abstain, 4×
    amatan; `MIXED` = confluence 0.5, 125× amatan): fixture ini tidak mengarang kosakata.
    """
    align_inner_clocks(tool, symbol, envelope)
    data = envelope.get("data")
    if not isinstance(data, dict):
        return
    if tool == DEEP and symbol == "BTC" and isinstance(data.get("confluence"), dict):
        data["confluence"]["state"] = "MIXED"
        data["confluence"]["score"] = 0.5
    if tool == ANALYZE and symbol == "ETH":
        data["verdict"] = "avoid"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    """Store fixture + satu tick agen: SOL GO, ETH[05], BTC[06,10], BNB[10]."""
    opened = build_store(tmp_path, mutate=break_two_symbols, at=ryo_now())
    tick(opened, CORE, DEFAULT_AGENT_PARAMS)
    yield opened
    opened.close()


@pytest.fixture
def client(store: Store, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Klien yang membaca store fixture. Path DB TIDAK PERNAH datang dari request."""
    monkeypatch.setattr(app.state, "db_path", str(store.path))
    with TestClient(app) as opened:
        yield opened


@pytest.fixture
def empty_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Klien atas store yang punya bukti tetapi NOL keputusan — menguji keadaan kosong."""
    empty = build_store(tmp_path, name="empty.db", at=ryo_now(), mutate=align_inner_clocks)
    monkeypatch.setattr(app.state, "db_path", str(empty.path))
    with TestClient(app) as opened:
        yield opened
    empty.close()


def legacy_row(store: Store, symbol: str = "SOL") -> int:
    """Baris bergaya generasi lama: `card_json` TANPA `algo`, `blocking_json` berisi INTEGER.

    Bentuknya persis 19 baris warisan di `data/ryo.db` (mis. id 2: `[5,6,10]`).
    """
    ids = [row.id for row in store.get_snapshots([1, 2, 3])]
    return store.insert_decision(
        symbol=symbol,
        decision="NO_GO",
        blocking=[5, 6, 10],
        snapshot_ids=ids,
        params_hash="a374aa6e" + "0" * 56,
        decision_hash="f" * 64,
        card={"skill": {"status": "ok"}, "agent": {"module": "gone"}},
    )


def zero_evidence_row(store: Store, symbol: str = "SOL") -> int:
    """Baris generasi berjalan yang dibukukan TANPA satu snapshot pun (bentuk id 20-23)."""
    from agent.anchor import recompute
    from core.replay import decision_hash, freeze

    bundle = freeze(store, [])
    recomputed = recompute(symbol, bundle, DEFAULT_PARAMS)
    return store.insert_decision(
        symbol=symbol,
        decision="NO_GO",
        blocking=["01_freshness"],
        snapshot_ids=[],
        params_hash=DEFAULT_PARAMS.params_hash(),
        decision_hash=decision_hash(bundle, DEFAULT_PARAMS.as_dict(), recomputed.decision.as_material()),
        card={
            "algo": ALGO,
            # Bentuk kartu baris 20-23 yang NYATA: envelope penolakan (`data=None`) yang tetap
            # menyebut kesepuluh baris panggilan lewat `availability`.
            "skill": {
                "status": "unavailable",
                "availability": {item_id: "unspoken" for item_id in ITEM_ORDER},
            },
            "agent": {},
        },
    )


def by_symbol(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["symbol"]: entry for entry in payload["symbols"]}


# --- kewajiban lomba: setiap respons mengaku simulasi ---------------------------------------
def test_every_route_including_its_errors_says_simulated(client: TestClient, store: Store) -> None:
    """Kalau satu saja lupa, layar mengklaim uang sungguhan (docs/rules-compliance.md).

    DITULIS ULANG untuk TASKS 3.5: `/` bukan lagi JSON melainkan Layar A, jadi menuntut field
    `simulated` di badannya akan menuntut bentuk yang salah. Yang TIDAK dilonggarkan adalah
    jaminannya — `/` sekarang wajib membawa banner SIMULATED yang TERBACA di halaman, penanda
    `<meta name="tenko-simulated" content="true">`, dan CSP; sisanya tetap wajib membawa
    `simulated: true` di JSON. Kalau `/` diam-diam kembali menjadi JSON, cabang HTML di bawah
    gagal; kalau ia kehilangan banner, ia gagal juga.
    """
    screen = client.get("/")
    assert screen.status_code == 200
    assert screen.headers["content-type"].startswith("text/html")
    assert BANNER in screen.text, "`/` tanpa banner SIMULATED"
    assert "practice" in screen.text and "No real funds" in screen.text
    assert '<meta name="tenko-simulated" content="true">' in screen.text
    assert "default-src 'none'" in screen.headers["content-security-policy"]
    paths = [
        "/api/routes",
        "/api/health",
        "/api/watchlist",
        "/api/decisions",
        "/api/decision/1",
        "/api/decision/999999",  # 404
        "/api/decision/abc",  # 422
        "/api/decision/0",  # 422: id di luar rentang bawah
        f"/api/decision/{2**63}",  # 422: id di luar INTEGER SQLite
        "/api/decision/" + "9" * 40,  # 422: id 40 digit
        "/api/preflight/SOL",
        "/api/preflight/DOGE",  # 404: nol bukti tentang simbol itu
        "/api/preflight/%20",  # 400: simbol tidak terbaca
        "/api/preflight/%7B%7D",  # 400: `{}` tidak berbentuk simbol
        "/favicon.ico",  # 404 router: path yang TIDAK dideklarasikan route mana pun
        "/api/decision/1/extra",  # 404 router: di bawah prefix yang dikenal
        "/api",  # 404 router
    ]
    for path in paths:
        response = client.get(path)
        body = response.json()
        assert body["simulated"] is True, path
        assert body["banner"] == BANNER, path
        assert "practice" in body["banner"] and "No real funds" in body["banner"]


def test_a_path_no_route_declares_still_says_simulated(client: TestClient) -> None:
    """404 router Starlette dulu keluar sebagai `{"detail":"Not Found"}` — tanpa `simulated`.

    Tes di atas hanya menembak path yang MEMANG dideklarasikan sebuah route, jadi lubang ini
    tidak dijaga apa pun: `app.exception_handler(fastapi.HTTPException)` tidak pernah dipanggil
    untuk 404 yang dilempar router, karena router melempar kelas INDUKNYA di Starlette.
    """
    response = client.get("/favicon.ico")
    assert response.status_code == 404
    body = response.json()
    assert body["simulated"] is True and body["banner"] == BANNER
    assert body["status_code"] == 404
    assert "detail" not in body


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/api/health"), ("delete", "/api/watchlist"), ("put", "/api/decision/1")],
)
def test_a_wrong_method_says_simulated_and_writes_nothing(
    client: TestClient, store: Store, method: str, path: str
) -> None:
    """405 juga dilempar router (kelas induk yang sama) — dan UI tetap baca-saja."""
    before = hashlib.sha256(Path(store.path).read_bytes()).hexdigest()
    response = getattr(client, method)(path)
    assert response.status_code == 405
    body = response.json()
    assert body["simulated"] is True and body["banner"] == BANNER
    assert body["status_code"] == 405
    assert "GET" in response.headers.get("allow", "")  # metode yang benar tetap disebut
    assert hashlib.sha256(Path(store.path).read_bytes()).hexdigest() == before


@pytest.mark.parametrize("decision_id", [str(2**63), str(2**63 + 1), "9" * 40, "0", "-1"])
def test_an_id_outside_the_sqlite_integer_range_is_422_not_a_crash(
    client: TestClient, decision_id: str
) -> None:
    """Satu GET tanpa autentikasi: id ≥ 2**63 membuat sqlite3 melempar `OverflowError`.

    `OverflowError` bukan `sqlite3.Error`, jadi ia lolos dari `agent/ledger.py` DAN dari
    `except (StoreError, LedgerError)` di route — dan `ServerErrorMiddleware` menjawab
    `text/plain` "Internal Server Error" tanpa `simulated`. Sekarang id di luar rentang ditolak
    di batas route, bentuk yang sama dengan `/api/decision/abc`.
    """
    response = client.get(f"/api/decision/{decision_id}")
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["simulated"] is True and body["banner"] == BANNER
    assert "nothing was read from the store" in body["error"]


def test_an_unexpected_failure_still_admits_it_is_a_simulation(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Jaring terakhir: bug pun menjawab JSON ber-`simulated`, bukan teks polos.

    `raise_server_exceptions=False` dipakai supaya yang diperiksa adalah apa yang DIKIRIM ke
    kabel; Starlette tetap melempar ulang exception-nya sesudah itu, jadi kegagalannya tidak
    hilang dari log server.
    """

    def explode(*args: object, **kwargs: object) -> None:
        raise TypeError("bukan StoreError, bukan LedgerError")

    monkeypatch.setattr(app.state, "db_path", str(store.path))
    monkeypatch.setattr(ui_app, "recent_decisions", explode)
    with TestClient(app, raise_server_exceptions=False) as opened:
        response = opened.get("/api/watchlist")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["simulated"] is True and body["banner"] == BANNER
    assert "TypeError" not in json.dumps(body)  # jenisnya ke log, bukan ke layar


def test_banner_is_the_one_sentence_the_video_shows() -> None:
    assert BANNER == ("SIMULATED — practice positions on read-only market intelligence. No real funds.")


# --- Layar A: watchlist ---------------------------------------------------------------------
def test_watchlist_orders_symbols_by_the_veto_power_of_their_blocker(client: TestClient) -> None:
    """ETH[05] > BTC[06,10] > BNB[10] > SOL GO — bentuk yang sama dengan bukti live 1 Sep."""
    payload = client.get("/api/watchlist").json()
    assert [entry["symbol"] for entry in payload["symbols"]] == ["ETH", "BTC", "BNB", "SOL"]
    assert payload["n"] == 4
    ranks = [entry["veto_rank"] for entry in payload["symbols"]]
    assert ranks == [5, 6, 10, None]


def test_watchlist_names_the_heaviest_blocker_with_its_evidence(client: TestClient) -> None:
    """Tunjuk→sebut juga di Layar A: item, tool, field, jam — bukan sekadar 'NO_GO'."""
    entry = by_symbol(client.get("/api/watchlist").json())["BTC"]
    top = entry["top_blocker"]
    assert top["item_id"] == "06_confluence"
    assert top["veto_rank"] == ITEM_ORDER.index("06_confluence") + 1
    assert top["tool"] == "deep_analysis"
    assert top["field"] == "data.confluence.state"
    assert top["status"] == "red"
    assert top["as_of_jst"].endswith("JST")
    assert entry["blocking"] == ["06_confluence", "10_relative"]  # urut daya veto
    assert entry["n_blocking"] == 2


def test_watchlist_counts_items_that_were_not_spoken(client: TestClient) -> None:
    """ "Berapa item tidak tersebut" adalah angka ber-`n`, bukan kesan."""
    for entry in client.get("/api/watchlist").json()["symbols"]:
        assert entry["n_items"] == len(ITEM_ORDER)
        assert isinstance(entry["n_unspoken"], int)
        assert 0 <= entry["n_unspoken"] <= entry["n_items"]
        assert entry["as_of"] and entry["as_of_jst"].endswith("JST")


def test_watchlist_shows_a_go_symbol_without_a_blocker(client: TestClient) -> None:
    entry = by_symbol(client.get("/api/watchlist").json())["SOL"]
    assert entry["decision"] == "GO"
    assert entry["blocking"] == [] and entry["top_blocker"] is None
    assert entry["verified"] is True and entry["replay_status"] == VERDICT_IDENTICAL


def test_watchlist_with_no_decisions_says_n_is_zero(empty_client: TestClient) -> None:
    """Keadaan kosong dieja, bukan ditampilkan sebagai layar kosong yang ambigu."""
    payload = empty_client.get("/api/watchlist").json()
    assert payload["n"] == 0
    assert payload["symbols"] == []
    assert any("no data yet (n=0)" in note for note in payload["notes"])


# --- TASKS 3.1a: dua generasi -------------------------------------------------------------
def test_watchlist_and_decisions_show_only_rows_that_carry_algo(client: TestClient, store: Store) -> None:
    """Baris warisan TERBARU sekalipun tidak boleh menggeser baris generasi berjalan."""
    legacy_id = legacy_row(store, "SOL")

    watch = client.get("/api/watchlist").json()
    assert legacy_id not in [entry["decision_id"] for entry in watch["symbols"]]
    assert watch["n_legacy_hidden"] == 1
    assert by_symbol(watch)["SOL"]["decision"] == "GO"  # keputusan generasi berjalan, bukan warisan

    listed = client.get("/api/decisions").json()
    assert legacy_id not in [entry["decision_id"] for entry in listed["decisions"]]
    assert listed["n_legacy_skipped"] == 1
    assert any("from an earlier generation" in note for note in listed["notes"])


def test_decisions_min_id_is_at_least_the_first_current_generation_row(
    client: TestClient, store: Store
) -> None:
    """AC 3.1a: `min(id)` daftar ≥ id keputusan generasi baru pertama."""
    for _ in range(3):
        legacy_row(store, "BTC")
    first_current = min(entry["decision_id"] for entry in client.get("/api/decisions").json()["decisions"])
    payload = client.get("/api/decisions").json()
    assert payload["min_id"] == first_current
    assert payload["min_id"] >= 1
    assert all(entry["decision_id"] >= payload["min_id"] for entry in payload["decisions"])


def test_a_legacy_id_is_200_with_stale_algo_and_no_rendered_blocking(
    client: TestClient, store: Store
) -> None:
    """Barisnya ADA, jadi bukan 404; tetapi tidak satu pun angkanya boleh dirender."""
    legacy_id = legacy_row(store)
    response = client.get(f"/api/decision/{legacy_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["simulated"] is True
    assert body["replay_status"] == VERDICT_STALE_ALGO
    assert body["verified"] is False
    assert body["items"] == []
    assert body["generation"] == "legacy"
    assert body["blocking"] == [] and body["blocking_rendered"] is False
    # `blocking_json` warisan berisi [5, 6, 10]: tidak boleh muncul sebagai daftar penahan.
    assert "5" not in [str(entry) for entry in body["blocking"]]
    assert any("no longer exists" in note for note in body["notes"])  # satu kalimat penjelasan
    assert any("holds integers" in note for note in body["notes"])
    assert body["narration"] is None


def test_stale_algo_still_counts_the_evidence_that_is_left(client: TestClient, store: Store) -> None:
    """Bukti yang tersisa disebut apa adanya — itu satu-satunya yang bisa ditawarkan."""
    body = client.get(f"/api/decision/{legacy_row(store)}").json()
    assert body["n_snapshots"] == 3
    assert body["n_snapshots_verifiable"] == 3
    assert body["n_snapshots_recorded"] == 3
    assert len(body["replay_bundle"]) == 3


# --- TASKS 3.1a: nol bukti ------------------------------------------------------------------
def test_a_zero_evidence_row_is_never_a_green_badge(client: TestClient, store: Store) -> None:
    """ "IDENTICAL atas nol bukti" terlihat seperti verifikasi; di sini ia bernama sendiri."""
    zero_id = zero_evidence_row(store)
    response = client.get(f"/api/decision/{zero_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["replay_status"] == VERDICT_NO_EVIDENCE
    assert body["verified"] is False
    assert body["replay_tone"] == TONE_NO_EVIDENCE
    assert VERDICT_IDENTICAL not in json.dumps(body)
    assert body["snapshot_ids"] == [] and body["replay_bundle"] == []
    assert any(
        "without a single piece of evidence" in note or "NOT verification" in note for note in body["notes"]
    )


def stale_params_row(store: Store, symbol: str = "SOL") -> int:
    """Baris generasi berjalan yang ditulis dengan ambang LAIN (bentuk id 20-23 hari ini)."""
    return store.insert_decision(
        symbol=symbol,
        decision="NO_GO",
        blocking=["01_freshness"],
        snapshot_ids=[],
        params_hash="b" * 64,  # bukan params yang berlaku: replay tidak menilai baris ini
        decision_hash="c" * 64,
        card={"algo": ALGO, "skill": {"status": "unavailable"}, "agent": {}},
    )


def mismatch_row(store: Store, symbol: str = "BTC") -> int:
    """Baris ber-bukti yang hash-nya TIDAK cocok: temuan, bukan ketidaktahuan."""
    ids = [row.id for row in store.get_snapshots([1, 2, 3])]
    return store.insert_decision(
        symbol=symbol,
        decision="NO_GO",
        blocking=["01_freshness"],
        snapshot_ids=ids,
        params_hash=DEFAULT_PARAMS.params_hash(),
        decision_hash="0" * 64,
        card={"algo": ALGO, "skill": {"status": "ok"}, "agent": {}},
    )


def test_stale_params_says_it_did_not_look_instead_of_accusing(client: TestClient, store: Store) -> None:
    """AC 0.14: `params_hash` bergeser → kami tidak menilai; nadanya bukan tuduhan."""
    body = client.get(f"/api/decision/{stale_params_row(store)}").json()
    assert body["replay_status"] == VERDICT_STALE_PARAMS
    assert body["verified"] is False
    assert body["replay_tone"] == TONE_NOT_ASSESSED
    assert VERDICT_IDENTICAL not in json.dumps(body)
    assert any("we did NOT judge it at all" in note for note in body["notes"])
    # Ketidaktahuan dikatakan sebagai ketidaktahuan: kalimatnya menolak DUA-DUANYA sekaligus.
    assert any("it is not a finding either" in note for note in body["notes"])
    assert "finding:" not in body["replay_label"]


def test_a_finding_and_an_unassessed_row_never_share_a_tone(client: TestClient, store: Store) -> None:
    """AC 0.14 (4): juri melihat keduanya berdampingan di /api/decisions dan Layar A."""
    stale_id = stale_params_row(store)
    bad_id = mismatch_row(store)
    listed = {entry["decision_id"]: entry for entry in client.get("/api/decisions").json()["decisions"]}
    stale, bad = listed[stale_id], listed[bad_id]
    assert stale["replay_status"] == VERDICT_STALE_PARAMS and bad["replay_status"] == "MISMATCH"
    assert stale["replay_tone"] == TONE_NOT_ASSESSED
    assert bad["replay_tone"] == TONE_FINDING
    assert stale["replay_tone"] != bad["replay_tone"]
    assert stale["replay_label"] != bad["replay_label"]
    assert "finding" in bad["replay_label"] and "finding" not in stale["replay_label"]
    # Perbedaannya juga dikatakan sekali, dengan cacahnya, di catatan daftar.
    notes = " ".join(client.get("/api/decisions").json()["notes"])
    assert TONE_NOT_ASSESSED in notes and TONE_FINDING in notes
    watch_notes = " ".join(client.get("/api/watchlist").json()["notes"])
    assert TONE_NOT_ASSESSED in watch_notes


def test_a_zero_evidence_row_still_shows_the_ten_unanswered_lines(client: TestClient, store: Store) -> None:
    """Barisnya tidak disembunyikan: sepuluh baris panggilan tetap ada, semuanya `unspoken`."""
    body = client.get(f"/api/decision/{zero_evidence_row(store)}").json()
    assert [row["item_id"] for row in body["items"]] == list(ITEM_ORDER)
    assert {row["status"] for row in body["items"]} == {"unspoken"}
    assert body["n_unspoken"] == len(ITEM_ORDER)


# --- bukti yang DIBUANG tetap disebut di layar (TASKS 2.2b AC(7)) --------------------------
@pytest.fixture
def skewed_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Store yang salah satu barisnya DIBUANG `latest_fresh` karena `ts_utc` vs `as_of`.

    Bentuknya persis yang ditutup TASKS 2.2b: satu baris `deep_analysis` BTC yang `ts_utc`-nya
    "beberapa detik lalu" padahal `as_of`-nya tiga hari lalu. `ts_utc` tidak dijaga hash apa
    pun; `as_of` dijaga `payload_hash`. Barisnya dilewati, dan bukti BTC jatuh ke baris lama
    yang masih segar — jadi kartunya tetap terbentuk, dan pertanyaannya menjadi: apakah UI
    MENYEBUT baris yang dibuang itu, atau menelannya?
    """
    from preflight.skill import tool_args
    from tests.helpers_agent import envelope_for

    moment = ryo_now()
    store = build_store(tmp_path, mutate=break_two_symbols, at=moment, name="skewed.db")
    envelope = envelope_for(DEEP, "BTC")
    break_two_symbols(DEEP, "BTC", envelope)
    # `as_of` TIGA HARI lebih tua dari `ts_utc` baris yang sama: jauh di atas toleransi 300 dtk,
    # dan bentuk yang tidak pernah muncul di 2537 baris nyata (skew maksimum +71 dtk).
    envelope["as_of"] = (moment - timedelta(days=3)).isoformat().replace("+00:00", "Z")
    store.insert_snapshot(
        DEEP,
        tool_args("BTC", "swing", DEFAULT_PARAMS)[DEEP],
        envelope,
        ts_utc=moment.isoformat(),
    )
    tick(store, ("BTC",), DEFAULT_AGENT_PARAMS)
    monkeypatch.setattr(app.state, "db_path", str(store.path))
    with TestClient(app) as opened:
        yield opened
    store.close()


def test_a_row_dropped_for_clock_skew_is_named_on_the_screen(skewed_client: TestClient) -> None:
    """Bukti yang dibuang tidak boleh hilang diam-diam di lapisan UI (PM, TASKS 2.2b AC(7)).

    `core.store.latest_fresh` menyebut baris yang dilewatinya, `preflight.skill` menaruhnya di
    `warnings` envelope — dan di sinilah rantai itu bisa putus tanpa terlihat: kartu yang hanya
    merender `items` akan menampilkan bukti BTC yang lebih tua tanpa satu kalimat pun tentang
    baris yang lebih baru dan ditolak.
    """
    card_id = skewed_client.get("/api/decisions").json()["decisions"][0]["decision_id"]
    body = skewed_client.get(f"/api/decision/{card_id}").json()
    assert body["n_warnings"] >= 1
    assert body["n_warnings"] == len(body["warnings"])
    dropped = [line for line in body["warnings"] if "disagree" in line]
    assert dropped, body["warnings"]
    assert "skipped" in dropped[0] and "snapshot_id=" in dropped[0]
    # Angkanya berdampingan dengan kalimatnya: juri tidak perlu membuka `warnings` untuk tahu.
    assert any(f"{body['n_warnings']} envelope warnings" in note for note in body["notes"])


def test_the_watchlist_counts_the_warnings_it_does_not_print(skewed_client: TestClient) -> None:
    """Layar A tidak memuat teks peringatan, tetapi tetap membawa cacahnya (`n`), bukan nol."""
    entry = by_symbol(skewed_client.get("/api/watchlist").json())["BTC"]
    assert entry["n_warnings"] >= 1


def test_preflight_repeats_the_dropped_row_instead_of_swallowing_it(skewed_client: TestClient) -> None:
    """Route yang MENGHITUNG ULANG kartu memakai rantai yang sama, jadi ia juga menyebutnya."""
    body = skewed_client.get("/api/preflight/BTC").json()
    assert any("disagree" in line for line in body["warnings"])
    assert body["n_warnings"] == len(body["warnings"])
    assert any("envelope warnings" in note for note in body["notes"])


def test_a_card_without_dropped_evidence_names_no_dropped_row(client: TestClient) -> None:
    """Kebalikannya juga dipatok: tanpa baris yang dibuang, tidak ada kalimat yang mengarang satu.

    Kartu normal TETAP membawa peringatan (mis. daftar peer envelope beku ≠ `params.peers`,
    yang memang harus terbaca), jadi yang diuji di sini bukan "nol peringatan" melainkan: tidak
    satu pun peringatan berbicara tentang bukti yang dilewati, sementara cacahnya tetap cocok
    dengan isinya.
    """
    card_id = client.get("/api/decisions").json()["decisions"][0]["decision_id"]
    body = client.get(f"/api/decision/{card_id}").json()
    assert body["n_warnings"] == len(body["warnings"])
    assert not [line for line in body["warnings"] if "disagree" in line or "skipped" in line]


# --- Layar B: kartu panggilan ---------------------------------------------------------------
def test_the_card_has_ten_point_and_say_rows_with_source_and_time(client: TestClient) -> None:
    listed = client.get("/api/decisions").json()["decisions"]
    card_id = next(entry["decision_id"] for entry in listed if entry["symbol"] == "BTC")
    body = client.get(f"/api/decision/{card_id}").json()

    assert [row["item_id"] for row in body["items"]] == list(ITEM_ORDER)
    assert body["items"][0]["veto_rank"] == 1
    for row in body["items"]:
        assert row["status"] in {"green", "red", "unspoken"}
        assert row["reason"]
        assert "tool" in row and "field" in row and "snapshot_id" in row
        if row["as_of"]:
            assert row["as_of_jst"].endswith("JST")
    assert body["decision"] == "NO_GO"
    assert body["blocking"] == ["06_confluence", "10_relative"]
    assert len(body["decision_hash"]) == 64
    assert body["replay_status"] == VERDICT_IDENTICAL and body["verified"] is True
    assert body["replay_command"] == f"make replay ID={card_id}"
    assert len(body["replay_bundle"]) == body["n_snapshots"] == 5
    assert {entry["tool"] for entry in body["replay_bundle"]} >= {"deep_analysis", "market_overview"}


def test_the_card_offers_narration_shape_even_when_there_is_none(client: TestClient) -> None:
    """TASKS 3.2 menempelkan narasi LLM di samping `reason` deterministik item yang sama.

    Kartu agen tidak membawa narasi (agen tidak memanggil LLM), dan UI juga tidak: yang
    disediakan adalah bentuk `Narration.as_summary()` ber-status `narration_unavailable` —
    sehingga layar tidak perlu cabang khusus untuk membedakan "belum ada" dari "gagal".
    """
    card_id = client.get("/api/decisions").json()["decisions"][0]["decision_id"]
    narration = client.get(f"/api/decision/{card_id}").json()["narration"]
    assert narration["status"] == "narration_unavailable"
    assert narration["n_lines"] == 0
    assert narration["n_items"] == len(ITEM_ORDER)
    assert "LLM" in narration["reason"]
    assert narration["lines"] == []


def test_a_missing_decision_is_an_honest_404(client: TestClient) -> None:
    response = client.get("/api/decision/999999")
    assert response.status_code == 404
    body = response.json()
    assert body["simulated"] is True
    assert "999999" in body["error"]


def test_an_unreadable_id_is_422_and_reads_nothing(client: TestClient) -> None:
    response = client.get("/api/decision/abc")
    assert response.status_code == 422
    assert response.json()["simulated"] is True
    assert "nothing was read from the store" in response.json()["error"]


# --- /api/preflight -------------------------------------------------------------------------
def test_preflight_recomputes_a_card_from_stored_snapshots(client: TestClient) -> None:
    body = client.get("/api/preflight/BTC").json()
    assert body["symbol"] == "BTC"
    assert body["decision"] == "NO_GO"
    assert body["blocking"][0] == "06_confluence"
    assert [row["item_id"] for row in body["items"]] == list(ITEM_ORDER)
    assert body["n_items"] == 10
    assert len(body["decision_hash"]) == 64
    assert body["envelope"]["simulated"] is True
    assert any("no RYO tool was called" in note for note in body["notes"])


def test_preflight_normalizes_the_symbol_like_the_recorder(client: TestClient) -> None:
    assert client.get("/api/preflight/btc").json()["symbol"] == "BTC"
    assert client.get("/api/preflight/%20sol%20").json()["symbol"] == "SOL"


def test_preflight_for_a_symbol_without_evidence_is_an_honest_404(client: TestClient) -> None:
    """DOGE: bukti market-wide ADA, bukti tentang DOGE tidak — dan itu yang dikatakan."""
    response = client.get("/api/preflight/DOGE")
    assert response.status_code == 404
    body = response.json()
    assert body["simulated"] is True
    assert "DOGE" in body["error"]
    assert "analyze_token" in body["error"] and "deep_analysis" in body["error"]
    assert "market-wide" in body["error"]


@pytest.mark.parametrize("symbol", ["{}", "{", "}", "%7B%7D", "--", "%22BTC%22", "123"])
def test_preflight_of_a_shape_that_is_not_a_symbol_is_400_not_a_card(client: TestClient, symbol: str) -> None:
    """Bentuk yang bukan simbol ditolak SEBELUM store dicari — 400, bukan 200 dan bukan 404.

    Dua kelemahan berbeda bertemu di sini. Yang pertama sudah ditutup: salinan lama `ui/app.py`
    menanyakan `symbol in json.dumps(args)`, dan karena `json.dumps({}) == "{}"` simbol `{}`
    membuat dua tool MARKET-WIDE terhitung sebagai bukti ber-simbol, sehingga route menjawab 200
    dengan kartu NO_GO penuh. Yang kedua ditutup sekarang: tanpa `plausible_symbol`, jawabannya
    turun menjadi 404 "tidak ada satu pun bukti TENTANG {} di store" — kalimat yang MENYIRATKAN
    `{}` adalah simbol sah yang kebetulan belum direkam, padahal agen menolak menuliskannya.
    """
    response = client.get(f"/api/preflight/{symbol}")
    assert response.status_code == 400
    body = response.json()
    assert body["simulated"] is True
    assert "is not shaped like a symbol" in body["error"]
    assert "market_overview" not in body["error"]
    assert "monitor_market_sentiment_shift" not in body["error"]
    assert "there is no evidence ABOUT" not in body["error"]


@pytest.mark.parametrize("symbol", ["\uff33\uff2f\uff2c", "S\u041eL", "BNB\U0001f600", "BTC-PERP"])
def test_preflight_uses_the_same_symbol_shape_rule_as_the_agent(client: TestClient, symbol: str) -> None:
    """Lebar penuh, homoglyph campur-aksara, emoji, tanda baca: ditolak seperti di agen.

    `SОL` (О Kiril di tengah huruf Latin) dan `SOL` tidak bisa dibedakan mata di Layar A;
    kalau UI menjawabnya dengan 404 yang berbunyi "belum ada bukti", juri diberi kesan bahwa
    merekamnya akan menghasilkan kartu — padahal `agent.watchlist` tidak akan pernah
    menerimanya.
    """
    response = client.get(f"/api/preflight/{symbol}")
    assert response.status_code == 400
    assert response.json()["simulated"] is True
    assert not plausible_symbol(symbol.strip().upper())


def test_a_single_script_confusable_is_404_because_the_agent_accepts_it_too(
    client: TestClient,
) -> None:
    """Batas yang DIAKUI, dipatok apa adanya: `ВТС` (Kiril penuh) lolos saringan bentuk.

    `agent.watchlist.plausible_symbol` menolak CAMPURAN aksara, bukan confusable ber-aksara
    tunggal — menutupnya butuh allowlist aksara dan itu keputusan tersendiri (docstring-nya
    menyebutnya). Yang dipatok di sini bukan kelemahan itu, melainkan PARITASNYA: UI tidak boleh
    diam-diam lebih ketat daripada agen, karena dua jawaban berbeda untuk satu simbol yang sama
    lebih menyesatkan daripada satu batas yang diketahui. Kalau kelak allowlist itu ada, tes ini
    yang harus diubah bersama definisi bersamanya — bukan salinan di `ui/app.py`.
    """
    assert plausible_symbol("\u0412\u0422\u0421")
    response = client.get("/api/preflight/\u0412\u0422\u0421")
    assert response.status_code == 404
    assert response.json()["simulated"] is True


def test_ui_and_agent_share_one_definition_of_symbol_shape() -> None:
    """Definisi bersama, bukan salinan (aturan yang sama dengan `symbol_evidence`, AC 0.13)."""
    assert ui_app.plausible_symbol is plausible_symbol
    assert not [name for name in vars(ui_app) if name in ("_plausible_symbol", "_looks_like_symbol")]


def test_preflight_note_counts_only_symbol_bearing_tools(client: TestClient) -> None:
    """Kalimat "N dari M tool ber-simbol": M = 3 (analyze/compare/deep), bukan 5."""
    notes = client.get("/api/preflight/BTC").json()["notes"]
    note = next(line for line in notes if "symbol-bearing tools" in line)
    assert f"of {len(symbol_tools('BTC'))} symbol-bearing tools" in note
    assert "of 3 symbol-bearing tools" in note
    assert "market_overview" not in note and "monitor_market_sentiment_shift" not in note
    named = note.split(":", 1)[1].strip().split(", ")
    assert named == sorted(named)  # `symbol_evidence` mengembalikan daftar TERURUT
    assert set(named) <= symbol_tools("BTC")


def test_ui_and_agent_share_one_definition_of_symbol_evidence() -> None:
    """`ui.app` tidak boleh punya salinannya sendiri (AC 0.13): ia MENGIMPOR yang satu itu."""
    assert ui_app.symbol_evidence is symbol_evidence
    assert ui_app.symbol_tools is symbol_tools
    assert not [name for name in vars(ui_app) if name in ("_symbol_evidence", "_symbol_tools")]


def test_preflight_rejects_a_symbol_that_cannot_be_read(client: TestClient) -> None:
    response = client.get("/api/preflight/%20")
    assert response.status_code == 400
    assert response.json()["simulated"] is True


# --- /api/health & indeks --------------------------------------------------------------------
def test_health_states_that_nothing_is_called(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["calls_ryo_tools"] is False
    assert body["calls_llm"] is False
    assert body["db_readable"] is True
    assert body["algo"] == ALGO
    assert body["params_hash"] == DEFAULT_PARAMS.params_hash()
    assert body["n_decisions_current_generation"] == 4
    assert body["n_decisions_legacy"] == 0
    assert "MOST RECENT" in body["note"]  # cacah ≠ total tabel


def test_the_landing_board_shows_real_rows_or_no_board_at_all(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-021: papan landing dirender dari store, dan TIDAK PERNAH diisi baris contoh.

    Bug yang membuat tes ini ada: `index()` membaca `watchlist()["entries"]`, sedangkan badan
    `/api/watchlist` menamainya `symbols`. Tidak ada yang merah — papannya hanya diam-diam
    hilang di setiap kondisi, termasuk saat store penuh. Kegagalan yang KELIHATAN seperti
    perilaku "store kosong" yang sah adalah kegagalan yang paling mudah dikirim.

    Dua cabang dijaga bersama, karena masing-masing sendirian bisa dipuaskan oleh kode yang
    salah: store berisi WAJIB memberi papan dengan simbol yang sama seperti `/api/watchlist`,
    dan store yang tak terbaca WAJIB menghilangkan papannya tanpa menghilangkan halamannya.
    """
    payload = client.get("/api/watchlist").json()
    symbols = [row["symbol"] for row in payload["symbols"]]
    assert symbols, "prasyarat: store fixture ini harus punya keputusan"

    page = client.get("/")
    assert page.status_code == 200
    assert '<section class="board"' in page.text, (
        "papan hilang padahal store berisi — inilah bentuk bug `entries`/`symbols`"
    )
    for symbol in symbols[:5]:
        assert symbol in page.text, f"{symbol} ada di /api/watchlist tapi tidak di papan landing"
    # Angka pada caption adalah cacah SEBENARNYA, bukan cacah yang muat (COUNT_NAMING_RULE).
    assert f"of {len(symbols)} symbols" in page.text

    # Dan store yang tidak terbaca: halaman tetap 200 dan tetap berbanner, papannya yang pergi.
    # Dimatikan di sumbernya (`watchlist()` melempar) supaya yang diuji adalah penanganan
    # kegagalan `index()`, bukan kemampuan kita membuat berkas DB rusak.
    def dead() -> dict[str, object]:
        raise HTTPException(status_code=503, detail="the store cannot be read")

    monkeypatch.setattr(ui_app, "watchlist", dead)
    broken = client.get("/")
    assert broken.status_code == 200, "landing tidak boleh 503 — itu kesan pertama yang salah"
    assert BANNER in broken.text
    assert '<section class="board"' not in broken.text
    # Halaman itu sendiri utuh: yang hilang HANYA papannya.
    assert "<h1>" in broken.text and 'href="/app"' in broken.text


def test_index_serves_the_landing_and_the_app_is_one_click_away(client: TestClient) -> None:
    """ADR-021, DITULIS ULANG dari `test_index_serves_screen_a_and_the_route_list_is_still_discoverable`.

    Niat berubah dua kali, dan tes ini menyimpan alasan keduanya supaya tidak ada yang berputar
    balik tanpa sadar. Niat pertama ("`/` adalah indeks route") ditulis saat Layar A belum ada;
    ia berbalik jadi cacat begitu layarnya ada, karena pemilik repo membuka `127.0.0.1:8000`,
    melihat JSON, dan mengira itu seluruh frontend. Niat kedua ("`/` adalah Layar A") diganti
    ADR-021 atas permintaan pemilik repo: `/` menjadi landing, aplikasi pindah ke `/app`.

    Yang TIDAK boleh hilang bersama perubahan itu adalah pelajaran dari cacat pertama — orang
    yang membuka akar harus sampai ke produk tanpa menebak. Tes ini karena itu menuntut EMPAT
    hal sekaligus: `/` adalah halaman (200 HTML, bukan 3xx, supaya `curl` tanpa `-L` pun
    menerima banner SIMULATED pada respons pertama), landing menautkan `/app`, `/app` benar-benar
    Layar A, dan daftar route masih bisa DITEMUKAN dengan mata.
    """
    page = client.get("/")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert 'data-screen="landing"' in page.text
    # Landing BUKAN Layar A: tabel watchlist tidak boleh ikut terbawa ke sini.
    assert page.text.count("<table id='watchlist'>") == 0
    # ...tetapi aplikasinya wajib satu klik jauhnya, dan tautan itu bagian dari kontrak.
    assert '<a class="go" href="/app"' in page.text and "Open the board</a>" in page.text
    assert 'aria-label="Open the watchlist board' in page.text, (
        "tautan aksi landing tanpa label: pembaca layar hanya mendengar 'the board'"
    )
    assert '<a href="/api/routes">/api/routes</a>' in page.text, "daftar route tidak bisa ditemukan"

    # `/app` adalah Layar A yang kanonik, dan `/ui` melayani layar yang SAMA (ADR-021 butir 3):
    # alias, bukan halaman kembar yang bisa menyimpang.
    app_screen = client.get("/app")
    assert app_screen.status_code == 200
    assert "<h1>Tenko 点呼 — pre-departure roll call (simulated)</h1>" in app_screen.text
    legacy = client.get("/ui")
    assert app_screen.text.count("<table id='watchlist'>") == legacy.text.count("<table id='watchlist'>") == 1

    body = client.get("/api/routes").json()
    assert body["simulated"] is True
    assert "/api/watchlist" in body["routes"]
    assert "/api/decision/{id}" in body["routes"]
    assert "/" in body["routes"] and "/ui/app.js" in body["routes"]
    assert "/app" in body["routes"] and "/app/decision/{id}" in body["routes"]
    assert body["screens"]["A_watchlist"] == "/app"
    assert body["screens"]["B_call_card"] == "/app/decision/{id}"
    assert body["calls_ryo_tools"] is False
    # Setiap route yang diumumkan benar-benar dijawab: daftar yang menyebut route mati adalah
    # penemuan yang menyesatkan, bukan penemuan.
    for route in body["routes"]:
        probe = (
            route.replace("{id}", "1")
            .replace("{symbol}", "SOL")
            # `{name}`/`{image}`/`{script}` hanya punya arti dari daftar TERTUTUP di
            # `ui/screens.py`; menebak nama di sini akan menguji 404-nya, bukan route-nya.
            # Tiga placeholder berbeda untuk tiga daftar berbeda, dengan sengaja: satu nama
            # `{name}` untuk ketiganya membuat probe `/ui/img/<nama font>` — 404 yang lolos
            # sebagai kegagalan route, padahal routenya hidup.
            .replace("{name}", ui_screens.FONT_NAMES[0])
            .replace("{image}", ui_screens.IMAGE_NAMES[0])
            .replace("{script}", ui_screens.VENDOR_NAMES[0])
        )
        assert client.get(probe).status_code == 200, route


def test_the_openapi_and_docs_pages_serve_nothing_from_a_cdn(client: TestClient) -> None:
    """Halaman /docs bawaan memuat JS/CSS dari CDN pihak ketiga: dimatikan (checklist UI)."""
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


# --- keamanan: tulis, panggilan keluar, dan teks asal-DB -------------------------------------
def test_the_ui_never_writes_to_the_database(client: TestClient, store: Store) -> None:
    """Store dibuka baca-saja; yang dipatok di sini adalah BERKASNYA, bukan janjinya."""
    before = hashlib.sha256(Path(store.path).read_bytes()).hexdigest()
    for path in ("/api/health", "/api/watchlist", "/api/decisions", "/api/decision/1", "/api/preflight/SOL"):
        assert client.get(path).status_code in {200, 404}
    after = hashlib.sha256(Path(store.path).read_bytes()).hexdigest()
    assert before == after


def test_no_route_calls_a_ryo_tool_or_an_llm(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ranjau di dua jalur keluar: klien RYO dan narasi LLM. Keduanya harus tetap utuh."""
    import core.ryo_client as ryo_client
    import preflight.narrate as narrate

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("ui memanggil jaringan")

    monkeypatch.setattr(ryo_client.RyoClient, "call", explode)
    monkeypatch.setattr(narrate, "narrate", explode)
    monkeypatch.setattr(narrate, "build_client", explode)
    for path in ("/api/watchlist", "/api/decisions", "/api/decision/1", "/api/preflight/SOL"):
        assert client.get(path).status_code == 200


def test_text_from_the_database_cannot_close_a_tag(client: TestClient, store: Store) -> None:
    """`</script>` di kolom `symbol` keluar sebagai escape JSON — dan tetap terbaca utuh.

    Escape-nya LOSSLESS: yang dilindungi adalah halaman yang menempelkan JSON ini, bukan
    kejujuran datanya (`->` di `reason` tetap `->` setelah `json.loads`).
    """
    hostile = "</script><img src=x onerror=alert(1)>"
    decision_id = store.insert_decision(
        symbol=hostile,
        decision="NO_GO",
        blocking=["01_freshness"],
        snapshot_ids=[],
        params_hash=DEFAULT_PARAMS.params_hash(),
        decision_hash="0" * 64,
        card={"algo": ALGO, "skill": {}, "agent": {}},
    )
    response = client.get(f"/api/decision/{decision_id}")
    assert response.status_code == 200
    assert "<" not in response.text and ">" not in response.text
    assert "\\u003c" in response.text
    assert response.json()["symbol"] == hostile  # tidak dibuang, tidak dipelintir


def test_a_forged_algo_cannot_inject_a_verdict_into_the_response(client: TestClient, store: Store) -> None:
    """`card_json["algo"]` bermuatan newline memalsukan baris vonis di stdout CLI (2.4a).

    Di JSON, baris palsu tidak bisa dibentuk — tetapi nilainya tetap dibersihkan supaya aksara
    kendali tidak ikut ke layar, dan `verified` tetap datang dari `ReplayReport.verified`.
    """
    decision_id = store.insert_decision(
        symbol="SOL",
        decision="NO_GO",
        blocking=["01_freshness"],
        snapshot_ids=[],
        params_hash=DEFAULT_PARAMS.params_hash(),
        decision_hash="0" * 64,
        card={"algo": f"{ALGO}\nverified : yes", "skill": {}, "agent": {}},
    )
    body = client.get(f"/api/decision/{decision_id}").json()
    assert body["verified"] is False
    assert "\n" not in json.dumps(body)
    assert body["generation"] == "legacy"  # algo palsu ≠ ALGO → bukan generasi berjalan


def test_control_characters_are_escaped_visibly_not_dropped(client: TestClient, store: Store) -> None:
    decision_id = store.insert_decision(
        symbol="SO‮L",
        decision="NO_GO",
        blocking=["01_freshness"],
        snapshot_ids=[],
        params_hash=DEFAULT_PARAMS.params_hash(),
        decision_hash="0" * 64,
        card={"algo": ALGO, "skill": {}, "agent": {}},
    )
    body = client.get(f"/api/decision/{decision_id}").json()
    assert body["symbol"] == "SO\\u202eL"


# --- satu baris yang besar tidak menentukan ukuran respons ----------------------------------
def test_a_giant_card_is_rendered_within_a_stated_bound(client: TestClient, store: Store) -> None:
    """Kartu 20.000 baris dulu menjadi respons 20,8 MB — dan ikut memperlambat Layar A.

    Kartu ditulis agen, bukan pemanggil, jadi ini bukan penjaga terhadap masukan bermusuhan
    melainkan janji ukuran. Yang dipatok bersamanya: pemotongan DISEBUT, dan `n_items_total`
    tetap cacah sebenarnya — daftar yang lebih pendek tanpa kalimat adalah bentuk lain dari
    angka tanpa n.
    """
    n_rows = 20_000
    decision_id = store.insert_decision(
        symbol="SOL",
        decision="NO_GO",
        blocking=["01_freshness"],
        snapshot_ids=[],
        params_hash=DEFAULT_PARAMS.params_hash(),
        decision_hash="0" * 64,
        card={
            "algo": ALGO,
            "skill": {
                "status": "ok",
                "warnings": [f"peringatan {i}" for i in range(n_rows)],
                "data": {"items": [{"id": f"zz_{i:05d}", "status": "red"} for i in range(n_rows)]},
            },
            "agent": {},
        },
    )
    response = client.get(f"/api/decision/{decision_id}")
    assert response.status_code == 200
    body = response.json()
    assert len(response.content) < 1_000_000
    assert len(body["items"]) == ui_app.MAX_ITEM_ROWS
    assert body["n_items_total"] == n_rows
    assert len(body["warnings"]) == ui_app.MAX_WARNING_ROWS
    # `n_warnings` = cacah SEBENARNYA (COUNT_NAMING_RULE); yang dirender punya nama sendiri.
    assert body["n_warnings"] == body["n_warnings_total"] == n_rows
    assert body["n_warnings_rendered"] == len(body["warnings"])
    assert any(f"{n_rows} roll-call rows" in note for note in body["notes"])
    assert any(f"{n_rows} envelope warnings sit on this row" in note for note in body["notes"])
    # Layar A membaca baris yang sama; ia tetap menjawab, dengan cacah SEBENARNYA di sampingnya.
    entry = by_symbol(client.get("/api/watchlist").json())["SOL"]
    assert entry["n_items"] == n_rows and entry["n_warnings"] == n_rows


# --- jam dari data RYO yang membuat konversi JST meledak ------------------------------------
#: `as_of` yang, dikonversi ke JST (+09:00), keluar dari rentang `datetime`: `astimezone`
#: melempar **OverflowError**, bukan ValueError. `core/store.py` menyalin `as_of` envelope apa
#: adanya tanpa validasi rentang, jadi bentuk ini bisa benar-benar datang dari satu respons RYO.
OVERFLOW_AS_OF = "9999-12-31T15:00:00Z"


def overflow_card_row(store: Store, symbol: str = "SOL") -> int:
    """Baris generasi berjalan yang `skill.as_of`-nya (DAN `as_of` satu itemnya) tahun 9999."""
    return store.insert_decision(
        symbol=symbol,
        decision="NO_GO",
        blocking=["01_freshness"],
        snapshot_ids=[],
        params_hash=DEFAULT_PARAMS.params_hash(),
        decision_hash="0" * 64,
        card={
            "algo": ALGO,
            "skill": {
                "status": "ok",
                "as_of": OVERFLOW_AS_OF,
                "data": {
                    "items": [
                        {
                            "id": "01_freshness",
                            "status": "red",
                            "value": 1,
                            "reason": "jam dari envelope, bukan dari kami",
                            "source": {
                                "tool": "deep_analysis",
                                "field": "as_of",
                                "snapshot_id": 1,
                                "as_of": OVERFLOW_AS_OF,
                            },
                        }
                    ]
                },
            },
            "agent": {},
        },
    )


def test_a_year_9999_as_of_in_a_card_does_not_take_screen_a_down(client: TestClient, store: Store) -> None:
    """Satu baris ber-`as_of` tahun 9999 dulu memadamkan /api/watchlist DAN /api/decisions.

    `_jst` hanya menangkap `ValueError`; `astimezone` melempar `OverflowError`, jadi respons
    keluar sebagai `text/plain` "Internal Server Error" TANPA `simulated` — dan karena
    `_summary_row` dipakai kedua daftar, Layar A padam seluruhnya karena satu baris.
    """
    decision_id = overflow_card_row(store)
    for path in ("/api/watchlist", "/api/decisions"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.json()["simulated"] is True, path

    body = client.get(f"/api/decision/{decision_id}").json()
    assert body["simulated"] is True
    # Jam yang tidak bisa dikonversi → None, sementara nilai MENTAHNYA tetap ditampilkan:
    # ketidaktahuan dikatakan, bukan diganti jam sekarang dan bukan pula disembunyikan.
    assert body["as_of_jst"] is None if "as_of_jst" in body else True
    assert body["items"][0]["as_of"] == OVERFLOW_AS_OF
    assert body["items"][0]["as_of_jst"] is None
    entry = by_symbol(client.get("/api/watchlist").json())["SOL"]
    assert entry["as_of"] is not None  # baris SOL terbaru = baris ini
    assert entry["as_of_jst"] is None


def test_a_year_9999_as_of_in_a_snapshot_does_not_take_the_card_down(
    client: TestClient, store: Store
) -> None:
    """Jalur KEDUA, terpisah: `as_of` snapshot beku dirender di `replay_bundle`."""
    from preflight.skill import tool_args
    from tests.helpers_agent import envelope_for

    envelope = envelope_for(DEEP, "BTC")
    envelope["as_of"] = OVERFLOW_AS_OF
    snapshot_id = store.insert_snapshot(DEEP, tool_args("BTC", "swing", DEFAULT_PARAMS)[DEEP], envelope)
    decision_id = store.insert_decision(
        symbol="BTC",
        decision="NO_GO",
        blocking=["01_freshness"],
        snapshot_ids=[snapshot_id],
        params_hash=DEFAULT_PARAMS.params_hash(),
        decision_hash="0" * 64,
        card={"algo": ALGO, "skill": {"status": "ok"}, "agent": {}},
    )
    response = client.get(f"/api/decision/{decision_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["simulated"] is True
    bundle = body["replay_bundle"]
    assert len(bundle) == 1
    assert bundle[0]["as_of"] == OVERFLOW_AS_OF and bundle[0]["as_of_jst"] is None
    assert client.get("/api/decisions").json()["simulated"] is True


def test_an_unreadable_clock_is_never_replaced_by_the_current_one(client: TestClient) -> None:
    """`_jst` mengembalikan None untuk ketiga jenis kegagalan, bukan jam sekarang."""
    assert ui_app._jst(OVERFLOW_AS_OF) is None
    assert ui_app._jst("0001-01-01T00:00:00Z") is not None  # +09:00 masih muat
    assert ui_app._jst("bukan jam") is None
    assert ui_app._jst("") is None and ui_app._jst(None) is None
    assert ui_app._jst("2026-09-03T20:28:28Z") == "2026-09-04 05:28 JST"


# --- DB hidup: empat id nol-bukti + satu id warisan yang NYATA -------------------------------
@pytest.fixture
def live_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Klien atas `data/ryo.db` — BACA-SAJA, dan tidak pernah ditulis (lihat tes hash di bawah)."""
    if not LIVE_DB.exists():  # pragma: no cover - hanya di mesin tanpa data rekaman
        pytest.skip("data/ryo.db belum ada di mesin ini")
    monkeypatch.setattr(app.state, "db_path", str(LIVE_DB))
    with TestClient(app) as opened:
        yield opened


@pytest.mark.parametrize("decision_id", LIVE_ZERO_EVIDENCE_IDS)
def test_live_zero_evidence_rows_are_marked_not_verified(live_client: TestClient, decision_id: int) -> None:
    """AC 3.1a + 0.14: keempat id nyata (20-23) — 200, penanda eksplisit, bukan lencana hijau.

    Vonisnya `STALE_PARAMS` sejak ambangnya bergeser (TASKS 0.12): cabang `NO_EVIDENCE`
    bersyarat `computed == stored`, jadi UI tidak boleh lagi mengklaim baris ini "terbukti
    ditulis tanpa bukti". Yang dipatok tes ini tidak berubah: bukan lencana hijau.
    """
    response = live_client.get(f"/api/decision/{decision_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["simulated"] is True
    assert body["replay_status"] == VERDICT_STALE_PARAMS
    assert body["verified"] is False
    assert body["n_snapshots"] == 0
    assert VERDICT_IDENTICAL not in json.dumps(body)
    assert body["replay_tone"] == TONE_NOT_ASSESSED and body["replay_tone"] != TONE_FINDING
    assert any("we did NOT judge it at all" in note for note in body["notes"])
    # Ketidaktahuan, bukan tuduhan: tidak satu pun kalimat mengaku memeriksa dan menemukan.
    assert not any("booked without a single piece of evidence" in note for note in body["notes"])


@pytest.mark.parametrize("decision_id", LIVE_ZERO_EVIDENCE_IDS)
def test_live_stale_params_rows_never_look_like_a_finding(live_client: TestClient, decision_id: int) -> None:
    """AC 0.14 (4): juri melihat `MISMATCH` dan `STALE_PARAMS` berdampingan; keduanya beda."""
    body = live_client.get(f"/api/decision/{decision_id}").json()
    assert "finding" not in body["replay_label"]
    assert "not assessed" in body["replay_label"]


def test_live_legacy_row_is_200_not_404(live_client: TestClient) -> None:
    response = live_client.get(f"/api/decision/{LIVE_LEGACY_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["replay_status"] == VERDICT_STALE_ALGO
    assert body["verified"] is False
    assert body["items"] == []
    assert body["blocking_rendered"] is False


def test_live_decisions_start_at_the_first_current_generation_row(live_client: TestClient) -> None:
    """AC 3.1a: `min(id)` ≥ id keputusan generasi baru; 19 baris warisan tidak ikut."""
    payload = live_client.get("/api/decisions").json()
    assert payload["n"] >= 1
    assert payload["min_id"] >= LIVE_FIRST_CURRENT_ID
    assert all(entry["decision_id"] >= LIVE_FIRST_CURRENT_ID for entry in payload["decisions"])
    watch = live_client.get("/api/watchlist").json()
    assert all(entry["decision_id"] >= LIVE_FIRST_CURRENT_ID for entry in watch["symbols"])


# --- penjaga tulis: yang diukur PROSES INI, bukan berkasnya ----------------------------------
#: Mode `open()` yang berniat menulis (`r+` juga menulis; `b`/`t` tidak menambah niat apa pun).
WRITE_MODES = frozenset("wxa+")
#: Flag `os.open()` yang berniat menulis.
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


class WriteWatch:
    """Pembukaan BER-NIAT-TULIS di bawah satu direktori, oleh proses ini saja.

    **Kenapa bukan hash berkas.** Versi lama tes di bawah meng-hash `data/ryo.db` sebelum dan
    sesudah empat request. Yang benar-benar diukurnya adalah "tidak ada SIAPA PUN yang menulis
    berkas ini selama jendela tes" — sementara namanya menjanjikan "tidak ada ROUTE yang
    mengubahnya". Perekam produksi menulis berkas yang sama ~1000 dtk tiap siklus, jadi suite
    yang mulai pada saat acak bisa MERAH tanpa satu pun route menyentuh apa pun: false positive
    yang, di layar juri, terbaca sebagai UI yang menulis DB. `agent/demo.py` sudah menuliskan
    alasan yang persis sama dan sudah memilih audit hook; ini menyamakan tes UI dengan repo.

    **Yang ditangkap, dan yang tidak.** `sys.addaudithook` mencatat perbuatan PROSES INI:
    `open`/`os.open` ber-niat-tulis, dan `sqlite3.connect` yang TIDAK membawa `mode=ro`. SQLite
    membuka berkasnya di lapisan C dan tidak memicu event `open`, jadi URI koneksinyalah yang
    diperiksa — dan itu justru lapisan yang sama dengan jaminan `Store(read_only=True)`
    (`ui/app.py:_open_store`). Proses lain (perekam) tidak terlihat di sini; memang itu maunya.

    **Batasnya, terus terang.** `sqlite3` tidak mengaudit `execute`, jadi yang terlihat adalah
    PEMBUKAAN koneksi, bukan pernyataannya; route yang menulis lewat koneksi rw yang sudah
    terbuka sebelum jendela tes akan lolos. Itu tidak bisa terjadi di sini karena
    `_open_store` membuka koneksi baru PER REQUEST (alasannya ada di docstring-nya), dan
    lapisan keduanya adalah `test_the_route_store_is_opened_read_only` + tiga lapis
    `core/store.py` (authorizer, `PRAGMA query_only`, URI `mode=ro`). Jendela bersenjatanya
    sengaja sempit — satu tes — supaya perbuatan utas lain di proses ini tidak ikut terhitung.

    Yang dicatat hanya PELANGGARAN, supaya hook tetap murah dan pesan gagalnya menyebut jalur.
    """

    def __init__(self) -> None:
        self.prefix: str | None = None
        self.hits: list[str] = []

    def arm(self, directory: Path) -> None:
        # `abspath` (bukan `resolve`) supaya hook tidak melakukan syscall di dalam audit hook.
        self.prefix = os.path.abspath(directory) + os.sep
        self.hits.clear()

    def disarm(self) -> None:
        self.prefix = None

    def under_watch(self, target: object) -> str | None:
        if self.prefix is None or not isinstance(target, str | bytes | os.PathLike):
            return None
        text = target.decode("utf-8", "replace") if isinstance(target, bytes) else str(target)
        # `Store` membuka lewat URI (`file:...?mode=ro`); prefiks + query dibuang dulu.
        plain = text[5:].split("?", 1)[0] if text.startswith("file:") else text
        return plain if os.path.abspath(plain).startswith(self.prefix) else None

    def note(self, how: str, target: str) -> None:
        entry = f"{how}: {target}"
        if entry not in self.hits:
            self.hits.append(entry)

    def hook(self, event: str, args: tuple[Any, ...]) -> None:
        if self.prefix is None:  # tidak dipersenjatai: satu perbandingan, lalu keluar
            return
        try:
            if event == "open":
                target = self.under_watch(args[0])
                if target is None:
                    return
                mode, flags = args[1], args[2]
                writing = bool(set(mode) & WRITE_MODES) if isinstance(mode, str) else False
                if isinstance(flags, int) and flags & WRITE_FLAGS:
                    writing = True
                if writing:
                    self.note(f"open(mode={mode!r})", target)
            elif event == "sqlite3.connect":
                target = self.under_watch(args[0])
                text = args[0] if isinstance(args[0], str) else ""
                if target is not None and "mode=ro" not in text:
                    self.note("sqlite3.connect tanpa mode=ro", target)
        except Exception:  # noqa: BLE001 — exception di audit hook membunuh proses; diamkan
            return


WRITE_WATCH = WriteWatch()
sys.addaudithook(WRITE_WATCH.hook)  # tidak bisa dicabut; karena itu `prefix=None` yang menjaga


@pytest.fixture
def write_watch() -> Iterator[WriteWatch]:
    """Penjaga proses ini, dilucuti lagi setelah tes — dipersenjatai oleh tesnya, eksplisit."""
    try:
        yield WRITE_WATCH
    finally:
        WRITE_WATCH.disarm()


def test_no_route_opens_the_live_database_with_write_intent(
    live_client: TestClient, write_watch: WriteWatch
) -> None:
    """Pengganti tes hash: subjeknya route, bukan berkas.

    Perekam boleh menulis `data/ryo.db` di sebelah tes ini tanpa mengubah hasilnya; sebuah route
    yang membuka DB-nya untuk ditulis tetap MERAH.
    """
    write_watch.arm(Path(str(app.state.db_path)).parent)  # direktori DB yang BENAR-BENAR dilayani
    for path in ("/api/health", "/api/watchlist", "/api/decisions", f"/api/decision/{LIVE_LEGACY_ID}"):
        assert live_client.get(path).status_code == 200
    assert write_watch.hits == []
    assert write_watch.prefix is not None  # penjaga benar-benar hidup, bukan diam-diam mati


def test_the_write_watch_would_actually_catch_a_writing_route(
    write_watch: WriteWatch, tmp_path: Path
) -> None:
    """Penjaga yang tidak pernah bisa merah tidak menjaga apa pun: buktikan arah gagalnya.

    Penulisnya diarahkan ke SALINAN di tmp — `data/ryo.db` tidak pernah ditulis oleh tes.
    """
    copy = tmp_path / "ryo.db"
    Store(copy).close()  # skema lengkap, di tmp
    write_watch.arm(tmp_path)
    with copy.open("ab") as handle:
        handle.write(b"")
    Store(copy).close()  # inilah "route yang menulis": koneksi tulis ke DB yang dilayani
    assert [hit.split(":", 1)[0] for hit in write_watch.hits] == [
        "open(mode='a')",  # FileIO melaporkan niatnya, bukan huruf `b`
        "sqlite3.connect tanpa mode=ro",
    ]
    assert all(str(copy) in hit for hit in write_watch.hits)
    # ...dan koneksi baca-saja yang benar-benar dipakai route TIDAK dihitung sebagai tulisan.
    write_watch.arm(tmp_path)
    Store(copy, read_only=True).close()
    with copy.open("rb") as handle:
        handle.read(1)
    assert write_watch.hits == []


def test_the_route_store_is_opened_read_only(live_client: TestClient) -> None:
    """Lapis kedua, struktural: koneksi yang dipakai route menolak tulisan (core/store.py)."""
    store = ui_app._open_store()
    try:
        assert store.read_only is True
        with pytest.raises(StoreReadOnlyError):
            store.insert_snapshot("analyze_token", {}, {})
    finally:
        store.close()


# --- Layar A & Layar B (TASKS 3.2) ------------------------------------------------------------
# Layar dirender di SERVER dan tidak memuat satu pun `<script>`, jadi yang diuji di bawah adalah
# HTML yang benar-benar sampai ke browser juri — bukan sumber JS yang dicocokkan sebagai teks.
# Fungsi render `ui/screens.py` murni (payload dict + banner + jam -> str), sehingga umur, klausa
# `reason`, penanda netral, dan label narasi bisa dipatok sebagai KALIMAT, bukan sebagai janji.


def card_payload(client: TestClient, symbol: str = "ETH") -> dict[str, Any]:
    """Badan `/api/decision/{id}` NYATA untuk satu simbol fixture — bukan dict karangan.

    Tes render di bawah memodifikasi salinannya (jam, stance, narasi) supaya yang diuji adalah
    perilaku layar, sementara BENTUK payload-nya tetap bentuk yang benar-benar diproduksi route.
    """
    entries = client.get("/api/watchlist").json()["symbols"]
    row = next(entry for entry in entries if entry["symbol"] == symbol)
    return client.get(f"/api/decision/{row['decision_id']}").json()


def render_clock() -> datetime:
    return datetime.now(UI_JST)


def aria_rows(html: str) -> list[str]:
    """Baris tabel yang membawa `aria-label`-nya sendiri, dalam urutan dokumen.

    Dipilih dari atributnya, BUKAN dari `html.split("<tr>")[2]`: baris header dan tabel bukti
    juga memakai `<tr>`, jadi indeks posisi diam-diam bergeser setiap kali kartu tumbuh satu
    seksi — dan tes yang bergeser diam-diam berhenti menguji apa yang tertulis di namanya.
    """
    return re.findall(r"<tr aria-label=.*?</tr>", html, re.S)


def strip_tags(html: str) -> str:
    """Teks yang benar-benar terbaca juri: tag dibuang, entitas dikembalikan, spasi dirapatkan.

    Spasi dirapatkan karena tag yang dibuang meninggalkan celah — `<bdi>SOL</bdi>` di tengah
    kalimat menjadi dua spasi. Yang diuji adalah KALIMAT yang dibaca juri, bukan whitespace HTML.
    """
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def test_screen_a_answers_which_symbol_is_closest_to_go_in_its_first_sentence(client: TestClient) -> None:
    """Uji 30 detik Layar A: jawabannya ada di kalimat pertama, bukan di urutan tabel.

    `/api/watchlist` mengurutkan daya veto MENURUN (penahan terberat di atas), jadi simbol yang
    paling dekat GO justru ada di bawah. Layar menyebutnya di kalimat pertama, lengkap dengan
    ATURAN yang dipakai — supaya "paling dekat" tidak terbaca sebagai skor atau ramalan.
    """
    page = client.get("/ui")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    lede = strip_tags(page.text.split("<table")[0])
    assert "closest to GO: SOL" in lede  # fixture: SOL satu-satunya GO
    assert "not a score" in lede
    assert BANNER in lede


def test_screen_a_orders_rows_by_veto_power_and_says_what_holds_each_symbol(client: TestClient) -> None:
    """Kolom "apa yang menahan": id penahan, statusnya, alasannya, sumbernya, dan jamnya."""
    text = client.get("/ui").text
    body = text.split("<tbody>")[1]  # urutan BARIS TABEL, bukan kalimat pembuka
    order = [body.index(f">{symbol}</bdi>") for symbol in ("ETH", "BTC", "BNB", "SOL")]
    assert order == sorted(order)  # 05 (rank 5) < 06 < 10; GO menyusul paling bawah
    readable = strip_tags(text)
    assert "05_verdict_quick" in readable and "06_confluence" in readable
    assert "the source argues against opening a position in any direction" in readable
    assert "analyze_token" in readable and "data.verdict" in readable
    assert " JST" in readable


def test_screen_a_says_how_much_it_did_not_show(client: TestClient) -> None:
    """Pemotongan harus terlihat: batas pindai dan baris generasi lama disebut dengan angkanya."""
    readable = strip_tags(client.get("/ui").text)
    assert "MOST RECENT decision rows, not the whole table" in readable
    assert "earlier-generation rows are not shown here" in readable


def test_screen_b_headline_answers_why_this_decision_without_a_single_click(client: TestClient) -> None:
    """Uji 30 detik Layar B: penahan terberat, alasannya, sumbernya, dan jamnya di atas lipatan."""
    payload = card_payload(client, "BTC")
    html = render_card(payload, BANNER, render_clock())
    above_fold = strip_tags(html.split("<h2")[0])
    assert "BTC" in above_fold and "NO_GO" in above_fold
    assert "HEAVIEST blocker: 06_confluence" in above_fold
    assert "the evidence does not agree with itself" in above_fold
    assert "deep_analysis" in above_fold and "data.confluence.state" in above_fold
    assert "as_of" in above_fold
    assert payload["decision_hash"] in html


#: Kata-kata TERAKHIR `reason` item 1 (`preflight/items.py`), dipatok sebagai LITERAL dan tidak
#: diturunkan dari `REASON_LIMIT` mana pun. Alasannya: tes yang membandingkan panjang dengan
#: konstanta yang sama yang memotongnya akan tetap hijau ketika konstanta itu diturunkan — ia
#: memakukan STRUKTUR (satu langit-langit, bukan tiga), bukan kecukupannya. Yang dilarang AC 3.2
#: justru bentuk kegagalan pada 240: klausa "not the wall clock" kebetulan selamat sementara
#: kalimat yang MENJELASKANNYA terpotong di tengah frasa, sehingga kartu terbaca sebagai klaim
#: kesegaran terhadap jam dinding. Kalimat ini utuh atau tidak sama sekali.
ITEM_ONE_LAST_WORDS = "so this says nothing about how old the bundle itself is"


def test_screen_b_shows_the_last_words_of_item_one_not_just_the_wall_clock_clause(
    client: TestClient,
) -> None:
    """Langit-langit `reason` harus cukup TINGGI untuk kalimat nyata, bukan sekadar konsisten.

    Kartu fixture ini dibangun agen sungguhan dari envelope RYO nyata (`docs/samples`), jadi
    `reason` yang diuji adalah kalimat yang benar-benar ditulis `preflight/items.py` — dan yang
    dipatok adalah kata-kata terakhirnya sebagai literal. Menurunkan `REASON_LIMIT` ke 240
    membuat tes ini MERAH, yang memang tugasnya.
    """
    payload = card_payload(client)
    reason = next(item["reason"] for item in payload["items"] if item["item_id"] == "01_freshness")
    assert ITEM_ONE_LAST_WORDS in reason  # prasyarat: route menyajikan kalimatnya utuh
    assert TRUNCATED_MARK not in reason
    readable = strip_tags(render_card(payload, BANNER, render_clock()))
    assert ITEM_ONE_LAST_WORDS in readable  # sampai KATA TERAKHIR, di layar yang dirender
    page = client.get(f"/ui/decision/{payload['decision_id']}")
    assert ITEM_ONE_LAST_WORDS in strip_tags(page.text)  # dan lewat HTTP, bukan hanya render()


def live_reason_row(live_client: TestClient) -> tuple[int, str] | None:
    """(id, reason) baris NYATA pertama yang membawa kalimat penuh item 1 — atau None.

    Dicari, bukan diasumsikan: kalau `data/ryo.db` hari ini tidak punya satu pun baris yang
    cocok, yang jujur adalah `skip` dengan sebab, bukan tes yang selalu hijau.
    """
    listing = live_client.get("/api/decisions").json()
    for entry in listing["decisions"]:
        card = live_client.get(f"/api/decision/{entry['decision_id']}").json()
        for item in card.get("items", []):
            if item.get("item_id") == "01_freshness" and ITEM_ONE_LAST_WORDS in (item.get("reason") or ""):
                return entry["decision_id"], item["reason"]
    return None


def test_live_card_shows_the_last_words_of_item_one_on_the_rendered_screen(
    live_client: TestClient,
) -> None:
    """Padanan tes di atas atas `data/ryo.db` — data yang benar-benar akan dibuka juri.

    Baris `data/ryo.db` yang ada hari ini dibukukan SEBELUM `REASON_LIMIT` lahir, jadi
    `reason`-nya sudah terpotong 240 di ledger dan kalimatnya tidak bisa dimunculkan layar mana
    pun. Selama itu masih benar, tes ini `skip` dengan sebab yang menyebut kondisinya — dan ia
    mulai menjaga sendirinya begitu satu baris baru direkam.
    """
    found = live_reason_row(live_client)
    if found is None:  # pragma: no cover - tergantung isi data/ryo.db saat tes dijalankan
        pytest.skip(
            "tidak ada baris `data/ryo.db` yang membawa kalimat penuh item 1; baris yang ada "
            f"dibukukan saat langit-langit `reason` masih {TEXT_LIMIT} aksara"
        )
    decision_id, reason = found
    assert TRUNCATED_MARK not in reason
    readable = strip_tags(live_client.get(f"/ui/decision/{decision_id}").text)
    assert ITEM_ONE_LAST_WORDS in readable


def test_screen_b_renders_item_one_reason_whole_including_the_wall_clock_clause(client: TestClient) -> None:
    """Amandemen TINGGI-2: memotong item 1 di "x min (limit y min)" mengubah artinya.

    Tanpa klausa "the clock is the newest as_of in this bundle …, not the wall clock", baris itu
    terbaca sebagai klaim kesegaran terhadap jam dinding — klaim yang TIDAK pernah dibuat kode.
    """
    payload = card_payload(client)
    reason = next(item["reason"] for item in payload["items"] if item["item_id"] == "01_freshness")
    assert "not the wall clock" in reason  # prasyarat: kartunya memang membawa klausa itu
    readable = strip_tags(render_card(payload, BANNER, render_clock()))
    assert "the clock is the newest as_of in this bundle" in readable
    assert "not the wall clock" in readable
    assert reason in readable  # utuh, bukan sepotong


def doctored_reason_row(store: Store, reason: str, symbol: str = "ETH") -> int:
    """Baris `decisions` NYATA yang `reason` item pertamanya ditulis lewat `core.skill.ItemResult`.

    Rantainya utuh dan itulah intinya: teks masuk lewat validator yang memotong pada
    `REASON_LIMIT` (bukan lewat dict rakitan tes), tersimpan di `card_json`, lalu dibaca
    `/api/decision/{id}` dan dirender `/ui/decision/{id}`. Tes yang memanggil `render_card`
    dengan payload rakitan TIDAK PERNAH melewati `ui/app.py` — dan justru di sanalah batas
    kedua yang berselisih itu bersembunyi (security review 3.2, TINGGI-1).

    `decision_hash` baris ini karena itu tidak lagi cocok dengan kartunya (vonis replay
    `MISMATCH`), dan itu memang jujur: kartunya memang diubah sesudah dihitung.
    """
    rows = [row for row in recent_decisions(store.path, limit=50) if row.is_current_generation()]
    source = next(row for row in rows if row.symbol == symbol)
    card = json.loads(source.card_json)
    first = card["skill"]["data"]["items"][0]
    item = ItemResult(id=first["id"], status=first["status"], reason=reason, value=None)
    card["skill"]["data"]["items"][0] = json.loads(item.model_dump_json())
    return store.insert_decision(
        symbol=source.symbol,
        decision=source.decision,
        blocking=[first["id"]],
        snapshot_ids=json.loads(source.snapshot_ids_json),
        params_hash=source.params_hash,
        decision_hash=source.decision_hash,
        card=card,
    )


def test_a_reason_survives_the_whole_chain_from_item_result_to_the_rendered_screen(
    client: TestClient, store: Store
) -> None:
    """`ItemResult` → ledger → `/api/decision` → layar: satu langit-langit, bukan tiga.

    Rantai yang diukur tinjauan keamanan 3.2: `reason` 839 aksara dibukukan sebagai 814
    (`REASON_LIMIT` + penanda), tetapi route menyajikan 414 dan layar memotongnya lagi di 400 —
    sehingga klausa wajib item 1 hilang DAN kartu mencetak "terpotong sebelum dibukukan" untuk
    potongan yang ia buat sendiri. Yang dipatok di sini adalah angkanya, di ketiga titik.
    """
    written = ItemResult(id="01_freshness", status="green", reason="Z" * 839, value=None)
    assert len(written.reason) == REASON_LIMIT + len(TRUNCATED_MARK)  # 800 + 14, dipotong ledger
    decision_id = doctored_reason_row(store, "Z" * 839)

    served = client.get(f"/api/decision/{decision_id}").json()
    reason = next(item["reason"] for item in served["items"] if item["item_id"] == "01_freshness")
    assert reason == written.reason  # route tidak memotong apa yang ledger simpan

    page = client.get(f"/ui/decision/{decision_id}")
    assert page.status_code == 200
    readable = strip_tags(page.text)
    assert reason in readable  # 814 aksara, utuh — bukan 400
    assert readable.count(SCREEN_CUT_NOTE.format(limit=REASON_LIMIT)) == 0
    assert REASON_CUT_NOTE in readable  # sebabnya disebut, dan sebabnya BENAR
    assert f"{REASON_LIMIT} characters" in REASON_CUT_NOTE  # langit-langit yang BENAR-BENAR memotong
    # Baris generasi kini tidak boleh memakai kalimat baris warisan: `TEXT_LIMIT` disebut hanya
    # oleh `LEGACY_REASON_CUT_NOTE`, dan menyebut langit-langit yang salah = kartu yang berbohong.
    assert "TEXT_LIMIT" not in REASON_CUT_NOTE
    assert LEGACY_REASON_CUT_NOTE not in readable


def test_the_screen_reason_limit_is_the_one_from_core_not_a_copy() -> None:
    """Dua konstanta yang bisa berselisih diam-diam adalah cara cacat TINGGI-1 lahir.

    Karena itu yang diuji bukan angkanya, melainkan bahwa angkanya SATU: `ui.screens` memakai
    `core.skill.REASON_LIMIT` yang sama persis dengan yang memotong `ItemResult`.
    """
    assert ui_screens.REASON_LIMIT is REASON_LIMIT
    long_reason = "x" * (REASON_LIMIT - 1)
    payload: dict[str, Any] = {
        "decision_id": 1,
        "symbol": "SOL",
        "decision": "NO_GO",
        "generation": "current",
        "blocking": ["01_freshness"],
        "items": [{"item_id": "01_freshness", "veto_rank": 1, "status": "green", "reason": long_reason}],
    }
    readable = strip_tags(render_card(payload, BANNER, render_clock()))
    assert long_reason in readable  # 799 aksara: layar tidak memotong apa pun
    assert TRUNCATED_MARK not in readable


def test_a_truncated_mark_that_is_no_ceiling_is_never_claimed_as_a_cut_that_happened_earlier() -> None:
    """Nilai RYO yang memuat literal `...(truncated)` tidak boleh membuat kartu mengaku.

    Penanda di panjang yang bukan langit-langit mana pun berarti: layar ini tidak memotongnya,
    dan kami tidak bisa membuktikan siapa yang memotongnya. Kalimat yang keluar mengatakan
    persis itu — bukan "terpotong sebelum dibukukan", yang akan menuduh ledger menyembunyikan
    sesuatu yang mungkin tidak pernah disembunyikan (security review 3.2, RENDAH a).
    """
    payload: dict[str, Any] = {
        "decision_id": 1,
        "symbol": "SOL",
        "decision": "NO_GO",
        "generation": "current",
        "blocking": ["01_freshness"],
        "items": [
            {
                "item_id": "01_freshness",
                "veto_rank": 1,
                "status": "green",
                "reason": "short" + TRUNCATED_MARK,
                "value": {"note": "harga " + TRUNCATED_MARK + " apa adanya"},
            }
        ],
    }
    readable = strip_tags(render_card(payload, BANNER, render_clock()))
    assert REASON_CUT_NOTE not in readable
    assert SCREEN_CUT_NOTE.format(limit=REASON_LIMIT) not in readable
    # tiga tempat penanda itu benar-benar muncul: `reason` di baris tabel, `value` di baris
    # tabel, dan `reason` penahan terberat di kalimat jawaban — masing-masing membawa sebabnya.
    assert readable.count(FOREIGN_MARK_NOTE) == 3


def test_a_row_booked_at_the_old_ceiling_names_that_ceiling_and_its_era() -> None:
    """Bentuk SELURUH 40 baris `data/ryo.db` hari ini: `reason` 254 aksara = `TEXT_LIMIT` + penanda.

    Ledger append-only, jadi baris itu tidak bisa ditulis ulang — dan kartu yang menjawab
    "panjangnya tidak berada di langit-langit mana pun" untuk potongan yang punya sebab yang
    bisa disebut salah bicara tentang dirinya sendiri, kelas kesalahan yang sama dengan TINGGI-1.
    Yang dituntut di sini: angka langit-langitnya (240) DAN eranya (dibukukan sebelum dinaikkan).
    """
    booked = clean_text("y" * (TEXT_LIMIT + 50), TEXT_LIMIT)
    assert len(booked) == TEXT_LIMIT + len(TRUNCATED_MARK) == 254  # bentuk baris nyata
    payload: dict[str, Any] = {
        "decision_id": 1,
        "symbol": "SOL",
        "decision": "NO_GO",
        "generation": "current",
        "blocking": ["01_freshness"],
        "items": [{"item_id": "01_freshness", "veto_rank": 1, "status": "green", "reason": booked}],
    }
    readable = strip_tags(render_card(payload, BANNER, render_clock()))
    assert LEGACY_REASON_CUT_NOTE in readable
    assert f"{TEXT_LIMIT} characters" in LEGACY_REASON_CUT_NOTE
    assert "BEFORE it was raised" in LEGACY_REASON_CUT_NOTE
    assert FOREIGN_MARK_NOTE not in readable  # sebabnya diketahui: jangan bilang tidak tahu
    assert REASON_CUT_NOTE not in readable  # dan jangan menyebut langit-langit yang salah


def test_the_live_cards_a_judge_opens_name_the_ceiling_that_really_cut_them(
    live_client: TestClient,
) -> None:
    """Padanan di atas atas `data/ryo.db` — kartu yang benar-benar akan dibuka juri.

    Tidak diasumsikan: barisnya dicari lewat panjang `reason`-nya, dan kalau tidak ada satu pun
    baris berbentuk itu, tesnya `skip` dengan sebabnya.
    """
    legacy_length = TEXT_LIMIT + len(TRUNCATED_MARK)
    found: tuple[int, str] | None = None
    for entry in live_client.get("/api/decisions").json()["decisions"]:
        card = live_client.get(f"/api/decision/{entry['decision_id']}").json()
        for item in card.get("items", []):
            reason = item.get("reason") or ""
            if item.get("item_id") == "01_freshness" and len(reason) == legacy_length:
                found = (entry["decision_id"], reason)
                break
        if found:
            break
    if found is None:  # pragma: no cover - tergantung isi data/ryo.db saat tes dijalankan
        pytest.skip(f"tidak ada baris `data/ryo.db` ber-`reason` {legacy_length} aksara")
    decision_id, reason = found
    assert reason.endswith(TRUNCATED_MARK)
    readable = strip_tags(live_client.get(f"/ui/decision/{decision_id}").text)
    assert LEGACY_REASON_CUT_NOTE in readable
    assert FOREIGN_MARK_NOTE not in readable
    assert REASON_CUT_NOTE not in readable


def test_the_screen_says_so_when_the_screen_itself_is_the_one_that_cut() -> None:
    """Potongan LAYAR menyebut layar dan batasnya, dan tidak pernah menyalahkan ledger."""
    payload: dict[str, Any] = {
        "decision_id": 1,
        "symbol": "SOL",
        "decision": "NO_GO",
        "generation": "current",
        "blocking": ["01_freshness"],
        "items": [
            {
                "item_id": "01_freshness",
                "veto_rank": 1,
                "status": "green",
                "reason": "r",
                "value": "v" * (VALUE_LIMIT + 500),
            }
        ],
    }
    readable = strip_tags(render_card(payload, BANNER, render_clock()))
    assert SCREEN_CUT_NOTE.format(limit=VALUE_LIMIT) in readable
    assert REASON_CUT_NOTE not in readable and FOREIGN_MARK_NOTE not in readable


def test_screen_b_age_is_derived_from_as_of_not_from_ts_utc(client: TestClient) -> None:
    """`as_of` dijaga `payload_hash`; `ts_utc` tidak dijaga hash apa pun.

    Umur yang dihitung dari `ts_utc` akan terlihat sama meyakinkannya dan tidak bisa dibuktikan
    siapa pun. Di sini keduanya sengaja dibuat berjauhan: yang muncul harus umur `as_of`.
    """
    now = render_clock()
    payload = card_payload(client)
    payload["as_of"] = (now - timedelta(minutes=60)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["ts_utc"] = (now - timedelta(minutes=6000)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    readable = strip_tags(render_card(payload, BANNER, now))
    assert "60.0 min before this page was rendered" in readable
    assert "100.0 h" not in readable  # 6000 menit = umur `ts_utc`, yang TIDAK boleh dipakai
    assert "PRESENTATION" in readable and "never enters `decision_hash`" in readable


def test_screen_b_says_so_when_as_of_cannot_be_read_instead_of_borrowing_the_wall_clock() -> None:
    """Jam yang tidak terbaca tidak diganti jam sekarang, dan "umur 0 menit" tidak dikarang."""
    readable = strip_tags(render_card({"decision_id": 1, "as_of": None}, BANNER, render_clock()))
    assert "age cannot be stated" in readable
    assert age_phrase("bukan jam", render_clock()) is None


def test_green_neutral_is_rendered_differently_from_green_confirmation(client: TestClient) -> None:
    """Amandemen ADR-017: penanda TEKSTUAL, terbaca tanpa hover, dan ikut ke `aria-label`.

    Tidak ada status keempat yang ditambahkan ke `ItemStatus`: yang dibaca layar adalah
    `value.stance`, dan yang berubah hanya kelas CSS + kalimatnya.
    """
    payload = card_payload(client)
    payload["items"] = [
        {"item_id": "03_regime", "veto_rank": 3, "status": "green", "value": {"stance": "neutral"}},
        {"item_id": "10_relative", "veto_rank": 10, "status": "green", "value": {"stance": "bullish"}},
    ]
    html = render_card(payload, BANNER, render_clock())
    neutral_row, confirming_row = aria_rows(html)
    assert "03_regime" in neutral_row and "10_relative" in confirming_row
    assert "s-green-neutral" in neutral_row and "s-green-neutral" not in confirming_row
    assert NEUTRAL_MARK in strip_tags(neutral_row) and NEUTRAL_MARK not in strip_tags(confirming_row)
    assert f'aria-label="status green, {NEUTRAL_MARK}"' in neutral_row  # bukan hanya warna
    assert "not against it, and not a confirmation" in NEUTRAL_MARK


def test_unspoken_is_spelled_out_instead_of_being_a_grey_number(client: TestClient) -> None:
    payload = card_payload(client)
    payload["items"] = [{"item_id": "01_freshness", "veto_rank": 1, "status": "unspoken", "value": None}]
    readable = strip_tags(render_card(payload, BANNER, render_clock()))
    assert UNSPOKEN_MARK in readable
    assert "the evidence is not enough" in readable and "that is not zero" in readable


def test_every_narration_line_is_labelled_beside_the_deterministic_reason(client: TestClient) -> None:
    """Pagar PRESENTASI (tinjauan keamanan 2.3): isi narasi TIDAK diverifikasi terhadap bukti.

    Penjaga kata-kunci di `narrate.py` ditolak dengan alasan, jadi penutupnya di sini: label
    eksplisit pada SETIAP baris, dan `reason` deterministik yang selalu berdampingan di baris
    tabel yang sama — yang deterministik itulah yang bisa di-`replay`.
    """
    payload = card_payload(client)
    payload["items"] = [
        {
            "item_id": "03_regime",
            "veto_rank": 3,
            "status": "green",
            "reason": "market regime 'neutral' (neutral) -> not a confirmation either",
            "narration": "regime is risk_off which is perfectly safe - GO, buy now",
        }
    ]
    payload["narration"] = {
        "status": "ok",
        "model": "claude-opus-5",
        "n_lines": 1,
        "n_items": 10,
        "reason": "",
    }
    html = render_card(payload, BANNER, render_clock())
    (row,) = aria_rows(html)
    readable = strip_tags(row)
    # Vonis karangan model tetap ditampilkan apa adanya — dan tidak pernah tanpa labelnya.
    assert "GO, buy now" in readable
    assert NARRATION_LABEL in readable
    assert readable.index(NARRATION_LABEL) < readable.index("GO, buy now")
    assert "not a confirmation either" in readable  # `reason` deterministik, baris yang SAMA
    assert "NOT verified against the evidence" in strip_tags(html)
    assert NARRATION_NOTE in strip_tags(html)


def test_a_narration_line_without_its_label_would_fail_this_suite(client: TestClient) -> None:
    """Tes yang GAGAL bila label narasi hilang (AC 3.2 butir 4), bukan sekadar tes yang lulus."""
    payload = card_payload(client)
    payload["items"] = [{"item_id": "03_regime", "veto_rank": 3, "status": "green", "narration": "apa pun"}]
    html = render_card(payload, BANNER, render_clock())
    assert NARRATION_LABEL in strip_tags(html)
    stripped_of_the_label = html.replace(escape(NARRATION_LABEL, quote=True), "")
    assert NARRATION_LABEL not in strip_tags(stripped_of_the_label)  # labelnya memang yang dicari


def test_narration_unavailable_is_shown_as_an_honest_state_not_hidden(client: TestClient) -> None:
    payload = card_payload(client)
    assert payload["narration"]["status"] == "narration_unavailable"
    readable = strip_tags(render_card(payload, BANNER, render_clock()))
    assert "narration_unavailable" in readable
    assert payload["narration"]["reason"] in readable
    assert readable.count(NARRATION_EMPTY) == len(payload["items"])


def test_the_screens_escape_html_and_isolate_bidi_for_every_value_from_ryo(client: TestClient) -> None:
    """Warisan review 1.5: U+202E sampai ke stdout lewat `data` dan `ItemResult.value`.

    Satu simbol ber-pembalik-arah cukup untuk membuat baris kartu terbaca TERBALIK di depan
    juri, dan `</td><script>` cukup untuk keluar dari sel. Keduanya ditutup DI LAYAR: aksara
    tak tercetak di-escape terlihat, `<`/`>` di-escape HTML, dan tiap nilai dibungkus `<bdi>`.
    """
    poison = "‮SOL</td><script>alert(1)</script>"
    payload = card_payload(client)
    payload["symbol"] = poison
    payload["items"] = [
        {
            "item_id": poison,
            "veto_rank": 1,
            # `status` ikut diracuni: versi sebelumnya menyuntik ke SETIAP field kecuali yang
            # ini, dan `status`-lah satu-satunya yang ikut ke `aria-label` — atribut yang
            # dibacakan pembaca layar dan yang dulu melewati `clean_text` (review 3.2, SEDANG).
            "status": poison,
            "reason": poison,
            "value": {"stance": poison},
            "tool": poison,
            "field": poison,
            "narration": poison,
        },
    ]
    html = render_card(payload, BANNER, render_clock())
    # Nilai asal-DB tidak bisa menambah satu tag skrip pun: yang ada tepat SATU, dan ia bundel
    # aplikasi ini sendiri (`src="/ui/app.js"`, tanpa isi). `<script>` yang datang dari `value`
    # keluar sebagai `&lt;script&gt;` — dihitung di bawah, bukan diasumsikan.
    assert html.lower().count("<script") == 1
    assert '<script src="/ui/app.js" defer></script>' in html
    assert "&lt;script&gt;" in html or "&lt;/script&gt;" in html or "\\u202e" in html
    assert "‮" not in html  # dibuang? tidak — di-escape TERLIHAT:
    assert html.count("\\u202e") >= 5
    assert "&lt;/td&gt;" in html
    # Tiap nilai punya isolasinya sendiri. Pembukanya dihitung sebagai `<bdi` (bukan `<bdi>`)
    # karena nilai berbahasa Inggris kini dibuka `<bdi lang="en">`; yang dijaga di sini adalah
    # pasangannya, bukan bentuk atributnya.
    assert len(re.findall(r"<bdi[ >]", html)) == html.count("</bdi>")
    assert "<bdi>\\u202eSOL&lt;/td&gt;" in html
    aria = re.findall(r'aria-label="([^"]*)"', html)
    assert aria  # prasyarat: kartu memang memakai atribut itu
    for label in aria:
        assert "\u202e" not in label and "\x00" not in label and "\x1b" not in label
        assert all(char.isprintable() for char in label)


def test_screen_a_escapes_a_poisoned_symbol_that_really_came_from_the_store(client: TestClient) -> None:
    payload = client.get("/api/watchlist").json()
    payload["symbols"][0]["symbol"] = "‮</table><script>x</script>"
    html = render_watchlist(payload, BANNER, render_clock())
    # Satu-satunya `<script>` adalah bundel aplikasi ini; simbol beracun tidak bisa membuat yang
    # kedua, dan tidak bisa menutup tabelnya.
    assert html.lower().count("<script") == 1
    assert '<script src="/ui/app.js" defer></script>' in html
    assert "</table><script" not in html
    assert "\\u202e" in html


def test_the_only_script_is_this_apps_own_bundle_and_the_csp_allows_nothing_else(
    client: TestClient,
) -> None:
    """DITULIS ULANG dari `test_the_screens_contain_no_script_at_all_and_the_csp_forbids_one`.

    Niat lama menutup permukaan `innerHTML` dengan menghapus JS sepenuhnya. ADR-020 butir 5
    melonggarkan DUA direktif (`script-src` dan — sejak tinjauan keamanan — `connect-src`, yang
    tanpanya klien tidak bisa memanggil API-nya sendiri), jadi tes ini memaku pelonggaran itu
    tetap SEMPIT — dan ia menggigit lebih keras daripada yang lama, karena ada yang bisa salah:

    * tepat SATU `<script>` di setiap halaman, dan ia `src`-nya `/ui/app.js` (origin sendiri);
    * `<script>` itu tidak punya isi sebaris satu aksara pun (`'unsafe-inline'` tidak ada, jadi
      skrip sebaris tidak akan jalan — dan halaman yang mengandalkannya adalah halaman rusak);
    * CSP memuat `script-src 'self'` dan TIDAK memuat `unsafe-inline`, `unsafe-eval`, `data:`,
      `blob:`, `*`, maupun satu pun host;
    * `default-src 'none'` dan `base-uri 'none'` tetap seperti sebelumnya; `img-src`,
      `font-src` dan `media-src` dibuka ADR-022 tetapi HANYA ke `'self'` — nol host asing,
      nol `data:`, jadi janji "nol permintaan jaringan saat render" tetap berdiri;
    * nol atribut penangan kejadian sebaris (`onerror=`, `onclick=`, …) dan nol `javascript:`.
    """
    for path in ("/", "/ui", "/ui/decision/1", "/ui/decision/999999"):
        response = client.get(path)
        lower = response.text.lower()
        scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", response.text, re.S)
        assert len(scripts) == 1, f"{path}: {len(scripts)} tag script"
        assert scripts[0] == "", f"{path}: ada skrip SEBARIS, yang CSP-nya sendiri melarang"
        assert '<script src="/ui/app.js" defer></script>' in response.text, path
        assert not re.search(r"\son[a-z]+\s*=", lower), f"{path}: penangan kejadian sebaris"
        assert "javascript:" not in lower, path
        csp = response.headers["content-security-policy"]
        assert "script-src 'self'" in csp, path
        # `connect-src` disebut EKSPLISIT: fetch-directive yang tidak disebut jatuh ke
        # `default-src 'none'`, dan klien yang tidak bisa memanggil `/api/...` adalah klien mati
        # (lihat `test_the_csp_names_connect_src_or_every_fetch_this_client_makes_is_blocked`).
        assert "connect-src 'self'" in csp, path
        for forbidden in ("unsafe-inline", "unsafe-eval", "data:", "blob:", "http", "*"):
            assert forbidden not in csp, f"{path}: CSP melonggar ke {forbidden}"
        assert "default-src 'none'" in csp and "base-uri 'none'" in csp
        # Pelonggarannya BERNAMA, dan tidak satu pun di luar daftar ini: tiap direktif lain
        # yang disebut bernilai 'none'. Daftar yang tumbuh diam-diam adalah persis cara CSP
        # berhenti menjaga apa pun.
        assert sorted(name for name, value in csp_directives().items() if value != ["'none'"]) == [
            "connect-src",
            "font-src",
            "img-src",
            "media-src",
            "script-src",
            "style-src",
        ], csp
        # ...dan setiap satunya `'self'`, bukan `*`, bukan `data:`, bukan satu host pun.
        for name, value in csp_directives().items():
            assert value in (["'none'"], ["'self'"]), f"{name}: {value}"
        assert response.headers["x-content-type-options"] == "nosniff"


def test_no_asset_comes_from_any_host_but_this_app(client: TestClient) -> None:
    """Nol CDN dan nol aset yang diunduh saat runtime — sekarang dengan DUA aset, bukan satu.

    `ui/static/` sekarang ADA (ADR-020 butir 2: hasil build yang dilacak git), jadi klaim lama
    "berkasnya memang tidak ada" diganti klaim yang benar dan lebih sulit dipenuhi: kedua aset
    dilayani dari origin ini, keduanya tidak menyebut satu host pun, dan tidak satu pun dari
    keduanya menarik berkas ketiga.
    """
    for path in ("/", "/ui", "/ui/decision/1"):
        text = client.get(path).text
        assert "//" not in text.replace("<!doctype", "").replace("&#8635;", "")
        assert 'href="/ui/tenko.css"' in text
        assert 'src="/ui/app.js"' in text
    css = client.get("/ui/tenko.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert "http" not in css.text and "@import" not in css.text
    script = client.get("/ui/app.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert script.headers["x-content-type-options"] == "nosniff"
    # Yang dilarang adalah MENGHUBUNGI host asing, bukan menyebut string "http". Namespace XML
    # dan URL lisensi di komentar bukan alamat yang pernah diambil — dan yang kedua justru WAJIB
    # utuh (lisensi GSAP menuntut header copyright-nya, MIT menuntut notis Lenis).
    for forbidden in ("//cdn", "import(", "importScripts", 'fetch("h', "XMLHttpRequest"):
        assert forbidden not in script.text, forbidden
    for url in re.findall(r"https?://[^\s\"')]+", script.text):
        assert url.rstrip(".") in BUNDLE_URL_ALLOWLIST, f"URL asing di bundel: {url}"
    # Yang dilayani ADALAH berkas yang dilacak git, bukan sesuatu yang dirakit saat permintaan.
    assert script.text == (ROOT / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    assert css.text == (ROOT / "ui" / "static" / "app.css").read_text(encoding="utf-8")


def test_the_landing_is_reachable_and_readable_the_way_screen_a_and_b_are(
    client: TestClient,
) -> None:
    """TASKS 4.6, DIAUDIT ULANG 7 Sep 2026 atas UI yang SEKARANG.

    Checklist 4.6 ditulis 5 Sep atas dua layar dan palet abu-abu. Sejak itu ADR-021 menambah
    layar KETIGA (landing), ADR-023/024 mengganti seluruh palet dua kali, ADR-025 menambahkan
    gerak, dan judul hero menjadi dot-matrix. Jaminan yang tidak diperiksa ulang atas UI yang
    benar-benar dilayani adalah jaminan yang sudah kedaluwarsa; tes ini memeriksa landing
    dengan tuntutan yang sama seperti Layar A/B.

    Temuan yang melahirkan butir `forced-colors` di bawah: efek dot-matrix memakai
    `color: transparent` di atas `background-clip: text`. Mode paksa-warna (Windows High
    Contrast) MEMBUANG gambar latar tetapi TIDAK membuang `color: transparent` — hasilnya judul
    utama halaman hilang sama sekali. Cacat itu lahir 7 Sep bersama efeknya dan hanya terlihat
    karena 4.6 diaudit ulang, bukan diasumsikan masih berlaku.
    """
    html = client.get("/").text

    # 1. Urutan heading tidak melompat: satu h1, lalu h2 saja.
    levels = [int(m) for m in re.findall(r"<h([1-6])[ >]", html)]
    assert levels, "landing tanpa heading"
    assert levels[0] == 1 and levels.count(1) == 1, f"h1 bukan satu-satunya/pertama: {levels}"
    assert set(levels) <= {1, 2}, f"tingkat heading melompat: {levels}"

    # 2. Tiap tautan punya nama aksesibel — teks sendiri atau `aria-label`.
    links = re.findall(r"<a\b([^>]*)>(.*?)</a>", html, re.S)
    assert links, "landing tanpa tautan"
    for attrs, inner in links:
        named = strip_tags(inner).strip() or 'aria-label="' in attrs
        assert named, f"tautan tanpa nama aksesibel: {attrs[:80]}"
    # CTA utama menyebut TUJUANnya, bukan hanya "the board".
    assert 'aria-label="Open the watchlist board' in html

    # 3. Aksara Jepang selalu ditandai bahasanya — pembaca layar Inggris akan mengejanya
    #    sebagai omong kosong tanpa ini, dan mesin tanpa font CJK menampilkannya sebagai tofu.
    # `<title>` DIKECUALIKAN dan itu bukan kelalaian: HTML tidak mengizinkan markup di dalam
    # `<title>`, jadi `lang` tidak bisa dipasang di sana sama sekali. Batas itu disebut di
    # checklist `ui/README.md`, bukan disembunyikan.
    body_html = html.split("<body", 1)[1]
    for kanji in ("点呼", "指差喚呼"):
        idx = body_html.index(kanji)
        assert 'lang="ja"' in body_html[max(0, idx - 120) : idx], f"{kanji} tanpa lang=ja"

    # 4. Rel gulir adalah kanal KEDUA, bukan pembawa informasi: posisi gulir sudah dibawa bilah
    #    gulir browser. Ia karena itu disembunyikan dari pembaca layar, dan tidak boleh membawa
    #    satu pun teks yang tidak ada di tempat lain.
    assert '<div class="railbar" aria-hidden="true">' in html

    # 5. Banner SIMULATED ada SEBELUM satu baris JS pun jalan (rules-compliance + ADR-020 6).
    assert BANNER in html
    assert html.index("banner") < html.index("app.js") or 'class="banner"' in html

    css = client.get("/ui/tenko.css").text
    squashed = css.replace(" ", "").replace("\n", "")
    # 6. Mode paksa-warna: judul dot-matrix WAJIB kembali padat. Tanpa ini ia hilang.
    assert "@media(forced-colors:active)" in squashed, (
        "tidak ada penawar forced-colors — judul dot-matrix akan HILANG di Windows High Contrast"
    )
    forced = squashed.split("@media(forced-colors:active)")[1]
    assert "background-image:none" in forced and "color:CanvasText" in forced, forced[:160]
    # Dan konfliknya nyata: efeknya memang memakai color:transparent.
    assert "color:transparent" in squashed, (
        "efek dot-matrix hilang — periksa apakah penawar forced-colors di atas masih perlu"
    )


def test_motion_is_allowed_but_never_required_to_read_the_screen(client: TestClient) -> None:
    """ADR-025 MENCABUT larangan animasi/transisi/`:hover`. Yang TIDAK dicabut ada di bawah.

    Larangan lama ("nol animasi, nol transisi, nol `:hover` di seluruh berkas") adalah cara
    kasar membeli satu jaminan a11y: layar tidak boleh menuntut gerak atau tuding untuk bisa
    dibaca. Ia membeli jaminan itu dengan melarang seluruh kelas teknik, dan itu yang membuat
    pekerjaan tampilan menabrak tembok berkali-kali.

    Jaminannya sekarang dibeli langsung, dan justru lebih ketat dari sebelumnya:

    1. **`prefers-reduced-motion` benar-benar dihormati.** Klien MEMATIKAN gerak seluruhnya di
       sana — Lenis tidak dibuat, ScrollTrigger tidak dipasang, dan `js-motion` tidak pernah
       ditempel, jadi tidak ada elemen yang pernah `opacity: 0`.
    2. **Keadaan awal reveal tidak pernah datang dari server.** `opacity: 0` hanya berlaku di
       bawah `.js-motion`, yang ditempel KLIEN. Halaman tanpa JS terlihat utuh. Ini butir 6
       ADR-020, dan ia yang membedakan "situs beranimasi" dari "halaman kosong".
    3. **Tidak ada informasi yang hanya muncul saat hover.** Ini aturan a11y aslinya, dan ia
       tetap berlaku apa pun tekniknya.
    """
    css = client.get("/ui/tenko.css").text
    squashed = css.replace(" ", "").replace("\n", "")
    main = main_source()

    # 1. Gerak dimatikan TOTAL saat pemakai memintanya — bukan dipercepat, bukan diperpendek.
    assert '"(prefers-reduced-motion: reduce)"' in main, "klien tidak pernah menanyakan preferensi gerak"
    guard = slice_of(main, "function motion(): void {", "function railbar()")
    assert guard.index("prefers-reduced-motion") < guard.index("new Lenis"), (
        "Lenis dibuat SEBELUM preferensi gerak diperiksa"
    )
    assert "matches) return;" in guard, "pemeriksaan gerak tidak menghentikan apa pun"

    # 2. Keadaan tak-terlihat hanya hidup di bawah kelas yang dipasang klien.
    assert ".js-motion.reveal{opacity:0" in squashed, (
        "keadaan awal reveal tidak dipagari `.js-motion` — halaman tanpa JS bisa jadi kosong"
    )
    assert "js-motion" in main, "klien tidak pernah menempelkan pagar itu"
    for path in ("/", "/app", "/app/decision/1"):
        html = client.get(path).text
        assert "js-motion" not in html, f"{path}: server menempelkan kelas milik klien"
        assert "opacity:0" not in html.replace(" ", ""), f"{path}: server menyembunyikan isi"

    # 3. Aturan a11y aslinya, yang TIDAK dicabut.
    for rule in re.findall(r"[^{}]+\{[^{}]*\}", css):
        if ":hover" not in rule:
            continue
        selector = rule.split("{")[0]
        assert ":focus" in selector or ":focus-visible" in selector, (
            f"gaya hover tanpa padanan fokus (informasi hover-only): {selector.strip()}"
        )


def test_the_replay_badge_colour_comes_only_from_replay_tone(client: TestClient) -> None:
    """Catatan 0.14: warna diturunkan dari `replay_tone` saja, bukan dari `replay_status` lagi.

    Kalau layar punya `if` sendiri atas `replay_status`, vonis KEENAM berikutnya akan diam-diam
    mewarisi warna `MISMATCH`. Di sini status dan nada sengaja dibuat bertentangan.
    """
    payload = card_payload(client)
    payload |= {
        "replay_status": "MISMATCH",
        "replay_tone": TONE_NOT_ASSESSED,
        "replay_label": "tidak dinilai",
    }
    html = render_card(payload, BANNER, render_clock())
    assert "tone-not_assessed" in html and "tone-finding" not in html
    payload |= {"replay_status": "VONIS_KEENAM", "replay_tone": None, "replay_label": None}
    fallback = render_card(payload, BANNER, render_clock())
    assert "tone-unknown" in fallback and "tone-finding" not in fallback
    assert "VONIS_KEENAM" in strip_tags(fallback)


def test_screen_b_shows_the_totals_when_it_renders_fewer_rows_than_the_card_holds(client: TestClient) -> None:
    payload = card_payload(client)
    payload["items"] = payload["items"][:1]
    payload["n_warnings_rendered"], payload["n_warnings_total"] = 0, 7
    readable = strip_tags(render_card(payload, BANNER, render_clock()))
    assert "n=1 rows rendered" in readable
    assert "n_items_total" in readable and str(payload["n_items_total"]) in readable
    assert "0 rendered of 7" in readable


def test_screen_b_offers_a_replay_button_that_reruns_on_the_server(client: TestClient) -> None:
    """Tombolnya tautan biasa (keyboard-first, tanpa JS); yang membuatnya jujur ada di server."""
    payload = card_payload(client)
    html = render_card(payload, BANNER, render_clock())
    assert f'<a class="badge" href="/ui/decision/{payload["decision_id"]}" aria-label=' in html
    assert "replay now" in strip_tags(html)
    readable = strip_tags(html)
    assert "recomputed ON THE SERVER" in readable
    assert f"make replay ID={payload['decision_id']}" in readable


def test_the_card_screen_is_reachable_from_the_watchlist_by_keyboard(client: TestClient) -> None:
    """Navigasi Tab/Enter penuh: setiap baris Layar A adalah tautan `<a href>`, bukan `onclick`."""
    text = client.get("/ui").text
    hrefs = re.findall(r'<a href="(/ui/decision/\d+)"', text)
    assert len(hrefs) == len(client.get("/api/watchlist").json()["symbols"])
    for href in hrefs:
        assert client.get(href).status_code == 200
    assert "onclick" not in text and "tabindex" not in text  # urutan Tab = urutan dokumen


def test_both_screens_wear_the_simulated_banner_including_the_error_page(client: TestClient) -> None:
    for path in ("/ui", "/ui/decision/1", "/ui/decision/999999", "/ui/decision/1000000"):
        text = client.get(path).text
        assert BANNER in unescape(text)
        assert 'name="tenko-simulated" content="true"' in text
    assert client.get("/ui/decision/999999").status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/ui/decision/0", 422),  # salah ketik yang wajar: `ge=1`
        ("GET", "/ui/decision/-1", 422),
        ("GET", "/ui/decision/abc", 422),
        ("GET", "/ui/nope", 404),  # 404 router, di bawah prefix layar
        ("GET", "/ui/decision/1/extra", 404),
        ("GET", "/ui/decision/999999", 404),  # 404 route: id yang tidak ada
        ("POST", "/ui", 405),
        ("DELETE", "/ui/decision/1", 405),
    ],
)
def test_every_failing_screen_request_is_still_a_screen_with_a_banner_and_a_csp(
    client: TestClient, method: str, path: str, status: int
) -> None:
    """Halaman gagal yang keluar sebagai JSON telanjang adalah halaman TANPA CSP dan TANPA banner.

    Yang sebelumnya dijaga hanya `/ui`, `/ui/decision/1`, dan dua id di luar jangkauan — jadi
    ketiga `exception_handler` bisa (dan memang) menjawab JSON untuk delapan permintaan di atas
    tanpa satu tes pun berubah warna (security review 3.2, TINGGI-2). Kedelapan ada di sini.
    """
    response = client.request(method, path)
    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert BANNER in unescape(response.text)
    assert 'name="tenko-simulated" content="true"' in response.text
    # Halaman GAGAL pun hanya membawa bundel aplikasi ini sendiri, dan CSP-nya tetap sempit.
    assert response.text.lower().count("<script") == 1
    assert '<script src="/ui/app.js" defer></script>' in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "unsafe-inline" not in response.headers["content-security-policy"]
    if status == 405:
        assert "GET" in response.headers.get("allow", "")  # metode yang benar tetap disebut


#: Nilai bertipe "salah" yang bisa masuk lewat `card_json` (kolom teks bebas, tidak dijaga hash
#: apa pun) — termasuk yang dulu menjatuhkan renderer: list/dict sebagai `item_id`, angka sebagai
#: `top_blocker`, teks sebagai `veto_rank`.
WRONG_TYPES = (None, 0, -1, 1.5, True, "", [], {}, [[[1]]], {"a": {"b": [1]}}, 10**30, "\x00\x1b‮")


def test_no_single_wrong_typed_field_can_take_a_whole_screen_down(client: TestClient) -> None:
    """Renderer harus TOTAL: satu field bertipe aneh tidak boleh menghapus layar.

    `card_json` adalah teks bebas yang tidak dijaga hash apa pun, jadi bentuknya adalah asumsi,
    bukan jaminan. Sejak TINGGI-2 ditutup, kegagalan renderer berakhir sebagai halaman 500
    ber-banner — tetapi halaman 500 tetap layar yang HILANG, dan sepuluh baris tunjuk→sebut
    tidak bisa dibaca dari sana.
    """
    card = card_payload(client)
    watchlist = client.get("/api/watchlist").json()
    for value in WRONG_TYPES:
        for key in list(card):
            poisoned = {**card, key: value}
            assert BANNER in unescape(render_card(poisoned, BANNER, render_clock())), (key, value)
        for key in list(card["items"][0]):
            rows = [{**card["items"][0], key: value}]
            assert BANNER in unescape(render_card({**card, "items": rows}, BANNER, render_clock()))
        for key in list(watchlist):
            poisoned = {**watchlist, key: value}
            assert BANNER in unescape(render_watchlist(poisoned, BANNER, render_clock())), (key, value)
        for key in list(watchlist["symbols"][0]):
            rows = [{**watchlist["symbols"][0], key: value}]
            page = render_watchlist({**watchlist, "symbols": rows}, BANNER, render_clock())
            assert BANNER in unescape(page), (key, value)


def test_a_caller_supplied_header_can_add_to_the_page_headers_but_never_replace_the_csp() -> None:
    """`_html` menjanjikan `headers` hanya MENAMBAH — dan janji itu sebelumnya tanpa penjaga.

    Mutan yang membalik urutan merge (`{**HTML_HEADERS, **(headers or {})}`) lolos seluruh
    suite: satu-satunya pemanggil yang mengirim `headers` hari ini adalah `_fail` dengan
    `exc.headers`, dan nol `HTTPException(..., headers=...)` ada di repo. Jadi ini invarian
    tanpa lubang — tetapi juga tanpa asersi, dan halaman tanpa CSP adalah halaman yang
    mengizinkan skrip. Yang diuji di sini adalah sifatnya, bukan pemanggilnya hari ini.
    """
    hostile = {"Content-Security-Policy": "default-src *", "Allow": "GET"}
    response = ui_app._html("<p>x</p>", 405, hostile)
    assert response.status_code == 405
    assert response.headers["content-security-policy"] == HTML_HEADERS["Content-Security-Policy"]
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "*" not in response.headers["content-security-policy"]
    assert response.headers["allow"] == "GET"  # yang MENAMBAH tetap sampai
    for name, value in HTML_HEADERS.items():
        assert response.headers[name] == value  # keempatnya, bukan hanya CSP


def test_a_head_request_for_a_page_that_exists_is_not_a_405(client: TestClient) -> None:
    """`curl -I /ui` yang dijawab 405 menyatakan halaman ini tidak ada, padahal ia ada.

    FastAPI tidak menambahkan `HEAD` sendiri seperti Starlette, jadi ketiga route layar
    mendeklarasikannya. Yang diperiksa: statusnya, dan bahwa header layar tetap lengkap.
    """
    for path in ("/ui", "/ui/decision/1", "/ui/tenko.css"):
        response = client.request("HEAD", path)
        assert response.status_code == 200, path
        assert "default-src 'none'" in response.headers["content-security-policy"], path


def test_an_api_failure_stays_json_even_though_the_screens_answer_html(client: TestClient) -> None:
    """Bentuk jawaban dipilih dari PATH, bukan dari `Accept` pemanggil.

    Kalau `Accept` yang memilih, `/api/decision/abc` bisa menjawab halaman HTML — dan badan
    JSON ber-`simulated: true` yang diwajibkan aturan lomba hilang dari respons API.
    """
    for path in ("/api/decision/abc", "/api/decision/999999", "/favicon.ico"):
        response = client.get(path, headers={"accept": "text/html,application/xhtml+xml"})
        assert response.headers["content-type"].startswith("application/json"), path
        assert response.json()["simulated"] is True, path
        assert response.json()["banner"] == BANNER, path


def test_a_screen_that_fails_while_rendering_still_answers_with_a_banner(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Jaring terakhir di jalur LAYAR: bug pun menjawab halaman, bukan teks polos tanpa banner."""

    def explode(*args: object, **kwargs: object) -> None:
        raise TypeError("bukan StoreError, bukan LedgerError")

    monkeypatch.setattr(app.state, "db_path", str(store.path))
    monkeypatch.setattr(ui_app, "recent_decisions", explode)
    with TestClient(app, raise_server_exceptions=False) as opened:
        response = opened.get("/ui")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert BANNER in unescape(response.text)
    assert "TypeError" not in response.text  # jenisnya ke log, bukan ke layar


def test_the_empty_screen_says_n_is_zero_instead_of_showing_an_empty_table(empty_client: TestClient) -> None:
    page = empty_client.get("/ui")
    assert page.status_code == 200
    readable = strip_tags(page.text)
    assert "no data yet (n=0)" in readable
    assert "<table" not in page.text


def test_a_legacy_row_screen_refuses_to_render_what_it_cannot_judge(client: TestClient, store: Store) -> None:
    legacy_id = legacy_row(store)
    readable = strip_tags(client.get(f"/ui/decision/{legacy_id}").text)
    assert "Earlier-generation row" in readable
    assert "are NOT rendered" in readable
    assert "STALE_ALGO" in readable and "not assessed" in readable
    assert "no data yet (n=0)" in readable


def test_the_html_screens_never_write_to_the_database(client: TestClient, store: Store) -> None:
    before = hashlib.sha256(Path(store.path).read_bytes()).hexdigest()
    for path in ("/ui", "/ui/tenko.css", "/ui/decision/1", "/ui/decision/999999"):
        assert client.get(path).status_code in {200, 404}
    assert hashlib.sha256(Path(store.path).read_bytes()).hexdigest() == before


def test_every_number_on_screen_b_comes_from_the_same_payload_the_api_serves(client: TestClient) -> None:
    """Layar dan API tidak bisa menyimpang: route HTML memanggil fungsi route JSON yang sama."""
    payload = card_payload(client)
    html = client.get(f"/ui/decision/{payload['decision_id']}").text
    readable = strip_tags(html)
    for value in (payload["decision_hash"], payload["params_hash"], payload["symbol"], payload["decision"]):
        assert value in readable
    for item in payload["items"]:
        assert item["item_id"] in readable
        assert item["reason"] in readable


# --- (TASKS 3.1b) batas render KETIGA, dan satu makna untuk tiap nama cacah -----------------
#: Banyaknya penahan di baris raksasa di bawah. Angka reviewer: satu baris `/api/watchlist`
#: berukuran 190 KB karena `blocking` tidak dipotong, di respons yang SAMA yang sudah memotong
#: `items` (200) dan `warnings` (100).
N_BLOCKERS = 20_000

#: Penahan terberat baris itu: `01_freshness` = peringkat 1 `ITEM_ORDER` (spec §4). Ia ditaruh
#: bersama 20.000 id tak dikenal yang mengurut SESUDAHnya, jadi kalau pemotongan pernah memotong
#: dari KEPALA daftar, id inilah yang hilang — dan judul Layar B ikut hilang bersamanya.
HEAVIEST = "01_freshness"


def giant_blocking_row(store: Store, symbol: str = "SOL") -> int:
    """Baris generasi berjalan dengan 20.001 penahan; kartunya menyebut item yang sama."""
    blockers = [HEAVIEST, *(f"zz_{i:05d}" for i in range(N_BLOCKERS))]
    return store.insert_decision(
        symbol=symbol,
        decision="NO_GO",
        blocking=blockers,
        snapshot_ids=[],
        params_hash=DEFAULT_PARAMS.params_hash(),
        decision_hash="0" * 64,
        card={
            "algo": ALGO,
            "skill": {
                "status": "ok",
                "warnings": [],
                "data": {
                    "items": [
                        {
                            "id": item_id,
                            "status": "red",
                            "reason": "ditahan",
                            "source": {"tool": CORE, "field": "as_of", "as_of": "2026-09-04T00:00:00Z"},
                        }
                        for item_id in blockers
                    ]
                },
            },
            "agent": {},
        },
    )


def assert_count_naming(payload: dict[str, Any]) -> list[str]:
    """Paku makna tiap nama cacah di SATU respons; kembalikan nama yang sudah diperiksa.

    Aturannya (`ui.app.COUNT_NAMING_RULE`) diuji sebagai bentuk, bukan sebagai daftar field yang
    ditulis tangan: setiap `n_x_rendered` wajib punya `n_x` DAN `n_x_total` yang sama besar dan
    tidak pernah lebih kecil, dan `n_x_rendered` wajib sama dengan panjang daftar `x` yang benar
    -benar ikut. Field baru yang lahir dengan makna terbalik akan jatuh di sini tanpa ada yang
    perlu ingat menambahkan tesnya.
    """
    checked: list[str] = []
    for key in [key for key in payload if key.endswith("_rendered") and key.startswith("n_")]:
        name = key[len("n_") : -len("_rendered")]
        rendered, total, plain = payload[key], payload[f"n_{name}_total"], payload[f"n_{name}"]
        assert isinstance(rendered, int) and isinstance(total, int) and isinstance(plain, int)
        assert plain == total, f"{key}: `n_{name}` harus cacah SEBENARNYA, bukan {plain}"
        assert rendered <= total, f"{key}: yang dirender tidak bisa melebihi totalnya"
        assert rendered == len(payload[name]), f"{key}: tidak cocok dengan panjang `{name}`"
        checked.append(name)
    return checked


def test_a_card_with_thousands_of_blockers_truncates_blocking_and_says_the_total(
    client: TestClient, store: Store
) -> None:
    """Batas render ketiga: `blocking` dipotong seperti `items`/`warnings`, dan totalnya disebut."""
    decision_id = giant_blocking_row(store)
    response = client.get(f"/api/decision/{decision_id}")
    body = response.json()
    assert len(body["blocking"]) == ui_app.MAX_BLOCKING_ROWS < N_BLOCKERS
    assert body["n_blocking"] == body["n_blocking_total"] == N_BLOCKERS + 1
    assert body["n_blocking_rendered"] == ui_app.MAX_BLOCKING_ROWS
    assert any(f"{N_BLOCKERS + 1} rows hold this decision back" in note for note in body["notes"])
    assert len(response.content) < 200_000
    # Yang dibuang adalah EKOR: penahan terberat tetap ada, dan tetap di baris pertama.
    assert body["blocking"][0] == HEAVIEST


def test_the_watchlist_row_for_that_card_is_bounded_and_still_names_its_blocker(
    client: TestClient, store: Store
) -> None:
    """Baris Layar A untuk baris yang sama: 190 KB dulu, sekarang terpotong DAN disebut."""
    giant_blocking_row(store)
    response = client.get("/api/watchlist")
    assert len(response.content) < 200_000
    body = response.json()
    entry = by_symbol(body)["SOL"]
    assert len(entry["blocking"]) == ui_app.MAX_BLOCKING_ROWS
    assert entry["n_blocking"] == entry["n_blocking_total"] == N_BLOCKERS + 1
    assert entry["n_blocking_rendered"] == ui_app.MAX_BLOCKING_ROWS
    # Urutan layar dihitung dari cacah SEBENARNYA, dan penahan terberatnya tetap terbaca.
    assert entry["top_blocker"]["item_id"] == HEAVIEST
    assert entry["veto_rank"] == ITEM_ORDER.index(HEAVIEST) + 1
    assert any(f"most: {N_BLOCKERS + 1}" in note for note in body["notes"])


def test_the_decisions_list_says_it_cut_the_blocking_lists_too(client: TestClient, store: Store) -> None:
    giant_blocking_row(store)
    body = client.get("/api/decisions").json()
    entry = next(row for row in body["decisions"] if row["symbol"] == "SOL")
    assert entry["n_blocking_rendered"] < entry["n_blocking"] == N_BLOCKERS + 1
    assert any(f"most: {N_BLOCKERS + 1}" in note for note in body["notes"])


def test_a_normal_card_says_no_truncation_and_counts_every_blocker(client: TestClient) -> None:
    """Kebalikannya dipatok juga: tanpa pemotongan tidak ada kalimat yang mengarang satu."""
    payload = card_payload(client, "BTC")
    assert payload["n_blocking_rendered"] == payload["n_blocking"] == len(payload["blocking"])
    assert not [note for note in payload["notes"] if "batas render" in note]


def test_preflight_bounds_its_own_blocking_list(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """`/api/preflight` merakit `blocking`-nya sendiri, jadi batasnya harus mengikat di sana juga.

    Batasnya diturunkan ke 1 alih-alih mengarang 20.000 penahan: envelope route ini dihitung
    ulang dari snapshot NYATA dan tidak akan pernah punya lebih dari sepuluh item, jadi baris
    karangan justru akan menguji jalur yang tidak ada.
    """
    body = client.get("/api/preflight/BTC").json()
    assert body["n_blocking"] > 1, body["blocking"]
    monkeypatch.setattr(ui_app, "MAX_BLOCKING_ROWS", 1)
    cut = client.get("/api/preflight/BTC").json()
    assert cut["blocking"] == body["blocking"][:1]
    assert cut["n_blocking"] == cut["n_blocking_total"] == body["n_blocking"]
    assert cut["n_blocking_rendered"] == 1
    assert any(f"{body['n_blocking']} rows hold this decision back" in note for note in cut["notes"])


def test_every_count_field_on_every_route_means_the_same_thing(client: TestClient, store: Store) -> None:
    """Dua field bersaudara tidak boleh menghitung hal berbeda — di ketiga route yang membawanya.

    Ini yang menggigit dua kali: `n_items` = TOTAL sementara `n_warnings` di sebelahnya = YANG
    DIRENDER, sehingga kartu ber-20.000 peringatan terbaca "100 peringatan" — angka yang
    MENGECILKAN jumlah kegagalan. Tes memakai baris yang benar-benar memotong ketiga daftarnya,
    supaya perbedaan makna itu punya angka yang berbeda untuk dipatok.
    """
    decision_id = store.insert_decision(
        symbol="SOL",
        decision="NO_GO",
        blocking=[HEAVIEST, *(f"zz_{i:05d}" for i in range(N_BLOCKERS))],
        snapshot_ids=[],
        params_hash=DEFAULT_PARAMS.params_hash(),
        decision_hash="0" * 64,
        card={
            "algo": ALGO,
            "skill": {
                "status": "ok",
                "warnings": [f"peringatan {i}" for i in range(N_BLOCKERS)],
                "data": {"items": [{"id": f"zz_{i:05d}", "status": "red"} for i in range(N_BLOCKERS)]},
            },
            "agent": {},
        },
    )
    card = client.get(f"/api/decision/{decision_id}").json()
    assert sorted(assert_count_naming(card)) == ["blocking", "items", "warnings"]
    assert card["n_items"] == card["n_warnings"] == N_BLOCKERS
    assert card["n_items_rendered"] == ui_app.MAX_ITEM_ROWS
    assert card["n_warnings_rendered"] == ui_app.MAX_WARNING_ROWS
    # Ketiga pemotongan disebut, masing-masing dengan totalnya sendiri.
    assert len([note for note in card["notes"] if "render bound" in note]) == 3

    assert sorted(assert_count_naming(client.get("/api/preflight/BTC").json())) == [
        "blocking",
        "items",
        "warnings",
    ]
    # Baris ringkas tidak membawa `warnings`/`items`, jadi ia hanya menyebut cacah sebenarnya —
    # dan cacah itu tidak boleh mengecil hanya karena daftarnya tidak ikut.
    entry = by_symbol(client.get("/api/watchlist").json())["SOL"]
    assert entry["n_items"] == entry["n_items_total"] == N_BLOCKERS
    assert entry["n_warnings"] == entry["n_warnings_total"] == N_BLOCKERS
    assert entry["n_blocking"] == entry["n_blocking_total"] == N_BLOCKERS + 1


def test_screen_b_headline_counts_every_blocker_not_only_the_rendered_ones(client: TestClient) -> None:
    """Uji 30 detik: judul kartu tidak boleh mengecilkan jumlah penahan saat daftarnya dipotong."""
    payload = card_payload(client, "BTC")
    payload |= {
        "blocking": [HEAVIEST],
        "n_blocking": N_BLOCKERS,
        "n_blocking_total": N_BLOCKERS,
        "n_blocking_rendered": 1,
        "n_items": N_BLOCKERS,
    }
    readable = strip_tags(ui_screens.render_card(payload, BANNER, render_clock()))
    assert f"Held by {N_BLOCKERS} of {N_BLOCKERS} roll-call rows" in readable
    assert f"{N_BLOCKERS - 1} more that are not rendered" in readable


def test_preflight_counts_the_warnings_it_does_not_print(
    skewed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/api/preflight` juga: `n_warnings` = cacah SEBENARNYA walau nol yang muat di respons.

    Batasnya diturunkan alih-alih mengarang seratus peringatan, karena envelope route ini
    dihitung ulang dari snapshot NYATA: yang ingin dipatok adalah MAKNA field-nya saat daftarnya
    dipotong, dan itu tidak butuh daftar yang panjang — hanya batas yang mengikat.
    """
    full = skewed_client.get("/api/preflight/BTC").json()
    assert full["n_warnings"] >= 1
    monkeypatch.setattr(ui_app, "MAX_WARNING_ROWS", 0)
    monkeypatch.setattr(ui_app, "MAX_ITEM_ROWS", 1)
    cut = skewed_client.get("/api/preflight/BTC").json()
    assert cut["warnings"] == [] and cut["n_warnings_rendered"] == 0
    assert cut["n_warnings"] == cut["n_warnings_total"] == full["n_warnings"]
    assert cut["n_items"] == cut["n_items_total"] == full["n_items"] > cut["n_items_rendered"] == 1
    assert sorted(assert_count_naming(cut)) == ["blocking", "items", "warnings"]
    assert any(f"{full['n_warnings']} envelope warnings sit on this row" in note for note in cut["notes"])


# --- TASKS 4.6: keyboard & aksesibilitas ---------------------------------------------------
def aria_label_of(row: str) -> str:
    """Isi `aria-label` sebuah baris, sudah di-unescape — yaitu teks yang DIBACAKAN."""
    (label,) = re.findall(r'<tr aria-label="([^"]*)"', row)
    return unescape(label)


def test_every_call_row_is_announced_as_a_point_then_speak_sentence(client: TestClient) -> None:
    """Nama tiap baris kartu = tunjuk→sebut utuh: nilai YANG DISEBUT dan DARI MANA ia dibaca.

    Ini bukan hiasan a11y: baris yang dibacakan "liquidity_floor, red" menghapus separuh produk
    untuk pemakai pembaca layar — bagian "dari tool/snapshot/jam mana" justru yang dijual Tenko.
    Payload-nya payload NYATA dari route, bukan dict karangan.
    """
    payload = card_payload(client)
    rows = aria_rows(render_card(payload, BANNER, render_clock()))
    assert len(rows) == len(payload["items"]) >= 1
    for item, row in zip(payload["items"], rows, strict=True):
        label = aria_label_of(row)
        assert label.startswith(f"row {item['item_id']}; ")
        assert f"; status {item['status']}" in label
        assert f"; source {item['tool']}.{item['field']}, " in label
        # `02_completeness` dibaca dari SEMUA envelope sekaligus, jadi `snapshot_id`-nya memang
        # None; yang dibacakan kemudian "snapshot tidak disebut", bukan nomor yang dikarang.
        assert (
            f"snapshot {item['snapshot_id']}" in label
            if item["snapshot_id"] is not None
            else "snapshot not stated" in label
        )
        assert f"as_of {item['as_of_jst']}" in label
        # Bagian "sebut": nilainya, atau kalimat yang mengaku nilainya tidak ada.
        assert ("value spoken: " in label) or ("value: " in label)
        # Anggaran `aria-label` cukup untuk data hari ini: kalimatnya utuh sampai klausa sumber.
        assert len(label) <= ui_screens.ARIA_LABEL_LIMIT


def test_an_unspoken_row_is_announced_as_not_spoken_never_as_zero(client: TestClient) -> None:
    """ADR-013 di telinga, bukan hanya di mata: kosong tidak boleh terdengar seperti nol."""
    payload = card_payload(client)
    payload["items"] = [
        {
            "item_id": "06_liquidity",
            "veto_rank": 6,
            "status": "unspoken",
            "value": None,
            "tool": "analyze_token",
            "field": "data.liquidity.depth_usd",
            "snapshot_id": 2166,
            "as_of_jst": "2026-09-04 23:42 JST",
        }
    ]
    (row,) = aria_rows(render_card(payload, BANNER, render_clock()))
    label = aria_label_of(row)
    assert f"value: {UNSPOKEN_MARK}" in label
    assert "value spoken" not in label  # tidak ada nilai yang disebut untuk baris ini
    assert "value: 0" not in label and "value: null" not in label and "value: -" not in label
    # Sumbernya tetap disebut: yang tidak ada adalah NILAInya, bukan tempat kami mencarinya.
    assert "source analyze_token.data.liquidity.depth_usd, snapshot 2166" in label


def test_a_null_value_on_a_spoken_row_is_still_never_announced_as_zero(client: TestClient) -> None:
    payload = card_payload(client)
    payload["items"] = [{"item_id": "07_flow", "veto_rank": 7, "status": "green", "value": None}]
    (row,) = aria_rows(render_card(payload, BANNER, render_clock()))
    label = aria_label_of(row)
    assert ui_screens.ARIA_NULL in label and "that is not zero" in label
    assert ui_screens.ARIA_NO_SOURCE in label  # tool/field memang tidak ada di baris ini


def test_every_watchlist_row_is_announced_with_its_blocker_and_where_it_came_from(
    client: TestClient,
) -> None:
    payload = client.get("/api/watchlist").json()
    rows = aria_rows(render_watchlist(payload, BANNER, render_clock()))
    assert len(rows) == len(payload["symbols"]) >= 1
    for entry, row in zip(payload["symbols"], rows, strict=True):
        label = aria_label_of(row)
        assert label.startswith(f"symbol {entry['symbol']}; decision {entry['decision']}; ")
        assert f"held by {entry['n_blocking']} of {entry['n_items']} roll-call rows" in label
        assert f"{entry['n_unspoken']} rows unspoken" in label
        assert f"bundle as_of {entry['as_of_jst']}" in label
        blocker = entry["top_blocker"]
        if blocker:
            assert f"heaviest blocker {blocker['item_id']}" in label
            assert f"source {blocker['tool']}.{blocker['field']}, as_of {blocker['as_of_jst']}" in label
            # `top_blocker` tidak membawa `snapshot_id`; layar TIDAK boleh mengaku bukti itu
            # tak bernomor hanya karena ringkasan barisnya tidak memuat nomornya.
            assert "snapshot not stated" not in label
        else:
            assert "zero rows hold this decision back" in label


def test_the_symbol_link_says_what_enter_will_open(client: TestClient) -> None:
    payload = client.get("/api/watchlist").json()
    html = render_watchlist(payload, BANNER, render_clock())
    entry = payload["symbols"][0]
    assert (
        f'aria-label="open the roll-call card for {entry["symbol"]}; decision {entry["decision"]}; '
        f'card number {entry["decision_id"]}"' in html
    )


def test_the_replay_link_says_what_it_replays_not_just_an_arrow(client: TestClient) -> None:
    """Tautannya berisi `&#8635;`; tanpa nama, pembaca layar hanya mengumumkan panah itu."""
    payload = card_payload(client)
    html = render_card(payload, BANNER, render_clock())
    assert (
        f'aria-label="replay decision number {payload["decision_id"]}; '
        "reloads this card and recomputes the verdict from the same frozen snapshots"
        '">replay now' in html
    )


def test_escape_has_nothing_to_close_because_no_screen_traps_focus(client: TestClient) -> None:
    """Esc dijawab dengan MENGHAPUS yang harus ditutup, bukan dengan JS (yang dilarang CSP).

    Tidak ada dialog, tidak ada blok yang harus dibuka, tidak ada `autofocus`, dan tidak ada
    `tabindex` — jadi tidak ada keadaan yang hanya bisa dibatalkan, dan tidak ada perangkap
    fokus. Klaim itu tercetak di kaki setiap layar, jadi ia bisa dibantah oleh pembaca.
    """
    for path in ("/ui", "/ui/decision/1", "/ui/decision/999999"):
        text = client.get(path).text.lower()
        for forbidden in ("<dialog", "<details", "<summary", 'role="dialog"', "autofocus", "tabindex"):
            assert forbidden not in text
        assert ui_screens.KEYBOARD_NOTE in unescape(client.get(path).text)
    css = client.get("/ui/tenko.css").text
    assert "position: fixed" not in css and "position:fixed" not in css


def test_every_skip_link_points_at_an_id_that_exists_on_the_page(client: TestClient) -> None:
    """Tautan lompat yang menunjuk id yang tidak ada = tombol yang berbohong kepada keyboard."""
    for path in ("/ui", "/ui/decision/1"):
        text = client.get(path).text
        targets = re.findall(r'href="#([\w-]+)"', text)
        assert targets, f"{path} tidak menawarkan satu pun tautan lompat"
        for target in targets:
            assert f'id="{target}"' in text or f"id='{target}'" in text
        assert 'id="isi"' in text  # landmark <main> yang sama di setiap layar
    assert "#watchlist" in client.get("/ui").text
    assert "#panggilan" in client.get("/ui/decision/1").text


def contrast_ratio(front: str, back: str) -> float:
    """Rasio kontras WCAG 2.1 dari dua warna sRGB `#rrggbb` — rumus, bukan taksiran.

    Dihitung di sini karena ADR-001 melarang toolchain npm: alat audit a11y arus utama semuanya
    Node. Rumusnya standar (WCAG 2.1 SC 1.4.3), dan yang dijaga tes ini adalah ANGKAnya.
    """

    def channel(value: int) -> float:
        ratio = value / 255
        return ratio / 12.92 if ratio <= 0.04045 else ((ratio + 0.055) / 1.055) ** 2.4

    def luminance(colour: str) -> float:
        red, green, blue = (int(colour[index : index + 2], 16) for index in (1, 3, 5))
        return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)

    high, low = sorted((luminance(front), luminance(back)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def composite(front: str, back: str, alpha: float) -> str:
    """`front` dengan alpha `alpha` DI ATAS `back`, sebagai `#rrggbb` — kompositing sRGB biasa.

    Dipakai untuk satu hal saja: menghitung latar TERBURUK yang mungkin muncul di bawah scrim
    gambar. Piksel paling terang yang bisa dimiliki sebuah gambar adalah putih, jadi latar
    terburuk di bawah scrim beralpha `a` adalah putih yang dikomposit di bawahnya.
    """
    parts = []
    for index in (1, 3, 5):
        top = int(front[index : index + 2], 16)
        bottom = int(back[index : index + 2], 16)
        parts.append(round(alpha * top + (1 - alpha) * bottom))
    return "#{:02x}{:02x}{:02x}".format(*parts)


def test_every_colour_pair_in_the_served_stylesheet_is_measured_not_claimed(client: TestClient) -> None:
    """Kontras ≥4.5:1 (teks) dan ≥3:1 (garis/cincin fokus), dari CSS yang BENAR-BENAR dilayani.

    ADR-027 mengganti seluruh paletnya (terang → gelap berlapis) dan MENCABUT kunci hex ADR-023/
    024; yang TIDAK berubah adalah tuntutan ini, dan ia sekarang menggigit di tiga tempat, bukan
    satu:

    1. tiap warna teks di atas KEEMPAT permukaan yang benar-benar dipakai (`--ink` dasar,
       `--panel` kartu/tabel, `--row` baris genap, `--card` chip/kepala kolom/baris tertunjuk);
    2. `--line` (batas komponen, SC 1.4.11) di atas keempatnya, dan warna banner di atas
       latarnya sendiri;
    3. **latar TERBURUK yang bisa muncul di bawah gambar.** Ini yang baru: ADR-027 memasang
       gambar sebagai latar `html`, jadi ada satu tempat di mana warna di bawah teks tidak
       ditentukan palet melainkan piksel. Yang dipatok karena itu bukan "gambarnya kebetulan
       gelap" melainkan scrim-nya: alpha minimalnya `.88`, dan bahkan piksel PUTIH di bawahnya
       menjadi #232527 — di mana keenam warna teks tetap lolos 4.5:1 (terketat `--nogo`,
       5.64:1). Ganti gambarnya dengan yang paling terang sekalipun dan angka ini tidak
       bergerak.

    Dan tetap: tiap hex di berkas ini harus habis oleh pasangan di atas. Warna kelima belas
    tidak bisa menyelinap masuk tanpa ikut diukur.
    """
    css = client.get("/ui/tenko.css").text
    palette = dict(re.findall(r"--([a-z]+): (#[0-9a-f]{6})", css))
    assert palette.keys() == set(PALETTE_ROLES), sorted(palette)
    roles = PALETTE_ROLES.items()
    surfaces = [palette[name] for name, role in roles if role == "surface" and name != "bannerbg"]
    texts = [name for name, role in roles if role == "text" and name != "bannerfg"]

    text_pairs = [(palette[name], background) for name in texts for background in surfaces]
    text_pairs += [(palette["bannerfg"], palette["bannerbg"])]
    # Batas gambar: scrim `--ink` paling TIPIS yang ada di stylesheet ini, dikomposit di atas
    # piksel paling terang yang mungkin dimiliki sebuah gambar (putih).
    scrim = composite(palette["ink"], "#ffffff", MIN_SCRIM_ALPHA)
    text_pairs += [(palette[name], scrim) for name in texts]
    non_text_pairs = [(palette["line"], background) for background in surfaces]
    non_text_pairs += [(palette["nogo"], palette["bannerbg"]), (palette["nogo"], palette["ink"])]

    for front, back in text_pairs:
        assert contrast_ratio(front, back) >= 4.5, f"teks {front} di atas {back}"
    for front, back in non_text_pairs:
        assert contrast_ratio(front, back) >= 3.0, f"non-teks {front} di atas {back}"

    # Angka yang ditulis `ui/README.md` adalah angka yang sama, bukan angka lama. Keempatnya
    # adalah yang TERKETAT di kategorinya masing-masing.
    assert round(contrast_ratio(palette["nogo"], palette["card"]), 2) == 5.88  # teks paling ketat
    assert round(contrast_ratio(palette["line"], palette["card"]), 2) == 3.06  # garis paling ketat
    assert round(contrast_ratio(palette["warn"], palette["ink"]), 2) == 10.47  # cincin fokus
    assert round(contrast_ratio(palette["nogo"], scrim), 2) == 5.64  # paling ketat di atas gambar

    measured = {colour for pair in text_pairs + non_text_pairs for colour in pair}
    decor = {palette[name] for name, role in PALETTE_ROLES.items() if role == "decor"}
    assert set(re.findall(r"#[0-9a-f]{6}", css)) == measured - {scrim} | decor


def test_the_ui_readme_carries_the_a11y_checklist_and_admits_the_missing_recording() -> None:
    """AC 4.6: checklist ada, dan rekamannya TIDAK diklaim ada selama berkasnya tidak ada."""
    readme = (ROOT / "ui" / "README.md").read_text(encoding="utf-8")
    assert BANNER in readme
    for claim in (
        "Tab / Shift+Tab",
        "Esc tidak punya apa pun untuk ditutup",
        "Fokus terlihat",
        "`aria-label` memuat nilai + sumber",
        "Isi yang berubah diumumkan",  # ADR-020: konten dinamis wajib `aria-live`
        "Nilai yang tidak ada adalah KATA, bukan tanda hubung",
        "Tanpa hover-only",
        # Angka palet ADR-027, bukan palet jalur JR ADR-023/024. Ketiganya adalah pasangan
        # TERSEMPIT di masing-masing kategori (teks, garis, teks di atas gambar), jadi tes ini
        # merah begitu klaim README tertinggal di belakang palet yang benar-benar dilayani.
        "5.88:1",
        "3.06:1",
        "5.64:1",
    ):
        assert claim in readme, claim
    assert "- [ ] **Rekaman 30 detik**" in readme  # belum dicentang, dan itu memang jujur
    assert "Rekaman 30 detik BELUM DIBUAT" in readme
    assert "Skrip rekaman 30 detik" in readme
    for key in ("`Tab` ×2", "`Enter`", "`Esc`", "`Shift+Tab` lalu `Tab`"):
        assert key in readme, key


# --- penyegaran tampilan: yang berubah bentuk, dan yang TIDAK boleh ikut berubah ------------
#: Palet yang diukur TASKS 4.6, dipatok sebagai LITERAL. Tes pasangan warna di atas menjamin
#: setiap warna di CSS ikut terukur; yang dijamin di sini adalah warnanya masih warna yang SAMA,
#: sehingga penyegaran tampilan berikutnya tidak bisa menggeser satu nilai lalu "mengukur ulang"
#: pasangannya menjadi angka lain yang kebetulan juga lulus. `--line` khususnya: ia baru saja
#: dinaikkan dari #2c3442 (1.52:1, GAGAL 1.4.11) dan tidak boleh diredupkan kembali demi rasa.
#: URL yang BOLEH muncul di bundel yang dilayani. Tidak satu pun pernah diambil saat runtime:
#: dua namespace XML (nama, bukan alamat) dan empat URL lisensi/dokumen di dalam komentar —
#: yang justru WAJIB utuh, karena lisensi GSAP menuntut header copyright-nya tidak dibuang dan
#: MIT menuntut notis Lenis ikut terdistribusi (DISCLOSURE.md).
BUNDLE_URL_ALLOWLIST = {
    "http://www.w3.org/1999/xhtml",
    "http://www.w3.org/2000/svg",
    "https://github.com/darkroomengineering/lenis/blob/main/LICENSE",
    "https://gsap.com",
    "https://gsap.com/standard-license",
    "https://gsap.com/forums/topic/20368-possible-gsap-bug-switching-classnames-in-chrome/",
}

#: PERAN tiap warna palet — dan peran itulah yang menentukan AMBANGnya. Ia bukan komentar:
#: `test_every_colour_pair_in_the_served_stylesheet_is_measured_not_claimed` membacanya untuk
#: merakit pasangan yang diukur, jadi warna yang perannya salah akan diukur dengan ambang yang
#: salah dan itu terlihat sebagai tes merah, bukan sebagai dokumen yang usang.
#:
#: `decor` adalah satu-satunya peran TANPA ambang, dan karena itu ia dibayar dengan larangan:
#: warna dekor tidak boleh pernah menjadi `color:` apa pun (dipatok di bawah). Ia hanya boleh
#: menjadi pemisah rambut yang kehilangannya tidak menghilangkan satu pun informasi.
PALETTE_ROLES = {
    "ink": "surface",
    "panel": "surface",
    "row": "surface",
    "card": "surface",
    "bannerbg": "surface",
    "fg": "text",
    "dim": "text",
    "go": "text",
    "nogo": "text",
    "warn": "text",
    "link": "text",
    "bannerfg": "text",
    "line": "line",
    "hair": "decor",
}

#: Alpha TERTIPIS yang boleh dimiliki scrim di atas gambar latar. Angka ini, bukan gambarnya,
#: yang menjaga kontras: piksel paling terang yang mungkin ada di sebuah gambar adalah putih,
#: dan putih di bawah scrim setebal ini masih menyisakan 5.64:1 untuk warna teks terketat.
MIN_SCRIM_ALPHA = 0.88

#: Palet ADR-027, dipatok sebagai LITERAL. Tes pasangan di atas menjamin tiap warna ikut
#: terukur; yang dijamin di sini adalah warnanya masih warna yang SAMA — sehingga penyegaran
#: tampilan berikutnya tidak bisa menggeser satu nilai lalu "mengukur ulang" pasangannya
#: menjadi angka lain yang kebetulan juga lulus.
MEASURED_PALETTE = {
    "ink": "#05070a",
    "panel": "#0e131a",
    "row": "#141b23",
    "card": "#1a222c",
    "fg": "#dfe7e0",
    "dim": "#9daba4",
    "go": "#7ddc9a",
    "nogo": "#ff6f61",
    "warn": "#e0b552",
    "link": "#6fd2e8",
    "line": "#5d6f78",
    "hair": "#232c35",
    "bannerbg": "#3a0d0b",
    "bannerfg": "#ffd9d4",
}


def test_the_palette_is_measured_from_the_stylesheet_that_ships(client: TestClient) -> None:
    """ADR-027 MENGGANTIKAN ADR-023/024. Yang dicabut adalah KUNCInya, bukan jaminannya.

    Tes ini menggantikan `test_the_palette_is_the_measured_jr_line_palette_and_nothing_else`,
    yang menuntut himpunan hex stylesheet PERSIS sama dengan empat warna jalur JR + latar
    terangnya. Tuntutan itu ternyata melarang lebih dari yang dimaksudnya: nol permukaan
    antara, nol tint kartu, nol garis rambut — jadi nol kedalaman, dan ketiga layar terbaca
    seperti dokumen yang dicetak. Pemilik repo menilai hasilnya demikian dengan kata-kata itu,
    dan ADR-027 mencabut kuncinya.

    Yang TIDAK ikut dicabut, dan yang dipaku di sini:

    1. **Angka kontrasnya DIUKUR dari stylesheet yang benar-benar dilayani**, bukan disalin dari
       komentar — itu pekerjaan tes pasangan di atas, dan ia sekarang mengukur lebih banyak
       (termasuk latar terburuk di bawah gambar).
    2. **Paletnya tetap dipatok sebagai literal.** Sebuah warna boleh berpindah, tetapi hanya
       lewat commit yang mengubah angka di sini — bukan lewat "sedikit lebih enak dilihat".
    3. **Warna dekor tidak pernah membawa teks.** Satu-satunya warna tanpa ambang kontras
       adalah `--hair`, dan ia dibayar dengan larangan yang diperiksa di sini: ia tidak boleh
       muncul sebagai `color:` apa pun. Tanpa butir ini, "dekor" menjadi pintu belakang untuk
       teks abu-abu yang tidak terbaca.
    4. **Transparansi hanya boleh MENGGELAPKAN.** Tiap `rgba()` di berkas ini wajib hitam atau
       warna dasar `--ink`; keduanya hanya menggerakkan latar ke arah gelap, dan karena seluruh
       teks halaman ini lebih terang daripada seluruh permukaannya, kontras hanya bisa NAIK.
       Sebuah `rgba()` berwarna lain bisa menerangi latar di bawah teks tanpa satu hex baru pun
       muncul di berkas — persis lubang yang membuat pemeriksaan "himpunan hex" saja tidak cukup.
    """
    css = client.get("/ui/tenko.css").text
    palette = dict(re.findall(r"--([a-z]+): (#[0-9a-f]{6})", css))
    assert palette == MEASURED_PALETTE
    assert "#eef2f7" not in css, "latar terang ADR-023 kembali tanpa ADR baru"

    # (3) warna dekor tidak pernah menjadi teks.
    decor = {palette[name] for name, role in PALETTE_ROLES.items() if role == "decor"}
    decor_tokens = {f"var(--{name})" for name, role in PALETTE_ROLES.items() if role == "decor"}
    for declaration in re.findall(r"[a-z-]+\s*:[^;{}]+", css):
        prop, _, value = declaration.partition(":")
        if prop.strip() != "color":
            continue
        assert not (decor | decor_tokens) & set(re.findall(r"var\(--[a-z]+\)|#[0-9a-f]{6}", value)), (
            f"warna dekor (tanpa ambang kontras) dipakai sebagai teks: {declaration.strip()}"
        )

    # (4) transparansi hanya menggelapkan.
    ink = tuple(int(palette["ink"][index : index + 2], 16) for index in (1, 3, 5))
    channels = [
        tuple(int(part) for part in match)
        for match in re.findall(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,", css)
    ]
    assert channels, "prasyarat: stylesheet ini memang memakai rgba (bayangan + scrim gambar)"
    for triple in channels:
        assert triple in (ink, (0, 0, 0)), f"rgba yang bukan penggelap: rgba{triple}"

    # ...dan scrim di atas gambar tidak boleh lebih tipis daripada yang diukur.
    for layer in re.findall(r"background-image:[^;]+", css):
        if "url(" not in layer:
            continue
        alphas = [float(value) for value in re.findall(r"rgba\([^)]*,\s*(\.?\d+(?:\.\d+)?)\s*\)", layer)]
        assert alphas, "gambar latar tanpa scrim sama sekali: kontras teks jadi milik pikselnya"
        assert min(alphas) >= MIN_SCRIM_ALPHA, f"scrim terlalu tipis: {min(alphas)}"


def test_no_sentence_on_a_judged_screen_sits_on_top_of_an_image(client: TestClient) -> None:
    """Klaim yang membuat pengukuran palet berlaku: nol teks di atas piksel gambar.

    Tiap angka kontras di atas mengandaikan latar teks adalah warna dari palet. Andaian itu
    runtuh di satu tempat yang mudah dibuat dan sulit dilihat: teks di atas gambar. ADR-027
    karena itu menaruh tiap gambar di KOTAKNYA SENDIRI — kolom hero, pita `.band`, kolom
    `.proofs-art` — dan tes ini memeriksa bahwa kotak-kotak itu memang tidak berisi apa pun
    selain `<img>`, pada HTML yang benar-benar dilayani.

    Satu-satunya gambar yang MEMANG berada di bawah teks adalah latar `html`, dan ia dibayar
    dengan scrim ≥`MIN_SCRIM_ALPHA` yang diukur terhadap piksel putih di tes di atas.
    """
    html = client.get("/").text
    for holder in ("hero-art", "band-art", "proofs-art"):
        blocks = re.findall(rf'<div class="{holder}">(.*?)</div>', html, re.S)
        assert blocks, f"{holder} tidak ada di landing"
        for inner in blocks:
            assert re.fullmatch(r"<img [^>]+>", inner.strip()), (
                f"{holder} memuat lebih dari gambar: {inner[:80]}"
            )
    # Tiap gambar itu benar-benar dilayani, membawa `alt`, dan membawa ukuran intrinsiknya —
    # `alt=""` disengaja (pemandangan, bukan bukti) dan ukuran mencegah halaman melompat.
    images = re.findall(r"<img ([^>]+)>", html)
    assert len(images) == 3, images
    for attrs in images:
        source = re.search(r'src="([^"]+)"', attrs)
        assert source and source.group(1).startswith("/ui/img/"), attrs
        assert client.get(source.group(1)).status_code == 200, attrs
        assert 'alt=""' in attrs, f"gambar hiasan tanpa alt kosong: {attrs}"
        assert "width=" in attrs and "height=" in attrs, f"gambar tanpa ukuran intrinsik: {attrs}"
    # Dan kalimat yang mengatakan gambar-gambar itu BUKAN bukti ikut tercetak.
    assert ui_screens.ART_NOTE in unescape(html)


def test_the_card_table_has_one_header_for_every_column_it_renders(client: TestClient) -> None:
    """Judul kolom yang bergeser adalah tabel yang salah menyebut sumber tiap nilai.

    `CARD_COLUMNS` dulu berisi TUJUH judul untuk SEMBILAN sel: "sumber: tool.field · snapshot ·
    as_of" berdiri di atas tiga kolom sekaligus, sehingga `narasi model` jatuh di atas kolom
    `snapshot` dan dua kolom terakhir tidak berjudul sama sekali. Itu bukan sekadar cacat
    tampilan: `<th scope="col">` adalah nama yang dibacakan pembaca layar untuk tiap sel.
    """
    payload = card_payload(client)
    html = render_card(payload, BANNER, render_clock())
    table = html.split("<table class='card'>")[1].split("</table>")[0]
    headers = re.findall(r'<th scope="col">', table)
    assert len(headers) == len(ui_screens.CARD_COLUMNS) == 9
    rows = aria_rows(table)
    assert rows, "kartu fixture tidak merender satu baris pun"
    for row in rows:
        assert len(re.findall(r"<t[dh][ >]", row)) == len(headers)
    readable = strip_tags(table)
    for name in ("source: tool.field", "snapshot", "evidence as_of"):
        assert name in readable, name


def test_the_watchlist_table_has_one_header_for_every_column_it_renders(client: TestClient) -> None:
    payload = client.get("/api/watchlist").json()
    html = render_watchlist(payload, BANNER, render_clock())
    table = html.split("<table id='watchlist'>")[1].split("</table>")[0]
    headers = re.findall(r'<th scope="col">', table)
    assert len(headers) == 9
    for row in aria_rows(table):
        assert len(re.findall(r"<t[dh][ >]", row)) == len(headers)


def test_screen_b_puts_the_ten_rows_above_the_hashes_not_below_them(client: TestClient) -> None:
    """Uji 30 detik: yang di bawah kalimat jawaban adalah BUKTInya, bukan delapan baris hash.

    Daftar `dl.meta` (delapan baris, sebagian berparagraf penjelas) sebelumnya berdiri di antara
    kalimat jawaban dan tabel tunjuk→sebut, mendorong tabel itu ke bawah lipatan 1280x720.
    Urutannya dibalik; tidak ada satu pun isi yang dihapus, dan tes ini menahannya tetap begitu.
    """
    html = render_card(card_payload(client), BANNER, render_clock())
    assert html.index('<h2 id="panggilan">') < html.index('<dl class="meta">')
    assert html.index("<h2>Hashes, clocks, and replay</h2>") < html.index('<dl class="meta">')
    # Tidak ada yang hilang saat dipindahkan: hash, vonis replay, dan tombolnya tetap di kartu.
    readable = strip_tags(html)
    for kept in ("decision_hash", "computed_hash", "params_hash", "replay now"):
        assert kept in readable, kept


def test_the_banner_stays_in_frame_without_becoming_an_overlay(client: TestClient) -> None:
    """Banner SIMULATED melekat (`sticky`) supaya ia ada di SETIAP frame video, bukan hanya di
    frame pertama — tetapi `sticky` tetap mengalir di dalam dokumen, jadi ia tidak menjadi
    overlay, tidak menutupi apa pun, dan tidak bisa menjebak fokus (`position: fixed` tetap nol).
    """
    css = client.get("/ui/tenko.css").text
    assert "position: sticky" in css
    assert "position: fixed" not in css and "position:fixed" not in css
    assert "scroll-margin-top" in css  # sasaran tautan lompat tidak mendarat di balik banner
    for path in ("/ui", "/ui/decision/1", "/ui/decision/999999"):
        text = client.get(path).text
        assert '<p class="banner" role="note">' in text
        assert BANNER in unescape(text)


def test_the_only_icon_on_the_screens_is_embedded_and_says_nothing_by_itself(client: TestClient) -> None:
    """Ikon banner ditulis sendiri dan TERTANAM: nol berkas, nol permintaan jaringan, nol CDN.

    Ia juga `aria-hidden`: kalimat di sebelahnya membawa seluruh maknanya, jadi ikon ini hanya
    menambah satu kanal BENTUK dan tidak pernah menjadi satu-satunya pembawa informasi.
    """
    for path in ("/ui", "/ui/decision/1", "/ui/decision/999999"):
        text = client.get(path).text
        (icon,) = re.findall(r"<svg .*?</svg>", text, re.S)
        assert 'aria-hidden="true"' in icon and 'focusable="false"' in icon
        for forbidden in ("href", "xlink", "url(", "data:", "http", "<image", "<use"):
            assert forbidden not in icon, forbidden
        assert text.count("<svg") == 1  # satu ikon, bukan set ikon yang diam-diam tumbuh


def test_hiding_a_control_actually_hides_it(client: TestClient) -> None:
    """Bug yang terkirim tanpa satu tes pun merah, dan hanya terlihat pada gambar.

    `main.ts` menyembunyikan bar kontrol di luar Layar A dengan `controlsNode.hidden = true`.
    Itu benar — tetapi `[hidden]` adalah aturan UA ber-spesifisitas (0,0,0), sedangkan
    `.controls { display: flex }` adalah (0,1,0) dan MENANG. Akibatnya kartu memamerkan kotak
    saring, pengurut, dan sakelar auto-refresh sambil kalimatnya sendiri berbunyi "This screen
    does NOT auto-refresh and carries no filter, sort, or refresh switch" — halaman yang
    membantah dirinya sendiri di depan juri yang menilai "Easy to read 25".

    Tidak ada browser di jalur tes, jadi yang berdiri di tempat browser adalah tes ini: ia
    menuntut penawarnya ADA, dan menuntut konflik yang membuatnya perlu tetap NYATA — sebuah
    penawar untuk konflik yang sudah hilang adalah baris mati yang akan dihapus orang berikutnya.
    """
    css = client.get("/ui/tenko.css").text
    squashed = css.replace(" ", "").replace("\n", "")
    assert "[hidden]{display:none!important" in squashed, (
        "tidak ada aturan yang membuat `[hidden]` menang atas `display` milik kelas; "
        "elemen yang disembunyikan JS akan tetap terlihat"
    )
    # Konfliknya nyata: ada kelas yang memasang `display` pada elemen yang memang disembunyikan.
    assert ".controls{display:flex" in squashed, (
        "`.controls` tidak lagi memasang display — periksa apakah penawar `[hidden]` di atas "
        "masih dibutuhkan sebelum menghapusnya"
    )
    # Dan klien memang menyembunyikannya di luar Layar A.
    main = main_source()
    assert 'controlsNode.hidden = state.screen !== "watchlist"' in main


def test_the_stylesheet_loads_nothing_from_anywhere_including_itself(client: TestClient) -> None:
    """Nol aset dari LUAR aplikasi ini: nol CDN, nol URI tertanam, nol gambar.

    Ini bukan selera melainkan syarat: domain aset mana pun harus lebih dulu `approved: true` di
    `sources.yaml` dengan barisnya sendiri di `DISCLOSURE.md`, dan demo spec §7 harus jalan
    tanpa jaringan.

    ADR-022 mengubah bentuk tes ini, bukan jaminannya. Dulu `url(` dan `@font-face` dilarang
    HABIS, yang kebetulan cukup selama tidak ada aset sama sekali. Sekarang font di-vendor dan
    (ADR-027) satu gambar dipakai sebagai latar layar, jadi yang dilarang bukan lagi "memuat"
    melainkan "memuat dari tempat lain": tiap `url()` di stylesheet WAJIB berupa path se-origin
    di bawah `/ui/fonts/` atau `/ui/img/` — DUA daftar TERTUTUP di `ui/screens.py`, bukan dua
    folder yang bisa diisi apa saja — dan tiap berkas yang ditunjuknya WAJIB benar-benar
    dilayani dengan tipe yang benar. Larangan yang dilonggarkan tanpa diganti penggantinya
    adalah larangan yang hilang, jadi keduanya diperiksa di sini.
    """
    css = client.get("/ui/tenko.css").text
    for forbidden in ("data:", "@import", "http", "//"):
        assert forbidden not in css, forbidden

    urls = re.findall(r"url\(([^)]*)\)", css)
    assert urls, "prasyarat: stylesheet memang memuat font yang di-vendor"
    kinds = {
        "/ui/fonts/": ("font/woff2", b"wOF2", ui_screens.FONT_NAMES),
        "/ui/img/": ("image/webp", b"RIFF", ui_screens.IMAGE_NAMES),
    }
    for raw in urls:
        target = raw.strip().strip("\"'")
        prefix = next((name for name in kinds if target.startswith(name)), None)
        assert prefix, f"aset dari luar /ui/fonts/ dan /ui/img/: {target}"
        assert ".." not in target, target
        kind, magic, closed_list = kinds[prefix]
        assert target.removeprefix(prefix) in closed_list, f"{target}: di luar daftar tertutup"
        # ...dan ia BENAR-BENAR ada. `url()` yang menunjuk berkas hilang adalah 404 diam-diam
        # yang hanya terlihat di browser, dan tidak ada browser di jalur tes.
        served = client.get(target)
        assert served.status_code == 200, f"{target} -> {served.status_code}"
        assert served.headers["content-type"] == kind, target
        assert served.content[:4] == magic, f"{target} bukan {kind}"

    # Tiap `@font-face` menyebut `font-display`, supaya teks terbaca dengan font sistem SEBELUM
    # Plex tiba: layar tidak pernah kosong menunggu aset (alasan yang sama dengan ADR-020 6).
    assert css.count("@font-face") == css.count("font-display"), "ada face tanpa font-display"
    # Tumpukan sistem tetap berdiri di belakangnya — fallback yang sungguh dipakai saat `swap`.
    assert "system-ui" in css and "ui-monospace" in css


def test_every_vendored_font_is_served_and_carries_its_licence(client: TestClient) -> None:
    """OFL 1.1 menuntut lisensinya ikut terdistribusi bersama fontnya (ADR-022 butir 4).

    Dua hal yang mudah hilang tanpa tes, dan keduanya berakibat kepatuhan, bukan tampilan:
    berkas lisensi yang tidak ikut terkirim, dan daftar font yang tumbuh diam-diam sehingga
    `DISCLOSURE.md` berhenti menggambarkan apa yang benar-benar dilayani.
    """
    licence = client.get("/ui/fonts/LICENSE.txt")
    assert licence.status_code == 200
    assert "SIL Open Font License, Version 1.1" in licence.text
    assert 'Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"' in licence.text

    faces = [name for name in ui_screens.FONT_NAMES if name.endswith(".woff2")]
    assert sorted(faces) == [
        "IBMPlexMono-Regular.woff2",
        "IBMPlexMono-SemiBold.woff2",
        "IBMPlexSans-Regular.woff2",
        "IBMPlexSans-SemiBold.woff2",
        # Tujuh face halaman `/kage` (ADR-026). Mereka ada DI SINI, bukan di daftar terpisah,
        # supaya daftar ini tetap "apa yang benar-benar dilayani" — dan supaya face kedelapan
        # tidak bisa masuk tanpa satu baris pun di `DISCLOSURE.md`.
        "NotoJP-400.woff2",
        "Onest-300.woff2",
        "Onest-400.woff2",
        "Onest-500.woff2",
        "Onest-700.woff2",
        "Wordmark-500.woff2",
        "Wordmark-600.woff2",
    ], "daftar face berubah — perbarui DISCLOSURE.md pada commit yang sama"

    # Nama yang tidak terdaftar tidak pernah menyentuh disk: 404, termasuk yang mencoba keluar.
    for bogus in ("../app.py", "../../ui/app.py", "nope.woff2"):
        assert client.get(f"/ui/fonts/{bogus}").status_code == 404, bogus

    # Dan `DISCLOSURE.md` menyebut apa yang benar-benar dilayani, bukan yang pernah dilayani.
    disclosure = (ROOT / "DISCLOSURE.md").read_text(encoding="utf-8")
    assert "IBM Plex" in disclosure and "OFL" in disclosure
    assert "v6.4.2" in disclosure, "versi font yang di-vendor tidak tercatat"
    # `ui/static/fonts/LICENSE.txt` menutupi IBM Plex SAJA. Ketujuh face lainnya datang lewat
    # proyek teman pemilik repo, dan lisensinya BELUM diverifikasi terhadap sumber hulu — jadi
    # yang dipatok di sini adalah bahwa kewajiban itu tertulis TERBUKA, bukan terlanjur ditulis
    # selesai. Tes ini akan menjadi merah kalau seseorang menghapus pengakuannya tanpa
    # menyelesaikan kewajibannya.
    for face_family in ("Onest", "NotoJP", "Wordmark"):
        assert face_family in disclosure, f"{face_family} dilayani tetapi tidak disebut"
    assert "not yet verified" in disclosure or "NOT YET VERIFIED" in disclosure


# --- laporan juri fase 5: kolom "daya veto" & angka `make grade` ---------------------------
def test_both_screens_admit_the_veto_rank_column_is_a_claim_not_a_measurement(
    client: TestClient,
) -> None:
    """Peringkat daya veto adalah konstanta spec §4, dan kedua layar HARUS mengatakannya.

    Juri fase 5 memotong nilai di dua kategori (T2 importance, T1 cause-effect) karena kolom itu
    dipajang otoritatif: angka 1..10 di samping tiap penahan terbaca sebagai temuan, padahal ia
    `preflight.params.ITEM_ORDER` — desain, bukan ukuran. Yang membuatnya bukan sekadar
    "belum diukur" melainkan **dibantah** adalah alat ukur repo ini sendiri: `preflight/veto_table.py`
    mencatat item peringkat 1 nol kali merah sementara item peringkat 6 merah puluhan kali.

    Tes ini menjaga tiga hal sekaligus: kalimatnya ada di KEDUA layar, ia menyebut sumber
    urutannya, ketidakvalidannya, DAN alat yang membantahnya berikut contohnya — dan judul
    kolomnya sendiri ikut mengaku, supaya juri yang hanya melihat tabel tidak perlu turun ke
    catatan untuk tahu.
    """
    sentence = ui_app.VETO_RANK_UNVALIDATED_SENTENCE
    for name, expected in (
        ("asal urutan", "spec §4"),
        ("nama konstantanya", "ITEM_ORDER"),
        ("the validation status", "NOT validated"),
        ("alat yang membantah", "preflight/veto_table.py"),
        ("contoh peringkat 1", "01_freshness"),
        ("contoh peringkat 6", "06_confluence"),
    ):
        assert expected in sentence, name
    # Catatan layar dipotong `WARNING_LIMIT`; kalimat kejujuran yang ikut terpotong adalah
    # kegagalan yang sama dengan yang sedang diperbaikinya.
    assert len(sentence) <= ui_screens.WARNING_LIMIT

    watchlist = render_watchlist(client.get("/api/watchlist").json(), BANNER, render_clock())
    card = render_card(card_payload(client), BANNER, render_clock())
    for screen in (watchlist, card):
        readable = strip_tags(screen)
        assert sentence in readable
        assert "not validated" in ui_screens.VETO_RANK_COLUMN
        assert ui_screens.VETO_RANK_COLUMN in readable


def test_the_screens_label_the_veto_order_without_reordering_it(client: TestClient) -> None:
    """Kejujurannya label, bukan urutan: `ITEM_ORDER` tetap milik `preflight/`.

    Layar yang mengarang urutan sendiri akan menampilkan peringkat yang BUKAN peringkat yang
    dipakai agen saat memutuskan — kartu yang tidak lagi menjelaskan keputusannya sendiri.
    """
    payload = card_payload(client)
    for item in payload["items"]:
        assert item["veto_rank"] == ITEM_ORDER.index(item["item_id"]) + 1


def test_screen_a_prints_the_grade_line_and_ui_still_never_imports_grading(
    client: TestClient,
) -> None:
    """Angka paling jujur di repo ditaruh di layar yang paling mungkin dibuka — tanpa impor.

    ADR-003 mengikat: `grading/` di luar jalur keputusan, dan `ui/` termasuk yang diperiksa
    `test_decision_path_never_imports_grading`. Jadi kalimat ini kualitatif dan menyebut
    PERINTAHnya; yang dijaga di sini adalah keduanya tetap benar bersama — ada kalimatnya, ada
    perintahnya, dan nol impor `grading` di seluruh `ui/`.
    """
    sentence = ui_app.GRADE_UNPROVEN_SENTENCE
    assert "NOT yet proven to have saved anything" in sentence
    assert "make grade" in sentence
    assert "zero confidence intervals" in sentence  # kenapa tak ada CI: n efektif di bawah ambang
    assert "ADR-003" in sentence
    assert len(sentence) <= ui_screens.WARNING_LIMIT

    readable = strip_tags(client.get("/ui").text)
    assert sentence in readable

    for path in sorted((ROOT / "ui").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("grading") for alias in node.names), path
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("grading"), path


def test_a_row_label_never_carries_less_than_the_cell_shows(client: TestClient) -> None:
    """Pembaca layar tidak boleh menerima LEBIH SEDIKIT bukti daripada pemakai awas.

    `aria-label` baris kartu dulu memotong `value` pada 200 aksara sementara selnya menampilkan
    1500: nilai `k=v` sepuluh field tampil utuh di mata dan terdengar `...(truncated)` di
    telinga. Di produk yang menjual "nilai ini datang dari sini", itu isi yang hilang.
    """
    assert ui_screens.ARIA_VALUE_LIMIT == VALUE_LIMIT
    payload = card_payload(client)
    long_value = {f"field_{index:02d}": f"value-{index:02d}-{'x' * 40}" for index in range(20)}
    payload["items"] = [
        {
            "item_id": "06_confluence",
            "veto_rank": 6,
            "status": "red",
            "value": long_value,
            "tool": "deep_analysis",
            "field": "data.confluence",
            "snapshot_id": 4242,
            "as_of_jst": "2026-09-06 06:15 JST",
        }
    ]
    (row,) = aria_rows(render_card(payload, BANNER, render_clock()))
    label = unescape(aria_label_of(row))
    cell_text = strip_tags(row)
    assert TRUNCATED_MARK not in label
    for key, value in long_value.items():
        assert f"{key}={value}" in label, key
        assert f"{key}={value}" in cell_text, key
    # Klausa SUMBER berdiri di belakang klausa nilai: anggaran yang terlalu kecil memakannya
    # diam-diam, dan justru klausa itulah "tunjuk"-nya tunjuk→sebut.
    assert "source deep_analysis.data.confluence, snapshot 4242, as_of 2026-09-06 06:15 JST" in label
    assert len(label) <= ui_screens.ARIA_LABEL_LIMIT


def test_english_evidence_is_marked_lang_en_inside_an_indonesian_page(client: TestClient) -> None:
    """Bingkai Indonesia, bukti Inggris — dan penandanya dipasang, bukan diabaikan.

    `<html lang="id">` atas kalimat `reason`/`value`/narasi yang seluruhnya Inggris membuat
    pembaca layar Indonesia melafalkan bagian yang justru membawa bukti dengan aturan fonem yang
    salah. Bahasa BINGKAI tidak diubah di sini: itu keputusan produk, bukan keputusan layar.
    """
    html = render_card(card_payload(client), BANNER, render_clock())
    assert '<html lang="en">' in html
    assert f'<bdi lang="{ui_screens.EVIDENCE_LANG}">' in html
    # `reason` deterministik `preflight/items.py` adalah kalimat Inggris terpenting di kartu.
    reasons = [item["reason"] for item in card_payload(client)["items"] if item.get("reason")]
    assert reasons
    for reason in reasons:
        assert f'<bdi lang="en">{escape(clean_text(reason, REASON_LIMIT), quote=True)}</bdi>' in html
    # Identifier (simbol, hash, tool.field) TIDAK ditandai Inggris: ia bukan kalimat bahasa apa pun.
    assert f"<bdi>{escape(card_payload(client)['symbol'], quote=True)}</bdi>" in html


def test_every_column_header_on_both_screens_carries_a_scope(client: TestClient) -> None:
    """`scope` pada SETIAP `<th>`, dengan kutip yang seragam supaya bisa dihitung sekali jalan.

    Juri fase 5 menghitung `scope="` dan menemukan satu di seluruh Layar B: `scope='col'`
    berkutip tunggal sah sebagai HTML tetapi lolos dari pemeriksaan siapa pun yang mencarinya
    dalam bentuk kanonik. Header tanpa scope yang terbaca = sel tanpa nama di pembaca layar.
    """
    for html in (
        render_watchlist(client.get("/api/watchlist").json(), BANNER, render_clock()),
        render_card(card_payload(client), BANNER, render_clock()),
    ):
        headers = re.findall(r"<th\b[^>]*>", html)
        assert headers
        for header in headers:
            assert 'scope="col"' in header or 'scope="row"' in header, header
        assert "scope='" not in html


# --- ADR-020: Node adalah alat BUILD, dan klien tidak boleh menghitung apa pun ---------------
#: Sumber TypeScript + hasil build-nya. Keduanya dilacak git; yang kedua ikut terkirim.
FRONTEND = ROOT / "frontend"
STATIC = ROOT / "ui" / "static"


def git_tracked(path: Path) -> bool:
    """Apakah git benar-benar melacak berkas ini? Diukur dengan `git ls-files`, bukan ditebak."""
    import subprocess

    result = subprocess.run(  # argumen tetap, tanpa shell
        ["git", "ls-files", "--error-unmatch", str(path.relative_to(ROOT))],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def strip_comments(source: str) -> str:
    """Kode tanpa komentarnya.

    Penjaga di bawah membandingkan APA YANG DIJALANKAN, bukan apa yang dijelaskan: docstring
    `frontend/src/dom.ts` menyebut `innerHTML` justru untuk mengatakan ia dilarang, dan tes yang
    membacanya sebagai pelanggaran akan menghukum dokumentasi yang benar.
    """
    without_block = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.M)


def test_a_clone_without_node_can_run_tenko_because_the_build_output_is_tracked() -> None:
    """ADR-020 butir 2 sebagai SYARAT PENERIMAAN, bukan harapan.

    Mesin juri belum tentu punya Node — ADR-001 lahir persis dari kekhawatiran itu. Yang membuat
    janji "clone bersih tanpa Node tetap bisa `make ui`" bisa dipercaya adalah tiga fakta yang
    diukur di sini, bukan kalimat di dokumen:

    1. kedua artefak build ADA dan tidak kosong;
    2. git benar-benar MELACAK keduanya (`git ls-files`), jadi `git clone` membawanya;
    3. `.gitignore` tidak memuat satu pun pola yang menangkap `ui/static/` — dan `node_modules/`
       justru DIABAIKAN, supaya pohon 49 paket build tidak ikut terkirim.

    Rusakkan salah satunya (hapus `ui/static/app.js`, atau tambahkan `ui/static/` ke
    `.gitignore`) dan tes ini merah — yang memang tugasnya.
    """
    for name in ("app.js", "app.css"):
        artefact = STATIC / name
        assert artefact.is_file(), f"{artefact} hilang: clone tanpa Node tidak akan bisa `make ui`"
        assert artefact.stat().st_size > 1000, f"{artefact} kosong"
        assert git_tracked(artefact), f"{artefact} tidak dilacak git; ia tidak akan ikut clone"
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in ignore  # pohon build tidak ikut terkirim
    for line in ignore.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        assert "static" not in entry, f"`.gitignore` bisa menelan hasil build: {entry!r}"


def test_nothing_on_the_runtime_path_reaches_for_node_or_npm() -> None:
    """`make ui`, `make test`, dan `make demo` tidak boleh menyentuh Node satu kali pun.

    Diukur di dua tempat: (1) tidak ada modul Python yang dikirim (`ui/`, `core/`, `preflight/`,
    `agent/`, `grading/`) menyebut `npm`/`npx`/`node_modules`/`esbuild`; (2) resep `Makefile`
    untuk `ui`, `test`, dan `demo` tidak menyebutnya juga. Hanya target `frontend` yang boleh.
    """
    for package in ("ui", "core", "preflight", "agent", "grading"):
        for source in (ROOT / package).rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            imported: set[str] = set()
            literals: set[str] = set()
            # Docstring & string telanjang = PROSA, bukan kode: dikenali dari posisinya (satu
            # `ast.Expr`) dan dikecualikan lebih dulu, supaya penjaga ini tidak menghukum
            # dokumentasi yang justru menjelaskan larangannya.
            prose = {
                id(node.value)
                for node in ast.walk(tree)
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) not in prose:
                        literals.add(node.value)
            # Diperiksa pada KODE, bukan pada prosa: docstring `ui/app.py` menyebut esbuild
            # justru untuk mengatakan bahwa buildnya terjadi di tempat lain.
            assert "subprocess" not in imported, f"{source}: mengimpor subprocess"
            for value in literals:
                for forbidden in ("npm", "npx", "node_modules", "esbuild"):
                    assert forbidden not in value.split(), f"{source}: literal {value!r}"

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipes: dict[str, list[str]] = {}
    current = None
    for line in makefile.splitlines():
        if line and not line.startswith("\t") and ":" in line and not line.startswith("."):
            current = line.split(":", 1)[0].strip()
            recipes[current] = []
        elif line.startswith("\t") and current:
            recipes[current].append(line)
    assert "frontend" in recipes, "target `make frontend` hilang"
    assert any("esbuild" in line for line in recipes["frontend"])
    assert any("npm ci" in line for line in recipes["frontend"]), "pakai `npm ci`, bukan `npm install`"
    for target in ("ui", "test", "demo"):
        joined = " ".join(recipes[target])
        for forbidden in ("npm", "npx", "node "):
            assert forbidden not in joined, f"`make {target}` menyentuh Node lewat {forbidden}"


def test_npm_has_zero_runtime_dependencies() -> None:
    """ADR-020 butir 3 + ADR-025: dependency RUNTIME npm tetap NOL; yang boleh masuk BUILD terdaftar.

    Yang dijaga di sini bukan "sedikit paket" melainkan janji yang membuat ADR-020 ada: **clone
    tanpa Node sama sekali tetap bisa `make ui`, `make test`, `make demo`**. Itu tetap benar
    dengan gsap dan lenis, karena keduanya DIBUNDEL ke `ui/static/app.js` yang dilacak git —
    mesin juri tidak pernah menjalankan npm.

    Daftarnya tetap TERTUTUP dan disebutkan satu per satu. Bukan karena paket itu jahat, tetapi
    karena `DISCLOSURE.md` wajib menyebut setiap paket yang benar-benar terkirim, dan daftar
    yang boleh tumbuh diam-diam adalah daftar yang berhenti menggambarkan apa pun.
    """
    manifest = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert manifest.get("dependencies", {}) == {}, "nol dependency RUNTIME npm (ADR-020 butir 3)"
    assert set(manifest["devDependencies"]) == {"esbuild", "typescript", "gsap", "lenis"}
    assert manifest["devDependencies"]["esbuild"] == "0.28.2"
    assert manifest["devDependencies"]["typescript"] == "7.0.2"
    assert manifest["devDependencies"]["gsap"] == "3.12.5"
    assert manifest["devDependencies"]["lenis"] == "1.1.0"
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    roots = {name.removeprefix("node_modules/") for name in lock["packages"] if name}
    non_platform = {
        name for name in roots if not name.startswith("@esbuild/") and not name.startswith("@typescript/")
    }
    assert non_platform == {
        "esbuild",
        "typescript",
        "gsap",
        "lenis",
        "@darkroom.engineering/tempus",  # satu-satunya transitif lenis
    }, non_platform


#: Konstanta yang HARUS berbunyi sama di server dan di klien. Kirinya nama di `frontend/src/
#: text.ts`, kanannya nilai Python-nya.
SHARED_SENTENCES = {
    "EMPTY_SENTENCE": ui_screens.EMPTY_SENTENCE,
    "NEUTRAL_MARK": ui_screens.NEUTRAL_MARK,
    "UNSPOKEN_MARK": ui_screens.UNSPOKEN_MARK,
    "ABSENT_MARK": ui_screens.ABSENT_MARK,
    "ARIA_NULL": ui_screens.ARIA_NULL,
    "ARIA_NO_SOURCE": ui_screens.ARIA_NO_SOURCE,
    "NARRATION_LABEL": ui_screens.NARRATION_LABEL,
    "NARRATION_EMPTY": ui_screens.NARRATION_EMPTY,
    "NARRATION_NOTE": ui_screens.NARRATION_NOTE,
    "VETO_RANK_COLUMN": ui_screens.VETO_RANK_COLUMN,
    "AGE_NOTE": ui_screens.AGE_NOTE,
    "TITLE_WATCHLIST": ui_screens.TITLE_WATCHLIST,
    "TITLE_CARD": ui_screens.TITLE_CARD,
}


def client_constants() -> dict[str, str]:
    """`export const NAMA = "...";` dari `frontend/src/text.ts`, dibaca TANPA menjalankan Node."""
    source = (FRONTEND / "src" / "text.ts").read_text(encoding="utf-8")
    found = re.findall(r'^export const ([A-Z_]+) = "((?:[^"\\]|\\.)*)";$', source, re.M)
    return {name: json.loads(f'"{value}"') for name, value in found}


def test_the_client_and_the_server_speak_one_sentence_not_two() -> None:
    """Klien merender tabel yang SAMA, jadi kalimatnya harus kalimat yang sama.

    Ini penjaga terhadap kelas kegagalan yang paling mungkin lahir dari ADR-020: dua salinan
    kalimat "unspoken — the evidence is not enough, and that is not zero", satu di Python dan
    satu di TypeScript, yang menyimpang diam-diam sampai layar hasil polling berbunyi lain
    daripada layar hasil render server. Berkas TS-nya dibaca sebagai TEKS — tidak ada Node yang
    dijalankan — dan tiap kalimat dibandingkan apa adanya.

    Ia juga menjaga BUNDEL tetap segar: kalimat yang sudah diubah di sumber tetapi belum
    dibangun ulang tidak akan ada di `ui/static/app.js`, dan cabang terakhir merah.
    """
    declared = client_constants()
    bundle = (STATIC / "app.js").read_text(encoding="utf-8")
    for name, sentence in SHARED_SENTENCES.items():
        assert name in declared, f"{name} tidak ada di frontend/src/text.ts"
        assert declared[name] == sentence, f"{name} berbeda antara server dan klien"
        assert sentence in bundle, f"{name} tidak ada di bundel: `make frontend` belum dijalankan?"


def test_the_client_can_only_write_text_never_html_and_never_evaluates_anything() -> None:
    """Permukaan `innerHTML` tidak lagi ditutup dengan "nol JS"; ia ditutup di sini.

    Sebelum ADR-020, layar ini membuktikan dirinya aman dari injeksi dengan tidak punya JS sama
    sekali. Sekarang ada JS, jadi janjinya harus dibuktikan dengan cara lain: satu-satunya jalan
    teks masuk DOM adalah `textContent`, dan tidak ada satu pun jalan untuk mengevaluasi teks
    sebagai kode. Diperiksa di SUMBER **dan** di bundel yang benar-benar dilayani — sumber yang
    bersih dengan bundel yang basi tidak membuktikan apa pun tentang yang dijalankan browser.
    """
    forbidden = (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        "srcdoc",
        "javascript:",
        "localStorage",
        "sessionStorage",
        "document.cookie",
    )
    sources = sorted((FRONTEND / "src").glob("*.ts"))
    assert len(sources) >= 5, "sumber TypeScript hilang"
    for path in [*sources, STATIC / "app.js"]:
        body = strip_comments(path.read_text(encoding="utf-8"))
        for needle in forbidden:
            assert needle not in body, f"{path.name}: {needle}"
        # Nol host pihak ketiga YANG DIHUBUNGI. Melarang string "http" secara buta akan
        # menolak namespace XML (yang bukan alamat yang pernah diambil) dan URL lisensi di
        # komentar (yang justru WAJIB ada — lihat DISCLOSURE.md). Yang diperiksa karena itu
        # adalah setiap URL yang muncul, terhadap daftar yang alasannya tertulis.
        for url in re.findall(r"https?://[^\s\"')]+", body):
            assert url.rstrip(".") in BUNDLE_URL_ALLOWLIST, f"{path.name}: URL asing {url}"


def test_the_client_renders_unspoken_as_a_word_and_computes_no_decision() -> None:
    """ADR-003 utuh, dan `unspoken` tetap `unspoken` — dipatok pada sumber klien.

    Yang dilarang bukan sekadar kata: klien tidak boleh memuat satu pun nama ambang, nama item,
    atau nama vonis yang akan berarti ia MEMUTUSKAN sesuatu. Kalau suatu hari seseorang menyalin
    `ITEM_ORDER` atau daftar `VERDICT_*` ke dalam TypeScript, keputusan mulai lahir di dua
    tempat dan `make replay` berhenti menjadi kebenaran tunggal.
    """
    body = strip_comments(
        "\n".join(path.read_text(encoding="utf-8") for path in sorted((FRONTEND / "src").glob("*.ts")))
    )
    for item_id in ITEM_ORDER:
        assert item_id not in body, f"klien menyebut item keputusan {item_id}"
    for forbidden in ("max_age_min", "params_hash =", "decide", "threshold"):
        assert forbidden not in body, forbidden
    # Yang WAJIB ada: kata untuk ketiadaan.
    assert "UNSPOKEN_MARK" in body and "ABSENT_MARK" in body
    # Dan modul yang MERENDER tidak boleh mengganti nilai kosong dengan nol satu kali pun.
    # `?? 0` yang sah hidup di `order.ts`, yang tidak pernah menyentuh DOM — pemisahan itulah
    # yang membuat aturan ini bisa ditegakkan, bukan sekadar dinasihatkan.
    render = strip_comments((FRONTEND / "src" / "render.ts").read_text(encoding="utf-8"))
    for substitute in ("?? 0", "|| 0", '?? ""', '|| ""', '?? "-"'):
        assert substitute not in render, f"render.ts mengganti ketiadaan dengan {substitute}"


def test_the_client_verifies_the_simulated_flag_before_it_renders_anything() -> None:
    """Kewajiban lomba berlaku untuk klien juga: badan tanpa `simulated: true` TIDAK dirender.

    Server sudah menjamin setiap respons membawanya; yang dijaga di sini adalah bahwa klien
    memeriksanya, bukan mempercayainya — dan bahwa yang tertinggal saat pemeriksaan itu gagal
    adalah layar hasil render server, bukan layar kosong.
    """
    api = (FRONTEND / "src" / "api.ts").read_text(encoding="utf-8")
    assert 'payload["simulated"] !== true' in api
    assert "throw new ApiError" in api
    bundle = (STATIC / "app.js").read_text(encoding="utf-8")
    assert '"simulated"' in bundle and "simulated: true" in bundle


def test_the_screens_declare_the_polling_interval_they_actually_use() -> None:
    """Selang penyegaran DISEBUT di layar dan bisa dimatikan — bukan angka rahasia di dalam JS.

    Satu angka, dua tempat yang harus setuju: kalimat yang dibaca juri dan atribut yang dibaca
    kode. Kalau salah satunya bergeser, layar menjanjikan ritme yang tidak dijalankannya.
    """
    seconds = ui_screens.POLL_INTERVAL_MS // 1000
    assert f"every {seconds} seconds" in ui_screens.WATCHLIST_CLIENT_NOTE
    assert "a switch on screen turns that off" in ui_screens.WATCHLIST_CLIENT_NOTE
    main = (FRONTEND / "src" / "main.ts").read_text(encoding="utf-8")
    assert 'body.dataset["pollMs"]' in main, "klien harus membaca selangnya dari halaman"
    assert "aria-pressed" in main, "sakelar penyegaran harus mengumumkan keadaannya"
    # Dan angka CADANGAN di dalam klien harus angka yang sama. Tanpa baris ini tes di atas
    # hanya membandingkan konstanta dengan dirinya sendiri: `WATCHLIST_CLIENT_NOTE` diturunkan dari
    # `POLL_INTERVAL_MS`, jadi menggesernya menggeser keduanya sekaligus dan tidak ada yang
    # merah. Yang BISA menyimpang adalah bawaan klien — dan itulah yang dipatok di sini.
    (fallback,) = re.findall(r"^let pollMs = (\d+);$", main, re.M)
    assert int(fallback) == ui_screens.POLL_INTERVAL_MS, "bawaan klien dan selang yang dicetak layar berbeda"
    # (Bundelnya sendiri menulis angka itu sebagai `3e4`; yang dipatok di sini sumbernya, dan
    # kesegaran bundel dijaga `test_the_client_and_the_server_speak_one_sentence_not_two`.)


def test_every_screen_carries_the_hooks_the_client_needs_and_a_banner_without_it(
    client: TestClient,
) -> None:
    """ADR-020 butir 6: banner dari SERVER, dan kait klien tidak boleh menggantikan isi apa pun.

    Halaman yang dikirim server sudah lengkap — banner, `<main id="isi">`, tabel — dan kait yang
    dipakai klien hanyalah dua atribut `data-*`. Kalau suatu hari server menyusut menjadi
    kerangka kosong, `<table>` di bawah hilang dan tes ini merah.
    """
    for path, screen in (
        ("/", "landing"),
        ("/app", "watchlist"),
        ("/ui", "watchlist"),
        ("/app/decision/1", "card"),
        ("/ui/decision/1", "card"),
    ):
        html = client.get(path).text
        assert f'data-screen="{screen}"' in html
        assert f'data-poll-ms="{ui_screens.POLL_INTERVAL_MS}"' in html
        assert '<main id="isi">' in html
        assert BANNER in html
        # Isi ADA sebelum satu baris JS pun jalan. Landing dikecualikan dari `<table>` karena
        # papannya HILANG saat store kosong (ADR-021) — menuntut tabel di sini akan memaksa
        # halaman ini mengarang baris, yang justru dilarang. Isinya tetap dituntut ada.
        assert "aria-label=" in html
        if screen == "landing":
            assert "<h1>" in html and 'href="/app"' in html
        else:
            assert "<table" in html
    card = client.get("/app/decision/1").text
    assert 'data-decision-id="1"' in card


# --- tinjauan keamanan (BLOKIR): CSP yang mematikan klien, dan layar kosong ------------------


def csp_directives(header: str | None = None) -> dict[str, list[str]]:
    """CSP jadi `{direktif: [nilai]}`. Tanpa argumen: yang terpasang di `ui/app.py`, bukan disalin.

    `header` diterima supaya CSP yang benar-benar DIKIRIM sebuah respons bisa di-parse dengan
    parser yang sama — dua parser adalah dua kesempatan untuk membaca hal berbeda.
    """
    header = HTML_HEADERS["Content-Security-Policy"] if header is None else header
    parsed: dict[str, list[str]] = {}
    for chunk in header.split(";"):
        parts = chunk.split()
        if parts:
            parsed[parts[0]] = parts[1:]
    return parsed


def client_fetch_targets() -> list[str]:
    """Argumen literal setiap `fetch(...)` di sumber klien. Kosong = tesnya tidak menguji apa-apa."""
    source = strip_comments((FRONTEND / "src" / "api.ts").read_text(encoding="utf-8"))
    return re.findall(r"fetch\(\s*([A-Za-z_][A-Za-z0-9_]*|\"[^\"]*\")", source)


def test_the_csp_names_connect_src_or_every_fetch_this_client_makes_is_blocked(
    client: TestClient,
) -> None:
    """Temuan TINGGI: CSP tanpa `connect-src` membunuh SELURUH alasan ADR-020 ada.

    `connect-src` adalah fetch-directive, dan fetch-directive yang tidak disebut jatuh ke
    `default-src` (CSP Level 3). `default-src 'none'` karena itu berarti `connect-src 'none'`:
    `fetch("/api/watchlist")` ditolak browser TERMASUK ke origin sendiri, muat pertama gagal,
    auto-refresh tidak pernah berhasil, dan `WATCHLIST_CLIENT_NOTE` yang dirender server menjanjikan
    ritme 30 detik yang tidak mungkin ditepati. Tidak ada browser di jalur tes — jadi yang
    berdiri di tempat browser adalah tes ini.

    Ia juga menjaga pelonggarannya tetap SEMPIT: `'self'` dan tidak satu token pun selain itu.
    """
    directives = csp_directives()
    assert directives["default-src"] == ["'none'"], "prasyarat: yang tidak disebut memang ditolak"
    assert "connect-src" in directives, (
        "CSP tidak menyebut connect-src: ia jatuh ke default-src 'none' dan SETIAP fetch() klien "
        "ditolak browser, termasuk ke origin sendiri"
    )
    assert directives["connect-src"] == ["'self'"], directives["connect-src"]
    # Dan yang benar-benar diambil klien memang se-origin, sehingga `'self'` CUKUP: satu host
    # asing di sini akan lolos CSP-nya sendiri dan gagal diam-diam di browser.
    targets = client_fetch_targets()
    assert targets, "prasyarat: klien memang memanggil fetch()"
    for target in targets:
        assert not target.startswith('"http'), target
    main = (FRONTEND / "src" / "main.ts").read_text(encoding="utf-8")
    paths = re.findall(r'getJson\(\s*[`"](/[^`"$]*)', main)
    assert paths, "prasyarat: klien memang meminta path API"
    for path in paths:
        assert path.startswith("/api/"), path
    # Header yang benar-benar dikirim, bukan hanya konstanta di modul.
    for route in ("/", "/ui", "/ui/decision/1"):
        assert "connect-src 'self'" in client.get(route).headers["content-security-policy"], route


def test_the_csp_that_ui_readme_describes_is_not_a_second_copy_of_it() -> None:
    """`ui/README.md` menjelaskan SIFAT CSP dan tidak mengutip nilainya — sengaja.

    Nilai CSP yang harfiah di tiga berkas adalah tiga berkas yang harus diperbarui serempak,
    dan yang ketiga selalu ketinggalan (itulah cara `img-src 'none'` sempat tertulis sebagai
    direktif terakhir setelah `connect-src` masuk). Satu tempat: `ui/app.py:HTML_HEADERS`.
    """
    readme = (ROOT / "ui" / "README.md").read_text(encoding="utf-8")
    assert "ui/app.py:HTML_HEADERS" in readme
    assert HTML_HEADERS["Content-Security-Policy"] not in readme
    assert "script-src 'self'; style-src" not in readme, "nilai CSP dikutip harfiah lagi"


def test_a_poisoned_snapshot_id_is_cleaned_at_the_api_boundary_not_only_in_html(
    client: TestClient, store: Store
) -> None:
    """Temuan SEDANG: `snapshot_id` dulu satu-satunya nilai asal-DB yang lolos tanpa `clean_text`.

    Server menuliskannya terlihat lewat `_bdi`, tetapi klien merender sel ini dengan
    `textContent` — jadi U+202E/ESC/NUL masuk DOM klien HIDUP-HIDUP, kelas serangan yang sama
    dengan injeksi ANSI. Yang ditutup di sini adalah BATAS API, tempat kedua perender membaca.
    """
    poison = "‮12\x1b[31m\x0034"
    from core.replay import decision_hash, freeze

    bundle = freeze(store, [])
    decision_id = store.insert_decision(
        symbol="SOL",
        decision="NO_GO",
        blocking=["01_freshness"],
        snapshot_ids=[],
        params_hash=DEFAULT_PARAMS.params_hash(),
        decision_hash=decision_hash(
            bundle,
            DEFAULT_PARAMS.as_dict(),
            {"decision": "NO_GO", "blocking": ["01_freshness"], "item_status": {"01_freshness": "red"}},
        ),
        card={
            "algo": ALGO,
            "skill": {
                "status": "ok",
                "data": {
                    "items": [
                        {
                            "id": "01_freshness",
                            "status": "red",
                            "value": {"age_min": 9},
                            "reason": "too old",
                            "source": {
                                "tool": "core_data",
                                "field": "data.age_min",
                                "snapshot_id": poison,
                                "as_of": "2026-09-06T06:15:00Z",
                            },
                        }
                    ]
                },
            },
            "agent": {},
        },
    )
    body = client.get(f"/api/decision/{decision_id}").json()
    (row,) = [item for item in body["items"] if item["item_id"] == "01_freshness"]
    served = row["snapshot_id"]
    assert isinstance(served, str)
    for raw in ("‮", "\x1b", "\x00"):
        assert raw not in served, f"{raw!r} sampai apa adanya ke klien"
    assert served.startswith("\\u202e"), served  # dibuang? tidak — di-escape TERLIHAT
    assert "\\u001b" in served and "\\u0000" in served
    assert served == clean_text(poison, 64)
    # Nomor snapshot yang JUJUR (angka) tidak boleh ikut berubah bentuk menjadi teks.
    normal = card_payload(client)
    assert any(isinstance(item["snapshot_id"], int) for item in normal["items"])


def test_both_renderers_say_the_same_thing_about_a_snapshot_id_that_is_not_a_number() -> None:
    """Sel dan `aria-label` tidak boleh berselisih — di server MAUPUN di klien.

    Klien memakai `num()` untuk `aria-label` tetapi `said()` untuk selnya: `snapshot_id`
    non-angka membuat pembaca layar mendengar "snapshot not stated" sementara selnya
    MENAMPILKAN nilainya — persis yang dilarang
    `test_a_row_label_never_carries_less_than_the_cell_shows` untuk sisi server.
    """
    payload: dict[str, Any] = {
        "decision_id": 1,
        "symbol": "SOL",
        "decision": "NO_GO",
        "generation": "current",
        "blocking": ["01_freshness"],
        "items": [
            {
                "item_id": "01_freshness",
                "veto_rank": 1,
                "status": "red",
                "value": {"age_min": 9},
                "tool": "core_data",
                "field": "data.age_min",
                "snapshot_id": "s-42",
                "as_of_jst": "2026-09-06 06:15 JST",
            }
        ],
    }
    labels = re.findall(r'<tr aria-label="([^"]*)"', render_card(payload, BANNER, render_clock()))
    assert any("snapshot s-42" in label for label in labels), labels
    assert not any("snapshot not stated" in label for label in labels)
    # Dan kembarannya di klien menurunkan klausa itu dengan penyaji yang SAMA dengan selnya.
    render = strip_comments((FRONTEND / "src" / "render.ts").read_text(encoding="utf-8"))
    assert 'num(item["snapshot_id"])' not in render, "klien kembali memakai num() untuk aria-label"
    assert 'phrase("snapshot", item["snapshot_id"], "snapshot not stated")' in render


def test_the_client_is_handed_the_cut_sentence_instead_of_deriving_it(client: TestClient) -> None:
    """`_cut_marks` tidak punya kembaran di klien — jadi kalimatnya DIKIRIM, bukan ditiru.

    Sesudah polling, `...(truncated)` tetap terlihat sementara kalimat yang menyebut SIAPA yang
    memotong hilang. Yang dikirim adalah keluaran `ui.screens.cut_note` atas nilai payload yang
    sama yang dibaca server-render: satu fungsi, dua perender, nol logika kedua yang bisa
    menyimpang.
    """
    payload = card_payload(client)
    assert payload["items"], "prasyarat: kartunya punya baris"
    for item in payload["items"]:
        assert "value_cut_note" in item and "reason_cut_note" in item
        # Kartu sehat tidak memotong apa pun, dan itu dikatakan sebagai None — bukan "".
        assert item["value_cut_note"] is None and item["reason_cut_note"] is None
    booked = clean_text("y" * (TEXT_LIMIT + 50), TEXT_LIMIT)
    assert ui_screens.cut_note(booked, REASON_LIMIT, ledger_limits=ui_screens.REASON_CEILINGS) == (
        LEGACY_REASON_CUT_NOTE
    )
    assert ui_screens.cut_note("x" * 20 + TRUNCATED_MARK, REASON_LIMIT) == FOREIGN_MARK_NOTE
    assert ui_screens.cut_note("z" * (VALUE_LIMIT + 5), VALUE_LIMIT) == SCREEN_CUT_NOTE.format(
        limit=VALUE_LIMIT
    )
    assert ui_screens.cut_note(None, VALUE_LIMIT) is None
    assert ui_screens.cut_note("pendek", VALUE_LIMIT) is None
    # Dan klien merendernya sebagai KALIMAT yang diterima, tanpa menurunkan apa pun sendiri.
    render = strip_comments((FRONTEND / "src" / "render.ts").read_text(encoding="utf-8"))
    assert 'cut(item["value_cut_note"])' in render and 'cut(item["reason_cut_note"])' in render
    assert TRUNCATED_MARK not in render, "klien tidak boleh menebak siapa yang memotong"
    bundle = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "value_cut_note" in bundle and "reason_cut_note" in bundle


def test_one_css_naming_rule_serves_both_renderers() -> None:
    """`isidentifier()` (Unicode) di server vs regex ASCII di klien = dua layar untuk satu baris.

    `status` bernilai `ｇreen` (U+FF47) dulu menghasilkan `class="s-ｇreen"` di server dan
    `s-unknown` di klien. Bukan lubang — keduanya meng-escape — tetapi divergensi, di layar yang
    seluruh nilai jualnya "dua render, satu kalimat".
    """
    assert ui_screens._css_word("green") and ui_screens._css_word("green_neutral")
    assert not ui_screens._css_word("ｇreen"), "identifier Unicode kembali diterima"
    assert not ui_screens._css_word("s-green") and not ui_screens._css_word("")
    assert "ｇreen".isidentifier(), "prasyarat: inilah yang dulu lolos"
    code = [
        line
        for line in (ROOT / "ui" / "screens.py").read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert not [line for line in code if ".isidentifier()" in line], "aturan lama masih dipakai"
    render = strip_comments((FRONTEND / "src" / "render.ts").read_text(encoding="utf-8"))
    assert render.count("/^[A-Za-z_][A-Za-z0-9_]*$/") == 2
    assert ui_screens.CSS_WORD.pattern == "^[A-Za-z_][A-Za-z0-9_]*$"


#: SHA-256 artefak build yang DIKIRIM (`ui/static/`), dipaku di sini.
#:
#: Kenapa hash dan bukan "bangun ulang lalu bandingkan": tes ini harus jalan di clone bersih
#: TANPA Node (ADR-020 butir 2, ADR-001) — mesin juri persis mesin itu, dan tes ber-`skipif npm`
#: akan di-skip justru di sana. Yang dipaku karena itu adalah BYTE yang benar-benar dijalankan
#: browser. Penjaga lain (`test_the_client_and_the_server_speak_one_sentence_not_two`, penjaga
#: sink HTML) memeriksa ISI bundel; yang ditutup di sini adalah celah sebaliknya: bundel yang
#: memuat kode TAMBAHAN yang tidak menyebut satu pun kalimat/nama yang dicari penjaga itu.
#:
#: Cara memperbaruinya: `make frontend` mencetak hash barunya. Menggantinya adalah perbuatan
#: yang TERLIHAT di diff — dan itulah seluruh gunanya.
BUNDLE_SHA256 = "75b55ac0a41138d51a2c1b38a1f339da9707d66ff4caa9555eb037ca59c47d4c"
STYLESHEET_SHA256 = "d6776cc92ef633e4619962bd2b49a20f652e0ab29696c794b1e74de165a2378f"


def test_the_shipped_bundle_is_pinned_byte_for_byte() -> None:
    """Artefak yang dijalankan mesin juri, dipaku sebagai BYTE — nol Node untuk memeriksanya."""
    for path, expected in ((STATIC / "app.js", BUNDLE_SHA256), (STATIC / "app.css", STYLESHEET_SHA256)):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == expected, (
            f"{path.name} berubah tanpa hash-nya diperbarui: {digest}. Kalau ini hasil "
            "`make frontend` yang sah, perbarui konstantanya di tes ini pada commit yang sama."
        )


def test_moving_to_another_screen_can_never_leave_a_blank_one() -> None:
    """Klik satu simbol tidak boleh menghasilkan halaman kosong permanen.

    `go()` dulu `clear(viewNode)` DULU lalu memanggil `load(true)`; cabang gagal `load()` hanya
    menyentuh baris status, jadi tabel hasil render server yang tadi ada terhapus dan tidak
    pernah kembali. Dengan CSP yang memblokir `fetch` (temuan TINGGI di atas) itu berarti: juri
    mengklik satu simbol dan mendapat LAYAR KOSONG. Perbaikannya berdiri sendiri — API yang
    sedang mati harus menghasilkan layar lama yang mengaku lama, bukan kekosongan.

    Diperiksa di sumber DAN di bundel, karena tidak ada browser di jalur tes.
    """
    main = strip_comments((FRONTEND / "src" / "main.ts").read_text(encoding="utf-8"))
    go = main[main.index("function go(") : main.index("function isInternal(")]
    assert "clear(" not in go, "go() mengosongkan layar sebelum tahu ada yang menggantikannya"
    assert "load(true)" in go
    dom_import = re.search(r'import \{([^}]*)\} from "\./dom\.js";', main)
    assert dom_import is not None, "prasyarat: main.ts mengimpor dari dom.ts"
    assert "clear" not in dom_import.group(1), "main.ts masih mengimpor clear(): pemakainya hanya go()"
    # Kegagalan mengaku layar MANA yang tertinggal; tiga kalimat, dan tidak satu pun kekosongan.
    for sentence in ("STALE_SCREEN", "OFFLINE_KEPT", "the server-rendered screen below is untouched"):
        assert sentence in main, sentence
    kept = main[main.index("function kept()") : main.index("async function load(")]
    assert "STALE_SCREEN" in kept and "OFFLINE_KEPT" in kept
    declared = client_constants()
    assert "STALE_SCREEN" in declared and "blank screen" in declared["STALE_SCREEN"]
    bundle = (STATIC / "app.js").read_text(encoding="utf-8")
    assert declared["STALE_SCREEN"] in bundle, "`make frontend` belum dijalankan?"


# --- tinjauan keamanan ronde-2: umur yang benar, dan janji yang menyesuaikan layarnya --------


def main_source() -> str:
    """`frontend/src/main.ts` tanpa komentar — komentarnya menyebut justru kesalahan yang dilarang."""
    return strip_comments((FRONTEND / "src" / "main.ts").read_text(encoding="utf-8"))


def slice_of(source: str, start: str, end: str) -> str:
    """Potongan satu fungsi. `start`/`end` harus ADA, supaya tesnya merah kalau namanya berubah."""
    assert start in source and end in source, (start, end)
    return source[source.index(start) : source.index(end)]


def test_a_keystroke_in_the_filter_box_cannot_reset_the_age_of_what_is_on_screen() -> None:
    """Temuan TINGGI: satu ketikan mengganti pengakuan "layar ini basi" dengan jam SEKARANG.

    Urutan yang direproduksi peninjau pada bundel yang dikirim:

    1. muat awal sukses → "... Fetched at 11:28:08 UTC (browser clock)."
    2. API mati, poll   → "could not refresh: ... the last successful response is still on
       screen and it is OLD ..."
    3. ketik "S"        → "filter "S": ... Fetched at 11:28:08 UTC (browser clock)."

    Langkah 3 memajang data dari pengambilan SEBELUMNYA, tetapi `paint()` memanggil
    `browserClock()` saat MELUKIS dan menuliskannya dengan kata "Fetched" — dan `OFFLINE_KEPT`
    lenyap tanpa jejak. Angka tanpa umur yang benar adalah persis kelas kesalahan yang produk
    ini ada untuk melawan; kartu yang berbohong soal kapan datanya diambil lebih buruk daripada
    kartu yang tidak menampilkan apa-apa.

    Yang dipatok di sini adalah bentuk perbaikannya, karena tidak ada browser di jalur tes
    (lihat "Batas yang belum tertutup" di `ui/README.md`): jam hanya dibaca saat respons TIBA,
    `paint()` tidak boleh menyentuhnya, dan kalimat kegagalan DISIMPAN sehingga ia selamat dari
    lukisan berikutnya.
    """
    main = main_source()
    paint = slice_of(main, "function paint()", "function shown()")
    assert "browserClock(" not in paint, (
        "paint() menghitung ulang jam: satu ketikan di kotak saring akan mencetak jam MELUKIS "
        "di atas data dari pengambilan sebelumnya"
    )
    assert "age()" in paint, "paint() harus meminjam kalimat umur, bukan mengarang satu"
    assert 'state.failure === null ? "ok" : "error"' in paint, "kegagalan harus tetap berwarna gagal"

    # Jamnya dibaca DI SATU JENIS TEMPAT saja: saat sebuah respons benar-benar tiba.
    calls = [line.strip() for line in main.splitlines() if "browserClock()" in line]
    assert calls, "prasyarat: kliennya memang mencatat jam"
    for line in calls:
        assert line in ("function browserClock(): string {", "state.fetchedAt = browserClock();"), line
    assert calls.count("state.fetchedAt = browserClock();") == 2, "dua jalur sukses: watchlist dan kartu"

    # Kalimat umur mendahulukan kegagalan yang BELUM digantikan respons sukses baru.
    age = slice_of(main, "function age(): string {", "function paint()")
    assert "if (state.failure !== null) return state.failure;" in age
    assert "NEVER_FETCHED" in age, "belum pernah sukses ≠ 'Fetched at'"

    # Dan kalimat kegagalan itu disimpan, bukan hanya diucapkan sekali.
    load = slice_of(main, "async function load(", "function schedule()")
    assert "state.failure =\n" in load or "state.failure =" in load
    assert load.count("state.failure = null;") == 2, "sukses harus MENCABUT pengakuan basi"
    assert "NOT_REFRESHED_SINCE" in load, "kegagalan harus menyebut kapan isi di layar diambil"

    declared = client_constants()
    bundle = (STATIC / "app.js").read_text(encoding="utf-8")
    for name in ("NEVER_FETCHED", "NOT_REFRESHED_SINCE"):
        assert name in declared, f"{name} tidak ada di frontend/src/text.ts"
        assert declared[name] in bundle, f"{name} belum masuk bundel: `make frontend` belum dijalankan?"


def test_every_screen_promises_only_the_rhythm_that_screen_actually_has(client: TestClient) -> None:
    """Temuan SEDANG: satu kalimat klien untuk tiga layar menjanjikan ritme yang dua di antaranya
    tidak punya.

    `schedule()` memasang timer HANYA bila layarnya watchlist, bar kontrol (saring/urut/sakelar)
    disembunyikan di luar watchlist, dan pada halaman gagal `start()` berhenti sebelum permintaan
    pertamanya. Kalimat lama — "re-fetches ... every 30 seconds (a switch on screen turns that
    off)" — tercetak di SETIAP halaman, termasuk kartu, yaitu layar yang paling mungkin dibuka
    juri kedua.
    """
    seconds = ui_screens.POLL_INTERVAL_MS // 1000
    watchlist = unescape(client.get("/app").text)
    card = unescape(client.get("/app/decision/1").text)
    failure = unescape(client.get("/app/decision/999999").text)
    landing = unescape(client.get("/").text)
    assert client.get("/app/decision/999999").status_code == 404, "prasyarat: ini halaman gagal"

    # Layar A memang punya keempatnya; yang lain tidak boleh mengklaimnya. Diperiksa sebagai
    # KALIMAT di halaman yang dilayani, bukan lewat konstanta, supaya yang merah adalah janjinya.
    assert f"every {seconds} seconds" in watchlist and "a switch on screen turns that off" in watchlist
    for page, name in ((card, "kartu"), (failure, "halaman gagal"), (landing, "landing")):
        assert f"every {seconds} seconds" not in page, f"{name} menjanjikan ritme yang tidak dimilikinya"
        assert "a switch on screen turns that off" not in page, f"{name} menjanjikan sakelar yang tidak ada"
        assert "filters and sorts the rows" not in page, f"{name} menjanjikan saringan yang tidak ada"
    assert ui_screens.WATCHLIST_CLIENT_NOTE in watchlist
    assert ui_screens.CARD_CLIENT_NOTE in card
    assert ui_screens.ERROR_CLIENT_NOTE in failure
    assert ui_screens.LANDING_CLIENT_NOTE in landing

    # Dan itu memang yang dilakukan kliennya — bukan hanya yang dikatakan kalimatnya.
    main = main_source()
    schedule = slice_of(main, "function schedule(): void {", "function go(")
    assert 'state.polling && state.screen === "watchlist"' in schedule, "timer di luar Layar A?"
    hidden = 'controlsNode.hidden = state.screen !== "watchlist"'
    assert hidden in main, "bar kontrol tidak lagi disembunyikan di luar Layar A"
    start = main[main.index("function start(): void {") :]
    # ADR-021: `error` dan `landing` sama-sama layar INERT, jadi penjaganya kini satu
    # nama — tetapi yang dituntut tetap sama, yaitu berhenti SEBELUM `load()` pertama.
    assert 'const inert = screen === "error" || screen === "landing";' in start, (
        "layar inert tidak lagi didefinisikan di satu tempat"
    )
    assert "if (inert) return;" in start, "layar inert harus berhenti sebelum load()"
    assert start.index("const inert") < start.index("if (inert) return;")
    assert "CLIENT_INERT" in start, "halaman gagal tidak boleh membuka dengan janji mengambil"


def test_the_window_title_follows_the_screen_the_client_actually_rendered(client: TestClient) -> None:
    """RENDAH-2: judul jendela tertinggal di layar sebelumnya sesudah perpindahan tanpa muat ulang.

    Kelas yang sama dengan `kept()`: apa yang dikatakan TENTANG layar harus ikut layar yang
    benar-benar ada. Judul karena itu ditulis di `shown()` — satu tempat, dan tempat itu hanya
    dicapai sesudah render yang sungguh terjadi, jadi perpindahan yang GAGAL meninggalkan judul
    yang masih cocok dengan layar yang tertinggal.
    """
    main = main_source()
    assert main.count("document.title") == 1, (
        "judul jendela tidak ditulis tepat satu kali: nol berarti ia tertinggal di layar "
        "sebelumnya sesudah perpindahan tanpa muat ulang, lebih dari satu berarti ia bisa "
        "menyimpang dari isi"
    )
    shown = slice_of(main, "function shown(): void {", "function kept()")
    assert "document.title =" in shown, "judul tidak menyusul isi yang benar-benar dirender"
    assert "TITLE_CARD.replace" in shown and "TITLE_WATCHLIST" in shown
    # Dan judulnya adalah judul yang sama yang dicetak server — satu kalimat, dua perender.
    assert f"<title>{ui_screens.TITLE_WATCHLIST}</title>" in client.get("/app").text
    assert f"<title>{ui_screens.TITLE_CARD.format(id=1)}</title>" in client.get("/ui/decision/1").text


def test_a_card_page_without_a_decision_id_makes_the_client_ask_for_nothing(
    client: TestClient,
) -> None:
    """RENDAH-4: `state.rendered` bisa menjadi `true` tanpa klien merender apa pun.

    Halaman kartu tanpa `data-decision-id` dulu jatuh ke cabang watchlist: `paint()` kembali
    lebih awal (layarnya kartu), tetapi `shown()` tetap dipanggil — sehingga kegagalan
    berikutnya berbunyi "isi klien yang tua" padahal yang di layar milik server. Jalurnya sempit
    (server SELALU memancarkan atribut itu, dan baris terakhir tes ini menjaganya), tetapi
    murah ditutup: klien tidak meminta apa pun, tidak melukis Layar A di atas URL kartu, dan
    mengaku demikian.
    """
    main = main_source()
    start = main[main.index("function start(): void {") :]
    assert 'if (state.screen === "card" && state.decisionId === null) {' in start
    assert "NO_CARD_ID" in start, "klien harus MENGAKU kenapa ia tidak meminta apa pun"
    load = slice_of(main, "async function load(", "function schedule()")
    assert "if (paint()) shown();" in load, "hanya render yang terjadi yang boleh diakui"
    declared = client_constants()
    bundle = (STATIC / "app.js").read_text(encoding="utf-8")
    assert declared["NO_CARD_ID"] in bundle, "`make frontend` belum dijalankan?"
    assert 'data-decision-id="1"' in client.get("/ui/decision/1").text


# --- halaman `/kage` (aset yang dibawa masuk pemilik repo) ---------------------------------
def test_the_kage_page_is_a_screen_that_still_says_simulated(client: TestClient) -> None:
    """`/kage` boleh menjadi papan gambar; ia TIDAK boleh menjadi halaman tanpa banner.

    Kewajibannya sama dengan layar lain (docs/rules-compliance.md): kalimat SIMULATED dirender
    server, sebelum satu baris JS pun jalan. Ditambah satu kewajiban yang khusus milik halaman
    ini: ia harus MENGATAKAN bahwa angka-angka di dalamnya bukan pengukuran, karena "1611" dan
    "92 minutes" di sebelah produk yang menjual bukti akan terbaca sebagai temuan.

    DIPERBARUI (ADR-026 butir 11): kalimat lama di sini berbunyi "header CSP-nya tidak
    dilonggarkan seujung direktif pun". Itu tidak lagi benar — `img-src` halaman ini menerima
    `data:`. Jaminannya tidak dihapus, ia dipindahkan dan dipersempit: setiap header selain CSP
    HARUS identik dengan `HTML_HEADERS`, dan CSP-nya harus persis `KAGE_HTML_HEADERS` — yang
    tes di bawah paku sebagai `HTML_HEADERS` + satu token pada satu direktif.
    """
    response = client.get("/kage")
    assert response.status_code == 200
    assert BANNER in response.text
    assert ui_screens.KAGE_NOTE in response.text
    assert '<meta name="tenko-simulated" content="true">' in response.text
    # Jalan pulang ke layar yang DINILAI ada di halaman itu sendiri, bukan hanya di riwayat
    # browser: halaman hiasan tanpa pintu keluar adalah cara termahal kehilangan 30 detik juri.
    assert '<a href="/app">' in response.text and '<a href="/">' in response.text
    for name, value in KAGE_HTML_HEADERS.items():
        assert response.headers[name] == value, name
    for name, value in HTML_HEADERS.items():
        if name != "Content-Security-Policy":
            assert response.headers[name] == value, name  # nosniff/no-referrer/no-store utuh
    assert client.head("/kage").status_code == 200


def test_the_kage_page_carries_no_inline_script_no_inline_style_and_no_foreign_host(
    client: TestClient,
) -> None:
    """`script-src`/`style-src` halaman ini tidak dilonggarkan, jadi markupnya harus HIDUP di
    bawah CSP yang ada.

    `style-src 'self'` menolak atribut `style=` sama seperti ia menolak `<style>`; `script-src
    'self'` menolak skrip sebaris. Markup yang dibawa masuk memuat tiga atribut `style=` (tiga
    cahaya kartu) — nilainya pindah ke kelas di `ui/static/kage.css`, dan tes ini menjaga
    keduanya tidak kembali. Nol host asing: tiap byte halaman ini berasal dari origin sendiri,
    jadi `make demo` tanpa jaringan tetap menampilkan halaman yang sama (spec §7).

    ADR-026 butir 11 melonggarkan `img-src` saja. Tes ini karena itu menjaga apa yang TIDAK
    ikut longgar: kedua `<script>` tetap `src` + `defer` dari origin sendiri, nol `<style>`,
    nol atribut ` style=`, nol `on…=` — semua yang butuh `unsafe-inline`, yang tidak ada.
    """
    text = client.get("/kage").text
    lower = text.lower()
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", text, re.S)
    assert len(scripts) == 2, f"{len(scripts)} tag script"
    assert scripts == ["", ""], "ada skrip SEBARIS, yang CSP-nya sendiri melarang"
    assert '<script src="/ui/vendor/three.min.js" defer></script>' in text
    assert '<script src="/ui/vendor/kage-scene.js" defer></script>' in text
    # ...dan TIDAK ADA `<script>` ketiga yang lolos dengan bentuk lain: tiap tag punya `src`
    # relatif ke origin sendiri dan `defer`, dihitung dari tag yang benar-benar ada di badan.
    tags = re.findall(r"<script\b[^>]*>", text)
    assert len(tags) == 2, tags
    for tag in tags:
        assert re.search(r'\ssrc="/ui/vendor/[^":]+"', tag), tag
        assert " defer" in tag, tag
    assert '<link rel="stylesheet" href="/ui/kage.css">' in text
    assert "<style" not in lower and " style=" not in lower
    assert not re.search(r"\son[a-z]+\s*=", lower), "penangan kejadian sebaris"
    assert "javascript:" not in lower
    assert "http://" not in lower and "https://" not in lower, "aset dari host asing"


#: Setiap route yang MENGIRIM sesuatu ke browser, dikelompokkan menurut apa yang dijanjikan
#: tentang CSP-nya (ADR-026 butir 11). Daftar ini sengaja harfiah: yang menjaga lingkup
#: pelonggaran tetap sempit adalah menyebut satu per satu apa yang ADA DI LUAR-nya.
JUDGED_SCREENS = ("/", "/app", "/app/decision/1", "/ui", "/ui/decision/1", "/ui/decision/999999")
API_ROUTES = (
    "/api/routes",
    "/api/health",
    "/api/watchlist",
    "/api/decisions",
    "/api/decision/1",
    "/api/preflight/SOL",
)


def asset_routes() -> tuple[str, ...]:
    """Route aset, namanya diambil dari daftar TERTUTUP di `ui/screens.py` — bukan ditebak."""
    return (
        "/ui/tenko.css",
        "/ui/app.js",
        "/ui/kage.css",
        f"/ui/fonts/{ui_screens.FONT_NAMES[0]}",
        f"/ui/img/{ui_screens.IMAGE_NAMES[0]}",
        f"/ui/vendor/{ui_screens.VENDOR_NAMES[0]}",
    )


def test_the_kage_csp_is_the_ordinary_one_plus_data_on_img_src_and_nothing_else(
    client: TestClient,
) -> None:
    """ADR-026 butir 11: pelonggarannya SATU direktif, SATU token, SATU route.

    Tes ini tidak puas dengan "kage punya `data:`" — kalimat itu juga benar untuk CSP yang
    kehilangan `default-src 'none'` atau yang diam-diam menumbuhkan `img-src * data:`. Yang
    dipaku: DIFF antara CSP `/kage` dan `HTML_HEADERS` persis satu direktif, dan isi direktif
    itu persis `'self' data:`. Setiap direktif lain harus identik nilai DAN urutannya.
    """
    sent = client.get("/kage").headers["content-security-policy"]
    assert sent == KAGE_HTML_HEADERS["Content-Security-Policy"], "header terpasang ≠ konstanta"
    strict, relaxed = csp_directives(), csp_directives(sent)
    assert set(strict) == set(relaxed), "daftar direktif berubah, bukan hanya isinya"
    assert relaxed["img-src"] == ["'self'", "data:"], relaxed["img-src"]
    differ = [name for name in strict if strict[name] != relaxed[name]]
    assert differ == ["img-src"], f"lebih dari satu direktif dilonggarkan: {differ}"
    # Direktif yang paling mahal kalau ikut hanyut, disebut namanya supaya kegagalannya terbaca.
    for name in ("script-src", "style-src", "connect-src", "font-src", "media-src"):
        assert relaxed[name] == ["'self'"], f"{name}: {relaxed[name]}"
    for name in ("default-src", "base-uri", "form-action", "frame-ancestors"):
        assert relaxed[name] == ["'none'"], f"{name}: {relaxed[name]}"
    assert "data:" not in " ".join(
        token for name, value in relaxed.items() if name != "img-src" for token in value
    ), sent


def test_no_judged_screen_and_no_api_response_allows_a_data_url_in_any_directive(
    client: TestClient,
) -> None:
    """Yang TIDAK ikut longgar — dan itulah setengah dari keputusannya.

    Layar A & B adalah satu-satunya layar yang merender nilai asal-RYO/DB ke DOM, jadi `data:`
    di sana menghidupkan kembali permukaan penyuntikan yang seluruh `ui/screens.py` dibangun
    untuk menutup. `/kage` tidak membaca store sama sekali; itulah kenapa ia boleh dan mereka
    tidak. Halaman GAGAL di sekitar `/kage` pun tidak ikut: ia tidak melukis grain apa pun.
    """
    for path in JUDGED_SCREENS:
        csp = client.get(path).headers["content-security-policy"]
        assert csp == HTML_HEADERS["Content-Security-Policy"], path
        assert "data:" not in csp, f"{path}: CSP layar yang dinilai melonggar ke data:"
    for path in API_ROUTES:
        assert "data:" not in client.get(path).headers.get("content-security-policy", ""), path
    for path in asset_routes():
        csp = client.get(path).headers["content-security-policy"]
        assert csp == HTML_HEADERS["Content-Security-Policy"], f"{path}: aset ikut dilonggarkan"
    for response in (client.post("/kage"), client.get("/kage/nope"), client.get("/ui/nope")):
        csp = response.headers.get("content-security-policy", "")
        assert "data:" not in csp, f"halaman gagal {response.status_code} melonggar ke data:"


def test_no_response_this_app_can_send_ever_allows_unsafe_inline_or_unsafe_eval(
    client: TestClient,
) -> None:
    """Satu tes untuk SELURUH aplikasi: pelonggaran ADR-026 tidak membawa penumpang.

    `'unsafe-inline'`/`'unsafe-eval'` adalah dua token yang membuat sisa CSP tidak berarti, dan
    keduanya paling mudah masuk saat satu direktif lain sedang dilonggarkan "sedikit saja".
    """
    paths = (*JUDGED_SCREENS, *API_ROUTES, *asset_routes(), "/kage")
    for path in paths:
        csp = client.get(path).headers.get("content-security-policy", "")
        for forbidden in ("unsafe-inline", "unsafe-eval", "blob:", "http", "*"):
            assert forbidden not in csp, f"{path}: CSP melonggar ke {forbidden}"
    for response in (client.post("/ui"), client.post("/kage"), client.get("/ui/nope")):
        csp = response.headers.get("content-security-policy", "")
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp, response.status_code
    # Dan tidak ada token itu di dalam KONSTANTA-nya sendiri, termasuk yang dilonggarkan.
    for headers in (HTML_HEADERS, KAGE_HTML_HEADERS):
        policy = headers["Content-Security-Policy"]
        assert "unsafe-" not in policy, policy


def test_every_kage_asset_route_serves_a_closed_list_and_builds_no_path_from_the_request(
    client: TestClient,
) -> None:
    """Gambar, skrip vendor, dan gaya `/kage`: daftar TERTUTUP, seperti `/ui/fonts/{name}`.

    Yang diuji bukan hanya "nama yang benar menjawab 200" melainkan "nama yang salah tidak
    pernah menyentuh disk": `..%2f`, path absolut, dan nama font yang diminta sebagai gambar
    semuanya 404 — dan 404 di bawah `/ui` tetap halaman ber-banner, bukan JSON telanjang.
    """
    for name in ui_screens.IMAGE_NAMES:
        got = client.get(f"/ui/img/{name}")
        assert got.status_code == 200, name
        assert got.headers["content-type"] == "image/webp", name
        assert got.content == ui_screens.IMAGES[name]
    for name in ui_screens.VENDOR_NAMES:
        got = client.get(f"/ui/vendor/{name}")
        assert got.status_code == 200, name
        expected = "text/plain" if name.endswith(".txt") else "text/javascript"
        assert got.headers["content-type"].startswith(expected), name
    # MIT menuntut notisnya ikut TERDISTRIBUSI bersama kodenya (ADR-026 butir 8), jadi lisensi
    # three.js dilayani — bukan hanya dilacak git — dan banner aslinya tetap utuh di kepala
    # berkasnya. Keduanya diperiksa di sini karena keduanya mudah hilang saat aset dirapikan.
    licence = client.get("/ui/vendor/LICENSE.three.txt")
    assert "The MIT License" in licence.text and "three.js authors" in licence.text
    assert "SPDX-License-Identifier: MIT" in client.get("/ui/vendor/three.min.js").text[:400]
    css = client.get("/ui/kage.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")

    # Setiap `url(...)` yang disebut berkas gaya itu benar-benar dilayani: gaya yang menyebut
    # aset mati adalah 404 yang hanya terlihat di konsol browser, bukan di tes.
    for url in sorted(set(re.findall(r"url\('([^']+)'\)", css.text))):
        assert client.get(url).status_code == 200, url
    # Begitu juga setiap `<img src>` di badan halaman.
    for src in sorted(set(re.findall(r'<img src="([^"]+)"', client.get("/kage").text))):
        assert client.get(src).status_code == 200, src

    for bad in ("/ui/img/nope.webp", f"/ui/img/{ui_screens.FONT_NAMES[0]}", "/ui/vendor/app.js"):
        refused = client.get(bad)
        assert refused.status_code == 404, bad
        assert BANNER in refused.text, bad  # 404 di bawah /ui tetap LAYAR


def test_the_kage_page_never_hijacks_the_screen_that_is_judged(client: TestClient) -> None:
    """`/` tetap landing ADR-021 dan `/app` tetap Layar A; `/kage` berdiri sendiri.

    Ini bukan selera tata letak: halaman `/kage` tidak membaca store sama sekali, jadi menaruhnya
    di `/` akan menempatkan prosa hiasan — dan angka yang bukan pengukuran — di depan
    satu-satunya layar yang menjawab pertanyaan juri.
    """
    assert "KAGE" not in client.get("/app").text
    assert "/ui/vendor/" not in client.get("/").text
    assert client.get("/app").status_code == 200
    scenery = client.get("/api/routes").json()["scenery"]
    assert scenery["path"] == "/kage" and scenery["reads_store"] is False
