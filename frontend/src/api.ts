// Satu-satunya jalan keluar klien: `/api/...` di origin ini sendiri.
//
// Tidak ada host lain yang boleh disebut di berkas ini (tes Python menolak `http` di seluruh
// `frontend/`), dan tidak ada kredensial apa pun yang dipegang klien — UI hanya bicara ke API-nya
// sendiri, yang membaca store BACA-SAJA.

import type { Json } from "./json.js";
import { obj } from "./json.js";

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Ambil satu badan JSON, dan TOLAK yang tidak mengaku simulasi.
 *
 * `simulated: true` adalah kewajiban `docs/rules-compliance.md` untuk SETIAP respons. Klien
 * yang merender badan tanpa penanda itu akan menampilkan angka yang tidak lagi bisa dipastikan
 * berasal dari layar simulasi ini — jadi ia menolak merendernya dan mengatakan kenapa,
 * meninggalkan layar hasil render server apa adanya.
 */
export async function getJson(path: string): Promise<Record<string, Json>> {
  let response: Response;
  try {
    response = await fetch(path, {
      headers: { accept: "application/json" },
      credentials: "omit",
      cache: "no-store",
    });
  } catch {
    throw new ApiError(`the API at ${path} could not be reached`, 0);
  }
  let body: Json;
  try {
    body = (await response.json()) as Json;
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
