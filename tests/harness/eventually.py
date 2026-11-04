"""Assertions for state that becomes consistent asynchronously.

Never ``sleep(n)`` and hope: poll the condition until it holds or the
timeout expires, then fail with the last assertion error.
"""

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def eventually(fn: Callable[[], T], timeout: float = 60, interval: float = 0.5) -> T:
    deadline = time.monotonic() + timeout
    while True:
        try:
            return fn()
        except AssertionError:
            if time.monotonic() >= deadline:
                raise
        time.sleep(interval)
