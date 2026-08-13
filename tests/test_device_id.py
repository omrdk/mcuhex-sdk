"""Behavioral tests for asking a chip what it is."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from probe.device_id import STM32F1, identify

# Measured on an STM32F103 over an ST-Link: DEV_ID 0x410, REV_ID 0, and bits
# RM0008 leaves reserved coming back set.
F103_IDCODE = 0x6410
F103_FLASH_KB = 128
F103_UID_WORDS = (0x01990230, 0x52310010, 0x3638414B)


class FakeCore:
    name = "Cortex-M3"


class FakeComponentID:
    def __init__(self, designer, designer_name):
        self.designer = designer
        self.designer_name = designer_name


class FakeAP:
    def __init__(self, cmpid):
        self.rom_table = type("FakeROMTable", (), {"cmpid": cmpid})()


class FakeDP:
    def __init__(self, aps):
        self.aps = aps


class FakeTarget:
    """A target whose memory answers only at the addresses given to it."""

    def __init__(self, memory, core=FakeCore(), aps=None):
        self._memory = memory
        self.selected_core = core
        self.dp = FakeDP(aps if aps is not None else {})

    def read32(self, address):
        return self._at(address, 4)

    def read16(self, address):
        return self._at(address, 2)

    def _at(self, address, width):
        if address not in self._memory:
            raise RuntimeError(f"transfer fault at {address:#010x}")
        return self._memory[address] & ((1 << (width * 8)) - 1)


def f103_memory(flash_kb=F103_FLASH_KB):
    memory = {
        STM32F1["idcode"]: F103_IDCODE,
        STM32F1["flash_size"]: flash_kb,
    }
    for index, word in enumerate(F103_UID_WORDS):
        memory[STM32F1["uid"] + index * 4] = word
    return memory


def test_an_stm32f1_reports_its_family_size_and_revision():
    detected = identify(FakeTarget(f103_memory()))

    assert detected["family"] == "STM32F1"
    assert detected["dev_id"] == 0x410
    assert detected["rev_id"] == 0
    assert detected["flash_size"] == 128 * 1024


def test_the_core_comes_from_what_pyocd_already_read():
    assert identify(FakeTarget(f103_memory()))["core"] == "Cortex-M3"


def test_a_core_pyocd_could_not_name_is_left_unknown():
    core = type("UnnamedCore", (), {"name": "Unknown (CPUID=0x411fc231)"})()

    assert identify(FakeTarget(f103_memory(), core=core))["core"] is None


def test_the_unique_id_reads_most_significant_word_first():
    assert identify(FakeTarget(f103_memory()))["uid"] == "3638414B5231001001990230"


def test_a_part_that_does_not_answer_is_left_unknown():
    """Every identity address faults, as it would on a part from elsewhere."""
    detected = identify(FakeTarget({}))

    assert detected["family"] is None
    assert detected["dev_id"] is None
    assert detected["flash_size"] is None


def test_a_device_id_outside_the_family_is_not_believed():
    """0x440 is an STM32F0, whose DBGMCU is not at this address at all."""
    memory = f103_memory()
    memory[STM32F1["idcode"]] = 0x10010440

    assert identify(FakeTarget(memory))["family"] is None


def test_an_implausible_flash_size_is_dropped_but_the_family_stays():
    memory = f103_memory()
    memory[STM32F1["flash_size"]] = 0xFFFF

    detected = identify(FakeTarget(memory))

    assert detected["family"] == "STM32F1"
    assert detected["flash_size"] is None


def test_a_flash_size_register_reading_zero_is_dropped():
    memory = f103_memory(flash_kb=0)

    assert identify(FakeTarget(memory))["flash_size"] is None


def test_an_unreadable_unique_id_does_not_take_the_family_down():
    memory = f103_memory()
    del memory[STM32F1["uid"] + 8]

    detected = identify(FakeTarget(memory))

    assert detected["family"] == "STM32F1"
    assert detected["uid"] is None


def test_an_arm_rom_table_is_not_reported_as_a_designer():
    """Naming ARM here would read as a vendor claim, and it is not one."""
    aps = {0: FakeAP(FakeComponentID(0x43B, "ARM Ltd"))}

    assert identify(FakeTarget(f103_memory(), aps=aps))["designer"] is None


def test_a_vendor_rom_table_is_reported():
    """What the measured STM32F103 exposes on AP #0."""
    aps = {0: FakeAP(FakeComponentID(0x020, "ST"))}

    assert identify(FakeTarget(f103_memory(), aps=aps))["designer"] == "ST"


def test_a_target_with_no_cores_yet_still_answers():
    detected = identify(FakeTarget(f103_memory(), core=None))

    assert detected["core"] is None
    assert detected["family"] == "STM32F1"
