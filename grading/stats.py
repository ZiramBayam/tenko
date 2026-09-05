"""Selang kepercayaan Wilson, ditulis tangan (stdlib `math`) — TASKS 3.3.

Kenapa ditulis tangan: ADR-001 (Python murni) + `docs/versions.md` tidak memin scipy/statsmodels,
dan menambah dependensi hanya untuk satu rumus tertutup adalah biaya yang tidak dibayar
manfaatnya. Rumusnya dua baris; yang panjang di modul ini adalah alasan **berapa `n` yang boleh
dimasukkan ke rumus itu**, dan itulah bagian yang benar-benar menentukan apakah angkanya jujur.

`n` yang jujur: cacah amatan != cacah peristiwa
-----------------------------------------------
Wilson mengandaikan `n` percobaan Bernoulli **saling bebas**. Di repo ini amatannya TIDAK bebas,
dan besarnya ketergantungan itu sudah terukur (security-reviewer atas TASKS 2.1b):
`preflight.skill.tool_args` memberi ``{}`` untuk ``market_overview`` DAN
``monitor_market_sentiment_shift``, sehingga keempat simbol inti berbagi satu ``args_hash``
(``44136fa355b3``) dan membaca **baris yang sama**. Item yang hanya membaca kedua tool itu
(3, 4, 8) karena itu mencacah satu peristiwa pasar sampai 4x: pada tabel veto, 32 "merah" item 8
lahir dari **8** snapshot berbeda di dalam satu jendela 8 jam.

Memberi `n=32` ke Wilson padahal peristiwanya 8 akan **mempersempit** selangnya kira-kira
sebanding akar rasio itu — angka agregat yang tampak yakin justru karena buktinya dihitung
berulang. Itu persis dosa yang produk ini ada untuk mencegah. Karena itu:

* :func:`observed_rate` menerima **dua** cacah: `n` (amatan/trade) dan `events` (peristiwa bukti
  berbeda). Keduanya selalu ikut ke keluaran; tidak ada satu pun jalur yang mencetak `k/n` tanpa
  `events` di sebelahnya.
* Ukuran sampel efektif :math:`n_{eff} = \\min(n, events)`. Itu batas ATAS informasi bebas yang
  bisa dimiliki himpunan ini, bukan taksiran halus: bila 5 trade lahir dari 2 pembacaan bukti,
  yang benar-benar diamati adalah 2 kali dunia berkata sesuatu.
* Selang dihitung atas cacah yang **dikempiskan** ke skala itu (:math:`k_{eff} = k \\cdot
  n_{eff}/n`, dibulatkan). Titik estimasinya tetap `k/n` — mengempiskan titik estimasi akan
  mengubah jawabannya, bukan hanya ketidakpastiannya.
* Gerbang :data:`MIN_N` berlaku atas :math:`n_{eff}`, **bukan** atas `n`. Menurunkan ambang 30
  supaya ada angka yang keluar adalah hal yang modul ini ada untuk menolak.

Batas yang diakui
-----------------
* :math:`\\min(n, events)` adalah koreksi **kasar**. Koreksi yang benar (efek desain klaster)
  butuh korelasi intra-klaster yang tidak bisa ditaksir dari `n_eff` sekecil ini. Yang dipilih
  di sini konservatif ke arah yang aman: ia tidak pernah membuat selangnya lebih sempit
  daripada cacah peristiwa membolehkan.
* Wilson tetap selang **aproksimasi normal**; untuk `n_eff` kecil ia lebih baik daripada Wald,
  tetapi tidak eksak. Karena gerbang 30 harus lewat dulu, ini tidak pernah dipakai pada `n` yang
  membuat aproksimasinya berbahaya.
* Modul ini tidak tahu apa-apa tentang keputusan. Ia tidak diimpor `preflight/` maupun `agent/`
  (ADR-003): mengukur tidak boleh bisa memutus.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "MIN_N",
    "Z_95",
    "GradingStatsError",
    "Interval",
    "ObservedRate",
    "observed_rate",
    "wilson_interval",
]


class GradingStatsError(Exception):
    """Cacah yang tidak mungkin (n<0, k>n, z<=0) — dilempar, bukan dibulatkan diam-diam."""


#: Ambang spec §6: "Ditampilkan hanya dengan n; n < 30 → 'belum cukup data'". Konstanta, bukan
#: parameter, karena satu-satunya alasan mengubahnya adalah keinginan agar ada angka yang keluar.
MIN_N = 30

#: z untuk selang 95 % dua sisi. Ditulis apa adanya supaya tidak perlu fungsi kuantil normal.
Z_95 = 1.959963984540054


@dataclass(frozen=True, slots=True)
class Interval:
    """Selang tertutup [low, high] pada skala proporsi, plus z yang memproduksinya."""

    low: float
    high: float
    z: float

    def as_dict(self) -> dict[str, float]:
        return {"low": self.low, "high": self.high, "z": self.z}


def wilson_interval(k: int, n: int, z: float = Z_95) -> Interval:
    """Selang skor Wilson untuk `k` sukses dari `n` percobaan.

    Rumus (Wilson 1927), ditulis apa adanya supaya bisa diperiksa mata::

        center = (k + z^2/2) / (n + z^2)
        half   = z/(n + z^2) * sqrt(k*(n-k)/n + z^2/4)

    Raises:
        GradingStatsError: `n <= 0`, `k` di luar [0, n], atau `z <= 0`. `n=0` BUKAN selang
            [0,1]: tidak ada amatan berarti tidak ada pernyataan, dan memaksanya jadi selang
            akan menyamarkan "belum ada data" sebagai "bisa apa saja".
    """
    if isinstance(k, bool) or isinstance(n, bool) or not isinstance(k, int) or not isinstance(n, int):
        raise GradingStatsError(f"k dan n harus int, bukan {type(k).__name__}/{type(n).__name__}")
    if n <= 0:
        raise GradingStatsError(f"n harus > 0 untuk sebuah selang, bukan {n}")
    if not 0 <= k <= n:
        raise GradingStatsError(f"k={k} harus berada di [0, {n}]")
    if not isinstance(z, (int, float)) or isinstance(z, bool) or not math.isfinite(z) or z <= 0:
        raise GradingStatsError(f"z harus bilangan berhingga > 0, bukan {z!r}")

    denominator = n + z * z
    center = (k + z * z / 2.0) / denominator
    spread = z / denominator * math.sqrt(k * (n - k) / n + z * z / 4.0)
    # Penjepitan hanya menutup galat pembulatan floating point di ujung (k=0 / k=n); tanpa itu
    # sebuah selang bisa tercetak sebagai -0.0000000001 dan terbaca sebagai proporsi negatif.
    return Interval(low=max(0.0, center - spread), high=min(1.0, center + spread), z=float(z))


@dataclass(frozen=True, slots=True)
class ObservedRate:
    """`k` dari `n` amatan yang lahir dari `events` peristiwa bukti berbeda.

    `interval` adalah None BILA DAN HANYA BILA `enough` False — tidak ada jalur yang memberi
    selang tanpa `n_effective >= MIN_N`, dan tidak ada jalur yang memberi `enough` tanpa selang.
    """

    k: int
    n: int
    events: int
    n_effective: int
    k_effective: int
    interval: Interval | None

    @property
    def enough(self) -> bool:
        """Cukup data menurut spec §6 — gerbangnya `n_effective`, bukan `n`."""
        return self.n_effective >= MIN_N

    @property
    def point(self) -> float | None:
        """`k/n` — None saat `n == 0`, karena 0/0 bukan 0 (CLAUDE.md: null != 0)."""
        return None if self.n == 0 else self.k / self.n

    def as_dict(self) -> dict[str, object]:
        return {
            "k": self.k,
            "n": self.n,
            "events": self.events,
            "n_effective": self.n_effective,
            "k_effective": self.k_effective,
            "enough": self.enough,
            "point": self.point,
            "interval": None if self.interval is None else self.interval.as_dict(),
        }

    def sentence(self, subject: str) -> str:
        """Kalimat spec §6 apa adanya, atau "belum cukup data" beserta angkanya.

        `subject` datang dari pemanggil (id item) dan sudah dibersihkan di sana; modul ini tidak
        menyentuh teks luar sama sekali supaya sanitasi hanya punya satu tempat (`core.skill`).
        """
        if self.n == 0:
            return f"{subject}: belum ada amatan (n=0) — tidak ada yang bisa dinilai"
        share = f"{self.k} dari {self.n}"
        provenance = f"n={self.n}, peristiwa bukti berbeda={self.events}, n_efektif={self.n_effective}"
        if not self.enough:
            return (
                f"{subject}: belum cukup data — {share} ({provenance}; ambang {MIN_N}); "
                "tidak ada selang kepercayaan yang dicetak"
            )
        assert self.interval is not None  # noqa: S101 - invarian kelas, dijaga observed_rate
        return (
            f"{subject}: {share} ({provenance}); "
            f"CI95 {self.interval.low:.3f}..{self.interval.high:.3f} atas cacah efektif "
            f"{self.k_effective}/{self.n_effective}"
        )


def observed_rate(k: int, n: int, events: int) -> ObservedRate:
    """Rakit :class:`ObservedRate`, termasuk pengempisan ke ukuran sampel efektif.

    Raises:
        GradingStatsError: cacah yang tidak mungkin. `events > n` termasuk: peristiwa bukti
            berbeda tidak bisa melebihi amatan yang melahirkannya, dan bila itu terjadi
            pemanggilnya salah mendefinisikan salah satu dari keduanya — didiamkan, ia akan
            **melebarkan** `n_effective` menjadi `n` dan menyembunyikan bugnya.
    """
    if isinstance(events, bool) or not isinstance(events, int):
        raise GradingStatsError(f"events harus int, bukan {type(events).__name__}")
    if isinstance(k, bool) or isinstance(n, bool) or not isinstance(k, int) or not isinstance(n, int):
        raise GradingStatsError(f"k dan n harus int, bukan {type(k).__name__}/{type(n).__name__}")
    if n < 0 or k < 0 or events < 0:
        raise GradingStatsError(f"cacah negatif: k={k}, n={n}, events={events}")
    if k > n:
        raise GradingStatsError(f"k={k} melebihi n={n}")
    if events > n:
        raise GradingStatsError(
            f"events={events} melebihi n={n}: peristiwa tidak bisa lebih banyak dari amatan"
        )
    if n == 0:
        return ObservedRate(k=0, n=0, events=events, n_effective=0, k_effective=0, interval=None)

    n_effective = min(n, events)
    # Pembulatan setengah-ke-atas lewat floor(x+0.5): `round()` Python membulatkan setengah ke
    # genap, sehingga k_eff sebuah item bisa berbeda dari item lain pada rasio yang sama persis.
    k_effective = min(n_effective, math.floor(k * n_effective / n + 0.5))
    interval = wilson_interval(k_effective, n_effective) if n_effective >= MIN_N else None
    return ObservedRate(
        k=k, n=n, events=events, n_effective=n_effective, k_effective=k_effective, interval=interval
    )
