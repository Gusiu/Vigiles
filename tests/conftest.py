"""Assign test-level markers from the directory a test lives in."""

from pathlib import Path

import pytest

_LEVELS = ("unit", "component", "e2e", "chaos")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        parts = Path(item.fspath).parts
        for level in _LEVELS:
            if level in parts:
                item.add_marker(getattr(pytest.mark, level))
