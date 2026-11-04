import json

import pytest

from vigiles_common.config import ServiceSettings
from vigiles_common.logging import bound_context, configure_logging, get_logger


def _records(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def test_logs_are_json_with_service_and_bound_context(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("importer")
    log = get_logger()

    with bound_context(correlation_id="abc-123"):
        log.info("file_imported", rows=3)
    log.info("after_block")

    inside, after = _records(capsys)
    assert inside["event"] == "file_imported"
    assert inside["service"] == "importer"
    assert inside["correlation_id"] == "abc-123"
    assert inside["rows"] == 3
    assert inside["level"] == "info"
    assert "timestamp" in inside
    assert "correlation_id" not in after


def test_level_filters_records(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("importer", level="WARNING")
    get_logger().info("hidden")
    get_logger().warning("shown")

    assert [r["event"] for r in _records(capsys)] == ["shown"]


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    assert ServiceSettings().log_level == "DEBUG"
