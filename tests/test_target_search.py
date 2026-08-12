"""Behavioral tests for what the chip search offers and in what order."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import CommandHandler


def flash_and_ram(flash_size, ram_size):
    return {
        "IROM1": {"size": flash_size, "startup": True,
                  "access": {"read": True, "write": False, "execute": True}},
        "IRAM1": {"size": ram_size, "default": True,
                  "access": {"read": True, "write": True, "execute": False}},
    }


def region(size, start=0, startup=False, default=False,
           read=True, write=False, execute=False, peripheral=False):
    return {
        "size": size, "start": start, "startup": startup, "default": default,
        "access": {"read": read, "write": write, "execute": execute,
                   "peripheral": peripheral},
    }


def part(vendor, flash, ram, pack, core="CortexM3", algorithms=True, memories=None,
         cores=None):
    return {
        "vendor": vendor,
        "memories": flash_and_ram(flash, ram) if memories is None else memories,
        "from_pack": {"vendor": "Keil", "pack": pack, "version": "2.4.1"},
        "processors": [{"core": c} for c in (cores or [core])],
        "algorithms": [{"file_name": "Flash/x.FLM", "default": True}] if algorithms else [],
    }


INDEX = {
    "STM32F103C8": part("STMicroelectronics:13", 65536, 20480, "STM32F1xx_DFP"),
    "STM32F103RC": part("STMicroelectronics:13", 262144, 49152, "STM32F1xx_DFP"),
    # Matches a query for "s" only as a substring, never as a prefix.
    "ATSAMD21G18A": part("Microchip:3", 262144, 32768, "SAMD21_DFP", core="CortexM0Plus"),
    # Application-profile: pyOCD cannot attach to it at all.
    "STM32MP157AAA": part("STMicroelectronics:13", 0, 262144, "STM32MP1xx_DFP",
                          core="CortexA7", algorithms=False),
    # M-profile, but its pack ships no flash algorithm.
    "STM32H7A3ZI": part("STMicroelectronics:13", 2097152, 1048576, "STM32H7xx_DFP",
                        core="CortexM7", algorithms=False),
    # Outside the classification, so its tier stays unknown.
    "XL6600": part("ArmChina:1", 524288, 131072, "StarMC1_DFP", core="StarMC1"),
    # Microchip's style: flash marked writable, beside a huge peripheral window.
    "ATSAMD11D14AU": part("Microchip:3", 0, 0, "SAMD11_DFP", core="CortexM0Plus", memories={
        "FLASH": region(16384, startup=True, default=True, write=True, execute=True),
        "HMCRAMC0": region(4096, start=0x20000000, default=True, write=True, execute=True),
        "PPB": region(1048576, start=0xE0000000, write=True, peripheral=True),
    }),
    # Analog Devices' style: the boot region is not marked executable at all.
    "ADUCM355": part("Analog Devices:1", 0, 0, "ADuCM355_DFP", memories={
        "IROM1": region(131072, startup=True, default=True, write=True),
        "dRAM": region(8192, start=0x20000000, default=True, write=True),
    }),
    # One flash mapped twice; the boot alias is the lower address.
    "GD32W515TGQ6": part("GigaDevice:1", 0, 0, "GD32W51x_DFP", core="CortexM33", memories={
        "IROM2": region(1048576, start=0x0C000000, startup=True, execute=True),
        "IROM1": region(1048576, start=0x08000000, startup=True, execute=True),
        "IRAM1": region(262144, start=0x20000000, default=True, write=True),
    }),
    # Flashless: boots from an external chip, so there is no size to report.
    "MIMXRT1052": part("NXP:11", 0, 0, "MIMXRT1052_DFP", core="CortexM7", memories={
        "IRAM1": region(393216, start=0x20000000, default=True, write=True),
    }),
    # An i.MX pairs an application core with a Cortex-M and lists the A first.
    "MCIMX6X1": part("NXP:11", 131072, 32768, "iMX6SX_DFP",
                     cores=["CortexA9", "CortexM4"], algorithms=False),
}

BUILTINS = {
    "stm32f103rc": {"name": "stm32f103rc", "vendor": "STMicroelectronics",
                    "flash_size": 524288, "ram_size": 65536},
}


class StubProbe:
    def is_open(self):
        return False


class FakeCache:
    index = INDEX

    def cache_descriptors(self):
        pass


@pytest.fixture
def handler(monkeypatch):
    h = CommandHandler(StubProbe())
    h._pack_cache = FakeCache()
    h._builtin_target_cache = BUILTINS
    monkeypatch.setattr(CommandHandler, "_get_installed_target_names", lambda self: set())
    return h


def search(handler, query, limit=10):
    return asyncio.run(
        handler.execute_command({"cmd": "search_targets", "query": query, "limit": limit, "id": 1})
    )


def test_a_builtin_target_needs_no_download(handler):
    row = search(handler, "stm32f103rc")["results"][0]

    assert row["source"] == "builtin"
    assert row["installed"] is True
    assert row["pack"] is None


def test_the_builtin_hides_the_pack_of_the_same_name(handler):
    """pyOCD keeps using its own target, so the pack's geometry never applies."""
    resp = search(handler, "stm32f103rc")

    assert resp["total"] == 1
    assert resp["results"][0]["flash_size"] == 524288


def test_a_prefix_match_outranks_a_substring_match(handler):
    names = [r["name"] for r in search(handler, "s")["results"]]

    assert names[0].lower().startswith("s")
    assert names[-1] == "ATSAMD21G18A"


def test_the_exact_part_comes_first(handler):
    assert search(handler, "stm32f103c8")["results"][0]["name"] == "STM32F103C8"


def test_the_total_counts_matches_the_page_left_out(handler):
    resp = search(handler, "", limit=2)

    assert len(resp["results"]) == 2
    # The built-in shadows the pack row of the same name, so the count is the
    # index minus that row, plus the built-in.
    assert resp["total"] == len(INDEX) - 1 + len(BUILTINS)


def test_every_part_reports_its_size(handler):
    row = search(handler, "stm32f103c8")["results"][0]

    assert (row["flash_size"], row["ram_size"]) == (65536, 20480)


def sizes(handler, query):
    row = search(handler, query)["results"][0]
    return row["flash_size"], row["ram_size"]


def test_writable_flash_is_not_mistaken_for_ram(handler):
    """Marking flash writable used to lose its size and report it as the RAM."""
    assert sizes(handler, "atsamd11d14au") == (16384, 4096)


def test_a_peripheral_window_is_never_reported_as_ram(handler):
    _, ram = sizes(handler, "atsamd11d14au")

    assert ram != 1048576


def test_a_boot_region_counts_as_flash_even_when_not_executable(handler):
    assert sizes(handler, "aducm355") == (131072, 8192)


def test_the_lower_alias_of_a_doubly_mapped_flash_wins(handler):
    """Index order used to decide, so identical parts reported different sizes."""
    assert sizes(handler, "gd32w515tgq6") == (1048576, 262144)


def test_a_flashless_part_reports_no_flash_size(handler):
    """A size taken from the flash algorithm would be the family's maximum."""
    assert sizes(handler, "mimxrt1052") == (None, 393216)


def test_an_application_profile_part_is_reported_unsupported(handler):
    """pyOCD debugs M-profile only, so offering it would be a dead end."""
    row = search(handler, "stm32mp157aaa")["results"][0]

    assert (row["support"], row["core"]) == ("none", "CortexA7")


def test_a_part_without_a_flash_algorithm_is_monitor_only(handler):
    row = search(handler, "stm32h7a3zi")["results"][0]

    assert row["support"] == "monitor"


def test_a_part_with_a_flash_algorithm_can_be_flashed(handler):
    row = search(handler, "stm32f103c8")["results"][0]

    assert row["support"] == "flash"


def test_a_builtin_target_can_be_flashed(handler):
    row = search(handler, "stm32f103rc")["results"][0]

    assert row["support"] == "flash"


def test_an_unclassified_core_is_left_unknown(handler):
    """A wrong 'none' would hide a part the user owns."""
    row = search(handler, "xl6600")["results"][0]

    assert row["support"] is None


def test_a_target_that_cannot_be_programmed_is_not_offered():
    """pyOCD's generic target debugs anything and programs nothing."""
    catalogue = CommandHandler(StubProbe())._builtin_targets()

    assert "cortex_m" not in catalogue
    assert "stm32f103rc" in catalogue


def test_a_split_flash_is_reported_whole():
    """One flash spans several regions when its sectors differ in size, and the
    boot region alone reported an eighth of an LPC1768."""
    catalogue = CommandHandler(StubProbe())._builtin_targets()

    assert catalogue["lpc1768"]["flash_size"] == 512 * 1024
    assert catalogue["stm32f429xi"]["flash_size"] == 2 * 1024 * 1024


def test_a_separate_flash_is_not_added_to_the_boot_one():
    """Nuvoton's LDROM begins exactly where APROM ends but is its own memory."""
    catalogue = CommandHandler(StubProbe())._builtin_targets()

    assert catalogue["m2354kjfae"]["flash_size"] == 1024 * 1024


def test_a_disputed_flash_size_carries_the_other_claim(handler):
    """pyOCD gives STM32F103RC the RE's 512K, the pack says 256K, and on other
    parts the pack is the wrong one — so neither is presented as the fact."""
    row = search(handler, "stm32f103rc")["results"][0]

    assert (row["flash_size"], row["flash_size_alt"]) == (524288, 262144)


def test_an_undisputed_size_carries_no_second_claim(handler):
    assert search(handler, "stm32f103c8")["results"][0]["flash_size_alt"] is None


def test_a_part_is_judged_by_its_m_profile_core(handler):
    """i.MX parts list the application core first, and pyOCD attaches to the M."""
    row = search(handler, "mcimx6x1")["results"][0]

    assert (row["core"], row["support"]) == ("CortexM4", "monitor")
