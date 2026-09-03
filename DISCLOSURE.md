# DISCLOSURE — sesuai Official Rules §5 "Original Work and Third-Party Materials"

Status: **lengkap per 1 Sep 2026** untuk isi `uv.lock` saat ini (32 paket di luar `tenko` sendiri).
Diperbarui pada commit yang sama dengan setiap penambahan dependency, aset, dataset, atau sumber eksternal
(`CLAUDE.md`, `docs/rules-compliance.md`).

Aturan tulis di berkas ini: **lebih baik menulis "belum" daripada menyiratkan "sudah".** Paket yang terkunci di
`uv.lock` tetapi belum dipanggil satu baris kode pun tetap didaftarkan (ia ada di environment), dengan status apa
adanya. Aset yang baru direncanakan tidak ditulis seolah sudah disalin ke repo.

## AI tools
- Claude Code (Anthropic) — pembuatan & refactor kode, dokumentasi, tes. Semua kode direview manusia sebelum commit.
  Subagent & hook konfigurasi ada di `.claude/`.

## Pre-existing materials
- Tidak ada. Seluruh kode dibuat untuk hackathon ini.

## Cara memverifikasi daftar di bawah
Sumber kebenaran = `uv.lock`. Dua tabel Python di bawah berada di antara penanda
`<!-- python-deps:start -->` dan `<!-- python-deps:end -->`; kolom pertama tiap baris adalah nama paket persis seperti
di `uv.lock`. `tests/test_disclosure.py` membandingkan kedua himpunan tersebut dua arah (tidak ada yang hilang, tidak
ada nama yang tidak dikenal lock). Daftar mentahnya:

```bash
grep '^name = ' uv.lock          # 33 baris = 32 dependency + paket "tenko" sendiri
```

## Open-source dependencies — Python (dari `uv.lock`)

<!-- python-deps:start -->

### Langsung (disebut di `pyproject.toml`)

| Paket | Versi | Lisensi | Peran & status |
|---|---|---|---|
| httpx | 0.28.1 | BSD-3-Clause | klien HTTP ke tool RYO — dipakai di `core/ryo_client.py` |
| pydantic | 2.13.5 | MIT | kontrak & validasi `SkillResult` — dipakai di `core/skill.py` |
| pyyaml | 6.0.3 | MIT | baca `sources.yaml` — dipakai di `core/sources.py` |
| anthropic | 1.2.0 | MIT | narasi kartu (di luar jalur keputusan, ADR-003) — **dipakai kode sejak TASKS 2.3**: hanya di `preflight/narrate.py`, satu panggilan `messages.create` per kartu, model `claude-opus-5`, setelah keputusan final; kredensial hanya dari env, tanpa kredensial tidak ada request sama sekali |
| fastapi | 0.141.1 | MIT | route JSON UI — **terkunci di lock, BELUM dipakai kode** (TASKS 3.1 belum dikerjakan) |
| uvicorn | 0.52.4 | BSD-3-Clause | server `make ui` — **terkunci di lock, BELUM dipakai kode** (TASKS 3.1 belum dikerjakan) |
| pytest | 9.1.1 | MIT | dev — menjalankan `make test` |
| ruff | 0.16.5 | MIT | dev — lint & format (`make lint`) |

### Transitif (ditarik resolver, dikunci di `uv.lock`)

| Paket | Versi | Lisensi | Ditarik oleh |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | fastapi |
| annotated-types | 0.8.0 | MIT | pydantic |
| anyio | 4.14.2 | MIT | httpx / starlette |
| certifi | 2026.7.22 | MPL-2.0 | httpx — lihat catatan lisensi di bawah |
| click | 8.5.0 | BSD-3-Clause | uvicorn |
| colorama | 0.4.6 | BSD (dari classifier PyPI) | pytest (dan click) — marker `sys_platform == 'win32'`, **tidak terpasang di Linux** |
| docstring-parser | 0.18.0 | MIT | anthropic |
| h11 | 0.16.0 | MIT | httpcore / uvicorn |
| httpcore | 1.0.9 | BSD-3-Clause | httpx |
| httpcore2 | 2.12.0 | BSD-3-Clause | httpx2 |
| httpx2 | 2.12.0 | BSD-3-Clause | anthropic — juga dipakai `starlette.testclient.TestClient` (`docs/versions.md`) |
| httpx2-jsfetch | 1.0 | tidak diverifikasi — **tidak terpasang** | httpx2 — marker `sys_platform == 'emscripten'` (`uv.lock`), tidak pernah terpasang di lingkungan ini |
| idna | 3.19 | BSD-3-Clause | anyio / httpx |
| iniconfig | 2.3.0 | MIT | pytest |
| jiter | 0.16.0 | MIT | anthropic |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | pytest |
| pluggy | 1.6.0 | MIT | pytest |
| pydantic-core | 2.46.5 | MIT | pydantic |
| pygments | 2.21.0 | BSD-2-Clause | pytest |
| sniffio | 1.3.1 | MIT OR Apache-2.0 | anyio |
| starlette | 1.6.0 | BSD-3-Clause | fastapi |
| truststore | 0.10.4 | MIT | httpx2 (marker `sys_platform != 'emscripten'`, `uv.lock`); juga dipakai uv sendiri |
| typing-extensions | 4.16.0 | PSF-2.0 | pydantic |
| typing-inspection | 0.4.4 | MIT | pydantic |

<!-- python-deps:end -->

### Catatan lisensi: `certifi` adalah satu-satunya copyleft-lemah di daftar
`certifi` 2026.7.22 berlisensi **MPL-2.0** — satu-satunya lisensi copyleft (lemah, per-berkas) di antara 32 paket;
sisanya permisif (MIT / BSD / Apache-2.0 / PSF-2.0). Konsekuensi praktis untuk proyek ini: **nihil**. Paket dipakai
apa adanya sebagai CA bundle oleh `httpx` saat verifikasi TLS; tidak ada berkas certifi yang dimodifikasi, di-fork,
atau disalin ke dalam kode proyek, sehingga kewajiban MPL-2.0 §3.2 (menyediakan sumber berkas yang diubah) tidak
pernah terpicu. Paket tetap terpasang utuh dari PyPI melalui `uv.lock`.

## Aset frontend JS — direncanakan (ADR-014), BELUM di-vendor

**Status jujur per 1 Sep 2026: direktori `ui/static/` tidak ada di repo** (`ls ui/static` → tidak ditemukan; isi `ui/`
hanya `ui/__init__.py`). Tidak satu pun berkas three/GSAP/Lenis pernah disalin ke repo ini. Fase 3.2 belum dikerjakan.

Rencananya (ADR-014): saat layar dibangun, ketiga library **di-vendor lokal** ke `ui/static/vendor/<paket>/`, tanpa
build step, tanpa npm, tanpa CDN saat runtime (ADR-001; `make demo` wajib jalan offline dari fixture dan chaos test
mematikan host). Versi, lisensi, format berkas, dan hash sudah diverifikasi api-verifier dan tercatat di
`docs/versions.md` §"Aset frontend JS"; pin-nya sendiri masih menunggu pemakaian nyata.

Baris di bawah adalah **rencana**, bukan laporan tentang berkas yang ada di repo. Berkas ini diperbarui pada commit
yang sama saat vendoring benar-benar terjadi (termasuk salinan LICENSE per paket dan header copyright yang
dipertahankan utuh).

| Paket (rencana) | Versi | Lisensi | Peran | Status di repo |
|---|---|---|---|---|
| three | 0.170.0 | MIT | WebGL 3D | BELUM di-vendor |
| lenis | 1.1.0 | MIT | smooth scroll | BELUM di-vendor |

GSAP sengaja **tidak** ada di tabel ini — lihat bagian berikutnya.

## Third-party proprietary (no-charge license) — juga BELUM di-vendor
Dipisahkan dari tabel open-source di atas karena **GSAP bukan perangkat lunak open source** (konsekuensi ADR-014).

| Paket (rencana) | Versi | Lisensi | Peran | Status di repo |
|---|---|---|---|---|
| gsap (core + ScrollTrigger) | 3.15.0 | GSAP Standard "No Charge" License (Webflow) | animasi & scroll-trigger | BELUM di-vendor |

- Lisensi: GSAP Standard "No Charge" License (Webflow), efektif 30 April 2025 —
  https://gsap.com/community/standard-license/ (diambil 1 Sep 2026, `docs/versions.md`).
- **Gratis untuk proyek ini** karena pengguna akhir tidak ditagih biaya apa pun untuk memakai produk ini atau
  mengakses bagian mana pun darinya. **ScrollTrigger termasuk**: plugin yang dulu "members-only" kini gratis.
- **Tidak ada kewajiban atribusi**, tetapi bagian III lisensi melarang menghapus atau mengubah proprietary
  notice/branding — jadi bila nanti di-vendor, header hak cipta di dalam berkas `.js` wajib dipertahankan utuh.
- Grant (bagian II) berbunyi "use, reproduce, display, and implement" — **tanpa kata "distribute" atau "sublicense"**.
  Menyalin berkas untuk dijalankan sebagai bagian aplikasi ini tercakup; meredistribusikan GSAP sebagai paket tidak.
- Upstream **tidak menyertakan berkas lisensi sama sekali** (paket npm nol berkas LICENSE; repo GitHub
  `license: None`). Karena itu, saat vendoring nanti proyek ini harus membuat sendiri
  `ui/static/vendor/gsap/LICENSE.txt` berisi salinan teks resmi + catatan provenans (URL sumber dan tanggal
  pengambilan). Berkas tersebut **belum ada** karena vendoring-nya belum terjadi.

## Reference material (tidak di-ship, tidak dieksekusi)
- `references/awwwards-3d/` — skill panduan desain 3D scroll-driven, **MIT** (Copyright (c) 2026 awwwards-3d skill
  authors). Dipakai sebagai acuan gaya visual (ADR-014). Tidak ada kodenya yang masuk ke produk; library yang
  dirujuknya (three.js, GSAP, Lenis) hanya akan dipakai bila di-vendor eksplisit — status vendoring ada di dua bagian
  di atas.

## Datasets
- Tidak ada dataset pihak ketiga di runtime. Referensi sitasi (bukan komponen): RED-PUMP-2026-v1 (CC BY 4.0) — hanya
  bila dipakai; MELT (CC BY-NC 4.0) — **TIDAK dipakai** (klausa NonCommercial ditolak).

## External services (read-only) — hanya yang `approved: true` di `sources.yaml`
| Sumber | Untuk | ToS | Status izin |
|---|---|---|---|
| RYO-CHAN tools (MCP/REST, app-ryochan.com/api/mcp) | semua data pasar | disediakan penyelenggara | approved |
| Anthropic Claude API (`claude-opus-5`, SDK `anthropic` 1.2.0) | narasi kartu per item dari data terstruktur; **tidak pernah menyentuh keputusan** (tes menjaga `decision_hash`) | https://www.anthropic.com/legal/commercial-terms | "your own AI model" — landing page RYO |

Output tool RYO adalah data RYO: dipakai hanya di dalam submission, tidak dipublikasikan di post sosial
(ADR-008, `docs/rules-compliance.md`).

## Simulation notice
Semua posisi adalah practice/simulated. Tidak ada dana nyata, wallet, atau eksekusi. Swap yang dianalisis (bila ada)
adalah transaksi on-chain publik pihak ketiga yang diamati, bukan trade kami.
