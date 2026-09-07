# DISCLOSURE — per Official Rules §5 "Original Work and Third-Party Materials"

Status: **complete as of 6 Sep 2026** for the current contents of `uv.lock` (32 packages besides `tenko` itself).
Updated in the same commit as every added dependency, asset, dataset or external source
(`docs/rules-compliance.md`).

The writing rule for this file: **better to write "not yet" than to imply "already done".** A package pinned in
`uv.lock` but not yet called by a single line of code is still listed (it exists in the environment), with its status
as it is. An asset that is merely planned is not written up as though it had already been copied into the repo.

## AI tools
- Claude Code (Anthropic) — code generation & refactoring, documentation, tests. All code is reviewed by a human
  before commit. Decision history, review findings that changed the code, and debt that is still open are recorded in
  `LOG.md`.

## Pre-existing materials
- **`ui/static/kage.html`, `ui/static/kage.css`, `ui/static/img/` (14 `.webp`), `ui/static/vendor/kage-scene.js`,
  and the seven `.woff2` faces Onest 300/400/500/700, NotoJP 400 and Wordmark 500/600 in `ui/static/fonts/`** —
  artwork and the "Kage" landing page by a friend of the repo owner, **used with permission** (ADR-026, 7 Sep 2026).
  Brought in from the owner's copy of that project. Not written for this hackathon; everything else here was.
  The licences of the three type families are **not yet verified** — see the **Fonts** section.
  **Added 7 Sep 2026 (ADR-029), and it is an addition to what is disclosed rather than a change of fact:**
  `ui/static/kage.html` itself was never named in this bullet, only the CSS and the assets around it — it is named
  now. And its influence is no longer confined to `/kage`: the **section arrangement** of the judged landing page
  `/` (`nav` → `hero` → `gate` → `pathways` → `lessons` → `eternity` → columned footer) and its section-head idiom
  are derived from that file. What is *not* derived from it: zero lines of `kage.html`/`kage.css` are imported,
  served or copied into `/` (the page loads `/ui/tenko.css` only, and a test forbids `@import` outright), zero of
  its colours travel with the arrangement, and its WebGL scene, fixed-position layers and foreground image layers
  are deliberately left behind — reasons in ADR-029 items 2 and 3. The hero formula on that same page comes from a
  different source, disclosed under **Reference material**.
- All other code was written for this hackathon.

## How to verify the list below
The source of truth is `uv.lock`. The two Python tables below sit between the markers
`<!-- python-deps:start -->` and `<!-- python-deps:end -->`; the first column of each row is the package name exactly
as it appears in `uv.lock`. `tests/test_disclosure.py` compares the two sets in both directions (nothing missing,
no name the lock does not know). The raw list:

```bash
grep '^name = ' uv.lock          # 33 lines = 32 dependencies + the "tenko" package itself
```

**The boundary of this list: packages that are shipped/imported, not system tooling.** `scripts/chaos.sh` calls
`openssl` to create a 1-day self-signed certificate for the fixture server on 127.0.0.1 (`openssl req -x509` in
`scripts/chaos.sh`; the certificate is born in `mktemp -d` and never enters the repo) — the same class as `bash`,
`git` or the `sqlite3` CLI: not shipped, not imported by code, not in `uv.lock`, and it only lives inside the test
harness. Because the source of truth for the table below is `uv.lock` and `tests/test_disclosure.py` compares the two
sets in **both directions**, adding a non-lock row would break the very invariant that keeps this list complete.
Runtime tooling versions are recorded in `docs/versions.md`.

## Open-source dependencies — Python (from `uv.lock`)

<!-- python-deps:start -->

### Direct (named in `pyproject.toml`)

| Package | Version | License | Role & status |
|---|---|---|---|
| httpx | 0.28.1 | BSD-3-Clause | HTTP client to the RYO tools — used in `core/ryo_client.py` |
| pydantic | 2.13.5 | MIT | `SkillResult` contract & validation — used in `core/skill.py` |
| pyyaml | 6.0.3 | MIT | reads `sources.yaml` — used in `core/sources.py` |
| anthropic | 1.2.0 | MIT | card narration (outside the decision path, ADR-003) — **used by code**: only in `preflight/narrate.py`, one `messages.create` call per card, model `claude-opus-5`, after the final decision; credentials from env only, and with no credentials there is no request at all |
| fastapi | 0.141.1 | MIT | JSON routes + a landing page and two HTML screens — **used by code**: `ui/app.py` announces **fourteen routes** in `ROUTE_INDEX` (`ui/app.py`, symbol `ROUTE_INDEX` — line numbers in `ui/` move, names do not): `/` (the landing page, ADR-021), `/app`, `/app/decision/{id}`, `/ui`, `/ui/decision/{id}` (the last two are aliases of the two before them, kept so that existing links in this repo do not die), `/ui/tenko.css`, `/ui/app.js`, `/ui/fonts/{name}`, `/api/routes`, `/api/health`, `/api/watchlist`, `/api/decisions`, `/api/decision/{id}`, `/api/preflight/{symbol}`. **Three** of those are assets, served from **tracked static files** — `ui/static/app.css` and `ui/static/app.js`, both built by esbuild from `frontend/` (ADR-020, landed in commit `ed77a8c`), plus the four vendored IBM Plex faces and their licence in `ui/static/fonts/` (ADR-022; see the **Fonts** section below). All are committed to git. `/ui/fonts/{name}` serves from a **closed list** (`ui/screens.py`, symbol `FONT_NAMES`) and never builds a filesystem path from the request. Each screen carries **one** `<script src="/ui/app.js" defer>` from its own origin (`ui/screens.py`, emitted by `_shell()`); still **zero CDN and zero npm packages served at runtime** (`package.json` → `"dependencies": {}`). The CSP sent with every **HTML** response (`ui/app.py` `HTML_HEADERS`; the `/api/…` JSON responses carry no such header — what protects them is `ui/app.py` `SafeJSONResponse`) names no third-party host and permits neither `unsafe-inline` nor `unsafe-eval`; scripts, styles, `fetch` targets and — since ADR-022 — images, fonts and media are all confined to this origin (`'self'`, never `*`). One narrow exception, added 7 Sep 2026 (ADR-026 item 11) and stated here because the earlier wording of this row said "never `data:`" and that is no longer true: the `/kage` scenery page — and **only** that page, via `ui/app.py` `KAGE_HTML_HEADERS` — sends `img-src 'self' data:`, because its grain layer is painted from `canvas.toDataURL()` and `img-src 'self'` was blocking it silently. The landing page, Screens A and B (the only screens that render RYO/DB values into the DOM), every `/api/…` response, every asset route and every error page keep the unchanged `HTML_HEADERS`; no other directive is relaxed anywhere, and `unsafe-inline`/`unsafe-eval` appear in neither policy. Read the header there rather than from this file, which is not the place that defines it — a pasted copy here is how the previous version of this row became false. This row replaces two earlier wordings, "six GET routes … Screens A/B do not exist yet" and "eight GET routes … zero static files … no `<script>` at all", both of which are now false (corrected 6 Sep 2026). It only reads the store (`Store(..., read_only=True)`); there is no `httpx`/`anthropic` in `ui/`, and `/api/health` reports `calls_ryo_tools: false` and `calls_llm: false`. Every response — including 400/404/405/422/500/503 — carries `simulated: true` + the SIMULATED banner |
| uvicorn | 0.52.4 | BSD-3-Clause | the `make ui` server — **used**, launched from the `Makefile` (`uv run uvicorn ui.app:app --port $UI_PORT`), not imported by code; without the `[standard]` extra (`docs/versions.md`) |
| pytest | 9.1.1 | MIT | dev — runs `make test` |
| ruff | 0.16.5 | MIT | dev — lint & format (`make lint`) |

### Transitive (pulled by the resolver, pinned in `uv.lock`)

| Package | Version | License | Pulled by |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | fastapi |
| annotated-types | 0.8.0 | MIT | pydantic |
| anyio | 4.14.2 | MIT | httpx / starlette |
| certifi | 2026.7.22 | MPL-2.0 | httpx — see the license note below |
| click | 8.5.0 | BSD-3-Clause | uvicorn |
| colorama | 0.4.6 | BSD (from the PyPI classifier) | pytest (and click) — marker `sys_platform == 'win32'`, **not installed on Linux** |
| docstring-parser | 0.18.0 | MIT | anthropic |
| h11 | 0.16.0 | MIT | httpcore / uvicorn |
| httpcore | 1.0.9 | BSD-3-Clause | httpx |
| httpcore2 | 2.12.0 | BSD-3-Clause | httpx2 |
| httpx2 | 2.12.0 | BSD-3-Clause | anthropic — also used by `starlette.testclient.TestClient` (`docs/versions.md`) |
| httpx2-jsfetch | 1.0 | not verified — **not installed** | httpx2 — marker `sys_platform == 'emscripten'` (`uv.lock`), never installed in this environment |
| idna | 3.19 | BSD-3-Clause | anyio / httpx |
| iniconfig | 2.3.0 | MIT | pytest |
| jiter | 0.16.0 | MIT | anthropic |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | pytest |
| pluggy | 1.6.0 | MIT | pytest |
| pydantic-core | 2.46.5 | MIT | pydantic |
| pygments | 2.21.0 | BSD-2-Clause | pytest |
| sniffio | 1.3.1 | MIT OR Apache-2.0 | anyio |
| starlette | 1.6.0 | BSD-3-Clause | fastapi |
| truststore | 0.10.4 | MIT | httpx2 (marker `sys_platform != 'emscripten'`, `uv.lock`); also used by uv itself |
| typing-extensions | 4.16.0 | PSF-2.0 | pydantic |
| typing-inspection | 0.4.4 | MIT | pydantic |

<!-- python-deps:end -->

### License note: `certifi` is the only weak-copyleft entry in the list
`certifi` 2026.7.22 is licensed **MPL-2.0** — the only copyleft license (weak, per-file) among the 32 packages; the
rest are permissive (MIT / BSD / Apache-2.0 / PSF-2.0). Practical consequence for this project: **none**. The package
is used as-is as a CA bundle by `httpx` during TLS verification; no certifi file is modified, forked or copied into
the project's code, so the MPL-2.0 §3.2 obligation (providing the source of modified files) is never triggered. The
package stays installed intact from PyPI via `uv.lock`.

## Frontend build tooling (ADR-020) — BUILD-ONLY, zero runtime dependencies

ADR-020 (ACCEPTED 6 Sep 2026) narrows ADR-001 and ADR-014: Node/npm become a **build** tool for the presentation
layer (TypeScript + esbuild), the build output (`ui/static/app.js`) is tracked in git and shipped, and **running**
Tenko still needs no Node at all. **Runtime npm dependencies: zero.** The rows below were verified against the npm
registry and against the LICENSE file inside each package on 6 Sep 2026. **The rewrite has landed** (commit `ed77a8c`):
`package.json`, `package-lock.json`, `frontend/` (7 `.ts` sources + `frontend/styles/app.css`) and `ui/static/`
(`app.css`, `app.js`) are all tracked in git — `git ls-files ui/static frontend package.json package-lock.json`.
An earlier version of this paragraph said none of them existed; that was true when it was written and is false now.
The rows below still describe the toolchain **on the build machine**, which is a smaller set than the lock (see below).

| Package | Version | License | Role | Status |
|---|---|---|---|---|
| node (Node.js) | 24.15.0 (LTS "Krypton", released 2026-04-15) | MIT for core; its LICENSE also carries the licenses of bundled components (OpenSSL, ICU, libuv, undici, V8 and others) | runs tsc + esbuild to build `ui/static/app.js` | **BUILD-ONLY** (not needed to run Tenko) |
| npm | 11.12.1 (bundled with Node 24.15.0) | Artistic-2.0 | installs the build devDependencies from `package-lock.json` | BUILD-ONLY |
| typescript | 7.0.2 | Apache-2.0 | type-checks the `frontend/` sources (strict) | BUILD-ONLY (devDependency) |
| @typescript/typescript-linux-x64 | 7.0.2 | Apache-2.0 | native tsc binary for this linux-x64 build machine (optionalDependency of typescript 7) | BUILD-ONLY |
| vscode-jsonrpc | 9.0.0 | MIT | vendored INSIDE the typescript 7.0.2 package (`vendor/vscode-jsonrpc`), not a separate install | BUILD-ONLY |
| esbuild | 0.28.2 | MIT | bundles and minifies the TypeScript into `ui/static/app.js` | BUILD-ONLY (devDependency) |
| @esbuild/linux-x64 | 0.28.2 | MIT | esbuild binary for this linux-x64 build machine (optionalDependency of esbuild) | BUILD-ONLY |

Two things stated plainly, because smoothing them over would be dishonest:

1. **`package-lock.json` records the entire platform matrix** — measured, not predicted: `lockfileVersion` 3, and
   `packages` holds **49 entries = 1 root entry + 48 packages**, of which **48/48 are `dev: true`** and 46 are also
   `optional: true` (20 `@typescript/*` + 26 `@esbuild/*` platform binaries). Only `esbuild` and `typescript` are
   non-optional. That is lock metadata, not files that are installed: on this build machine **4** of the 48 are
   actually on disk. Full per-package list, counts and commands: `docs/versions.md` §"LOCK REPO SUDAH DIBACA".
2. **The build is not guaranteed to be offline.** `esbuild` has a `postinstall` step that downloads the platform
   tarball when the binary is not already present. What *is* guaranteed offline is **running** Tenko: the build output
   is tracked in git (ADR-020 item 2), so a clean clone with no Node and no network still runs `make ui`, `make test`
   and `make demo`.

Status note, 6 Sep 2026: the contents of `package-lock.json` **have been verified**, line by line, against the lock
itself, against `node_modules` on disk, and against the npm registry — `docs/versions.md` §"LOCK REPO SUDAH DIBACA"
(commit `ed77a8c`). Three results from that check belong here rather than being left in a versions file:

- **Zero npm runtime dependencies is a measurement now, not an intention**: `package.json` carries the literal
  `"dependencies": {}`, and every one of the 48 locked packages is marked `dev`.
- **Every locked entry has both `resolved` and `integrity`**, all from `registry.npmjs.org`; no other host appears.
  **8 of the 48** entries were re-fetched from the registry and compared field by field — **8/8 matched** on
  integrity, tarball URL and license — plus a byte-level check of the `typescript` 7.0.2 tarball whose
  self-computed sha512 equals the lock's `integrity` (`docs/versions.md`). The remaining 40 entries are **not**
  individually re-fetched, and this file does not pretend otherwise.
- **One package is invisible to the lock**: `vscode-jsonrpc` 9.0.0 ships **vendored inside** the `typescript` 7.0.2
  package (`node_modules/typescript/vendor/vscode-jsonrpc`, MIT, License.txt included) and `grep -ci jsonrpc
  package-lock.json` → **0**. It is in the table above for exactly that reason: a disclosure built from the lock
  alone would have missed it.

This file is corrected in the same commit if any of that changes.

## Frontend JS assets — SHIPPED (ADR-025); three.js NOW VENDORED (ADR-026)

**Honest status as of 6 Sep 2026: `ui/static/` now EXISTS and holds exactly two files** — `app.css` and `app.js`,
both esbuild output from `frontend/`, both tracked (`git ls-files ui/static`). An earlier version of this paragraph
said the directory did not exist; that is corrected here. **Both of those statements are now out of date, and this paragraph is the correction (7 Sep 2026,
ADR-026).** `ui/static/` holds far more than two files: `three.min.js` and `kage-scene.js` under `vendor/`,
`kage.css`, 14 `.webp` under `img/`, and eleven font faces. The sentence that used to stand here — "not one
three/GSAP/Lenis file has ever been copied into this repo" — is **false as of ADR-026**: three.js r149 is
vendored as a file. GSAP and Lenis remain bundled rather than vendored (next sections). **One more sentence here
went out of date on 7 Sep 2026 and is corrected rather than quietly edited (ADR-027):** it used to say Screens A
and B "are untouched and still use none of it". They now use two of those pre-existing assets — the vendored Onest
faces, and `img/kage-approach.webp` as a background — because ADR-027 reconciled the two visual languages that
ADR-026 left standing side by side. What is still true and narrower: no screen uses three.js, `kage-scene.js`, or
`kage.css`, and the Kage page remains a separate route.

The plan (ADR-014): when the screens were built, the three libraries would be **vendored locally** into
`ui/static/vendor/<package>/`, with no build step, no npm, no CDN at runtime (ADR-001; `make demo` must run offline
from fixtures and the chaos test kills the host). Versions, licenses, file formats and hashes have already been
verified against primary sources and recorded in `docs/versions.md` §"Aset frontend JS"; the pins themselves still
await real use. **The screens actually shipped use none of the three**, so this plan remains a plan; if any of them is
ever used, this file changes in the same commit. Note that ADR-020 narrows the "no build step, no npm" part of
ADR-014 for the presentation layer (see the section above); it does not vendor any of the three libraries below.

The rows below are a **plan**, not a report about files present in the repo. This file is updated in the same commit
in which vendoring actually happens (including a per-package copy of LICENSE and copyright headers kept intact).

| Package (planned) | Version | License | Role | Status in repo |
|---|---|---|---|---|
| three | **r149 (0.149.x)** | MIT | WebGL 3D — the Kage landing scene | **VENDORED** as `ui/static/vendor/three.min.js` (ADR-026). The upstream MIT banner (`Copyright 2010-2023 Three.js Authors`, `SPDX-License-Identifier: MIT`) is kept intact at the head of the file, and the full upstream `LICENSE` ships beside it as `ui/static/vendor/LICENSE.three.txt` (1,081 bytes, sha256 `852e0e8699169bf9f6fdc6bda3e682d078dcbc738b5d33e74df594721bff271d`). The shipped `three.min.js` was verified **byte-for-byte identical** to `package/build/three.min.js` from the official tarball `registry.npmjs.org/three/-/three-0.149.0.tgz` (`cmp` → 0 bytes differ); sha256 `8a5f7249903b54d30f79f708699d2fed2d6a1d0741a4cd41377d1f01bb5a2271`, 608,081 bytes. Released 2023-01-26. Zero published advisories for 0.149.0. Note this is **not** the 0.170.0 that ADR-014 pinned: the vendored file is the build that is known to work with this scene. The pin + evidence live in `docs/versions.md` |
| lenis | 1.1.0 | MIT | smooth scroll | NOT vendored |

GSAP is deliberately **not** in this table — see the next section.

## Third-party proprietary (no-charge license) — SHIPPED, bundled into `ui/static/app.js`
Kept separate from the open-source table above because **GSAP is not open source software** (a consequence of
ADR-014).

**Status as of 7 Sep 2026 (ADR-025): GSAP 3.12.5 and Lenis 1.1.0 are now SHIPPED.** They are not vendored as
separate files; they are bundled by esbuild into `ui/static/app.js`, which is tracked in git. Node is still a
**build** tool only — a clone with no Node at all still runs `make ui`, `make test` and `make demo`, because the
bundle is committed (ADR-020 § 2). `package.json` → `"dependencies": {}` remains true and is tested.

| Package | Version | License | Licence obligation, and how it is met |
|---|---|---|---|
| **gsap** (incl. ScrollTrigger) | **3.12.5** | **Standard "no charge" license** — *not* open source. `package.json` → `"license"` names `https://gsap.com/standard-license`; the package ships **no LICENSE file** (verified 7 Sep 2026 in `node_modules/gsap`). | Free while end users are not charged — nothing here charges anyone. The licence requires the copyright headers to stay intact, so the JS build uses `--legal-comments=eof`, **not** `none`; `grep GreenSock ui/static/app.js` finds them. A test pins the URLs that may appear in the bundle. |
| **lenis** | **1.1.0** | **MIT** — `LICENSE` file present: *"The MIT License / Copyright (c) 2024 darkroom.engineering"* | MIT requires the notice to travel with the code. The minified dist carries no notice of its own, so the build injects one with esbuild `--banner:js`; it is the first line of `ui/static/app.js`. |
| **@darkroom.engineering/tempus** | **0.0.46** | **MIT** (`package.json` → `"license"`; no LICENSE file in the package) | The only transitive dependency of lenis. Bundled with it. |

Why these two and not a framework: they buy smooth scroll and scroll-triggered reveals, which is what the owner
asked for, without adding a runtime framework. **Correction (7 Sep 2026): three.js IS now vendored** (ADR-026), reversing
ADR-025 point 8. ADR-025 chose Lenis + GSAP over the 3D path because writing that path would cost the remaining
time; ADR-026 vendors a scene that was already written.

| Package (planned) | Version | License | Role | Status in repo |
|---|---|---|---|---|
| gsap (core + ScrollTrigger) | 3.15.0 | GSAP Standard "No Charge" License (Webflow) | animation & scroll trigger | NOT vendored |

- License: GSAP Standard "No Charge" License (Webflow), effective 30 April 2025 —
  https://gsap.com/community/standard-license/ (retrieved 1 Sep 2026, `docs/versions.md`).
- **Free for this project** because end users are not charged anything to use this product or to access any part of
  it. **ScrollTrigger is included**: the plugin that used to be "members-only" is now free.
- **No attribution obligation**, but part III of the license forbids removing or altering proprietary
  notices/branding — so if it is ever vendored, the copyright headers inside the `.js` files must be kept intact.
- The grant (part II) reads "use, reproduce, display, and implement" — **without the words "distribute" or
  "sublicense"**. Copying the files to run them as part of this application is covered; redistributing GSAP as a
  package is not.
- Upstream **ships no license file at all** (the npm package has zero LICENSE files; the GitHub repo says
  `license: None`). So when vendoring happens, this project must itself create
  `ui/static/vendor/gsap/LICENSE.txt` containing a copy of the official text plus a provenance note (source URL and
  retrieval date). That file **does not exist yet**, because the vendoring has not happened.

## Fonts — VENDORED and shipped (ADR-022)

Four IBM Plex faces are committed to `ui/static/fonts/` and served from this app's own origin at
`/ui/fonts/{name}`. **Zero CDN, zero network request at render**, so `make demo` still runs with
no network (spec §7) and `font-src 'self'` is enough. **Corrected 7 Sep 2026 (ADR-026):** this used to end "These are
the only binary assets in the repo besides the esbuild output". They are no longer — 14 `.webp`
images now live in `ui/static/img/`, and seven more font faces sit beside these four (below).

| Asset | Version | License | Verified how |
|---|---|---|---|
| IBM Plex Sans (Regular, SemiBold) | **v6.4.2** | **SIL OFL 1.1** | `curl https://raw.githubusercontent.com/IBM/plex/v6.4.2/LICENSE.txt` → *"Copyright © 2017 IBM Corp. with Reserved Font Name \"Plex\" … licensed under the SIL Open Font License, Version 1.1"*, read 6 Sep 2026 |
| IBM Plex Mono (Regular, SemiBold) | **v6.4.2** | **SIL OFL 1.1** | same file, same tag |

Downloaded from `https://raw.githubusercontent.com/IBM/plex/v6.4.2/IBM-Plex-{Sans,Mono}/fonts/complete/woff2/`.
Pinned to the release tag, not `master`, so the bytes are reproducible. SHA-256 of what is
committed:

```
49ce58b41a0e1cb921c0f58d9a5b8b96a2cc21437c7066f3ba4f24873076d131  IBMPlexMono-Regular.woff2
ed5eaca7522336959d6c3810bd9bb78424f0d964082d581bfbea169ee08d14e3  IBMPlexMono-SemiBold.woff2
ba711a3085ff9f27440b6b9c4550cfc47c97bf36591d5da958b975bb3add8c1a  IBMPlexSans-Regular.woff2
f78048030eab62e860efa39a0df79e2e5581bf122eb95b9bc42c0b8a4988d205  IBMPlexSans-SemiBold.woff2
7e6b2818edbd8f6a01ae80641cc8f16a51080d08fb4e532be3a0b6f74adb07da  LICENSE.txt
```

**OFL 1.1 obligations, and how they are met.** The licence text ships with the fonts at
`ui/static/fonts/LICENSE.txt` (tracked by git) and is also served at `/ui/fonts/LICENSE.txt`;
`tests/test_ui.py::test_every_vendored_font_is_served_and_carries_its_licence` fails if either
disappears. The files are distributed **unmodified** and **not sold**, and the Reserved Font Name
"Plex" is not used for any modified version — Tenko does not modify them at all. The CSS family
names (`"IBM Plex Sans Vendored"`) are local `@font-face` aliases pointing at the unmodified
files, not renamed fonts.

### Seven more faces, from JAWAA — SHIPPED, licences NOT YET VERIFIED (ADR-026)

These arrived embedded as `data:font/woff2;base64` inside the friend's `fonts.css`. Tenko's CSP
(`font-src 'self'`, never `data:` — ADR-022, and the ADR-026 item 11 relaxation touches `img-src` only, so this
stays true for fonts) would block that form, so they were decoded into real
files under `ui/static/fonts/` and are served from the same closed list at `/ui/fonts/{name}`. All
are **subsets** (1.6–15 KB), not complete families.

| Asset | Faces | Licence | Status |
|---|---|---|---|
| Onest | 300, 400, 500, 700 | **almost certainly SIL OFL 1.1** — not yet verified against upstream | **OPEN** |
| NotoJP (Noto Sans JP, subset) | 400 | **almost certainly SIL OFL 1.1** — not yet verified against upstream | **OPEN** |
| Wordmark | 500, 600 | **unknown** — a 1.6 KB custom subset; upstream family not identified | **OPEN** |

```
256fa8037be67fd4a857a660c328a09e4696a0fb5de166f1357534a81f738556  Onest-300.woff2
cbc780c47e7975372debee1cad74f54bc38a3d366840a74b0af33bdd9ce5e8cf  Onest-400.woff2
21150b504f3aa78ba6dd04a6d11dd938fdc7c354f7e93ee2312fa18046d3aabd  Onest-500.woff2
98e3406af6c57c1b730a1f043cf087689fb16d2dc0bc37feb7d5f1860f55d01e  Onest-700.woff2
919f3ce938b0deabdf894554f466747c789806daddc1e3ab2a01d4cff4464382  NotoJP-400.woff2
6a93626eb17466bfd9239cec66b2c371bb20381999776a5d16e2c22a92f00349  Wordmark-500.woff2
87832ba80d7ac0927a4c4c32588e0f92d368eca9ae70a99e720769ed7153bc3d  Wordmark-600.woff2
```

**This is an open obligation, written as open rather than as done.** OFL 1.1 requires the licence
text to travel with the fonts; `ui/static/fonts/LICENSE.txt` currently covers **IBM Plex only**. Two
things must happen before submission: confirm each family's licence from its upstream source, and
ship that text. Until then these faces are disclosed as unverified rather than described as cleared.
Because the repo owner obtained them from a friend's project rather than from upstream, the
provenance chain runs through a person, not a URL — which is exactly the case the warning below is about.

**Corrected 7 Sep 2026 (ADR-026).** This paragraph used to open "no asset in this repo comes from a
third-party template or portfolio repository". That is no longer true: the Kage assets listed under
**Pre-existing materials** came from another person's project, used with permission. The warning below is
therefore live rather than hypothetical, and it is exactly why the three new type families are flagged as
unverified. What still holds is the narrower claim — no asset here comes from a third-party
"awwwards-style" template or portfolio repository. Such repositories are typically MIT **for
their code only**, while their fonts, video and textures remain licensed to the original client —
and a hackathon submission is publication. Every asset here has a licence that names assets
explicitly, verified from the upstream file above rather than from a summary.

## Reference material (not shipped, not executed)
- `references/awwwards-3d/` — a 3D scroll-driven design guidance skill, **MIT** (Copyright (c) 2026 awwwards-3d skill
  authors). Used as a visual style reference (ADR-014, and again ADR-028). **Correction, 7 Sep 2026:** the old wording
  here said the libraries it refers to "will only be used if explicitly vendored". That was a promise, and it has
  since been kept, so it is now a fact and is written as one — GSAP 3.12.5 and Lenis 1.1.0 are bundled at build time
  and three.js r149 is vendored for `/kage`; all three are listed in the sections above with their licences. None of
  those three arrived *through* this skill.

  What ADR-028 took from it is **two PNG screenshots read by a human being**
  (`docs/screenshots/minimal-hero.png`, `coin-hero.png`) and the layout formula they show: near-black void, one
  wide radial light, numbered mono label, giant two-line title in the lower third, empty middle. That formula was
  re-implemented from scratch as ~40 lines of CSS in `frontend/styles/app.css`. **Zero lines of its code, zero of
  its assets, and zero of its dependency choices ship**, and this is not a matter of taste: its deliverable is an
  ESM importmap served from unpkg/esm.sh (our CSP is `script-src 'self'` and `make demo` must run offline), it pins
  three.js 0.170.0 against our vendored r149, and its assets are GLB/HDRI models. Nothing in
  `references/` is executed, imported, or served by this app.

## Datasets
- No third-party dataset at runtime. Citation references (not components): RED-PUMP-2026-v1 (CC BY 4.0) — only if
  used; MELT (CC BY-NC 4.0) — **NOT used** (the NonCommercial clause was rejected).

## External services (read-only) — only those with `approved: true` in `sources.yaml`
| Source | For | ToS | Permission status |
|---|---|---|---|
| RYO-CHAN tools (MCP/REST, app-ryochan.com/api/mcp) | all market data | provided by the organisers | approved |
| Anthropic Claude API (`claude-opus-5`, SDK `anthropic` 1.2.0) | per-item card narration from structured data; **never touches the decision** (tests guard `decision_hash`) | https://www.anthropic.com/legal/commercial-terms | "your own AI model" — RYO landing page |

RYO tool output is RYO data: used only inside the submission, not published in social posts
(ADR-008, `docs/rules-compliance.md`).

**Fixtures in the repo = copies of RYO output, not new third-party material.** `docs/samples/*.json` (6 live
envelopes, 1 Sep 2026), `agent/fixtures/*.json` and `preflight/fixtures/*.json` are all copied from the responses of
the tool above and are covered by the RYO-CHAN row; no dataset or new source enters through those files, so there is
no extra row here. One exception that must be named: in
`preflight/fixtures/outage-one-tool-2026-09-05.json` one envelope is **shaped** into `status: "unavailable"` because
RYO has never emitted that status (0 out of 3645 snapshots, `docs/api-facts.md` §A). The file declares this itself in
a `synthetic` block, and the `make demo` script prints that declaration — see README §6.

## Simulation notice
All positions are practice/simulated. No real funds, no wallet, no execution. Any swap analysed is a public
third-party on-chain transaction that was observed, not a trade of ours.
