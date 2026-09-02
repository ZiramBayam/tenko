"""Tes TASKS 2.0a: `core/livelock.py` — satu jalur `flock` untuk semua pemanggil live.

Yang dijaga di sini bukan kenyamanan, melainkan kuota: dua proses yang memanggil tool RYO
atas satu kunci API berarti 2×60 panggilan/menit. Karena itu tesnya menuntut hal-hal yang
tidak kelihatan dari luar — pemanggil yang KALAH tidak menyentuh berkas kunci sama sekali
(isi dan `mtime_ns` sama persis), tidak menunggu, tidak melempar, dan tidak membocorkan fd.
"""

from __future__ import annotations

import ast
import errno
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import core.livelock as livelock
import core.recorder as recorder
from core.livelock import (
    ENV_LOCK_PATH,
    LOCK_ACQUIRED,
    LOCK_HELD,
    LOCK_UNUSABLE,
    RecorderLockError,
    acquire_single_instance_lock,
    try_acquire,
)

ROOT = Path(__file__).resolve().parent.parent

#: Pemegang kunci di proses LAIN. `flock` per-proses: kunci di proses yang sama tidak akan
#: pernah bentrok dengan dirinya sendiri, jadi bentrok yang sebenarnya hanya bisa diuji begini.
#: Umurnya SENGAJA pendek (:data:`_HOLD_S`): kalau suatu saat `LOCK_NB` hilang dari
#: `core/livelock.py`, tes yang memanggil di bawah kunci ini harus MERAH CEPAT, bukan
#: menggantung sampai job CI dianggap macet.
_HOLD_S = 20.0
_HOLDER = (
    "import fcntl, os, sys, time\n"
    "h = open(sys.argv[1], 'a+', encoding='utf-8')\n"
    "fcntl.flock(h.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "h.seek(0); h.truncate(); h.write(f'{os.getpid()}\\n'); h.flush()\n"
    "print('HELD', flush=True)\n"
    "time.sleep(float(sys.argv[2]))\n"
)

#: Batas waktu keras untuk SETIAP panggilan kunci di bawah kunci yang ditahan. Longgar untuk
#: mesin lambat, jauh lebih pendek daripada umur penahan: `flock` yang memblokir kena SIGALRM
#: dan tesnya gagal dalam hitungan detik (PEP 475: handler yang melempar tidak di-retry).
_CALL_TIMEOUT_S = 5.0


class LockCallTimeout(RuntimeError):
    """Batas waktu tes. SENGAJA bukan turunan `OSError`.

    `TimeoutError` adalah `OSError`, jadi ia akan tertelan `except OSError` di `_acquire` dan
    regresi `LOCK_NB` malah terlihat seperti "kunci dipegang" — kegagalan yang menyamar jadi
    hasil normal. Persis itu yang terjadi pada draf pertama tes ini.
    """


@pytest.fixture(autouse=True)
def fail_fast_instead_of_hanging() -> Iterator[None]:
    """Batas waktu keras untuk SETIAP tes di berkas ini — satu mekanisme, bukan per call site.

    Kalau `LOCK_NB` suatu saat hilang dari `core/livelock.py`, `flock` akan MEMBLOKIR: menunggu
    pemegang di proses lain, dan pada kontensi sesama-proses menunggu selamanya (dua `open()`
    = dua open file description, jadi proses itu memblokir dirinya sendiri tanpa ada yang akan
    melepas). Di CI itu terlihat sebagai job macet, bukan tes merah — kegagalan paling mahal
    untuk didiagnosis. SIGALRM mengubahnya menjadi merah dalam hitungan detik (PEP 475:
    handler yang melempar tidak di-retry). Autouse, karena tes berikutnya yang ditulis orang
    lain tidak boleh perlu mengingat aturan ini.

    Dua batas yang disebut apa adanya: (1) proteksi ini bergantung pada `core/livelock.py` dan
    tes di berkas ini TIDAK menumbuhkan `except Exception` lebar — badan seperti itu akan
    menelan :class:`LockCallTimeout` dan hang kembali menjadi senyap (hari ini nol `except
    Exception` di keduanya); (2) tes yang SAH berjalan >5 dtk di berkas ini akan merah karena
    batas ini, bukan karena cacat — marginnya besar hari ini (`pytest --durations` di berkas
    ini: tes terlama < 0,1 dtk, seluruh berkas < 1,5 dtk), jadi menaikkan
    :data:`_CALL_TIMEOUT_S` harus jadi keputusan sadar, bukan refleks.
    """

    def _fire(signum: int, frame: object) -> None:
        raise LockCallTimeout(f"tes kunci tidak selesai dalam {_CALL_TIMEOUT_S}s — LOCK_NB hilang?")

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, _CALL_TIMEOUT_S)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture
def held_lock(tmp_path: Path):
    """Berkas kunci yang benar-benar dipegang proses lain, sampai tes selesai."""
    lock_path = tmp_path / "recorder.lock"
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(lock_path), str(_HOLD_S)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "HELD", "proses penahan kunci gagal start"
        yield lock_path
    finally:
        proc.kill()
        proc.wait(timeout=10)
        if proc.stdout is not None:
            proc.stdout.close()


def _open_fd_count() -> int:
    """Jumlah fd terbuka (Linux/WSL; repo ini memang Linux, lihat docs/versions.md)."""
    return len(os.listdir("/proc/self/fd"))


# --- AC2: hanya SATU jalur flock di core/ ----------------------------------------------------
def test_core_has_exactly_one_flock_call_site() -> None:
    """AC2: cocok-teks `fcntl.flock` di seluruh `core/` harus menunjuk tepat satu berkas.

    Sengaja cocok-teks, bukan AST: inilah yang dijalankan AC (`grep -rn "fcntl.flock" core/`).
    Konsekuensinya prosa di `core/` tidak boleh menyebut string itu — komentar memakai
    "`flock`" saja. Bentuk yang lolos sama dengan tes berikutnya (lihat docstring-nya).
    """
    users = [
        path.relative_to(ROOT).as_posix()
        for path in sorted((ROOT / "core").rglob("*.py"))
        if "fcntl.flock" in path.read_text(encoding="utf-8")
    ]
    assert users == ["core/livelock.py"]


def test_only_livelock_imports_fcntl_in_core() -> None:
    """Penjaga AST atas impor `fcntl` polos — dan hanya itu, sengaja disebut apa adanya.

    Yang TIDAK tertangkap tes ini maupun tes cocok-teks di atasnya: `__import__("fcntl")`,
    `importlib.import_module("fcntl")`, dan `getattr(fcntl, "flo" + "ck")`. Ketiganya lolos.
    Penjaga ini menangkap jalan yang wajar (seseorang menulis `import fcntl` di modul core
    kedua), bukan penyamaran yang disengaja. Klaim yang lebih besar dari kemampuannya justru
    membuat pembaca berikutnya berhenti memeriksa.
    """
    importers = []
    for path in sorted((ROOT / "core").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "fcntl" for a in node.names):
                importers.append(path.relative_to(ROOT).as_posix())
            elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "fcntl":
                importers.append(path.relative_to(ROOT).as_posix())
    assert sorted(set(importers)) == ["core/livelock.py"]


# --- refactor tanpa perubahan perilaku -------------------------------------------------------
def test_recorder_reexports_the_same_objects() -> None:
    """`core.recorder` tetap pintu masuk historis; objeknya harus IDENTIK, bukan salinan."""
    assert recorder.acquire_single_instance_lock is acquire_single_instance_lock
    assert recorder.RecorderLockError is RecorderLockError
    assert recorder.ENV_LOCK_PATH is ENV_LOCK_PATH


def test_env_name_is_unchanged() -> None:
    """Tanpa nama env baru: `RECORDER_LOCK_PATH` tetap satu-satunya (scripts/proc_status.py ikut)."""
    assert ENV_LOCK_PATH == "RECORDER_LOCK_PATH"


def test_lock_path_precedence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """argumen > env > ROOT/logs/recorder.lock — persis seperti sebelum pemindahan."""
    monkeypatch.delenv(ENV_LOCK_PATH, raising=False)
    assert livelock._resolve_lock_path(None) == ROOT / "logs" / "recorder.lock"
    monkeypatch.setenv(ENV_LOCK_PATH, str(tmp_path / "dari-env.lock"))
    assert livelock._resolve_lock_path(None) == tmp_path / "dari-env.lock"
    assert livelock._resolve_lock_path(tmp_path / "argumen.lock") == tmp_path / "argumen.lock"
    # env kosong tidak boleh berarti "path kosong"
    monkeypatch.setenv(ENV_LOCK_PATH, "")
    assert livelock._resolve_lock_path(None) == ROOT / "logs" / "recorder.lock"


def test_hard_failure_message_is_unchanged(held_lock: Path) -> None:
    """Pesan exit-2 perekam dibaca operator di log; refactor tidak boleh mengubah katanya."""
    with pytest.raises(RecorderLockError) as excinfo:
        acquire_single_instance_lock(held_lock)
    assert str(excinfo.value) == (
        f"perekam lain sudah memegang {held_lock}; menjalankan dua perekam berarti "
        "dua kali laju panggilan atas satu kunci API"
    )


# --- try_acquire: jalur sukses ---------------------------------------------------------------
def test_try_acquire_returns_a_handle_and_records_the_pid(tmp_path: Path) -> None:
    lock_path = tmp_path / "free.lock"
    handle, outcome = try_acquire(lock_path)
    assert outcome == LOCK_ACQUIRED
    assert handle is not None
    try:
        assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())
    finally:
        handle.close()
    # setelah dilepas, pemanggil berikutnya boleh masuk
    second = try_acquire(lock_path)
    assert second.outcome == LOCK_ACQUIRED
    assert second.handle is not None
    second.handle.close()


def test_try_acquire_uses_the_env_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lock_path = tmp_path / "env.lock"
    monkeypatch.setenv(ENV_LOCK_PATH, str(lock_path))
    handle, outcome = try_acquire()
    assert outcome == LOCK_ACQUIRED
    assert handle is not None
    handle.close()
    assert lock_path.is_file()


def test_try_acquire_and_hard_acquire_contend_over_the_same_file(tmp_path: Path) -> None:
    """Satu badan `flock`: kunci yang dipegang `try_acquire` menolak perekam, dan sebaliknya."""
    lock_path = tmp_path / "shared.lock"
    handle, outcome = try_acquire(lock_path)
    assert outcome == LOCK_ACQUIRED
    assert handle is not None
    try:
        with pytest.raises(RecorderLockError):
            acquire_single_instance_lock(lock_path)
    finally:
        handle.close()
    held = acquire_single_instance_lock(lock_path)
    try:
        assert try_acquire(lock_path) == (None, LOCK_HELD)
    finally:
        held.close()


# --- AC3: yang KALAH tidak menunggu, tidak melempar, tidak menyentuh berkas -------------------
def test_try_acquire_returns_none_fast_when_another_process_holds_the_lock(held_lock: Path) -> None:
    start = time.monotonic()
    handle, outcome = try_acquire(held_lock)
    elapsed = time.monotonic() - start
    assert handle is None
    assert outcome == LOCK_HELD
    assert elapsed < 1.0, f"try_acquire menunggu {elapsed:.3f}s; agen harus store-first, bukan menggantung"


def test_failed_try_acquire_does_not_touch_the_lock_file(held_lock: Path) -> None:
    """`flock` advisory pada inode: pembaca yang truncate/hapus akan merusak kunci pemegangnya."""
    before_bytes = held_lock.read_bytes()
    before_stat = held_lock.stat()
    assert try_acquire(held_lock) == (None, LOCK_HELD)
    after_stat = held_lock.stat()
    assert held_lock.read_bytes() == before_bytes, "isi berkas kunci berubah oleh pemanggil yang gagal"
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns, "berkas kunci ditulisi/di-truncate"
    assert after_stat.st_ino == before_stat.st_ino, "inode berubah — flock jadi tidak berarti"
    assert after_stat.st_size == before_stat.st_size


def test_failed_hard_acquire_does_not_touch_the_lock_file(held_lock: Path) -> None:
    before_bytes = held_lock.read_bytes()
    before_stat = held_lock.stat()
    with pytest.raises(RecorderLockError):
        acquire_single_instance_lock(held_lock)
    after_stat = held_lock.stat()
    assert held_lock.read_bytes() == before_bytes
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert after_stat.st_ino == before_stat.st_ino


def test_failed_try_acquire_leaks_no_file_descriptor(held_lock: Path) -> None:
    """Agen memanggil ini tiap tick; fd yang bocor akan menghabiskan kuota fd dalam sehari.

    Penjaga LEMAH, disebut apa adanya: refcounting CPython menutup handle yang telanjur
    yatim lewat `__del__`, jadi tes ini tetap hijau meski `handle.close()` eksplisit dihapus
    dari jalur gagal. Yang benar-benar melihat perbedaan itu adalah menjalankan suite dengan
    `-W error::ResourceWarning`. Nilai tes ini: fd yang benar-benar TERTAHAN (mis. handle
    disimpan di variabel modul) tetap tertangkap.
    """
    before = _open_fd_count()
    for _ in range(50):
        assert try_acquire(held_lock) == (None, LOCK_HELD)
    assert _open_fd_count() == before


def test_try_acquire_returns_none_when_the_lock_cannot_be_opened(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Path salah = tidak ada kunci = tidak boleh live. Dicatat WARNING, tidak dilempar."""
    directory = tmp_path / "bukan-berkas"
    directory.mkdir()
    with caplog.at_level("WARNING", logger="core.livelock"):
        handle, outcome = try_acquire(directory)
    assert handle is None
    assert outcome == LOCK_UNUSABLE
    assert "store-first" in caplog.text


def test_try_acquire_does_not_create_a_lock_file_it_could_not_use(tmp_path: Path, held_lock: Path) -> None:
    """Pemanggil yang gagal tidak meninggalkan berkas baru di sekitar kunci."""
    before = sorted(p.name for p in held_lock.parent.iterdir())
    assert try_acquire(held_lock) == (None, LOCK_HELD)
    assert sorted(p.name for p in held_lock.parent.iterdir()) == before
    assert held_lock.is_file(), "berkas kunci pemegang tidak boleh hilang"


def test_held_and_unusable_are_distinguishable(tmp_path: Path, held_lock: Path) -> None:
    """Sebab yang salah di kartu = bukti karangan: "dipegang perekam" != "kunci rusak".

    Tanpa pembeda ini, agen yang path kuncinya rusak akan menuduh perekam memegang kuota —
    selamanya, tiap tick, tanpa seorang pun memegang apa pun.
    """
    broken = tmp_path / "sebuah-direktori"
    broken.mkdir()
    held = try_acquire(held_lock)
    unusable = try_acquire(broken)
    assert held.handle is None and unusable.handle is None, "keduanya tetap melarang live"
    assert held.outcome != unusable.outcome
    assert (held.outcome, unusable.outcome) == (LOCK_HELD, LOCK_UNUSABLE)


#: Sweep errno `flock`. `held` HANYA untuk EWOULDBLOCK/EAGAIN (`man 2 flock` ERRORS: EBADF,
#: EINTR, EINVAL, ENOLCK, EWOULDBLOCK). EACCES ada di daftar ini sebagai `unusable` dengan
#: sengaja: itu errno kontensi `fcntl(F_SETLK)`, BUKAN `flock` — pernah salah masuk ke
#: `_HELD_ERRNOS` sekali, dan barisnya di sini yang mencegahnya masuk lagi.
_ERRNO_OUTCOMES = (
    (errno.EWOULDBLOCK, LOCK_HELD),
    (errno.EAGAIN, LOCK_HELD),
    (errno.EACCES, LOCK_UNUSABLE),
    (errno.ENOLCK, LOCK_UNUSABLE),
    (errno.ENOSYS, LOCK_UNUSABLE),
    (errno.EOPNOTSUPP, LOCK_UNUSABLE),
    (errno.EINTR, LOCK_UNUSABLE),
    (errno.EBADF, LOCK_UNUSABLE),
    (errno.EINVAL, LOCK_UNUSABLE),
    (errno.EPERM, LOCK_UNUSABLE),
    (errno.EIO, LOCK_UNUSABLE),
    (errno.EROFS, LOCK_UNUSABLE),
)


@pytest.mark.parametrize(("number", "expected"), _ERRNO_OUTCOMES)
def test_only_contention_errnos_are_reported_as_held(
    number: int, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hanya EWOULDBLOCK/EAGAIN yang berarti "ada pemegang"; sisanya kunci yang tidak bisa dipakai.

    Filesystem tanpa dukungan kunci (NFS lama, sebagian overlay), kuota kunci kernel yang habis,
    izin ditolak, fd rusak: semuanya melempar OSError di titik yang sama. Menyebutnya kontensi
    membuat pemanggil menuduh proses yang tidak ada, dan operator mencari perekam hantu.
    """

    def refuse(fd: int, operation: int) -> None:
        raise OSError(number, os.strerror(number))

    monkeypatch.setattr(livelock.fcntl, "flock", refuse)
    lock_path = tmp_path / f"errno-{number}.lock"
    handle, outcome = try_acquire(lock_path)
    assert handle is None
    assert outcome == expected

    with pytest.raises(RecorderLockError) as excinfo:
        acquire_single_instance_lock(lock_path)
    message = str(excinfo.value)
    if expected == LOCK_HELD:
        assert "perekam lain sudah memegang" in message
    else:
        assert "perekam lain" not in message, "kegagalan non-kontensi tidak boleh menuduh perekam"
        assert "tidak bisa dikunci" in message


def test_outcomes_are_three_distinct_values() -> None:
    """Sentinel, bukan bool: pemanggil mem-branch atas nama, bukan atas "kosong/tidak kosong"."""
    assert len({LOCK_ACQUIRED, LOCK_HELD, LOCK_UNUSABLE}) == 3
    assert LOCK_HELD == "held", "kata yang sama dengan scripts/proc_status.py::LOCK_HELD"


def test_lock_attempt_unpacks_as_a_tuple(tmp_path: Path) -> None:
    """Bentuk tuple dipertahankan supaya pemanggil bisa `handle, outcome = try_acquire()`."""
    attempt = try_acquire(tmp_path / "tuple.lock")
    try:
        assert isinstance(attempt, tuple)
        assert len(attempt) == 2
        assert attempt[0] is attempt.handle and attempt[1] == attempt.outcome
    finally:
        assert attempt.handle is not None
        attempt.handle.close()


def test_try_acquire_documents_the_store_first_contract() -> None:
    """Kontraknya ada di docstring karena itulah yang dibaca pemanggil berikutnya (2.0b)."""
    doc = try_acquire.__doc__ or ""
    assert "store-first" in doc
    assert "unspoken" in doc
    assert "nol panggilan jaringan" in doc
    # sebab yang benar wajib diucapkan pemanggil, dan keduanya sama-sama melarang live
    assert "LOCK_HELD" in doc
    assert "LOCK_UNUSABLE" in doc
    assert "fail-closed" in doc


# --- ADR-002 -------------------------------------------------------------------------------
def test_livelock_does_not_import_upper_layers() -> None:
    source = (ROOT / "core" / "livelock.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="core/livelock.py")
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert imported & {"preflight", "agent", "ui", "grading"} == set()
