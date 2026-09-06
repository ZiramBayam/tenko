// Layar A & B, dirakit di klien dari badan JSON yang SAMA dengan yang dipakai server.
//
// Yang dijaga di berkas ini, dan yang menjadi seluruh alasan produk ini ada:
//
// 1. **Nol perhitungan.** Setiap angka yang muncul di sini datang apa adanya dari respons API.
//    Klien tidak menjumlah, tidak merata-rata, tidak menurunkan keputusan, dan tidak menghitung
//    umur relatif (lihat `AGE_CLIENT_NOTE`). Satu-satunya urutan yang boleh dibuatnya adalah
//    urutan yang dipilih pemakai dari kolom yang SUDAH ada di respons — dan urutan bawaannya
//    adalah urutan server, tidak disentuh.
// 2. **`unspoken` tetap `unspoken`.** Tidak pernah 0, tidak pernah sel kosong, tidak pernah
//    tanda hubung yang menyamar jadi nilai: nilai yang tidak ada dirender sebagai KATA
//    (`ABSENT_MARK`) dengan warna `--warn` yang sama dengan `unspoken`.
// 3. **Teks, bukan HTML.** Semua lewat `el()`/`bdi()` di `dom.ts`.

import { append, bdi, clear, el } from "./dom.js";
import type { Json } from "./json.js";
import { dicts, num, obj, scalar, strings, text } from "./json.js";
import { closestToGo, heldCount } from "./order.js";
import {
  ABSENT_MARK,
  AGE_CLIENT_NOTE,
  ARIA_NO_SOURCE,
  AGE_NOTE,
  ARIA_NULL,
  EMPTY_SENTENCE,
  NARRATION_EMPTY,
  NARRATION_LABEL,
  NARRATION_NOTE,
  NEUTRAL_MARK,
  UNSPOKEN_MARK,
  VETO_RANK_COLUMN,
} from "./text.js";

const CARD_COLUMNS = [
  VETO_RANK_COLUMN,
  "item",
  "status",
  "reason (deterministic, replayable)",
  "value spoken",
  "source: tool.field",
  "snapshot",
  "evidence as_of",
  NARRATION_LABEL,
];

const WATCHLIST_COLUMNS = [
  "symbol",
  "decision",
  `${VETO_RANK_COLUMN} — blocker`,
  "what holds it (and where that came from)",
  "blockers / rows",
  "unspoken",
  "evidence",
  "replay",
  "bundle as_of",
];

/** Sel untuk nilai yang boleh TIDAK ADA. Ketiadaan selalu keluar sebagai kata, bukan kekosongan. */
function said(value: Json, lang?: string): HTMLElement {
  if (value === null || value === undefined) return el("span", { class: "empty" }, ABSENT_MARK);
  return bdi(scalar(value), lang);
}

function mark(note: string): HTMLElement {
  return el("span", { class: "mark" }, note);
}

function badge(cls: string, label: string): HTMLElement {
  return el("span", { class: `badge ${cls}` }, label);
}

function decisionBadge(value: Json): HTMLElement {
  const label = text(value) ?? "?";
  const css = label === "GO" ? "d-GO" : label === "NO_GO" ? "d-NO_GO" : "d-unknown";
  return badge(css, label);
}

function replayBadge(row: Record<string, Json>): DocumentFragment {
  const tone = text(row["replay_tone"]);
  const css = tone && /^[A-Za-z_][A-Za-z0-9_]*$/.test(tone) ? tone : "unknown";
  const fragment = document.createDocumentFragment();
  append(
    fragment,
    badge(`tone-${css}`, text(row["replay_status"]) ?? "NOT RUN"),
    mark(text(row["replay_label"]) ?? "not assessed"),
  );
  return fragment;
}

/** Kembaran `ui/screens.py:_status_words` — satu turunan untuk sel DAN untuk `aria-label`. */
function statusWords(item: Record<string, Json>): { label: string; css: string; note: string } {
  const label = text(item["status"]) ?? "?";
  const stance = text(obj(item["value"])["stance"]);
  let css = /^[A-Za-z_][A-Za-z0-9_]*$/.test(label) ? `s-${label}` : "s-unknown";
  let note = "";
  if (stance === "neutral") {
    note = NEUTRAL_MARK;
    if (label === "green") css = "s-green-neutral";
  } else if (label === "unspoken") {
    note = UNSPOKEN_MARK;
  }
  return { label, css, note };
}

function ariaStatus(item: Record<string, Json>): string {
  const { label, note } = statusWords(item);
  return note ? `status ${label}, ${note}` : `status ${label}`;
}

function ariaValue(item: Record<string, Json>): string {
  const { note } = statusWords(item);
  const value = item["value"];
  if (note === UNSPOKEN_MARK) {
    const head = `value: ${UNSPOKEN_MARK}`;
    if (value === null || value === undefined) return head;
    return `${head}; what is readable in the evidence: ${scalar(value)}`;
  }
  if (value === null || value === undefined) return `value: ${ARIA_NULL}`;
  return `value spoken: ${scalar(value)}`;
}

function ariaSource(item: Record<string, Json>): string {
  const tool = text(item["tool"]);
  const field = text(item["field"]);
  if (tool === null && field === null) return ARIA_NO_SOURCE;
  const parts = [`source ${tool ?? "tool not stated"}.${field ?? "field not stated"}`];
  if ("snapshot_id" in item) {
    const snapshot = num(item["snapshot_id"]);
    parts.push(snapshot === null ? "snapshot not stated" : `snapshot ${snapshot}`);
  }
  const asOf = text(item["as_of_jst"]);
  parts.push(asOf === null ? "as_of not stated" : `as_of ${asOf}`);
  return parts.join(", ");
}

function aria(...parts: (string | null)[]): string {
  return parts.filter((part): part is string => !!part && part.trim() !== "").join("; ");
}

function phrase(label: string, value: Json, absent: string): string {
  const spoken = value === null || value === undefined ? null : scalar(value);
  return spoken === null || spoken.trim() === "" ? absent : `${label} ${spoken}`;
}

function headRow(columns: string[]): HTMLElement {
  const row = el("tr");
  for (const name of columns) append(row, el("th", { scope: "col" }, name));
  return row;
}

function notesList(payload: Record<string, Json>): DocumentFragment {
  const fragment = document.createDocumentFragment();
  const notes = strings(payload["notes"]);
  if (notes.length === 0) return fragment;
  const list = el("ul", { class: "notes" });
  for (const note of notes) append(list, el("li", {}, note));
  append(fragment, el("h2", {}, "Screen notes"), list);
  return fragment;
}

// --- Layar A --------------------------------------------------------------------------------

function watchlistRow(entry: Record<string, Json>): HTMLElement {
  const blocker = obj(entry["top_blocker"]);
  const hasBlocker = Object.keys(blocker).length > 0;
  const decisionId = num(entry["decision_id"]);
  const symbol = text(entry["symbol"]);
  const unspoken = num(entry["n_unspoken"]);

  const label = aria(
    phrase("symbol", entry["symbol"], "symbol not stated"),
    phrase("decision", entry["decision"], "decision not stated"),
    phrase("held by", entry["n_blocking"], "blocker count not stated") +
      phrase(" of", entry["n_items"], " of an unstated number of") +
      " roll-call rows",
    hasBlocker
      ? [
          phrase("heaviest blocker", blocker["item_id"], "heaviest blocker has no id"),
          phrase("veto power", blocker["veto_rank"], "veto power not stated"),
          ariaStatus(blocker),
          ariaSource(blocker),
        ].join("; ")
      : "zero rows hold this decision back",
    unspoken === null ? "the count of unspoken rows is not stated" : `${unspoken} rows unspoken`,
    phrase("bundle as_of", entry["as_of_jst"], "bundle as_of is unreadable"),
  );

  const row = el("tr", { "aria-label": label });
  const head = el("th", { scope: "row" });
  if (decisionId !== null) {
    const link = el("a", {
      href: `/ui/decision/${decisionId}`,
      "aria-label": aria(
        phrase("open the roll-call card for", entry["symbol"], "open this symbol's roll-call card"),
        phrase("decision", entry["decision"], "decision not stated"),
        phrase("card number", entry["decision_id"], "no card number"),
      ),
    });
    append(link, said(symbol));
    append(head, link);
  } else {
    append(head, said(symbol), mark("this row has no id that can be opened"));
  }

  const held = el("td");
  if (hasBlocker) {
    const name = el("strong");
    append(name, said(blocker["item_id"]));
    const reason = el("span", { class: "mark" });
    append(reason, said(blocker["reason"], "en"));
    const source = el("span", { class: "mark" });
    source.textContent =
      `source ${text(blocker["tool"]) ?? ABSENT_MARK}.${text(blocker["field"]) ?? ABSENT_MARK}` +
      ` · as_of ${text(blocker["as_of_jst"]) ?? ABSENT_MARK}`;
    append(
      held,
      name,
      document.createTextNode(
        ` (veto power ${scalarOrAbsent(blocker["veto_rank"])}, status ${scalarOrAbsent(blocker["status"])})`,
      ),
      reason,
      source,
    );
  } else {
    append(held, mark("zero rows hold this decision back"));
  }

  const counts = el("td", { class: "num" });
  append(
    counts,
    said(entry["n_blocking"]),
    document.createTextNode(" / "),
    said(entry["n_items"]),
  );

  const evidence = el("td", { class: "num" });
  append(
    evidence,
    said(entry["n_snapshots"]),
    document.createTextNode(" snapshots"),
    el("br"),
    said(entry["n_warnings"]),
    document.createTextNode(" warnings"),
  );

  const replay = el("td");
  append(replay, replayBadge(entry));

  const asOf = el("td");
  append(asOf, said(entry["as_of_jst"]), mark(AGE_CLIENT_NOTE));

  const decision = el("td");
  append(decision, decisionBadge(entry["decision"]));
  const rank = el("td", { class: "num" });
  append(rank, said(entry["veto_rank"]));
  const unspokenCell = el("td", { class: "num" });
  append(unspokenCell, said(entry["n_unspoken"]));

  append(row, head, decision, rank, held, counts, unspokenCell, evidence, replay, asOf);
  return row;
}

function scalarOrAbsent(value: Json): string {
  return value === null || value === undefined ? ABSENT_MARK : scalar(value);
}

function watchlistLede(entries: Record<string, Json>[], total: number | null): DocumentFragment {
  const fragment = document.createDocumentFragment();
  const nearest = closestToGo(entries);
  if (nearest === null) {
    append(fragment, el("p", { class: "answer empty" }, EMPTY_SENTENCE));
    return fragment;
  }
  const held = heldCount(entries);
  const answer = el("p", { class: "answer" });
  // `n` datang dari respons (`payload.n`), BUKAN dari panjang daftar yang sedang tampil: baris
  // yang disaring keluar tetap ada, dan judul yang menghitung baris yang tersisa akan
  // mengecilkan jumlah simbol yang benar-benar dibaca. Kalau saringan sedang menggigit, kalimat
  // ini mengatakan keduanya.
  const shown = total !== null && total !== entries.length ? `n=${entries.length} of ` : "n=";
  answer.textContent = `${shown}${total ?? entries.length} symbols read (one row = that symbol's LAST decision); ${held} held among the rows shown; `;
  if ((num(nearest["n_blocking"]) ?? -1) > 0) {
    const blocker = obj(nearest["top_blocker"]);
    answer.textContent +=
      `nothing is GO; the closest is ${text(nearest["symbol"]) ?? ABSENT_MARK}, held by ` +
      `${scalarOrAbsent(nearest["n_blocking"])} rows, heaviest ` +
      `${text(blocker["item_id"]) ?? ABSENT_MARK} (veto power ${scalarOrAbsent(blocker["veto_rank"])}).`;
  } else {
    answer.textContent += `closest to GO: ${text(nearest["symbol"]) ?? ABSENT_MARK} — zero rows hold it.`;
  }
  const sub = el("p", { class: "sub" });
  sub.textContent =
    'Ordered by veto power: the heaviest blocker (spec §4 rank 1) on top. "Closest to GO" is read ' +
    "backwards from that order: the lightest heaviest-blocker first, then the fewest blockers — " +
    "not a score, not a forecast. The rank itself is not validated: it is a spec §4 design " +
    "constant, and this repo's own instrument contradicts it — see Screen notes below.";
  append(fragment, answer, sub);
  return fragment;
}

export function renderWatchlist(
  payload: Record<string, Json>,
  entries: Record<string, Json>[],
  target: HTMLElement,
): void {
  clear(target);
  append(target, el("h1", {}, "Tenko 点呼 — pre-departure roll call (simulated)"));
  const links = el("p", { class: "sub" });
  append(links, el("a", { href: "/api/watchlist" }, "the JSON behind this screen, verbatim"));
  if (entries.length > 0) {
    append(
      links,
      document.createTextNode(" · "),
      el("a", { href: "#watchlist" }, "skip to the watchlist table"),
    );
  }
  append(target, links, watchlistLede(entries, num(payload["n"])));

  if (entries.length > 0) {
    const table = el("table", { id: "watchlist" });
    const caption = el("caption", { class: "sub" });
    caption.textContent =
      `n=${entries.length} symbols; read from the ${scalarOrAbsent(payload["scan_limit"])} MOST ` +
      `RECENT decision rows, not the whole table. ${scalarOrAbsent(payload["n_legacy_hidden"])} ` +
      "earlier-generation rows are not shown here.";
    const thead = el("thead");
    append(thead, headRow(WATCHLIST_COLUMNS));
    const tbody = el("tbody");
    for (const entry of entries) append(tbody, watchlistRow(entry));
    append(table, caption, thead, tbody);
    append(target, table);
  }
  append(target, notesList(payload));
}

// --- Layar B --------------------------------------------------------------------------------

function itemRow(item: Record<string, Json>): HTMLElement {
  const { label, css, note } = statusWords(item);
  const row = el("tr", {
    "aria-label": aria(
      phrase("row", item["item_id"], "row without an item_id"),
      phrase("veto power", item["veto_rank"], "veto power not stated"),
      ariaStatus(item),
      ariaValue(item),
      ariaSource(item),
    ),
  });

  const rank = el("td", { class: "num" });
  append(rank, said(item["veto_rank"]));
  const name = el("th", { scope: "row" });
  append(name, said(item["item_id"]));

  const status = el("td");
  append(status, badge(css, label));
  if (note) append(status, mark(note));

  const reason = el("td");
  append(reason, said(item["reason"], "en"));

  const value = el("td", { class: "mono" });
  append(value, said(item["value"], "en"));

  const source = el("td", { class: "mono", "aria-label": ariaSource(item) });
  append(
    source,
    said(item["tool"]),
    document.createTextNode("."),
    said(item["field"]),
  );

  const snapshot = el("td", { class: "num" });
  append(snapshot, said(item["snapshot_id"]));

  const asOf = el("td");
  append(asOf, said(item["as_of_jst"]), mark(text(item["as_of"]) ?? ABSENT_MARK));

  const narration = el("td");
  append(narration, el("span", { class: "narr-label" }, NARRATION_LABEL));
  const line = text(item["narration"]);
  append(narration, el("span", { class: "narr", lang: "en" }, line ?? NARRATION_EMPTY));

  append(row, rank, name, status, reason, value, source, snapshot, asOf, narration);
  return row;
}

function cardAnswer(payload: Record<string, Json>, items: Record<string, Json>[]): DocumentFragment {
  const fragment = document.createDocumentFragment();
  if (text(payload["generation"]) === "legacy") {
    append(
      fragment,
      el(
        "p",
        { class: "answer" },
        "Earlier-generation row: its items and blockers are NOT rendered (see the notes).",
      ),
    );
    return fragment;
  }
  const blocking = strings(payload["blocking"]);
  if (blocking.length === 0) {
    append(
      fragment,
      el(
        "p",
        { class: "answer" },
        `Zero rows hold this decision back; ${scalarOrAbsent(payload["n_items"])} roll-call rows ` +
          `were spoken, ${scalarOrAbsent(payload["n_unspoken"])} unspoken.`,
      ),
    );
    return fragment;
  }
  const first = blocking[0] as string;
  const top = items.find((item) => text(item["item_id"]) === first) ?? {};
  const stated = num(payload["n_blocking"]);
  const total = stated !== null && stated >= blocking.length ? stated : blocking.length;
  const hidden = total - blocking.length;
  const answer = el("p", { class: "answer" });
  answer.textContent =
    `Held by ${total} of ${scalarOrAbsent(payload["n_items"])} roll-call rows. HEAVIEST blocker: ` +
    `${first} (veto power ${scalarOrAbsent(top["veto_rank"])}) — ${text(top["reason"]) ?? ABSENT_MARK} ` +
    `Source: ${text(top["tool"]) ?? ABSENT_MARK}.${text(top["field"]) ?? ABSENT_MARK}, ` +
    `as_of ${text(top["as_of_jst"]) ?? ABSENT_MARK}.`;
  const others = blocking.slice(1).join(", ");
  const sub = el("p", { class: "sub" });
  sub.textContent =
    "Other blockers, by veto power: " +
    (hidden > 0
      ? `${others}, and ${hidden} more that are not rendered (\`n_blocking\` is the true count).`
      : blocking.length > 1
        ? `${others}.`
        : "none.");
  append(fragment, answer, sub);
  return fragment;
}

function metaRow(list: HTMLElement, name: string, ...values: (Node | null)[]): void {
  append(list, el("dt", {}, name));
  const dd = el("dd");
  append(dd, ...values);
  append(list, dd);
}

function cardMeta(payload: Record<string, Json>): HTMLElement {
  const list = el("dl", { class: "meta" });
  metaRow(list, "bundle as_of", said(payload["as_of_jst"]), mark(AGE_NOTE), mark(AGE_CLIENT_NOTE));
  metaRow(list, "booked (ts_utc)", said(payload["ts_jst"]), mark(text(payload["ts_utc"]) ?? ABSENT_MARK));
  metaRow(list, "decision_hash", said(payload["decision_hash"]));
  metaRow(list, "computed_hash", said(payload["computed_hash"]));
  metaRow(list, "params_hash", said(payload["params_hash"]));
  metaRow(list, "algo", said(payload["algo"]));
  const verdict = el("span");
  append(verdict, replayBadge(payload));
  metaRow(list, "replay", verdict, mark(text(payload["detail"]) ?? ABSENT_MARK));
  const decisionId = num(payload["decision_id"]);
  const replay =
    decisionId === null
      ? mark("this row has no id that can be replayed")
      : el(
          "a",
          {
            class: "badge",
            href: `/ui/decision/${decisionId}`,
            "aria-label": aria(
              `replay decision number ${decisionId}`,
              "reloads this card and recomputes the verdict from the same frozen snapshots",
            ),
          },
          "replay now ↻",
        );
  metaRow(
    list,
    "replay",
    replay,
    mark(
      "That link asks the server for this card again, and the verdict above is recomputed ON THE " +
        `SERVER from the same ${scalarOrAbsent(payload["n_snapshots"])} frozen snapshots every ` +
        "time — the client computes no verdict of its own. Off screen: " +
        `${text(payload["replay_command"]) ?? ABSENT_MARK}.`,
    ),
  );
  return list;
}

function listBlock(title: string, entries: string[], empty: string): DocumentFragment {
  const fragment = document.createDocumentFragment();
  append(fragment, el("h2", {}, title));
  if (entries.length === 0) {
    append(fragment, el("p", { class: "sub empty" }, empty));
    return fragment;
  }
  const list = el("ul", { class: "notes" });
  for (const entry of entries) append(list, el("li", { lang: "en" }, entry));
  append(fragment, list);
  return fragment;
}

function narrationBlock(payload: Record<string, Json>): DocumentFragment {
  const fragment = document.createDocumentFragment();
  const block = obj(payload["narration"]);
  if (Object.keys(block).length === 0) return fragment;
  append(fragment, el("h2", {}, "Model narration"));
  const line = el("p", { class: "sub" });
  append(line, el("span", { class: "narr-label" }, NARRATION_LABEL));
  line.appendChild(
    document.createTextNode(
      `status ${text(block["status"]) ?? ABSENT_MARK}, n_lines=${scalarOrAbsent(block["n_lines"])} ` +
        `of n_items=${scalarOrAbsent(block["n_items"])}, model ${text(block["model"]) ?? ABSENT_MARK}.`,
    ),
  );
  append(
    fragment,
    line,
    el("p", { class: "sub" }, text(block["reason"]) ?? ABSENT_MARK),
    el("p", { class: "sub" }, NARRATION_NOTE),
  );
  return fragment;
}

function bundleTable(payload: Record<string, Json>): DocumentFragment {
  const fragment = document.createDocumentFragment();
  append(fragment, el("h2", {}, "Frozen evidence"));
  const rows = dicts(payload["replay_bundle"]);
  if (rows.length === 0) {
    append(fragment, el("p", { class: "sub empty" }, EMPTY_SENTENCE));
    return fragment;
  }
  const table = el("table");
  const caption = el("caption", { class: "sub" });
  caption.textContent = `n=${rows.length} snapshots rendered of the ${scalarOrAbsent(payload["n_snapshots"])} this row names.`;
  const thead = el("thead");
  append(thead, headRow(["snapshot", "tool", "status", "as_of"]));
  const tbody = el("tbody");
  for (const entry of rows) {
    const row = el("tr");
    const snapshot = el("td", { class: "num" });
    append(snapshot, said(entry["snapshot_id"]));
    const tool = el("td");
    append(tool, said(entry["tool"]));
    const status = el("td");
    append(status, said(entry["status"]));
    const asOf = el("td");
    append(asOf, said(entry["as_of_jst"]), mark(AGE_CLIENT_NOTE));
    append(row, snapshot, tool, status, asOf);
    append(tbody, row);
  }
  append(table, caption, thead, tbody);
  append(fragment, table);
  return fragment;
}

export function renderCard(payload: Record<string, Json>, target: HTMLElement): void {
  clear(target);
  const items = dicts(payload["items"]);
  const decisionId = num(payload["decision_id"]);

  const links = el("p", { class: "sub" });
  append(links, el("a", { href: "/ui" }, "← back to the watchlist"));
  if (decisionId !== null) {
    append(
      links,
      document.createTextNode(" · "),
      el("a", { href: `/api/decision/${decisionId}` }, "the JSON behind this card, verbatim"),
    );
  }
  append(
    links,
    document.createTextNode(" · "),
    el("a", { href: "#panggilan" }, "skip to the ten point-then-speak rows"),
  );

  const title = el("h1");
  append(title, said(payload["symbol"]), document.createTextNode(" — "), decisionBadge(payload["decision"]));
  append(
    title,
    el(
      "span",
      { class: "sub" },
      ` (decision #${scalarOrAbsent(payload["decision_id"])}, generation ${text(payload["generation"]) ?? ABSENT_MARK})`,
    ),
  );

  append(target, links, title, cardAnswer(payload, items));
  append(target, el("p", { class: "sub", lang: "en" }, text(payload["headline"]) ?? ABSENT_MARK));
  append(target, el("h2", { id: "panggilan" }, "Ten point-then-speak rows"));

  if (items.length === 0) {
    append(
      target,
      el("p", { class: "sub empty" }, `${EMPTY_SENTENCE} — this card holds no roll-call row at all.`),
    );
  } else {
    const table = el("table", { class: "card" });
    const caption = el("caption", { class: "sub" });
    caption.textContent =
      `n=${items.length} rows rendered; this card holds ${scalarOrAbsent(payload["n_items_total"])} ` +
      `rows (\`n_items_total\`), ${scalarOrAbsent(payload["n_unspoken"])} of them unspoken.`;
    const thead = el("thead");
    append(thead, headRow(CARD_COLUMNS));
    const tbody = el("tbody");
    for (const item of items) append(tbody, itemRow(item));
    append(table, caption, thead, tbody);
    append(target, table);
  }

  append(target, el("h2", {}, "Hashes, clocks, and replay"), cardMeta(payload));
  append(target, narrationBlock(payload));
  append(
    target,
    listBlock(
      `Envelope warnings (${scalarOrAbsent(payload["n_warnings_rendered"])} rendered of ` +
        `${scalarOrAbsent(payload["n_warnings_total"])} (\`n_warnings_total\`))`,
      strings(payload["warnings"]),
      "zero warnings (n=0)",
    ),
  );
  append(target, bundleTable(payload), notesList(payload));
}
