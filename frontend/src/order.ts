// Urutan — dan HANYA urutan.
//
// Berkas ini dipisah dari `render.ts` karena satu aturan yang lebih mudah ditegakkan daripada
// dijelaskan: **modul yang merender tidak boleh mengganti nilai kosong dengan nol.** Mengurutkan
// menuntut angka untuk setiap baris, jadi baris tanpa `n_blocking` harus mendarat di suatu
// tempat; menampilkan menuntut yang sebaliknya, yaitu KATA. Selama kedua pekerjaan itu tinggal
// di berkas yang sama, `?? 0` yang sah untuk urutan bisa merembes ke sel yang dilihat juri.
// Sekarang tidak bisa: `tests/test_ui.py` menolak `?? 0` di `render.ts`, dan berkas ini tidak
// pernah menyentuh DOM.

import type { Json } from "./json.js";
import { num, text } from "./json.js";

export type Key = [number, number, number, string];

export function compare(a: Key, b: Key): number {
  for (let index = 0; index < 3; index += 1) {
    const left = a[index] as number;
    const right = b[index] as number;
    if (left !== right) return left < right ? -1 : 1;
  }
  return a[3] < b[3] ? -1 : a[3] > b[3] ? 1 : 0;
}

/**
 * Baris yang paling dekat GO — kembaran `ui/screens.py:_closest_to_go`.
 *
 * Bukan skor dan bukan ramalan: ia membaca urutan yang SUDAH ditetapkan server secara terbalik,
 * dari field yang sudah ada di respons. Aturannya tercetak di layar tepat di bawah kalimatnya.
 */
export function closestToGo(entries: Record<string, Json>[]): Record<string, Json> | null {
  let best: Record<string, Json> | null = null;
  let bestKey: Key | null = null;
  for (const entry of entries) {
    const blocking = num(entry["n_blocking"]) ?? 0;
    const key: Key = [blocking ? 1 : 0, -(num(entry["veto_rank"]) ?? 0), blocking, text(entry["symbol"]) ?? ""];
    if (bestKey === null || compare(key, bestKey) < 0) {
      best = entry;
      bestKey = key;
    }
  }
  return best;
}

/** Berapa baris yang DITAHAN di antara yang sedang tampil — cacah, bukan angka baru. */
export function heldCount(entries: Record<string, Json>[]): number {
  return entries.filter((entry) => (num(entry["n_blocking"]) ?? 0) > 0).length;
}
