"""Parameter agen practice-trade: ukuran tetap, batas waktu, watchlist (TASKS 2.4).

Dua rumpun parameter yang SENGAJA tidak dicampur, karena keduanya menjawab pertanyaan yang
berbeda dan hanya satu di antaranya boleh menggeser `decisions.decision_hash`:

* **Parameter KEPUTUSAN** (`preflight.params.Params`) — ambang yang menilai sepuluh item.
  Inilah yang masuk kolom `decisions.params_hash` dan ke dalam `core.replay.decision_hash`.
  Agen tidak punya satu ambang pun di rumpun ini; ia memakai `DEFAULT_PARAMS` apa adanya.
* **Parameter EKSEKUSI** (:class:`AgentParams` di bawah) — ukuran posisi, batas waktu,
  watchlist. Semuanya mempengaruhi baris `trade_events`, TIDAK satu pun mempengaruhi
  `decision`/`blocking`/`decision_hash`. Kalau ia ikut ke `params_hash` keputusan, mengganti
  ukuran posisi akan membuat setiap keputusan lama tampak MISMATCH saat di-replay padahal
  aturan penilaiannya tidak berubah sedikit pun. Jejaknya tetap ada, tetapi di tempat yang
  benar: `card_json["agent"]` berikut :meth:`AgentParams.exec_params_hash` sendiri.

**Ukuran posisi TETAP** (spec §4 item 11 "Bunryō" opsional dan TIDAK dikerjakan): tiap trade
practice memakai :data:`DEFAULT_SIZE_USD` USD nosional. Tidak ada penakaran berbasis
volatilitas, tidak ada compounding, tidak ada saldo — Tenko tidak punya dana, dompet, maupun
eksekusi (docs/rules-compliance.md), sehingga "ukuran" di sini hanyalah angka yang membuat
baris ledger bisa dibaca, bukan risiko yang ditanggung siapa pun.

Nilai-nilai di sini adalah KONSTANTA, bukan env: yang bisa digeser lewat lingkungan tidak bisa
dipertanggungjawabkan di kartu, dan satu-satunya hal yang boleh datang dari env adalah daftar
simbol (lihat `agent/watchlist.py`) karena ia tidak mengubah aritmetika apa pun.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.replay import params_hash as _replay_params_hash
from preflight.params import DEFAULT_PARAMS, Params

#: Ukuran nosional satu practice trade (USD). Tetap; lihat docstring modul.
DEFAULT_SIZE_USD = 1000.0

#: Umur maksimum posisi terbuka sebelum ditutup `timeout` (menit) = **720 (12 jam)**.
#:
#: Angka ini DITURUNKAN dari cakupan harga yang terukur, bukan dari selera — sumbernya
#: docs/api-facts.md §A "Harga — `analyze_token.data.market.price_usd` — DIVERIFIKASI
#: 4 Sep 2026 (TASKS 2.4)", sapuan `id <= 1727` (jendela ≈64,7 jam, 4 simbol inti):
#:
#: * `price_usd` **929/929 terisi (100 %)** — harga null BUKAN mode kegagalan yang pernah
#:   teramati; yang menggigit adalah KESEGARAN;
#: * jarak antar-baris `analyze_token` per simbol: median **62–63 mnt**, maksimum **1194 mnt
#:   (SOL/BTC)** dan **1455 mnt = 24,3 jam (ETH/BNB)**, dengan **5–7 celah > 120 mnt** per
#:   simbol dalam 2,7 hari;
#: * akibatnya sebuah posisi terbuka menghadapi harga "tidak diketahui" **42–48 % waktu**.
#:
#: Nilai lama 1440 (24 jam, = jendela outcome spec §6) **bertabrakan tepat** dengan celah
#: maksimum yang teramati (1455 mnt): satu celah data bisa menelan SELURUH umur posisi,
#: sehingga jatuh temponya lewat tanpa satu pun kesempatan mencatat exit sebelum jendela
#: grading §6 tutup. Dengan 720 mnt, jatuh tempo berada di TENGAH jendela §6 dan menyisakan
#: ~720 mnt berikutnya — pada jarak median 62–63 mnt itu kira-kira **11 siklus perekam**
#: kesempatan mencatat exit sebelum outcome T+24 j dibaca. Ia TIDAK menghilangkan tabrakan
#: (1455 > 720 juga): tidak ada nilai timeout yang bisa, karena yang menentukan adalah letak
#: celahnya, bukan panjang jendelanya. Yang benar-benar memperbaikinya adalah uptime perekam
#: (dicatat verifikasi API 4 Sep 2026 sebagai task tersendiri, bukan task ini).
#:
#: **Cabang yang TIDAK diambil, dan alasannya:** menutup `timeout` TANPA harga (dengan `price`
#: sebagai `unspoken`) menuntut perubahan skema — `trade_events.price` adalah `REAL NOT NULL`
#: di `core/store.py` dan di `data/ryo.db` yang hidup. Perubahan skema bukan milik task ini
#: dan bukan milik `agent/`: DILAPORKAN, tidak dikerjakan sendiri. Sampai ada keputusan itu,
#: exit `timeout` tetap WAJIB memakai harga perekam yang nyata; keterlambatannya dibukukan
#: eksplisit di `reason` baris `exit` (`agent/trade.py`), bukan disembunyikan.
DEFAULT_TIMEOUT_MIN = 12 * 60

#: Empat simbol inti (TASKS 2.4, dipotong penjagaan ruang lingkup 3 Sep 2026). Ini juga simbol yang
#: benar-benar direkam `core/recorder.py`, jadi watchlist ini punya bukti di store.
CORE_SYMBOLS: tuple[str, ...] = ("SOL", "BTC", "ETH", "BNB")


@dataclass(frozen=True, slots=True)
class AgentParams:
    """Parameter EKSEKUSI agen. Tidak satu pun menggeser `decision_hash` (lihat modul)."""

    size_usd: float = DEFAULT_SIZE_USD
    timeout_min: int = DEFAULT_TIMEOUT_MIN
    #: Parameter KEPUTUSAN yang dipakai memanggil `preflight` — dilewatkan apa adanya.
    preflight: Params = DEFAULT_PARAMS

    def __post_init__(self) -> None:
        if not isinstance(self.size_usd, int | float) or isinstance(self.size_usd, bool):
            raise ValueError(f"size_usd harus angka, bukan {type(self.size_usd).__name__}")
        if not (self.size_usd > 0) or self.size_usd != self.size_usd:
            raise ValueError(f"size_usd harus > 0 dan finit, bukan {self.size_usd!r}")
        if isinstance(self.timeout_min, bool) or not isinstance(self.timeout_min, int):
            raise ValueError(f"timeout_min harus bilangan bulat, bukan {type(self.timeout_min).__name__}")
        if self.timeout_min <= 0:
            raise ValueError(f"timeout_min harus > 0 menit, bukan {self.timeout_min}")

    def as_dict(self) -> dict[str, Any]:
        """Bentuk yang ikut ke `card_json["agent"]` — dibaca UI dan grading, bukan hash keputusan."""
        return {
            "size_usd": float(self.size_usd),
            "timeout_min": int(self.timeout_min),
            "sizing": "fixed_notional",
            "exits": ["target", "stop", "timeout"],
            "simulated": True,
        }

    def exec_params_hash(self) -> str:
        """Hash parameter EKSEKUSI — jejak sendiri, sengaja terpisah dari `params_hash` keputusan."""
        return _replay_params_hash(self.as_dict())

    def decision_params_hash(self) -> str:
        """Hash parameter KEPUTUSAN — inilah yang masuk kolom `decisions.params_hash`."""
        return self.preflight.params_hash()


#: Parameter jalur produksi agen.
DEFAULT_AGENT_PARAMS = AgentParams()


def params_hash(params: AgentParams | Mapping[str, Any] = DEFAULT_AGENT_PARAMS) -> str:
    """`exec_params_hash` untuk :class:`AgentParams` maupun mapping mentah (satu definisi)."""
    if isinstance(params, AgentParams):
        return params.exec_params_hash()
    return _replay_params_hash(params)
