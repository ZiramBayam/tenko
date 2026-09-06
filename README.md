# Tenko (点呼) — RYO-CHAN Hackathon 2026

> **SIMULATED — practice positions on read-only market intelligence.** Every position in this repo is a *practice
> trade*: no funds, no wallet, no signing keys, no real execution. Every output envelope carries `simulated: true`,
> every UI response carries it too (including 404/405/422/503), and every screen carries the SIMULATED banner.

**The problem:** retail traders — and almost every signal agent — act on a single verdict without checking all the
evidence that is available, and (more dangerously) without knowing which evidence is **not** available: stale data,
`unavailable` sections, a plan that does not exist. Common guardrails treat missing data as a pass, or as a zero
(`PRD.md` §1). **The solution, one product for three tracks** (ADR-012 — at most one BUIDL, `docs/api-facts.md` §E):
before each practice trade, Tenko calls out ten pieces of evidence from five RYO tools one at a time — pointing at
`tool.field`, speaking its value and its `as_of` — and then **does not depart** if there is one red item **or one item
that goes unspoken**; that call card is a first-class decision, readable on two keyboard-first screens, emitted as a
skill in a RYO envelope, and replayable byte for byte.

**What runs today (6 Sep 2026).** The demo script in `docs/spec.md` §7, steps 1–6, runs from a single command and two
runs are byte-identical; both screens run; the two-date demo flips the SOL decision from GO to NO_GO; the suite is
**1869 passed, 1 skipped** in 210.41 s and `make lint` is clean over 92 files (measured 6 Sep 2026 on this tree,
after the ADR-020 frontend rewrite landed — `uv run pytest -q`). What is **not** there is stated as not being there in §7, §8 and §11 — including the most unpleasant number in
this repo, which gets a section of its own in §7.

---

## 1. Architecture (`docs/spec.md` §2) — every box below runs

```
 RYO tools (6) ──► core.ryo_client (rate limit, fixtures) ──► core.store (append-only snapshot, hash)
                                                                     │
                                            preflight.skill ◄────────┘  freeze(snapshot_ids)
                                            10 items (spec §4) → GO | NO_GO + blocking
                                                     │                      │
                                             agent (practice trade      preflight.narrate (LLM,
                                              plan / declined)           never touches the decision)
                                                     │                      │
                                             agent.replay ID= (IDENTICAL | MISMATCH | 3 "we don't know" verdicts)
                                                     │
                                             ui: landing (/) · Screen A watchlist (/app) · Screen B call card
                                                 (/app/decision/{id}) · SIMULATED banner
 core.recorder (per cycle) ──► grading (make grade): outcome → item reputation, only with n (§7)
```

The rule that binds those arrows: **no LLM in the decision path** (ADR-003). The decision is a deterministic function
over frozen snapshots; the narration only translates the result, and it is proven not to move `decision_hash` even
when the card is poisoned and the narration is adversarial (`preflight/narrate.py`, `tests/test_narrate.py`). The
agent never imports the live client
(`tests/test_agent.py::test_agent_package_never_imports_the_live_client`), and `ui/` does not contain `httpx` or
`anthropic` at all — the screens only read decisions that are already stored.

---

## 2. How to run it

Prerequisites: Python 3.12 + [uv](https://docs.astral.sh/uv/) (`docs/versions.md`). **Running Tenko needs no Node.**
Node/npm are a build tool only, for the frontend, and the build output is tracked in git and shipped, so a clean clone
with no Node at all can still run `make ui`, `make test` and `make demo` (ADR-020, which narrows ADR-001 instead of
revoking it; zero npm runtime dependencies — `package.json` carries the literal `"dependencies": {}`). The build-time
toolchain is listed in `DISCLOSURE.md`; the frontend rewrite it belongs to **has landed** (§5), its output
(`ui/static/app.css`, `ui/static/app.js`) is tracked in git, and the build itself is not guaranteed to work offline —
only running Tenko is.

```bash
make doctor    # python 3.12.3 · uv 0.12.7 · sqlite 3.45.1 · 2 sumber aktif (2 active sources)
make demo      # spec §7 script, steps 1-6, entirely from offline fixtures
make test      # 1869 passed, 1 skipped (210.41 s, 6 Sep 2026 — see §7)
```

**`make demo` needs no network, no credentials and no recorder DB.** It builds a fresh SQLite DB in a temporary
directory from `agent/fixtures/*.json` + `preflight/fixtures/*.json` on every run, and it installs an audit hook that
**measures** that claim; the line is printed along with the rest: `penjaga: 0 berkas di data/ dibuka proses ini
(sys.addaudithook atas open + sqlite3.connect)` — "guard: 0 files under `data/` were opened by this process"
(`agent/demo.py:186`). `.env` is deliberately **not** loaded, because §7 step 4 demonstrates a dead LLM
(`scripts/demo.sh:15-18`). Two runs must be byte-identical:

```bash
make demo > a; make demo > b; diff a b     # empty (pinned by tests/test_agent_demo.py)
```

**Not a single recorder DB is shipped.** `data/` is in `.gitignore`, and timestamped market snapshots cannot be
recreated by anyone except the recorder that ran at that moment — so shipping them would not make them re-checkable
either. What you have after cloning is zero snapshots; **that DB is born on your own machine when `make record`
runs**, and `make demo` / `make test` do not wait for it. The consequence is stated honestly, and the tools keep
answering honestly rather than with a default number:

| Command | Needs what | Before your recorder has written anything |
|---|---|---|
| `make demo` · `make two-dates` · `make test` · `make lint` | nothing | runs in full from fixtures |
| `make ui` (`/`, `/app`, `/app/decision/{id}`) | a readable store | **503 with a SIMULATED banner**, not a fake empty page |
| `make skill ARGS="preflight SOL"` | a snapshot in the store | exit 0, envelope `status:"unavailable"`, `data:null` |
| `make agent-tick` · `make replay ID=` · `make grade` · `uv run python -m preflight.veto_table` | a local ledger/snapshot | says there is no evidence; does not invent numbers |
| `make record` | RYO credentials from env | this is the command that **creates** the store; it respects the rate limit (`docs/api-facts.md` §A) |

To **see both screens in a browser without a recorder** there is a fixture path: `uv run python -m agent.demo_fixture
--db /tmp/tenko-demo.db` writes a DB from the same fixtures the demo uses (`agent/demo_fixture.py:318`; its guard
refuses any destination inside `ROOT/data`, so fixture rows can never contaminate a recorder DB), then
`DB_PATH=/tmp/tenko-demo.db make ui` (the `DB_PATH` env var is read by `ui/app.py`, docstring item 2, and it was
demonstrably used by the judge when running the server against a copy of the DB —
`docs/judge-reports/fase-5.md` §destructive test b). **What we do run today as a single recipe is `make demo` step
5**, which renders both screens from the same application; the two commands above have not been run in sequence by us
and must not be read as tested.

---

## 3. "Point and call" as code — one unspoken item ⇒ NO_GO

This is the product. `make demo` §7 step 2 runs a fixture in which **one required tool answers `unavailable` and the
other four answer** (`preflight/fixtures/outage-one-tool-2026-09-05.json`, used by default by `scripts/demo.sh:28`).
The card — an **excerpt**; the `status=` line, `snapshot_ids=`, the ten call lines and the canonical envelope are cut
here (`…` marks the cut). `make demo` prints all of it:

```
=== §7 langkah 2 — satu tool wajib unavailable, sisanya menjawab ===
…
decision=NO_GO  blocking=02_completeness, 04_sentiment_shift
…
n_items=10 (green 7, red 1, unspoken 2)
…
```

The ten lines, item by item (pinned by a test, not retyped from a screen —
`tests/test_preflight_outage.py::test_one_required_tool_unavailable_makes_item_two_red_and_the_envelope_partial`):

| item | status | why it means what it means |
|---|---|---|
| `01_freshness` `03_regime` `05_verdict_quick` `06_confluence` `07_verdict_deep` `09_plan` `10_relative` | **green** (7) | evidence exists, is readable, and does not contradict the plan |
| `02_completeness` | **red** | one required tool answered `unavailable`; `reason` names that tool, and `source.tool` points at it |
| `04_sentiment_shift` | **unspoken** | its source is gone. A required item ⇒ **it joins `blocking`** even though it is not red |
| `08_derivatives` | **unspoken** | its source is gone too, but this item is optional (spec §4) ⇒ it does **not** veto |

What makes those two `unspoken` lines more than a label: **an `unspoken` item may not carry any value at all**,
including `0` and `0.0` — exactly the kind of lie we set out to prevent. Enforced by a validator, not by convention:
`core/skill.py::_unspoken_carries_no_value` · `tests/test_skill.py::test_unspoken_item_may_not_carry_a_value` ·
`availability` must mark `"unspoken"` **if and only if** that item is unspoken
(`::test_unspoken_item_must_be_marked_unspoken_in_availability`) · about 30 `*_unspoken_*` tests in
`tests/test_items.py` force null / `unavailable` / unknown vocabulary / non-finite numbers to become `unspoken` with a
`reason`, not zero. Run `make demo` to see the canonical JSON lines in full; we do not paste a hand-edited copy here.

**The shape is deliberately the RYO shape** (ADR-013/015). There is no formal spec for a T3 skill
(`docs/api-facts.md` §E), so the cheapest thing for RYO to consume is their own tool envelope: `SkillResult` carries
the **same ten top-level fields** (`schema_version, tool, status, data_mode, as_of, request, data, summary,
availability, warnings`) plus **exactly one** field, `simulated`, which `docs/rules-compliance.md` requires. That
invariant is tested mechanically against all six real envelope samples in `docs/samples/`
(`tests/test_skill.py::test_top_level_invariant_holds_for_all_six_samples`). What does **not** happen in the envelope:
no invented `data`, no `as_of` manufactured from `now()`, no zero standing in for "we don't know" — the validator
rejects non-null `data` when `status="unavailable"` and rejects `ok`/`partial` without data
(`core/skill.py::_honesty_invariants`).

---

## 4. Replay: the same decision, or a named accusation

```bash
make demo                    # step 3 runs replay + tamper in front of you
make replay ID=<trade_id>    # against your own local ledger
```

Inside `make demo` step 3 (fixtures, so the result is the same on anyone's machine): three ticks write 3 GO + 1 NO_GO,
`replay ID=1` and `ID=4` answer **IDENTICAL**, then one byte of `payload_json` is changed on a copy of the DB and the
verdict becomes **MISMATCH naming `snapshot_id=1`** together with both sha256 values (`docs/judge-reports/fase-5.md`,
§7 table row 3; pinned by
`tests/test_agent_replay.py::test_one_tampered_byte_in_the_copy_gives_mismatch_naming_the_snapshot`).

There are **five** verdicts, and only **one** of them means "verified": `IDENTICAL`
(`VERIFIED_VERDICTS = frozenset({VERDICT_IDENTICAL})`, `agent/replay.py:126`). `MISMATCH` (exit 1) is a **finding** —
the evidence does not reproduce the decision. `STALE_ALGO`, `STALE_PARAMS` and `NO_EVIDENCE` are **admissions of not
knowing**, not passes: four of the five exit 0, and exit 0 here means "the command produced a verdict", not "the
decision is verified" (`agent/replay.py:132-134`).

The ledger **our** recorder produced contains three generations because the store is append-only (ADR-006/016). That
DB is not shipped, so the ids below are a report about our machine, not a file you can open; the ledger your recorder
produces starts with one generation and will not show this branching until the algorithm or the params move. Rows
written by an implementation that has since been deleted are reported as `STALE_ALGO` (ids 1–19), rows from an earlier
params generation as `STALE_PARAMS` (ids 20–27), the rest are the current generation. We do not call any of those old
rows wrong — we state that we **are not judging them**. Hiding them would be tidier and less honest.

**The current generation's params are frozen, and the number is pasted here:**

```
params_hash = 3d39fb1bd1a03b7f5269f53fec347d34645d2b402ff1c2de47e53f19d69616b7
```

That value goes into every `decision_hash`, so changing any threshold would make every stored `decisions` row report
`STALE_PARAMS` on replay — the correct behaviour, not a bug, but also the reason the freeze is more than an intention:
a test **fails** if this value moves (`tests/test_params.py:242` `CURRENT_PARAMS_HASH`). Today's acceptance of ADR-017
(§8) does not move it — what changed is the docstring and the rationale, and the hash material only reads the value.

---

## 5. Two screens (T2) — `make ui`

| Screen | Route | Question answered in ≤30 s | Exactly the same JSON |
|---|---|---|---|
| 0. Landing | `/` | "what is this, and why should the missing evidence matter?" | server-rendered board, no fetch |
| A. Watchlist | `/app` | "which symbol is closest to GO, and what is holding the others back?" | `/api/watchlist` |
| B. Call card | `/app/decision/{id}` | "why this decision — evidence per row, from which tool/field, as of when?" | `/api/decision/{id}` |

Server-rendered, with **one** same-origin `<script src="/ui/app.js" defer>` per page since the ADR-020 rewrite landed
(commit `ed77a8c`), and **two** assets — `/ui/tenko.css` and `/ui/app.js`, both esbuild output from `frontend/`,
tracked in git as `ui/static/app.css` and `ui/static/app.js`. The CSP sent with every **HTML** response names **no
third-party host** and permits neither `unsafe-inline` nor `unsafe-eval`; scripts, styles and `fetch` targets are all
restricted to this origin, and framing, `base` and form submission are shut off entirely. The header itself lives in
`ui/app.py` `HTML_HEADERS` and is **deliberately not retyped here** — a pasted copy is exactly how the previous
version of this paragraph became false, and `ui/README.md` pins the header with a test. All navigation interaction is
`GET`, zero `tabindex`, focus ring **4.94:1**, zero `:hover`, zero animation; every card row is announced as one
"point → call" sentence via `aria-label`, and an `unspoken` row is announced as "not spoken", never as 0. The full
checklist, with **the name of the test that fails if a promise is broken**, is in `ui/README.md`.

**The contrast numbers are measured from the stylesheet the server actually hands out, not estimated** — and that is
the claim, not a courtesy: `tests/test_ui.py:2503`
`::test_every_colour_pair_in_the_served_stylesheet_is_measured_not_claimed` fetches `/ui/tenko.css` over the app
itself (so since ADR-020 it reads the esbuild output of `frontend/styles/app.css`, not any string that used to live
in Python), parses every `--name: #rrggbb` in `:root`, and demands ≥4.5:1 for text pairs and ≥3:1 for non-text —
**plus** that the colour list is exhausted by the measured pairs, so a new colour cannot slip in unmeasured. Three
values are pinned exactly: focus ring `warn` on `bg` = **4.94**, the tightest text pair `go` on the even row = **4.55**,
the tightest non-text pair `line` on the even row = **3.06**.

The rewrite in TypeScript, bundled with esbuild and served as a separate same-origin script file, was accepted on
6 Sep 2026 (ADR-020) and **landed the same day** (commit `ed77a8c`; sources in `frontend/src/*.ts`, output in
`ui/static/`). The earlier wording here — "zero `<script>`" and a pasted CSP string — described the version before
that commit and has been corrected above. What the rewrite did **not** change is the T2 score below, which was given
to the pre-rewrite screens.

Acknowledged limits, copied from `ui/README.md` §"Batas yang belum tertutup" (limits not yet closed): **the 30-second
recording of Screen B has not been made** (a human artifact; the scene script is in the same file, and a separate
video script was deliberately **not** written at the repo owner's request — `LOG.md`) · zero testing with a real
screen reader · zero third-party contrast audit · 200 % zoom untested.

The last third-party assessment scored T2 at 85/100 (`docs/judge-reports/fase-5-ulang.md`, cited in ADR-020). **That
score judged the pre-rewrite UI, which is no longer the UI you get when you run `make ui`**: it was given before
ADR-020 landed and it has **not** been re-scored since. So it is evidence about a version of the screens that this
repo no longer serves, and ADR-020 records exactly that as a "consciously accepted risk".

---

## 6. The §7 script step by step — status today

One command runs all six: `make demo`. The "evidence" column is output from the 6 Sep 2026 run that a judge also
verified (`docs/judge-reports/fase-5.md`, §7 table); run it yourself to get the same bytes.

| # | §7 step | Status | Evidence |
|---|---|---|---|
| 1 | `make skill ARGS="preflight SOL"` → 10 items, GO/NO_GO, blocking, snapshot_ids | ✔ | `status=partial data_mode=live decision=GO blocking=- snapshot_ids=[1,2,3,4,11] decision_hash=083d3ed0…`; 10 items, 10 green; canonical envelope printed in full |
| 2 | broken fixture → red item + unspoken item, no exception | ✔ (the outage envelope is **SYNTHETIC**, see below) | `decision=NO_GO blocking=02_completeness, 04_sentiment_shift`; `n_items=10 (green 7, red 1, unspoken 2)` (§3) |
| 3 | 3 ticks → ≥1 GO + ≥1 NO_GO; replay IDENTICAL; tamper → MISMATCH | ✔ | 3 GO + 1 NO_GO; `replay ID=1` & `ID=4` IDENTICAL; tamper → MISMATCH naming `snapshot_id=1` |
| 4 | per-item narration; LLM switched off → `narration_unavailable`, same hash | ✔ | 10-line card from `decisions.card_json`; `narration_unavailable`, `n_lines=0`; hash before = after = `083d3ed0…` |
| 5 | `make ui` Screen A/B, keyboard-first, SIMULATED banner | ✔ | `/app` 200, `/app/decision/9` 200, SIMULATED banner, `aria-label: 31 · tautan fokusable: 4 · handler hover/onclick: 0` (i.e. 31 `aria-label`s, 4 focusable links, 0 hover/onclick handlers). **Those counts were taken before the ADR-020 rewrite landed** (§5), so re-run `make demo` for today's numbers instead of quoting these |
| 6 | two dates, same symbol → different decision | ✔ | `make two-dates`: SOL **GO (2026-09-03) → NO_GO (2026-09-04)**, `03_regime` & `08_derivatives` green→red, same `params_hash` so the only thing that changed is **the evidence** (`agent/two_dates.py:9-12`) |

**Step 2 uses a SYNTHETIC envelope, and the script says so itself.** RYO has **never** emitted `status:
"unavailable"` in our observation — **0 out of 3645 snapshots** (`docs/api-facts.md` §A, sweep of 6 Sep 2026,
`id <= 3645`). So one envelope in the step-2 fixture is **shaped** into `unavailable`; the other envelopes are
verbatim copies of real evidence. The script counts for itself which envelopes are shaped and compares that with the
file's own declaration: if the two disagree it prints `KETIDAKCOCOKAN` ("mismatch") (`agent/demo.py`
`_disclosure_mismatch`). The hole reported in `docs/judge-reports/fase-5.md` — a fixture whose `synthetic` block is
**deleted entirely** passing silently — is **closed**: the absence of that block is the third kind of disagreement, so
a file carrying an outage-shaped envelope while declaring nothing is called "pemadaman yang TIDAK DIAKUI" ("an
UNDECLARED outage") (`agent/demo.py` `_disclosure_mismatch`, argument `declared`). A third party attacked it again
with four variants and got `KETIDAKCOCOKAN` **2×** on the block-deletion variant
(`docs/judge-reports/fase-5-ulang.md`, variant table V3/V3b).

The residue that remains, and we name it because it is real: if a fixture row **carries no `source_payload_hash`**,
the script has no key to compare against and falls from an accusation to a soft note — it prints "TIDAK DIVERIFIKASI
di sini: barisnya tidak membawa `source_payload_hash`" ("NOT VERIFIED here: the row carries no
`source_payload_hash`") instead of `KETIDAKCOCOKAN` (variant V5 in the same report). That is technically honest; what
does **not** yet exist is a rule that an unverified row is not fit to play an outage.

Besides the two-date demo, `make two-dates` carries one sharper case: **a DGAI NO_GO caused by MISSING evidence, not
by bad numbers**. On 2026-09-03, of the six items holding DGAI back **five are `unspoken`** and the only red one
(`02_completeness`) is red precisely because of that hole — `deep_analysis` answered but reported
`availability.trade_plan='unavailable'`, so `09_plan` became `unspoken` and **was named** in `blocking`
(`agent/two_dates.py:13-18`). Same market, same tools: one symbol held back by **a number**, one symbol held back by
**an answer that does not exist**, and the card says which is which.

---

## 7. The most unpleasant number in this repo: k = 0

`make grade` judges decisions that are ≥24 h old and asks one thing: **did this veto save anything?** Today's answer,
over our local ledger (6 Sep 2026, 24 h horizon, `grading/outcomes.py:68`):

- **13 decisions could be judged. `k = 0`.** Zero losses demonstrably prevented by any item.
- **`target_first = 3`.** Three times a veto held back a plan that **hit its target first** — that is, three times the
  veto was wrong. That sentence is printed by the measuring tool itself, with the same denominator:
  `"yang justru menyentuh target lebih dulu (veto ini salah)"` — "which hit the target first instead (this veto was
  wrong)" (`grading/grader.py:100`).
- **`n_efektif` 1–5 against a threshold of 30** (`grading/stats.py:80`) → **zero confidence intervals printed**, and
  Bunryō (position sizing from reliability, spec §4 item 11) closes with `TIDAK ada takaran yang dikeluarkan` ("NO
  sizing is issued") (`grading/grader.py:537`).

`n_efektif` is not `n`: the observations here are **not independent** — `market_overview` and
`monitor_market_sentiment_shift` are called with the argument `{}`, so all four symbols read the same row and one
market event can be counted up to 4×. `grading/stats.py` deflates `n` to `min(n, number of distinct evidence events)`
precisely so the interval does not look confident because of duplication. The consequence: **we have no evidence that
these ten items produce better decisions.** What we have is a decision that can be pointed at, spoken, replayed and
argued with — and a measuring tool that says "not enough data yet" instead of printing a small, convincing number.

The rest, with their `n` and period:

| Measurement | Number | n & period | Command / report |
|---|---|---|---|
| Chaos (host down · kill -9 · 429 · DB deleted · credentials revoked · evidence gap) | **`LULUS=8 GAGAL=0`** (PASS 8 / FAIL 0), zero tracebacks | 8 scenarios, 5 Sep 2026 23:27–23:30 JST — **measured before the ADR-020 rewrite and deliberately not re-run** (see the note under this table) | `bash scripts/chaos.sh` → `docs/reports/chaos-2026-09-05.md` |
| Evidence gap: `preflight()` over holed evidence | **0 raised**; distribution `{partial: 156, unavailable: 268}` | n = 424 calls | same report, `evidence_gap` scenario |
| Item reputation from outcomes | **k = 0**, `target_first = 3`, zero intervals | 13 decisions judged, 24 h horizon, 6 Sep 2026 | `make grade` |
| Empirical veto power per item | `01_freshness` **0× red**; `06_confluence` **82× red** | n = 201 observations (4 core symbols × recorder anchors, window 2026-09-01T06:29Z–09-05T12:15Z), over a frozen copy with **`max(snapshots.id) = 3608`**, 25 anchors dropped by the scan limit | the left-hand numbers come from the DB **our** recorder produced (not shipped). Recipe against your own DB: `cp <your recorder DB> /tmp/frozen.db` then `uv run python -m preflight.veto_table --db /tmp/frozen.db --until 2026-09-05T12:32:00Z`; the full per-item table is in `preflight/items.py:94-105` |
| ↑ third-party repetition, a **different** frozen copy | `01_freshness` **0× red**; `06_confluence` **75× red** | n = 186, **`max(snapshots.id) = 3981`**, exactly the same `--until` | `docs/judge-reports/fase-5-ulang.md` — `n` moves, its direction does not; this is why `max(id)` must always be quoted |
| Suite | **1869 passed, 1 skipped** (210.41 s) | 6 Sep 2026, our working tree, **after** the ADR-020 frontend rewrite and the security fixes landed; `make lint` clean over 92 files. An earlier README printed **1852**, which no run supported; a third party measured **1848** on the pre-rewrite tree (`docs/judge-reports/fase-5-ulang.md`) | `make test` = `uv run pytest -q` (on a clean clone the number is smaller: twelve parity tests for a local lint/compliance script **SKIP** with a readable reason — that script is a work tool, not part of the application, and is deliberately not shipped; `LOG.md`) |
| RYO envelopes that were ever `unavailable` | **0 out of 3645** | sweep of 6 Sep 2026, `id <= 3645` | `docs/api-facts.md` §A |

**The two chaos rows are the only numbers above that predate the ADR-020 rewrite, and they were not refreshed on
purpose.** `scripts/chaos.sh` `kill -9`s the recorder mid-cycle, and nothing brings it back automatically — that is a
known hole with a measured cost (TASKS 0.8c: eight manual restarts, a 387-minute gap). Re-running it two days before
the deadline risks leaving the recorder dead and tearing a fresh, unrecreatable hole in the snapshot history, so the
repo owner chose not to. Read those two rows as evidence about the pre-rewrite tree; every other row carries its own
date, `n` and command.

**Three warnings attached to that table, which may not be detached:**

1. **`01_freshness` has never held anything back — 0× red in n=201, and 0× again in the third-party repetition
   (n=186); the cause is structural.** The evidence picker already bounds age to `params.max_age_min` **before** any
   item is evaluated (`preflight/skill.py:536`). So that row is a **receipt**, not a veto: it states "the evidence
   used really was inside the window", and evidence outside the window never reaches it. What has been done about
   that finding (`docs/judge-reports/fase-5.md`, item 11): **the sentence** confesses, not the name — item 1's green
   `reason` says for itself "…so this says nothing about how old the bundle itself is. Absolute age was already
   enforced when the evidence was picked … which is why this line is a receipt and has never blocked a departure"
   (`preflight/items.py:677-684`), and its `ItemDef.title` is now "Envelope ages agree (absolute age enforced at
   selection)" (`preflight/items.py:1966`). What was deliberately **not** done: the id `01_freshness` was not
   renamed — ids go into `params_hash`/`decision_hash`, so renaming it would move the hash of every stored `decisions`
   row. What is **still** owed: that title never reaches any screen (§11 item 5).
2. **The "veto power" ordering shown on both screens is a spec §4 design constant, not validated**
   (`preflight/params.py:163` `ITEM_ORDER`). This repo's own measuring tool contradicts it: the item ranked 1 has
   never been red, the item ranked 6 was red 82× (75× on the third-party copy). The ordering is useful as "which one a
   human reads first", not as an empirical claim — and **both screens say so themselves**: the column heading rendered
   on Screen A and Screen B reads `veto power (spec §4, not validated)` (`ui/screens.py` `VETO_RANK_COLUMN`, and the
   full screen note in `ui/app.py` `VETO_RANK_UNVALIDATED_SENTENCE`).
3. **The veto-table numbers do not repeat with `--until` alone.** The recorder adds rows every cycle, so the same
   anchor yields a different `n` a few days later (measured 201 → 237 against the live DB). What repeats the numbers
   is `--db <frozen copy>`, and **the recipe already reads that way**: `cp <your recorder DB> /tmp/frozen.db` then
   `--db /tmp/frozen.db --until …` (`preflight/veto_table.py:66-69`, `preflight/items.py:63-66`). The tool also tells
   you when its anchor population has shifted (`scan_limited` up, `n` down, `max(snapshots.id)`, all three in the
   header). A third party proved why this is mandatory: exactly the same `--until` over a different copy
   (`max(snapshots.id) = 3981`) gives **n=186**, not 237 (`docs/judge-reports/fase-5-ulang.md`). That is why every
   veto number in this README is quoted together with the state of the copied DB.

---

## 8. What we do NOT claim

- **No claim that the veto is right.** See §7: `k = 0`, `target_first = 3`, zero confidence intervals. "Red" means
  that item held back the departure, not that holding back was correct.
- **No trading performance claim.** All positions are practice, zero P&L; position size is fixed
  (`agent/trade.py` `"sizing": "fixed_notional"`) — there is no path in which reliability moves size.
- **GO does not mean "the evidence supports it".** It means "no item objects". On the live GO card in our ledger
  (id 49), three items were `neutral` and still green: green here means "no objection", not "support"
  (`preflight/params.py:511` `neutral_stance_status`; **ADR-017, ACCEPTED 6 Sep 2026** — the code has behaved that way
  since 2 Sep, so what changed today is the rationale, not the behaviour, and `params_hash` did not move). The card
  says this in a sentence, not just with a colour.
- **Item 9 tests nothing today.** `risk_reward[0] ≥ min_rr` with `min_rr = 1.0` while `risk_reward = [1.0, 2.0]` in
  **1476/1476** `deep_analysis` rows that carry a `trade_plan` — out of 1496 rows; the other 20 have
  `trade_plan=null`, so item 9 is `unspoken` there, not green. Sweep over a **frozen copy** of our recorder DB (not
  shipped) on 6 Sep 2026, `id <= 3919`, last row `2026-09-06T00:13:23Z` (`preflight/items.py:1527-1541`
  `RR_ROWS_WITH_PLAN`; `preflight/params.py:69-71`). The threshold is a tautology on the evidence that exists, so
  item 9's `reason` now **carries that number inside its sentence** (`RR_CONSTANT_NOTE`) instead of stopping at "worth
  its risk". An attached note that may not be detached: the store is append-only, so **already-stored cards do not
  change** when item text is fixed — a third-party check on 6 Sep 2026 found **0 out of 52** cards in our ledger
  carrying the new sentence and **29** still carrying the old one, including the most recent card
  (`docs/judge-reports/fase-5-ulang.md`, finding 2). Only new ticks produce cards with the new sentence; old cards are
  deliberately not rewritten.
- **A fresh `as_of` does not guarantee a fresh envelope body, and item 1 does not catch that.** Across 47 real
  `monitor_market_sentiment_shift` rows, 47/47 carry `fear_greed`/`altseason` with `data_date` D-0 but
  `funding`/`liquidation` with `data_date` **D-1** (evidence age 25.2–47.5 hours, median 35.8 hours;
  `docs/api-facts.md` §A, sweep of 5 Sep 2026). What we did: the card **speaks it** — item 8 carries
  `funding_data_date` and `liquidation_data_date` in `value`, and its `reason` reads "evidence dates: funding from …
  (1d before this reading)" in every branch, with "date not stated" when the date is null. What we did **not** do, and
  that is a written decision: veto on it — the D-1 state is normal 47/47, so vetoing would make item 8 red forever. So
  do not read item 1 as a promise about the freshness of an envelope's contents.
- **No RYO tool reliability numbers in any public document.** RYO tool output is RYO data: inside the submission
  only, never in a social post (ADR-008, `docs/rules-compliance.md`).
- **A veto by a required `unspoken` item has so far only fired in fixtures.** That is this product's flagship feature
  (§3, §9), and in our live decision ledger it has never happened: a third-party check found **zero** blockers with
  status `unspoken` across 29 current-generation decisions (`docs/judge-reports/fase-5-ulang.md`, question 1). What
  demonstrates it — `make demo` step 2 and the DGAI case in `make two-dates` — runs from frozen fixtures, and §3 and
  §6 say so.
- **No "production-ready", "audited" or "100 %".** What exists: 1869 green tests (§7) — a measure of our own
  checking coverage, not proof that the decisions are right — and a list of reproducible failures written by a third party in
  `docs/judge-reports/fase-5.md` + `fase-5-ulang.md`, including the still-open fixture-declaration residue (§6) and
  the `ItemDef.title` that never reaches a screen. One judge finding was closed after the report was written: cards
  **from decision 53 onward** (tick of 6 Sep 2026) speak the new item 9 and item 1 sentences — checked directly in
  `decisions.card_json`. Cards 1–52 keep speaking the old sentences and **always will**: the store is append-only
  (ADR-006), so frozen artifacts are not rewritten. That is a deliberate design consequence, not debt.
- **The 30-second recording of Screen B does not exist yet** (§5).

---

## 9. How this differs from a signal agent + guardrail, and from a P&L arena

- **Missing data vetoes, and never becomes a number.** Common guardrails treat an empty field as a pass (no rule
  fires) or as zero (a rule fires on an invented number). Here a required `unspoken` item enters `blocking` just like
  a red item, is recorded **separately** from red, and the validator **rejects** an `unspoken` item that carries a
  value — including `0` (§3).
- **NO_GO is a first-class decision, not the absence of one.** It has a ledger row, a ten-line card, a
  `decision_hash` and a page of its own. Most agents only record what they did; here what was **not** done also has an
  address and a cause that can be pointed at, down to `tool.field` + `snapshot_id` + `as_of`.
- **The trail can be argued with, not merely displayed.** `make replay` re-judges from frozen snapshots; one changed
  byte produces a MISMATCH that **names the snapshot** (§4). What cannot be verified gets its own "we don't know"
  verdict rather than a silent pass.
- **Not a P&L arena.** The official rubric does not score short-term P&L or API call volume (`docs/api-facts.md` §E),
  and this repo does not print a single P&L number. What is at stake is the quality of the decision bookkeeping —
  including the nerve to print `k = 0` about itself (§7).
- **A public card is fact, not judgement.** The card states values, sources and times; it does not say anyone is
  wrong, and it does not rate RYO tools in public (ADR-008).

---

## 10. Tracks & compliance

One BUIDL, three tracks (`docs/api-facts.md` §E: at most one BUIDL per team; each track is judged separately).

| Track | What is submitted | One-command evidence |
|---|---|---|
| **T1** agent | practice-trade agent + 10-item call card + a replay that can contradict itself | `make demo` (steps 1–4, 6), `make replay ID=` |
| **T2** screens | Screen A watchlist + Screen B card, keyboard-first, SIMULATED banner, one same-origin script and zero third-party hosts since the ADR-020 rewrite landed (§5) | `make ui`, `make demo` step 5, `ui/README.md` |
| **T3** skill | `preflight(symbol, intent)` in a RYO envelope + one `simulated` field | `make skill ARGS="preflight SOL"`, `make demo` step 1 |

Compliance (`docs/rules-compliance.md`):

- **Credentials come from env only**, are never logged, never `repr`-ed, and never appear in tests or screenshots.
  What enforces this **inside the submission** is the code and its tests: the client redacts credentials
  (`core/ryo_client.py`), the CLI never prints another party's exception message (`core/skill_cli.py`), and logs write
  only `type(exc).__name__`. One control outside the code is shipped as well: **`scripts/guard.sh`**, which blocks
  commands that would print credentials to the screen — and it demonstrably bit someone who is not us: two of a
  third-party reviewer's own commands were blocked when the key variable name was mentioned
  (`docs/judge-reports/fase-5.md` §destructive test d). One local lint/compliance script is **not** shipped — it is a
  work tool, not part of the application (`LOG.md`); a clean clone does not have it, its twelve parity tests SKIP with
  a readable reason, and this repo must not be judged on a control that is not inside it. Value-by-value judge scans
  over tracked files + all of `logs/` → **0 matches on two consecutive assessments** (125 files,
  `docs/judge-reports/fase-5.md`; 120 files, `docs/judge-reports/fase-5-ulang.md`).
- **SIMULATED** in the README (first paragraph), in both screens' banners, in every CLI envelope, in every API
  response including 400/404/405/422/503, and in the closing line of `make demo`.
- **External sources only where `enabled: true` AND `approved: true`** in `sources.yaml` (ADR-005) — two today: the
  RYO tools and one LLM for narration. The LLM's `approved` rests on "your own AI model" on the landing page, not on a
  written answer from the organisers; it is the only self-asserted `approved`, and narration sits outside the decision
  path so the demo runs without it.
- **Dependencies, assets and third-party sources** are listed in `DISCLOSURE.md` in the same commit, guarded in both
  directions against `uv.lock` by `tests/test_disclosure.py`.
- **RYO tool output is not published** in social posts (ADR-008); post drafts are in `docs/posts/`.

---

## 11. Honest roadmap

Everything below **does not exist** today, and is written as not existing. The first two are features that may never
be built; the rest is debt we named ourselves above.

1. **Item reputation that actually feeds back into the decision.** Today `grading/` is a **measuring** tool, not a
   **deciding** one: not a single number from it enters `preflight`. The requirement is n ≥ 30 per class (spec §6);
   with `n_efektif` at 1–5 today (§7), the answer is "not enough data yet".
2. **The 11th item, Bunryō** (position sizing from regime-conditional reliability, spec §4). `make grade` already
   prints its cell and already refuses to issue a size. As long as the gate of 30 is not passed, this item **will
   not** appear — not estimated, not loosened.
3. **Refusing an outage fixture whose row carries no `source_payload_hash`** — the V5 residue in §6: without that key
   the script can only note "NOT VERIFIED", and there is no rule yet stating that such a row is unfit to play an
   outage.
4. **Bringing item titles to a screen, or deleting `ItemDef.title`.** Today the item title has exactly one consumer —
   an assertion in a test — and appears zero times in `make demo` or in either screen's HTML
   (`docs/judge-reports/fase-5-ulang.md`, finding 1). What humans actually read is the tail of `reason`, so item 1's
   new title (§7 warning 1) has never been seen by anyone. The id `01_freshness` itself **will not** be renamed: ids
   go into `decision_hash` (§7 warning 1).
5. **The 30-second recording of Screen B** (a human artifact; the scene script is in `ui/README.md`. A separate video
   script **was not written and will not be written** — skipped at the repo owner's request, `LOG.md`).

Three lines that stood on this list at the previous assessment are now **closed**, so they are no longer here — and
that was re-verified by a third party, not by us (`docs/judge-reports/fase-5-ulang.md`): the §7 step 2 declaration
hole (§6), the "not validated" status on the veto-power column on **both** screens (`ui/screens.py`
`VETO_RANK_COLUMN`, `ui/app.py` `VETO_RANK_UNVALIDATED_SENTENCE`), and the veto-table reproduction recipe with `--db <frozen copy>` (`preflight/veto_table.py:66-69`).

Decision history, review findings that changed the code, and debt that is still open: **`LOG.md`**. The latest
third-party assessment of this repo — including per-category scores, a list of reproducible failures, and five
questions that are not all answered yet — is in `docs/judge-reports/fase-5.md` and
`docs/judge-reports/fase-5-ulang.md`.
