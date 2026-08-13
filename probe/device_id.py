"""Ask a live target what it is, using only reads that cannot change it.

Nothing here writes, halts or resets. Identification runs right after attaching
to a board that is already running its firmware, so it has to leave that
firmware alone.

Every field is optional and absent means unknown, never a negative: a caller
that cannot tell what the chip is must behave exactly as it did before.
"""
import logging
from typing import Any, Dict, Optional

LOG = logging.getLogger("device-id")

# A read that returns more than this is not a flash size register.
MAX_PLAUSIBLE_FLASH_KB = 16 * 1024

# The debug port is designed by ARM on every standard SW-DP, so finding ARM
# here says nothing about who made the silicon.
ARM_DESIGNER = 0x43B

# Addresses come from the vendor's own description of the part:
#   idcode      ST's STM32F103xx.svd (shipped in pyocd/debug/svd/svd_data.zip),
#               DBG peripheral baseAddress
#   dev_ids     RM0008 §33.6.1, cross-checked against blackmagic's stm32f1.c
#   flash_size  RM0008 §30.1.1, half word, in KB
#   uid         RM0008 §30.2
STM32F1 = {
    "family": "STM32F1",
    "idcode": 0xE0042000,
    "dev_ids": frozenset({0x410, 0x412, 0x414, 0x418, 0x420, 0x428, 0x430}),
    "flash_size": 0x1FFFF7E0,
    "uid": 0x1FFFF7E8,
}

FAMILIES = (STM32F1,)


def identify(target) -> Dict[str, Any]:
    """What the chip says about itself, as far as it can be established."""
    detected: Dict[str, Any] = {
        "core": _core_name(target),
        "designer": _designer(target),
        "family": None,
        "dev_id": None,
        "rev_id": None,
        "flash_size": None,
        "uid": None,
    }
    for family in FAMILIES:
        answer = _interrogate(target, family)
        if answer:
            detected.update(answer)
            break
    return detected


def _core_name(target) -> Optional[str]:
    """The core pyOCD already resolved from CPUID while connecting.

    pyOCD falls back to a string carrying the raw CPUID when it does not
    recognise the part, which is a debugging aid rather than a core name.
    """
    core = getattr(target, 'selected_core', None)
    name = getattr(core, 'name', None)
    if not name or name.startswith("Unknown"):
        return None
    return name


def _designer(target) -> Optional[str]:
    """The JEP106 designer of the first CoreSight ROM table that is not ARM's.

    Reported for the record only — most Cortex-M parts expose ARM here and
    nothing else, so this cannot be the thing that identifies a vendor.
    """
    dp = getattr(target, 'dp', None)
    for ap in getattr(dp, 'aps', {}).values():
        rom = getattr(ap, 'rom_table', None)
        cmpid = getattr(rom, 'cmpid', None)
        if cmpid is None or cmpid.designer == ARM_DESIGNER:
            continue
        return cmpid.designer_name or f"{cmpid.designer:#03x}"
    return None


def _interrogate(target, family: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Read one family's identity registers, or nothing if they do not answer.

    The device ID has to land in the family's own published set before any of
    it is believed: on a part from another vendor these addresses belong to
    whatever that vendor put there, and a stray value read as a chip identity
    is worse than admitting we do not know.
    """
    raw = _read32(target, family["idcode"])
    if raw is None:
        return None
    dev_id = raw & 0xFFF
    if dev_id not in family["dev_ids"]:
        LOG.debug(f"{family['family']}: dev_id {dev_id:#05x} is not one of ours")
        return None

    answer = {
        "family": family["family"],
        "dev_id": dev_id,
        "rev_id": (raw >> 16) & 0xFFFF,
    }

    size_kb = _read16(target, family["flash_size"])
    if size_kb is not None and 0 < size_kb <= MAX_PLAUSIBLE_FLASH_KB:
        answer["flash_size"] = size_kb * 1024
    elif size_kb is not None:
        LOG.debug(f"{family['family']}: implausible flash size {size_kb} KB")

    words = [_read32(target, family["uid"] + offset) for offset in (0, 4, 8)]
    if all(w is not None for w in words):
        answer["uid"] = "".join(f"{w:08X}" for w in reversed(words))

    return answer


def _read32(target, address: int) -> Optional[int]:
    return _read(target, 'read32', address)


def _read16(target, address: int) -> Optional[int]:
    return _read(target, 'read16', address)


def _read(target, method: str, address: int) -> Optional[int]:
    """A read that may legitimately fail.

    An address the part does not implement faults, and pyOCD clears the sticky
    error itself, so asking and being refused costs the session nothing.
    """
    try:
        return getattr(target, method)(address)
    except Exception as e:
        LOG.debug(f"read of {address:#010x} failed: {e}")
        return None
