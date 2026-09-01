"""Tes TASKS 1.1 — core/ryo_client.py.

Semua tes OFFLINE: `RyoClient` selalu memakai `httpx.MockTransport`, `FixtureClient`
membaca `docs/samples/`. Tidak ada jaringan, tidak ada `data/ryo.db`, tidak ada `sleep`
sungguhan (jam & tidur disuntik).
"""

from __future__ import annotations

import base64
import inspect
import json
import logging
import pprint
from pathlib import Path
from typing import Any

import httpx
import pytest

from core.ryo_client import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_S,
    ENV_KEY,
    ENV_URL,
    MAX_BACKOFF_S,
    MAX_BODY_EXCERPT_CHARS,
    MAX_ERROR_CODE_CHARS,
    MAX_RESPONSE_BYTES,
    RATE_LIMIT_PER_MIN,
    TOOLS,
    BaseRyoClient,
    FixtureClient,
    RateLimitInfo,
    RyoClient,
    RyoError,
    RyoHTTPError,
    RyoNetworkError,
    RyoProtocolError,
    TokenBucket,
    _reset_shared_buckets,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "docs" / "samples"
BASE_URL = "https://example.invalid/api/mcp"
LOUD_KEY = "KEYVALUEMUSTNOTLEAK123"


# --- perkakas tes ------------------------------------------------------------------------
class FakeClock:
    """Jam + tidur palsu: tidur hanya memajukan waktu, tes tetap milidetik."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def make_client(
    handler: Any,
    *,
    clock: FakeClock | None = None,
    key: str = LOUD_KEY,
    rate_per_min: int = RATE_LIMIT_PER_MIN,
    **kwargs: Any,
) -> RyoClient:
    """Klien uji: transport palsu, jam palsu, dan bucket TERISOLASI per tes.

    Bucket sengaja disuntik: di produksi bucket dibagi per key (registry modul), dan
    berbagi bucket antar tes akan membocorkan jam palsu satu tes ke tes lain.
    """
    clock = clock or FakeClock()
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    kwargs.setdefault("bucket", TokenBucket(rate_per_min, time_fn=clock.time, sleep_fn=clock.sleep))
    return RyoClient(
        BASE_URL,
        key,
        http_client=http,
        time_fn=clock.time,
        sleep_fn=clock.sleep,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _clean_shared_buckets() -> Any:
    """Registry bucket per-key adalah state modul; kosongkan sebelum & sesudah tiap tes."""
    _reset_shared_buckets()
    yield
    _reset_shared_buckets()


def envelope_response(tool: str, *, status: int = 200, headers: dict[str, str] | None = None):
    """Balasan sukses memakai sampel nyata docs/samples/<tool>.json."""
    body = json.loads((SAMPLES / f"{tool}.json").read_text(encoding="utf-8"))
    return httpx.Response(status, json=body, headers=headers or {})


# --- 1. FixtureClient: 6 tool + health/whoami/catalog -------------------------------------
def test_fixture_client_covers_six_tools() -> None:
    fx = FixtureClient(SAMPLES)
    envelopes = {
        "market_overview": fx.market_overview(),
        "scan_market": fx.scan_market(top_n=10),
        "analyze_token": fx.analyze_token("SOL"),
        "deep_analysis": fx.deep_analysis("SOL", include_perp=True),
        "compare_tokens": fx.compare_tokens(["SOL", "BNB", "ETH"], intent="swing"),
        "monitor_market_sentiment_shift": fx.monitor_market_sentiment_shift(),
    }
    assert set(envelopes) == set(TOOLS)
    for tool, envelope in envelopes.items():
        assert envelope["tool"] == tool
        # bentuk envelope api-facts §A
        for field in ("schema_version", "status", "data_mode", "as_of", "request", "data"):
            assert field in envelope, (tool, field)
        assert envelope["status"] in {"ok", "partial", "unavailable"}


def test_fixture_client_records_arguments_without_none() -> None:
    fx = FixtureClient(SAMPLES)
    fx.analyze_token("SOL")
    fx.scan_market(top_n=10)
    fx.deep_analysis("SOL", include_perp=True)
    fx.compare_tokens(["SOL", "BNB", "ETH"], intent="swing")
    assert fx.calls == [
        ("analyze_token", {"symbol": "SOL"}),
        ("scan_market", {"top_n": 10}),
        ("deep_analysis", {"symbol": "SOL", "include_perp": True}),
        ("compare_tokens", {"symbols": "SOL, BNB, ETH", "intent": "swing"}),
    ]


def test_fixture_client_health_whoami_catalog() -> None:
    fx = FixtureClient(SAMPLES)
    health = fx.health()
    assert health["status"] == "ok" and health["tools"] == 6
    assert health["server"] == "ryo-chan"

    whoami = fx.whoami()
    assert whoami["scopes"] == ["tools:read"]
    assert whoami["rate_limit_per_min"] == RATE_LIMIT_PER_MIN
    assert set(whoami["tools"]) == set(TOOLS)

    catalog = fx.catalog()
    assert {entry["name"] for entry in catalog} == set(TOOLS)


def test_fixture_client_reads_rate_limit_headers() -> None:
    fx = FixtureClient(SAMPLES)
    fx.analyze_token("SOL")
    assert fx.last_rate_limit is not None
    assert fx.last_rate_limit.limit == RATE_LIMIT_PER_MIN
    assert fx.last_rate_limit.remaining is not None


def test_fixture_client_rejects_unknown_tool() -> None:
    with pytest.raises(RyoError):
        FixtureClient(SAMPLES).call("safety_check", {"symbol": "SOL"})


def test_fixture_client_never_touches_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("FixtureClient tidak boleh membuka koneksi")

    monkeypatch.setattr(httpx.Client, "request", boom)
    assert FixtureClient(SAMPLES).market_overview()["status"] == "ok"


# --- 2. Kejujuran envelope (ADR-004) ------------------------------------------------------
def test_partial_and_unavailable_pass_through_untouched() -> None:
    fx = FixtureClient(SAMPLES)
    envelope = fx.deep_analysis("SOL", include_perp=True)
    assert envelope["status"] == "partial"
    # null tetap null — tidak pernah jadi 0/{} (api-facts §A, ADR-004)
    assert envelope["data"]["token_profile"] is None
    assert envelope["data"]["derivatives"] is None
    assert envelope["availability"]["derivatives"] == "unavailable"
    assert envelope["availability"]["token_profile"] == "unavailable"
    assert envelope["warnings"]

    raw = json.loads((SAMPLES / "deep_analysis.json").read_text(encoding="utf-8"))["result"]
    assert envelope == raw  # byte-identik secara struktur: nol normalisasi


def test_http_partial_envelope_returned_as_is() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return envelope_response("deep_analysis")

    with make_client(handler) as client:
        envelope = client.deep_analysis("SOL", include_perp=True)
    assert envelope["status"] == "partial"
    assert envelope["data"]["derivatives"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"tool": "analyze_token"},  # tanpa 'result'
        {"result": "bukan objek"},
        {"result": {"data": {}}},  # tanpa 'status'
        [],
    ],
)
def test_invalid_envelope_is_explicit_failure(body: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with make_client(handler) as client, pytest.raises(RyoProtocolError):
        client.analyze_token("SOL")


def test_non_json_body_is_protocol_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    with make_client(handler) as client, pytest.raises(RyoProtocolError):
        client.market_overview()


# --- 3. Bentuk request: endpoint, body, auth ----------------------------------------------
def test_call_posts_bare_arguments_and_drops_none() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return envelope_response("scan_market")

    with make_client(handler) as client:
        client.scan_market(top_n=10)

    request = seen[0]
    assert str(request.url) == f"{BASE_URL}/tools/scan_market/call"
    assert request.method == "POST"
    assert json.loads(request.content) == {"top_n": 10}
    assert request.headers["authorization"] == f"Bearer {LOUD_KEY}"


@pytest.mark.parametrize(
    ("method", "args", "kwargs", "expected"),
    [
        ("market_overview", (), {}, {}),
        (
            "scan_market",
            (),
            {"chain": "eth", "theme": "ai", "top_n": 5},
            {"chain": "eth", "theme": "ai", "top_n": 5},
        ),
        ("analyze_token", ("SOL",), {"chain": "eth"}, {"symbol": "SOL", "chain": "eth"}),
        ("deep_analysis", ("SOL",), {"include_perp": True}, {"symbol": "SOL", "include_perp": True}),
        ("compare_tokens", ("SOL, BNB",), {"intent": "swing"}, {"symbols": "SOL, BNB", "intent": "swing"}),
        ("monitor_market_sentiment_shift", (), {"time_window": "7d"}, {"time_window": "7d"}),
    ],
)
def test_tool_methods_send_catalog_parameter_names(
    method: str, args: tuple[Any, ...], kwargs: dict[str, Any], expected: dict[str, Any]
) -> None:
    seen: list[dict[str, Any]] = []
    tool = method

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content or b"{}"))
        assert str(request.url).endswith(f"/tools/{tool}/call")
        return envelope_response(tool)

    with make_client(handler) as client:
        getattr(client, method)(*args, **kwargs)
    assert seen == [expected]


def test_method_signatures_match_live_catalog() -> None:
    """Anti-halusinasi: nama parameter hanya yang ada di docs/samples/catalog.json."""
    catalog = {e["name"]: e["inputSchema"] for e in json.loads((SAMPLES / "catalog.json").read_text())}
    assert set(catalog) == set(TOOLS)
    for tool, schema in catalog.items():
        params = set(inspect.signature(getattr(BaseRyoClient, tool)).parameters) - {"self"}
        assert params == set(schema.get("properties", {})), tool
        assert set(schema.get("required", [])) <= params, tool


def test_health_has_no_authorization_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=json.loads((SAMPLES / "health.json").read_text()))

    with make_client(handler) as client:
        assert client.health()["tools"] == 6
    assert str(seen[0].url) == f"{BASE_URL}/health"
    assert "authorization" not in seen[0].headers


def test_whoami_and_catalog_use_auth_and_no_quota() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {LOUD_KEY}"
        name = "whoami" if request.url.path.endswith("/whoami") else "catalog"
        return httpx.Response(200, json=json.loads((SAMPLES / f"{name}.json").read_text()))

    with make_client(handler) as client:
        assert client.whoami()["rate_limit_per_min"] == RATE_LIMIT_PER_MIN
        assert len(client.catalog()) == 6
        # endpoint tanpa kuota tidak mengambil token bucket
        assert client._bucket.tokens == pytest.approx(float(RATE_LIMIT_PER_MIN))


def test_rate_limit_headers_are_remembered() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return envelope_response(
            "market_overview",
            headers={
                "x-ratelimit-limit": "60",
                "x-ratelimit-remaining": "58",
                "x-ratelimit-reset": "1788234492",
            },
        )

    with make_client(handler) as client:
        assert client.last_rate_limit is None
        client.market_overview()
        assert client.last_rate_limit == RateLimitInfo(limit=60, remaining=58, reset=1788234492)


# --- 4. Token bucket ----------------------------------------------------------------------
def test_token_bucket_refills_at_configured_rate() -> None:
    clock = FakeClock()
    bucket = TokenBucket(60, time_fn=clock.time, sleep_fn=clock.sleep)
    for _ in range(60):
        assert bucket.take() == 0.0
    assert clock.sleeps == []
    assert bucket.take() == pytest.approx(1.0)
    assert clock.sleeps == [pytest.approx(1.0)]


def test_call_61_in_one_minute_waits() -> None:
    """60 panggilan/menit per key (api-facts §A) — dibuktikan dengan jam palsu."""
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        return envelope_response("market_overview")

    with make_client(handler, clock=clock) as client:
        for _ in range(RATE_LIMIT_PER_MIN):
            client.market_overview()
        assert clock.sleeps == []
        started = clock.t
        client.market_overview()

    assert clock.sleeps == [pytest.approx(1.0)]
    assert clock.t - started == pytest.approx(1.0)


# --- 5. Retry & backoff -------------------------------------------------------------------
def test_429_with_retry_after_backs_off_then_succeeds() -> None:
    clock = FakeClock()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"retry-after": "2"}, json={"code": "rate_limited"})
        return envelope_response("analyze_token")

    with make_client(handler, clock=clock) as client:
        envelope = client.analyze_token("SOL")

    assert envelope["status"] == "ok"
    assert len(attempts) == 2
    assert clock.sleeps == [pytest.approx(2.0)]


def test_503_is_retried_with_exponential_backoff_and_jitter() -> None:
    clock = FakeClock()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) <= 2:
            return httpx.Response(503, json={"code": "unavailable"})
        return envelope_response("market_overview")

    with make_client(handler, clock=clock) as client:
        assert client.market_overview()["status"] == "ok"

    assert len(attempts) == 3
    assert len(clock.sleeps) == 2
    assert 1.0 <= clock.sleeps[0] <= 2.0  # 2**0 + jitter[0,1)
    assert 2.0 <= clock.sleeps[1] <= 3.0  # 2**1 + jitter[0,1)
    assert clock.sleeps[0] < clock.sleeps[1]


def test_retry_after_is_capped_and_falls_back_when_not_numeric() -> None:
    clock = FakeClock()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"retry-after": "99999"}, json={})
        if len(attempts) == 2:
            return httpx.Response(429, headers={"retry-after": "Wed, 01 Sep 2026 04:00:00 GMT"}, json={})
        return envelope_response("analyze_token")

    with make_client(handler, clock=clock) as client:
        client.analyze_token("SOL")

    assert clock.sleeps[0] == pytest.approx(MAX_BACKOFF_S)
    assert 2.0 <= clock.sleeps[1] <= 3.0


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 500])
def test_non_retryable_statuses_are_not_retried(status: int) -> None:
    clock = FakeClock()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(status, json={"code": "bad_request", "message": "invalid symbol"})

    with make_client(handler, clock=clock) as client, pytest.raises(RyoHTTPError) as excinfo:
        client.analyze_token("NOPE")

    assert len(attempts) == 1
    assert clock.sleeps == []
    assert excinfo.value.status_code == status


def test_429_exhausted_raises_http_error_with_trace_id() -> None:
    clock = FakeClock()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(
            429,
            headers={"retry-after": "1", "x-trace-id": "e8fefd4d"},
            json={"code": "rate_limited"},
        )

    with make_client(handler, clock=clock, max_retries=2) as client:
        with pytest.raises(RyoHTTPError) as excinfo:
            client.market_overview()

    assert len(attempts) == 3
    assert clock.sleeps == [pytest.approx(1.0), pytest.approx(1.0)]
    assert excinfo.value.status_code == 429
    assert excinfo.value.trace_id == "e8fefd4d"


def test_network_failure_is_retried_then_succeeds() -> None:
    clock = FakeClock()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return envelope_response("market_overview")

    with make_client(handler, clock=clock) as client:
        assert client.market_overview()["status"] == "ok"
    assert len(attempts) == 2
    assert len(clock.sleeps) == 1


def test_network_failure_exhausted_raises_network_error() -> None:
    clock = FakeClock()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ReadTimeout("read timed out", request=request)

    with make_client(handler, clock=clock) as client, pytest.raises(RyoNetworkError):
        client.analyze_token("SOL")

    assert len(attempts) == DEFAULT_MAX_RETRIES + 1
    assert len(clock.sleeps) == DEFAULT_MAX_RETRIES


# --- 6. Kredensial tidak pernah bocor -----------------------------------------------------
def test_repr_and_str_do_not_contain_key() -> None:
    with make_client(lambda request: envelope_response("market_overview")) as client:
        assert LOUD_KEY not in repr(client)
        assert LOUD_KEY not in str(client)
        assert BASE_URL in repr(client)


def test_key_is_not_plain_in_instance_dict() -> None:
    """`repr(vars(client))` / `pprint` / `--showlocals` tidak boleh memuat kunci."""
    with make_client(lambda request: envelope_response("market_overview")) as client:
        for rendered in (
            repr(vars(client)),
            str(vars(client)),
            pprint.pformat(vars(client)),
            pprint.pformat(client, depth=8),
            repr(client._key),
            str(client._key),
        ):
            assert LOUD_KEY not in rendered
        assert client._key.reveal() == LOUD_KEY  # nilainya tetap bisa dipakai internal


def test_exception_context_does_not_carry_httpx_request() -> None:
    """`from None` saja tidak cukup: `__context__` masih memegang request + header."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {request.headers['authorization']}", request=request)

    with make_client(handler, max_retries=0) as client, pytest.raises(RyoNetworkError) as excinfo:
        client.analyze_token("SOL")

    assert excinfo.value.__context__ is None
    assert excinfo.value.__cause__ is None


def test_http_error_context_is_clear_too() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "BAD_REQUEST", "message": "invalid symbol"})

    with make_client(handler) as client, pytest.raises(RyoHTTPError) as excinfo:
        client.analyze_token("NOPE")

    assert excinfo.value.__context__ is None


def test_http_500_exception_message_hides_key_even_if_server_echoes_it() -> None:
    """Server jahat memantulkan header Authorization di body error → tetap harus teredaksi."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"code": "internal", "message": f"upstream failed for {request.headers['authorization']}"},
            headers={"x-trace-id": "abc123"},
        )

    with make_client(handler) as client, pytest.raises(RyoHTTPError) as excinfo:
        client.analyze_token("SOL")

    message = str(excinfo.value)
    assert LOUD_KEY not in message
    assert "***" in message
    assert "HTTP 500" in message


def _trace_id_of(header_value: str, *, key: str = LOUD_KEY) -> tuple[str | None, str, list[str]]:
    """Jalankan satu error 500 dengan `x-trace-id` tertentu; kembalikan (trace_id, pesan, log)."""
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler_log = Capture()
    logger = logging.getLogger("core.ryo_client")
    logger.addHandler(handler_log)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"code": "INTERNAL", "message": "boom"},
            headers={"x-trace-id": header_value},
        )

    try:
        with make_client(handler, key=key) as client:
            try:
                client.analyze_token("SOL")
            except RyoHTTPError as exc:
                logging.getLogger("core.ryo_client").warning("%s", exc)
                return exc.trace_id, str(exc), records
    finally:
        logger.removeHandler(handler_log)
    raise AssertionError("HTTP 500 harus melempar RyoHTTPError")


def test_trace_id_accepts_only_the_observed_shape() -> None:
    """Allowlist BENTUK: 32 hex huruf kecil seperti keenam sampel docs/samples/*.headers."""
    real = "e8fefd4d5e4c47a5b2d602b2e3027c1c"
    assert _trace_id_of(real)[0] == real
    for rejected in ("Bearer " + LOUD_KEY, "abc", "ZZZZ" * 8, "f" * 5000, "", "  "):
        assert _trace_id_of(rejected)[0] is None, rejected


@pytest.mark.parametrize(
    ("label", "header"),
    [
        ("spasi", " ".join(LOUD_KEY)),
        ("tab", "\t".join(LOUD_KEY)),
        ("persen", "%".join(LOUD_KEY)),
        ("lowercase", LOUD_KEY.lower()),
        ("uppercase", LOUD_KEY.upper()),
        ("base64", base64.b64encode(LOUD_KEY.encode()).decode()),
        ("bearer_lower", f"bearer {LOUD_KEY.lower()}"),
        ("terpotong", LOUD_KEY[:12] + " " + LOUD_KEY[12:]),
    ],
)
def test_trace_id_cannot_be_used_to_smuggle_the_key(label: str, header: str) -> None:
    """Filter yang MENGHAPUS karakter akan merakit ulang kunci — pola positif tidak bisa."""
    trace_id, message, records = _trace_id_of(header)
    haystacks = [trace_id or "", message, *records]
    for text in haystacks:
        assert LOUD_KEY not in text, label
        assert LOUD_KEY.lower() not in text.lower(), label
        # rakitan ulang: buang semua non-alfanumerik lalu cari lagi
        squashed = "".join(ch for ch in text if ch.isalnum())
        assert LOUD_KEY not in squashed, label
        assert LOUD_KEY.lower() not in squashed.lower(), label
        try:
            decoded = base64.b64decode(text.strip(), validate=True).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — bukan base64 sah, tidak ada yang bisa didekode
            decoded = ""
        assert LOUD_KEY not in decoded, label
    assert trace_id is None  # tidak satu pun bentuk di atas menyerupai trace id


def _echo_everywhere(payload: str) -> tuple[str | None, str | None, str]:
    """Server memantulkan `payload` di KETIGA jalur: trace id, `code`, dan body error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"code": payload, "message": payload, "details": None, "trace_id": payload},
            headers={"x-trace-id": payload},
        )

    with make_client(handler) as client, pytest.raises(RyoHTTPError) as excinfo:
        client.analyze_token("SOL")
    return excinfo.value.trace_id, excinfo.value.code, str(excinfo.value)


@pytest.mark.parametrize(
    ("label", "encode"),
    [
        ("verbatim", lambda k: k),
        ("hex", lambda k: k.encode().hex()),
        ("hex_upper", lambda k: k.encode().hex().upper()),
        ("b64", lambda k: base64.b64encode(k.encode()).decode()),
        ("b64_tanpa_padding", lambda k: base64.b64encode(k.encode()).decode().rstrip("=")),
        ("b64_urlsafe", lambda k: base64.urlsafe_b64encode(k.encode()).decode()),
    ],
)
def test_encoded_key_is_redacted_on_all_three_server_text_paths(label: str, encode: Any) -> None:
    """Satu tambalan di `_redact` harus berlaku untuk trace id, `code`, DAN cuplikan body.

    `x-trace-id: KEY.encode().hex()` dulu cocok pola hex dan lolos utuh.
    """
    payload = encode(LOUD_KEY)
    trace_id, code, message = _echo_everywhere(payload)
    for surface, text in (("trace_id", trace_id or ""), ("code", code or ""), ("message", message)):
        assert LOUD_KEY not in text, (label, surface)
        assert payload.lower() not in text.lower(), (label, surface)
        try:
            decoded = bytes.fromhex(text).decode("utf-8", "replace")
        except ValueError:
            decoded = ""
        assert LOUD_KEY not in decoded, (label, surface)


def test_known_limit_encodings_we_do_not_enumerate_still_pass_through() -> None:
    """Batas yang DIAKUI (bukan klaim kekebalan): rot13 tidak diredaksi di jalur mana pun.

    Tes ini sengaja mengunci kenyataan, supaya docstring modul tetap didukung bukti dan
    tidak ada yang menuliskan klaim "kebal terhadap kunci yang di-encode ulang".
    """
    import codecs

    rot13 = codecs.encode(LOUD_KEY, "rot_13")
    trace_id, code, message = _echo_everywhere(rot13)
    assert codecs.decode(code or "", "rot_13") == LOUD_KEY  # lolos di `code`
    assert rot13 in message  # lolos di cuplikan body
    assert trace_id is None  # kebetulan tidak berbentuk trace id — bukan jaminan


def test_trace_id_cannot_inject_log_lines_or_ansi() -> None:
    """Baris log palsu + escape ANSI lewat trace id / body error harus tersaring."""
    poison = "aaa\r\n2026-09-01 FATAL palsu\x1b[2Jbbb"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            content=f'{{"code":"X","message":"{poison}"}}'.encode(),
            headers={"content-type": "application/json", "x-trace-id": poison},
        )

    with make_client(handler) as client, pytest.raises(RyoHTTPError) as excinfo:
        client.market_overview()

    message = str(excinfo.value)
    assert excinfo.value.trace_id is None  # bukan bentuk trace id → dibuang, bukan "dibersihkan"
    assert "trace=" not in message
    for forbidden in ("\n", "\r", "\x1b"):
        assert forbidden not in message, forbidden
    assert all(ch.isprintable() for ch in message)
    assert len(message.splitlines()) == 1


def test_body_excerpt_is_capped_and_printable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b"\x1b[2K" + b"z" * 10_000, headers={"content-type": "text/plain"})

    with make_client(handler) as client, pytest.raises(RyoHTTPError) as excinfo:
        client.analyze_token("SOL")

    body = str(excinfo.value).partition("body=")[2]
    assert len(body) == MAX_BODY_EXCERPT_CHARS
    assert "\x1b" not in body
    assert all(ch.isprintable() for ch in str(excinfo.value))


def test_timeout_exception_message_hides_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        leak = request.headers["authorization"]
        raise httpx.ConnectTimeout(f"timeout while sending {leak}", request=request)

    with make_client(handler) as client, pytest.raises(RyoNetworkError) as excinfo:
        client.market_overview()

    assert LOUD_KEY not in str(excinfo.value)
    assert LOUD_KEY not in repr(excinfo.value)


def test_no_log_record_contains_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"key": LOUD_KEY})
        if len(attempts) == 2:
            raise httpx.ConnectError(f"refused {request.headers['authorization']}", request=request)
        return envelope_response("analyze_token")

    with make_client(handler) as client:
        client.analyze_token("SOL")

    assert caplog.records, "skenario retry harus menghasilkan log"
    rendered = "\n".join(
        [record.getMessage() for record in caplog.records]
        + [logging.Formatter("%(message)s").format(record) for record in caplog.records]
        + [str(record.args) for record in caplog.records]
    )
    assert LOUD_KEY not in rendered


def test_whole_traceback_of_failure_hides_key() -> None:
    """Rantai exception httpx tidak boleh ikut membawa kredensial ke traceback."""
    import traceback

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {request.headers['authorization']}", request=request)

    with make_client(handler, max_retries=0) as client:
        try:
            client.market_overview()
        except RyoNetworkError as exc:
            text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    assert LOUD_KEY not in text


# --- 6b. Validasi tool, envelope, bucket per-key, dan pembungkusan kegagalan ---------------
@pytest.mark.parametrize(
    "tool",
    ["../../../admin/keys", "analyze_token/../../whoami", "safety_check", "", "analyze_token\r\nX"],
)
def test_call_rejects_tool_names_outside_catalog(tool: str) -> None:
    """Klien nyata harus memvalidasi seperti FixtureClient — path traversal ditutup."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return envelope_response("market_overview")

    with make_client(handler) as client, pytest.raises(RyoError) as excinfo:
        client.call(tool, {})

    assert seen == []  # tidak ada request yang dikirim
    assert all(ch.isprintable() for ch in str(excinfo.value))


def test_token_bucket_is_shared_per_key_not_per_instance() -> None:
    """Kuota 60/mnt berlaku per KEY: perekam + skill paralel harus berbagi bucket."""
    clock = FakeClock()
    http = httpx.Client(transport=httpx.MockTransport(lambda request: envelope_response("market_overview")))
    common = {"http_client": http, "time_fn": clock.time, "sleep_fn": clock.sleep}
    first = RyoClient(BASE_URL, LOUD_KEY, **common)
    second = RyoClient(BASE_URL, LOUD_KEY, **common)
    other_key = RyoClient(BASE_URL, "OTHERKEY", **common)

    assert first._bucket is second._bucket
    assert other_key._bucket is not first._bucket

    for _ in range(RATE_LIMIT_PER_MIN):
        first.market_overview()
    assert clock.sleeps == []
    second.market_overview()  # instance kedua, kunci sama → tetap harus menunggu
    assert clock.sleeps == [pytest.approx(1.0)]
    http.close()


def test_shared_bucket_index_never_stores_the_raw_key() -> None:
    from core.ryo_client import _SHARED_BUCKETS

    http = httpx.Client(transport=httpx.MockTransport(lambda request: envelope_response("health")))
    RyoClient(BASE_URL, LOUD_KEY, http_client=http)
    assert _SHARED_BUCKETS
    assert all(LOUD_KEY not in index for index in _SHARED_BUCKETS)
    http.close()


@pytest.mark.parametrize(
    "base_url",
    ["app-ryochan.com/api/mcp", "ftp://app-ryochan.com", "/api/mcp", "https://"],
)
def test_base_url_without_scheme_or_host_is_rejected_at_construction(base_url: str) -> None:
    """`RYO_MCP_URL` tanpa skema dulu meledak sebagai ValueError httpx saat panggilan pertama."""
    with pytest.raises(RyoError):
        RyoClient(base_url, LOUD_KEY)


def test_non_serializable_arguments_become_ryo_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return envelope_response("analyze_token")

    with make_client(handler) as client:
        with pytest.raises(RyoError) as excinfo:
            client.call("analyze_token", {"symbol": {"SOL"}})  # set → tidak JSON-serializable
    assert not isinstance(excinfo.value, RyoNetworkError)
    assert LOUD_KEY not in str(excinfo.value)


def test_unexpected_transport_exception_is_wrapped_and_not_retried() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise RuntimeError(f"transport rusak {request.headers['authorization']}")

    with make_client(handler) as client, pytest.raises(RyoError) as excinfo:
        client.market_overview()

    assert len(attempts) == 1  # bukan kegagalan jaringan → tidak diretry
    assert LOUD_KEY not in str(excinfo.value)
    assert excinfo.value.__context__ is None


@pytest.mark.parametrize(
    "envelope",
    [
        {"schema_version": "1.0", "tool": "analyze_token", "status": "error", "data": {}},
        {"schema_version": "1.0", "tool": "analyze_token", "status": None, "data": {}},
        {"schema_version": "1.0", "tool": "compare_tokens", "status": "ok", "data": {}},
        {"schema_version": "1.0", "status": "ok", "data": {}},
    ],
)
def test_envelope_status_enum_and_tool_attribution_are_validated(envelope: dict[str, Any]) -> None:
    """Status di luar {ok,partial,unavailable} atau tool tidak cocok = kegagalan eksplisit."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tool": "analyze_token", "result": envelope})

    with make_client(handler) as client, pytest.raises(RyoProtocolError):
        client.analyze_token("SOL")


@pytest.mark.parametrize("tool", TOOLS)
def test_real_samples_still_pass_validation(tool: str) -> None:
    """Validasi baru tidak boleh menolak satu pun sampel nyata (anti-regresi ADR-004)."""
    fixture = FixtureClient(SAMPLES)
    envelope = fixture.call(tool, {})
    raw = json.loads((SAMPLES / f"{tool}.json").read_text(encoding="utf-8"))["result"]
    assert envelope == raw


# --- 6b2. Ronde 2: kuota per-key, TLS, pickle, pagar RyoError, isError ---------------------
def test_shared_bucket_ignores_rate_per_min_in_the_index() -> None:
    """Laju berbeda TIDAK boleh melahirkan bucket kedua untuk kunci yang sama."""
    clock = FakeClock()
    http = httpx.Client(transport=httpx.MockTransport(lambda request: envelope_response("market_overview")))
    common = {"http_client": http, "time_fn": clock.time, "sleep_fn": clock.sleep}
    lenient = RyoClient(BASE_URL, LOUD_KEY, rate_per_min=60, **common)
    stricter = RyoClient(BASE_URL, LOUD_KEY, rate_per_min=30, **common)

    assert lenient._bucket is stricter._bucket
    assert lenient._bucket.rate_per_min == 30  # yang paling ketat menang
    # kapasitas turun ke 30 → panggilan ke-31 sudah harus menunggu
    for _ in range(30):
        lenient.market_overview()
    assert clock.sleeps == []
    stricter.market_overview()
    assert clock.sleeps  # bukan 90 panggilan bebas seperti sebelumnya
    http.close()


def test_shared_bucket_is_not_loosened_by_a_later_client() -> None:
    http = httpx.Client(transport=httpx.MockTransport(lambda request: envelope_response("market_overview")))
    strict = RyoClient(BASE_URL, LOUD_KEY, rate_per_min=10, http_client=http)
    loose = RyoClient(BASE_URL, LOUD_KEY, rate_per_min=600, http_client=http)
    assert strict._bucket is loose._bucket
    assert loose._bucket.rate_per_min == 10
    http.close()


def test_from_env_refuses_bucket_injection() -> None:
    """Jalur produksi tidak boleh melewati registry per-key diam-diam."""
    with pytest.raises(RyoError):
        RyoClient.from_env({ENV_URL: BASE_URL, ENV_KEY: LOUD_KEY}, bucket=TokenBucket(60))


@pytest.mark.parametrize(
    "base_url",
    ["http://app-ryochan.com/api/mcp", "HTTP://app-ryochan.com", "ftp://app-ryochan.com"],
)
def test_plain_http_is_rejected(base_url: str) -> None:
    """Bearer key tidak boleh dikirim tanpa TLS (api-facts §A: https://app-ryochan.com/api/mcp)."""
    with pytest.raises(RyoError):
        RyoClient(base_url, LOUD_KEY)


def test_base_url_whitespace_is_stripped() -> None:
    client = RyoClient(f"  {BASE_URL}/  ", LOUD_KEY, bucket=TokenBucket(60))
    try:
        assert client.base_url == BASE_URL
    finally:
        client.close()


def test_secret_refuses_pickle() -> None:
    import pickle

    from core.ryo_client import Secret

    secret = Secret(LOUD_KEY)
    with pytest.raises(TypeError):
        pickle.dumps(secret)
    with pytest.raises(TypeError):
        secret.__reduce__()
    with pytest.raises(TypeError):
        pickle.dumps(make_client(lambda request: envelope_response("health")))


def test_failure_in_injected_sleep_or_bucket_becomes_ryo_error() -> None:
    """Kontrak modul: perekam kehilangan satu komponen, bukan satu siklus."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "1"}, json={})

    def angry_sleep(seconds: float) -> None:
        raise TimeoutError("sleep gagal")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RyoClient(
        BASE_URL,
        LOUD_KEY,
        http_client=http,
        sleep_fn=angry_sleep,
        bucket=TokenBucket(60),
    )
    try:
        with pytest.raises(RyoError) as excinfo:
            client.market_overview()
    finally:
        client.close()
        http.close()
    assert not isinstance(excinfo.value, httpx.HTTPError)
    assert "TimeoutError" in str(excinfo.value)


def test_keyboard_interrupt_is_not_swallowed() -> None:
    """KeyboardInterrupt/SystemExit harus tetap lolos (BaseException)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt

    with make_client(handler) as client, pytest.raises(KeyboardInterrupt):
        client.market_overview()


def test_is_error_envelope_is_a_failure() -> None:
    """api-facts §A: kegagalan tool ada di `result.isError`, bisa datang dengan HTTP 200."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "tool": "analyze_token",
                "result": {
                    "schema_version": "1.0",
                    "tool": "analyze_token",
                    "status": "ok",
                    "isError": True,
                    "data": {},
                },
            },
        )

    with make_client(handler) as client, pytest.raises(RyoProtocolError):
        client.analyze_token("SOL")


# --- 6c. Timeout, kode error, dan batas ukuran --------------------------------------------
def test_timeout_is_sent_per_request_even_with_injected_client() -> None:
    """Klien httpx yang disuntik bisa berdefault 5 dtk; compare_tokens butuh ~42 dtk."""
    seen: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions.get("timeout"))
        return envelope_response("compare_tokens")

    http = httpx.Client(transport=httpx.MockTransport(handler))  # tanpa timeout eksplisit
    client = RyoClient(BASE_URL, LOUD_KEY, http_client=http, bucket=TokenBucket(60))
    try:
        client.compare_tokens("SOL, BNB")
        client.health()
    finally:
        client.close()
        http.close()

    assert len(seen) == 2
    for extension in seen:
        assert extension == {k: DEFAULT_TIMEOUT_S for k in ("connect", "read", "write", "pool")}


def test_error_code_from_body_is_exposed_and_sanitized() -> None:
    """Body error `{code, message, details, trace_id}` (api-facts §A) → RyoHTTPError.code."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "code": "UNAUTHENTICATED",
                "message": "Missing credential.",
                "details": None,
                "trace_id": "abc",
            },
            headers={"www-authenticate": "Bearer"},
        )

    with make_client(handler) as client, pytest.raises(RyoHTTPError) as excinfo:
        client.market_overview()

    assert excinfo.value.code == "UNAUTHENTICATED"
    assert excinfo.value.status_code == 401
    assert "code=UNAUTHENTICATED" in str(excinfo.value)


def test_error_code_that_echoes_key_or_control_chars_is_cleaned() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"code": f"X\n{request.headers['authorization']}"})

    with make_client(handler) as client, pytest.raises(RyoHTTPError) as excinfo:
        client.market_overview()

    code = excinfo.value.code or ""
    assert LOUD_KEY not in code
    assert all(ch.isprintable() for ch in code)
    assert len(code) <= MAX_ERROR_CODE_CHARS


def test_missing_or_non_string_code_is_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"code": 500, "message": "nope"})

    with make_client(handler) as client, pytest.raises(RyoHTTPError) as excinfo:
        client.market_overview()
    assert excinfo.value.code is None


def test_oversized_success_response_is_rejected() -> None:
    payload = b'{"tool":"market_overview","result":{"status":"ok","tool":"market_overview","data":"'
    payload += b"z" * (MAX_RESPONSE_BYTES + 1) + b'"}}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-type": "application/json"})

    with make_client(handler) as client, pytest.raises(RyoProtocolError) as excinfo:
        client.market_overview()
    assert "melebihi batas" in str(excinfo.value)


def test_oversized_error_body_is_not_copied_whole() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"y" * 5_000_000, headers={"content-type": "text/plain"})

    with make_client(handler) as client, pytest.raises(RyoHTTPError) as excinfo:
        client.market_overview()
    assert len(str(excinfo.value)) <= MAX_BODY_EXCERPT_CHARS + 80


# --- 7. Konstruksi & kontrak antarmuka ----------------------------------------------------
def test_from_env_reads_official_names() -> None:
    client = RyoClient.from_env({ENV_URL: f"{BASE_URL}/", ENV_KEY: LOUD_KEY})
    try:
        assert client.base_url == BASE_URL  # trailing slash dibuang
        assert LOUD_KEY not in repr(client)
    finally:
        client.close()


@pytest.mark.parametrize("env", [{}, {ENV_URL: BASE_URL}, {ENV_KEY: LOUD_KEY}])
def test_from_env_without_credentials_fails_loudly(env: dict[str, str]) -> None:
    with pytest.raises(RyoError) as excinfo:
        RyoClient.from_env(env)
    assert LOUD_KEY not in str(excinfo.value)


def test_clients_share_the_same_surface() -> None:
    surface = set(TOOLS) | {"call", "health", "whoami", "catalog"}
    for cls in (RyoClient, FixtureClient):
        assert issubclass(cls, BaseRyoClient)
        for name in surface:
            assert callable(getattr(cls, name)), (cls.__name__, name)
