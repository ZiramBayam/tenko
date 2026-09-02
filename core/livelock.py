"""Kunci "pemanggil live": satu proses saja yang boleh memanggil tool RYO di mesin ini.

Alasan modul ini ada — pengaman kuota, bukan kenyamanan. Kunci API RYO punya satu token
bucket 60 panggilan/menit (api-facts §A). Dua proses yang memanggil live atas kunci yang sama
berarti 2×60/menit: persis cara tercepat menghabiskan kuota, dan sumber HTTP 429 yang sudah
terlihat di log perekam. Berkas PID saja tidak cukup — `scripts/daemons.sh` menyimpan pid
pembungkus `uv run`, bukan pid proses Python yang benar-benar memanggil RYO. `flock` dilepas
kernel saat proses berakhir, termasuk pada `kill -9`.

Dua nama publik, satu badan `flock` (:func:`_acquire`). Duplikasi kode pengaman pasti
melenceng, jadi satu-satunya pemanggilan `flock` di `core/` ada di berkas ini:

* :func:`acquire_single_instance_lock` — GAGAL KERAS (:class:`RecorderLockError`). Dipakai
  perekam: instance kedua tidak punya pekerjaan yang masuk akal, jadi ia keluar dengan kode 2.
* :func:`try_acquire` — GAGAL LUNAK (:class:`LockAttempt`, tanpa menunggu, tanpa melempar).
  Dipakai pemanggil yang masih berguna tanpa data segar: yang tidak memegang kunci WAJIB
  store-first murni (nol panggilan jaringan) dan menyatakan bukti yang tidak segar sebagai
  item `unspoken`, bukan menebak nilai. Kembaliannya membawa SEBAB (`held` vs `unusable`)
  karena pemanggil harus menyebut sebab yang benar, bukan satu kalimat untuk dua keadaan.

Path kunci, presedens tetap: argumen ``path`` > env ``RECORDER_LOCK_PATH`` >
``ROOT/logs/recorder.lock``. Tidak ada nama env lain; agen dan perekam berebut kunci yang
SAMA, karena yang dijaga adalah satu kuota bersama, bukan satu program.

**Berkas kunci tidak pernah dihapus, dan tidak pernah ditulisi dari jalur yang gagal.**
`flock` bekerja pada inode: menghapus berkasnya membuat proses berikutnya mengunci inode baru
sementara pemegang lama masih hidup — dua pemanggil live sekaligus, yaitu kegagalan yang mau
dicegah modul ini. `truncate()` + tulis pid terjadi HANYA setelah `flock` berhasil, sehingga
pemanggil yang kalah tidak mengubah isi maupun mtime berkas. `scripts/proc_status.py`
(stdlib-saja, sengaja tidak mengimpor `core.*`) bergantung pada sifat itu untuk membaca status
tanpa merusak kunci.

**Batas yang diakui:** `open("a+")` + `truncate()` MENGIKUTI symlink, jadi operator yang
menunjuk `RECORDER_LOCK_PATH` ke symlink milik orang lain akan menulis pid ke sasarannya.
Sama seperti sebelum modul ini ada (bukan regresi); penutupnya adalah `os.open(..., O_NOFOLLOW)`
dan itu perubahan desain tersendiri, bukan bagian dari pemindahan ini.

ADR-002: modul ini tidak mengimpor `preflight`/`agent`/`ui`/`grading`.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
from pathlib import Path
from typing import IO, NamedTuple

log = logging.getLogger("core.livelock")

ROOT = Path(__file__).resolve().parent.parent

#: Satu-satunya nama env untuk path kunci (dipakai juga oleh `scripts/proc_status.py`, yang
#: mendefinisikannya ulang secara lokal karena sengaja stdlib-saja).
ENV_LOCK_PATH = "RECORDER_LOCK_PATH"


#: Hasil :func:`try_acquire`. Ketiganya HARUS bisa dibedakan pemanggil: "dipegang perekam" dan
#: "kunci tidak bisa dipakai" sama-sama melarang panggilan live, tetapi menyebut sebab yang salah
#: di kartu panggilan sama saja dengan mengarang bukti. Nilai `held` sengaja sama dengan
#: `scripts/proc_status.py::LOCK_HELD` supaya operator membaca kata yang sama di dua tempat.
LOCK_ACQUIRED = "acquired"
LOCK_HELD = "held"
LOCK_UNUSABLE = "unusable"

#: errno yang benar-benar berarti "ada pemegang lain" pada `flock(LOCK_NB)`. Sisanya = kunci
#: tidak bisa dipakai, bukan kontensi — lihat `_acquire`. Daftar ini SENGAJA pendek dan diambil
#: dari `man 2 flock`, yang ERRORS-nya hanya EBADF, EINTR, EINVAL, ENOLCK, EWOULDBLOCK.
#: JANGAN menambahkan EACCES "supaya lengkap": EACCES adalah errno kontensi `fcntl(F_SETLK)`,
#: bukan `flock`, dan memasukkannya berarti melaporkan perekam hantu untuk kegagalan izin.
#: EINTR pun bukan kontensi di sini — dengan `LOCK_NB` tidak ada penantian yang bisa disela.
#: (EWOULDBLOCK == EAGAIN di Linux; keduanya tetap disebut agar niatnya terbaca, bukan
#: bergantung pada kebetulan platform.)
_HELD_ERRNOS = frozenset({errno.EWOULDBLOCK, errno.EAGAIN})


class LockAttempt(NamedTuple):
    """Hasil percobaan kunci: handle (atau None) DAN sebabnya.

    Sengaja bukan `IO[str] | None` telanjang: `None` tanpa sebab memaksa pemanggil menebak,
    dan tebakan itu akan muncul sebagai kalimat yang salah di kartu panggilan (mis. menuduh
    perekam memegang kuota padahal path kuncinya yang rusak). Bentuk tuple dipertahankan agar
    pemanggil bisa menulis ``handle, outcome = try_acquire()``.
    """

    handle: IO[str] | None
    #: Salah satu dari :data:`LOCK_ACQUIRED`, :data:`LOCK_HELD`, :data:`LOCK_UNUSABLE`.
    outcome: str


class RecorderLockError(Exception):
    """Pemanggil live lain sudah berjalan di mesin ini (atau kunci tidak bisa dibuka)."""


def _resolve_lock_path(path: str | Path | None) -> Path:
    """Presedens: argumen > env `RECORDER_LOCK_PATH` > `ROOT/logs/recorder.lock`."""
    raw = path if path is not None else os.environ.get(ENV_LOCK_PATH) or ROOT / "logs" / "recorder.lock"
    return Path(raw)


def _acquire(path: str | Path | None) -> tuple[IO[str] | None, str, str]:
    """Badan `flock` TUNGGAL: `(handle, outcome, pesan)`.

    Satu-satunya tempat `flock` dipanggil di `core/`, karena dua jalur pengaman yang mirip
    pasti melenceng. `pesan` adalah kalimat siap-log/siap-lempar (memuat path kunci, tidak
    pernah kredensial); `outcome` adalah sebab yang bisa dibedakan mesin.

    Urutan yang dijaga tes: buka → `flock(LOCK_EX|LOCK_NB)` → baru `truncate()` + tulis pid.
    Pemanggil yang gagal berhenti di langkah kedua, jadi ia tidak pernah menyentuh isi berkas.
    """
    lock_path = _resolve_lock_path(path)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
    except OSError as exc:
        return None, LOCK_UNUSABLE, f"tidak bisa membuka kunci {lock_path}: {exc}"
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if exc.errno not in _HELD_ERRNOS:
            # `flock` bisa gagal karena sebab yang BUKAN "ada pemegang": ENOLCK (kuota kunci
            # kernel habis), ENOSYS/EOPNOTSUPP (filesystem tanpa dukungan kunci — NFS lama,
            # beberapa overlay), EINTR yang dijadikan exception. Menyebutnya "perekam lain"
            # akan membuat pemanggil menuduh proses yang tidak ada.
            return None, LOCK_UNUSABLE, f"kunci {lock_path} tidak bisa dikunci: {exc}"
        return (
            None,
            LOCK_HELD,
            f"perekam lain sudah memegang {lock_path}; menjalankan dua perekam berarti "
            "dua kali laju panggilan atas satu kunci API",
        )
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
    except OSError:  # menulis pid hanya untuk kenyamanan; kuncinya sudah dipegang
        log.warning("kunci %s tidak bisa ditulisi; kunci tetap dipegang", lock_path)
    return handle, LOCK_ACQUIRED, ""


def acquire_single_instance_lock(path: str | Path | None = None) -> IO[str]:
    """Kunci `flock` non-blocking; instance kedua → :class:`RecorderLockError`.

    Dua perekam atas satu kunci API = dua token bucket = 2×60 panggilan/menit, yaitu cara
    tercepat menghabiskan kuota (dan sumber 429 yang sudah terlihat di log). Berkas PID saja
    tidak cukup: `scripts/daemons.sh` menyimpan pid pembungkus `uv run`, bukan pid proses
    Python yang benar-benar memanggil RYO. Kunci dilepas otomatis saat proses berakhir.
    """
    handle, _outcome, message = _acquire(path)
    if handle is None:
        raise RecorderLockError(message)
    return handle


def try_acquire(path: str | Path | None = None) -> LockAttempt:
    """Coba pegang kunci live TANPA menunggu dan TANPA melempar → :class:`LockAttempt`.

    Kembaliannya `(handle, outcome)`. Kontrak pemanggil (ini inti fungsinya, bukan detail):

    * :data:`LOCK_ACQUIRED` (`handle` bukan None) → proses ini pemegang kuota live: boleh
      memanggil tool RYO. Handle harus tetap terbuka selama panggilan live berlangsung dan
      ditutup sesudahnya (atau dibiarkan ditutup kernel saat proses berakhir); menutupnya
      lebih awal berarti melepas kunci sementara panggilan masih jalan.
    * :data:`LOCK_HELD` (`handle` None) → proses lain sedang memegang kuota.
    * :data:`LOCK_UNUSABLE` (`handle` None) → kunci tidak bisa dibuka/dipakai (path menunjuk
      direktori, izin ditolak, filesystem read-only). TIDAK ada pemegang; menyalahkan perekam
      di sini berarti mengarang sebab.

    **Kedua kegagalan sama-sama melarang live** (fail-closed: tanpa kunci yang terbukti
    dipegang, nol panggilan jaringan). Yang berbeda hanya SEBAB yang wajib diucapkan
    pemanggil. Pemanggil WAJIB store-first MURNI pada tick itu: keputusan hanya dari snapshot
    yang sudah ada di DB, bukti yang tidak segar dinyatakan sebagai item `unspoken` — bukan
    nilai default, bukan angka lama yang dipura-purakan segar — dan `reason` yang ditampilkan
    menyebut sebab yang benar sesuai `outcome`, bukan satu kalimat untuk dua keadaan berbeda.

    Jangan menunggu kunci: menunggu mengubah tugas cepat menjadi tugas yang menggantung
    tanpa batas. `LOCK_UNUSABLE` juga dicatat WARNING agar salah konfigurasi tidak lolos
    senyap — ia terlihat seperti "agen sopan" padahal artinya agen buta selamanya.
    """
    handle, outcome, message = _acquire(path)
    if handle is None:
        log.warning("kunci live tidak didapat (%s), wajib store-first: %s", outcome, message)
    return LockAttempt(handle, outcome)
