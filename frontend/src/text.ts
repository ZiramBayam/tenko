// Kalimat yang WAJIB sama persis dengan `ui/screens.py` / `ui/app.py`.
//
// Klien merender tabel yang sama dengan yang dirender server, jadi dua salinan kalimat yang
// boleh berselisih = dua layar yang berbeda untuk pertanyaan juri yang sama. Yang menjaganya
// bukan kedisiplinan melainkan tes: `tests/test_ui.py::test_the_client_and_the_server_speak_one
// _sentence_not_two` mem-parse berkas INI dan membandingkannya dengan konstanta Python-nya.
// Karena itu setiap baris di bawah berbentuk `export const NAMA = "...";` satu baris — bentuk
// yang bisa dibaca ulang tanpa menjalankan Node.

export const EMPTY_SENTENCE = "no data yet (n=0)";
export const NEUTRAL_MARK = "neutral — not against it, and not a confirmation";
export const UNSPOKEN_MARK = "unspoken — the evidence is not enough, and that is not zero";
export const ABSENT_MARK = "not stated";
export const ARIA_NULL = "the value is not in the evidence — unspoken, and that is not zero";
export const ARIA_NO_SOURCE = "source not stated";
export const NARRATION_LABEL = "model narration — NOT verified against the evidence";
export const NARRATION_EMPTY = "no narration for this row";
export const VETO_RANK_COLUMN = "veto power (spec §4, not validated)";
export const AGE_NOTE = "This age is PRESENTATION: it is computed from the bundle `as_of` (guarded by `payload_hash`), not from the ledger row `ts_utc` (guarded by no hash at all), and it never enters `decision_hash`.";
export const NARRATION_NOTE = "The sentences in this column were written by a language model. Only their `item_id` is validated; their CONTENT is never checked against the evidence, and no code stops the model from writing a verdict. What can be replayed is the `reason` column beside it, not this column.";

// --- kalimat milik klien saja (tidak ada kembarannya di server) -----------------------------

// Kenapa klien tidak mencetak umur relatif: ia akan menjadi ARITMETIKA KEDUA atas jam, ditulis
// dua kali dalam dua bahasa, dan tidak ada tes Python yang bisa menjalankan versi TypeScript-nya.
// Yang dicetak klien hanyalah `as_of_jst` yang datang dari API, ditambah kalimat ini.
export const AGE_CLIENT_NOTE = "the relative age line is written by the server render only; this row was refreshed by the client, so reload the page for a fresh age";
export const UNVERIFIED_ENVELOPE = "this API response did not say `simulated: true`, so nothing from it was rendered; the screen you can see is the one the server rendered";
export const LOADING = "loading from the API…";
export const OFFLINE_KEPT = "the last successful response is still on screen and it is OLD; nothing was invented to replace it";
