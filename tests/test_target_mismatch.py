"""Behavioral tests for refusing a target the attached chip contradicts."""
import sys
from pathlib import Path

import pytest
from pyocd.core.memory_map import MemoryType

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import CommandHandler, ErrorCode, ProbeError

# What the measured STM32F103 reports through its own flash size register.
F103_FLASH = 128 * 1024


class Region:
    def __init__(self, length, type=MemoryType.FLASH, alias=None):
        self.length = length
        self.type = type
        self.alias = alias


class MemoryMap:
    def __init__(self, regions):
        self._regions = regions

    def iter_matching_regions(self, **kwargs):
        for region in self._regions:
            if all(getattr(region, k, None) == v for k, v in kwargs.items()):
                yield region


class StubProbe:
    def __init__(self, detected):
        self._detected = detected

    def is_open(self):
        return False

    def get_target_info(self):
        return {"detected": self._detected}


def handler_for(flash_size):
    return CommandHandler(StubProbe({"family": "STM32F1", "dev_id": 0x410,
                                     "flash_size": flash_size}))


def check(flash_size, regions):
    handler_for(flash_size)._check_target_matches_chip(MemoryMap(regions))


def test_a_target_matching_the_chip_is_accepted():
    check(F103_FLASH, [Region(F103_FLASH)])


def test_a_target_claiming_more_flash_than_the_chip_has_is_refused():
    """Picking stm32f429xi with an F103 attached: 2 MB against 128 KB."""
    with pytest.raises(ProbeError) as excinfo:
        check(F103_FLASH, [Region(2048 * 1024)])

    assert excinfo.value.error_code == ErrorCode.FLASH_TARGET_MISMATCH


def test_the_refusal_names_both_sizes_and_the_device_id():
    with pytest.raises(ProbeError) as excinfo:
        check(F103_FLASH, [Region(2048 * 1024)])

    message = str(excinfo.value)
    assert "128 KB" in message
    assert "2048 KB" in message
    assert "0x410" in message


def test_a_target_claiming_less_flash_than_the_chip_has_is_accepted():
    """Conservative, and the usual case: a 64 KB target on a 128 KB die."""
    check(F103_FLASH, [Region(64 * 1024)])


def test_split_flash_is_counted_as_a_whole():
    """Banks are separate regions, so judging one of them would let a target
    four times the chip's size through."""
    with pytest.raises(ProbeError):
        check(F103_FLASH, [Region(128 * 1024), Region(128 * 1024), Region(128 * 1024)])


def test_an_aliased_region_is_not_counted_twice():
    """Some parts map the same flash at a second address, which is not extra
    room and would refuse a target that actually fits."""
    check(F103_FLASH, [Region(F103_FLASH), Region(F103_FLASH, alias="flash")])


def test_ram_is_not_counted_as_flash():
    check(F103_FLASH, [Region(F103_FLASH), Region(1024 * 1024, type=MemoryType.RAM)])


def test_an_unidentified_chip_never_blocks_a_flash():
    check(None, [Region(2048 * 1024)])


def test_a_probe_that_reports_no_detection_at_all_never_blocks_a_flash():
    """An older SDK, or a probe family that cannot be interrogated."""
    handler = CommandHandler(StubProbe(None))

    handler._check_target_matches_chip(MemoryMap([Region(2048 * 1024)]))
