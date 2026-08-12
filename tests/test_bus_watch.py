"""Behavioral tests for noticing boards plugged in and out between sessions."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server as server_mod
from server import CommandHandler

BOARD = {"device": "usb://1", "description": "ST-Link"}

bus: list = []


class FakeCmsisDapScan:
    def list_devices(self):
        return [dict(d) for d in bus]


class FakeSerialScan:
    def list_devices(self):
        return []


class IdleProbe:
    """No session open, so the bus watcher is free to scan."""

    demo = False

    def is_open(self):
        return False


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, raw):
        self.messages.append(json.loads(raw))


@pytest.fixture(autouse=True)
def fast_watch(monkeypatch):
    monkeypatch.setattr(server_mod, "BUS_WATCH_SECS", 0.01)
    monkeypatch.setattr(server_mod, "PyOCDProbe", FakeCmsisDapScan)
    monkeypatch.setattr(server_mod, "DebugProbe", FakeSerialScan)
    bus.clear()


@pytest.fixture
def handler():
    h = CommandHandler(IdleProbe())
    h._websocket = FakeWebSocket()
    return h


def run_watch(handler, change_bus):
    """Let a client list the bus, change it, then let the watcher run."""

    async def flow():
        await handler.execute_command({"cmd": "list_devices"})
        handler._start_bus_watch(handler._websocket)
        change_bus()
        await asyncio.sleep(server_mod.BUS_WATCH_SECS * 5)
        handler._stop_bus_watch()

    asyncio.run(flow())
    return [m for m in handler._websocket.messages if m["type"] == "devices_changed"]


def test_a_board_plugged_in_is_announced(handler):
    changed = run_watch(handler, lambda: bus.append(BOARD))

    assert len(changed) == 1
    assert [d["device"] for d in changed[0]["devices"]] == ["usb://1"]


def test_the_last_board_unplugged_is_announced(handler):
    bus.append(BOARD)

    changed = run_watch(handler, lambda: bus.clear())

    assert len(changed) == 1
    assert changed[0]["devices"] == []


def test_devices_changed_carries_no_status_field(handler):
    """Clients fall back to matching a reply by 'status', so a push carrying
    one would be mistaken for the answer to a command still in flight."""
    changed = run_watch(handler, lambda: bus.append(BOARD))

    assert "status" not in changed[0]


def test_an_unchanged_bus_says_nothing(handler):
    bus.append(BOARD)

    assert run_watch(handler, lambda: None) == []


def test_an_open_session_is_not_rescanned(handler, monkeypatch):
    monkeypatch.setattr(handler.probe, "is_open", lambda: True)

    assert run_watch(handler, lambda: bus.append(BOARD)) == []
