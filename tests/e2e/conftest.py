import pytest

from harness.stack import Stack, local_stack


@pytest.fixture
def stack(tmp_path) -> Stack:
    with local_stack(tmp_path) as running:
        yield running


@pytest.fixture
def full_stack(tmp_path) -> Stack:
    """Everything: broker, two hub replicas and the gateway as well."""
    with local_stack(tmp_path, messaging=True) as running:
        running.drop("zestawienie_B1.csv")
        from harness.eventually import eventually

        def imported() -> None:
            assert running.registry_sql("SELECT count(*) FROM assets") == [(11,)]

        eventually(imported, timeout=30)
        running.drop("site-config.json")

        def catalogued() -> None:
            assert running.registry_sql("SELECT count(*) FROM devices") == [(3,)]

        eventually(catalogued, timeout=30)
        yield running
