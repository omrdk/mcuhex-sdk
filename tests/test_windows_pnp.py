"""Behavioral tests for spotting a probe Windows has no driver for."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import probe.windows_pnp as pnp
from probe.windows_pnp import PnpDevice, driverless_probes, scan

FAILED_INSTALL = 28
NOT_CONFIGURED = 1
UNSIGNED_DRIVER = 52
RUNNING = 0

# Both recorded with pnputil on a Windows 11 machine before any driver work.
DONGLE = PnpDevice(r"USB\VID_0483&PID_3748\A", "STM32 STLink", FAILED_INSTALL)

NUCLEO_PARENT_ID = r"USB\VID_0483&PID_374B\0675FF383337554E43123516"
NUCLEO = [
    PnpDevice(NUCLEO_PARENT_ID, "STM32 STLink", RUNNING),
    PnpDevice(r"USB\VID_0483&PID_374B&MI_00\9&32a9ab96&0&0000", "ST-Link Debug", FAILED_INSTALL, NUCLEO_PARENT_ID),
    PnpDevice(r"USB\VID_0483&PID_374B&MI_01\9&32a9ab96&0&0001", "USB Mass Storage Device", RUNNING, NUCLEO_PARENT_ID),
    PnpDevice(r"USB\VID_0483&PID_374B&MI_02\9&32a9ab96&0&0002", "USB Serial Device (COM3)", RUNNING, NUCLEO_PARENT_ID),
]


def devices_of(rows):
    return [r["device"] for r in rows]


def test_a_driverless_dongle_is_reported_once_with_its_ids():
    rows = driverless_probes([DONGLE])

    assert rows == [{
        "device": DONGLE.instance_id,
        "description": "STM32 STLink",
        "vid": 0x0483,
        "pid": 0x3748,
    }]


def test_a_composite_probe_collapses_to_its_parent_named_after_the_failing_interface():
    rows = driverless_probes(NUCLEO)

    assert devices_of(rows) == [NUCLEO_PARENT_ID]
    assert rows[0]["description"] == "ST-Link Debug"


def test_a_probe_whose_driver_is_bound_is_not_reported():
    running = PnpDevice(DONGLE.instance_id, DONGLE.description, RUNNING)

    assert driverless_probes([running]) == []


def test_a_driverless_device_pyocd_would_not_list_is_ignored():
    printer = PnpDevice(r"USB\VID_04B8&PID_0005\ABC", "EPSON Printer", FAILED_INSTALL)

    assert driverless_probes([printer]) == []


def test_every_driver_problem_code_counts():
    rows = driverless_probes([
        PnpDevice(r"USB\VID_0483&PID_3748\1", "STM32 STLink", NOT_CONFIGURED),
        PnpDevice(r"USB\VID_0483&PID_3748\2", "STM32 STLink", UNSIGNED_DRIVER),
    ])

    assert len(rows) == 2


def test_an_unrelated_problem_code_does_not_count():
    # CM_PROB_DISABLED: the user turned the device off, no driver is missing.
    disabled = PnpDevice(DONGLE.instance_id, DONGLE.description, 22)

    assert driverless_probes([disabled]) == []


def test_every_stlink_generation_pyocd_knows_is_recognised():
    v3 = PnpDevice(r"USB\VID_0483&PID_374E&MI_00\1", "ST-Link Debug", FAILED_INSTALL, r"USB\VID_0483&PID_374E\S")

    assert devices_of(driverless_probes([v3])) == [r"USB\VID_0483&PID_374E\S"]


def test_a_jlink_is_recognised_by_segger_vid():
    jlink = PnpDevice(r"USB\VID_1366&PID_0101\000123", "J-Link", FAILED_INSTALL)

    assert len(driverless_probes([jlink])) == 1


def test_a_cmsis_dap_probe_is_recognised_by_pyocd_id_table_or_by_name():
    daplink = PnpDevice(r"USB\VID_0D28&PID_0204\1", "", FAILED_INSTALL)
    by_name = PnpDevice(r"USB\VID_DEAD&PID_BEEF\1", "Acme CMSIS-DAP", FAILED_INSTALL)
    stranger = PnpDevice(r"USB\VID_DEAD&PID_BEEF\2", "Acme Gadget", FAILED_INSTALL)

    assert len(driverless_probes([daplink, by_name, stranger])) == 2


def test_non_usb_nodes_are_ignored():
    hid = PnpDevice(r"HID\VID_0483&PID_3748\1", "STM32 STLink", FAILED_INSTALL)

    assert driverless_probes([hid]) == []


def test_without_pyocd_tables_nothing_is_reported(monkeypatch):
    monkeypatch.setattr(pnp, "STLinkUSBInterface", None)

    assert driverless_probes([DONGLE]) == []


def test_scan_is_empty_off_windows(monkeypatch):
    monkeypatch.setattr(pnp.platform, "system", lambda: "Darwin")

    assert scan() == []


def test_scan_survives_a_failing_enumeration(monkeypatch):
    monkeypatch.setattr(pnp.platform, "system", lambda: "Windows")

    def broken():
        raise OSError("SetupDiGetClassDevsW failed")

    monkeypatch.setattr(pnp, "_enumerate_usb_tree", broken)

    assert scan() == []
