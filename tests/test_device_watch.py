"""Behavioral tests for noticing a probe that is unplugged mid-session."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server as server_mod
from server import CommandHandler, ErrorCode

URI = "usb://1"


class WatchableProbe:
    """Probe that can be told whether its device is still on the bus."""

    def __init__(self):
        self.present = True
        self.open = False
        self.port = None
        self.disconnect_calls = 0

    async def set_port(self, port):
        self.port = port

    async def connect(self):
        self.open = True
        return True

    async def disconnect(self):
        self.disconnect_calls += 1
        self.open = False
        return False

    def is_open(self):
        return self.open

    def get_target_info(self):
        return None

    def is_device_present(self, unique_id):
        return self.present


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, raw):
        self.messages.append(json.loads(raw))


@pytest.fixture(autouse=True)
def fast_watch(monkeypatch):
    monkeypatch.setattr(server_mod, "DEVICE_WATCH_SECS", 0.01)


@pytest.fixture
def probe():
    return WatchableProbe()


@pytest.fixture
def handler(probe):
    h = CommandHandler(probe)
    h._websocket = FakeWebSocket()
    return h


def run_session(handler, after_connect):
    """Connect, apply a change to the bus, then let the watcher run."""

    async def flow():
        resp = await handler.execute_command({"cmd": "connect", "uri": URI})
        assert resp["status"] == 0
        after_connect()
        await asyncio.sleep(server_mod.DEVICE_WATCH_SECS * 4)
        handler._stop_device_watch()

    asyncio.run(flow())


def test_unplugged_probe_is_reported_and_session_released(handler, probe):
    run_session(handler, lambda: setattr(probe, "present", False))

    lost = [m for m in handler._websocket.messages if m["type"] == "device_lost"]
    assert len(lost) == 1
    assert lost[0]["error_code"] == ErrorCode.DEVICE_DISCONNECTED
    assert lost[0]["device"] == URI
    assert probe.disconnect_calls == 1


def test_device_lost_carries_no_status_field(handler, probe):
    """Clients fall back to matching a reply by 'status', so a push carrying
    one would be mistaken for the answer to a command still in flight."""
    run_session(handler, lambda: setattr(probe, "present", False))

    assert "status" not in handler._websocket.messages[-1]


def test_present_probe_is_left_connected(handler, probe):
    run_session(handler, lambda: None)

    assert handler._websocket.messages == []
    assert probe.is_open()


def test_unreadable_bus_is_not_treated_as_an_unplug(handler, probe):
    run_session(handler, lambda: setattr(probe, "present", None))

    assert handler._websocket.messages == []
    assert probe.is_open()
