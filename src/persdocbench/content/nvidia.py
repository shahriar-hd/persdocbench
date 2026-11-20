"""OpenAI-compatible NVIDIA endpoint client: one adapter, one error taxonomy.

The HTTP call is injectable (``http``), so every status code the provider can
return is exercised in tests without a network. The real call is
``urllib``-based and runs in a worker thread, so the async dispatcher never
blocks on a 60-second generation.

Error taxonomy — the whole point of this module, because these are the failures
that actually happen on a 6,000-request batch:

===================================  ===========================================
condition                            raised
===================================  ===========================================
connect/read timeout, DNS failure    ``Retryable(kind="timeout")``
HTTP 429                             ``Retryable(rate_limited=True, retry_after=…)``
HTTP 500/502/503/504, 408            ``Retryable(kind="server")``
HTTP 401/403                         ``AuthError`` (key quarantined, retry elsewhere)
HTTP 404 / "model not found"         ``ModelUnavailable`` (never retried: the id is wrong)
HTTP 400/422 with "not supported"    ``ModelUnsupported`` (wrong endpoint for this model)
HTTP 400/422 otherwise               ``Fatal`` (bad request)
non-JSON body / no choices           ``Retryable(kind="parse")``
``finish_reason == "length"``        ``Retryable(kind="truncated")``
===================================  ===========================================

``ModelUnavailable`` and ``ModelUnsupported`` subclass ``Fatal`` so the existing
dispatcher gives up on the request; the probe records them separately so a
model catalogue can be triaged without decoding log strings.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from persdocbench.content.dispatcher import AuthError, Fatal, Request, Response, Retryable
from persdocbench.content.keys import Key

# (status, headers, body_bytes) — the only thing tests have to fake.
HttpCall = Callable[
    [str, str, dict[str, str], bytes | None, float], "tuple[int, dict[str, str], bytes]"
]


class ModelUnavailable(Fatal):
    """404 / unknown model: retrying cannot help, the id is wrong or retired."""


class ModelForbidden(Fatal):
    """The key is valid but is not entitled to *this* model.

    Deliberately NOT an ``AuthError``: the key works (other models answer on the
    same key), so quarantining it would kill a batch for a bad model id. Observed
    shapes on the NVIDIA endpoint: ``403: authorization failed`` and
    ``404: function '<uuid>': not found for account '<id>'``.

    ``key_level_suspicion`` tells the dispatcher that this shape is *also* what a
    revoked key returns, so one occurrence must not kill the request but a
    repeated one across different models must retire the key (2026-11-20: one of
    four keys was revoked and, because this was a plain Fatal, every unit routed
    to it died — 26 units lost in an 8-unit smoke run).
    """

    key_level_suspicion = True


class ModelUnsupported(Fatal):
    """The model exists but not on this endpoint (e.g. embeddings, rerank)."""


class ParamRejected(Fatal):
    """A generation parameter is not allowed by this model.

    Observed: ``400 validation: `top_p` is fixed at 0.95 for this model; received
    0.9. omit `top_p` or set it to 0.95``. Recoverable — drop the parameter and
    resend — so the offending name is carried on the exception instead of being
    parsed twice by callers.
    """

    def __init__(self, message: str, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


def _urllib_http(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:  # a real response, just not 2xx
        return exc.code, dict(exc.headers or {}), exc.read() or b""
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise TimeoutError(str(reason)) from exc
        raise ConnectionError(str(reason)) from exc


def as_retryable(exc: BaseException, *, what: str = "") -> BaseException:
    """Map a transport-level failure to the dispatcher's retry taxonomy.

    A read timeout surfaces as a bare ``TimeoutError`` from ``urlopen``/``read``,
    *before* any HTTP status exists, so ``classify()`` never sees it. Observed in
    production on a 1,400-token generation: the exception escaped the transport and
    killed the whole batch instead of cooling the key down and retrying. Connection
    resets and DNS failures behave the same way.
    """
    if isinstance(exc, (Retryable, Fatal)):
        return exc
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return Retryable(f"timeout{': ' + what if what else ''}: {exc}")
    if isinstance(exc, (ConnectionError, urllib.error.URLError, OSError)):
        return Retryable(f"transport{': ' + what if what else ''}: {exc}")
    return exc


def _retry_after(headers: dict[str, str], default: float = 60.0) -> float:
    for k, v in headers.items():
        if k.lower() == "retry-after":
            try:
                return max(1.0, float(v))
            except (TypeError, ValueError):
                return default
    return default


def _error_text(body: bytes, limit: int = 400) -> str:
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body[:limit].decode("utf-8", "replace")
    if isinstance(data, dict):
        for key in ("detail", "message", "error"):
            val = data.get(key)
            if isinstance(val, str):
                return val[:limit]
            if isinstance(val, dict) and isinstance(val.get("message"), str):
                return str(val["message"])[:limit]
    return json.dumps(data)[:limit]


_NOT_FOUND_HINTS = (
    "model not found",
    "unknown model",
    "does not exist",
    "not a valid model",
    "no such model",
)
# "your key is fine, this model is not yours" — must never quarantine the key.
_ENTITLEMENT_HINTS = (
    "authorization failed",
    "not authorized",
    "unauthorized to access",
    "not have access",
    "no access to",
    "not entitled",
    "not enabled for",
    "not found for account",
    "not available for your account",
    "permission denied",
    "insufficient permissions",
)
_UNSUPPORTED_HINTS = (
    "not supported",
    "unsupported",
    "does not support",
    "not available for",
    "not a chat model",
    "invalid model type",
)


_PARAM_HINTS = (
    re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)` is fixed at"),
    re.compile(r"omit `([A-Za-z_][A-Za-z0-9_]*)`"),
    re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)` is not supported"),
    re.compile(r"unrecognized (?:parameter|argument)[: ]+'?([A-Za-z_][A-Za-z0-9_]*)'?"),
)


def _rejected_param(text: str) -> str | None:
    for rx in _PARAM_HINTS:
        m = rx.search(text)
        if m:
            return m.group(1)
    return None


def classify(status: int, headers: dict[str, str], body: bytes) -> Exception | None:
    """Map a non-2xx response to the exception the dispatcher should see."""
    text = _error_text(body).lower()
    if status == 429:
        return Retryable(
            f"429: {text}", retry_after=_retry_after(headers), rate_limited=True
        )
    if status in (401, 403):
        if any(h in text for h in _ENTITLEMENT_HINTS):
            return ModelForbidden(f"{status}: {text}")
        return AuthError(f"{status}: {text}")
    if status == 404 or any(h in text for h in _NOT_FOUND_HINTS):
        return ModelUnavailable(f"{status}: {text}")
    if status in (400, 422) and any(h in text for h in _UNSUPPORTED_HINTS):
        return ModelUnsupported(f"{status}: {text}")
    if status in (400, 422):
        param = _rejected_param(text)
        if param:
            return ParamRejected(f"{status}: {text}", param=param)
        return Fatal(f"{status}: {text}")
    if status in (408, 409, 425, 500, 502, 503, 504):
        return Retryable(
            f"{status}: {text}", retry_after=_retry_after(headers, default=5.0)
        )
    if 500 <= status < 600:
        return Retryable(f"{status}: {text}", retry_after=5.0)
    return Fatal(f"{status}: {text}")


@dataclass
class NvidiaClient:
    """Transport for the dispatcher, plus catalogue discovery."""

    base_url: str = "https://integrate.api.nvidia.com/v1"
    timeout: float = 180.0
    http: HttpCall = field(default=_urllib_http, repr=False)
    last_usage: Usage = field(default_factory=Usage, init=False)

    def _headers(self, key: Key) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {key.secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def list_models(self, key: Key, *, timeout: float = 30.0) -> list[dict[str, object]]:
        url = f"{self.base_url.rstrip('/')}/models"
        try:
            status, headers, body = self.http(
                "GET", url, self._headers(key), None, timeout
            )
        except Exception as exc:
            raise as_retryable(exc, what=f"GET {url}") from exc
        if not 200 <= status < 300:
            err = classify(status, headers, body) or Fatal(str(status))
            raise err
        data = json.loads(body.decode("utf-8", "replace") or "{}")
        items = data.get("data") if isinstance(data, dict) else data
        return list(items or [])

    def complete_sync(
        self,
        *,
        key: Key,
        model: str,
        prompt: str,
        params: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> tuple[str, Usage]:
        params = dict(params or {})
        messages = params.pop("messages", None) or [{"role": "user", "content": prompt}]
        payload: dict[str, object] = {"model": model, "messages": messages}
        for name in (
            "temperature",
            "top_p",
            "max_tokens",
            "stop",
            "seed",
            "presence_penalty",
            "frequency_penalty",
        ):
            if name in params and params[name] is not None:
                payload[name] = params[name]
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        try:
            status, headers, raw = self.http(
                "POST", url, self._headers(key), body, timeout or self.timeout
            )
        except Exception as exc:
            raise as_retryable(exc, what=f"POST {url} model={model}") from exc
        if not 200 <= status < 300:
            err = classify(status, headers, raw) or Fatal(str(status))
            raise err
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise Retryable(f"parse: {exc}") from exc
        choices = (data or {}).get("choices") or []
        if not choices:
            raise Retryable(f"parse: no choices in {str(data)[:200]}")
        choice = choices[0]
        finish = choice.get("finish_reason")
        text = (choice.get("message") or {}).get("content") or ""
        if not isinstance(text, str):
            raise Retryable("parse: non-string content")
        if finish == "length":
            raise Retryable("truncated: finish_reason=length")
        if not text.strip():
            raise Retryable(f"empty completion (finish_reason={finish})")
        u = (data or {}).get("usage") or {}
        self.last_usage = Usage(
            prompt_tokens=int(u.get("prompt_tokens") or 0),
            completion_tokens=int(u.get("completion_tokens") or 0),
            total_tokens=int(u.get("total_tokens") or 0),
        )
        return text, self.last_usage

    async def __call__(
        self, spec: Request, key: Key, timeout: float | None = None
    ) -> Response:
        text, usage = await asyncio.to_thread(
            self.complete_sync,
            key=key,
            model=spec.model,
            prompt=spec.prompt,
            params=spec.params,
            timeout=timeout,
        )
        return Response(text=text, usage=usage.as_dict())
