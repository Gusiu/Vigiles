from datetime import UTC, datetime

import pytest

from vigiles_common.envelope import Envelope
from vigiles_hub.ingress.router import NoRoute, route


def _env(type_: str) -> Envelope:
    return Envelope.model_construct(message_id="m", correlation_id="m", type=type_, site="B1", zone="Z3",
                                    device_id="d", vendor="alfa", occurred_at=datetime.now(UTC), payload={})


@pytest.mark.parametrize(
    ("type_", "topic"),
    [("alarm", "vigiles/events/alarm/B1/Z3"), ("fault", "vigiles/events/fault/B1/Z3"),
     ("telemetry", "vigiles/events/telemetry/B1/Z3")],
)
def test_routes_by_type(type_: str, topic: str) -> None:
    assert route(_env(type_)) == topic


def test_unknown_type_has_no_route() -> None:
    with pytest.raises(NoRoute):
        route(_env("party"))
