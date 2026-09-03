"""Tes `agent/ledger.py` — koneksi buku besar HARUS benar-benar tidak bisa menulis.

Berkas ini adalah pembayaran dua temuan security-reviewer 4 Sep 2026 yang keduanya dibuktikan
dengan tulisan/penutupan yang BERHASIL, bukan dengan dugaan:

* **TINGGI-3** — `f"file:{path}?mode=ro"` merakit URI dengan interpolasi tanpa escape, sehingga
  nama berkas yang sah tetapi memuat `?`/`#`/`&` menyuntikkan parameternya sendiri dan
  `mode=ro` bisa ditimpa. `INSERT` benar-benar berhasil lewat koneksi yang modul itu sebut
  baca-saja — dan itu membatalkan satu-satunya alasan SQL boleh hidup di luar `core/store.py`.
* **TINGGI-2** — `decisions.card_json` tidak dilindungi hash apa pun, tetapi dulu dialah yang
  menentukan `entry`/`stop`/`target` saat posisi ditutup.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent.ledger import LedgerError, _connect

SCHEMA = (
    "CREATE TABLE decisions (id INTEGER PRIMARY KEY, ts_utc TEXT, symbol TEXT, decision TEXT,"
    " blocking_json TEXT, snapshot_ids_json TEXT, params_hash TEXT, decision_hash TEXT,"
    " card_json TEXT, simulated INTEGER)"
)


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ledger-default.db"))


def _seed(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("name", ["evil.db?mode=rwc&x=", "note#1.db", "a%2fb.db", "plain.db"])
def test_a_ledger_connection_can_never_write_whatever_the_filename_is(tmp_path: Path, name: str) -> None:
    """Nama berkas adalah NAMA BERKAS, bukan sintaks URI."""
    path = tmp_path / name
    _seed(path)
    with _connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
        with pytest.raises(sqlite3.Error):
            conn.execute(
                "INSERT INTO decisions (id, ts_utc, symbol, decision, blocking_json,"
                " snapshot_ids_json, params_hash, decision_hash, card_json, simulated)"
                " VALUES (1,'x','SOL','GO','[]','[]','h','h','{}',0)"
            )
    # Berkas yang benar-benar dibaca adalah berkas yang kami tunjuk (bukan tetangganya).
    assert path.exists()


def test_query_only_is_a_second_layer(tmp_path: Path) -> None:
    path = tmp_path / "plain2.db"
    _seed(path)
    with _connect(path) as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1


def test_a_missing_file_is_an_honest_error(tmp_path: Path) -> None:
    with pytest.raises(LedgerError, match="baca-saja"), _connect(tmp_path / "nope.db") as conn:
        conn.execute("SELECT 1")
