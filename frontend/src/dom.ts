// Pembuat elemen. SATU pintu, dan ia hanya bisa memasang TEKS.
//
// Tidak ada `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, maupun
// `new Function` di seluruh `frontend/` — bukan sebagai gaya melainkan karena nilai yang
// dirender datang dari RYO/DB. `ui/screens.py` menutup permukaan itu dengan `html.escape` +
// `<bdi>`; di sini penutupnya adalah `textContent`, yang secara konstruksi tidak bisa membuka
// satu tag pun. Tes Python membaca berkas TS ini DAN bundel hasilnya untuk memastikannya.

export type Attrs = Record<string, string | number | null | undefined>;

export function el(tag: string, attrs: Attrs = {}, text?: string | null): HTMLElement {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === null || value === undefined) continue;
    node.setAttribute(name, String(value));
  }
  if (text !== null && text !== undefined) node.textContent = text;
  return node;
}

/** Nilai asal-RYO/DB. `<bdi>` mengisolasi arah teksnya, seperti `ui/screens.py:_bdi`. */
export function bdi(text: string, lang?: string): HTMLElement {
  const node = el("bdi", lang ? { lang } : {});
  node.textContent = text;
  return node;
}

export function append(parent: Node, ...children: (Node | null)[]): void {
  for (const child of children) if (child) parent.appendChild(child);
}

export function clear(node: Node): void {
  while (node.firstChild) node.removeChild(node.firstChild);
}
