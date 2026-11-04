import random
from datetime import UTC, datetime

import httpx
import pytest

from vigiles_common.retry import (
    Decision,
    RetryError,
    RetryPolicy,
    call_with_retry,
    classify_exception,
    classify_status,
    parse_retry_after,
)


class MaxRandom(random.Random):
    """Always returns the upper bound, exposing the jitter ceiling."""

    def uniform(self, a: float, b: float) -> float:
        return b


def test_backoff_ceiling_grows_exponentially_up_to_cap() -> None:
    policy = RetryPolicy(base_s=1, cap_s=30)
    ceilings = [policy.backoff(n, MaxRandom()) for n in range(7)]

    assert ceilings == [1, 2, 4, 8, 16, 30, 30]


def test_backoff_is_full_jitter_within_range() -> None:
    policy = RetryPolicy(base_s=1, cap_s=30)
    rng = random.Random(7)
    delays = [policy.backoff(4, rng) for _ in range(500)]

    assert all(0 <= d <= 16 for d in delays)
    assert min(delays) < 2 and max(delays) > 14


def test_longer_retry_after_wins() -> None:
    policy = RetryPolicy(base_s=1, cap_s=30)

    assert policy.delay(0, retry_after_s=20, rng=MaxRandom()) == 20
    assert policy.delay(5, retry_after_s=3, rng=MaxRandom()) == 30


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (500, Decision.RETRY),
        (503, Decision.RETRY),
        (429, Decision.RETRY),
        (401, Decision.REFRESH_AND_RETRY),
        (400, Decision.GIVE_UP),
        (403, Decision.GIVE_UP),
        (404, Decision.GIVE_UP),
        (409, Decision.GIVE_UP),
        (422, Decision.GIVE_UP),
    ],
)
def test_status_classification(status: int, expected: Decision) -> None:
    assert classify_status(status) is expected


def test_401_is_retried_only_once() -> None:
    assert classify_status(401, refreshed_token=True) is Decision.GIVE_UP


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ConnectionRefusedError(), Decision.RETRY),
        (TimeoutError(), Decision.RETRY),
        (httpx.ConnectError("refused"), Decision.RETRY),
        (httpx.ReadTimeout("slow"), Decision.RETRY),
        (ValueError("bad payload"), Decision.GIVE_UP),
    ],
)
def test_exception_classification(exc: BaseException, expected: Decision) -> None:
    assert classify_exception(exc) is expected


def test_parse_retry_after() -> None:
    now = datetime(2026, 11, 4, 10, 0, 0, tzinfo=UTC)

    assert parse_retry_after("12") == 12
    assert parse_retry_after("Wed, 04 Nov 2026 10:00:30 GMT", now) == 30
    assert parse_retry_after("Wed, 04 Nov 2026 09:00:00 GMT", now) == 0
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None


def test_call_with_retry_succeeds_after_transient_failures() -> None:
    sleeps: list[float] = []
    outcomes = iter([ConnectionError(), TimeoutError(), "done"])

    def fn() -> str:
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    result = call_with_retry(fn, sleep=sleeps.append, rng=MaxRandom())

    assert result == "done"
    assert sleeps == [1, 2]


def test_call_with_retry_gives_up_on_permanent_error() -> None:
    sleeps: list[float] = []

    def fn() -> None:
        raise ValueError("invalid")

    with pytest.raises(RetryError) as info:
        call_with_retry(fn, sleep=sleeps.append)

    assert info.value.attempts == 1
    assert sleeps == []


def test_call_with_retry_stops_after_max_attempts() -> None:
    calls = 0

    def fn() -> None:
        nonlocal calls
        calls += 1
        raise ConnectionError()

    with pytest.raises(RetryError):
        call_with_retry(fn, policy=RetryPolicy(max_attempts=4), sleep=lambda _: None)

    assert calls == 4


def test_call_with_retry_refreshes_token_once() -> None:
    refreshes = 0

    class Unauthorized(Exception):
        pass

    def classify(exc: BaseException) -> Decision:
        return Decision.REFRESH_AND_RETRY if isinstance(exc, Unauthorized) else Decision.GIVE_UP

    def refresh() -> None:
        nonlocal refreshes
        refreshes += 1

    def fn() -> None:
        raise Unauthorized()

    with pytest.raises(RetryError) as info:
        call_with_retry(fn, classify=classify, on_refresh=refresh, sleep=lambda _: None)

    assert refreshes == 1
    assert info.value.attempts == 2
