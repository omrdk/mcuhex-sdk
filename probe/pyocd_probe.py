import logging

from pyocd.core.helpers import ConnectHelper
from pyocd.core.session import Session
from pyocd.core.soc_target import SoCTarget

from .debugprobe import DebugProbe
from typing import Optional

LOG = logging.getLogger("pyocd-probe")


class PyOCDProbe(DebugProbe):
    """Generic PyOCD probe — works with ANY ARM Cortex-M target.

    Supports all PyOCD-compatible debug probes (STLink, J-Link, CMSIS-DAP, DAPLink)
    and all ARM Cortex-M targets with CMSIS pack support (STM32, NRF, RP2040, LPC, SAMD, etc.).

    PyOCD auto-detects the target via the debug probe. No hardcoded CMSIS pack needed.
    Use target_override to explicitly specify a target (e.g. 'stm32g474retx').
    """

    def __init__(self, target_override: Optional[str] = None):
        super().__init__()
        self._target_override = target_override
        self._unique_id: Optional[str] = None
        self.session: Optional[Session] = None
        self.target: Optional[SoCTarget] = None

    async def set_port(self, port: str):
        self._unique_id = port

    def set_target_override(self, target_name: Optional[str]):
        """Override the PyOCD target for the next connect() call."""
        self._target_override = target_name or None
        LOG.info(f"Target override set to: {self._target_override}")

    @property
    def target_override(self) -> Optional[str]:
        return self._target_override

    def is_open(self):
        return self.session is not None and self.session.is_open

    async def connect(self) -> bool:
        try:
            options = {
                "connect_mode": "attach",
                "resume_on_disconnect": True,
            }
            kwargs = {
                "blocking": False,
                "auto_open": False,
                "options": options,
            }
            if self._target_override:
                kwargs["target_override"] = self._target_override
            if self._unique_id:
                kwargs["unique_id"] = self._unique_id

            session = ConnectHelper.session_with_chosen_probe(**kwargs)
            if session is None:
                raise RuntimeError("No debug probe found. Connect a debug probe via USB.")

            session.open()
            self.session = session
            self.target = session.target

            probe_name = getattr(session.probe, 'product_name', 'unknown')
            target_name = getattr(session.target, 'part_number', 'unknown')
            LOG.info(f"Connected: probe={probe_name}, target={target_name}")

            return session.is_open
        except Exception as e:
            LOG.error(f"Connect failed: {e}")
            self.session = None
            self.target = None
            raise

    def get_target_info(self) -> dict | None:
        if not self.session or not self.target:
            return None
        return {
            "part_number": getattr(self.target, 'part_number', None),
            "probe_name": getattr(self.session.probe, 'product_name', None),
        }

    async def disconnect(self) -> bool:
        if self.session is None:
            return False
        try:
            self.session.close()
            LOG.info("Session closed")
            is_open = self.session.is_open
            self.session = None
            self.target = None
            return is_open
        except Exception as e:
            LOG.error(f"Disconnect failed: {e}")
            self.session = None
            self.target = None
            raise

    async def read(self, addr, nb: int):
        if self.target is None:
            raise RuntimeError("Not connected to a target")
        ls = self.target.read_memory_block8(addr, nb)
        return bytes(ls)

    async def write(self, addr, data: bytes):
        if self.target is None:
            raise RuntimeError("Not connected to a target")
        self.target.write_memory_block8(addr, data)

    async def write_u32(self, addr, value):
        if self.target is None:
            raise RuntimeError("Not connected to a target")
        self.target.write_memory_block32(addr, [value])

    def list_devices(self) -> list[dict]:
        """List all connected debug probes via PyOCD."""
        try:
            all_probes = ConnectHelper.get_all_connected_probes(blocking=False)
            result = []
            for probe in all_probes:
                probe_dict = {
                    "device": probe.unique_id,
                    "description": getattr(probe, 'description', probe.unique_id),
                    "manufacturer": getattr(probe, 'vendor_name', None),
                    "product": getattr(probe, 'product_name', None),
                }
                result.append(probe_dict)
            return result
        except Exception as e:
            LOG.error(f"Failed to list probes: {e}")
            return []

    def get_driver_list(self):
        return []

    def init_drivers(self):
        LOG.info("PyOCDProbe: no driver init needed")
