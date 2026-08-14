"""Behavioral tests for the target/pack flow, driven through execute_command."""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyocd.target
from pyocd.target.pack import pack_target

import server as server_mod
from probe import pack_errors
from server import CommandHandler, ErrorCode


@pytest.fixture(autouse=True)
def pack_host_answers(monkeypatch):
    """Classifying a pack failure opens a socket; the suite must not."""
    monkeypatch.setattr(pack_errors, "pack_host_reachable", lambda: True)


class StubProbe:
    """Records the target override the server would use on connect."""

    def __init__(self):
        self.override = "UNSET"

    def set_target_override(self, target):
        self.override = target

    async def set_port(self, port):
        self.port = port

    async def connect(self):
        return False

    def get_target_info(self):
        return None


class FakePackDevice:
    def __init__(self, part_number):
        self.part_number = part_number


class FakeCache:
    """Stand-in for cmsis_pack_manager.Cache."""

    def __init__(self, index, fail_download=None, download_delay=0, data_path=None):
        self.index = index
        self.data_path = data_path
        self._fail_download = fail_download
        self._download_delay = download_delay

    def cache_descriptors(self):
        pass

    # Resolving which packs cover a device costs nothing; downloading them is the
    # slow part that can fail. A fake that collapsed the two is what let a version
    # ship where no pack was ever downloaded.
    def packs_for_devices(self, devices):
        return [f"pack:{d.get('name')}" for d in devices]

    def download_pack_list(self, pack_list):
        if self._fail_download:
            raise self._fail_download
        if self._download_delay:
            time.sleep(self._download_delay)


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


def test_slow_download_emits_heartbeats(handler, monkeypatch):
    monkeypatch.setattr(server_mod, "PACK_HEARTBEAT_SECS", 0.03)
    handler._pack_cache = FakeCache(index={PART: {"name": PART}}, download_delay=0.15)
    set_installed(monkeypatch, [PART])

    done = run(install_and_wait(handler, PART))

    assert done["success"] is True
    heartbeats = [
        m for m in handler._websocket.messages
        if m["type"] == "pack_progress" and "Still downloading" in m["msg"]
    ]
    assert len(heartbeats) >= 2


def test_cancel_during_download_reports_cancelled(handler):
    handler._pack_cache = FakeCache(index={PART: {"name": PART}}, download_delay=0.5)

    async def flow():
        await handler.execute_command({"cmd": "install_pack", "target": PART, "id": 7})
        task = handler._pack_task
        await asyncio.sleep(0.1)
        resp = await handler.execute_command({"cmd": "cancel_pack"})
        assert resp["msg"] == "pack_install_cancelled"
        await asyncio.gather(task, return_exceptions=True)

    run(flow())

    done = handler._websocket.messages[-1]
    assert done["type"] == "pack_complete"
    assert done["success"] is False
    assert done["error_code"] == ErrorCode.FLASH_CANCELLED
    assert handler._pack_task is None


def test_cancel_without_active_install_is_harmless(handler):
    resp = send(handler, {"cmd": "cancel_pack"})

    assert resp["status"] == 0
    assert resp["msg"] == "no_active_pack_install"


# --- Device listing ---


class FakeBoardInfo:
    def __init__(self, name, target):
        self.name = name
        self.target = target


class FakePyocdProbe:
    def __init__(self, uid, board_info=None, board_error=None):
        self.unique_id = uid
        self.description = uid
        self.vendor_name = "STMicroelectronics"
        self.product_name = "ST-Link"
        self._board_info = board_info
        self._board_error = board_error

    @property
    def associated_board_info(self):
        if self._board_error:
            raise self._board_error
        return self._board_info


def list_devices_with(handler, monkeypatch, probes):
    import probe.pyocd_probe as pp

    monkeypatch.setattr(
        pp.ConnectHelper, "get_all_connected_probes", staticmethod(lambda blocking=False: probes)
    )
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [])
    resp = send(handler, {"cmd": "list_devices"})
    assert resp["status"] == 0
    return resp["devices"]


def test_dev_board_exposes_target_hint(handler, monkeypatch):
    devices = list_devices_with(
        handler,
        monkeypatch,
        [FakePyocdProbe("usb://nucleo", FakeBoardInfo("NUCLEO-G071RB", "stm32g071rbtx"))],
    )

    assert devices[0]["target_hint"] == "stm32g071rbtx"
    assert devices[0]["board_name"] == "NUCLEO-G071RB"
    assert devices[0]["family"] == "ARM Cortex-M"


def test_bare_probe_lists_without_target_hint(handler, monkeypatch):
    devices = list_devices_with(handler, monkeypatch, [FakePyocdProbe("usb://stlink")])

    assert devices[0]["device"] == "usb://stlink"
    assert "target_hint" not in devices[0]
    assert "board_name" not in devices[0]


def test_probe_with_failing_board_query_still_lists(handler, monkeypatch):
    devices = list_devices_with(
        handler,
        monkeypatch,
        [FakePyocdProbe("usb://busy", board_error=RuntimeError("device busy"))],
    )

    assert devices[0]["device"] == "usb://busy"
    assert "target_hint" not in devices[0]


def test_failed_download_is_reported_with_message(handler):
    handler._pack_cache = FakeCache(
        index={PART: {"name": PART}}, fail_download=RuntimeError("network down")
    )

    done = run(install_and_wait(handler, PART))

    assert done["type"] == "pack_complete"
    assert done["success"] is False
    assert "network down" in done["msg"]
    # Nothing observable was wrong, so the failure keeps the generic code.
    assert done["error_code"] == ErrorCode.UNKNOWN


def test_failed_download_names_a_silent_pack_server(handler, monkeypatch):
    handler._pack_cache = FakeCache(
        index={PART: {"name": PART}}, fail_download=RuntimeError("download failed")
    )
    monkeypatch.setattr(pack_errors, "pack_host_reachable", lambda: False)

    done = run(install_and_wait(handler, PART))

    assert done["error_code"] == ErrorCode.PACK_NETWORK_UNREACHABLE


def test_failed_download_names_a_full_disk_where_the_cache_lives(handler, monkeypatch):
    handler._pack_cache = FakeCache(
        index={PART: {"name": PART}},
        fail_download=RuntimeError("download failed"),
        data_path="/var/cache/cmsis",
    )
    seen = []
    monkeypatch.setattr(pack_errors, "_free_bytes", lambda path: seen.append(path) or 0)

    done = run(install_and_wait(handler, PART))

    assert done["error_code"] == ErrorCode.PACK_DISK_FULL
    assert seen == ["/var/cache/cmsis"]


def test_a_target_the_index_does_not_have_is_named(handler):
    done = run(install_and_wait(handler, "STM32NOTREAL"))

    assert done["success"] is False
    assert done["error_code"] == ErrorCode.CORTEX_M_UNSUPPORTED_TARGET


# --- Devices we have no transport for ---


class FakeSerialPort:
    def __init__(self, device, vid=0x1A86, pid=0x7523):
        self.device = device
        self.description = "USB Serial"
        self.manufacturer = "QinHeng"
        self.vid = vid
        self.pid = pid
        self.hwid = f"USB VID:PID={vid:04X}:{pid:04X}"
        self.product = "CH340"


def list_with_serial(handler, monkeypatch, ports, probes=()):
    import probe.pyocd_probe as pp

    monkeypatch.setattr(
        pp.ConnectHelper, "get_all_connected_probes", staticmethod(lambda blocking=False: probes)
    )
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: ports)
    resp = send(handler, {"cmd": "list_devices"})
    assert resp["status"] == 0
    return resp["devices"]


def test_a_serial_device_is_listed_and_marked_unsupported(handler, monkeypatch):
    """Dropping it would leave a user looking for their board at an empty list."""
    devices = list_with_serial(handler, monkeypatch, [FakeSerialPort("/dev/cu.usbserial-110")])

    assert devices[0]["device"] == "/dev/cu.usbserial-110"
    assert devices[0]["supported"] is False
    assert devices[0]["transport"] == "serial"


def test_an_swd_probe_is_supported(handler, monkeypatch):
    devices = list_with_serial(handler, monkeypatch, [], [FakePyocdProbe("usb://stlink")])

    assert (devices[0]["supported"], devices[0]["transport"]) == (True, "swd")


def test_an_esp32_is_supported_only_with_its_driver(handler, monkeypatch):
    monkeypatch.setitem(server_mod.PROBE_MAP, "OCD_ESP32C3_Probe", object)
    monkeypatch.setattr(server_mod, "OCD_ESP32C3_Probe", object)

    devices = list_with_serial(
        handler, monkeypatch, [FakeSerialPort("/dev/cu.esp", vid=0x303A)]
    )

    assert (devices[0]["supported"], devices[0]["transport"]) == (True, "openocd")


def test_an_esp32_without_its_driver_is_not_offered(handler, monkeypatch):
    monkeypatch.setattr(server_mod, "OCD_ESP32C3_Probe", None)

    devices = list_with_serial(
        handler, monkeypatch, [FakeSerialPort("/dev/cu.esp", vid=0x303A)]
    )

    assert devices[0]["supported"] is False


def test_connecting_to_a_device_we_cannot_speak_to_says_so(handler, monkeypatch):
    list_with_serial(handler, monkeypatch, [FakeSerialPort("/dev/cu.usbserial-110")])

    resp = send(handler, {"cmd": "connect", "uri": "/dev/cu.usbserial-110"})

    assert resp["status"] != 0
    assert resp["error_code"] == ErrorCode.PROBE_TRANSPORT_UNSUPPORTED


def test_a_uri_no_scan_has_seen_is_not_refused_on_that_basis(handler):
    """Refusing an unscanned uri would break a client that connects directly."""
    resp = send(handler, {"cmd": "connect", "uri": "usb://never-scanned"})

    assert resp.get("error_code") != ErrorCode.PROBE_TRANSPORT_UNSUPPORTED


# --- One probe, several boards ---


def test_a_device_with_no_target_of_its_own_does_not_inherit_one(handler, monkeypatch):
    set_installed(monkeypatch, [PART])
    send(handler, {"cmd": "set_target", "uri": "usb://1", "target": PART})

    send(handler, {"cmd": "connect", "uri": "usb://2"})

    assert handler.probe.override is None


def test_a_device_keeps_its_own_target_across_another_connect(handler, monkeypatch):
    set_installed(monkeypatch, [PART])
    send(handler, {"cmd": "set_target", "uri": "usb://1", "target": PART})
    send(handler, {"cmd": "connect", "uri": "usb://2"})

    send(handler, {"cmd": "connect", "uri": "usb://1"})

    assert handler.probe.override == PART


def test_the_target_given_at_startup_outlives_a_device_without_one(monkeypatch):
    """--target names the part for everything; clearing per device would drop it."""
    monkeypatch.setattr(
        pack_target.ManagedPacks, "populate_target", staticmethod(lambda name: None)
    )
    probe = StubProbe()
    probe.target_override = "cortex_m"

    h = CommandHandler(probe)
    send(h, {"cmd": "connect", "uri": "usb://2"})

    assert h.probe.override == "cortex_m"
