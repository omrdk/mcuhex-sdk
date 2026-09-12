"""Ask Windows which debug probes are plugged in with no working driver.

pyOCD reaches an ST-Link or a CMSIS-DAP v2 probe through libusb, and libusb
only sees an interface bound to WinUSB. A probe Windows has no driver for is
therefore invisible to every scan the server runs, and the user is left with
an empty device list and no error. Windows itself does see it: the device
sits in the Plug and Play tree with a problem code. This module reads that
tree so the server can list the probe as present but unusable.

Two conditions have to hold before a device is reported, so that a printer
sitting driverless on the same machine never turns into "your probe needs a
driver":

  * Windows reports a driver problem on the node (`DRIVER_PROBLEM_CODES`).
  * pyOCD would list the device if the driver were there. The recognition
    tables are pyOCD's own, imported rather than copied, so they grow with it.

A composite probe (the ST-Link V2-1 on a Nucleo, which also exposes a serial
port and a mass-storage disk) carries the problem on its debug interface node
(`...&MI_00`) while the parent and the sibling interfaces run fine; a plain
dongle carries it on the device node itself. Both shapes were observed on
real hardware and both are reduced to one row keyed by the parent device.
"""
import logging
import platform
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

LOG = logging.getLogger("windows-pnp")

# SEGGER has no entry in pyOCD: J-Link is driven through its own DLL.
SEGGER_VID = 0x1366

# CM_PROB_* values from cfgmgr32.h that all mean "no working driver is bound":
# NOT_CONFIGURED, FAILED_INSTALL, UNSIGNED_DRIVER. Only 28 has been seen on
# hardware; the other two are the same situation reported at a different stage.
DRIVER_PROBLEM_CODES = frozenset({1, 28, 52})

_INSTANCE_ID = re.compile(
    r"^USB\\VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})(?:&MI_[0-9A-Fa-f]{2})?\\", re.ASCII
)

try:
    from pyocd.probe.pydapaccess.interface.common import (
        is_known_cmsis_dap_vid_pid,
        is_known_device_string,
    )
    from pyocd.probe.stlink.usb import STLinkUSBInterface
except ImportError as e:
    LOG.warning(f"pyOCD recognition tables unavailable, driver check disabled: {e}")
    STLinkUSBInterface = None
    is_known_cmsis_dap_vid_pid = None
    is_known_device_string = None


@dataclass(frozen=True)
class PnpDevice:
    """One node of the Windows USB device tree, as Windows describes it."""

    instance_id: str
    description: str
    # 0 while the node runs; a CM_PROB_* code otherwise.
    problem_code: int
    # Set on an interface node of a composite device; None on a device node.
    parent_id: Optional[str] = None


def parse_instance_id(instance_id: str) -> Optional[Tuple[int, int]]:
    """VID and PID out of a USB instance id, or None for anything else."""
    m = _INSTANCE_ID.match(instance_id)
    if not m:
        return None
    return int(m.group(1), 16), int(m.group(2), 16)


def is_probe(vid: int, pid: int, description: str) -> bool:
    """Would pyOCD list this device if its driver were in place?"""
    if STLinkUSBInterface is None:
        return False
    if vid == STLinkUSBInterface.USB_VID and pid in STLinkUSBInterface.USB_PID_EP_MAP:
        return True
    if vid == SEGGER_VID:
        return True
    return is_known_cmsis_dap_vid_pid(vid, pid) or is_known_device_string(description)


def driverless_probes(devices: Iterable[PnpDevice]) -> List[dict]:
    """The probes among `devices` that Windows has no working driver for.

    Pure: takes the tree as data so the decision can be tested anywhere,
    while only `scan` needs Windows.
    """
    rows = {}
    for d in devices:
        if d.problem_code not in DRIVER_PROBLEM_CODES:
            continue
        ids = parse_instance_id(d.instance_id)
        if ids is None:
            continue
        vid, pid = ids
        if not is_probe(vid, pid, d.description):
            continue
        device = d.parent_id or d.instance_id
        rows.setdefault(device, {
            "device": device,
            "description": d.description,
            "vid": vid,
            "pid": pid,
        })
    return list(rows.values())


def scan() -> List[dict]:
    """Driverless probes on this machine; empty anywhere but Windows."""
    if platform.system() != "Windows":
        return []
    try:
        return driverless_probes(_enumerate_usb_tree())
    except Exception as e:
        LOG.debug(f"PnP scan skipped: {e}")
        return []


# --- Windows only below: SetupAPI / CfgMgr32 through ctypes -----------------

def _enumerate_usb_tree() -> List[PnpDevice]:
    import ctypes
    from ctypes import wintypes

    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    cfgmgr = ctypes.WinDLL("cfgmgr32", use_last_error=True)

    DIGCF_PRESENT = 0x02
    DIGCF_ALLCLASSES = 0x04
    DN_HAS_PROBLEM = 0x400
    CR_SUCCESS = 0
    SPDRP_DEVICEDESC = 0
    DEVPROP_TYPE_STRING = 0x12
    MAX_DEVICE_ID_LEN = 200

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("ClassGuid", GUID),
            ("DevInst", wintypes.DWORD),
            ("Reserved", ctypes.c_void_p),
        ]

    class DEVPROPKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    # DEVPKEY_Device_BusReportedDeviceDesc: the product string the device
    # itself reports. A driverless node has no INF to take a friendly name
    # from, so this is the only name Windows has for it.
    bus_reported_desc = DEVPROPKEY(
        GUID(0x540B947E, 0x8B40, 0x45BC, (ctypes.c_ubyte * 8)(0xA8, 0xA2, 0x6A, 0x0B, 0x89, 0x4C, 0xBD, 0xA2)),
        4,
    )

    setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    setupapi.SetupDiEnumDeviceInfo.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
    setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
    ]
    setupapi.SetupDiGetDevicePropertyW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(DEVPROPKEY), ctypes.POINTER(wintypes.ULONG),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD
    ]
    setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
    ]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    cfgmgr.CM_Get_DevNode_Status.argtypes = [ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(wintypes.ULONG), wintypes.DWORD, wintypes.ULONG]
    cfgmgr.CM_Get_Parent.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.ULONG]
    cfgmgr.CM_Get_Device_IDW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.ULONG, wintypes.ULONG]

    def description_of(hdev, info) -> str:
        buf = ctypes.create_unicode_buffer(512)
        prop_type = wintypes.ULONG()
        if setupapi.SetupDiGetDevicePropertyW(
            hdev, ctypes.byref(info), ctypes.byref(bus_reported_desc), ctypes.byref(prop_type),
            buf, ctypes.sizeof(buf), None, 0
        ) and prop_type.value == DEVPROP_TYPE_STRING:
            return buf.value
        if setupapi.SetupDiGetDeviceRegistryPropertyW(
            hdev, ctypes.byref(info), SPDRP_DEVICEDESC, None, buf, ctypes.sizeof(buf), None
        ):
            return buf.value
        return ""

    def device_id_of(devinst) -> str:
        buf = ctypes.create_unicode_buffer(MAX_DEVICE_ID_LEN + 1)
        if cfgmgr.CM_Get_Device_IDW(devinst, buf, len(buf), 0) != CR_SUCCESS:
            return ""
        return buf.value

    devices: List[PnpDevice] = []
    hdev = setupapi.SetupDiGetClassDevsW(None, "USB", None, DIGCF_PRESENT | DIGCF_ALLCLASSES)
    if hdev == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "SetupDiGetClassDevsW failed")
    try:
        index = 0
        while True:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setupapi.SetupDiEnumDeviceInfo(hdev, index, ctypes.byref(info)):
                break
            index += 1

            id_buf = ctypes.create_unicode_buffer(MAX_DEVICE_ID_LEN + 1)
            if not setupapi.SetupDiGetDeviceInstanceIdW(hdev, ctypes.byref(info), id_buf, len(id_buf), None):
                continue
            instance_id = id_buf.value

            status = wintypes.ULONG()
            problem = wintypes.ULONG()
            if cfgmgr.CM_Get_DevNode_Status(ctypes.byref(status), ctypes.byref(problem), info.DevInst, 0) != CR_SUCCESS:
                continue
            problem_code = problem.value if status.value & DN_HAS_PROBLEM else 0

            parent_id = None
            if "&MI_" in instance_id.upper():
                parent = wintypes.DWORD()
                if cfgmgr.CM_Get_Parent(ctypes.byref(parent), info.DevInst, 0) == CR_SUCCESS:
                    parent_id = device_id_of(parent.value) or None

            devices.append(PnpDevice(instance_id, description_of(hdev, info), problem_code, parent_id))
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return devices
