"""Behavioral tests for asking a chip what it is."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from probe.device_id import ST_DEVICES, UID_BY_FAMILY, identify

F1_IDCODE = 0xE0042000
F0_IDCODE = 0x40015800
H7_IDCODE = 0x5C001000

# Measured on an STM32F103 over an ST-Link: DEV_ID 0x410, REV_ID 0, and bits
# RM0008 leaves reserved coming back set.
F103_IDCODE_VALUE = 0x6410
F103_FLASH_KB = 128
F103_UID_WORDS = (0x01990230, 0x52310010, 0x3638414B)


class FakeCore:
    def __init__(self, name="Cortex-M3"):
        self.name = name


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

    def __init__(self, memory, core=None, aps=None):
        self._memory = memory
        self.selected_core = FakeCore() if core is None else core
        self.dp = FakeDP(aps if aps is not None else {})
        self.reads = []

    def read32(self, address):
        return self._at(address, 4)

    def read16(self, address):
        return self._at(address, 2)

    def _at(self, address, width):
        self.reads.append(address)
        if address not in self._memory:
            raise RuntimeError(f"transfer fault at {address:#010x}")
        return self._memory[address] & ((1 << (width * 8)) - 1)


def f103_memory(flash_kb=F103_FLASH_KB):
    memory = {
        F1_IDCODE: F103_IDCODE_VALUE,
        ST_DEVICES[0x410][1]: flash_kb,
    }
    for index, word in enumerate(F103_UID_WORDS):
        memory[UID_BY_FAMILY['STM32F1'] + index * 4] = word
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
    core = FakeCore("Unknown (CPUID=0x411fc231)")

    assert identify(FakeTarget(f103_memory(), core=core))["core"] is None


def test_an_unnamed_core_is_not_interrogated_at_all():
    """Without a core there is no telling which address holds the device ID, and
    on a part from elsewhere it holds whatever that vendor put there."""
    target = FakeTarget(f103_memory(), core=FakeCore("Unknown (CPUID=0x0)"))

    detected = identify(target)

    assert detected["family"] is None
    assert target.reads == []


def test_the_unique_id_reads_most_significant_word_first():
    assert identify(FakeTarget(f103_memory()))["uid"] == "3638414B5231001001990230"


def test_a_part_that_does_not_answer_is_left_unknown():
    detected = identify(FakeTarget({}))

    assert detected["family"] is None
    assert detected["dev_id"] is None
    assert detected["flash_size"] is None


def test_a_device_id_outside_the_table_is_not_believed():
    memory = f103_memory()
    memory[F1_IDCODE] = 0x10010999

    assert identify(FakeTarget(memory))["family"] is None


def test_a_cortex_m0_is_asked_at_the_address_its_family_uses():
    """An STM32F03x: same core family, different DBGMCU address."""
    memory = {F0_IDCODE: 0x10010444, ST_DEVICES[0x444][1]: 32}

    detected = identify(FakeTarget(memory, core=FakeCore("Cortex-M0+")))

    assert detected["family"] == "STM32F0"
    assert detected["flash_size"] == 32 * 1024


def test_a_cortex_m0_is_not_asked_at_the_cortex_m3_address():
    target = FakeTarget(f103_memory(), core=FakeCore("Cortex-M0"))

    assert identify(target)["family"] is None
    assert F1_IDCODE not in target.reads


def test_a_core_shared_by_two_families_tries_both_addresses():
    """F7 and H7 are both Cortex-M7 and disagree on where IDCODE lives."""
    memory = {H7_IDCODE: 0x10010450, ST_DEVICES[0x450][1]: 2048}

    detected = identify(FakeTarget(memory, core=FakeCore("Cortex-M7")))

    assert detected["family"] == "STM32H7"
    assert detected["flash_size"] == 2048 * 1024


def test_a_family_whose_size_register_is_not_read_still_identifies():
    """The H5 register sits in system flash, which a running firmware can leave
    unreadable, so no size is claimed for it."""
    memory = {0xE0044000: 0x10010484}

    detected = identify(FakeTarget(memory, core=FakeCore("Cortex-M33")))

    assert detected["family"] == "STM32H5"
    assert detected["flash_size"] is None


def test_an_implausible_flash_size_is_dropped_but_the_family_stays():
    memory = f103_memory()
    memory[ST_DEVICES[0x410][1]] = 0xFFFF

    detected = identify(FakeTarget(memory))

    assert detected["family"] == "STM32F1"
    assert detected["flash_size"] is None


def test_a_flash_size_register_reading_zero_is_dropped():
    assert identify(FakeTarget(f103_memory(flash_kb=0)))["flash_size"] is None


def test_an_unreadable_flash_size_does_not_take_the_family_down():
    memory = f103_memory()
    del memory[ST_DEVICES[0x410][1]]

    detected = identify(FakeTarget(memory))

    assert detected["family"] == "STM32F1"
    assert detected["flash_size"] is None


def test_a_family_with_no_confirmed_unique_id_address_reports_none():
    memory = {F0_IDCODE: 0x10010444, ST_DEVICES[0x444][1]: 32}

    assert identify(FakeTarget(memory, core=FakeCore("Cortex-M0+")))["uid"] is None


def test_an_arm_rom_table_is_not_reported_as_a_designer():
    """Naming ARM here would read as a vendor claim, and it is not one."""
    aps = {0: FakeAP(FakeComponentID(0x43B, "ARM Ltd"))}

    assert identify(FakeTarget(f103_memory(), aps=aps))["designer"] is None


def test_a_vendor_rom_table_is_reported():
    """What the measured STM32F103 exposes on AP #0."""
    aps = {0: FakeAP(FakeComponentID(0x020, "ST"))}

    assert identify(FakeTarget(f103_memory(), aps=aps))["designer"] == "ST"


def test_every_family_label_is_usable_as_a_search_query():
    """The label is handed to the chip search as its opening query, so a stray
    one would leave the user staring at an empty list."""
    for dev_id, (family, _) in ST_DEVICES.items():
        assert family.startswith('STM32'), f"{dev_id:#05x}: {family!r}"
        assert family.isalnum(), f"{dev_id:#05x}: {family!r}"
