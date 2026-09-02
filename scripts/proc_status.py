#!/usr/bin/env python3
"""Deteksi proses untuk `scripts/daemons.sh status` (TASKS 1.13).

Kenapa ada: `status` dulu hanya membaca `logs/recorder.pid` dan memanggil `kill -0`. Pada 1
Sep 2026 perekam live (pid 160582/160586) mati pukul 22:55 di tengah siklus — dibunuh dari
luar, nol ERROR di log — dan selama ~45 menit tidak ada yang tahu: jumlah snapshot beku di
689 sementara pid file masih menunjuk pid mati. Snapshot pasar ber-timestamp tidak bisa
dibuat ulang, jadi setiap jam yang lewat tanpa perekam hilang selamanya. Berkas ini
menjawab tiga pertanyaan yang tidak dijawab pid file:

* pid di dalam berkas itu **masih ada, dan benar-benar menjalankan daemon yang dimaksud**
  (pid didaur ulang; pid file yang menunjuk proses asing dihapus sebagai basi);
* `logs/recorder.lock` sedang **dipegang** seseorang atau tidak;
* ada proses `core.recorder` yang jalan **tanpa** pid file (yatim) — hanya disebut, tidak
  diapa-apakan.

Tiga aturan yang mengikat berkas ini, semuanya punya tes di `tests/test_daemons_status.py`:

* **Nol sinyal.** Status mendiagnosis, tidak mengintervensi. Liveness dibaca dari
  `/proc/<pid>/stat`, bukan `os.kill(pid, 0)` — sinyal 0 memang tidak dikirim ke proses,
  tetapi begitu `kill` ada di berkas ini, jarak ke `kill(pid, SIGTERM)` tinggal satu
  argumen. `signal` dan `subprocess` tidak diimpor sama sekali; tes AST menjaganya.
* **`logs/recorder.lock` TIDAK PERNAH dihapus, dibuat, atau ditulisi.** `flock` itu advisory
  pada *inode*: menghapus berkas kunci selagi pemegangnya hidup membuat instance berikutnya
  membuka inode baru dan mendapat kuncinya sendiri — dua perekam atas satu kunci API =
  2×60 panggilan/menit, persis yang dicegah `core/livelock.py::acquire_single_instance_lock`.
  Uji basi di sini non-destruktif: coba `flock(LOCK_EX|LOCK_NB)` lalu **segera lepas**;
  berhasil = tidak ada pemegang, dan itu saja yang dilaporkan. Yang dihapus HANYA
  `logs/<name>.pid` yang pid-nya tidak hidup.
* **Semua teks asal-luar di-escape** lewat :func:`safe_text` sebelum dicetak — baris log,
  `cmdline`, isi pid file. Alat yang dibuat supaya kebohongan terlihat tidak boleh bisa
  dibungkam oleh satu urutan ANSI di dalam teks yang dilaporkannya.
* **Stdlib saja, tanpa `core.*`.** Alat diagnostik harus jalan justru saat aplikasinya rusak
  atau venv-nya belum ada — dan `python3 scripts/daemons.sh status` dipanggil sebelum apa pun
  dipastikan sehat. Ditegakkan tes AST, sama seperti `scripts/backup.py`.

Tanpa `pgrep`/`flock(1)`/`ps`: keduanya tidak dijamin ada di mesin juri. Yang dipakai hanya
`/proc` dan `fcntl` dari stdlib.

Batas yang diakui (jangan dibaca lebih jauh daripada yang tertulis):

* **Pid file menyimpan pid pembungkus `uv run`, BUKAN pid anak Python yang benar-benar
  memanggil RYO.** Pada insiden 1 Sep, 160582 adalah pembungkus dan 160586 anaknya. Karena
  itu "JALAN (pid N)" di sini **tidak membuktikan** proses yang merekam masih hidup —
  pembungkus bisa hidup dengan anak yang sudah mati. Status sengaja TIDAK menelusuri pohon
  proses; sebagai gantinya anak itu muncul di baris "YATIM" (ia memang tidak ada di pid
  file), dan setiap pembungkus yang terdeteksi diberi baris `catatan:` yang menyebut batas
  ini terang-terangan. Bukti hidup-matinya perekam yang sebenarnya adalah **umur snapshot
  terbaru** dari `scripts/db_status.py`, bukan baris pid mana pun.
* Umur snapshot & ambang "> 2× interval" tidak dihitung di sini — itu milik
  `scripts/db_status.py`, satu-satunya bagian status yang membuka DB.
* Deteksi berbasis `cmdline`. Perekam yang dijalankan lewat pembungkus dengan cmdline lain
  (mis. supervisor yang meng-embed `core.recorder` sebagai library) tidak akan terlihat.
* `/proc` khusus Linux. Di sistem tanpa `/proc`, semua pid dilaporkan tidak terbaca —
  bukan "hidup".
* **FIFO pada `RECORDER_LOCK_PATH` membuat status MENGGANTUNG.** :func:`lock_state` memakai
  `os.open(..., O_RDONLY)`; membuka FIFO tanpa penulis memblokir sampai ada penulis, dan
  tidak ada timeout di sini. Ini satu-satunya jalur di berkas ini yang bisa membuat `status`
  berhenti tanpa mencetak apa pun. Kunci nyata adalah berkas biasa yang dibuat perekam;
  nilai `RECORDER_LOCK_PATH` datang dari operator, bukan dari luar.
* Pencocokan argv **membaca teks, tidak menjalankan parser Python**: `python3 -c "...impor
  aneh..." -m core.recorder` (argv memuat `-m core.recorder` tanpa modulnya benar-benar
  dijalankan) akan dilaporkan sebagai perekam. Perlu akses shell lokal untuk melakukannya,
  dan hasilnya hanya baris laporan palsu — status tidak mengirim sinyal ke pid mana pun.
* Sebaliknya, proses python yang hanya MENYENTUH berkas perekam sebagai argumen (mis.
  `python -m pytest core/recorder.py`, alat yang dijalankan lewat `python -m`) muncul sebagai
  YATIM hantu. Bentuk `-m core.recorder` lebih ketat; bentuk path sengaja dipertahankan supaya
  perekam yang dijalankan sebagai skrip tetap terlihat.
* `_tail` memakai `splitlines()`, yang juga memotong pada pemisah baris Unicode (U+2028 dkk.),
  jadi "baris terakhir" log bisa lebih pendek daripada yang dilihat `tail -1`.
* `ts_utc` bertipe BLOB di DB membuat `scripts/db_status.py` melempar `AttributeError`. Itu
  TIDAK diredam: `scripts/daemons.sh` mencetak traceback-nya ke stderr plus baris PERINGATAN,
  dan status tetap exit 0. Kolomnya TEXT di skema `core/store.py`.

Pakai:
    python3 scripts/proc_status.py --dir logs --name recorder --name agent --lock logs/recorder.lock
Exit: selalu 0 — status dipanggil skrip lain; peringatan disampaikan lewat teks di stdout,
bukan lewat exit code.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROC_ROOT = "/proc"
DEFAULT_NAMES = ("recorder", "agent")
ENV_LOCK_PATH = "RECORDER_LOCK_PATH"

RECORDER_MODULE = "core.recorder"
AGENT_MODULE = "agent.main"
PYTHON_PREFIX = "python"
UV_LAUNCHER = "uv"

#: Modul yang HARUS benar-benar dijalankan oleh pid di `logs/<name>.pid`. Pid file yang
#: pid-nya hidup tetapi menjalankan hal lain = pid daur-ulang, bukan daemon kita.
DAEMON_MODULES: dict[str, str] = {"recorder": RECORDER_MODULE, "agent": AGENT_MODULE}

CLIENT_LIVE = "live"
CLIENT_FIXTURE = "fixture"

LOCK_HELD = "held"
LOCK_FREE = "free"
LOCK_ABSENT = "absent"
LOCK_UNKNOWN = "unknown"

STALE_PID = "MATI (pid file basi)"
MISMATCH_PID = "TIDAK DIKENALI (pid hidup, bukan daemon ini)"
ORPHAN = "YATIM"

TAIL_LIMIT = 90
CMDLINE_LIMIT = 110
PID_TEXT_LIMIT = 40
TRUNCATED_MARK = "...(truncated)"


def safe_text(text: str, limit: int) -> str:
    """Escape setiap karakter tak tercetak menjadi ``\\uXXXX`` lalu potong ke `limit`.

    Semua teks di berkas ini berasal dari luar: baris terakhir `logs/*.log`, `cmdline`
    proses asing, dan isi pid file. Tanpa escape, satu baris log berakhiran
    ``\x1b[8m\x1b[30;40m`` (conceal + hitam-di-atas-hitam) membuat SEMUA baris sesudahnya —
    termasuk ``PERINGATAN: TIDAK ADA SNAPSHOT BARU`` — tidak terbaca di terminal, dan satu
    ``\n`` di argv mencetak baris palsu yang tampak seperti keluaran status. Alat yang
    dibuat supaya kebohongan terlihat tidak boleh bisa dibungkam oleh teks yang dilaporkannya.

    **Sengaja kembar-fungsi dengan `core.skill.clean_text` (`core/skill.py:91`), dan sengaja
    TIDAK mengimpornya.** TASKS 1.5b menyatukan sanitizer di `core/` karena arah impor
    `preflight` → `core` diizinkan; di sini tidak: `scripts/` adalah perkakas diagnostik yang
    harus jalan dengan `python3` polos justru ketika venv/paket aplikasi rusak (pola yang
    sama sudah dipilih untuk `scripts/backup.py`, ditegakkan tes AST). Yang dihindari 1.5b
    adalah duplikasi TANPA alasan tertulis; ini alasannya, dan `scripts/db_status.py`
    memakai fungsi yang SAMA (mengimpornya dari berkas ini) supaya tidak ada salinan ketiga.
    """
    parts: list[str] = []
    length = 0
    for char in text:
        piece = char if char.isprintable() else f"\\u{ord(char):04x}"
        if length + len(piece) > limit:
            return "".join(parts) + TRUNCATED_MARK
        parts.append(piece)
        length += len(piece)
    return "".join(parts)


@dataclass(frozen=True)
class RecorderProcess:
    """Satu proses yang cmdline-nya benar-benar menjalankan `core.recorder`."""

    pid: int
    argv: tuple[str, ...]
    client: str

    @property
    def command(self) -> str:
        return safe_text(" ".join(self.argv), CMDLINE_LIMIT)

    @property
    def is_wrapper(self) -> bool:
        """Peluncur seperti `uv run python -m core.recorder`: pid-nya bukan pemanggil RYO."""
        return bool(self.argv) and Path(self.argv[0]).name == UV_LAUNCHER


# --- /proc -------------------------------------------------------------------------------
def _proc_entry(pid: int, name: str, proc_root: str | Path = DEFAULT_PROC_ROOT) -> Path:
    return Path(proc_root) / str(pid) / name


def pid_is_alive(pid: int, proc_root: str | Path = DEFAULT_PROC_ROOT) -> bool:
    """True bila `/proc/<pid>` ada DAN prosesnya bukan zombie. Tanpa mengirim sinyal apa pun.

    Zombie (`state Z`) sengaja dihitung MATI: ia sudah berhenti bekerja dan hanya menunggu
    dipanen induknya. Melaporkannya "JALAN" persis kebohongan yang membuat insiden 1 Sep
    tidak terlihat selama 45 menit.
    """
    if pid <= 0:
        return False
    try:
        text = _proc_entry(pid, "stat", proc_root).read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, NotADirectoryError, ProcessLookupError):
        return False
    except OSError:
        return False
    _, _, rest = text.rpartition(")")
    state = rest.strip().split(" ", 1)[0] if rest.strip() else ""
    return state != "Z"


def read_cmdline(pid: int, proc_root: str | Path = DEFAULT_PROC_ROOT) -> tuple[str, ...] | None:
    """Argv proses, atau None bila prosesnya sudah hilang / tidak bisa dibaca."""
    try:
        raw = _proc_entry(pid, "cmdline", proc_root).read_bytes()
    except (FileNotFoundError, NotADirectoryError, PermissionError, ProcessLookupError):
        return None
    except OSError:
        return None
    parts = [p.decode("utf-8", "replace") for p in raw.split(b"\0")]
    while parts and parts[-1] == "":
        parts.pop()
    return tuple(parts)


def _is_python(argv0: str) -> bool:
    name = Path(argv0).name
    return name.startswith(PYTHON_PREFIX) or name == UV_LAUNCHER


def runs_module(argv: list[str] | tuple[str, ...], module: str) -> bool:
    """True hanya bila cmdline BENAR-BENAR menjalankan `module` (mis. `core.recorder`).

    Cocokkan struktur argv, bukan substring: `grep -rn core.recorder`, `vim core/recorder.py`,
    dan shell `bash -c '... core.recorder ...'` (termasuk proses yang menjalankan status ini
    sendiri) semuanya memuat teks yang sama tetapi tidak merekam apa pun. Bentuk yang dikenali:
    `-m core.recorder`, `-mcore.recorder`, dan path skrip `.../core/recorder.py` — masing-masing
    dengan argv[0] berupa python atau peluncur `uv`.
    """
    args = list(argv)
    if not args or not _is_python(args[0]):
        return False
    script_parts = tuple(module.split(".")[:-1]) + (module.split(".")[-1] + ".py",)
    for i, item in enumerate(args[1:], start=1):
        if item == "-m" and i + 1 < len(args) and args[i + 1] == module:
            return True
        if item == f"-m{module}":
            return True
        parts = Path(item).parts
        if len(parts) >= len(script_parts) and parts[-len(script_parts) :] == script_parts:
            return True
    return False


def looks_like_recorder(argv: list[str] | tuple[str, ...]) -> bool:
    """Alias khusus perekam; pemindaian yatim hanya peduli `core.recorder`."""
    return runs_module(argv, RECORDER_MODULE)


def client_kind(argv: list[str] | tuple[str, ...]) -> str:
    """`--client fixture` → fixture; selain itu live (default argparse `core.recorder`)."""
    args = list(argv)
    for i, item in enumerate(args):
        if item == "--client" and i + 1 < len(args):
            return CLIENT_FIXTURE if args[i + 1] == CLIENT_FIXTURE else CLIENT_LIVE
        if item.startswith("--client="):
            return CLIENT_FIXTURE if item.split("=", 1)[1] == CLIENT_FIXTURE else CLIENT_LIVE
    return CLIENT_LIVE


def scan_recorders(
    proc_root: str | Path = DEFAULT_PROC_ROOT,
    exclude: set[int] | frozenset[int] = frozenset(),
) -> list[RecorderProcess]:
    """Semua proses `core.recorder` di `/proc`, kecuali pid pada `exclude` dan proses ini.

    Proses yang menghilang di tengah pemindaian dilewati diam-diam: itu keadaan normal,
    bukan kesalahan.
    """
    root = Path(proc_root)
    try:
        entries = sorted(int(e.name) for e in root.iterdir() if e.name.isdigit())
    except OSError:
        return []
    skip = set(exclude) | {os.getpid(), os.getppid()}
    found: list[RecorderProcess] = []
    for pid in entries:
        if pid in skip:
            continue
        argv = read_cmdline(pid, proc_root)
        if argv is None or not looks_like_recorder(argv):
            continue
        found.append(RecorderProcess(pid=pid, argv=argv, client=client_kind(argv)))
    return found


# --- pid file ----------------------------------------------------------------------------
def read_pid_file(path: str | Path) -> int | None:
    """Pid di dalam berkas, atau None bila berkasnya hilang/kosong/tidak masuk akal.

    None berarti "tidak ada pid yang bisa dipercaya", bukan "pid 0" — sama seperti di
    seluruh proyek ini, nilai yang tidak diketahui tidak pernah menjadi angka.
    """
    text = _read_text(path).strip()
    if not text.isdigit():
        return None
    value = int(text)
    if value <= 0 or value > 2**31:
        return None
    return value


def _read_text(path: str | Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _tail(path: str | Path, limit: int = TAIL_LIMIT) -> str:
    """Baris terakhir log, MENTAH; pemanggil wajib melewatkannya ke :func:`safe_text`."""
    lines = _read_text(path).splitlines()
    last = lines[-1] if lines else ""
    return last[:limit]


def _argv0_name(argv: tuple[str, ...] | None) -> str:
    """Basename argv[0], atau "" bila cmdline kosong.

    Kernel thread (`[kworker/*]`) punya `cmdline` KOSONG. `Path(argv[0])` tanpa penjagaan ini
    melempar `IndexError` yang mematikan SELURUH keluaran status — baris kunci dan pemindaian
    yatim ikut hilang justru saat status paling dibutuhkan.
    """
    return Path(argv[0]).name if argv else ""


def check_daemon(
    name: str,
    pid_path: str | Path,
    log_path: str | Path,
    proc_root: str | Path = DEFAULT_PROC_ROOT,
) -> tuple[list[str], int | None]:
    """Baris status satu daemon + pid-nya bila hidup DAN identitasnya cocok.

    "Pid itu hidup" tidak cukup: pid didaur ulang. Proses lain yang kebetulan mewarisi pid
    perekam membuat status mencetak "JALAN", dan `start_one` di `scripts/daemons.sh` melihat
    pid hidup lalu memutuskan "sudah jalan" — perekam tidak pernah menyala lagi, persis
    insiden 1 Sep tetapi kali ini dengan status yang ikut membenarkannya. Karena itu `cmdline`
    pid dari pid file diverifikasi terhadap :data:`DAEMON_MODULES`.

    **Arah gagalnya disengaja: ke "bukan JALAN".** Bentuk perintah sah yang tidak dikenali
    `runs_module` (peluncur selain python/`uv`, perekam yang di-embed sebagai library, atau
    proses yang `cmdline`-nya tidak bisa dibaca karena beda pemilik/namespace) TIDAK akan
    dilaporkan JALAN. Laporan "tidak jalan" yang palsu memancing operator menyalakan perekam
    kedua — dan itu aman, karena yang menolak instance kedua adalah `flock` di
    `core/livelock.py::acquire_single_instance_lock`, bukan pid file. Sebaliknya "JALAN" palsu
    membuat siapa pun berhenti memeriksa.

    **Penghapusan pid file HANYA untuk pid yang benar-benar tidak hidup** (atau isi berkas
    yang bukan pid sama sekali). Pid yang MASIH HIDUP tetapi identitasnya tidak cocok membuat
    berkasnya DIPERTAHANKAN: `stop` di `scripts/daemons.sh` bersyarat `[ -f logs/<name>.pid ]`,
    jadi menghapusnya akan membuat proses hidup itu tidak bisa lagi dihentikan lewat
    `make daemons ARGS=stop` — status yang mendiagnosis tidak boleh mencabut kendali operator.
    Ini juga persis bunyi AC 1: yang dibersihkan adalah pid file yang pid-nya TIDAK hidup.

    **Batas yang diakui:** pemindaian yatim memakai matcher yang SAMA (`looks_like_recorder`
    → :func:`runs_module`), jadi daemon dengan bentuk perintah yang tidak dikenali tidak akan
    muncul sebagai YATIM juga. Satu-satunya jejaknya adalah baris `TIDAK DIKENALI` ini —
    karena itu pid-nya disebut eksplisit di situ.

    Efek samping yang DISENGAJA dan satu-satunya di berkas ini: `logs/<name>.pid` yang pid-nya
    tidak hidup akan dihapus. Berkas kunci tidak pernah disentuh.
    """
    pid_file = Path(pid_path)
    if not pid_file.exists():
        return ([f"{name}: mati (tidak ada {pid_file})"], None)

    pid = read_pid_file(pid_file)
    if pid is None:
        raw = safe_text(_read_text(pid_file), PID_TEXT_LIMIT)
        removed = _remove(pid_file)
        return (
            [f"{name}: {STALE_PID} — isi {pid_file} ({raw!r}) bukan pid yang terbaca; {removed}"],
            None,
        )
    if not pid_is_alive(pid, proc_root):
        removed = _remove(pid_file)
        return (
            [f"{name}: {STALE_PID} — pid {pid} tidak ada lagi di {proc_root}; {removed}"],
            None,
        )

    argv = read_cmdline(pid, proc_root)
    module = DAEMON_MODULES.get(name)
    if module is not None and (argv is None or not runs_module(argv, module)):
        shown = safe_text(" ".join(argv), CMDLINE_LIMIT) if argv else "cmdline tidak terbaca/kosong"
        return (
            [
                f"{name}: {MISMATCH_PID} — pid {pid} hidup tetapi BUKAN {module} ({shown}); "
                f"{name} TIDAK berjalan. {pid_file} SENGAJA tidak dihapus justru karena pid {pid} "
                f"masih hidup: menghapusnya membuat `daemons.sh stop` tidak bisa lagi "
                f"menghentikan pid {pid}. Periksa pid {pid} lalu hentikan sendiri bila memang asing"
            ],
            None,
        )

    lines = [f"{name}: JALAN (pid {pid}) — {safe_text(_tail(log_path), TAIL_LIMIT)}"]
    if module is None:
        lines.append(
            f"catatan: modul untuk daemon {name!r} tidak dikenal, jadi identitas pid {pid} "
            "TIDAK diverifikasi — angka itu hanya berarti ada proses hidup"
        )
    if _argv0_name(argv) == UV_LAUNCHER:
        lines.append(
            f"catatan: pid {pid} adalah pembungkus `uv run`; pid anak Python yang benar-benar "
            "memanggil RYO tidak dicatat di pid file, jadi JALAN di sini TIDAK membuktikan "
            "anaknya hidup — periksa umur snapshot di bawah"
        )
    return (lines, pid)


def _remove(path: Path) -> str:
    try:
        path.unlink()
    except OSError as exc:
        return f"{path} GAGAL dihapus ({exc})"
    return f"{path} dihapus"


# --- kunci -------------------------------------------------------------------------------
def lock_state(path: str | Path) -> str:
    """Ada pemegang `flock` atau tidak — tanpa menghapus, membuat, atau menulisi berkasnya.

    Berkas dibuka RDONLY (tidak pernah `O_CREAT`), kunci diambil non-blocking lalu SEGERA
    dilepas: probe yang menahan kunci akan menolak perekam yang kebetulan start sedetik
    kemudian.
    """
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except FileNotFoundError:
        return LOCK_ABSENT
    except OSError:
        return LOCK_UNKNOWN
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return LOCK_HELD
        except OSError:
            return LOCK_UNKNOWN
        fcntl.flock(fd, fcntl.LOCK_UN)
        return LOCK_FREE
    finally:
        os.close(fd)


def lock_line(path: str | Path) -> str:
    state = lock_state(path)
    if state == LOCK_HELD:
        return (
            f"kunci: {path} dipegang proses lain — ada yang memegang kunci (biasanya perekam), "
            "tetapi status tidak membuktikan proses mana; berkas tidak disentuh"
        )
    if state == LOCK_FREE:
        return (
            f"kunci: {path} basi/kosong — tidak ada pemegang, jadi tidak ada perekam yang "
            "berjalan; berkas SENGAJA tidak dihapus (flock advisory pada inode)"
        )
    if state == LOCK_ABSENT:
        return f"kunci: {path} tidak ada (perekam belum pernah start di sini)"
    return f"kunci: {path} tidak bisa diperiksa (izin/OS) — jangan disimpulkan hidup atau mati"


# --- CLI ---------------------------------------------------------------------------------
def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="proc_status.py",
        description="Status proses perekam/agen: pid basi, kunci, dan proses yatim. Tanpa sinyal.",
    )
    parser.add_argument("--dir", default="logs", help="direktori pid & log (default: logs)")
    parser.add_argument("--name", action="append", default=None, help="nama daemon (boleh berulang)")
    parser.add_argument("--lock", default=None, help=f"path kunci (default: env {ENV_LOCK_PATH})")
    parser.add_argument("--proc-root", default=DEFAULT_PROC_ROOT, help="root /proc (untuk tes)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    directory = Path(args.dir)
    names = args.name or list(DEFAULT_NAMES)
    lock_path = args.lock or os.environ.get(ENV_LOCK_PATH) or str(directory / "recorder.lock")

    alive: set[int] = set()
    for name in names:
        lines, pid = check_daemon(name, directory / f"{name}.pid", directory / f"{name}.log", args.proc_root)
        for line in lines:
            print(line)
        if pid is not None:
            alive.add(pid)

    print(lock_line(lock_path))

    orphans = scan_recorders(proc_root=args.proc_root, exclude=alive)
    if not orphans:
        print(f"proses {RECORDER_MODULE}: tidak ada yang jalan di luar pid file")
    for proc in orphans:
        print(f"proses {RECORDER_MODULE}: {ORPHAN} pid {proc.pid} ({proc.client}) — {proc.command}")
    if orphans:
        print(
            "catatan: anak dari pembungkus `uv run` juga muncul sebagai YATIM — status sengaja "
            "TIDAK menelusuri pohon proses; cocokkan pid-nya dengan baris JALAN di atas sebelum "
            "menyimpulkan ada perekam kedua. Tidak ada sinyal yang dikirim ke pid mana pun."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
