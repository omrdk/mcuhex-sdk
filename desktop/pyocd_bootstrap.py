"""Pre-import and register pyocd probe plugins for frozen PyInstaller apps.

PyOCD discovers probe drivers via importlib entry_points, which breaks in
frozen binaries because package metadata isn't available. This module bypasses
that by directly importing probe classes and registering them in pyocd's
plugin registry (PROBE_CLASSES dict in pyocd.probe.aggregator).
"""

import logging
import sys

LOG = logging.getLogger(__name__)


def bootstrap_pyocd():
    """Register pyocd probe plugins directly, bypassing entry_points discovery.

    Must be called before any code that invokes
    DebugProbeAggregator.get_all_connected_probes() or similar.
    """
    if not getattr(sys, 'frozen', False):
        return  # Not frozen — let pyocd use normal entry_points

    try:
        from pyocd.probe.aggregator import PROBE_CLASSES

        # ST-Link (STM32, most common)
        try:
            from pyocd.probe.stlink_probe import StlinkProbe
            PROBE_CLASSES['stlink'] = StlinkProbe
        except ImportError:
            LOG.warning("Failed to import StlinkProbe")

        # J-Link (Segger)
        try:
            from pyocd.probe.jlink_probe import JLinkProbe
            PROBE_CLASSES['jlink'] = JLinkProbe
        except ImportError:
            LOG.warning("Failed to import JLinkProbe")

        # CMSIS-DAP (generic, DAPLink)
        try:
            from pyocd.probe.cmsis_dap_probe import CMSISDAPProbe
            PROBE_CLASSES['cmsisdap'] = CMSISDAPProbe
        except ImportError:
            LOG.warning("Failed to import CMSISDAPProbe")

        # Picoprobe (RP2040-based)
        try:
            from pyocd.probe.picoprobe import PicoprobeProbe
            PROBE_CLASSES['picoprobe'] = PicoprobeProbe
        except ImportError:
            LOG.warning("Failed to import PicoprobeProbe")

        LOG.info(f"PyOCD bootstrap: registered {len(PROBE_CLASSES)} probe(s): "
                 f"{list(PROBE_CLASSES.keys())}")

    except ImportError as e:
        LOG.error(f"PyOCD bootstrap failed: {e}")
