"""Behavioral tests for the target/pack flow, driven through execute_command."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyocd.target
from pyocd.target.pack import pack_target

from server import CommandHandler, ErrorCode


class StubProbe:
    """Records the target override the server would use on connect."""

    def __init__(self):
        self.override = "UNSET"

    def set_target_override(self, target):
        self.override = target


class FakePackDevice:
    def __init__(self, part_number):
        self.part_number = part_number


class FakeCache:
    """Stand-in for cmsis_pack_manager.Cache."""

    def __init__(self, index, fail_download=None):
        self.index = index
        self._fail_download = fail_download

    def cache_descriptors(self):
        pass

    def packs_for_devices(self, devices):
        if self._fail_download:
            raise self._fail_download


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, raw):
        self.messages.append(json.loads(raw))


PART = "STM32F100C4"


@pytest.fixture
def handler(monkeypatch):
    h = CommandHandler(StubProbe())
    h._pack_cache = FakeCache(index={PART: {"name": PART}})
    h._websocket = FakeWebSocket()
    monkeypatch.setattr(
        pack_target.ManagedPacks, "populate_target", staticmethod(lambda name: None)
    )
    set_installed(monkeypatch, [])
    return h


def set_installed(monkeypatch, parts):
    monkeypatch.setattr(
        pack_target.ManagedPacks,
        "get_installed_targets",
        staticmethod(lambda cache=None: [FakePackDevice(p) for p in parts]),
    )
    for p in parts:
        monkeypatch.setitem(pyocd.target.TARGET, p.lower(), object())


def run(coro):
    return asyncio.run(coro)


def send(handler, cmd):
    return run(handler.execute_command(cmd))


async def install_and_wait(handler, target):
    resp = await handler.execute_command({"cmd": "install_pack", "target": target, "id": 7})
    assert resp["status"] == 0
    await handler._pack_task
    return handler._websocket.messages[-1]


# --- Applying a target ---


def test_target_without_installed_pack_cannot_be_applied(handler):
    pyocd.target.TARGET.pop("stm32g070rbtx", None)

    resp = send(handler, {"cmd": "set_target", "uri": "usb://1", "target": "stm32g070rbtx"})

    assert resp["status"] != 0
    assert resp["error_code"] == ErrorCode.CORTEX_M_UNSUPPORTED_TARGET
    assert "usb://1" not in handler._target_overrides
    assert handler.probe.override == "UNSET"


def test_installed_target_is_applied_for_next_connect(handler, monkeypatch):
    set_installed(monkeypatch, [PART])

    resp = send(handler, {"cmd": "set_target", "uri": "usb://1", "target": PART})

    assert resp["status"] == 0
    assert handler._target_overrides["usb://1"] == PART
    assert handler.probe.override == PART


def test_clearing_target_removes_override(handler, monkeypatch):
    set_installed(monkeypatch, [PART])
    send(handler, {"cmd": "set_target", "uri": "usb://1", "target": PART})

    resp = send(handler, {"cmd": "set_target", "uri": "usb://1", "target": None})

    assert resp["status"] == 0
    assert "usb://1" not in handler._target_overrides
    assert handler.probe.override is None


def test_builtin_target_applies_without_pack_manager(handler, monkeypatch):
    monkeypatch.setattr(
        pack_target.ManagedPacks,
        "populate_target",
        staticmethod(lambda name: (_ for _ in ()).throw(RuntimeError("no pack manager"))),
    )
    monkeypatch.setitem(pyocd.target.TARGET, "cortex_m", object())

    resp = send(handler, {"cmd": "set_target", "uri": "usb://1", "target": "cortex_m"})

    assert resp["status"] == 0
    assert handler.probe.override == "cortex_m"


def test_set_target_without_uri_returns_error(handler):
    resp = send(handler, {"cmd": "set_target", "target": PART})

    assert resp["status"] != 0
    assert "usb://1" not in handler._target_overrides


# --- Installing a pack ---


def test_verified_install_reports_success_and_search_shows_installed(handler, monkeypatch):
    set_installed(monkeypatch, [PART])

    done = run(install_and_wait(handler, PART))

    assert done["type"] == "pack_complete"
    assert done["success"] is True
    assert "error_code" not in done

    resp = send(handler, {"cmd": "search_targets", "query": PART.lower()})
    assert resp["status"] == 0
    assert resp["results"][0]["name"] == PART
    assert resp["results"][0]["installed"] is True


def test_unverified_install_is_reported_as_failure(handler):
    # Fixture default: nothing installed, so the downloaded target never registers
    done = run(install_and_wait(handler, PART))

    assert done["type"] == "pack_complete"
    assert done["success"] is False
    assert done["error_code"] == ErrorCode.CORTEX_M_UNSUPPORTED_TARGET

    resp = send(handler, {"cmd": "search_targets", "query": PART.lower()})
    assert resp["results"][0]["installed"] is False


def test_search_survives_broken_pack_enumeration(handler, monkeypatch):
    monkeypatch.setattr(
        pack_target.ManagedPacks,
        "get_installed_targets",
        staticmethod(lambda cache=None: (_ for _ in ()).throw(RuntimeError("boom"))),
    )

    resp = send(handler, {"cmd": "search_targets", "query": PART.lower()})

    assert resp["status"] == 0
    assert resp["results"][0]["installed"] is False


def test_failed_download_is_reported_with_message(handler):
    handler._pack_cache = FakeCache(
        index={PART: {"name": PART}}, fail_download=RuntimeError("network down")
    )

    done = run(install_and_wait(handler, PART))

    assert done["type"] == "pack_complete"
    assert done["success"] is False
    assert "network down" in done["msg"]
