import pytest

from harness.stack import Stack, local_stack


@pytest.fixture
def stack(tmp_path) -> Stack:
    with local_stack(tmp_path) as running:
        yield running
