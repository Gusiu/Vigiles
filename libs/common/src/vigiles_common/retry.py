"""Retry with exponential backoff and full jitter.

One policy for every outgoing call (outbox relays, sync pushes, webhooks):

* delay = ``random(0, min(cap, base * 2**attempt))``; a longer
  ``Retry-After`` from the server wins,
* retried: connection errors, timeouts, 5xx, 429, and 401 once after a
  token refresh,
* not retried: other 4xx (400, 403, 404, 409, 422) - these go straight to
  the dead letter channel,
* after ``max_attempts`` the message is dead and can be replayed by hand.

Clock, sleep and random source are injected so tests are deterministic.
"""

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import TypeVar

T = TypeVar("T")


class Decision(Enum):
    RETRY = "retry"
    REFRESH_AND_RETRY = "refresh_and_retry"
    GIVE_UP = "give_up"


@dataclass(frozen=True)
class RetryPolicy:
    base_s: float = 1.0
    cap_s: float = 30.0
    max_attempts: int = 15

    def backoff(self, attempt: int, rng: random.Random | None = None) -> float:
        """Full-jitter delay before retry number ``attempt`` (0-based)."""
        rng = rng or random.Random()
        ceiling = min(self.cap_s, self.base_s * 2**attempt)
        return rng.uniform(0, ceiling)

    def delay(
        self,
        attempt: int,
        retry_after_s: float | None = None,
        rng: random.Random | None = None,
    ) -> float:
        jittered = self.backoff(attempt, rng)
        if retry_after_s is not None and retry_after_s > jittered:
            return retry_after_s
        return jittered

    def exhausted(self, attempts_made: int) -> bool:
        return attempts_made >= self.max_attempts


def classify_status(status: int, refreshed_token: bool = False) -> Decision:
    if status == 401:
        return Decision.GIVE_UP if refreshed_token else Decision.REFRESH_AND_RETRY
    if status == 429 or 500 <= status <= 599:
        return Decision.RETRY
    return Decision.GIVE_UP


def classify_exception(exc: BaseException) -> Decision:
    """Network-level failures are retryable; anything else is a bug or bad input."""
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return Decision.RETRY
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is optional for this module
        return Decision.GIVE_UP
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return Decision.RETRY
    return Decision.GIVE_UP


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Parse ``Retry-After`` given as seconds or as an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    now = now or datetime.now(UTC)
    return max(0.0, (when - now).total_seconds())


class RetryError(Exception):
    def __init__(self, attempts: int, last: BaseException) -> None:
        super().__init__(f"gave up after {attempts} attempt(s): {last}")
        self.attempts = attempts
        self.last = last


def call_with_retry(
    fn: Callable[[], T],
    policy: RetryPolicy = RetryPolicy(),
    classify: Callable[[BaseException], Decision] = classify_exception,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    on_refresh: Callable[[], None] | None = None,
) -> T:
    """Run ``fn`` in-process until it succeeds, gives up or runs out of attempts.

    Meant for short synchronous calls. Durable work (outbox rows) stores the
    attempt counter in the database and uses ``RetryPolicy.delay`` directly.
    """
    attempts = 0
    refreshed = False
    while True:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - classification decides
            attempts += 1
            decision = classify(exc)
            if decision is Decision.REFRESH_AND_RETRY:
                if refreshed or on_refresh is None:
                    raise RetryError(attempts, exc) from exc
                on_refresh()
                refreshed = True
                continue
            if decision is Decision.GIVE_UP or policy.exhausted(attempts):
                raise RetryError(attempts, exc) from exc
            sleep(policy.delay(attempts - 1, getattr(exc, "retry_after_s", None), rng))
