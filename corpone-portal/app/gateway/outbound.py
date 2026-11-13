"""Outbound gateway: the single path the portal uses to call external services.

Every call to the network API, billing API, or AI model goes through `gateway.call(...)`, which applies
the upstream's policy:

* rate limit (token bucket; waits for a token rather than failing)
* circuit breaker (opens after N consecutive failures, half-opens after a cool-down)
* retries with exponential backoff + jitter, for errors marked retryable only
* a call log row per attempt (outbound_call_log) and a log line

Retries on network/billing are safe because those calls carry Idempotency-Key headers.
"""
import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

import httpx

from app.config import get_settings
from app.db import SessionLocal
from app.models import OutboundCallLog

log = logging.getLogger(__name__)
T = TypeVar("T")


class GatewayError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable, self.status_code = retryable, status_code


class CircuitOpenError(GatewayError):
    pass


@dataclass
class UpstreamPolicy:
    name: str
    max_retries: int
    backoff_seconds: float
    breaker_threshold: int
    breaker_cooldown_seconds: float
    rate_per_sec: float


class _Breaker:
    def __init__(self, threshold: int, cooldown: float):
        self.threshold, self.cooldown = threshold, cooldown
        self.failures = 0
        self.opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        return time.monotonic() - self.opened_at >= self.cooldown  # half-open: let one through

    def success(self) -> None:
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.allow() else "open"


class _TokenBucket:
    def __init__(self, rate: float):
        self.rate, self.capacity = rate, max(1.0, rate)
        self.tokens, self.updated = self.capacity, time.monotonic()

    def wait_time(self) -> float:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return 0.0
        return (1 - self.tokens) / self.rate


class OutboundGateway:
    def __init__(self, policies: dict[str, UpstreamPolicy]):
        self.policies = policies
        self._breakers = {n: _Breaker(p.breaker_threshold, p.breaker_cooldown_seconds) for n, p in policies.items()}
        self._buckets = {n: _TokenBucket(p.rate_per_sec) for n, p in policies.items()}
        self._lock = threading.Lock()
        self.sleep = time.sleep  # swapped out in tests

    def call(self, upstream: str, operation: str, fn: Callable[[], T]) -> T:
        policy = self.policies[upstream]
        breaker = self._breakers[upstream]
        attempt = 0
        while True:
            attempt += 1
            with self._lock:
                if not breaker.allow():
                    self._record(upstream, operation, attempt, "circuit_open", 0, "circuit open")
                    raise CircuitOpenError(f"{upstream} is unavailable (circuit open)", retryable=True)
                wait = self._buckets[upstream].wait_time()
            if wait:
                self.sleep(wait)
            start = time.monotonic()
            try:
                result = fn()
            except GatewayError as exc:
                latency = int((time.monotonic() - start) * 1000)
                with self._lock:
                    breaker.failure() if exc.retryable else breaker.success()
                will_retry = exc.retryable and attempt <= policy.max_retries
                self._record(upstream, operation, attempt, "retry" if will_retry else "error", latency, str(exc))
                if not will_retry:
                    raise
                self.sleep(policy.backoff_seconds * (2 ** (attempt - 1)) * (0.5 + random.random()))
                continue
            latency = int((time.monotonic() - start) * 1000)
            with self._lock:
                breaker.success()
            self._record(upstream, operation, attempt, "ok", latency, None)
            return result

    def status(self) -> dict:
        with self._lock:
            return {n: {"circuit": b.state, "consecutive_failures": b.failures} for n, b in self._breakers.items()}

    def reset(self) -> None:
        with self._lock:
            for b in self._breakers.values():
                b.success()

    @staticmethod
    def _record(upstream, operation, attempt, outcome, latency_ms, error) -> None:
        level = logging.INFO if outcome == "ok" else logging.WARNING
        log.log(level, "outbound %s.%s attempt=%d outcome=%s latency=%dms %s",
                upstream, operation, attempt, outcome, latency_ms, error or "")
        try:  # best effort: logging must never break the call
            with SessionLocal() as db:
                db.add(OutboundCallLog(upstream=upstream, operation=operation, attempt=attempt,
                                       outcome=outcome, latency_ms=latency_ms, error=(error or None) and error[:2000]))
                db.commit()
        except Exception:
            log.exception("Could not write outbound call log")


def http_post(client: httpx.Client, path: str, payload: dict, idempotency_key: str) -> dict:
    """POST JSON and classify failures for the gateway (5xx/429/transport = retryable)."""
    try:
        resp = client.post(path, json=payload, headers={"Idempotency-Key": idempotency_key})
    except httpx.TransportError as exc:
        raise GatewayError(f"POST {path}: {exc.__class__.__name__}: {exc}", retryable=True) from exc
    if resp.status_code >= 500 or resp.status_code == 429:
        raise GatewayError(f"POST {path}: HTTP {resp.status_code}", retryable=True, status_code=resp.status_code)
    if resp.status_code >= 400:
        raise GatewayError(f"POST {path}: HTTP {resp.status_code} {resp.text[:300]}",
                           retryable=False, status_code=resp.status_code)
    try:
        return resp.json()
    except ValueError as exc:
        raise GatewayError(f"POST {path}: invalid JSON response") from exc


def _build() -> OutboundGateway:
    s = get_settings()
    common = dict(max_retries=s.outbound_max_retries, backoff_seconds=s.outbound_backoff_seconds,
                  breaker_threshold=s.outbound_breaker_threshold,
                  breaker_cooldown_seconds=s.outbound_breaker_cooldown_seconds)
    return OutboundGateway({
        "network": UpstreamPolicy("network", rate_per_sec=s.outbound_rate_per_sec, **common),
        "billing": UpstreamPolicy("billing", rate_per_sec=s.outbound_rate_per_sec, **common),
        # The Anthropic SDK already retries 429/5xx/connection errors, so the gateway doesn't retry AI calls.
        "ai": UpstreamPolicy("ai", rate_per_sec=s.ai_rate_per_sec, **{**common, "max_retries": 0}),
    })


gateway = _build()
