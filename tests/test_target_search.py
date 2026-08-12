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


def part(vendor, flash, ram, pack, core="CortexM3", algorithms=True):
    return {
        "vendor": vendor,
        "memories": flash_and_ram(flash, ram),
        "from_pack": {"vendor": "Keil", "pack": pack, "version": "2.4.1"},
        "processors": [{"core": core}],
        "algorithms": [{"file_name": "Flash/x.FLM", "default": True}] if algorithms else [],
    }


INDEX = {
    "STM32F103C8": part("STMicroelectronics:13", 65536, 20480, "STM32F1xx_DFP"),
    "STM32F103RC": part("STMicroelectronics:13", 262144, 49152, "STM32F1xx_DFP"),
    # Matches a query for "s" only as a substring, never as a prefix.
    "ATSAMD21G18A": part("Microchip:3", 262144, 32768, "SAMD21_DFP", core="CortexM0Plus"),
    # An application-profile part: pyOCD cannot attach to it at all.
    "STM32MP157AAA": part("STMicroelectronics:13", 0, 262144, "STM32MP1xx_DFP",
                          core="CortexA7", algorithms=False),
    # M-profile, but its pack ships no flash algorithm.
    "STM32H7A3ZI": part("STMicroelectronics:13", 2097152, 1048576, "STM32H7xx_DFP",
                        core="CortexM7", algorithms=False),
    # A core outside the classification, so its tier stays unknown.
    "XL6600": part("ArmChina:1", 524288, 131072, "StarMC1_DFP", core="StarMC1"),
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
    """Installing that pack leaves pyOCD using its own target, so the pack's
    geometry would describe something that never gets applied."""
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
    assert resp["total"] == 6


def test_every_part_reports_its_size(handler):
    row = search(handler, "stm32f103c8")["results"][0]

    assert (row["flash_size"], row["ram_size"]) == (65536, 20480)


def test_an_application_profile_part_is_reported_unsupported(handler):
    """SWD debug in pyOCD is M-profile only, so offering it would be a dead end."""
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
    """A core we have not judged must not be reported as unsupported: a wrong
    'none' would hide a part the user actually owns."""
    row = search(handler, "xl6600")["results"][0]

    assert row["support"] is None


def test_a_target_that_cannot_be_programmed_is_not_offered():
    """pyOCD's generic target debugs anything and programs nothing."""
    catalogue = CommandHandler(StubProbe())._builtin_targets()

    assert "cortex_m" not in catalogue
    assert "stm32f103rc" in catalogue
