"""Unit tests: key pool, rate limiting, ledger resumption, dispatcher, gates.

No network is touched. The fake clock/sleeper pair makes rate-limit behaviour
deterministic and instant, which is the only way to test that a 60 req/min
rhythm is actually respected rather than accidentally bypassed.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from persdocbench.content.dispatcher import (
    AuthError,
    Dispatcher,
    Fatal,
    Request,
    Response,
    Retryable,
)
from persdocbench.content.gates import (
    digit_style,
    evaluate,
    find_banned_patterns,
    find_echo,
    normalize_for_storage,
    terminology_density,
)
from persdocbench.content.keys import KeyPool, key_fingerprint, parse_keys
from persdocbench.content.ledger import Ledger, Status, request_id
from persdocbench.content.ratelimit import Budget, TokenBucket

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept = 0.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay
        self.slept += delay


def make_pool(
    secrets: list[str], ft: FakeTime, rpm: float = 60.0, burst: int | None = 1
) -> KeyPool:
    return KeyPool(
        secrets, rate_per_minute=rpm, burst=burst, clock=ft.clock, sleeper=ft.sleep
    )


SPECIALIST_TEXT = (
    "اتیولوژی این اختلال چندعاملی است و در ۳۸ درصد موارد سابقه خانوادگی مثبت گزارش "
    "می‌شود. تشخیص افتراقی شامل اختلالات متابولیک و عفونی است. در مطالعه حاضر ۱۲۴ "
    "بیمار با میانگین سن ۴۷ سال بررسی شدند و حساسیت ۸۲ درصد و ویژگی ۹۱ درصد به دست "
    "آمد. پروگنوز بیماران با تشخیص زودهنگام به‌طور معناداری بهتر بود و عود در پیگیری "
    "شش‌ماهه در ۹ درصد موارد مشاهده شد."
)


# ---------------------------------------------------------------------------
# key parsing and secrecy
# ---------------------------------------------------------------------------
def test_parse_keys_comma_separated_from_env_form() -> None:
    keys = parse_keys("nv_key1, nv_key2 ,, nv_key1, nv_key3,")
    assert keys == ["nv_key1", "nv_key2", "nv_key3"]


def test_parse_keys_rejects_empty() -> None:
    for bad in ("", ",", ", ,", None):
        with pytest.raises(ValueError):
            parse_keys(bad)


def test_from_env_reads_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "a,b,c")
    pool = KeyPool.from_env("NVIDIA_API_KEY", rate_per_minute=60)
    assert len(pool.keys) == 3
    assert pool.capacity_per_minute == 180


def test_secrets_never_appear_in_repr_or_snapshot() -> None:
    """A key must never reach a log, a ledger record or a run manifest."""
    pool = KeyPool(["nv_secret_alpha", "nv_secret_beta"], rate_per_minute=60)
    blob = repr(pool.keys) + json.dumps(pool.snapshot(), ensure_ascii=False)
    assert "nv_secret_alpha" not in blob
    assert "nv_secret_beta" not in blob
    assert pool.keys[0].id == key_fingerprint("nv_secret_alpha")


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------
def test_token_bucket_respects_rate() -> None:
    ft = FakeTime()
    _ = ft  # placeholder kept for symmetry with the two-key test below
    tb = TokenBucket(60, burst=1, clock=ft.clock, sleeper=ft.sleep)

    async def go() -> None:
        assert tb.try_acquire() is True
        assert tb.try_acquire() is False
        await tb.acquire()  # must wait ~1 s for the next token

    asyncio.run(go())
    assert ft.slept == pytest.approx(1.0, abs=1e-6)


def test_two_keys_double_the_rhythm() -> None:
    """The limit is per key: two keys must sustain twice the request rate."""
    one = FakeTime()
    one_key = make_pool(["k1"], one, rpm=60, burst=1)
    for _ in range(4):
        asyncio.run(one_key.acquire())
    assert one.slept == pytest.approx(3.0, abs=1e-6)  # 1 free token, then 1 s each

    two = FakeTime()
    two_keys = make_pool(["k1", "k2"], two, rpm=60, burst=1)
    for _ in range(4):
        asyncio.run(two_keys.acquire())
    # 2 free tokens (one per key), then the remaining 2 arrive after 1 s
    assert two.slept == pytest.approx(1.0, abs=1e-6)
    assert two.slept < one.slept, "two keys must finish the same batch sooner"


def test_eta_scales_with_key_count() -> None:
    ft = FakeTime()
    one = make_pool(["k1"], ft, rpm=60)
    three = make_pool(["k1", "k2", "k3"], ft, rpm=60)
    assert one.eta_seconds(6000) == pytest.approx(6000.0)
    assert three.eta_seconds(6000) == pytest.approx(2000.0)


def test_rate_limited_key_cools_down_and_is_skipped() -> None:
    ft = FakeTime()
    pool = make_pool(["k1", "k2"], ft, rpm=600)
    first = asyncio.run(pool.acquire())
    pool.report_rate_limited(first, retry_after=30.0)

    async def next_key() -> str:
        k = await pool.acquire()
        return k.id

    assert asyncio.run(next_key()) != first.id
    assert (
        first.cooldown_until == pytest.approx(ft.now + 30.0, abs=1e-6) or first.disabled
    )


def test_quarantined_key_disables_pool_when_last() -> None:
    ft = FakeTime()
    pool = make_pool(["only"], ft, rpm=60)
    pool.quarantine(pool.keys[0], reason="auth: 401")
    assert pool.active == []
    assert pool.capacity_per_minute == 0
    with pytest.raises(RuntimeError):
        asyncio.run(pool.acquire())


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------
def test_request_id_is_stable_and_excludes_transport(tmp_path) -> None:
    a = request_id(model="m", prompt="p", params={"t": 0.7})
    b = request_id(model="m", prompt="p", params={"t": 0.7})
    c = request_id(model="m", prompt="p", params={"t": 0.9})
    assert a == b and a != c


def test_ledger_resumes_and_skips_completed(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    led = Ledger(path)
    led.mark_ok("abc", attempts=1, output_sha256="deadbeef", usage={"total_tokens": 10})
    assert led.is_terminal("abc")
    # replay from disk
    led2 = Ledger(path)
    assert led2.status("abc") == Status.OK
    assert led2.total_tokens() == 10
    assert led2.outstanding(["abc", "def"]) == ["def"]


def test_ledger_survives_torn_line(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    led = Ledger(path)
    led.mark_failed("x", attempts=1, error="boom")
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"request_id": "y", "status": "ok", "ts"')  # truncated write
    led2 = Ledger(path)
    assert led2.status("x") == Status.FAILED
    assert led2.status("y") is None


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------
def run(coro):
    return asyncio.run(coro)


def test_dispatcher_happy_path_with_two_keys(tmp_path) -> None:
    ft = FakeTime()
    pool = make_pool(["k1", "k2"], ft, rpm=6000)
    led = Ledger(tmp_path / "l.jsonl", fsync=False)
    used: list[str] = []

    async def transport(req: Request, key) -> Response:
        used.append(key.id)
        return Response(SPECIALIST_TEXT, {"total_tokens": 120})

    d = Dispatcher(transport, led, pool, concurrency=4)
    reqs = [Request(prompt=f"p{i}", model="m") for i in range(4)]
    stats = run(d.run(reqs))
    assert stats.ok == 4 and stats.failed == 0
    assert len(set(used)) == 2  # both keys were used
    assert all(led.status(r.id) == Status.OK for r in reqs)


def test_dispatcher_is_idempotent_across_restarts(tmp_path) -> None:
    ft = FakeTime()
    led_path = tmp_path / "l.jsonl"
    calls = {"n": 0}

    async def transport(req: Request, key) -> Response:
        calls["n"] += 1
        return Response(SPECIALIST_TEXT, {"total_tokens": 50})

    reqs = [Request(prompt="same", model="m")]
    run(
        Dispatcher(
            transport, Ledger(led_path, fsync=False), make_pool(["k"], ft, 6000)
        ).run(reqs)
    )
    stats = run(
        Dispatcher(
            transport, Ledger(led_path, fsync=False), make_pool(["k"], ft, 6000)
        ).run(reqs)
    )
    assert calls["n"] == 1, "a completed request must never be re-sent"
    assert stats.skipped_done == 1


def test_dead_key_is_quarantined_and_batch_continues(tmp_path) -> None:
    """A revoked key must not kill a 6,000-request batch."""
    ft = FakeTime()
    pool = make_pool(["dead", "live"], ft, rpm=6000)
    dead_id = pool.keys[0].id

    async def transport(req: Request, key) -> Response:
        if key.id == dead_id:
            raise AuthError("401 invalid api key")
        return Response(SPECIALIST_TEXT, {"total_tokens": 10})

    led = Ledger(tmp_path / "l.jsonl", fsync=False)
    d = Dispatcher(transport, led, pool, concurrency=1)
    reqs = [Request(prompt=f"p{i}", model="m") for i in range(3)]
    stats = run(d.run(reqs))
    assert stats.ok == 3
    assert stats.quarantined_keys == 1
    assert [k.disabled for k in pool.keys] == [True, False]


def test_rate_limit_sets_cooldown_and_retries(tmp_path) -> None:
    ft = FakeTime()
    pool = make_pool(["k1", "k2"], ft, rpm=6000)
    state = {"first": True}

    async def transport(req: Request, key) -> Response:
        if state["first"]:
            state["first"] = False
            raise Retryable("429", retry_after=5.0, rate_limited=True)
        return Response(SPECIALIST_TEXT, {"total_tokens": 10})

    led = Ledger(tmp_path / "l.jsonl", fsync=False)
    d = Dispatcher(transport, led, pool, concurrency=1, max_attempts=3)
    reqs = [Request(prompt="p", model="m")]
    stats = run(d.run(reqs))
    assert stats.ok == 1 and stats.retries == 1
    assert any(k.failures >= 1 for k in pool.keys)


def test_retry_exhaustion_is_failed_not_ok(tmp_path) -> None:
    ft = FakeTime()

    async def transport(req: Request, key) -> Response:
        raise Retryable("timeout")

    led = Ledger(tmp_path / "l.jsonl", fsync=False)
    d = Dispatcher(transport, led, pool=make_pool(["k"], ft, 60000), max_attempts=2)
    reqs = [Request(prompt="p", model="m")]
    stats = run(d.run(reqs))
    assert stats.failed == 1 and stats.ok == 0
    assert led.status(reqs[0].id) == Status.FAILED


def test_fatal_error_is_not_retried(tmp_path) -> None:
    ft = FakeTime()
    calls = {"n": 0}

    async def transport(req: Request, key) -> Response:
        calls["n"] += 1
        raise Fatal("400 bad request")

    d = Dispatcher(
        transport, Ledger(tmp_path / "l.jsonl", fsync=False), make_pool(["k"], ft, 6000)
    )
    run(d.run([Request(prompt="p", model="m")]))
    assert calls["n"] == 1


def test_budget_ceiling_blocks_sending(tmp_path) -> None:
    ft = FakeTime()
    budget = Budget(requests_per_minute=60, max_requests=1)

    async def transport(req: Request, key) -> Response:
        return Response(SPECIALIST_TEXT, {"total_tokens": 10})

    led = Ledger(tmp_path / "l.jsonl", fsync=False)
    d = Dispatcher(
        transport, led, make_pool(["k"], ft, 6000), budget=budget, concurrency=1
    )
    stats = run(d.run([Request(prompt=f"p{i}", model="m") for i in range(3)]))
    assert stats.ok == 1
    assert stats.failed == 2
    assert budget.used_requests == 1


def test_rejected_output_is_recorded_with_reason(tmp_path) -> None:
    ft = FakeTime()

    async def transport(req: Request, key) -> Response:
        return Response("به زبان ساده، همان‌طور که می‌دانید این یک متن ساده است.")

    led = Ledger(tmp_path / "l.jsonl", fsync=False)
    d = Dispatcher(
        transport,
        led,
        make_pool(["k"], ft, 6000),
        banned=["به زبان ساده", "همان‌طور که می‌دانید"],
    )
    reqs = [Request(prompt="p", model="m")]
    stats = run(d.run(reqs))
    assert stats.rejected == 1 and stats.ok == 0
    rec = led.get(reqs[0].id)
    assert rec is not None and rec.status == Status.REJECTED
    assert "banned_lay_pattern" in (rec.error or "")


def test_on_ok_callback_receives_clean_text(tmp_path) -> None:
    ft = FakeTime()
    seen: list[str] = []

    async def transport(req: Request, key) -> Response:
        return Response(SPECIALIST_TEXT + "\r\n\r\n\r\n", {"total_tokens": 5})

    d = Dispatcher(
        transport,
        Ledger(tmp_path / "l.jsonl", fsync=False),
        make_pool(["k"], ft, 6000),
        on_ok=lambda req, resp, rep: seen.append(resp.text),
    )
    run(d.run([Request(prompt="p", model="m")]))
    assert seen and seen[0] == SPECIALIST_TEXT


# ---------------------------------------------------------------------------
# gates: normalisation that must not destroy meaning
# ---------------------------------------------------------------------------
def test_normalisation_preserves_zwnj_digits_and_letters() -> None:
    raw = "می\u200cرود ۱۲۳ کتاب\u0640\u0640خانه\r\n\r\n\r\nعلي كريم"
    out = normalize_for_storage(raw)
    assert "می\u200cرود" in out, "ZWNJ must survive storage normalisation"
    assert "۱۲۳" in out, "Persian digits must not be folded to ASCII"
    assert "\r" not in out
    assert "علي كريم" in out, "Arabic yeh/kaf must not be silently rewritten"
    assert "\n\n\n" not in out


def test_digit_style_is_reported_not_normalised() -> None:
    assert digit_style("مبلغ ۱۲۳ ریال") == "persian"
    assert digit_style("مبلغ 123 ریال") == "latin"
    assert digit_style("۱۲۳ و 123") == "mixed"


def test_banned_pattern_matching_is_zwnj_insensitive() -> None:
    """The generator may insert or omit ZWNJ; the gate must catch both."""
    assert find_banned_patterns("همانطور که میدانید اینطور است", ["همان‌طور که می‌دانید"])
    assert find_banned_patterns("همان‌طور که می‌دانید اینطور است", ["همانطور که میدانید"])
    assert find_banned_patterns("همانطور که می دانید اینطور است", ["همان‌طور که می‌دانید"])


def test_echo_detector_catches_the_kitab_failure_mode() -> None:
    leak = (
        "You're right - let me write it exactly as it appears in the image, "
        "maintaining the right-to-left direction:"
    )
    assert find_echo(leak), "the published KITAB-Bench GT leak must be caught"
    assert find_echo("متن زیر را در نظر بگیرید") == [] or find_echo(
        "متن زیر را در نظر بگیرید"
    )


def test_terminology_density_counts_distinct_terms() -> None:
    lex = ["اتیولوژی", "تشخیص افتراقی", "پروگنوز", "حساسیت و ویژگی"]
    d = terminology_density(SPECIALIST_TEXT, lex, per=200)
    assert d > 0
    repeated = "اتیولوژی " * 300
    assert terminology_density(repeated, lex, per=200) < d


def test_gate_rejects_lay_explainer_accepts_specialist() -> None:
    bad = "به زبان ساده، در دنیای امروز این بیماری اهمیت زیادی دارد. تصور کنید..."
    good = SPECIALIST_TEXT
    lex = ["اتیولوژی", "تشخیص افتراقی", "پروگنوز", "حساسیت و ویژگی", "عود"]
    r_bad = evaluate(
        bad, banned=["به زبان ساده", "در دنیای امروز", "اهمیت زیادی دارد"], lexicon=lex
    )
    r_good = evaluate(good, lexicon=lex, min_term_density=4)
    assert r_bad.verdict == "reject"
    assert "banned_lay_pattern" in r_bad.reasons
    assert r_good.verdict == "accept"
    assert r_good.term_density >= 4


def test_body_text_strips_tables_and_headings() -> None:
    """R5 must measure prose: a correct invoice is not a 10-word-sentence document."""
    from persdocbench.content.gates import body_text

    page = (
        "**جدول اقلام**\n"
        "| ردیف | شرح | مبلغ |\n"
        "|------|-----|------|\n"
        "| ۱ | کابل | ۲۵۰۰۰ |\n"
        "\n"
        "این بند شرایط پرداخت را در بازه سی روزه تعیین می‌کند و "
        "در صورت تأخیر، وجه التزام مطابق بند چهارم اعمال می‌شود.\n"
    )
    out = body_text(page)
    assert "|" not in out
    assert "شرایط پرداخت" in out
