"""Behavioral tests for telling a debuggable target from a flashable one."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from probe.pyocd_probe import PyOCDProbe


class FakeMemoryMap:
    def __init__(self, boot):
        self._boot = boot

    def get_boot_memory(self):
        return self._boot


class UnreadableMemoryMap:
    def get_boot_memory(self):
        raise RuntimeError("no cores discovered")


class FakeProbe:
    product_name = "ST-Link"


class FakeSession:
    probe = FakeProbe()


def probe_with(memory_map):
    p = PyOCDProbe()
    p.session = FakeSession()
    p.target = type("FakeTarget", (), {"part_number": "STM32F103C8", "memory_map": memory_map})()
    return p


def test_a_target_with_flash_geometry_can_program():
    info = probe_with(FakeMemoryMap(boot=object())).get_target_info()

    assert info["can_program"] is True


def test_the_generic_target_cannot_program():
    """pyOCD's fallback target debugs fine but has no flash region."""
    info = probe_with(FakeMemoryMap(boot=None)).get_target_info()

    assert info["can_program"] is False


def test_an_unreadable_map_is_not_reported_as_flashable():
    info = probe_with(UnreadableMemoryMap()).get_target_info()

    assert info["can_program"] is False


def test_no_session_reports_no_target_at_all():
    assert PyOCDProbe().get_target_info() is None


def test_a_target_that_answers_nothing_still_reports_a_detection_shape():
    """Callers read the fields directly, so the keys have to be there even when
    the target cannot be interrogated at all."""
    detected = probe_with(FakeMemoryMap(boot=object())).get_target_info()["detected"]

    assert detected["family"] is None
    assert detected["core"] is None
