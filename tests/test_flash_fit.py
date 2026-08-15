"""Behavioral tests for refusing an image the target has no room for."""
import sys
from pathlib import Path

import pytest
from intelhex import IntelHex

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import CommandHandler, ErrorCode, ProbeError

FLASH_START = 0x08000000
FLASH_SIZE = 64 * 1024
RAM_START = 0x20000000


class Region:
    def __init__(self, start, size, is_flash=True):
        self.start = start
        self.end = start + size - 1
        self.is_flash = is_flash
        self.is_writable = not is_flash


class MemoryMap:
    """An STM32F103C8: 64K of flash and 20K of RAM, nothing in between."""
    regions = [Region(FLASH_START, FLASH_SIZE), Region(RAM_START, 20 * 1024, is_flash=False)]

    def get_region_for_address(self, address, pname=None):
        return next((r for r in self.regions if r.start <= address <= r.end), None)


def test_the_stub_map_takes_what_the_real_one_takes():
    # _check_image_fits passes the core name positionally. pyOCD only grew that
    # parameter in 0.37, so against an older build every flash dies on the
    # argument count — and a stub free to accept anything says nothing about it.
    import inspect
    from pyocd.core.memory_map import MemoryMap as PyocdMemoryMap

    real = inspect.signature(PyocdMemoryMap.get_region_for_address).parameters
    stub = inspect.signature(MemoryMap.get_region_for_address).parameters

    assert list(stub) == list(real)


class StubProbe:
    def is_open(self):
        return False


@pytest.fixture
def handler():
    return CommandHandler(StubProbe())


def check(handler, image):
    handler._check_image_fits(MemoryMap(), 'core0', image)


def write_hex(tmp_path, chunks):
    hexfile = IntelHex()
    for address, size in chunks:
        for offset in range(size):
            hexfile[address + offset] = 0xAA
    path = tmp_path / "firmware.hex"
    hexfile.write_hex_file(str(path))
    return str(path)


def test_an_image_inside_flash_is_accepted(handler):
    check(handler, [(FLASH_START, FLASH_SIZE)])


def test_an_image_running_past_the_end_of_flash_is_refused(handler):
    """pyOCD drops such chunks with a log warning for hex and ELF files, so
    without this the flash would report success and lose the tail."""
    with pytest.raises(ProbeError) as excinfo:
        check(handler, [(FLASH_START, FLASH_SIZE + 1)])

    assert excinfo.value.error_code == ErrorCode.FLASH_IMAGE_DOES_NOT_FIT


def test_an_image_at_an_address_the_target_does_not_have_is_refused(handler):
    with pytest.raises(ProbeError):
        check(handler, [(0x08100000, 16)])


def test_the_gap_between_flash_and_ram_is_not_programmable(handler):
    with pytest.raises(ProbeError):
        check(handler, [(0x10000000, 16)])


def test_a_binary_is_measured_from_the_boot_address(handler, tmp_path):
    path = tmp_path / "firmware.bin"
    path.write_bytes(b"\xAA" * 1024)

    assert handler._image_ranges(str(path), FLASH_START) == [(FLASH_START, 1024)]


def test_a_hex_file_is_measured_by_the_bytes_it_carries(handler, tmp_path):
    """Its size on disk is roughly three times its payload, and it is sparse."""
    path = write_hex(tmp_path, [(FLASH_START, 256), (FLASH_START + 0x1000, 128)])

    assert handler._image_ranges(path, FLASH_START) == [
        (FLASH_START, 256),
        (FLASH_START + 0x1000, 128),
    ]


def test_a_sparse_hex_file_beyond_flash_is_refused(handler, tmp_path):
    path = write_hex(tmp_path, [(FLASH_START, 256), (FLASH_START + FLASH_SIZE, 16)])

    with pytest.raises(ProbeError):
        check(handler, handler._image_ranges(path, FLASH_START))
