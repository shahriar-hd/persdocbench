"""Unit tests for the .env reader, the NVIDIA error taxonomy and the probe scoring."""

from __future__ import annotations

import json
import urllib.error

import pytest

from persdocbench.content.dispatcher import AuthError, Fatal, Retryable
from persdocbench.content.dotenv import EnvFileError, load_dotenv, parse_dotenv
from persdocbench.content.keys import Key, KeyPool
from persdocbench.content.nvidia import (
    ModelForbidden,
    ModelUnavailable,
    ModelUnsupported,
    NvidiaClient,
    ParamRejected,
    classify,
)
from persdocbench.content.probe import (
    ModelSummary,
    ProbeRow,
    persian_ratio,
    rank,
    summarise,
    to_markdown,
)
from persdocbench.content.ratelimit import TokenBucket


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------
def test_parse_dotenv_quotes_comments_export() -> None:
    text = """
    # comment
    NVIDIA_API_KEY="nv_a,nv_b"
    EXPORTED=value   # trailing comment
    export QUOTED='single'
    SPACED=a b c

    EMPTY=
    """
    got = parse_dotenv(text)
    assert got["NVIDIA_API_KEY"] == "nv_a,nv_b"
    assert got["EXPORTED"] == "value"
    assert got["QUOTED"] == "single"
    assert got["SPACED"] == "a b c"
    assert got["EMPTY"] == ""


def test_parse_dotenv_rejects_malformed_line() -> None:
    with pytest.raises(EnvFileError):
        parse_dotenv("GOOD=1\nnot-a-pair\n")


def test_load_dotenv_does_not_override_real_env(tmp_path, monkeypatch) -> None:
    f = tmp_path / ".env"
    f.write_text("A=from_file\nB=from_file\n", encoding="utf-8")
    monkeypatch.setenv("A", "from_process")
    applied = load_dotenv(f)
    import os

    assert os.environ["A"] == "from_process", "process env must win over the file"
    assert os.environ["B"] == "from_file"
    assert applied == {"B": "from_file"}


def test_load_dotenv_missing_file_is_not_an_error(tmp_path) -> None:
    assert load_dotenv(tmp_path / "nope.env") == {}


# ---------------------------------------------------------------------------
# error taxonomy
# ---------------------------------------------------------------------------
def body(msg: str) -> bytes:
    return json.dumps({"detail": msg}).encode()


@pytest.mark.parametrize(
    ("status", "headers", "payload", "kind"),
    [
        (429, {"Retry-After": "17"}, body("slow down"), Retryable),
        (401, {}, body("invalid api key"), AuthError),
        (403, {}, body("forbidden"), AuthError),
        (404, {}, body("model not found"), ModelUnavailable),
        (400, {}, body("this model is not supported on this endpoint"), ModelUnsupported),
        (400, {}, body("bad temperature"), Fatal),
        (422, {}, body("unprocessable"), Fatal),
        (500, {}, body("boom"), Retryable),
        (503, {}, body("unavailable"), Retryable),
        (408, {}, body("timeout"), Retryable),
        (418, {}, body("teapot"), Fatal),
    ],
)
def test_classify_maps_status_to_exception(status, headers, payload, kind) -> None:
    err = classify(status, headers, payload)
    assert isinstance(err, kind)
    if isinstance(err, Retryable) and status == 429:
        assert err.rate_limited is True
        assert err.retry_after == 17.0


def test_model_entitlement_403_is_not_an_auth_error() -> None:
    """A valid key that is not entitled to one model must not be quarantined."""
    for payload in (
        body("authorization failed"),
        body("You are not authorized to access this model"),
        body("function 'abc': not found for account 'xyz'"),
        body("permission denied for this model"),
    ):
        err = classify(403, {}, payload)
        assert isinstance(err, ModelForbidden), payload
        assert not isinstance(err, AuthError)


def test_invalid_key_is_still_an_auth_error() -> None:
    for payload in (
        body("invalid api key"),
        body("authentication failed"),
        body("unauthorized"),
    ):
        assert isinstance(classify(401, {}, payload), AuthError)


def test_forbidden_model_is_fatal_so_the_request_is_dropped() -> None:
    c = client_returning(403, body("authorization failed"))
    with pytest.raises(Fatal) as ei:
        c.complete_sync(key=make_key(), model="m", prompt="p")
    assert isinstance(ei.value, ModelForbidden)
    assert not isinstance(ei.value, AuthError)


def test_classify_unknown_model_by_message_even_on_400() -> None:
    err = classify(400, {}, body("The model `foo` does not exist"))
    assert isinstance(err, ModelUnavailable)


def test_classify_retry_after_missing_defaults() -> None:
    err = classify(429, {}, body("rate limited"))
    assert isinstance(err, Retryable) and err.retry_after == 60.0


# ---------------------------------------------------------------------------
# client behaviour with a fake HTTP layer
# ---------------------------------------------------------------------------
def make_key() -> Key:
    return Key(secret="nv_fake", bucket=TokenBucket(600, burst=10))


def client_returning(
    status: int, payload: object, headers: dict[str, str] | None = None
) -> NvidiaClient:
    def http(method, url, hdrs, data, timeout):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, headers or {}, raw

    return NvidiaClient(http=http)


def ok_payload(text: str, finish: str = "stop") -> dict:
    return {
        "choices": [{"message": {"content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def test_complete_sync_returns_text_and_usage() -> None:
    c = client_returning(200, ok_payload("متن فارسی تخصصی"))
    text, usage = c.complete_sync(key=make_key(), model="m", prompt="p")
    assert text == "متن فارسی تخصصی"
    assert usage.completion_tokens == 20 and usage.total_tokens == 30


def test_truncated_completion_is_retryable() -> None:
    c = client_returning(200, ok_payload("نیمه", finish="length"))
    with pytest.raises(Retryable) as ei:
        c.complete_sync(key=make_key(), model="m", prompt="p")
    assert "truncated" in str(ei.value)


def test_empty_content_is_retryable_not_ok() -> None:
    c = client_returning(200, ok_payload("   "))
    with pytest.raises(Retryable):
        c.complete_sync(key=make_key(), model="m", prompt="p")


def test_non_json_body_is_retryable() -> None:
    c = client_returning(200, b"<html>gateway</html>")
    with pytest.raises(Retryable):
        c.complete_sync(key=make_key(), model="m", prompt="p")


def test_no_choices_is_retryable() -> None:
    c = client_returning(200, {"choices": []})
    with pytest.raises(Retryable):
        c.complete_sync(key=make_key(), model="m", prompt="p")


def test_429_on_completion_is_rate_limited() -> None:
    c = client_returning(429, body("too many requests"), {"retry-after": "3"})
    with pytest.raises(Retryable) as ei:
        c.complete_sync(key=make_key(), model="m", prompt="p")
    assert ei.value.rate_limited and ei.value.retry_after == 3.0


def test_list_models_parses_catalogue() -> None:
    c = client_returning(200, {"data": [{"id": "a/b"}, {"id": "c/d"}]})
    got = c.list_models(make_key())
    assert [m["id"] for m in got] == ["a/b", "c/d"]


def test_client_never_holds_or_prints_the_key() -> None:
    c = NvidiaClient(http=lambda *a, **k: (200, {}, b"{}"))
    assert "nv_fake" not in repr(c)


def test_auth_error_surfaces_as_auth_error_not_generic_fatal() -> None:
    c = client_returning(401, body("invalid api key"))
    with pytest.raises(AuthError):
        c.complete_sync(key=make_key(), model="m", prompt="p")


def test_pool_snapshot_has_no_secrets_after_client_use() -> None:
    pool = KeyPool(["nv_secret_one", "nv_secret_two"], rate_per_minute=600, burst=5)
    c = client_returning(200, ok_payload("x"))
    c.complete_sync(key=pool.keys[0], model="m", prompt="p")
    blob = json.dumps(pool.snapshot())
    assert "nv_secret_one" not in blob and "nv_secret_two" not in blob


# ---------------------------------------------------------------------------
# probe scoring
# ---------------------------------------------------------------------------
def test_persian_ratio_separates_languages() -> None:
    fa = "اتیولوژی نارسایی مزمن قلب در ۳۸ درصد موارد سابقه خانوادگی مثبت است."
    en = "Chronic heart failure aetiology is multifactorial in most cases."
    assert persian_ratio(fa) > 0.95
    assert persian_ratio(en) < 0.05


def test_rank_prefers_answering_persian_and_fast() -> None:
    rows = [
        ProbeRow(
            model="good",
            probe="p",
            ok=True,
            latency_s=1.0,
            tok_per_s=80,
            persian_ratio=0.98,
            gate_verdict="accept",
        ),
        ProbeRow(
            model="slow_english",
            probe="p",
            ok=True,
            latency_s=40.0,
            tok_per_s=2,
            persian_ratio=0.1,
            gate_verdict="reject",
        ),
        ProbeRow(
            model="half_down",
            probe="p",
            ok=True,
            latency_s=2.0,
            tok_per_s=60,
            persian_ratio=0.9,
            gate_verdict="accept",
        ),
        ProbeRow(
            model="half_down", probe="q", ok=False, error_class="timeout", error="t"
        ),
        ProbeRow(
            model="dead", probe="p", ok=False, error_class="ModelUnavailable", error="404"
        ),
    ]
    ranked, dead = rank(summarise(rows))
    assert next(s.model for s in ranked) == "good"
    assert [s.model for s in ranked][-1] == "slow_english"
    assert [s.model for s in dead] == ["dead"]
    assert all(s.score > 0 for s in ranked)


def test_summary_reports_units_and_gate_pass_rate() -> None:
    rows = [
        ProbeRow(
            model="m",
            probe="p",
            ok=True,
            latency_s=2.0,
            tok_per_s=50,
            persian_ratio=1.0,
            gate_verdict="accept",
        ),
        ProbeRow(
            model="m",
            probe="q",
            ok=True,
            latency_s=4.0,
            tok_per_s=70,
            persian_ratio=0.8,
            gate_verdict="reject",
            gate_reasons=("banned_lay_pattern",),
        ),
    ]
    (s,) = summarise(rows)
    assert s.probes == 2 and s.answered == 2
    assert s.median_latency_s == pytest.approx(3.0)
    assert s.gate_pass_rate == pytest.approx(0.5)
    d = s.as_dict()
    assert d["median_tok_per_s"] == pytest.approx(60.0)


def test_markdown_lists_ranked_and_dead() -> None:
    ranked = [ModelSummary("a/b", 2, 2, 1.0, 1.0, 50.0, 0.99, 1.0, 0.0, score=0.9)]
    dead = [
        ModelSummary(
            "c/d", 2, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, error_classes=["ModelUnavailable"]
        )
    ]
    md = to_markdown(ranked, dead, probe_count=2)
    assert "`a/b`" in md and "`c/d`" in md
    assert "Did not answer" in md
    assert "not comparable" in md


def test_fixed_parameter_error_carries_the_param_name() -> None:
    payload = body(
        "validation: `top_p` is fixed at 0.95 for this model; received 0.9. omit `top_p` or set it to 0.95"
    )
    err = classify(400, {}, payload)
    assert isinstance(err, ParamRejected)
    assert err.param == "top_p"
    assert isinstance(err, Fatal)


def test_unknown_parameter_error_is_plain_fatal() -> None:
    err = classify(400, {}, body("bad request: malformed json"))
    assert isinstance(err, Fatal) and not isinstance(err, ParamRejected)


# ---------------------------------------------------------------------------
# transport-level failures must reach the dispatcher's retry taxonomy
# ---------------------------------------------------------------------------
def test_read_timeout_is_retryable_not_fatal() -> None:
    """Regression: a bare TimeoutError from urlopen escaped and killed a whole batch."""
    from persdocbench.content.dispatcher import Retryable
    from persdocbench.content.nvidia import as_retryable

    mapped = as_retryable(TimeoutError("The read operation timed out"), what="POST x")
    assert isinstance(mapped, Retryable)
    assert "timeout" in str(mapped)


def test_connection_reset_is_retryable() -> None:
    from persdocbench.content.dispatcher import Retryable
    from persdocbench.content.nvidia import as_retryable

    assert isinstance(as_retryable(ConnectionResetError("reset")), Retryable)
    assert isinstance(as_retryable(urllib.error.URLError("dns")), Retryable)


def test_existing_taxonomy_exceptions_pass_through() -> None:
    from persdocbench.content.dispatcher import Fatal, Retryable
    from persdocbench.content.nvidia import as_retryable

    already = Retryable("429")
    assert as_retryable(already) is already
    fatal = Fatal("nope")
    assert as_retryable(fatal) is fatal


def test_client_maps_timeout_from_injected_http() -> None:
    """The mapping must sit in the client, not only in the helper."""
    from persdocbench.content.dispatcher import Retryable
    from persdocbench.content.nvidia import NvidiaClient

    def boom(*_a, **_k):
        raise TimeoutError("The read operation timed out")

    client = NvidiaClient(http=boom)
    key = KeyPool(["x" * 70]).keys[0]
    try:
        client.complete_sync(key=key, model="m", prompt="p")
    except Retryable as exc:
        assert "timeout" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("timeout was not mapped to Retryable")
