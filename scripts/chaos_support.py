"""Pembantu `scripts/chaos.sh` (TASKS 4.5) — stdlib saja, dijalankan sebagai subperintah.

Skrip chaos butuh empat hal yang tidak nyaman ditulis di bash: server fixture HTTPS yang
membalas 429 + `Retry-After`, penghitung koneksi TCP mentah, salinan SQLite yang aman diambil
saat perekam produksi sedang menulis, dan satu pemanggil `core.ryo_client.RyoClient` yang
mencatat berapa lama ia benar-benar menunggu.

Aturan yang dipegang berkas ini:

* **Nol dependency baru.** Sertifikat dibuat `scripts/chaos.sh` lewat `openssl` (sudah ada di
  mesin); di sini hanya `ssl`, `http.server`, `socket`, `sqlite3`.
* **Tidak pernah menyentuh `data/ryo.db` kecuali sebagai SUMBER baca-saja.** `db-copy` membuka
  sumber dengan `mode=ro` dan memakai `sqlite3.Connection.backup`, bukan `cp`: perekam
  produksi menulis ke berkas itu tiap jam dan salinan byte biasa bisa sobek di tengah
  transaksi. Tujuan salinan wajib berada di luar `data/`, dan `db-check --recover` - satu-satunya
  subperintah yang membuka DB baca-tulis - memakai penjaga yang sama (`evidence_refusal`).
* **Tidak ada kredensial yang dicetak.** `ryo-call` menerima kunci lewat env, meneruskannya ke
  :class:`core.ryo_client.RyoClient` (yang menyimpannya sebagai `Secret`), dan tidak pernah
  menuliskannya ke `--out` maupun ke log.
* **Server fixture hanya mendengarkan 127.0.0.1.** Ia menyajikan `docs/samples/*.json` apa
  adanya, jadi jawaban 200-nya adalah bentuk envelope yang sama dengan yang RYO kirim
  (api-facts §A) dan bukan karangan baru.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sqlite3
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Batas baca body request server fixture. Chaos memanggil dirinya sendiri, tetapi server yang
#: membaca `Content-Length` tanpa batas adalah cara termurah menggantungkan skrip chaos.
MAX_BODY_BYTES = 64 * 1024

log = logging.getLogger("chaos_support")


# --- util kecil ---------------------------------------------------------------------------
def _write_atomic(path: Path, text: str) -> None:
    """Tulis lalu ganti nama: `chaos.sh` mem-poll berkas ini, jangan sampai terbaca separuh."""
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _free_port() -> int:
    """Port TCP yang bebas SEKARANG. Ada balapan bawaan (port bisa direbut sebelum dipakai);
    dipakai hanya untuk skenario 'host mati', yang justru ingin port tanpa pendengar."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Counter:
    """Pencacah yang setiap kenaikannya langsung mendarat di berkas (chaos.sh membacanya)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._value = 0
        _write_atomic(self._path, "0")

    def bump(self) -> int:
        with self._lock:
            self._value += 1
            _write_atomic(self._path, str(self._value))
            return self._value


# --- penghitung TCP -----------------------------------------------------------------------
def cmd_tcp_counter(args: argparse.Namespace) -> int:
    """Dengarkan 127.0.0.1 dan cacah tiap koneksi masuk, lalu tutup tanpa bicara.

    Dipakai skenario "kredensial dicabut": yang harus dibuktikan bukan "tidak ada HTTP 200"
    melainkan **nol koneksi**. Jabat tangan TLS yang gagal pun tetap terlihat di sini, jadi
    perekam tidak bisa lolos dengan alasan "requestnya toh gagal".
    """
    counter = _Counter(Path(args.count_file))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(16)
        _write_atomic(Path(args.port_file), str(server.getsockname()[1]))
        while True:
            conn, _ = server.accept()
            counter.bump()
            conn.close()


# --- server fixture HTTPS -----------------------------------------------------------------
def _handler_class(state: dict[str, object]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *fmt_args: object) -> None:
            """Diamkan log bawaan: ia menulis ke stderr per request dan membanjiri chaos.log."""

        def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # nama metode diwajibkan BaseHTTPRequestHandler
            length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY_BYTES)
            if length:
                self.rfile.read(length)
            counter: _Counter = state["counter"]  # type: ignore[assignment]
            seen = counter.bump()
            n_429: int = state["n_429"]  # type: ignore[assignment]
            if seen <= n_429:
                payload = json.dumps({"error": "rate limited", "code": "RATE_LIMITED"}).encode()
                self._send(429, payload, {"Retry-After": str(state["retry_after"])})
                return
            self._send(200, state["sample"], {})  # type: ignore[arg-type]

    return Handler


def cmd_tls_server(args: argparse.Namespace) -> int:
    """HTTPS di 127.0.0.1: `--n-429` request pertama dijawab 429 + `Retry-After`, sisanya 200.

    Sertifikatnya dibuat `chaos.sh` dan hanya dipercaya oleh pemanggil chaos, jadi tidak ada
    jalur di mana klien produksi bisa tertipu memakai server ini. HTTPS-nya bukan hiasan:
    `RyoClient` MENOLAK `base_url` non-https supaya Bearer key tidak pernah lewat polos, jadi
    server fixture yang jujur harus TLS.
    """
    sample = Path(args.sample).read_bytes()
    state: dict[str, object] = {
        "counter": _Counter(Path(args.count_file)),
        "n_429": args.n_429,
        "retry_after": args.retry_after,
        "sample": sample,
    }
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=args.cert, keyfile=args.key)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(state))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    _write_atomic(Path(args.port_file), str(server.server_address[1]))
    server.serve_forever()
    return 0


# --- pemanggil RyoClient ------------------------------------------------------------------
def cmd_ryo_call(args: argparse.Namespace) -> int:
    """Panggil `market_overview` lewat `RyoClient` sungguhan dan catat berapa lama ia menunggu.

    Ini SATU-SATUNYA cara jujur menguji "429 + Retry-After": mesin retry, jitter, dan token
    bucket yang diuji harus yang dipakai perekam, bukan tiruan. Kunci dibaca dari env dan tidak
    pernah masuk `--out`; `verify` diarahkan ke CA chaos supaya sertifikat self-signed tidak
    memaksa siapa pun mematikan verifikasi TLS.
    """
    sys.path.insert(0, str(ROOT))
    import httpx

    from core.ryo_client import RyoClient, RyoError

    logging.basicConfig(
        filename=args.log,
        filemode="w",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    context = ssl.create_default_context(cafile=args.ca)
    result: dict[str, object] = {"tool": "market_overview"}
    started = time.monotonic()
    http_client = httpx.Client(verify=context, timeout=args.timeout)
    try:
        client = RyoClient(
            args.url,
            os.environ["CHAOS_RYO_KEY"],
            timeout=args.timeout,
            max_retries=args.max_retries,
            http_client=http_client,
        )
        envelope = client.market_overview()
        result["status"] = str(envelope.get("status"))
        result["outcome"] = "ok"
    except RyoError as exc:
        # Pesan `RyoError` sudah melewati redaksi `_server_text` di `core/ryo_client.py`.
        result["outcome"] = "ryo_error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 — chaos: apa pun yang lolos harus TERCATAT, bukan traceback
        result["outcome"] = "unexpected"
        result["error_type"] = type(exc).__name__
    finally:
        http_client.close()
    result["elapsed_s"] = round(time.monotonic() - started, 3)
    _write_atomic(Path(args.out), json.dumps(result, sort_keys=True))
    return 0


# --- sapuan preflight (jendela tanpa bukti) ------------------------------------------------
#: Kelima jam yang TASKS 0.23 laporkan melempar `TypeError: 'NoneType' object is not
#: subscriptable` — jam yang jatuh di dalam celah perekaman yang 0.8b ukur. Ditanam sebagai
#: konstanta, bukan dihitung, supaya probe ini tetap menanyakan pertanyaan yang SAMA walau
#: isi `data/ryo.db` berubah.
TASK_0_23_INSTANTS = (
    "2026-09-02T12:30",
    "2026-09-02T17:00",
    "2026-09-03T19:30",
    "2026-09-04T02:00",
    "2026-09-04T21:30",
)


def cmd_preflight_sweep(args: argparse.Namespace) -> int:
    """Panggil `preflight()` di banyak `now` dan cacah berapa yang MELEMPAR, bukan menjawab.

    Alasan skenario ini ada: `preflight()` berjanji "tidak pernah melempar untuk bukti yang
    jelek", dan `core/skill_cli.py` hanya menerjemahkan `PreflightError` menjadi envelope —
    exception jenis lain lolos ke terminal sebagai traceback mentah dan langsung melanggar AC
    4.5 "nol traceback tak tertangani". Yang dicacah karena itu bukan "GO/NO_GO" melainkan
    "melempar / tidak", dan tipe exception-nya disimpan apa adanya.

    DB dibuka baca-saja dan seharusnya sudah berupa SALINAN (lihat `db-copy`).
    """
    sys.path.insert(0, str(ROOT))
    import logging as _logging

    from core.store import Store
    from preflight.skill import preflight

    # Preflight menulis diagnostik per snapshot yang dilewati; ribuan panggilan berarti puluhan
    # ribu baris yang tidak menambah apa pun pada pertanyaan "apakah ia melempar?".
    _logging.disable(_logging.CRITICAL)

    from datetime import UTC, datetime, timedelta

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    instants: list[datetime] = [
        datetime.fromisoformat(text).replace(tzinfo=UTC) for text in TASK_0_23_INSTANTS
    ]
    anchor = datetime.fromisoformat(args.anchor).replace(tzinfo=UTC)
    steps = int(args.hours_back * 60 / args.step_min)
    instants += [anchor - timedelta(minutes=args.step_min * i) for i in range(steps)]

    store = Store(args.db, read_only=True)
    statuses: dict[str, int] = {}
    raises: list[dict[str, str]] = []
    calls = 0
    try:
        for symbol in symbols:
            for mode in ("swing", "scalp"):
                for now in instants:
                    calls += 1
                    try:
                        result = preflight(symbol, mode, store=store, now=now)
                    except Exception as exc:  # noqa: BLE001 — justru inilah yang sedang dicacah
                        raises.append(
                            {
                                "symbol": symbol,
                                "mode": mode,
                                "now": now.isoformat(),
                                "type": type(exc).__name__,
                                "message": str(exc)[:200],
                            }
                        )
                        continue
                    key = str(getattr(result, "status", "?"))
                    statuses[key] = statuses.get(key, 0) + 1
    finally:
        store.close()

    report = {
        "calls": calls,
        "raises": len(raises),
        "statuses": statuses,
        "named_instants": list(TASK_0_23_INSTANTS),
        "first_raises": raises[:5],
    }
    _write_atomic(Path(args.out), json.dumps(report, sort_keys=True))
    print(json.dumps(report, sort_keys=True))
    return 0


# --- SQLite -------------------------------------------------------------------------------
#: Store bukti repo ini. Perekam produksi menulis ke sini tiap jam dan isinya TIDAK BISA
#: dibuat ulang (ADR-006); tidak ada subperintah chaos yang boleh membukanya baca-tulis.
EVIDENCE_DB = ROOT / "data" / "ryo.db"


def _same_file(left: Path, right: Path) -> bool:
    """Dua path menunjuk berkas yang sama, termasuk lewat symlink dan HARDLINK.

    Sengaja sama persis dengan `core.recorder._same_file`: `resolve()` mengikuti symlink tetapi
    tidak menembus hardlink, jadi identitas inode diperiksa lebih dulu bila kedua berkas ada,
    dan perbandingan path menjadi cadangan untuk berkas yang belum ada.
    """
    try:
        if left.exists() and right.exists():
            lstat, rstat = left.stat(), right.stat()
            return lstat.st_ino == rstat.st_ino and lstat.st_dev == rstat.st_dev
    except OSError:  # pragma: no cover - stat gagal (izin, mount hilang)
        pass
    try:
        return left.resolve() == right.resolve()
    except OSError:  # pragma: no cover
        return False


def _ledger_rows(path: Path) -> int | None:
    """Berapa baris `decisions` di `path`; None bila tidak bisa dibaca sama sekali.

    Sinyal yang sama yang dipakai `core.recorder.fixture_refusal`: buku besar `decisions` hanya
    ditulis `agent.main`, jadi keberadaannya berarti berkas ini SUDAH store bukti - juga bila ia
    berada di luar `data/`.
    """
    if not path.exists():
        return 0
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        conn.execute("PRAGMA query_only=ON")
        return int(conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0])
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def evidence_refusal(path: Path, *, hot_journal_ok: bool = False) -> str | None:
    """Alasan menolak membuka `path` dengan koneksi yang BISA MENULIS, atau None bila aman.

    Tiga alasan yang saling bebas, disusun agar konsisten dengan `core.recorder.fixture_refusal`
    (temuan SEDANG tinjauan keamanan atas chaos): berkasnya adalah store bukti repo (identitas
    inode, jadi cwd lain, symlink, dan hardlink tertangkap); ia berada di dalam direktori bukti
    `ROOT/data`; atau ia memegang buku besar `decisions`.

    `hot_journal_ok=True` dipakai `db-check --recover`, dan HANYA melonggarkan alasan ketiga: DB
    yang baru saja kena `kill -9` meninggalkan `-journal` panas sehingga koneksi `mode=ro` tidak
    bisa membaca satu tabel pun - memperlakukan "tidak terbaca" sebagai "store bukti" akan
    membuat penjaga ini menolak justru satu-satunya kasus yang membuat `--recover` ada. Dua
    alasan pertama tidak pernah longgar, dan keduanya yang melindungi `data/ryo.db`.

    **Celah yang diakui, sama dengan yang sudah ditulis `core/recorder.py`:** store bukti kustom
    DI LUAR `ROOT/data` yang belum punya satu pun baris `decisions` tidak tertangkap. Chaos tidak
    menyimpang dari batas itu sendirian.
    """
    if _same_file(path, EVIDENCE_DB):
        return f"{path} adalah store bukti repo ini ({EVIDENCE_DB})"
    try:
        resolved_parents = path.resolve().parents
    except OSError:  # pragma: no cover
        return f"{path} tidak bisa dibaca path-nya, jadi ia tidak dianggap aman"
    if EVIDENCE_DB.parent in resolved_parents:
        return f"{path} berada di dalam direktori bukti {EVIDENCE_DB.parent}"
    ledger = _ledger_rows(path)
    if ledger is None and not hot_journal_ok:
        return f"{path} tidak bisa diperiksa isinya, jadi ia tidak dianggap aman"
    if ledger is not None and ledger > 0:
        return f"{path} memegang buku besar keputusan ({ledger} baris), jadi ia store bukti"
    return None


def cmd_db_copy(args: argparse.Namespace) -> int:
    """Salinan online: sumber dibuka `mode=ro`, tujuan wajib bukan store bukti.

    `cp` tidak dipakai karena perekam produksi menulis ke sumbernya; `backup()` mengambil
    salinan yang konsisten tanpa satu pun tulisan ke sumber. Tujuan yang SUDAH ADA ditolak tanpa
    kecuali: menimpa diam-diam adalah cara termurah kehilangan berkas yang bukan milik chaos, dan
    chaos sendiri selalu menyalin ke `mktemp -d` yang baru, jadi penjaga ini tidak pernah
    menghalangi pekerjaan yang sah. Bila memang perlu menyalin ulang: hapus tujuannya lebih dulu.
    """
    src = Path(args.src).resolve()
    dst = Path(args.dst).resolve()
    if dst == src:
        raise SystemExit(f"tujuan salinan menolak: {dst} menimpa sumbernya sendiri")
    if dst.exists():
        raise SystemExit(f"tujuan salinan menolak: {dst} sudah ada (hapus dulu bila memang mau)")
    refusal = evidence_refusal(dst)
    if refusal is not None:
        raise SystemExit(f"tujuan salinan menolak: {refusal}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return 0


def _table_count(conn: sqlite3.Connection, table: str) -> int | None:
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608 - nama tabel dari daftar tetap
    except sqlite3.Error:
        return None
    return int(row[0])


#: Berkas sampingan yang ditinggalkan SQLite saat prosesnya mati di tengah transaksi.
SIDECARS = ("-journal", "-wal", "-shm")


def cmd_db_check(args: argparse.Namespace) -> int:
    """Ringkasan JSON satu baris: ada/tidak, sidecar, `integrity_check`, cacah baris per tabel.

    **`--recover` ada karena chaos run 5 Sep 2026 hampir melaporkan kerusakan yang tidak ada.**
    `kill -9` di tengah transaksi meninggalkan `-journal` PANAS. Koneksi `mode=ro` tidak bisa
    memutar balik jurnal itu — ia butuh menulis — sehingga `PRAGMA integrity_check` gagal
    dengan `OperationalError: attempt to write a readonly database`. Membaca itu sebagai
    "DB rusak" adalah kesimpulan yang SALAH: koneksi baca-tulis memulihkannya dan menjawab
    `ok` dengan seluruh barisnya utuh. Jadi:

    * default tetap `mode=ro` (nol tulisan; dipakai untuk mem-poll DB yang penulisnya hidup);
    * `--recover` membuka baca-tulis, yang berarti SQLite memutar balik jurnal panas —
      persis yang dilakukan proses mana pun yang membuka DB itu sesudah crash.

    `sidecars` selalu dilaporkan supaya pembaca tahu apakah pemulihan memang diperlukan.
    """
    path = Path(args.db)
    if args.recover:
        # Koneksi baca-tulis = SQLite boleh memutar balik jurnal, dan membuka DB yang BELUM ADA
        # baca-tulis akan MEMBUATNYA. Dua-duanya tulisan, dan tulisan ke store bukti tidak pernah
        # boleh datang dari alat ukur - jadi penjaganya berdiri sebelum `exists()` diperiksa.
        # Penjaga yang sama dengan `db-copy` dan `core.recorder.fixture_refusal`.
        refusal = evidence_refusal(path, hot_journal_ok=True)
        if refusal is not None:
            raise SystemExit(f"--recover menolak: {refusal}")
    report: dict[str, object] = {
        "db": str(path),
        "exists": path.exists(),
        "sidecars": [s for s in SIDECARS if Path(str(path) + s).exists()],
        "recover": bool(args.recover),
    }
    if path.exists():
        target = str(path) if args.recover else f"file:{path}?mode=ro"
        conn = sqlite3.connect(target, uri=not args.recover)
        try:
            report["integrity"] = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            for table in args.table:
                report[f"rows_{table}"] = _table_count(conn, table)
        except sqlite3.Error as exc:
            report["integrity"] = f"sqlite_error:{type(exc).__name__}"
            report["integrity_detail"] = str(exc)[:120]
        finally:
            conn.close()
    print(json.dumps(report, sort_keys=True))
    return 0


def cmd_free_port(_args: argparse.Namespace) -> int:
    print(_free_port())
    return 0


# --- CLI ----------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chaos_support", description="pembantu scripts/chaos.sh")
    sub = parser.add_subparsers(dest="cmd", required=True)

    tcp = sub.add_parser("tcp-counter", help="dengarkan dan cacah koneksi TCP")
    tcp.add_argument("--port-file", required=True)
    tcp.add_argument("--count-file", required=True)
    tcp.set_defaults(func=cmd_tcp_counter)

    tls = sub.add_parser("tls-server", help="server fixture HTTPS 429/200")
    tls.add_argument("--port-file", required=True)
    tls.add_argument("--count-file", required=True)
    tls.add_argument("--cert", required=True)
    tls.add_argument("--key", required=True)
    tls.add_argument("--sample", required=True)
    tls.add_argument("--n-429", type=int, default=2)
    tls.add_argument("--retry-after", type=int, default=2)
    tls.set_defaults(func=cmd_tls_server)

    call = sub.add_parser("ryo-call", help="satu market_overview lewat RyoClient sungguhan")
    call.add_argument("--url", required=True)
    call.add_argument("--ca", required=True)
    call.add_argument("--out", required=True)
    call.add_argument("--log", required=True)
    call.add_argument("--max-retries", type=int, default=3)
    call.add_argument("--timeout", type=float, default=10.0)
    call.set_defaults(func=cmd_ryo_call)

    sweep = sub.add_parser("preflight-sweep", help="cacah preflight() yang MELEMPAR, bukan menjawab")
    sweep.add_argument("--db", required=True)
    sweep.add_argument("--out", required=True)
    sweep.add_argument("--anchor", required=True, help="ISO UTC; titik akhir sapuan mundur")
    sweep.add_argument("--symbols", default="SOL,BTC,ETH,BNB")
    sweep.add_argument("--hours-back", type=float, default=24.0)
    sweep.add_argument("--step-min", type=int, default=30)
    sweep.set_defaults(func=cmd_preflight_sweep)

    copy = sub.add_parser("db-copy", help="salinan online SQLite (sumber baca-saja)")
    copy.add_argument("--src", required=True)
    copy.add_argument("--dst", required=True)
    copy.set_defaults(func=cmd_db_copy)

    check = sub.add_parser("db-check", help="integrity_check + cacah baris")
    check.add_argument("--db", required=True)
    check.add_argument("--table", action="append", default=[])
    check.add_argument(
        "--recover",
        action="store_true",
        help="buka baca-tulis supaya jurnal panas sesudah kill -9 diputar balik (lihat cmd_db_check)",
    )
    check.set_defaults(func=cmd_db_check)

    port = sub.add_parser("free-port", help="port TCP bebas (untuk skenario 'host mati')")
    port.set_defaults(func=cmd_free_port)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
