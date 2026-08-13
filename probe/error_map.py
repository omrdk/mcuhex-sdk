"""Turn a failure into the error code the user sees, using what the failure knows.

The code is read from the failure itself, in descending order of certainty:

  1. Our own ProbeError already carries one — it passes through untouched.
  2. libusb reports a number in ``USBError.errno``.
  3. ST-Link firmware reports a numbered status, which pyOCD prints in a single
     machine-written format (``pyocd/probe/stlink/constants.py:165``).
  4. pyOCD raises a typed exception. Every probe converges here: CMSIS-DAP,
     DAPLink and J-Link errors are converted into the same hierarchy
     (``pyocd/probe/cmsis_dap_probe.py:716-728``), so this step alone always
     produces something better than nothing.

Nothing below searches an English sentence for a word. Two exceptions are pinned
literals, each naming the pyOCD line that writes it, because those failures have
no type or status of their own.

When no step is certain the code stays generic. Sending a user after the wrong
fix costs more than admitting the failure was not understood.

The strings here are sent on the WebSocket as-is and must match ErrorCode in
server.py and ConnectionErrorCode in the web client.
"""
import logging
import re
from typing import Optional

from pyocd.core import exceptions
from pyocd.core.target import Target

from .errors import ProbeError

LOG = logging.getLogger("error-map")

UNKNOWN = "UNKNOWN_CONNECTION_ERROR"

# Codes general enough that an observation of the chip beats them. A precise
# answer from the probe is never overruled by one.
_REFINABLE = (UNKNOWN, "READ_WRITE_FAILED")

# ARMv7-M fault status registers, in the space every Cortex-M implements.
HFSR = 0xE000ED2C
CFSR = 0xE000ED28

# pyOCD writes the ST-Link status as "STLink error (N): <text>" with N in
# decimal, from one place only.
_STLINK_STATUS = re.compile(r"STLink error \((\d+)\)")

# ST-Link firmware status -> code. Only statuses whose meaning is unambiguous
# are listed; the rest fall through to the exception type.
STLINK_STATUS_CODES = {
    0x04: "CORTEX_M_NO_TARGET_RESPONSE",      # Unknown JTAG chain
    0x05: "CORTEX_M_NO_TARGET_RESPONSE",      # No device connected
    0x09: "CORTEX_M_NO_TARGET_RESPONSE",      # Get IDCODE error
    0x0B: "CORTEX_M_DEBUG_POWER_FAILED",      # Debug power error
    0x0E: "PROBE_ALREADY_OPEN",               # Already opened in another mode
    0x10: "CONNECT_TIMEOUT",                  # AP wait
    0x12: "CORTEX_M_SWD_PROTOCOL_ERROR",      # AP error
    0x13: "CORTEX_M_SWD_PROTOCOL_ERROR",      # AP parity error
    0x14: "CONNECT_TIMEOUT",                  # DP wait
    0x16: "CORTEX_M_SWD_PROTOCOL_ERROR",      # DP error
    0x17: "CORTEX_M_SWD_PROTOCOL_ERROR",      # DP parity error
    0x41: "PROBE_DRIVER_MISMATCH",            # Frequency not supported
}

# An AP or DP fault means the port answered and refused the access. While
# connecting that is the signature of a locked debug port (RDP level 1,
# APPROTECT); during a memory access it is an ordinary transfer fault.
_STLINK_FAULT_STATUSES = (0x11, 0x15)

# pyocd/coresight/coresight_target.py:313 — a DebugError with nothing but this
# sentence to distinguish it from the other DebugErrors.
NO_CORES_SENTINEL = "No cores were discovered!"

# pyocd/probe/stlink/stlink.py:190,193 — both are ProbeError, and the version
# numbers in them rule out matching the whole sentence.
_OLD_FIRMWARE_SENTINELS = ("firmware does not support", "older firmware version")

# libusb errno values, from the two we have seen a probe produce.
_USB_ERRNO_CODES = {
    13: "PERMISSION_DENIED",   # access denied — no udev rule, or another owner
    16: "DEVICE_BUSY",         # resource busy
}


def classify(exc: BaseException, operation: str = "unknown", target=None) -> str:
    """The error code for `exc`, raised while doing `operation`.

    `operation` is 'connect', 'read', 'write' or 'flash'. It only decides
    whether a refused access is read as a locked debug port or as a transfer
    fault — the same silicon answer means different things at those two moments.

    `target` is the live session, when there is one. A chip that is held in
    reset or sitting in a fault says so itself, which beats anything that can be
    inferred from the failure of the operation it broke.
    """
    if isinstance(exc, ProbeError):
        return exc.error_code

    usb_code = _from_usb_errno(exc)
    if usb_code:
        return usb_code

    status_code = _from_stlink_status(exc, operation)
    if status_code:
        return status_code

    code = _from_type(exc, operation)
    if target is not None and operation != "connect" and code in _REFINABLE:
        return _from_chip_state(target, exc) or code
    return code


def _from_usb_errno(exc: BaseException) -> Optional[str]:
    try:
        from usb.core import USBError
    except ImportError:
        return None
    if not isinstance(exc, USBError):
        return None
    return _USB_ERRNO_CODES.get(exc.errno)


def _from_stlink_status(exc: BaseException, operation: str) -> Optional[str]:
    match = _STLINK_STATUS.search(str(exc))
    if match is None:
        return None
    status = int(match.group(1))
    if status in _STLINK_FAULT_STATUSES:
        return "CORTEX_M_DEBUG_PORT_LOCKED" if operation == "connect" else "READ_WRITE_FAILED"
    return STLINK_STATUS_CODES.get(status)


def _from_type(exc: BaseException, operation: str) -> str:
    if isinstance(exc, FileNotFoundError):
        return "FLASH_FILE_NOT_FOUND"
    if isinstance(exc, PermissionError):
        return "PERMISSION_DENIED"
    if isinstance(exc, exceptions.FlashEraseFailure):
        return "FLASH_ERASE_FAILED"
    if isinstance(exc, exceptions.FlashFailure):
        return "FLASH_PROGRAM_FAILED"
    if isinstance(exc, exceptions.TargetSupportError):
        return "CORTEX_M_UNSUPPORTED_TARGET"
    # pyOCD's TimeoutError is its own class, unrelated to the builtin one that
    # asyncio and the socket layer raise. Both mean the same thing here.
    if isinstance(exc, (exceptions.TransferTimeoutError, exceptions.TimeoutError, TimeoutError)):
        return "CONNECT_TIMEOUT"
    if isinstance(exc, exceptions.TransferFaultError):
        return "CORTEX_M_DEBUG_PORT_LOCKED" if operation == "connect" else "READ_WRITE_FAILED"
    if isinstance(exc, exceptions.TransferError):
        return "CORTEX_M_SWD_PROTOCOL_ERROR" if operation == "connect" else "READ_WRITE_FAILED"
    if isinstance(exc, exceptions.ProbeDisconnected):
        return "DEVICE_DISCONNECTED"
    if isinstance(exc, exceptions.DebugError):
        return _from_debug_error(exc)
    if isinstance(exc, exceptions.ProbeError):
        return _from_probe_error(exc)
    LOG.debug(f"unclassified {type(exc).__name__}: {exc}")
    return UNKNOWN


def _from_chip_state(target, exc: BaseException) -> Optional[str]:
    """What the chip says about itself once an operation on it has failed.

    Every read here can fail in turn — the link that broke the operation is the
    same link this travels over — and a failure means only that this step has
    nothing to add.
    """
    state = _core_state(target)
    if state is Target.State.RESET:
        return "CORTEX_M_TARGET_IN_RESET"
    if state is Target.State.LOCKUP:
        return "CORTEX_M_HARDFAULT_DETECTED"
    if isinstance(exc, exceptions.CoreRegisterAccessError) and state not in (
            None, Target.State.HALTED):
        return "CORTEX_M_TARGET_NOT_HALTED"

    # HFSR is sticky since reset, so it proves a fault happened, not that it
    # happened just now. It is asked last for that reason: every state above is
    # about the chip as it is at this moment.
    hfsr = _read_word(target, HFSR)
    if hfsr:
        LOG.info(f"fault status after failure: HFSR={hfsr:#010x} "
                 f"CFSR={_read_word(target, CFSR) or 0:#010x}")
        return "CORTEX_M_HARDFAULT_DETECTED"
    return None


def _core_state(target):
    core = getattr(target, 'selected_core', None)
    if core is None:
        return None
    try:
        return core.get_state()
    except Exception as e:
        LOG.debug(f"core state unavailable: {e}")
        return None


def _read_word(target, address: int) -> Optional[int]:
    try:
        return target.read32(address)
    except Exception as e:
        LOG.debug(f"read of {address:#010x} failed: {e}")
        return None


def _from_debug_error(exc: BaseException) -> str:
    message = str(exc)
    if NO_CORES_SENTINEL in message:
        return "CORTEX_M_NO_CORE_FOUND"
    if "wire protocol" in message:
        return "PROBE_DRIVER_MISMATCH"
    return UNKNOWN


def _from_probe_error(exc: BaseException) -> str:
    message = str(exc)
    if any(sentinel in message for sentinel in _OLD_FIRMWARE_SENTINELS):
        return "PROBE_FIRMWARE_TOO_OLD"
    return UNKNOWN
