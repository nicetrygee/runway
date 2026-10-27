import logging

import pytest

import app as app_module


def test_startup_warns_when_lab_mode_on(monkeypatch, caplog):
    monkeypatch.setenv("RUNWAY_LAB", "1")

    with caplog.at_level(logging.WARNING):
        app_module.warn_if_lab_mode()

    (record,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "RUNWAY_LAB=1" in record.getMessage()
    assert "synthetic" in record.getMessage()


@pytest.mark.parametrize("value", [None, "0"])
def test_startup_silent_when_lab_mode_off(monkeypatch, caplog, value):
    if value is not None:
        monkeypatch.setenv("RUNWAY_LAB", value)

    with caplog.at_level(logging.WARNING):
        app_module.warn_if_lab_mode()

    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
