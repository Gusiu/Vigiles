"""Content-Based Router: canonical message -> channel by ``type``.

The rules are a declarative table; a type without a rule goes to the
dead letter channel.
"""

from vigiles_common.envelope import Envelope

ROUTES: dict[str, str] = {
    "alarm": "vigiles/events/alarm/{site}/{zone}",
    "fault": "vigiles/events/fault/{site}/{zone}",
    "telemetry": "vigiles/events/telemetry/{site}/{zone}",
}


class NoRoute(Exception):
    pass


def route(envelope: Envelope) -> str:
    template = ROUTES.get(envelope.type)
    if template is None:
        raise NoRoute(f"no route for type {envelope.type!r}")
    return template.format(site=envelope.site, zone=envelope.zone)
