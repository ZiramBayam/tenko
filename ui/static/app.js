"use strict";
(() => {
  // frontend/src/json.ts
  function obj(value) {
    return typeof value === "object" && value !== null && !Array.isArray(value) ? value : {};
  }
  function arr(value) {
    return Array.isArray(value) ? value : [];
  }
  function dicts(value) {
    return arr(value).filter((entry) => typeof entry === "object" && entry !== null && !Array.isArray(entry)).map((entry) => entry);
  }
  function strings(value) {
    return arr(value).filter((entry) => typeof entry === "string");
  }
  function text(value) {
    return typeof value === "string" && value.trim() !== "" ? value : null;
  }
  function num(value) {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  }
  function scalar(value) {
    if (typeof value === "boolean") return value ? "true" : "false";
    if (typeof value === "number") return String(value);
    if (typeof value === "string") return value;
    if (value === null || value === void 0) return "null";
    if (Array.isArray(value)) return "[" + value.map(scalar).join(", ") + "]";
    const entries = Object.entries(obj(value)).sort((a, b) => a[0] < b[0] ? -1 : 1);
    return entries.map(([key, item]) => `${key}=${scalar(item)}`).join("; ");
  }

  // frontend/src/api.ts
  var ApiError = class extends Error {
    constructor(message, status) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  };
  async function getJson(path) {
    let response;
    try {
      response = await fetch(path, {
        headers: { accept: "application/json" },
        credentials: "omit",
        cache: "no-store"
      });
    } catch {
      throw new ApiError(`the API at ${path} could not be reached`, 0);
    }
    let body;
    try {
      body = await response.json();
    } catch {
      throw new ApiError(`${path} answered ${response.status} with a body that is not JSON`, response.status);
    }
    const payload = obj(body);
    if (payload["simulated"] !== true) {
      throw new ApiError(`${path} answered without \`simulated: true\``, response.status);
    }
    if (!response.ok) {
      const detail = typeof payload["error"] === "string" ? payload["error"] : `HTTP ${response.status}`;
      throw new ApiError(detail, response.status);
    }
    return payload;
  }

  // frontend/src/dom.ts
  function el(tag, attrs = {}, text2) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (value === null || value === void 0) continue;
      node.setAttribute(name, String(value));
    }
    if (text2 !== null && text2 !== void 0) node.textContent = text2;
    return node;
  }
  function bdi(text2, lang) {
    const node = el("bdi", lang ? { lang } : {});
    node.textContent = text2;
    return node;
  }
  function append(parent, ...children) {
    for (const child of children) if (child) parent.appendChild(child);
  }
  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  // frontend/src/order.ts
  function compare(a, b) {
    for (let index = 0; index < 3; index += 1) {
      const left = a[index];
      const right = b[index];
      if (left !== right) return left < right ? -1 : 1;
    }
    return a[3] < b[3] ? -1 : a[3] > b[3] ? 1 : 0;
  }
  function closestToGo(entries) {
    let best = null;
    let bestKey = null;
    for (const entry of entries) {
      const blocking = num(entry["n_blocking"]) ?? 0;
      const key = [blocking ? 1 : 0, -(num(entry["veto_rank"]) ?? 0), blocking, text(entry["symbol"]) ?? ""];
      if (bestKey === null || compare(key, bestKey) < 0) {
        best = entry;
        bestKey = key;
      }
    }
    return best;
  }
  function heldCount(entries) {
    return entries.filter((entry) => (num(entry["n_blocking"]) ?? 0) > 0).length;
  }

  // frontend/src/text.ts
  var EMPTY_SENTENCE = "no data yet (n=0)";
  var NEUTRAL_MARK = "neutral — not against it, and not a confirmation";
  var UNSPOKEN_MARK = "unspoken — the evidence is not enough, and that is not zero";
  var ABSENT_MARK = "not stated";
  var ARIA_NULL = "the value is not in the evidence — unspoken, and that is not zero";
  var ARIA_NO_SOURCE = "source not stated";
  var NARRATION_LABEL = "model narration — NOT verified against the evidence";
  var NARRATION_EMPTY = "no narration for this row";
  var VETO_RANK_COLUMN = "veto power (spec §4, not validated)";
  var AGE_NOTE = "This age is PRESENTATION: it is computed from the bundle `as_of` (guarded by `payload_hash`), not from the ledger row `ts_utc` (guarded by no hash at all), and it never enters `decision_hash`.";
  var NARRATION_NOTE = "The sentences in this column were written by a language model. Only their `item_id` is validated; their CONTENT is never checked against the evidence, and no code stops the model from writing a verdict. What can be replayed is the `reason` column beside it, not this column.";
  var AGE_CLIENT_NOTE = "the relative age line is written by the server render only; this row was refreshed by the client, so reload the page for a fresh age";
  var UNVERIFIED_ENVELOPE = "this API response did not say `simulated: true`, so nothing from it was rendered; the screen you can see is the one the server rendered";
  var LOADING = "loading from the API…";
  var OFFLINE_KEPT = "the last successful response is still on screen and it is OLD; nothing was invented to replace it";
  var STALE_SCREEN = "the screen below is the PREVIOUS one, not this URL; it was kept on purpose because a blank screen answers nothing, and nothing was invented to replace it";
  var NEVER_FETCHED = "no fetch by this client has succeeded yet, so what is below is still the screen the server rendered";
  var NOT_REFRESHED_SINCE = "and nothing on screen has been refreshed since";
  var NO_CARD_ID = "this card page did not name a decision id, so the client asked for nothing and rendered nothing; what is below is the screen the server rendered";
  var CLIENT_ATTACHED = "This screen was rendered by the server. The client is attached now: it will re-fetch the same JSON API, and it says here every time it does.";
  var CLIENT_INERT = "This screen was rendered by the server. The client is attached but does nothing here: on a failure page it fetches nothing, refreshes nothing, and changes nothing on screen.";
  var TITLE_WATCHLIST = "Tenko — watchlist (simulated)";
  var TITLE_CARD = "Tenko — card #{id} (simulated)";

  // frontend/src/render.ts
  var CARD_COLUMNS = [
    VETO_RANK_COLUMN,
    "item",
    "status",
    "reason (deterministic, replayable)",
    "value spoken",
    "source: tool.field",
    "snapshot",
    "evidence as_of",
    NARRATION_LABEL
  ];
  var WATCHLIST_COLUMNS = [
    "symbol",
    "decision",
    `${VETO_RANK_COLUMN} — blocker`,
    "what holds it (and where that came from)",
    "blockers / rows",
    "unspoken",
    "evidence",
    "replay",
    "bundle as_of"
  ];
  function said(value, lang) {
    if (value === null || value === void 0) return el("span", { class: "empty" }, ABSENT_MARK);
    return bdi(scalar(value), lang);
  }
  function mark(note) {
    return el("span", { class: "mark" }, note);
  }
  function cut(note) {
    const line = text(note);
    return line === null ? null : mark(line);
  }
  function badge(cls, label) {
    return el("span", { class: `badge ${cls}` }, label);
  }
  function decisionBadge(value) {
    const label = text(value) ?? "?";
    const css = label === "GO" ? "d-GO" : label === "NO_GO" ? "d-NO_GO" : "d-unknown";
    return badge(css, label);
  }
  function replayBadge(row) {
    const tone = text(row["replay_tone"]);
    const css = tone && /^[A-Za-z_][A-Za-z0-9_]*$/.test(tone) ? tone : "unknown";
    const fragment = document.createDocumentFragment();
    append(
      fragment,
      badge(`tone-${css}`, text(row["replay_status"]) ?? "NOT RUN"),
      mark(text(row["replay_label"]) ?? "not assessed")
    );
    return fragment;
  }
  function statusWords(item) {
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
  function ariaStatus(item) {
    const { label, note } = statusWords(item);
    return note ? `status ${label}, ${note}` : `status ${label}`;
  }
  function ariaValue(item) {
    const { note } = statusWords(item);
    const value = item["value"];
    if (note === UNSPOKEN_MARK) {
      const head = `value: ${UNSPOKEN_MARK}`;
      if (value === null || value === void 0) return head;
      return `${head}; what is readable in the evidence: ${scalar(value)}`;
    }
    if (value === null || value === void 0) return `value: ${ARIA_NULL}`;
    return `value spoken: ${scalar(value)}`;
  }
  function ariaSource(item) {
    const tool = text(item["tool"]);
    const field = text(item["field"]);
    if (tool === null && field === null) return ARIA_NO_SOURCE;
    const parts = [`source ${tool ?? "tool not stated"}.${field ?? "field not stated"}`];
    if ("snapshot_id" in item) {
      parts.push(phrase("snapshot", item["snapshot_id"], "snapshot not stated"));
    }
    const asOf = text(item["as_of_jst"]);
    parts.push(asOf === null ? "as_of not stated" : `as_of ${asOf}`);
    return parts.join(", ");
  }
  function aria(...parts) {
    return parts.filter((part) => !!part && part.trim() !== "").join("; ");
  }
  function phrase(label, value, absent) {
    const spoken = value === null || value === void 0 ? null : scalar(value);
    return spoken === null || spoken.trim() === "" ? absent : `${label} ${spoken}`;
  }
  function headRow(columns) {
    const row = el("tr");
    for (const name of columns) append(row, el("th", { scope: "col" }, name));
    return row;
  }
  function notesList(payload) {
    const fragment = document.createDocumentFragment();
    const notes = strings(payload["notes"]);
    if (notes.length === 0) return fragment;
    const list = el("ul", { class: "notes" });
    for (const note of notes) append(list, el("li", {}, note));
    append(fragment, el("h2", {}, "Screen notes"), list);
    return fragment;
  }
  function watchlistRow(entry) {
    const blocker = obj(entry["top_blocker"]);
    const hasBlocker = Object.keys(blocker).length > 0;
    const decisionId = num(entry["decision_id"]);
    const symbol = text(entry["symbol"]);
    const unspoken = num(entry["n_unspoken"]);
    const label = aria(
      phrase("symbol", entry["symbol"], "symbol not stated"),
      phrase("decision", entry["decision"], "decision not stated"),
      phrase("held by", entry["n_blocking"], "blocker count not stated") + phrase(" of", entry["n_items"], " of an unstated number of") + " roll-call rows",
      hasBlocker ? [
        phrase("heaviest blocker", blocker["item_id"], "heaviest blocker has no id"),
        phrase("veto power", blocker["veto_rank"], "veto power not stated"),
        ariaStatus(blocker),
        ariaSource(blocker)
      ].join("; ") : "zero rows hold this decision back",
      unspoken === null ? "the count of unspoken rows is not stated" : `${unspoken} rows unspoken`,
      phrase("bundle as_of", entry["as_of_jst"], "bundle as_of is unreadable")
    );
    const row = el("tr", { "aria-label": label });
    const head = el("th", { scope: "row" });
    if (decisionId !== null) {
      const link = el("a", {
        href: `/ui/decision/${decisionId}`,
        "aria-label": aria(
          phrase("open the roll-call card for", entry["symbol"], "open this symbol's roll-call card"),
          phrase("decision", entry["decision"], "decision not stated"),
          phrase("card number", entry["decision_id"], "no card number")
        )
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
      source.textContent = `source ${text(blocker["tool"]) ?? ABSENT_MARK}.${text(blocker["field"]) ?? ABSENT_MARK} · as_of ${text(blocker["as_of_jst"]) ?? ABSENT_MARK}`;
      append(
        held,
        name,
        document.createTextNode(
          ` (veto power ${scalarOrAbsent(blocker["veto_rank"])}, status ${scalarOrAbsent(blocker["status"])})`
        ),
        reason,
        source
      );
    } else {
      append(held, mark("zero rows hold this decision back"));
    }
    const counts = el("td", { class: "num" });
    append(
      counts,
      said(entry["n_blocking"]),
      document.createTextNode(" / "),
      said(entry["n_items"])
    );
    const evidence = el("td", { class: "num" });
    append(
      evidence,
      said(entry["n_snapshots"]),
      document.createTextNode(" snapshots"),
      el("br"),
      said(entry["n_warnings"]),
      document.createTextNode(" warnings")
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
  function scalarOrAbsent(value) {
    return value === null || value === void 0 ? ABSENT_MARK : scalar(value);
  }
  function watchlistLede(entries, total) {
    const fragment = document.createDocumentFragment();
    const nearest = closestToGo(entries);
    if (nearest === null) {
      append(fragment, el("p", { class: "answer empty" }, EMPTY_SENTENCE));
      return fragment;
    }
    const held = heldCount(entries);
    const answer = el("p", { class: "answer" });
    const shown2 = total !== null && total !== entries.length ? `n=${entries.length} of ` : "n=";
    answer.textContent = `${shown2}${total ?? entries.length} symbols read (one row = that symbol's LAST decision); ${held} held among the rows shown; `;
    if ((num(nearest["n_blocking"]) ?? -1) > 0) {
      const blocker = obj(nearest["top_blocker"]);
      answer.textContent += `nothing is GO; the closest is ${text(nearest["symbol"]) ?? ABSENT_MARK}, held by ${scalarOrAbsent(nearest["n_blocking"])} rows, heaviest ${text(blocker["item_id"]) ?? ABSENT_MARK} (veto power ${scalarOrAbsent(blocker["veto_rank"])}).`;
    } else {
      answer.textContent += `closest to GO: ${text(nearest["symbol"]) ?? ABSENT_MARK} — zero rows hold it.`;
    }
    const sub = el("p", { class: "sub" });
    sub.textContent = `Ordered by veto power: the heaviest blocker (spec §4 rank 1) on top. "Closest to GO" is read backwards from that order: the lightest heaviest-blocker first, then the fewest blockers — not a score, not a forecast. The rank itself is not validated: it is a spec §4 design constant, and this repo's own instrument contradicts it — see Screen notes below.`;
    append(fragment, answer, sub);
    return fragment;
  }
  function renderWatchlist(payload, entries, target) {
    clear(target);
    append(target, el("h1", {}, "Tenko 点呼 — pre-departure roll call (simulated)"));
    const links = el("p", { class: "sub" });
    append(links, el("a", { href: "/api/watchlist" }, "the JSON behind this screen, verbatim"));
    if (entries.length > 0) {
      append(
        links,
        document.createTextNode(" · "),
        el("a", { href: "#watchlist" }, "skip to the watchlist table")
      );
    }
    append(target, links, watchlistLede(entries, num(payload["n"])));
    if (entries.length > 0) {
      const table = el("table", { id: "watchlist" });
      const caption = el("caption", { class: "sub" });
      caption.textContent = `n=${entries.length} symbols; read from the ${scalarOrAbsent(payload["scan_limit"])} MOST RECENT decision rows, not the whole table. ${scalarOrAbsent(payload["n_legacy_hidden"])} earlier-generation rows are not shown here.`;
      const thead = el("thead");
      append(thead, headRow(WATCHLIST_COLUMNS));
      const tbody = el("tbody");
      for (const entry of entries) append(tbody, watchlistRow(entry));
      append(table, caption, thead, tbody);
      append(target, table);
    }
    append(target, notesList(payload));
  }
  function itemRow(item) {
    const { label, css, note } = statusWords(item);
    const row = el("tr", {
      "aria-label": aria(
        phrase("row", item["item_id"], "row without an item_id"),
        phrase("veto power", item["veto_rank"], "veto power not stated"),
        ariaStatus(item),
        ariaValue(item),
        ariaSource(item)
      )
    });
    const rank = el("td", { class: "num" });
    append(rank, said(item["veto_rank"]));
    const name = el("th", { scope: "row" });
    append(name, said(item["item_id"]));
    const status = el("td");
    append(status, badge(css, label));
    if (note) append(status, mark(note));
    const reason = el("td");
    append(reason, said(item["reason"], "en"), cut(item["reason_cut_note"]));
    const value = el("td", { class: "mono" });
    append(value, said(item["value"], "en"), cut(item["value_cut_note"]));
    const source = el("td", { class: "mono", "aria-label": ariaSource(item) });
    append(
      source,
      said(item["tool"]),
      document.createTextNode("."),
      said(item["field"])
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
  function cardAnswer(payload, items) {
    const fragment = document.createDocumentFragment();
    if (text(payload["generation"]) === "legacy") {
      append(
        fragment,
        el(
          "p",
          { class: "answer" },
          "Earlier-generation row: its items and blockers are NOT rendered (see the notes)."
        )
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
          `Zero rows hold this decision back; ${scalarOrAbsent(payload["n_items"])} roll-call rows were spoken, ${scalarOrAbsent(payload["n_unspoken"])} unspoken.`
        )
      );
      return fragment;
    }
    const first = blocking[0];
    const top = items.find((item) => text(item["item_id"]) === first) ?? {};
    const stated = num(payload["n_blocking"]);
    const total = stated !== null && stated >= blocking.length ? stated : blocking.length;
    const hidden = total - blocking.length;
    const answer = el("p", { class: "answer" });
    answer.textContent = `Held by ${total} of ${scalarOrAbsent(payload["n_items"])} roll-call rows. HEAVIEST blocker: ${first} (veto power ${scalarOrAbsent(top["veto_rank"])}) — ${text(top["reason"]) ?? ABSENT_MARK} Source: ${text(top["tool"]) ?? ABSENT_MARK}.${text(top["field"]) ?? ABSENT_MARK}, as_of ${text(top["as_of_jst"]) ?? ABSENT_MARK}.`;
    const others = blocking.slice(1).join(", ");
    const sub = el("p", { class: "sub" });
    sub.textContent = "Other blockers, by veto power: " + (hidden > 0 ? `${others}, and ${hidden} more that are not rendered (\`n_blocking\` is the true count).` : blocking.length > 1 ? `${others}.` : "none.");
    append(fragment, answer, sub);
    return fragment;
  }
  function metaRow(list, name, ...values) {
    append(list, el("dt", {}, name));
    const dd = el("dd");
    append(dd, ...values);
    append(list, dd);
  }
  function cardMeta(payload) {
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
    const replay = decisionId === null ? mark("this row has no id that can be replayed") : el(
      "a",
      {
        class: "badge",
        href: `/ui/decision/${decisionId}`,
        "aria-label": aria(
          `replay decision number ${decisionId}`,
          "reloads this card and recomputes the verdict from the same frozen snapshots"
        )
      },
      "replay now ↻"
    );
    metaRow(
      list,
      "replay",
      replay,
      mark(
        `That link asks the server for this card again, and the verdict above is recomputed ON THE SERVER from the same ${scalarOrAbsent(payload["n_snapshots"])} frozen snapshots every time — the client computes no verdict of its own. Off screen: ${text(payload["replay_command"]) ?? ABSENT_MARK}.`
      )
    );
    return list;
  }
  function listBlock(title, entries, empty) {
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
  function narrationBlock(payload) {
    const fragment = document.createDocumentFragment();
    const block = obj(payload["narration"]);
    if (Object.keys(block).length === 0) return fragment;
    append(fragment, el("h2", {}, "Model narration"));
    const line = el("p", { class: "sub" });
    append(line, el("span", { class: "narr-label" }, NARRATION_LABEL));
    line.appendChild(
      document.createTextNode(
        `status ${text(block["status"]) ?? ABSENT_MARK}, n_lines=${scalarOrAbsent(block["n_lines"])} of n_items=${scalarOrAbsent(block["n_items"])}, model ${text(block["model"]) ?? ABSENT_MARK}.`
      )
    );
    append(
      fragment,
      line,
      el("p", { class: "sub" }, text(block["reason"]) ?? ABSENT_MARK),
      el("p", { class: "sub" }, NARRATION_NOTE)
    );
    return fragment;
  }
  function bundleTable(payload) {
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
  function renderCard(payload, target) {
    clear(target);
    const items = dicts(payload["items"]);
    const decisionId = num(payload["decision_id"]);
    const links = el("p", { class: "sub" });
    append(links, el("a", { href: "/ui" }, "← back to the watchlist"));
    if (decisionId !== null) {
      append(
        links,
        document.createTextNode(" · "),
        el("a", { href: `/api/decision/${decisionId}` }, "the JSON behind this card, verbatim")
      );
    }
    append(
      links,
      document.createTextNode(" · "),
      el("a", { href: "#panggilan" }, "skip to the ten point-then-speak rows")
    );
    const title = el("h1");
    append(title, said(payload["symbol"]), document.createTextNode(" — "), decisionBadge(payload["decision"]));
    append(
      title,
      el(
        "span",
        { class: "sub" },
        ` (decision #${scalarOrAbsent(payload["decision_id"])}, generation ${text(payload["generation"]) ?? ABSENT_MARK})`
      )
    );
    append(target, links, title, cardAnswer(payload, items));
    append(target, el("p", { class: "sub", lang: "en" }, text(payload["headline"]) ?? ABSENT_MARK));
    append(target, el("h2", { id: "panggilan" }, "Ten point-then-speak rows"));
    if (items.length === 0) {
      append(
        target,
        el("p", { class: "sub empty" }, `${EMPTY_SENTENCE} — this card holds no roll-call row at all.`)
      );
    } else {
      const table = el("table", { class: "card" });
      const caption = el("caption", { class: "sub" });
      caption.textContent = `n=${items.length} rows rendered; this card holds ${scalarOrAbsent(payload["n_items_total"])} rows (\`n_items_total\`), ${scalarOrAbsent(payload["n_unspoken"])} of them unspoken.`;
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
        `Envelope warnings (${scalarOrAbsent(payload["n_warnings_rendered"])} rendered of ${scalarOrAbsent(payload["n_warnings_total"])} (\`n_warnings_total\`))`,
        strings(payload["warnings"]),
        "zero warnings (n=0)"
      )
    );
    append(target, bundleTable(payload), notesList(payload));
  }

  // frontend/src/main.ts
  var SORTS = [
    { id: "server", label: "veto power (the order the server sent)", key: null },
    {
      id: "symbol",
      label: "symbol, A to Z",
      key: (entry) => [0, text(entry["symbol"]) ?? ""]
    },
    {
      id: "blockers",
      label: "blockers, most first (n_blocking)",
      key: (entry) => [-(num(entry["n_blocking"]) ?? 0), text(entry["symbol"]) ?? ""]
    },
    {
      id: "unspoken",
      label: "unspoken rows, most first (n_unspoken)",
      key: (entry) => [-(num(entry["n_unspoken"]) ?? 0), text(entry["symbol"]) ?? ""]
    }
  ];
  var state = {
    screen: "watchlist",
    decisionId: null,
    filter: "",
    sort: "server",
    polling: true,
    entries: [],
    payload: {},
    loaded: false,
    timer: 0,
    /** Layar yang BENAR-BENAR ada di DOM saat ini — dipakai untuk mengaku layar mana yang terlihat. */
    shownScreen: "watchlist",
    shownId: null,
    /** Sudah pernahkah klien mengganti isi render server? Selama belum, yang di layar milik server. */
    rendered: false,
    /**
     * Jam peramban pada pengambilan TERAKHIR YANG SUKSES — bukan jam melukis.
     *
     * `paint()` berjalan pada tiap ketikan saringan dan tiap perubahan urutan tanpa mengambil
     * apa pun; versi sebelumnya memanggil `browserClock()` di sana dan mencetak "Fetched at
     * <sekarang>" di atas data dari pengambilan sebelumnya. Satu ketikan karena itu menghapus
     * pengakuan "layar ini basi" dan menggantinya dengan jam yang salah — persis kelas kesalahan
     * yang produk ini ada untuk melawan. Jamnya sekarang disimpan sekali, saat responsnya tiba.
     */
    fetchedAt: null,
    /** Kalimat kegagalan yang MASIH berlaku; `null` hanya bila pengambilan terakhir sukses. */
    failure: null
  };
  var liveNode;
  var viewNode;
  var controlsNode;
  var filterInput;
  var pollButton;
  var pollMs = 3e4;
  function say(message, kind) {
    liveNode.className = `live state-${kind === "ok" ? "ok" : kind}`;
    liveNode.textContent = message;
  }
  function browserClock() {
    return (/* @__PURE__ */ new Date()).toISOString().replace("T", " ").slice(0, 19) + " UTC (browser clock)";
  }
  function buildControls() {
    const bar = el("div", { class: "controls", role: "group", "aria-label": "watchlist controls" });
    const filterField = el("div", { class: "field" });
    const filterId = "tenko-filter";
    append(filterField, el("label", { for: filterId }, "filter by symbol"));
    filterInput = el("input", {
      id: filterId,
      type: "text",
      placeholder: "e.g. SOL",
      autocomplete: "off",
      "aria-describedby": "tenko-live"
    });
    filterInput.addEventListener("input", () => {
      state.filter = filterInput.value.trim().toUpperCase();
      paint();
    });
    append(filterField, filterInput);
    const sortField = el("div", { class: "field" });
    const sortId = "tenko-sort";
    append(sortField, el("label", { for: sortId }, "sort rows by"));
    const select = el("select", { id: sortId });
    for (const sort of SORTS) {
      const option = el("option", { value: sort.id }, sort.label);
      append(select, option);
    }
    select.value = state.sort;
    select.addEventListener("change", () => {
      state.sort = select.value;
      paint();
    });
    append(sortField, select);
    const pollField = el("div", { class: "field" });
    append(pollField, el("label", { for: "tenko-poll" }, `auto-refresh every ${Math.round(pollMs / 1e3)}s`));
    pollButton = el("button", {
      id: "tenko-poll",
      type: "button",
      "aria-pressed": "true"
    }, "auto-refresh: on");
    pollButton.addEventListener("click", () => {
      state.polling = !state.polling;
      pollButton.setAttribute("aria-pressed", state.polling ? "true" : "false");
      pollButton.textContent = state.polling ? "auto-refresh: on" : "auto-refresh: off";
      schedule();
      say(
        state.polling ? `auto-refresh is on: this screen re-fetches /api/watchlist every ${Math.round(pollMs / 1e3)} seconds.` : "auto-refresh is off: nothing will change on this screen until you ask it to.",
        "ok"
      );
    });
    append(pollField, pollButton);
    const refreshField = el("div", { class: "field" });
    append(refreshField, el("label", { for: "tenko-refresh" }, "or ask right now"));
    const refresh = el("button", { id: "tenko-refresh", type: "button" }, "refresh now");
    refresh.addEventListener("click", () => {
      void load(true);
    });
    append(refreshField, refresh);
    append(bar, filterField, sortField, pollField, refreshField);
    return bar;
  }
  function visible() {
    const rows = state.filter ? state.entries.filter((entry) => (text(entry["symbol"]) ?? "").toUpperCase().includes(state.filter)) : state.entries.slice();
    const sort = SORTS.find((entry) => entry.id === state.sort);
    if (sort && sort.key) {
      const key = sort.key;
      rows.sort((left, right) => {
        const a = key(left);
        const b = key(right);
        return compare([a[0], 0, 0, a[1]], [b[0], 0, 0, b[1]]);
      });
    }
    return rows;
  }
  function age() {
    if (state.failure !== null) return state.failure;
    if (state.fetchedAt === null) return `${NEVER_FETCHED}.`;
    return `Fetched at ${state.fetchedAt}.`;
  }
  function paint() {
    if (state.screen !== "watchlist" || !state.loaded) return false;
    const rows = visible();
    renderWatchlist(state.payload, rows, viewNode);
    const sort = SORTS.find((entry) => entry.id === state.sort);
    const order = sort ? sort.label : state.sort;
    const filtered = state.filter ? `filter "${state.filter}": showing n=${rows.length} of n=${state.entries.length} symbols. ` : `showing all n=${rows.length} symbols. `;
    say(`${filtered}Sorted by ${order}. ${age()}`, state.failure === null ? "ok" : "error");
    return true;
  }
  function shown() {
    state.shownScreen = state.screen;
    state.shownId = state.decisionId;
    state.rendered = true;
    document.title = state.screen === "card" && state.decisionId !== null ? TITLE_CARD.replace("{id}", String(state.decisionId)) : TITLE_WATCHLIST;
  }
  function kept() {
    if (state.shownScreen !== state.screen || state.shownId !== state.decisionId) return STALE_SCREEN;
    return state.rendered ? OFFLINE_KEPT : "the server-rendered screen below is untouched";
  }
  async function load(spoken) {
    if (spoken) say(LOADING, "loading");
    try {
      if (state.screen === "card" && state.decisionId !== null) {
        const payload = await getJson(`/api/decision/${state.decisionId}`);
        state.payload = payload;
        state.loaded = true;
        state.fetchedAt = browserClock();
        state.failure = null;
        renderCard(payload, viewNode);
        shown();
        say(
          `card #${state.decisionId} re-fetched from /api/decision/${state.decisionId} at ${state.fetchedAt}; the replay verdict on it was recomputed by the server, not by this client.`,
          "ok"
        );
      } else {
        const payload = await getJson("/api/watchlist");
        state.payload = payload;
        state.entries = dicts(payload["symbols"]);
        state.loaded = true;
        state.fetchedAt = browserClock();
        state.failure = null;
        if (paint()) shown();
      }
    } catch (error) {
      const message = error instanceof ApiError && error.message.includes("`simulated: true`") ? UNVERIFIED_ENVELOPE : `could not refresh: ${error instanceof Error ? error.message : "unknown failure"}`;
      state.failure = `${message} — ${kept()}` + (state.fetchedAt === null ? "." : `; it was fetched at ${state.fetchedAt}, ${NOT_REFRESHED_SINCE}.`);
      say(state.failure, "error");
    }
    schedule();
  }
  function schedule() {
    if (state.timer) window.clearTimeout(state.timer);
    state.timer = 0;
    if (state.polling && state.screen === "watchlist") {
      state.timer = window.setTimeout(() => {
        void load(false);
      }, pollMs);
    }
  }
  function go(path, push) {
    const card = /^\/ui\/decision\/(\d+)$/.exec(path);
    if (card) {
      state.screen = "card";
      state.decisionId = Number(card[1]);
    } else {
      state.screen = "watchlist";
      state.decisionId = null;
    }
    state.loaded = false;
    if (push) window.history.pushState({ path }, "", path);
    controlsNode.hidden = state.screen !== "watchlist";
    void load(true);
  }
  function isInternal(link) {
    if (link.origin !== window.location.origin) return false;
    const path = link.pathname;
    return path === "/" || path === "/ui" || /^\/ui\/decision\/\d+$/.test(path);
  }
  function railbar() {
    const bar = document.querySelector(".railbar");
    if (!(bar instanceof HTMLElement)) return;
    let queued = false;
    const paint2 = () => {
      queued = false;
      const doc = document.documentElement;
      const span = doc.scrollHeight - doc.clientHeight;
      const p = span > 0 ? Math.min(1, Math.max(0, doc.scrollTop / span)) : 1;
      bar.style.setProperty("--p", p.toFixed(4));
    };
    const onScroll = () => {
      if (queued) return;
      queued = true;
      window.requestAnimationFrame(paint2);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    paint2();
  }
  function start() {
    const body = document.body;
    const main = document.getElementById("isi");
    if (!main) return;
    const declared = num(Number(body.dataset["pollMs"]));
    if (declared !== null && declared >= 1e3) pollMs = declared;
    viewNode = el("div", { id: "tenko-view" });
    while (main.firstChild) viewNode.appendChild(main.firstChild);
    const screen = body.dataset["screen"];
    state.screen = screen === "card" ? "card" : "watchlist";
    state.decisionId = screen === "card" ? Number(body.dataset["decisionId"] ?? "0") || null : null;
    controlsNode = buildControls();
    liveNode = el("p", { id: "tenko-live", class: "live state-ok", role: "status", "aria-live": "polite" });
    if (screen === "landing") railbar();
    const inert = screen === "error" || screen === "landing";
    liveNode.textContent = inert ? CLIENT_INERT : CLIENT_ATTACHED;
    main.appendChild(controlsNode);
    main.appendChild(liveNode);
    main.appendChild(viewNode);
    state.shownScreen = state.screen;
    state.shownId = state.decisionId;
    controlsNode.hidden = state.screen !== "watchlist" || inert;
    if (inert) return;
    document.addEventListener("click", (event) => {
      if (event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const target = event.target?.closest("a");
      if (!target || !(target instanceof HTMLAnchorElement) || !isInternal(target)) return;
      event.preventDefault();
      go(target.pathname, true);
    });
    window.addEventListener("popstate", () => {
      go(window.location.pathname, false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || state.filter === "") return;
      state.filter = "";
      filterInput.value = "";
      paint();
      filterInput.focus();
    });
    if (state.screen === "card" && state.decisionId === null) {
      say(`${NO_CARD_ID}.`, "stale");
      return;
    }
    void load(false);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
