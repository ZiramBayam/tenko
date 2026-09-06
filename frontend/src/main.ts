// Klien Tenko: berpindah layar tanpa muat ulang, menyaring, mengurutkan, dan menyegarkan.
//
// Empat hal itu, dan tidak lebih (ADR-020 butir 4). Yang TIDAK dilakukan berkas ini, dan yang
// dijaga tes:
//
// * tidak menghitung keputusan apa pun (ADR-003 utuh) — `decision`, `blocking`, `veto_rank`,
//   `replay_status`, dan setiap cacah datang dari respons API;
// * tidak menyentuh banner. Banner dirender server dan berada DI LUAR `<main>`; klien tidak
//   pernah membacanya, mengubahnya, atau menghapusnya (ADR-020 butir 6);
// * tidak menyimpan apa pun ke `localStorage`, tidak memasang cookie, tidak memegang kredensial;
// * tidak berbicara ke host mana pun selain origin ini sendiri.
//
// Kalau berkas ini gagal dimuat, yang tertinggal adalah layar lengkap hasil render server.

import { getJson, ApiError } from "./api.js";
import { append, clear, el } from "./dom.js";
import type { Json } from "./json.js";
import { dicts, num, text } from "./json.js";
import { compare } from "./order.js";
import { renderCard, renderWatchlist } from "./render.js";
import { LOADING, OFFLINE_KEPT, UNVERIFIED_ENVELOPE } from "./text.js";

type Screen = "watchlist" | "card";

interface Sort {
  readonly id: string;
  readonly label: string;
  /** Kunci urut, DIBACA dari respons — bukan dihitung. `null` = pakai urutan server apa adanya. */
  readonly key: ((entry: Record<string, Json>) => [number, string]) | null;
}

const SORTS: Sort[] = [
  { id: "server", label: "veto power (the order the server sent)", key: null },
  {
    id: "symbol",
    label: "symbol, A to Z",
    key: (entry) => [0, text(entry["symbol"]) ?? ""],
  },
  {
    id: "blockers",
    label: "blockers, most first (n_blocking)",
    key: (entry) => [-(num(entry["n_blocking"]) ?? 0), text(entry["symbol"]) ?? ""],
  },
  {
    id: "unspoken",
    label: "unspoken rows, most first (n_unspoken)",
    key: (entry) => [-(num(entry["n_unspoken"]) ?? 0), text(entry["symbol"]) ?? ""],
  },
];

const state = {
  screen: "watchlist" as Screen,
  decisionId: null as number | null,
  filter: "",
  sort: "server",
  polling: true,
  entries: [] as Record<string, Json>[],
  payload: {} as Record<string, Json>,
  loaded: false,
  timer: 0,
};

let liveNode: HTMLElement;
let viewNode: HTMLElement;
let controlsNode: HTMLElement;
let filterInput: HTMLInputElement;
let pollButton: HTMLButtonElement;
let pollMs = 30000;

function say(message: string, kind: "ok" | "loading" | "error" | "stale"): void {
  liveNode.className = `live state-${kind === "ok" ? "ok" : kind}`;
  liveNode.textContent = message;
}

/** Jam PERAMBAN, dan ia diberi label demikian: ia bukan jam server dan bukan `as_of` bukti. */
function browserClock(): string {
  return new Date().toISOString().replace("T", " ").slice(0, 19) + " UTC (browser clock)";
}

function buildControls(): HTMLElement {
  const bar = el("div", { class: "controls", role: "group", "aria-label": "watchlist controls" });

  const filterField = el("div", { class: "field" });
  const filterId = "tenko-filter";
  append(filterField, el("label", { for: filterId }, "filter by symbol"));
  filterInput = el("input", {
    id: filterId,
    type: "text",
    placeholder: "e.g. SOL",
    autocomplete: "off",
    "aria-describedby": "tenko-live",
  }) as HTMLInputElement;
  filterInput.addEventListener("input", () => {
    state.filter = filterInput.value.trim().toUpperCase();
    paint();
  });
  append(filterField, filterInput);

  const sortField = el("div", { class: "field" });
  const sortId = "tenko-sort";
  append(sortField, el("label", { for: sortId }, "sort rows by"));
  const select = el("select", { id: sortId }) as HTMLSelectElement;
  for (const sort of SORTS) {
    const option = el("option", { value: sort.id }, sort.label) as HTMLOptionElement;
    append(select, option);
  }
  select.value = state.sort;
  select.addEventListener("change", () => {
    state.sort = select.value;
    paint();
  });
  append(sortField, select);

  const pollField = el("div", { class: "field" });
  append(pollField, el("label", { for: "tenko-poll" }, `auto-refresh every ${Math.round(pollMs / 1000)}s`));
  pollButton = el("button", {
    id: "tenko-poll",
    type: "button",
    "aria-pressed": "true",
  }, "auto-refresh: on") as HTMLButtonElement;
  pollButton.addEventListener("click", () => {
    state.polling = !state.polling;
    pollButton.setAttribute("aria-pressed", state.polling ? "true" : "false");
    pollButton.textContent = state.polling ? "auto-refresh: on" : "auto-refresh: off";
    schedule();
    say(
      state.polling
        ? `auto-refresh is on: this screen re-fetches /api/watchlist every ${Math.round(pollMs / 1000)} seconds.`
        : "auto-refresh is off: nothing will change on this screen until you ask it to.",
      "ok",
    );
  });
  append(pollField, pollButton);

  const refreshField = el("div", { class: "field" });
  append(refreshField, el("label", { for: "tenko-refresh" }, "or ask right now"));
  const refresh = el("button", { id: "tenko-refresh", type: "button" }, "refresh now") as HTMLButtonElement;
  refresh.addEventListener("click", () => {
    void load(true);
  });
  append(refreshField, refresh);

  append(bar, filterField, sortField, pollField, refreshField);
  return bar;
}

/** Baris yang lolos saringan + urutannya. Nol angka baru: hanya memilih dan menyusun ulang. */
function visible(): Record<string, Json>[] {
  const rows = state.filter
    ? state.entries.filter((entry) => (text(entry["symbol"]) ?? "").toUpperCase().includes(state.filter))
    : state.entries.slice();
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

function paint(): void {
  if (state.screen !== "watchlist" || !state.loaded) return;
  const rows = visible();
  renderWatchlist(state.payload, rows, viewNode);
  const sort = SORTS.find((entry) => entry.id === state.sort);
  const order = sort ? sort.label : state.sort;
  const filtered = state.filter
    ? `filter "${state.filter}": showing n=${rows.length} of n=${state.entries.length} symbols. `
    : `showing all n=${rows.length} symbols. `;
  say(`${filtered}Sorted by ${order}. Fetched at ${browserClock()}.`, "ok");
}

async function load(spoken: boolean): Promise<void> {
  if (spoken) say(LOADING, "loading");
  try {
    if (state.screen === "card" && state.decisionId !== null) {
      const payload = await getJson(`/api/decision/${state.decisionId}`);
      state.payload = payload;
      state.loaded = true;
      renderCard(payload, viewNode);
      say(
        `card #${state.decisionId} re-fetched from /api/decision/${state.decisionId} at ${browserClock()}; ` +
          "the replay verdict on it was recomputed by the server, not by this client.",
        "ok",
      );
    } else {
      const payload = await getJson("/api/watchlist");
      state.payload = payload;
      state.entries = dicts(payload["symbols"]);
      state.loaded = true;
      paint();
    }
  } catch (error) {
    const message =
      error instanceof ApiError && error.message.includes("`simulated: true`")
        ? UNVERIFIED_ENVELOPE
        : `could not refresh: ${error instanceof Error ? error.message : "unknown failure"}`;
    say(`${message} — ${state.loaded ? OFFLINE_KEPT : "the server-rendered screen below is untouched"}.`, "error");
  }
  schedule();
}

function schedule(): void {
  if (state.timer) window.clearTimeout(state.timer);
  state.timer = 0;
  if (state.polling && state.screen === "watchlist") {
    state.timer = window.setTimeout(() => {
      void load(false);
    }, pollMs);
  }
}

function go(path: string, push: boolean): void {
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
  clear(viewNode);
  append(viewNode, el("p", { class: "sub" }, LOADING));
  void load(true);
}

function isInternal(link: HTMLAnchorElement): boolean {
  if (link.origin !== window.location.origin) return false;
  const path = link.pathname;
  return path === "/" || path === "/ui" || /^\/ui\/decision\/\d+$/.test(path);
}

function start(): void {
  const body = document.body;
  const main = document.getElementById("isi");
  if (!main) return;
  const declared = num(Number(body.dataset["pollMs"]));
  if (declared !== null && declared >= 1000) pollMs = declared;

  viewNode = el("div", { id: "tenko-view" });
  while (main.firstChild) viewNode.appendChild(main.firstChild);

  controlsNode = buildControls();
  liveNode = el("p", { id: "tenko-live", class: "live state-ok", role: "status", "aria-live": "polite" });
  liveNode.textContent =
    "This screen was rendered by the server. The client is attached now: it will re-fetch the same " +
    "JSON API, and it says here every time it does.";
  main.appendChild(controlsNode);
  main.appendChild(liveNode);
  main.appendChild(viewNode);

  const screen = body.dataset["screen"];
  state.screen = screen === "card" ? "card" : "watchlist";
  state.decisionId = screen === "card" ? Number(body.dataset["decisionId"] ?? "0") || null : null;
  controlsNode.hidden = state.screen !== "watchlist" || screen === "error";
  if (screen === "error") return;

  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const target = (event.target as Element | null)?.closest("a");
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

  void load(false);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start);
} else {
  start();
}
