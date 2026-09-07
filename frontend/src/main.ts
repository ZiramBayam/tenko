// Klien Tenko: berpindah layar tanpa muat ulang, menyaring, mengurutkan, dan menyegarkan.
//
// Empat hal itu, dan tidak lebih (ADR-020 butir 4) — dan tidak keempatnya di setiap layar:
// menyaring, mengurutkan, dan penyegaran 30 detik hanya ada di Layar A (`schedule()` memasang
// timer hanya di sana dan bar kontrolnya disembunyikan di luar sana); Layar B mengambil kartunya
// SEKALI saat tiba; halaman gagal tidak mengambil apa pun. `ui.screens.client_note(screen)`
// mengatakan yang mana untuk layar yang sedang dirender, dan tes menjaga ketiganya.
//
// Yang TIDAK dilakukan berkas ini, dan yang dijaga tes:
//
// * tidak menghitung keputusan apa pun (ADR-003 utuh) — `decision`, `blocking`, `veto_rank`,
//   `replay_status`, dan setiap cacah datang dari respons API;
// * tidak menyentuh banner. Banner dirender server dan berada DI LUAR `<main>`; klien tidak
//   pernah membacanya, mengubahnya, atau menghapusnya (ADR-020 butir 6);
// * tidak menyimpan apa pun ke `localStorage`, tidak memasang cookie, tidak memegang kredensial;
// * tidak berbicara ke host mana pun selain origin ini sendiri.
//
// Kalau berkas ini gagal dimuat, yang tertinggal adalah layar lengkap hasil render server.

import gsap from "gsap";
import ScrollTrigger from "gsap/ScrollTrigger";
import Lenis from "lenis";
import { getJson, ApiError } from "./api.js";
import { append, el } from "./dom.js";
import type { Json } from "./json.js";
import { dicts, num, text } from "./json.js";
import { compare } from "./order.js";
import { renderCard, renderWatchlist } from "./render.js";
import {
  CLIENT_ATTACHED,
  CLIENT_INERT,
  LOADING,
  NEVER_FETCHED,
  NOT_REFRESHED_SINCE,
  NO_CARD_ID,
  OFFLINE_KEPT,
  STALE_SCREEN,
  TITLE_CARD,
  TITLE_WATCHLIST,
  UNVERIFIED_ENVELOPE,
} from "./text.js";

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
  /** Layar yang BENAR-BENAR ada di DOM saat ini — dipakai untuk mengaku layar mana yang terlihat. */
  shownScreen: "watchlist" as Screen,
  shownId: null as number | null,
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
  fetchedAt: null as string | null,
  /** Kalimat kegagalan yang MASIH berlaku; `null` hanya bila pengambilan terakhir sukses. */
  failure: null as string | null,
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

/** Umur isi yang ADA di layar. Ia tidak pernah dihitung ulang saat melukis. */
function age(): string {
  if (state.failure !== null) return state.failure;
  if (state.fetchedAt === null) return `${NEVER_FETCHED}.`;
  return `Fetched at ${state.fetchedAt}.`;
}

/** Melukis ulang Layar A. `false` = tidak ada yang dilukis, jadi tidak ada yang boleh diakui. */
function paint(): boolean {
  if (state.screen !== "watchlist" || !state.loaded) return false;
  const rows = visible();
  renderWatchlist(state.payload, rows, viewNode);
  const sort = SORTS.find((entry) => entry.id === state.sort);
  const order = sort ? sort.label : state.sort;
  const filtered = state.filter
    ? `filter "${state.filter}": showing n=${rows.length} of n=${state.entries.length} symbols. `
    : `showing all n=${rows.length} symbols. `;
  // Menyaring dan mengurutkan MEMILIH baris; keduanya tidak mengambil apa pun. Kalimat umurnya
  // karena itu datang dari `age()`, dan sebuah kegagalan yang belum digantikan tetap terdengar
  // — lengkap dengan warnanya.
  say(`${filtered}Sorted by ${order}. ${age()}`, state.failure === null ? "ok" : "error");
  return true;
}

/** Catat bahwa DOM sekarang benar-benar menampilkan layar yang diminta — judul jendela ikut. */
function shown(): void {
  state.shownScreen = state.screen;
  state.shownId = state.decisionId;
  state.rendered = true;
  // Judul menyusul ISI, bukan niat: ia hanya berubah sesudah render yang benar-benar terjadi,
  // jadi perpindahan yang gagal meninggalkan judul yang masih cocok dengan layar yang tertinggal.
  document.title =
    state.screen === "card" && state.decisionId !== null
      ? TITLE_CARD.replace("{id}", String(state.decisionId))
      : TITLE_WATCHLIST;
}

/**
 * Kalimat yang mengaku LAYAR MANA yang tertinggal saat pengambilan gagal.
 *
 * Ketiganya berbeda dan tidak boleh tertukar: belum ada apa pun dari klien (yang di layar milik
 * server), layar yang sama tetapi tua, atau layar SEBELUMNYA karena perpindahan gagal. Yang
 * tidak pernah menjadi jawaban: layar kosong.
 */
function kept(): string {
  if (state.shownScreen !== state.screen || state.shownId !== state.decisionId) return STALE_SCREEN;
  return state.rendered ? OFFLINE_KEPT : "the server-rendered screen below is untouched";
}

async function load(spoken: boolean): Promise<void> {
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
        `card #${state.decisionId} re-fetched from /api/decision/${state.decisionId} at ${state.fetchedAt}; ` +
          "the replay verdict on it was recomputed by the server, not by this client.",
        "ok",
      );
    } else {
      const payload = await getJson("/api/watchlist");
      state.payload = payload;
      state.entries = dicts(payload["symbols"]);
      state.loaded = true;
      state.fetchedAt = browserClock();
      state.failure = null;
      // Hanya render yang BENAR-BENAR terjadi yang boleh diakui: `paint()` menolak melukis
      // Layar A di atas URL kartu, dan `shown()` yang tetap dipanggil di situ akan membuat
      // kegagalan berikutnya berbunyi "isi klien yang tua" padahal yang di layar milik server.
      if (paint()) shown();
    }
  } catch (error) {
    const message =
      error instanceof ApiError && error.message.includes("`simulated: true`")
        ? UNVERIFIED_ENVELOPE
        : `could not refresh: ${error instanceof Error ? error.message : "unknown failure"}`;
    // Kalimat ini DISIMPAN, bukan hanya diucapkan: ia harus selamat dari ketikan berikutnya di
    // kotak saringan, yang melukis ulang baris status tanpa mengambil apa pun.
    state.failure =
      `${message} — ${kept()}` +
      (state.fetchedAt === null ? "." : `; it was fetched at ${state.fetchedAt}, ${NOT_REFRESHED_SINCE}.`);
    say(state.failure, "error");
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
  // Isi lama DITAHAN sampai responsnya datang, dan tetap ditahan kalau ia tidak pernah datang.
  // Versi sebelumnya `clear(viewNode)` lebih dulu: satu klik simbol dengan API yang gagal (atau
  // CSP yang memblokir `fetch`) menghapus tabel hasil render server dan tidak pernah
  // mengembalikannya — juri mendapat HALAMAN KOSONG permanen, karena cabang gagal `load()`
  // hanya menyentuh baris status. Layar kosong tidak boleh mungkin; yang boleh adalah layar
  // lama yang MENGAKU dirinya lama (`kept()`).
  void load(true);
}

function isInternal(link: HTMLAnchorElement): boolean {
  if (link.origin !== window.location.origin) return false;
  const path = link.pathname;
  return path === "/" || path === "/ui" || /^\/ui\/decision\/\d+$/.test(path);
}


/* `railbar()` DIBUANG di ADR-030 bersama elemennya: pita mendatar kedua di atas hero yang
   menduplikasi kepala halaman dengan kata yang berbeda untuk bagian yang sama, sementara posisi
   gulir yang ia bawa sudah dibawa bilah gulir browser. */

/** Smooth scroll (Lenis) + reveal saat gulir (GSAP ScrollTrigger). Landing SAJA.
 *
 * Tiga aturan yang membuat gerak ini tidak menabrak jaminan 4.6, dan semuanya wajib:
 *
 * 1. `prefers-reduced-motion` MEMATIKANNYA SELURUHNYA — bukan mempercepatnya. Lenis tidak
 *    pernah dibuat, ScrollTrigger tidak pernah dipasang, dan `js-motion` tidak pernah
 *    ditempel, jadi tidak ada satu pun elemen yang pernah `opacity: 0`. Gulir kembali menjadi
 *    gulir bawaan browser, yang memang yang diminta pemakainya.
 * 2. Kelas `js-motion` ditempel KLIEN. Keadaan awal reveal (`opacity: 0`) karena itu tidak
 *    pernah ada di HTML server: klien yang gagal memuat meninggalkan landing yang TERLIHAT.
 *    Ini butir 6 ADR-020, dan ia lebih penting daripada animasinya.
 * 3. Gerak tidak pernah menahan akses ke informasi. Reveal hanya memindahkan 18px dan memudar
 *    masuk; tidak ada isi yang hanya muncul setelah digulir, dan `once: true` memastikan blok
 *    yang sudah tampil tidak pernah menghilang lagi saat digulir balik.
 *
 * Kegagalan apa pun di sini dibiarkan diam: landing tetap halaman lengkap tanpa gerak.
 */
function motion(): void {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  // Yang muncul saat digulir: blok TEKS dan kartu. Gambar TIDAK ikut di sini — sebuah gambar
  // yang memudar masuk hanya menunda pemandangan, sementara blok yang memudar masuk memberi
  // halaman ritme baca. Daftarnya eksplisit supaya tidak ada elemen yang tanpa sengaja
  // ber-`opacity: 0` hanya karena ia kebetulan cocok dengan pemilih yang terlalu lebar.
  const targets = document.querySelectorAll<HTMLElement>(
    ".hero .kicker, .hero h1, .hero .stand, .hero .cta, " +
      ".sec-head, .gate-grid h2, .gate-copy, .rails, " +
      ".board .lb, .cur-say, .figs li, .les, .band-in"
  );
  if (!targets.length) return;

  document.documentElement.classList.add("js-motion");
  for (const node of targets) node.classList.add("reveal");

  // `lerp`, bukan `duration`: gulirnya mengejar posisi target dengan pecahan tetap tiap frame
  // (interpolasi linear), jadi gerak berhenti mulus dari kecepatan berapa pun dan tidak punya
  // panjang tetap yang terasa seperti animasi. 0.08 ada di tengah rentang yang enak (0.06-0.1);
  // di bawahnya halaman terasa berat, di atasnya efeknya hilang.
  const lenis = new Lenis({ lerp: 0.08, smoothWheel: true });
  lenis.on("scroll", ScrollTrigger.update);
  gsap.ticker.add((time: number) => lenis.raf(time * 1000));
  gsap.ticker.lagSmoothing(0);

  gsap.registerPlugin(ScrollTrigger);
  targets.forEach((node, index) => {
    gsap.to(node, {
      opacity: 1,
      y: 0,
      duration: 0.7,
      // Saudara di dalam satu kartu berangkat berurutan, bukan serentak: itu yang membuat
      // sebuah bagian terbaca sebagai satu kalimat, bukan sebagai empat kotak yang muncul.
      delay: (index % 3) * 0.06,
      ease: "power2.out",
      scrollTrigger: { trigger: node, start: "top 90%", once: true },
    });
  });

  // Judul hero mendapat tween SENDIRI, dan ia menimpa reveal umum di atas untuk elemen yang
  // sama (GSAP menjalankan tween terakhir yang menyentuh properti itu). Alasannya: ia satu-
  // satunya elemen yang sudah terlihat pada frame pertama, jadi ia tidak "muncul saat digulir"
  // melainkan MENDARAT — lebih lambat, dari lebih jauh, sekali saja.
  const headline = document.querySelector<HTMLElement>(".hero h1");
  if (headline) {
    gsap.to(headline, { opacity: 1, y: 0, duration: 1.2, delay: 0.1, ease: "power3.out" });
  }

  // Parallax: gambar bergerak lebih lambat daripada halaman. `scrub` mengikatnya ke posisi
  // gulir (bukan ke waktu), jadi ia tidak pernah bergerak sendiri saat halaman diam, dan ia
  // hanya menyentuh `y` sebuah elemen DEKORATIF — tidak ada teks yang ikut bergeser.
  for (const art of document.querySelectorAll<HTMLElement>(".proofs-art img, .band-art img")) {
    gsap.to(art, {
      yPercent: 12,
      ease: "none",
      scrollTrigger: { trigger: art.parentElement ?? art, start: "top bottom", end: "bottom top", scrub: 0.6 },
    });
  }
}

function start(): void {
  const body = document.body;
  const main = document.getElementById("isi");
  if (!main) return;
  const declared = num(Number(body.dataset["pollMs"]));
  if (declared !== null && declared >= 1000) pollMs = declared;

  viewNode = el("div", { id: "tenko-view" });
  while (main.firstChild) viewNode.appendChild(main.firstChild);

  const screen = body.dataset["screen"];
  state.screen = screen === "card" ? "card" : "watchlist";
  state.decisionId = screen === "card" ? Number(body.dataset["decisionId"] ?? "0") || null : null;

  controlsNode = buildControls();
  liveNode = el("p", { id: "tenko-live", class: "live state-ok", role: "status", "aria-live": "polite" });
  // Kalimat pembuka pun harus cocok dengan layarnya: halaman gagal tidak pernah mengambil apa
  // pun, jadi ia tidak boleh berjanji akan mengambil.
  // `landing` dan `error` sama-sama INERT (ADR-021 butir 6). Tanpa cabang ini keduanya jatuh ke
  // `state.screen = "watchlist"` di atas, dan klien akan melukis Layar A di atas halaman yang
  // bukan Layar A — landing kehilangan seluruh isinya beberapa milidetik setelah dibuka.
  if (screen === "landing") motion();
  const inert = screen === "error" || screen === "landing";
  liveNode.textContent = inert ? CLIENT_INERT : CLIENT_ATTACHED;
  main.appendChild(controlsNode);
  // Landing tidak mendapat baris status sama sekali (ADR-030). Dua alasan, dan keduanya cukup
  // sendirian: tidak ada satu pun isi di sana yang pernah berubah, jadi tidak ada yang perlu
  // diumumkan; dan kalimat inert itu berbunyi "on a failure page…", yaitu kalimat layar yang
  // LAIN. Yang tersisa hanyalah sebuah kotak — pita mendatar kedua tepat di atas hero, di
  // halaman yang seluruh nilainya adalah kekosongan yang tersisa. Layar gagal tetap
  // mendapatkannya: di sana kalimat itu benar, dan di sana ada sesuatu yang harus diakui.
  if (screen !== "landing") main.appendChild(liveNode);
  main.appendChild(viewNode);
  // Yang ADA di DOM sekarang adalah layar hasil render server untuk URL ini — dicatat supaya
  // kalimat kegagalan berikutnya bisa membedakan "layar ini, tua" dari "layar yang LAIN".
  state.shownScreen = state.screen;
  state.shownId = state.decisionId;
  controlsNode.hidden = state.screen !== "watchlist" || inert;
  if (inert) return;

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

  if (state.screen === "card" && state.decisionId === null) {
    // Halaman kartu tanpa `data-decision-id`: tidak ada yang bisa diminta. Yang TIDAK boleh
    // terjadi adalah jatuh ke cabang watchlist dan melukis Layar A di atas URL kartu — atau
    // mengaku sudah merender sesuatu padahal isi di layar milik server.
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
