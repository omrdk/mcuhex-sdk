"""Scans and creates communication interfaces."""

import logging
from .dummyprobe import DummyProbe
from .ocd_g474 import OCD_G4x_Probe
from .skolbus_ext import SKolbusEx

# __author__ = 'dak'
# __all__ = []

logger = logging.getLogger(__name__)

CLASSES = []

def init_probes():
    """Initialize all the drivers."""
    CLASSES.extend([DummyProbe, OCD_G4x_Probe, SKolbusEx])
    logger.info(CLASSES)


def get_probe_list():
    """Get the list of probes."""
    class_names = []
    for name in CLASSES:
        class_names.append(name.__name__)
    return class_names
