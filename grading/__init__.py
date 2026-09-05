"""grading (opsional, TASKS 3.3) — outcome keputusan berumur >=24 jam menjadi reputasi item.

Alat UKUR, baca-saja, dan sengaja berada di luar jalur keputusan (ADR-003): tidak ada satu pun
modul di `core/`, `preflight/`, `agent/`, atau `ui/` yang mengimpor paket ini, dan ambang di sini
tidak ikut ke ``params_hash``.

* :mod:`grading.outcomes` — jawaban spec §6 untuk satu keputusan, dari harga perekam.
* :mod:`grading.stats` — Wilson CI tulis tangan + aturan `n` yang jujur (amatan != peristiwa).
* :mod:`grading.grader` — sapuan `decisions` -> tabel per item + takaran Bunryo; CLI `make grade`.

Ambangnya spec §4/§6: `n < 30` -> "belum cukup data", dan itu jawaban yang SAH.
"""
