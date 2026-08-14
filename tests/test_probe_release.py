"""Behavioral tests for letting go of the probe when a connection attempt fails."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from probe import pyocd_probe
from probe.errors import ProbeError
from probe.pyocd_probe import PyOCDProbe


class FakeSession:
    """Stands in for pyOCD's Session, which claims the USB interface in open()."""

    def __init__(self, open_error=None, close_error=None):
        self._open_error = open_error
        self._close_error = close_error
        self.closed = False
        self.is_open = True
        self.probe = type("FakeProbe", (), {"product_name": "ST-Link"})()
        self.target = type("FakeTarget", (), {"part_number": "STM32F103C8"})()

    def open(self):
        if self._open_error is not None:
            raise self._open_error

    def close(self):
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


@pytest.fixture
def chosen_session(monkeypatch):
    """Hands connect() a session the test controls."""

    def install(session):
        monkeypatch.setattr(
            pyocd_probe.ConnectHelper,
            "session_with_chosen_probe",
            staticmethod(lambda **kwargs: session),
        )
        return session

    return install


def connect_expecting_failure(probe):
    with pytest.raises(ProbeError) as caught:
        asyncio.run(probe.connect())
    return caught.value


def test_a_target_that_never_answers_does_not_keep_the_probe(chosen_session):
    session = chosen_session(FakeSession(open_error=RuntimeError("No cores were discovered!")))

    connect_expecting_failure(PyOCDProbe())

    assert session.closed is True


def test_the_original_failure_survives_releasing_the_probe(chosen_session):
    chosen_session(FakeSession(open_error=RuntimeError("STLink error (9): Get IDCODE error")))

    error = connect_expecting_failure(PyOCDProbe())

    assert "Get IDCODE error" in str(error)


def test_a_release_that_itself_fails_does_not_replace_the_real_error(chosen_session):
    chosen_session(
        FakeSession(
            open_error=RuntimeError("STLink error (9): Get IDCODE error"),
            close_error=RuntimeError("probe already gone"),
        )
    )

    error = connect_expecting_failure(PyOCDProbe())

    assert "Get IDCODE error" in str(error)


def test_a_successful_connect_keeps_the_session_open(chosen_session):
    session = chosen_session(FakeSession())

    probe = PyOCDProbe()
    assert asyncio.run(probe.connect()) is True

    assert session.closed is False
    assert probe.session is session
