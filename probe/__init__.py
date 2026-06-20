"""Scans and creates communication interfaces."""

import logging

logger = logging.getLogger(__name__)

# Import probe drivers with graceful fallback — some drivers have optional
# native dependencies (numpy, aioserial) that may not be available in all
# environments (e.g., PyInstaller bundles, minimal installs).
_DRIVER_IMPORTS = []

try:
    from .dummyprobe import DummyProbe
    _DRIVER_IMPORTS.append(DummyProbe)
except ImportError as e:
    logger.warning(f"DummyProbe unavailable: {e}")

try:
    from .ocd_g474 import OCD_G4x_Probe
    _DRIVER_IMPORTS.append(OCD_G4x_Probe)
except ImportError as e:
    logger.warning(f"OCD_G4x_Probe unavailable: {e}")

try:
    from .skolbus_ext import SKolbusEx
    _DRIVER_IMPORTS.append(SKolbusEx)
except ImportError as e:
    logger.warning(f"SKolbusEx unavailable: {e}")

try:
    from .ocd_esp32c3 import OCD_ESP32C3_Probe
    _DRIVER_IMPORTS.append(OCD_ESP32C3_Probe)
except ImportError as e:
    logger.warning(f"OCD_ESP32C3_Probe unavailable: {e}")


CLASSES = []

def init_probes():
    """Initialize all the drivers."""
    CLASSES.extend(_DRIVER_IMPORTS)
    logger.info(CLASSES)


def list_probes():
    """Get the list of probes."""
    class_names = []
    for name in CLASSES:
        class_names.append(name.__name__)
    return class_names
