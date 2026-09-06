// Pembaca JSON yang tidak pernah MENGARANG nilai.
//
// Aturan tunggalnya: yang tidak ada tetap tidak ada. `num()` mengembalikan `null` untuk apa pun
// yang bukan angka — TIDAK PERNAH 0 — dan `text()` mengembalikan `null` untuk apa pun yang
// bukan teks, TIDAK PERNAH "". Semua penyaji di `render.ts` mengubah `null` menjadi kata
// (`ABSENT_MARK`), bukan menjadi sel kosong. Inilah seluruh alasan produk ini ada.

export type Json = unknown;

export function obj(value: Json): Record<string, Json> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, Json>)
    : {};
}

export function arr(value: Json): Json[] {
  return Array.isArray(value) ? value : [];
}

export function dicts(value: Json): Record<string, Json>[] {
  return arr(value)
    .filter((entry) => typeof entry === "object" && entry !== null && !Array.isArray(entry))
    .map((entry) => entry as Record<string, Json>);
}

export function strings(value: Json): string[] {
  return arr(value).filter((entry): entry is string => typeof entry === "string");
}

export function text(value: Json): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

export function num(value: Json): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Bentuk terbaca dari nilai apa pun — kembaran `ui/screens.py:_scalar`.
 *
 * Ia TIDAK meratakan `null` menjadi 0 atau "": pemanggilnya memutuskan kata apa yang dipakai
 * untuk ketiadaan, dan jawabannya selalu sebuah KATA.
 */
export function scalar(value: Json): string {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "null";
  if (Array.isArray(value)) return "[" + value.map(scalar).join(", ") + "]";
  const entries = Object.entries(obj(value)).sort((a, b) => (a[0] < b[0] ? -1 : 1));
  return entries.map(([key, item]) => `${key}=${scalar(item)}`).join("; ");
}
