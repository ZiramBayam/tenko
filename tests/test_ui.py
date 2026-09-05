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
* **UI tidak menulis dan tidak memanggil apa pun**: hash berkas DB sebelum/sesudah semua route
  identik, dan jalur klien RYO maupun narasi LLM dipasangi ranjau yang meledak bila disentuh.

Fixture memakai `tests/helpers_agent.build_store` (envelope RYO nyata dari `docs/samples`).
Satu-satunya tes yang menyentuh `data/ryo.db` adalah kelompok "DB hidup" di bawah, dan hanya
lewat route yang membukanya BACA-SAJA; berkasnya tidak pernah ditulis (dipatok tes hash).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import ui.app as ui_app
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
from core.store import Store
from preflight.params import DEFAULT_PARAMS, ITEM_ORDER
from preflight.skill import symbol_evidence, symbol_tools
from tests.helpers_agent import ANALYZE, CORE, DEEP, build_store
from ui.app import BANNER, TONE_FINDING, TONE_NO_EVIDENCE, TONE_NOT_ASSESSED, app

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
    """Kalau satu saja lupa, layar mengklaim uang sungguhan (docs/rules-compliance.md)."""
    paths = [
        "/",
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
    assert "tidak ada yang dibaca dari store" in body["error"]


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
    assert any("belum ada data (n=0)" in note for note in payload["notes"])


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
    assert any("generasi sebelumnya" in note for note in listed["notes"])


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
    assert any("dihapus" in note for note in body["notes"])  # satu kalimat penjelasan
    assert any("bilangan bulat" in note for note in body["notes"])
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
    assert any("tidak ada bukti" in note or "BUKAN verifikasi" in note for note in body["notes"])


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
    assert any("TIDAK menilainya sama sekali" in note for note in body["notes"])
    # Ketidaktahuan dikatakan sebagai ketidaktahuan: kalimatnya menolak DUA-DUANYA sekaligus.
    assert any("bukan pula temuan" in note for note in body["notes"])
    assert "temuan:" not in body["replay_label"]


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
    assert "temuan" in bad["replay_label"] and "temuan" not in stale["replay_label"]
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
    assert any(f"{body['n_warnings']} peringatan envelope" in note for note in body["notes"])


def test_the_watchlist_counts_the_warnings_it_does_not_print(skewed_client: TestClient) -> None:
    """Layar A tidak memuat teks peringatan, tetapi tetap membawa cacahnya (`n`), bukan nol."""
    entry = by_symbol(skewed_client.get("/api/watchlist").json())["BTC"]
    assert entry["n_warnings"] >= 1


def test_preflight_repeats_the_dropped_row_instead_of_swallowing_it(skewed_client: TestClient) -> None:
    """Route yang MENGHITUNG ULANG kartu memakai rantai yang sama, jadi ia juga menyebutnya."""
    body = skewed_client.get("/api/preflight/BTC").json()
    assert any("disagree" in line for line in body["warnings"])
    assert body["n_warnings"] == len(body["warnings"])
    assert any("peringatan envelope" in note for note in body["notes"])


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
    assert "tidak ada yang dibaca dari store" in response.json()["error"]


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
    assert any("tidak ada tool RYO yang dipanggil" in note for note in body["notes"])


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
    assert "tidak berbentuk seperti simbol" in body["error"]
    assert "market_overview" not in body["error"]
    assert "monitor_market_sentiment_shift" not in body["error"]
    assert "tidak ada satu pun bukti" not in body["error"]


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
    note = next(line for line in notes if "tool ber-simbol" in line)
    assert f"dari {len(symbol_tools('BTC'))} tool ber-simbol" in note
    assert "dari 3 tool ber-simbol" in note
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
    assert "TERBARU" in body["note"]  # cacah ≠ total tabel


def test_index_lists_the_routes_instead_of_being_a_landing_page(client: TestClient) -> None:
    body = client.get("/").json()
    assert "/api/watchlist" in body["routes"]
    assert "/api/decision/{id}" in body["routes"]
    assert body["calls_ryo_tools"] is False


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
    assert body["n_warnings"] == len(body["warnings"])
    assert body["n_warnings_total"] == n_rows
    assert any(f"{n_rows} baris panggilan" in note for note in body["notes"])
    assert any(f"{n_rows} peringatan envelope ada" in note for note in body["notes"])
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
    assert any("TIDAK menilainya sama sekali" in note for note in body["notes"])
    # Ketidaktahuan, bukan tuduhan: tidak satu pun kalimat mengaku memeriksa dan menemukan.
    assert not any("terbukti ditulis tanpa bukti" in note for note in body["notes"])


@pytest.mark.parametrize("decision_id", LIVE_ZERO_EVIDENCE_IDS)
def test_live_stale_params_rows_never_look_like_a_finding(live_client: TestClient, decision_id: int) -> None:
    """AC 0.14 (4): juri melihat `MISMATCH` dan `STALE_PARAMS` berdampingan; keduanya beda."""
    body = live_client.get(f"/api/decision/{decision_id}").json()
    assert "temuan" not in body["replay_label"]
    assert "tidak dinilai" in body["replay_label"]


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


def test_live_database_is_not_modified_by_any_route(live_client: TestClient) -> None:
    before = hashlib.sha256(LIVE_DB.read_bytes()).hexdigest()
    for path in ("/api/health", "/api/watchlist", "/api/decisions", f"/api/decision/{LIVE_LEGACY_ID}"):
        assert live_client.get(path).status_code == 200
    assert hashlib.sha256(LIVE_DB.read_bytes()).hexdigest() == before
