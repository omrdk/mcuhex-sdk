"""Behavioral tests for reading the error code out of a failure."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyocd.core import exceptions
from pyocd.core.target import Target
from pyocd.probe.stlink.constants import Status

from probe.error_map import CFSR, HFSR, classify
from probe.errors import ProbeError


def stlink(status: int) -> exceptions.ProbeError:
    """The exception pyOCD raises for an ST-Link status, message and all."""
    return exceptions.ProbeError(Status.get_error_message(status))


class FakeCore:
    def __init__(self, state=Target.State.RUNNING):
        self._state = state

    def get_state(self):
        if self._state is None:
            raise RuntimeError("transfer fault reading DHCSR")
        return self._state


class FakeTarget:
    """A target whose state and memory answer only what it was given."""

    def __init__(self, state=Target.State.RUNNING, memory=None):
        self.selected_core = FakeCore(state)
        self._memory = memory or {}

    def read32(self, address):
        if address not in self._memory:
            raise RuntimeError(f"transfer fault at {address:#010x}")
        return self._memory[address]


def test_our_own_code_is_never_second_guessed():
    refusal = ProbeError("the chip reports 128 KB", "FLASH_TARGET_MISMATCH")

    assert classify(refusal, "flash") == "FLASH_TARGET_MISMATCH"


def test_a_target_that_is_not_there_says_so():
    assert classify(stlink(Status.JTAG_NO_DEVICE_CONNECTED), "connect") == \
        "CORTEX_M_NO_TARGET_RESPONSE"


def test_a_target_that_will_not_give_its_idcode_is_the_same_case():
    assert classify(stlink(Status.JTAG_GET_IDCODE_ERROR), "connect") == \
        "CORTEX_M_NO_TARGET_RESPONSE"
    assert classify(stlink(Status.JTAG_UNKNOWN_JTAG_CHAIN), "connect") == \
        "CORTEX_M_NO_TARGET_RESPONSE"


def test_a_debug_unit_that_cannot_be_powered_is_its_own_case():
    assert classify(stlink(Status.JTAG_DBG_POWER_ERROR), "connect") == \
        "CORTEX_M_DEBUG_POWER_FAILED"


def test_a_probe_held_in_another_mode_is_not_a_target_problem():
    assert classify(stlink(Status.JTAG_ALREADY_OPENED_IN_OTHER_MODE), "connect") == \
        "PROBE_ALREADY_OPEN"


def test_a_frequency_the_probe_will_not_take_points_at_the_probe():
    assert classify(stlink(Status.JTAG_FREQ_NOT_SUPPORTED), "connect") == \
        "PROBE_DRIVER_MISMATCH"


@pytest.mark.parametrize("status", [Status.SWD_AP_FAULT, Status.SWD_DP_FAULT])
def test_a_refused_access_while_connecting_reads_as_a_locked_port(status):
    """The port answered and said no, which is what RDP level 1 looks like."""
    assert classify(stlink(status), "connect") == "CORTEX_M_DEBUG_PORT_LOCKED"


@pytest.mark.parametrize("status", [Status.SWD_AP_FAULT, Status.SWD_DP_FAULT])
def test_the_same_refusal_while_reading_is_an_ordinary_transfer_fault(status):
    assert classify(stlink(status), "read") == "READ_WRITE_FAILED"


@pytest.mark.parametrize("status", [
    Status.SWD_AP_ERROR,
    Status.SWD_AP_PARITY_ERROR,
    Status.SWD_DP_ERROR,
    Status.SWD_DP_PARITY_ERROR,
])
def test_a_garbled_line_reads_as_a_protocol_error(status):
    assert classify(stlink(status), "connect") == "CORTEX_M_SWD_PROTOCOL_ERROR"


@pytest.mark.parametrize("status", [Status.SWD_AP_WAIT, Status.SWD_DP_WAIT])
def test_a_port_that_keeps_asking_to_wait_is_a_timeout(status):
    assert classify(stlink(status), "connect") == "CONNECT_TIMEOUT"


def test_a_status_with_no_agreed_meaning_falls_back_to_the_type():
    """An SPI error inside the probe is real but says nothing we can act on."""
    assert classify(stlink(Status.JTAG_SPI_ERROR), "connect") == "UNKNOWN_CONNECTION_ERROR"


def test_usb_tells_us_about_permissions_and_ownership_by_number():
    usb = pytest.importorskip("usb.core")

    assert classify(usb.USBError("Access denied", errno=13), "connect") == "PERMISSION_DENIED"
    assert classify(usb.USBError("Resource busy", errno=16), "connect") == "DEVICE_BUSY"


def test_an_erase_that_failed_is_not_reported_as_a_programming_failure():
    assert classify(exceptions.FlashEraseFailure("sector", address=0x8000000), "flash") == \
        "FLASH_ERASE_FAILED"
    assert classify(exceptions.FlashProgramFailure("page", result_code=1), "flash") == \
        "FLASH_PROGRAM_FAILED"


def test_a_missing_file_comes_from_the_operating_system():
    assert classify(FileNotFoundError(2, "No such file or directory"), "flash") == \
        "FLASH_FILE_NOT_FOUND"


def test_a_part_pyocd_has_no_support_for_says_so_by_type():
    assert classify(exceptions.TargetSupportError("no target named 'stm32f9'"), "connect") == \
        "CORTEX_M_UNSUPPORTED_TARGET"


def test_a_port_that_answers_but_hides_no_cortex_m_is_named():
    """pyOCD gives this failure nothing but its sentence, so the sentence is pinned."""
    from probe.error_map import NO_CORES_SENTINEL

    assert classify(exceptions.DebugError(NO_CORES_SENTINEL), "connect") == \
        "CORTEX_M_NO_CORE_FOUND"


def test_a_probe_that_cannot_speak_swd_points_at_the_probe():
    failure = exceptions.DebugError(
        "requested wire protocol SWD not supported by the debug probe")

    assert classify(failure, "connect") == "PROBE_DRIVER_MISMATCH"


@pytest.mark.parametrize("message", [
    "V2J20 firmware does not support JTAG/SWD. Please update to a newer version",
    "STLink 066BFF is using an unsupported, older firmware version. Please update it.",
])
def test_old_probe_firmware_is_a_different_job_than_any_wiring_fix(message):
    assert classify(exceptions.ProbeError(message), "connect") == "PROBE_FIRMWARE_TOO_OLD"


def test_a_lost_probe_is_reported_as_disconnected():
    assert classify(exceptions.ProbeDisconnected("gone"), "read") == "DEVICE_DISCONNECTED"


def test_a_transfer_error_during_a_read_is_not_blamed_on_the_wiring():
    assert classify(exceptions.TransferError("read failed"), "read") == "READ_WRITE_FAILED"


def test_words_in_a_message_no_longer_decide_anything():
    """What this module exists to stop: 'fault' and 'not found' appearing in an
    unrelated sentence used to pick the code."""
    assert classify(exceptions.ProbeError("probe logged a fault of its own"), "connect") == \
        "UNKNOWN_CONNECTION_ERROR"
    assert classify(RuntimeError("target type stm32f9 not found"), "flash") == \
        "UNKNOWN_CONNECTION_ERROR"


def test_a_failure_from_nowhere_we_know_stays_generic():
    assert classify(Exception("something went sideways"), "connect") == \
        "UNKNOWN_CONNECTION_ERROR"


def test_a_chip_held_in_reset_says_so_rather_than_blaming_the_read():
    target = FakeTarget(state=Target.State.RESET)

    assert classify(exceptions.TransferFaultError("read"), "read", target) == \
        "CORTEX_M_TARGET_IN_RESET"


def test_a_locked_up_core_is_reported_as_the_fault_it_is():
    target = FakeTarget(state=Target.State.LOCKUP)

    assert classify(exceptions.TransferFaultError("read"), "read", target) == \
        "CORTEX_M_HARDFAULT_DETECTED"


def test_registers_cannot_be_read_while_the_core_runs():
    target = FakeTarget(state=Target.State.RUNNING)
    refusal = exceptions.CoreRegisterAccessError(
        "cannot read register r0 because core #0 is not halted")

    assert classify(refusal, "read", target) == "CORTEX_M_TARGET_NOT_HALTED"


def test_a_halted_core_refusing_a_register_is_not_called_a_running_one():
    target = FakeTarget(state=Target.State.HALTED)
    refusal = exceptions.CoreRegisterAccessError("register unavailable")

    assert classify(refusal, "read", target) == "UNKNOWN_CONNECTION_ERROR"


def test_a_fault_the_firmware_already_took_is_surfaced():
    target = FakeTarget(memory={HFSR: 0x40000000, CFSR: 0x00000082})

    assert classify(exceptions.TransferFaultError("read"), "read", target) == \
        "CORTEX_M_HARDFAULT_DETECTED"


def test_a_chip_with_nothing_to_report_leaves_the_code_alone():
    target = FakeTarget(memory={HFSR: 0})

    assert classify(exceptions.TransferFaultError("read"), "read", target) == \
        "READ_WRITE_FAILED"


def test_a_chip_that_cannot_be_asked_leaves_the_code_alone():
    """The link that broke the read is the link this would travel over."""
    target = FakeTarget(state=None)

    assert classify(exceptions.TransferFaultError("read"), "read", target) == \
        "READ_WRITE_FAILED"


def test_a_precise_answer_from_the_probe_is_never_overruled_by_the_chip():
    target = FakeTarget(state=Target.State.RESET)

    assert classify(stlink(Status.JTAG_ALREADY_OPENED_IN_OTHER_MODE), "read", target) == \
        "PROBE_ALREADY_OPEN"


def test_the_chip_is_not_asked_about_a_connection_that_never_opened():
    target = FakeTarget(state=Target.State.RESET)

    assert classify(exceptions.TransferFaultError("dp"), "connect", target) == \
        "CORTEX_M_DEBUG_PORT_LOCKED"
