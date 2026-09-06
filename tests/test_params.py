"""Tes TASKS 2.1 — `preflight/params.py` (ambang + tabel kosakata + params_hash).

Acuan: docs/spec.md §4, docs/api-facts.md §A "Kosakata terukur" (697 snapshot, api-verifier
1 Sep 2026), ADR-003 (keputusan deterministik), ADR-013.

Yang dijaga tes ini: (a) `params_hash` deterministik DAN berubah untuk setiap ambang/tabel
yang berubah — kalau tidak, keputusan lama akan tampak bisa di-replay padahal aturannya
sudah lain; (b) dua namespace verdict TIDAK tercampur; (c) pencocokan string PENUH, tanpa
pemotongan di em dash; (d) tidak ada nilai karangan di tabel.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import get_args, get_type_hints
from urllib.parse import quote

import pytest

from core.replay import params_hash as replay_params_hash
from preflight import params as P
from preflight.params import (
    ANALYZE_TOKEN_VERDICT,
    COMPARE_TOKENS_VERDICT,
    DEEP_ANALYSIS_VERDICT_CALL,
    DEFAULT_PARAMS,
    ITEM_DERIVATIVES,
    ITEM_ORDER,
    MARKET_REGIME,
    VOCABULARIES,
    Params,
    VocabEntry,
    Vocabulary,
    params_hash,
)

ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = ROOT / "data" / "ryo.db"


# --- params_hash -------------------------------------------------------------------------
def test_params_hash_is_deterministic() -> None:
    assert DEFAULT_PARAMS.params_hash() == DEFAULT_PARAMS.params_hash()
    assert Params().params_hash() == Params().params_hash()


def test_params_hash_uses_the_single_repo_definition() -> None:
    """Satu definisi hash (core/replay.py), bukan varian kedua di preflight."""
    assert DEFAULT_PARAMS.params_hash() == replay_params_hash(DEFAULT_PARAMS.as_dict())
    assert params_hash(DEFAULT_PARAMS) == replay_params_hash(DEFAULT_PARAMS.as_dict())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_age_min", 30),
        ("min_confluence", 0.5),
        ("min_rr", 1.5),
        ("peers", ("BTC", "ETH")),
        ("neutral_stance_status", "red"),
        ("optional_items", frozenset()),
    ],
)
def test_params_hash_changes_when_a_threshold_changes(field: str, value: object) -> None:
    changed = DEFAULT_PARAMS.with_overrides(**{field: value})
    assert changed.params_hash() != DEFAULT_PARAMS.params_hash()


def test_params_hash_changes_when_a_vocabulary_mapping_changes() -> None:
    """Mengubah SATU pemetaan kosakata = mengubah keputusan; hash wajib ikut berubah."""
    tampered = Vocabulary(
        field=ANALYZE_TOKEN_VERDICT.field,
        entries={**ANALYZE_TOKEN_VERDICT.entries, "avoid": VocabEntry(P.BULLISH, "karangan tes")},
    )
    others = tuple(v for v in VOCABULARIES if v.field != ANALYZE_TOKEN_VERDICT.field)
    changed = DEFAULT_PARAMS.with_overrides(vocabularies=(tampered, *others))
    assert changed.params_hash() != DEFAULT_PARAMS.params_hash()


def test_params_hash_accepts_plain_mapping_too() -> None:
    assert params_hash({"a": 1}) == replay_params_hash({"a": 1})


def test_as_dict_is_json_serialisable_with_sorted_keys() -> None:
    text = json.dumps(DEFAULT_PARAMS.as_dict(), sort_keys=True, allow_nan=False)
    assert "vocabularies" in text and "max_age_min" in text


# --- validasi ----------------------------------------------------------------------------
def test_rejects_nonsense_thresholds() -> None:
    with pytest.raises(ValueError, match="max_age_min"):
        Params(max_age_min=0)
    with pytest.raises(ValueError, match="min_rr"):
        Params(min_rr=float("nan"))
    with pytest.raises(ValueError, match="di luar spec"):
        Params(optional_items=frozenset({"99_invented"}))


def test_rejects_two_tables_for_the_same_field() -> None:
    """Satu field = satu namespace; tabel ganda adalah pintu masuk pencampuran kosakata."""
    duplicate = Vocabulary(field=ANALYZE_TOKEN_VERDICT.field, entries={})
    with pytest.raises(ValueError, match="namespace"):
        Params(vocabularies=(ANALYZE_TOKEN_VERDICT, duplicate))


def test_only_item_8_is_optional() -> None:
    assert DEFAULT_PARAMS.optional_items == frozenset({ITEM_DERIVATIVES})
    assert DEFAULT_PARAMS.is_required(ITEM_DERIVATIVES) is False
    for item_id in ITEM_ORDER:
        if item_id != ITEM_DERIVATIVES:
            assert DEFAULT_PARAMS.is_required(item_id) is True


# --- namespace verdict -------------------------------------------------------------------
def test_verdict_namespaces_are_separate_tables() -> None:
    """`compare_tokens` memakai kosakata `deep_analysis.verdict.call`, BUKAN `analyze_token`."""
    assert ANALYZE_TOKEN_VERDICT.field == "analyze_token.data.verdict"
    assert DEEP_ANALYSIS_VERDICT_CALL.field == "deep_analysis.data.verdict.call"
    assert COMPARE_TOKENS_VERDICT.field == "compare_tokens.data.tokens[].verdict"
    # Nilai analyze_token TIDAK dikenal di dua tabel lain, dan sebaliknya.
    for raw in ("accumulate", "avoid", "caution — overbought"):
        assert ANALYZE_TOKEN_VERDICT.lookup(raw) is not None
        assert DEEP_ANALYSIS_VERDICT_CALL.lookup(raw) is None
        assert COMPARE_TOKENS_VERDICT.lookup(raw) is None
    for raw in ("constructive", "cautious"):
        assert ANALYZE_TOKEN_VERDICT.lookup(raw) is None
        assert DEEP_ANALYSIS_VERDICT_CALL.lookup(raw) is not None


def test_every_table_declares_the_field_it_belongs_to() -> None:
    for vocab in VOCABULARIES:
        assert vocab.field and "." in vocab.field
        for raw, entry in vocab.entries.items():
            assert isinstance(raw, str) and entry.provenance, (vocab.field, raw)


# --- bentuk string -----------------------------------------------------------------------
def test_full_string_match_only_no_em_dash_splitting() -> None:
    """Kepala setelah split TIDAK dipakai; kualifikasi ikut tercatat (instruksi api-verifier)."""
    entry = ANALYZE_TOKEN_VERDICT.lookup("caution — overbought")
    assert entry is not None and entry.qualifier == "overbought"
    assert ANALYZE_TOKEN_VERDICT.lookup("caution") is None
    # Bentuk majemuk BARU tidak boleh dipotong jadi `avoid` yang sudah dikenal.
    assert ANALYZE_TOKEN_VERDICT.lookup("avoid — extended") is None
    assert ANALYZE_TOKEN_VERDICT.lookup("euphoric") is None


def test_lookup_never_guesses_from_whitespace_case_or_type() -> None:
    for raw in (" accumulate", "accumulate ", "ACCUMULATE", "Accumulate", None, 1, True, ["accumulate"]):
        assert ANALYZE_TOKEN_VERDICT.lookup(raw) is None


def test_em_dash_separator_is_the_exact_observed_form() -> None:
    """Pemisahnya persis spasi + U+2014 + spasi (api-facts §A), bukan '-' atau '–'."""
    compound = [raw for raw in ANALYZE_TOKEN_VERDICT.entries if " " in raw]
    assert compound == ["caution — overbought"]
    assert "—" in compound[0] and "–" not in compound[0]


# --- isi tabel = amatan, bukan karangan --------------------------------------------------
def test_tables_hold_exactly_the_values_api_facts_lists() -> None:
    """Nilai teramati (api-facts §A) hadir; satu-satunya nilai tanpa amatan ditandai jelas."""
    assert set(ANALYZE_TOKEN_VERDICT.entries) == {
        "accumulate",
        "neutral",
        "caution — overbought",
        "avoid",
    }
    assert set(DEEP_ANALYSIS_VERDICT_CALL.entries) == {"constructive", "neutral", "cautious"}
    assert set(P.CONFLUENCE_STATE.entries) == {"CONFIRMED", "MIXED"}
    assert set(P.SENTIMENT_LABEL.entries) == {"extreme_greed", "greed"}
    assert set(P.SENTIMENT_REGIME.entries) == {
        "extreme_greed_unconfirmed",
        "mixed_sentiment",
        "complacent_crowding",
    }
    # `risk_off` teramati pertama kali pada snapshot id=763 (2 Sep 2026); `up` tetap 0× dan
    # karena itu tetap dicabut.
    assert set(MARKET_REGIME.entries) == {"risk_on", "neutral", "risk_off"}
    assert set(P.FEAR_GREED_CHANGE_DIRECTION.entries) == {"down", "flat"}
    assert set(P.TECHNICAL_TREND.entries) == {"up", "sideways", "down"}
    # TASKS 0.12: `long_crowding`/`extreme` masuk dengan provenance (id 1976/2047/2106).
    # Ejaannya `long_crowding` — string `crowded_long` yang pernah kami karang TIDAK ada di sini.
    assert set(P.FUNDING_CROWDING_STATE.entries) == {"normal", "long_crowding"}
    assert "crowded_long" not in P.FUNDING_CROWDING_STATE.entries
    assert set(P.LIQUIDATION_PRESSURE_STATE.entries) == {"normal", "extreme"}
    # `short_crowding` belum pernah teramati; ia hanya boleh hidup di fixture tes.
    assert "short_crowding" not in P.FUNDING_CROWDING_STATE.entries


#: Tabel yang nilainya dibaca `preflight.items._classify`. Konsekuensinya keras: `_classify`
#: TIDAK punya cabang untuk `KNOWN`, jadi entri `KNOWN` di sini akan jatuh ke `RED` dengan
#: alasan "opposed to the plan" — nilai yang netral akan diam-diam memveto.
_CLASSIFIED_TABLES = (
    "ANALYZE_TOKEN_VERDICT",
    "DEEP_ANALYSIS_VERDICT_CALL",
    "COMPARE_TOKENS_VERDICT",
    "MARKET_REGIME",
    "FEAR_GREED_CHANGE_DIRECTION",
)
#: Tabel "keadaan": hanya tenang atau siaga, tidak ada arah.
_STATE_TABLES = ("FUNDING_CROWDING_STATE", "LIQUIDATION_PRESSURE_STATE")
#: Tabel konteks kartu: tidak menggerakkan status item mana pun.
_CONTEXT_TABLES = ("SENTIMENT_LABEL", "SENTIMENT_REGIME", "TECHNICAL_TREND")


@pytest.mark.parametrize("name", _CLASSIFIED_TABLES)
def test_classified_tables_never_hold_a_known_entry(name: str) -> None:
    """`KNOWN` di tabel yang masuk `_classify` = veto diam-diam (temuan quant TASKS 0.12)."""
    allowed = {P.BULLISH, P.BEARISH, P.NEUTRAL, P.ABSTAIN}
    meanings = {entry.meaning for entry in getattr(P, name).entries.values()}
    assert meanings <= allowed, f"{name}: {sorted(meanings - allowed)} tidak dikenal _classify"


@pytest.mark.parametrize("name", _STATE_TABLES)
def test_state_tables_only_say_calm_or_alert(name: str) -> None:
    meanings = {entry.meaning for entry in getattr(P, name).entries.values()}
    assert meanings <= {P.CALM, P.ALERT}


@pytest.mark.parametrize("name", _CONTEXT_TABLES)
def test_context_tables_never_move_an_item(name: str) -> None:
    meanings = {entry.meaning for entry in getattr(P, name).entries.values()}
    assert meanings == {P.KNOWN}


def test_the_crowding_alert_carries_the_side_it_sits_on() -> None:
    """Tanpa `qualifier`, item 8 tidak bisa membedakan "searah plan" dari "sisi seberang"."""
    assert P.FUNDING_CROWDING_STATE.entries["long_crowding"].qualifier == "long"
    assert P.FUNDING_CROWDING_STATE.entries["normal"].qualifier is None
    # Pressure tidak bersyarat arah menurut spec §4 item 8, jadi ia TIDAK boleh punya sisi.
    assert P.LIQUIDATION_PRESSURE_STATE.entries["extreme"].qualifier is None


#: `params_hash` yang BERLAKU. Dipatok, bukan sekadar "berbeda dari yang lama": `provenance`
#: ikut ke `as_dict()`, jadi menyunting PROSA satu entri pun menggeser hash ini dan membuat
#: setiap baris `decisions` menjadi `STALE_PARAMS`. Penjaga yang hanya menuntut `!= before`
#: membiarkan pergeseran senyap itu lolos (mutan M4 security-reviewer 0.12 selamat).
#: Memperbaruinya adalah tindakan SADAR: pastikan Anda memang bermaksud menggeser params.
CURRENT_PARAMS_HASH = "3d39fb1bd1a03b7f5269f53fec347d34645d2b402ff1c2de47e53f19d69616b7"
#: Hash sebelum TASKS 0.12, tercatat di `decisions` id 20-27 pada `data/ryo.db`.
PARAMS_HASH_BEFORE_0_12 = "f2781184fc0e168c9e2011f873519c51e8561675fc6b7d17d60a445bd692291f"


def test_params_hash_is_pinned_so_no_change_to_the_tables_is_silent() -> None:
    """Empat baris kosakata 0.12 menggeser `params_hash`, dan pergeserannya dipatok.

    Sesudah 0.12, baris `decisions` id 20-27 dilaporkan `STALE_PARAMS` (TASKS 0.14) — bukan
    `MISMATCH`, dan bukan lagi `NO_EVIDENCE` untuk id 20-23. Itu konsekuensi yang diterima
    sadar oleh pemilik repo saat memberi izin membuka params yang dibekukan TASKS 5.0.
    """
    assert P.DEFAULT_PARAMS.params_hash() == CURRENT_PARAMS_HASH
    assert CURRENT_PARAMS_HASH != PARAMS_HASH_BEFORE_0_12


def test_editing_only_the_provenance_prose_still_moves_the_hash() -> None:
    """Alasan tes di atas mematok nilai, bukan ketidaksamaan: prosa pun menggeser hash."""
    entry = P.SENTIMENT_REGIME.entries["complacent_crowding"]
    edited = P.Vocabulary(
        field=P.SENTIMENT_REGIME.field,
        entries={
            **P.SENTIMENT_REGIME.entries,
            "complacent_crowding": P.VocabEntry(entry.meaning, entry.provenance + " (disunting)"),
        },
    )
    others = tuple(v for v in P.VOCABULARIES if v.field != edited.field)
    moved = P.DEFAULT_PARAMS.with_overrides(vocabularies=(edited, *others))
    assert moved.params_hash() != CURRENT_PARAMS_HASH


def test_adr_017_is_a_decision_not_a_proposal() -> None:
    """TASKS 2.1c AC(1): ADR-017 DITERIMA 6 Sep 2026; kata "USULAN" tidak boleh tersisa.

    Selama ADR ini USULAN, kartu GO kami hijau atas alasan yang belum diputuskan siapa pun.
    Tes ini membaca dokumennya, bukan mengandalkan ingatan.
    """
    text = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    sections = [line for line in text.splitlines() if line.startswith("## ADR-017")]
    assert len(sections) == 1, sections
    assert "DITERIMA" in sections[0] and "USULAN" not in sections[0]
    start = text.index(sections[0])
    end = text.index("\n## ADR-018", start)
    body = text[start:end]
    assert "USULAN" not in body
    assert "**Status: DITERIMA**" in body


def _neutral_knob_comment() -> str:
    """Blok komentar `#:` tepat di atas `neutral_stance_status` — docstring atribut itu."""
    lines = (ROOT / "preflight" / "params.py").read_text(encoding="utf-8").splitlines()
    idx = next(i for i, line in enumerate(lines) if line.strip().startswith("neutral_stance_status:"))
    block: list[str] = []
    for line in reversed(lines[:idx]):
        if not line.strip().startswith("#:"):
            break
        block.append(line)
    return "\n".join(reversed(block))


def test_the_neutral_knob_is_justified_by_adr_017_and_not_by_reading_spec_word_for_word() -> None:
    """TASKS 2.1c AC(2): docstringnya menyebut ADR-017 dan berhenti memakai alasan lemahnya.

    Alasan lama ("bacaan harfiah kolom Merah" spec §4) tidak bisa dipertahankan: spec §4
    TIDAK menuliskan keadaan ketiga sama sekali, jadi tidak ada kalimat yang bisa dibaca
    harfiah untuknya. Yang menanggung default `green` sekarang adalah ADR-017.
    """
    comment = _neutral_knob_comment()
    assert "ADR-017" in comment
    assert "DITERIMA" in comment and "USULAN" not in comment
    assert "harfiah" not in comment


def test_the_neutral_knob_keeps_both_settings_so_adr_017_stays_revocable() -> None:
    """TASKS 2.1c AC(3): knob TIDAK dihapus — `red` tetap dapat dipilih dan tetap material."""
    source = (ROOT / "preflight" / "params.py").read_text(encoding="utf-8")
    assert 'neutral_stance_status: Literal["green", "red"] = "green"' in source
    hints = get_type_hints(Params, include_extras=True)
    assert set(get_args(hints["neutral_stance_status"])) == {"green", "red"}
    assert DEFAULT_PARAMS.neutral_stance_status == "green"
    flipped = DEFAULT_PARAMS.with_overrides(neutral_stance_status="red")
    assert flipped.params_hash() != CURRENT_PARAMS_HASH


def test_deciding_adr_017_did_not_move_the_frozen_params_hash() -> None:
    """TASKS 2.1c AC(3) berdiri di atas asumsi yang keliru, dan itu ditulis apa adanya.

    AC menuntut `params_hash` "berubah tepat sekali" saat ADR-017 diputuskan. Materi hash
    adalah :meth:`Params.as_dict` — NILAI, bukan prosa komentar (lihat `as_dict`, yang tidak
    membaca satu docstring pun). 2.1c hanya mengganti alasan tertulis, bukan nilai `green`
    yang sudah dipakai sejak 2 Sep, jadi hash tidak bergeser — dan itulah hasil yang benar:
    tidak ada baris `decisions` yang menjadi `STALE_PARAMS` karena sebuah kalimat disunting.
    Yang menggeser hash adalah MENGUBAH knobnya (tes di atas), dan itu belum terjadi.
    """
    assert (
        DEFAULT_PARAMS.params_hash()
        == CURRENT_PARAMS_HASH
        == "3d39fb1bd1a03b7f5269f53fec347d34645d2b402ff1c2de47e53f19d69616b7"
    )


def test_no_table_row_exists_without_an_observation_count() -> None:
    """Aturan positif: tiap baris kosakata mengutip hitungan amatan > 0, tanpa kecuali.

    Tidak ada lagi baris yang dibenarkan oleh teks spec saja. `change_direction: up` (0/20;
    spec bahkan tidak pernah menulis string itu) dicabut 2 Sep 2026; `regime: risk_off`
    dikembalikan pada hari yang sama BUKAN karena spec melainkan karena baris amatan
    pertamanya muncul (snapshot id=763, 2026-09-01T18:06:48Z).
    """
    assert "up" not in P.FEAR_GREED_CHANGE_DIRECTION.entries
    # `risk_off` boleh ada HANYA karena provenance-nya menyebut baris amatan yang nyata.
    risk_off = MARKET_REGIME.entries["risk_off"].provenance
    assert "id=763" in risk_off and "data/ryo.db" in risk_off and "docs/spec.md" not in risk_off
    for vocab in VOCABULARIES:
        for raw, entry in vocab.entries.items():
            counts = [int(n) for n in re.findall(r"(\d+)×", entry.provenance)]
            assert counts, f"{vocab.field}[{raw!r}] tidak mengutip hitungan amatan"
            assert max(counts) > 0, f"{vocab.field}[{raw!r}] nol amatan: {entry.provenance}"
            assert "docs/spec.md" not in entry.provenance, (vocab.field, raw)


def test_the_only_zero_count_in_its_own_field_is_declared_as_such() -> None:
    """Satu-satunya baris ber-0× DI FIELD-NYA SENDIRI: `compare_tokens…verdict = cautious`.

    Dibenarkan api-facts §A (field itu memakai namespace `deep_analysis.verdict.call`, 14×
    di sana) dan tidak berbahaya: verdict di item 10 hanya konteks kartu, tidak bisa
    menggerakkan status item mana pun.
    """
    entry = COMPARE_TOKENS_VERDICT.entries["cautious"]
    assert "0× di field ini" in entry.provenance
    assert "deep_analysis.verdict.call" in entry.provenance
    for vocab in VOCABULARIES:
        for raw, other in vocab.entries.items():
            if (vocab.field, raw) == (COMPARE_TOKENS_VERDICT.field, "cautious"):
                continue
            # dicocokkan sebagai ANGKA, bukan substring: "20×" mengandung "0×".
            counts = [int(n) for n in re.findall(r"(\d+)×", other.provenance)]
            assert 0 not in counts, (vocab.field, raw, other.provenance)


#: Penanda "telusuri setiap elemen list di sini" pada jalur kanari.
LIST = object()


def dig(value: object, path: tuple[object, ...]) -> list[str]:
    """Setiap string di ujung `path`, menelusuri list bila jalurnya menyebut :data:`LIST`.

    Versi pertama kanari ini hanya menelusuri dict, jadi `compare_tokens.data.tokens[].verdict`
    — satu-satunya nilai kosakata yang hidup DI DALAM list — tidak pernah disapu, dan tabel
    `COMPARE_TOKENS_VERDICT` tidak dijaga apa pun. Ia hijau karena kebetulan, bukan karena
    diperiksa (temuan quant-engineer saat TASKS 0.12).
    """
    if not path:
        return [value] if isinstance(value, str) else []
    head, rest = path[0], path[1:]
    if head is LIST:
        return [found for item in value for found in dig(item, rest)] if isinstance(value, list) else []
    return dig(value.get(head), rest) if isinstance(value, dict) else []


def test_the_canary_walks_into_lists_not_just_dicts() -> None:
    """Tes negatif: tanpa ini, "kanari hijau" tidak berarti apa-apa untuk nilai di dalam list."""
    payload = {"tokens": [{"verdict": "constructive"}, {"verdict": "DIKARANG"}]}
    assert dig(payload, ("tokens", LIST, "verdict")) == ["constructive", "DIKARANG"]
    # Bentuk yang tidak cocok tidak boleh meledak, dan tidak boleh diam-diam menemukan apa pun.
    assert dig(payload, ("tokens", "verdict")) == []
    assert dig({"tokens": "bukan list"}, ("tokens", LIST, "verdict")) == []
    assert dig(None, ("tokens", LIST, "verdict")) == []


@pytest.mark.skipif(not LIVE_DB.exists(), reason="data/ryo.db tidak ada (tidak di-commit)")
def test_an_invented_value_inside_a_list_turns_the_canary_red() -> None:
    """Kanari harus MENGGIGIT untuk nilai di dalam list, bukan sekadar tidak meledak."""
    forged = {"tokens": [{"verdict": "constructive"}, {"verdict": "DIKARANG"}]}
    found = dig(forged, ("tokens", LIST, "verdict"))
    unknown = [value for value in found if COMPARE_TOKENS_VERDICT.lookup(value) is None]
    assert unknown == ["DIKARANG"]


@pytest.mark.skipif(not LIVE_DB.exists(), reason="data/ryo.db tidak ada (tidak di-commit)")
def test_tables_still_cover_every_value_in_the_live_recorder_db() -> None:
    """Kanari anti-halusinasi: DB perekam dibuka HANYA baca (mode=ro), tidak pernah ditulis.

    Kalau tes ini gagal, RYO mengeluarkan nilai baru — panggil `@agent-api-verifier` untuk
    menambah barisnya dengan hitungan + tanggal, JANGAN memetakannya ke tetangga terdekat.
    """
    paths = {
        "analyze_token": [
            (("verdict",), ANALYZE_TOKEN_VERDICT),
            (("technical_analysis", "trend"), P.TECHNICAL_TREND),
        ],
        "deep_analysis": [
            (("verdict", "call"), DEEP_ANALYSIS_VERDICT_CALL),
            (("confluence", "state"), P.CONFLUENCE_STATE),
        ],
        "market_overview": [
            (("regime",), MARKET_REGIME),
            (("sentiment", "label"), P.SENTIMENT_LABEL),
        ],
        "monitor_market_sentiment_shift": [
            (("evidence", "sentiment_regime"), P.SENTIMENT_REGIME),
            (("evidence", "funding", "crowding_state"), P.FUNDING_CROWDING_STATE),
            (("evidence", "liquidation", "pressure_state"), P.LIQUIDATION_PRESSURE_STATE),
            (("evidence", "fear_greed", "change_direction"), P.FEAR_GREED_CHANGE_DIRECTION),
        ],
    }
    paths["compare_tokens"] = [(("tokens", LIST, "verdict"), COMPARE_TOKENS_VERDICT)]
    connection = sqlite3.connect("file:" + quote(LIVE_DB.as_posix(), safe="/") + "?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT tool, payload_json FROM snapshots").fetchall()
    finally:
        connection.close()
    unknown: dict[str, set[str]] = {}
    for tool, payload_json in rows:
        for path, vocab in paths.get(tool, []):
            for value in dig(json.loads(payload_json).get("data"), path):
                if vocab.lookup(value) is None:
                    unknown.setdefault(vocab.field, set()).add(value)
    # Nilai enum dipotong sebelum masuk pesan kegagalan: payload RYO tidak boleh membanjiri
    # keluaran pytest (dan tidak boleh membawa CR/LF ke terminal) hanya karena tes gagal.
    trimmed = {
        field: sorted(repr(value)[:60] for value in sorted(values)[:5])
        for field, values in sorted(unknown.items())
    }
    assert not unknown, f"nilai baru di data/ryo.db (panggil api-verifier): {trimmed}"
