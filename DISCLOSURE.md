# DISCLOSURE — sesuai Official Rules §5 "Original Work and Third-Party Materials"
Diperbarui pada setiap commit yang menambah dependency/sumber. Status: DRAF (Fase 0).

## AI tools
- Claude Code (Anthropic) — pembuatan & refactor kode, dokumentasi, tes. Semua kode direview manusia sebelum commit. Subagent & hook konfigurasi ada di `.claude/`.

## Pre-existing materials
- Tidak ada. Seluruh kode dibuat untuk hackathon ini.

## Open-source dependencies (Python dari `uv.lock`, aset frontend dari `docs/versions.md`; lisensi dicek saat pin)
| Paket | Versi | Lisensi | Peran |
|---|---|---|---|
| httpx | 0.28.1 | BSD-3-Clause | langsung — klien HTTP ke tool RYO |
| pydantic | 2.13.5 | MIT | langsung — validasi envelope |
| pyyaml | 6.0.3 | MIT | langsung — baca `sources.yaml` |
| anthropic | 1.2.0 | MIT | langsung — narasi kartu (di luar jalur keputusan) |
| fastapi | 0.141.1 | MIT | langsung — route JSON UI |
| uvicorn | 0.52.4 | BSD-3-Clause | langsung — server `make ui` |
| starlette | 1.6.0 | BSD-3-Clause | transitif dari fastapi |
| click | 8.5.0 | BSD-3-Clause | transitif dari uvicorn |
| annotated-doc | 0.0.5 | MIT | transitif dari fastapi |
| pytest | 9.1.1 | MIT | langsung (dev) |
| ruff | 0.16.5 | MIT | langsung (dev) |
| three | 0.170.0 | MIT | frontend — WebGL 3D, di-vendor ke `ui/static/vendor/three/` |
| lenis | 1.1.0 | MIT | frontend — smooth scroll, di-vendor ke `ui/static/vendor/lenis/` |

Aset frontend di atas (dan GSAP di bawah) disalin apa adanya ke `ui/static/vendor/<paket>/` — vendored, tanpa build step,
tanpa npm, tanpa CDN (ADR-001, ADR-014) — lengkap dengan file LICENSE masing-masing dan header copyright di dalam berkas
dipertahankan utuh; alasannya `make demo` wajib jalan offline dari fixture dan chaos test menguji keadaan host mati.

## Third-party proprietary (no-charge license)
Dipisahkan dari tabel di atas karena **GSAP bukan perangkat lunak open source**.

| Paket | Versi | Lisensi | Peran |
|---|---|---|---|
| gsap (core + ScrollTrigger) | 3.15.0 | GSAP Standard "No Charge" License (Webflow) | frontend — animasi & scroll-trigger, di-vendor ke `ui/static/vendor/gsap/` |

- Lisensi: GSAP Standard "No Charge" License (Webflow), efektif 30 April 2025 —
  https://gsap.com/community/standard-license/ (diambil 1 Sep 2026).
- **Gratis untuk proyek ini** karena pengguna akhir tidak ditagih biaya apa pun untuk memakai produk ini atau mengakses
  bagian mana pun darinya. **ScrollTrigger termasuk**: plugin yang dulu "members-only" kini gratis.
- **Tidak ada kewajiban atribusi**, tetapi header hak cipta di dalam file `.js` dipertahankan utuh — bagian III lisensi
  melarang menghapus atau mengubah proprietary notice/branding.
- Grant (bagian II) berbunyi "use, reproduce, display, and implement" — **tanpa kata "distribute" atau "sublicense"**.
  Menyalin file untuk dijalankan sebagai bagian aplikasi ini tercakup; meredistribusikan GSAP sebagai paket tidak.
- Upstream **tidak menyertakan file lisensi sama sekali** (paket npm nol file LICENSE; repo GitHub `license: None`),
  sehingga proyek ini membuat sendiri `ui/static/vendor/gsap/LICENSE.txt` berisi salinan teks resmi + catatan provenans
  (URL sumber dan tanggal pengambilan).

## Reference material (tidak di-ship, tidak dieksekusi)
- `references/awwwards-3d/` — skill panduan desain 3D scroll-driven, **MIT** (Copyright (c) 2026 awwwards-3d skill authors).
  Dipakai sebagai acuan gaya visual (ADR-014). Tidak ada kodenya yang masuk ke produk; library yang dirujuknya
  (three.js, GSAP, Lenis) hanya dipakai bila di-vendor eksplisit dan akan didaftarkan di sini saat itu terjadi.

## Datasets
- Tidak ada dataset pihak ketiga di runtime. Referensi sitasi (bukan komponen): RED-PUMP-2026-v1 (CC BY 4.0) — hanya bila dipakai; MELT (CC BY-NC 4.0) — TIDAK dipakai.

## External services (read-only) — hanya yang `approved: true` di sources.yaml
| Sumber | Untuk | ToS | Status izin |
|---|---|---|---|
| RYO-CHAN tools (MCP/REST, app-ryochan.com/api/mcp) | semua data pasar | disediakan penyelenggara | approved |
| Anthropic Claude API (`claude-opus-5`, SDK `anthropic` 1.2.0) | narasi kartu per item dari data terstruktur; **tidak pernah menyentuh keputusan** (tes menjaga decision_hash) | https://www.anthropic.com/legal/commercial-terms | "your own AI model" — landing page RYO |

## Simulation notice
Semua posisi adalah practice/simulated. Tidak ada dana nyata, wallet, atau eksekusi. Swap yang dianalisis (bila ada) adalah transaksi on-chain publik pihak ketiga yang diamati, bukan trade kami.
