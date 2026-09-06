# VERSIONS — versi yang dipakai
Aturan: pin persis. Jangan upgrade/downgrade tanpa ADR. "BELUM DIVERIFIKASI" → verifikasi API cek pypi.org sebelum pin (TASKS 0.1).

## Runtime (diverifikasi lokal 1 Sep 2026)
| Alat | Versi | Catatan |
|---|---|---|
| Python | **3.12** (`.python-version`; lokal 3.12.3) | evaluator pakai 3.13 lewat uv; di sini pin 3.12 = interpreter sistem, nol instalasi tambahan |
| uv | 0.12.7 (lokal) | pengelola env & lock |
| SQLite | 3.45.1 (stdlib `sqlite3`) | tidak perlu server DB |
| Node/pnpm | TIDAK DIPAKAI (ADR-001) | |
| openssl (binary sistem) | **3.0.13** (30 Jan 2024; `openssl version`, diverifikasi verifikasi API 6 Sep 2026) | HANYA di `scripts/chaos.sh:304` untuk sertifikat self-signed umur 1 hari (`RyoClient` menolak base_url non-https, jadi server fixture 429 harus TLS). Bukan dependency Python: tidak ada di `uv.lock`/`pyproject.toml`, tidak diimpor, tidak di-vendor; sertifikat lahir di `mktemp -d` dan tidak pernah masuk repo. Sekelas `git`/`curl`/`make`/`bash`. Skrip sudah gagal-tertutup bila tidak ada (`record rate_limit_429 DILEWATI`), jadi ia tidak wajib terpasang. **Keputusan baris `DISCLOSURE.md` bukan milik berkas ini** — lihat rekomendasi verifikasi API ke `penulisan dokumen` |

## Python (pyproject.toml)
| Paket | Versi | Status |
|---|---|---|
| httpx | 0.28.1 | diverifikasi 24 Agu (evaluator) |
| pydantic | **2.13.5** | KOREKSI 1 Sep 2026: baris lama menulis 2.13.4 (rilis 6 Mei 2026, ada & tidak yanked) tapi pyproject.toml + uv.lock memin **2.13.5** (rilis 28 Agu 2026, terbaru stabil). Yang benar = 2.13.5 (sesuai lock terpasang). Diverifikasi https://pypi.org/pypi/pydantic/json 1 Sep 2026 |
| pytest | 9.1.1 | diverifikasi 24 Agu (evaluator) |
| ruff | latest saat 0.1 (catat di lock) | — |
| fastapi | **0.141.1** | MIT, py>=3.10 (classifier 3.12 ✓); rilis 29 Jul 2026. requires-dist inti: `starlette>=0.46.0`, `pydantic>=2.9.0`, `typing-extensions>=4.8.0`, `typing-inspection>=0.4.2`, `annotated-doc>=0.0.2`. httpx HANYA di extra `[standard]` → jangan pakai extra. Diverifikasi https://pypi.org/pypi/fastapi/json 1 Sep 2026 |
| uvicorn | **0.52.4** | BSD-3-Clause, py>=3.10 (classifier 3.12 ✓); rilis 19 Agu 2026. TANPA extra `[standard]`: Makefile hanya `uvicorn ui.app:app --port` (tanpa `--reload`/ws) → uvloop/watchfiles/websockets tidak perlu. Deps inti: `click>=7.0`, `h11>=0.8`. Diverifikasi https://pypi.org/pypi/uvicorn/json 1 Sep 2026 |
| starlette (transitif fastapi) | **1.6.0** | BSD-3-Clause; rilis 8 Agu 2026. fastapi 0.141.1 memberi batas `>=0.46.0` TANPA batas atas → resolver mengambil 1.6.0; dikunci lewat uv.lock, jangan dipin di pyproject tanpa ADR. Diverifikasi https://pypi.org/pypi/starlette/json 1 Sep 2026 |
| pyyaml | **6.0.3** | MIT (`License :: OSI Approved :: MIT License`), `requires_python >=3.8`, classifier 3.12 ✓, rilis 2025-09-25, TIDAK yanked (73 file, termasuk wheel `cp312` manylinux/macos), TANPA requires-dist. Sama dengan pyproject.toml + uv.lock (`specifier ==6.0.3`) dan yang terpasang di `.venv` (`yaml.__version__ == '6.0.3'`). Dipakai di jalur produksi sejak TASKS 1.3: `core/sources.py` (`yaml.safe_load` untuk `sources.yaml`). Diverifikasi https://pypi.org/pypi/pyyaml/json + https://pypi.org/pypi/pyyaml/6.0.3/json, 1 Sep 2026. Baris DISCLOSURE.md sudah ada (pyyaml 6.0.3 MIT) — tidak perlu baris baru |
| numpy | BELUM DIVERIFIKASI | hanya bila validate/CI butuh; Wilson CI bisa tanpa numpy — pertimbangkan tidak memakai |
| mcp (python SDK) | BELUM DIVERIFIKASI | hanya bila transport MCP wajib; REST sudah cukup (api-facts §A) → tidak dipakai |
| anthropic | **1.2.0** | **MASIH INI yang dipakai kode** (rencana pindah ke Google Gemini: lihat bagian "LLM narasi — kandidat pindah ke Google Gemini" di bawah; belum dipin, belum ber-ADR). MIT, py>=3.10; diverifikasi pypi.org 1 Sep 2026. Hanya untuk narasi kartu (ADR-003/011), tidak pernah menyentuh keputusan. DIPAKAI KODE sejak TASKS 2.3 (`preflight/narrate.py`) — bentuk request diverifikasi 4 Sep 2026, lihat "Bentuk request narasi" di bawah. Transitifnya di `uv.lock`: anyio, docstring-parser, **httpx2 2.12.0**, jiter, pydantic, sniffio, typing-extensions — semuanya sudah ada di DISCLOSURE.md; pemakaian `httpx2` di `tests/test_narrate.py` TIDAK menambah dependency baru |

### Catatan verifikasi fastapi/uvicorn (verifikasi API, 1 Sep 2026)
- **TestClient tidak butuh paket tambahan yang dipin.** `fastapi.testclient.TestClient` = re-export `starlette.testclient.TestClient`; starlette 1.6.0 mencoba `import httpx2`, jatuh ke `httpx`, dan `RuntimeError` bila keduanya tidak ada. Di env proyek `httpx2==2.12.0` SUDAH ada sebagai transitif `anthropic==1.2.0` → TestClient memakai httpx2, nol warning. Dengan httpx 0.28.1 saja tetap jalan tapi memicu `StarletteDeprecationWarning` ("Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead"). Tidak ada `filterwarnings=error` di pyproject → tidak memblokir tes.
- **Bukti (env scratch, pin persis proyek + fastapi/uvicorn, Python 3.12.3, uv 0.12.7):** `uv sync` resolve OK; `TestClient(app).get("/health")` → 200; `uvicorn ui.app:app --port 8731` (tanpa `[standard]`) → `GET /health 200 OK`; `pytest -q` → 1 passed.
- **Paket baru yang masuk env** (butuh baris DISCLOSURE.md): fastapi 0.141.1 (MIT), uvicorn 0.52.4 (BSD-3-Clause), starlette 1.6.0 (BSD-3-Clause, transitif), click 8.5.0 (BSD-3-Clause, transitif), annotated-doc 0.0.5 (MIT, transitif). `h11` & `typing-inspection` sudah ada sebelumnya.

Model LLM: `claude-opus-5` (id tanpa akhiran tanggal), `output_config.effort="low"` — tugas narasi sederhana dari data terstruktur.

### Bentuk request narasi (anthropic 1.2.0, `preflight/narrate.py`) — DIVERIFIKASI verifikasi API 4 Sep 2026
Semua di bawah dibaca dari SDK yang TERPASANG di `.venv` (bukan ingatan) + dokumentasi resmi. Reproduksi:
`.venv/bin/python -c "import inspect;from anthropic.resources.messages import Messages;print(inspect.signature(Messages.create))"`,
`cat .venv/lib/python3.12/site-packages/anthropic/types/{output_config_param,json_output_format_param,refusal_stop_details}.py`,
`grep -n "stop_details\|content:" .venv/lib/python3.12/site-packages/anthropic/types/message.py`.

- `Messages.create` menerima `output_config`, `thinking`, `system`, `max_tokens`, dan `timeout` (kwarg per-request). `effort` TIDAK ada di top-level — ia field di dalam `OutputConfigParam`.
- `OutputConfigParam` = `{effort: Optional[Literal["low","medium","high","xhigh","max"]], format: Optional[JSONOutputFormatParam]}`. `"low"` sah.
- `JSONOutputFormatParam` = `{schema: Required[Dict[str, object]], type: Required[Literal["json_schema"]]}`. Jadi `{"type":"json_schema","schema":{…}}` adalah bentuk yang BENAR, dan structured outputs sudah **GA tanpa beta header**: `messages.create` non-beta sendiri menerimanya, dan dokumentasi resmi menyatakan "The `output_format` parameter has moved to `output_config.format`, and beta headers are no longer required" (https://platform.claude.com/docs/en/build-with-claude/structured-outputs, diambil 4 Sep 2026). `claude-opus-5` terdaftar sebagai model yang didukung di halaman itu.
- `Message.stop_reason: Optional[StopReason]`, dan `StopReason` memuat `"refusal"`; `Message.stop_details: Optional[RefusalStopDetails]`; `RefusalStopDetails` = `{category: Optional[Literal["cyber","bio","frontier_llm","reasoning_extraction","general_harms"]], explanation: Optional[str], type: Literal["refusal"]}`; `Message.content: List[ContentBlock]` (blok ber-`type`).
- `anthropic.Anthropic(max_retries=0, timeout=...)` sah; argumen kredensial opsional (SDK membaca variabel environment kredensial sendiri — namanya ada di `ENV_CREDENTIAL`, `preflight/narrate.py`). Semua kelas error yang ditangkap `narrate.py` (`APITimeoutError`, `APIConnectionError`, `RateLimitError`, `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`, `BadRequestError`, `APIStatusError`, `AnthropicError`) ada di namespace `anthropic`.
- `"claude-opus-5"` ada di literal `anthropic/types/model.py`.

**Batas skema JSON — ini yang bisa mematikan narasi, bukan bentuk `output_config`.** Dokumentasi structured outputs (diambil 4 Sep 2026) menyebut constraint string (`minLength`/`maxLength`), constraint numerik (`minimum`/`maximum`/`multipleOf`), dan constraint array selain `minItems` ∈ {0,1} sebagai **tidak didukung**, serta: "If you use an unsupported feature, you'll receive a 400 error with details". SDK mengonfirmasi daftar yang sama di `anthropic/lib/_parse/_transform.py` (`transform_schema` memindahkan `maxLength`/`maxItems` ke `description`, memaksa `additionalProperties: False` di tiap objek, mempertahankan `required` dan `enum`, dan hanya menerima `minItems` 0/1).
**`transform_schema` HANYA dipakai untuk `output_format=<type>`/`.parse()`. Skema dict mentah yang dikirim lewat `output_config={"format": {...}}` diteruskan APA ADANYA** (`anthropic/resources/messages/messages.py`, cabang `is_given(output_format)`). Skema tulis-tangan karena itu wajib sudah memenuhi batas di atas sendiri.
- WAJIB: `additionalProperties: false` di setiap objek + `required` yang mendaftar propertinya. `enum` string didukung.
- DILARANG: `maxLength`, `minLength`, `maxItems`, `minimum`/`maximum`, `additionalProperties` selain `false`, skema rekursif, `$ref` eksternal.

Kesimpulan: tidak perlu panggilan API untuk memastikan bentuk `output_config.format` — tipe SDK terpasang + dokumen resmi sudah menjawabnya. Yang butuh koreksi kode adalah isi `schema`-nya.
Tanpa: pandas, sklearn, plotting lib. Chart untuk UI: canvas/SVG inline, atau library JS yang **di-vendor lokal** (lihat "Aset frontend JS" di bawah) — CDN saat runtime sudah gugur karena `make demo` wajib offline; putuskan di 4.2 dengan ADR. Chart.js sendiri BELUM DIVERIFIKASI dan belum dipin.

## LLM narasi — kandidat pindah ke Google Gemini (diverifikasi verifikasi API 6 Sep 2026; **BELUM DIPIN, BELUM ber-ADR**)
Pemilik repo memutuskan memindahkan narasi kartu dari Anthropic ke Google Gemini (free tier). verifikasi API hanya memverifikasi
fakta; **memakai paket/sumber baru tetap butuh ADR + manusia** (lihat `docs/decisions.md`) dan `sources.yaml` masih `llm_narration.provider: anthropic`.
Sampai keduanya berubah, baris `anthropic 1.2.0` di atas TETAP yang dipakai kode (`preflight/narrate.py`).
Fakta lengkap + kutipan ketentuan data: **docs/api-facts.md §B2**.

| Paket | Kandidat pin | Lisensi | Bukti (URL/perintah, 6 Sep 2026) |
|---|---|---|---|
| **google-genai** | **2.22.0** (rilis 2026-09-02T18:06:00Z, tidak yanked) | **Apache-2.0** (`info.license_expression`; dist-info terpasang `License-Expression: Apache-2.0`) | `curl -sS https://pypi.org/pypi/google-genai/json`; `requires_python >=3.10`, classifier `Python :: 3.12` ADA → cocok dengan pin Python 3.12 |
| ~~google-generativeai~~ | **JANGAN** | Apache 2.0 | DEPRECATED oleh Google sendiri; README paket: *"[Deprecated] Google AI Python SDK"*, *"this repository is now considered legacy"*, *"All support for this repository ended permanently on November 30, 2025"* — `curl -sS https://pypi.org/pypi/google-generativeai/json` → `info.description` |
| ~~google-ai-generativelanguage~~ | bukan pilihan | Apache-2.0 | client GAPIC lapis bawah (dependency SDK lama, `==0.6.15`), bukan SDK aplikasi — `curl -sS https://pypi.org/pypi/google-ai-generativelanguage/json` |

**Paket baru yang masuk env bila pindah** (butuh baris DISCLOSURE.md pada commit yang sama; versi & lisensi dibaca dari
`importlib.metadata` di venv bersih Python 3.12.3 hasil `uv pip install google-genai==2.22.0`, 6 Sep 2026):
`google-genai` 2.22.0 Apache-2.0 · google-auth 2.57.1 Apache-2.0 · requests 2.34.2 Apache-2.0 · tenacity 9.1.4 Apache-2.0 ·
websockets 16.1.1 BSD-3-Clause · distro 1.9.0 Apache-2.0 · cryptography 50.0.1 (Apache-2.0 OR BSD-3-Clause) · certifi 2026.7.22 **MPL-2.0** ·
urllib3 2.7.0 MIT · idna 3.19 BSD-3-Clause · charset-normalizer 3.5.1 MIT · pyasn1 0.6.4 BSD-2-Clause · pyasn1_modules 0.4.2 BSD ·
cffi 2.1.1 MIT-0 · pycparser 3.0 BSD-3-Clause.
Yang **tidak** menambah baris karena sudah ada di proyek: httpx 0.28.1, pydantic 2.13.5 (+ pydantic_core, annotated-types,
typing-inspection, typing_extensions, anyio, sniffio, h11, httpcore) — resolusi google-genai menarik versi yang **sama persis**
dengan pin proyek. Perhatikan `certifi` **MPL-2.0** (lisensi copyleft-berkas, berbeda dari MIT/BSD/Apache lain di daftar) —
penulisan dokumen perlu menulisnya apa adanya, bukan digeneralisasi jadi "MIT".

**Perbandingan jejak dependency:** `anthropic 1.2.0` menarik 7 transitif (anyio, docstring-parser, httpx2, jiter, pydantic, sniffio,
typing-extensions); `google-genai 2.22.0` menarik **24**, di antaranya `cryptography` + `cffi` (ekstensi native) dan `requests`
+ `websockets`. Ini fakta netral, tetapi relevan untuk `make doctor`/offline: instalasi lebih berat dan `cryptography` butuh wheel.

**Bentuk request (bila jadi pindah) — DIVERIFIKASI dari SDK terpasang, bukan ingatan.** Ringkas; detail + kutipan di api-facts §B2.3–B2.4:
- Dua bentuk sah: `client.models.generate_content(model=..., contents=..., config=...)` (dokumentasi resmi: *"generateContent remains
  fully supported"*, tetapi *"now considered legacy"*) dan `client.interactions.create(model=..., input=..., ...)` (GA sejak Juni 2026,
  *"recommended for all new projects"*).
- **Tidak ada `output_config`, tidak ada `effort`.** Padanan terdekat = `thinking_level` (`minimal|low|medium|high`, dukungan
  **berbeda per model**) atau `thinking_config.thinking_budget` (token; `0` DISABLED, `-1` AUTOMATIC).
- Batas token keluaran: `max_output_tokens`. Timeout: `http_options.timeout` dalam **milidetik** (generateContent) /
  kwarg `timeout` bertipe `float|httpx.Timeout` (Interactions; **satuan BELUM DIVERIFIKASI**).
- Retry: **default tidak ada retry** (`stop_after_attempt(1)`) — cocok dengan kebijakan `narrate.py` saat ini.
- JSON terstruktur: BISA — `response_mime_type="application/json"` + `response_schema`/`response_json_schema`, atau
  `response_format={"type":"text","mime_type":"application/json","schema":{...}}`. Daftar keyword JSON Schema yang didukung
  **berbeda** dari Anthropic (`minItems`/`maxItems`/`minimum`/`maximum` justru DIDUKUNG di Gemini) → skema di `narrate.py`
  wajib ditinjau ulang, bukan disalin.
- Kredensial: SDK membaca sendiri variabel environment (dua nama, dengan prioritas) — nama persisnya di api-facts §B2.2.
- Kelas error: `google.genai.errors.{APIError, ClientError, ServerError, ...}`; **tidak ada kelas timeout SDK** — timeout/koneksi
  keluar sebagai exception `httpx` → sembilan `except anthropic.*` di `narrate.py` tidak punya padanan satu-lawan-satu.

**Model:** id yang free tier (jalur Standard) per halaman pricing 4 Sep 2026 antara lain `gemini-2.5-flash-lite`, `gemini-2.5-flash`,
`gemini-3.5-flash-lite`, `gemini-3.8-flash` (daftar lengkap + yang `Not available` di api-facts §B2.5). **Belum ada yang dipin** —
pemilihan model adalah keputusan ADR. Catatan yang harus dibaca dulu: `gemini-3.5-flash-lite` ada di dokumentasi tetapi **tidak ada**
di `Literal` model SDK 2.22.0.

**Batas free tier: angka RPM/TPM/RPD BELUM DIVERIFIKASI** — halaman rate-limits 2 Sep 2026 tidak lagi menerbitkan tabel angka dan
mengarahkan ke AI Studio (butuh sign-in). Yang terverifikasi hanya dimensinya dan "RPD reset tengah malam Pacific, per project,
bukan per key". Jangan menulis angka tebakan di kode.

**Ketentuan data free tier (ringkas; kutipan verbatim ada di api-facts §B2.7 — INI yang harus diputuskan manusia lebih dulu):**
Terms Gemini API menyatakan untuk Unpaid Services Google memakai konten yang dikirim dan respons untuk mengembangkan produk &
teknologi machine learning, *"human reviewers may read, annotate, and process your API input and output"*, dan
*"Do not submit sensitive, confidential, or personal information to the Unpaid Services"*; untuk Paid Services justru
*"Google doesn't use your prompts ... or responses to improve our products"*. Halaman pricing mengulangnya sebagai baris tabel
`Used to improve our products | Yes (Free) | No (Paid)` di semua blok model. Prompt narasi kita memuat nilai keluaran tool RYO.
verifikasi API tidak menyimpulkan aman/tidak aman; keputusannya milik pemilik repo.

## Aset frontend JS (di-vendor lokal, TANPA CDN, TANPA npm) — diverifikasi verifikasi API 1 Sep 2026
Konteks: gaya awwwards (3D + GSAP + smooth scroll) untuk semua layar Fase 3.2. Runtime TIDAK boleh menyentuh CDN
(`make demo` harus jalan offline dari fixture dengan dua run identik — spec §7; chaos test TASKS 4.5 mematikan host;
docs/rules-compliance.md membatasi endpoint eksternal). Semua file disalin apa adanya ke `ui/static/vendor/<paket>/`
tanpa build step — konsisten dengan ADR-001 (tidak ada Node/npm).
**Status: PIN MENUNGGU ADR.** verifikasi API hanya memverifikasi fakta (versi ada, lisensi, isi berkas, hash);
memakai library baru tetap butuh ADR + manusia (konvensi repo: "Tanpa library di luar docs/versions.md tanpa ADR").

| Paket | Pin | Terbaru stabil (1 Sep 2026) | Lisensi | Bukti |
|---|---|---|---|---|
| three | **0.170.0** (rilis 31 Okt 2024) | 0.185.1 (1 Jul 2026) | MIT | https://registry.npmjs.org/three (`dist-tags.latest`=0.185.1; `versions["0.170.0"].license`="MIT", tidak deprecated) + LICENSE r170 |
| gsap (core + ScrollTrigger) | **3.15.0** (rilis 13 Apr 2026) | 3.15.0 | **BUKAN MIT** — GSAP Standard "No Charge" License (Webflow), efektif 30 Apr 2025 | https://registry.npmjs.org/gsap (`license` = "Standard 'no charge' license: https://gsap.com/standard-license.") + https://gsap.com/community/standard-license/ |
| lenis | **1.1.0** (rilis 30 Mei 2024) | 1.3.26 (5 Agu 2026) | MIT | https://registry.npmjs.org/lenis (`versions["1.1.0"].license`="MIT") + https://raw.githubusercontent.com/darkroomengineering/lenis/v1.1.0/LICENSE |

### three 0.170.0 — MIT, aman di-vendor
- LICENSE verbatim (https://raw.githubusercontent.com/mrdoob/three.js/r170/LICENSE, 1081 byte, diambil 1 Sep 2026):
  `The MIT License` / `Copyright © 2010-2024 three.js authors` / teks MIT standar.
  (0.185.1 memakai LICENSE yang sama kecuali baris copyright: `Copyright © 2010-2026 three.js authors`.)
- **Tidak ada build UMD.** Isi `build/` pada 0.170.0 hanya: `three.cjs`, `three.module.js`, `three.module.min.js`,
  `three.webgpu[.nodes][.min].js`. Browser wajib `<script type="module">`.
  Diverifikasi: https://data.jsdelivr.com/v1/packages/npm/three@0.170.0?structure=flat
- **0.170.0 self-contained**: `build/three.module.js` nol `import ... from` relatif → cukup satu file.
  Bandingkan 0.185.1: `three.module.js` berisi `from './three.core.js'` (dan versi min → `"./three.core.min.js"`) →
  kalau naik ke 0.185.x harus vendor DUA file yang sepasang. (Diverifikasi dengan membaca 20 KB pertama tiap file, 1 Sep 2026.)
- **Addons butuh import map**: `examples/jsm/controls/OrbitControls.js` diawali `import {...} from 'three';`
  (bare specifier). Kalau memakai addon (EffectComposer/OrbitControls/loader), `index.html` wajib punya
  `<script type="importmap">` yang memetakan `"three"` dan `"three/addons/"` ke path lokal `/static/vendor/three/...`.
- File yang di-vendor (SHA-256 base64 dari byte file persis; sumber https://data.jsdelivr.com/v1/packages/npm/three@0.170.0?structure=flat):
  | File | Ukuran | sha256 (b64) |
  |---|---|---|
  | `build/three.module.js` | 1314681 | `zh+kGN4WoZSVqfcklVgOMBXXdFwpbTzgSFiX+QLd7fs=` |
  | `build/three.module.min.js` | 691648 | `CP11RdE9LH+2WraRUwqALa/v1jhZZQGFTyZ9D7E8Oec=` |
  | `LICENSE` | 1081 | `TECh72JFC4V8OyqvKUk2MEzVUtll+82dMtTFvPS6RFQ=` |
  Addons (`examples/jsm/**`) di-vendor per-file sesuai pemakaian; hash tiap file ada di listing jsDelivr yang sama.
- Unduh: `https://cdn.jsdelivr.net/npm/three@0.170.0/<path>` atau `https://unpkg.com/three@0.170.0/<path>`;
  paket utuh (otoritatif) `https://registry.npmjs.org/three/-/three-0.170.0.tgz`
  integrity `sha512-FQK+LEpYc0fBD+J8g6oSEyyNzjp+Q7Ks1C568WWaoMRLW+TkNNWmenWeGgJjV105Gd+p/2ql1ZcjYvNiPZBhuQ==`.
- Verifikasi isi file: tiap build diawali banner `@license` + `Copyright 2010-2024 Three.js Authors` +
  `SPDX-License-Identifier: MIT` (diverifikasi 1 Sep 2026) → banner ini WAJIB tidak dihapus.
- Kewajiban MIT saat vendoring: sertakan `ui/static/vendor/three/LICENSE` (salinan persis) dan pertahankan header di file build.
- Advisory: GHSA-7vvq-7r29-5vg3 (XSS, `<0.137.0`) dan GHSA-fq6p-x6j3-cmmq (DoS, `<0.125.0`) TIDAK kena 0.170.0.
  Sumber: https://api.github.com/advisories?ecosystem=npm&affects=three (1 Sep 2026).

### GSAP 3.15.0 — proprietary, gratis, boleh dipakai; BUKAN open source
- Teks lisensi resmi yang berlaku: **https://gsap.com/standard-license → redirect 200 ke https://gsap.com/community/standard-license/**
  ("Standard 'No Charge' GSAP License", *Effective date: April 30, 2025*, *Last modified date: May 30, 2025*, *Copyright (©) 2025, Webflow*).
  Kutipan verbatim yang menentukan (diambil 1 Sep 2026):
  - Ringkasan: "You may use the code at no charge in commercial or non-commercial apps, web sites, games, components,
    and other software as long as end users are not charged a fee of any kind to use your product or gain access to any part of it."
  - II. GRANT OF LICENSE: "Webflow grants you a non-exclusive, worldwide license to **use, reproduce, display, and implement**
    GSAP Products solely for Permitted Uses." ("Permitted Uses" = "the implementation and/or use of GSAP Products on any
    website, web application, or digital interface by any person or entity".)
  - III. RESTRICTIONS: dilarang "Prohibited Uses" (tool yang memungkinkan user membuat animasi visual tanpa kode dan
    bersaing dengan kemampuan animasi visual Webflow), dilarang reverse-engineer untuk membuat Competitive Products, dan
    dilarang "**Remove or alter any proprietary notices or branding from GSAP Products**".
  - FAQ: "All of GSAP including the plugins that were formerly 'members-only' like SplitText and MorphSVG can be used in
    commercial projects at no charge."
  - VI. Amendments: Webflow boleh mengubah lisensi kapan saja; versi lama boleh terus dipakai dengan syarat yang berlaku saat itu.
- **Jawaban tegas untuk proyek ini** (situs hackathon non-komersial, repo private, kode dikirim sebagai submission):
  (a) **Gratis, boleh** — kita tidak menagih end user apa pun. (b) **ScrollTrigger termasuk gratis** — semua plugin gratis
  (FAQ di atas; `dist/ScrollTrigger.min.js` ada di paket npm publik `gsap`). (c) **Tidak ada kewajiban atribusi**;
  yang WAJIB adalah tidak menghapus/mengubah header copyright di file. Produk kita bukan visual animation builder → bukan Prohibited Use.
- **Tidak ada file LICENSE di paket maupun repo.** `gsap@3.12.5` dan `gsap@3.15.0` di npm nol file bernama LICENSE/COPYING
  (listing jsDelivr); `https://api.github.com/repos/greensock/GSAP` → `license: None`; `raw.githubusercontent.com/greensock/GSAP/{3.12.5,3.15.0,master}/LICENSE.md` → 404.
  → Saat vendoring **kita** harus menaruh `ui/static/vendor/gsap/LICENSE.txt` berisi salinan teks dari
  https://gsap.com/community/standard-license/ + tanggal pengambilan (1 Sep 2026), karena file itu tidak ikut dari upstream.
- Header di dalam file (WAJIB dipertahankan; diverifikasi 1 Sep 2026):
  `/*! GSAP 3.15.0 · https://gsap.com · @license Copyright 2026, GreenSock. All rights reserved. · Subject to the terms at https://gsap.com/standard-license. */`
  (di 3.12.5 headernya: `Copyright 2024, GreenSock` + "...or for Club GSAP members, the agreement issued with that membership.")
- Format: `dist/*.min.js` adalah **UMD** (`typeof exports ... define.amd ... else global`) → cukup `<script src>` biasa,
  memberi global `gsap` dan `ScrollTrigger`; tidak perlu import map, tidak perlu esm.sh.
- File yang di-vendor:
  | File | Ukuran | sha256 (b64) |
  |---|---|---|
  | `dist/gsap.min.js` | 72927 | `krualkdvmD0hKivE9UyIkDnBaW3URh1ApzaGCThXD7s=` |
  | `dist/ScrollTrigger.min.js` | 44575 | `sLFNZ7VbDEPHVqwLEGz8sJ0IeZRfbq1kRRBlsGcpFqI=` |
- Unduh: `https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/{gsap,ScrollTrigger}.min.js`; paket utuh
  `https://registry.npmjs.org/gsap/-/gsap-3.15.0.tgz` integrity `sha512-dMW4CWBTUK1AEEDeZc1g4xpPGIrSf9fJF960qbTZmN/QwZIWY5wgliS6JWl9/25fpTGJrMRtSjGtOmPnfjZB+A==`.
- **Kenapa 3.15.0, bukan 3.12.5** (3.12.5 ADA, rilis 18 Jan 2024, tidak deprecated, hash tarball
  `sha512-srBfnk4n+Oe/ZnMIOXt3gT605BX9x5+rh/prT2F1SsNJsU1XuMiP0E2aptW481OnonOGACZWBqseH5Z7csHxhQ==`):
  3.12.5 dirilis SEBELUM lisensi 30 Apr 2025, dan teks lisensi yang berlaku saat itu **tidak bisa diambil lagi dari sumber primer**
  (URL di header file sekarang menampilkan teks 2025). Untuk Official Rules §5 kita harus bisa mengutip teks lisensi yang
  benar-benar berlaku → pakai 3.15.0 yang teksnya bisa dikutip hari ini. Konsekuensi teknis: **kompatibilitas
  ScrollTrigger 3.12.5→3.15.0 BELUM DIVERIFIKASI** (changelog resmi gsap.com 404 pada 1 Sep 2026, GitHub releases kosong) →
  wajib uji visual di Fase 3.2; kalau ada regresi, turun ke 3.12.5 hanya lewat ADR yang mencatat keterbatasan bukti lisensi di atas.
- Konsekuensi disclosure: GSAP **tidak boleh** ditulis di bagian "Open-source dependencies" DISCLOSURE.md — perlu barisnya sendiri
  sebagai "proprietary, no-charge license". Grant-nya menyebut use/reproduce/display/implement (tidak menyebut "distribute"/"sublicense"
  seperti MIT) → menyalin file ke repo submission untuk dijalankan = boleh; mempublikasikan repo sebagai paket open-source yang
  meredistribusi GSAP = JANGAN tanpa izin tertulis.

### Lenis 1.1.0 — MIT
- **Nama paket yang benar sekarang: `lenis`.** `@studio-freight/lenis` DEPRECATED di npm; field `deprecated` pada 1.0.42:
  "The '@studio-freight/lenis' package has been renamed to 'lenis'. Please update your dependencies: npm install lenis..."
  (https://registry.npmjs.org/@studio-freight%2Flenis, 1 Sep 2026). Jangan pakai scope lama.
- LICENSE verbatim (https://raw.githubusercontent.com/darkroomengineering/lenis/v1.1.0/LICENSE, 1096 byte, 1 Sep 2026):
  `The MIT License` / `Copyright (c) 2024 darkroom.engineering` / teks MIT standar (memakai tanda kutip typografis “ ”).
  File `/LICENSE` di paket 1.1.0 dan 1.3.26 identik (sha256 b64 `u6FbETc0anPtjDXn8glhpp4O2ELZVqUDOSQMPnsIwIk=`).
  Repo GitHub juga terdaftar MIT (`https://api.github.com/repos/darkroomengineering/lenis` → `spdx_id: MIT`).
- Format: `dist/lenis.min.js` di 1.1.0 = **UMD** yang memberi global `Lenis` (`...(t=globalThis...).Lenis=e()`) → `<script src>` biasa.
  `dist/lenis.mjs` tersedia bila mau ESM. 1.1.0 **tidak** punya `dist/lenis.css` (baru ada di seri 1.3.x).
- File yang di-vendor:
  | File | Ukuran | sha256 (b64) |
  |---|---|---|
  | `dist/lenis.min.js` | 12189 | `S4ee+T3KAQ0yOi9DauM4ixxBYDuQyZqKHzN8c83y+fY=` |
  | `dist/lenis.mjs` (opsional) | 11970 | `jrkeia/zqM19eWX7rrCZpsIB0tZynEYMf7QM+Zs1W68=` |
  | `LICENSE` | 1096 | `u6FbETc0anPtjDXn8glhpp4O2ELZVqUDOSQMPnsIwIk=` |
- Unduh: `https://cdn.jsdelivr.net/npm/lenis@1.1.0/dist/lenis.min.js`; paket utuh
  `https://registry.npmjs.org/lenis/-/lenis-1.1.0.tgz` integrity `sha512-katl22OfUAMtgFlKfvCSZqWT0xE1LtN/u7zRHDnlXczrYCIR5+6R9hY5iRHKgjMp88S3y3zBwb8jd6oQpbATbg==`.
- Kalau nanti naik ke 1.3.26 (MIT juga): bundle-nya IIFE yang menyetel `globalThis.Lenis`, DAN README resmi meminta
  `dist/lenis.css` ikut dimuat → vendor dua file. Advisory GitHub untuk `lenis`: nol.

### Cara verifikasi integritas file yang sudah di-vendor (tanpa dependency baru)
`hash` pada API jsDelivr = SHA-256 base64 dari byte file persis — dibuktikan 1 Sep 2026 dengan mencocokkan hash
`lenis@1.3.26/dist/lenis.min.js` hasil hitung sendiri (`Uxlcl5fnznv51/qSQrCCCeV/Rt5MnawSamSU+ngOM0Y=`) dengan nilai di API. Perintah:
```
python3 -c "import hashlib,base64,sys;[print('sha256-'+base64.b64encode(hashlib.sha256(open(f,'rb').read()).digest()).decode(),f) for f in sys.argv[1:]]" ui/static/vendor/*/*.js
```
Cek versi dari dalam file (tanpa jaringan): `head -c 120 ui/static/vendor/gsap/gsap.min.js` harus memuat `GSAP 3.15.0`;
`head -c 120 ui/static/vendor/three/three.module.js` harus memuat `Copyright 2010-2024 Three.js Authors`.

### Larangan & catatan operasional (Fase 3.2)
- **Nol CDN saat runtime.** Tidak ada `unpkg.com`/`esm.sh`/`cdn.jsdelivr.net` di HTML/JS yang dikirim; URL di atas hanya untuk
  langkah vendoring satu kali oleh manusia. Sumber eksternal runtime tetap hanya yang `approved: true` di `sources.yaml`.
- Jebakan offline dari resep awwwards yang beredar: `DRACOLoader.setDecoderPath('https://unpkg.com/...')`,
  `KTX2Loader.setTranscoderPath('https://unpkg.com/...')`, dan font `helvetiker_*.typeface.json` dari unpkg — semuanya
  memanggil jaringan saat render. Kalau dipakai, decoder/transcoder/font WAJIB ikut di-vendor ke `ui/static/vendor/three/`.
- `three-mesh-bvh` (dipakai sebagian resep MeshTransmissionMaterial): BELUM DIVERIFIKASI, belum dipin, belum ber-ADR → jangan dipakai.
- Alternatif ber-MIT bila lisensi GSAP ditolak pemilik proyek (versi & lisensi diverifikasi https://registry.npmjs.org/<paket> 1 Sep 2026,
  API-nya BUKAN drop-in): `motion` 13.1.1 (MIT) atau `animejs` 4.5.0 (MIT); scroll-trigger bisa diganti IntersectionObserver bawaan browser.
  Penilaian verifikasi API: **tidak perlu** — lisensi GSAP tidak membebani untuk pemakaian ini.

## Zona waktu
Semua tampilan JST (`Asia/Tokyo`); penyimpanan UTC ISO-8601.
