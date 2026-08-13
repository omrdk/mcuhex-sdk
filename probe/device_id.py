"""Ask a live target what it is, using only reads that cannot change it.

Nothing here writes, halts or resets. Identification runs right after attaching
to a board that is already running its firmware, so it has to leave that
firmware alone.

Every field is optional and absent means unknown, never a negative: a caller
that cannot tell what the chip is must behave exactly as it did before.

SOURCES. No address below was written from memory. Each one had to appear,
identically, in two projects that read these registers independently:

  * stlink-org/stlink at 5ec9d51 — config/chips/*.chip gives device ID to flash
    size register per die group and cites the ST reference manual for each;
    stlink_chip_id() in src/stlink-lib/common_legacy.c gives the IDCODE address
    per core, with RM section numbers.
  * openocd-org/openocd master — src/flash/nor/stm32{f1x,f2x,h7x,l4x,lx}.c,
    device tables keyed by the same device IDs.
  * blackmagic-debug/blackmagic main — src/target/stm32*.c, a third reading of
    the same registers, used where the first two disagree in shape.

Where only one project knows a part, the part is left out and identification
stays silent for it: a wrong size here would refuse a flash that should have
been allowed. That excludes STM32C5 (RM0522), which only stlink carries, and
the STM32WB0/WL3 parts, which have no DBGMCU_IDCODE at all and are identified
through the JTAG ID instead — a path this module does not implement.
"""
import logging
from typing import Any, Dict, Optional, Tuple

LOG = logging.getLogger("device-id")

# stlink treats anything above this as a bogus read of the flash size register.
MAX_PLAUSIBLE_FLASH_KB = 8 * 1024

# The debug port is designed by ARM on every standard SW-DP, so finding ARM
# here says nothing about who made the silicon.
ARM_DESIGNER = 0x43B

# Where DBGMCU_IDCODE sits, by the core pyOCD already read out of CPUID. ST
# moved the register between families and the core is what distinguishes them;
# both reference projects select it exactly this way. Cortex-M7 and M33 carry
# two candidates because two families share the core and disagree on the
# address, so both are tried and the device ID decides which answered.
IDCODE_BY_CORE = {
    'Cortex-M0': (0x40015800,),
    'Cortex-M0+': (0x40015800,),
    'Cortex-M23': (0x40015800,),
    'Cortex-M3': (0xE0042000,),
    'Cortex-M4': (0xE0042000,),
    'Cortex-M7': (0xE0042000, 0x5C001000),
    'Cortex-M33': (0xE0044000, 0x44024000),
}

# device ID -> (family, flash size register). The comment is the die group the
# ID actually identifies: it is not a part number, and several parts in
# different packages share one ID. A None register means the family is
# recognised but its size is not read — see STM32H5 below.
ST_DEVICES: Dict[int, Tuple[str, Optional[int]]] = {
    0x443: ('STM32C0', 0x1FFF75A0),   # C011
    0x44C: ('STM32C0', 0x1FFF75A0),   # C051
    0x44D: ('STM32C0', 0x1FFF75A0),   # C091/C092
    0x453: ('STM32C0', 0x1FFF75A0),   # C031
    0x493: ('STM32C0', 0x1FFF75A0),   # C071
    0x440: ('STM32F0', 0x1FFFF7CC),   # F05x
    0x442: ('STM32F0', 0x1FFFF7CC),   # F09x
    0x444: ('STM32F0', 0x1FFFF7CC),   # F03x
    0x445: ('STM32F0', 0x1FFFF7CC),   # F04x
    0x448: ('STM32F0', 0x1FFFF7CC),   # F07x
    0x410: ('STM32F1', 0x1FFFF7E0),   # F1 medium density
    0x412: ('STM32F1', 0x1FFFF7E0),   # F1 low density
    0x414: ('STM32F1', 0x1FFFF7E0),   # F1 high density
    0x418: ('STM32F1', 0x1FFFF7E0),   # F1 connectivity line
    0x420: ('STM32F1', 0x1FFFF7E0),   # F1 value line, low/medium density
    0x428: ('STM32F1', 0x1FFFF7E0),   # F1 value line, high density
    0x430: ('STM32F1', 0x1FFFF7E0),   # F1 XL density
    0x411: ('STM32F2', 0x1FFF7A22),   # F2xx
    0x422: ('STM32F3', 0x1FFFF7CC),   # F302/F303/F358
    0x432: ('STM32F3', 0x1FFFF7CC),   # F37x
    0x438: ('STM32F3', 0x1FFFF7CC),   # F303x6/8, F328
    0x439: ('STM32F3', 0x1FFFF7CC),   # F301, F302x6/8, F318
    0x446: ('STM32F3', 0x1FFFF7CC),   # F302xD/E, F303xD/E, F398
    0x413: ('STM32F4', 0x1FFF7A22),   # F405/F407/F415/F417
    0x419: ('STM32F4', 0x1FFF7A22),   # F42x/F43x
    0x421: ('STM32F4', 0x1FFF7A22),   # F446
    0x423: ('STM32F4', 0x1FFF7A22),   # F401xB/xC
    0x431: ('STM32F4', 0x1FFF7A22),   # F411xC/xE
    0x433: ('STM32F4', 0x1FFF7A22),   # F401xD/xE
    0x434: ('STM32F4', 0x1FFF7A22),   # F469/F479
    0x441: ('STM32F4', 0x1FFF7A22),   # F412
    0x458: ('STM32F4', 0x1FFF7A22),   # F410
    0x463: ('STM32F4', 0x1FFF7A22),   # F413/F423
    0x449: ('STM32F7', 0x1FF0F442),   # F74x/F75x
    0x451: ('STM32F7', 0x1FF0F442),   # F76x/F77x
    0x452: ('STM32F7', 0x1FF07A22),   # F72x/F73x — not 0x1FF0F442, per RM0431
    0x456: ('STM32G0', 0x1FFF75E0),   # G05x/G06x
    0x460: ('STM32G0', 0x1FFF75E0),   # G07x/G08x
    0x466: ('STM32G0', 0x1FFF75E0),   # G03x/G04x
    0x467: ('STM32G0', 0x1FFF75E0),   # G0Bx/G0Cx
    0x468: ('STM32G4', 0x1FFF75E0),   # G43x/G44x
    0x469: ('STM32G4', 0x1FFF75E0),   # G47x/G48x
    0x479: ('STM32G4', 0x1FFF75E0),   # G49x/G4Ax
    # The H5 flash size register lives in the system flash information block,
    # which a running firmware can leave unreadable. stlink recovers by halting
    # the core at its reset vector; identification must not disturb a running
    # board, so the family is reported without a size and the size gate that
    # depends on it simply does not arm.
    0x484: ('STM32H5', None),         # H5xx
    0x450: ('STM32H7', 0x1FF1E880),   # H74x/H75x
    0x480: ('STM32H7', 0x08FFF80C),   # H7Ax/H7Bx
    0x483: ('STM32H7', 0x1FF1E880),   # H72x/H73x
    0x417: ('STM32L0', 0x1FF8007C),   # L0 category 3
    0x425: ('STM32L0', 0x1FF8007C),   # L0 category 2
    0x447: ('STM32L0', 0x1FF8007C),   # L0 category 5
    0x457: ('STM32L0', 0x1FF8007C),   # L0 category 1
    0x416: ('STM32L1', 0x1FF8004C),   # L1 category 1
    0x429: ('STM32L1', 0x1FF8004C),   # L1 category 2
    0x427: ('STM32L1', 0x1FF800CC),   # L1 category 3
    0x436: ('STM32L1', 0x1FF800CC),   # L1 category 4
    0x437: ('STM32L1', 0x1FF800CC),   # L1 category 5
    0x415: ('STM32L4', 0x1FFF75E0),   # L47x/L48x
    0x435: ('STM32L4', 0x1FFF75E0),   # L43x/L44x
    0x461: ('STM32L4', 0x1FFF75E0),   # L496/L4A6
    0x462: ('STM32L4', 0x1FFF75E0),   # L45x/L46x
    0x464: ('STM32L4', 0x1FFF75E0),   # L41x/L42x
    0x470: ('STM32L4', 0x1FFF75E0),   # L4Rx/L4Sx
    0x471: ('STM32L4', 0x1FFF75E0),   # L4Px/L4Qx
    0x472: ('STM32L5', 0x0BFA05E0),   # L552/L562
    0x459: ('STM32U0', 0x1FFF3EA0),   # U031
    0x489: ('STM32U0', 0x1FFF6EA0),   # U073/U083
    0x455: ('STM32U5', 0x0BFA07A0),   # U535/U545
    0x476: ('STM32U5', 0x0BFA07A0),   # U5Fx/U5Gx
    0x481: ('STM32U5', 0x0BFA07A0),   # U59x/U5Ax
    0x482: ('STM32U5', 0x0BFA07A0),   # U575/U585
    0x495: ('STM32WB', 0x1FFF75E0),   # WBx0/WBx5
    0x497: ('STM32WL', 0x1FFF75E0),   # WLEx
}

# The 96-bit unique ID moved between families too, and no second source pins it
# per family — so it is read only where it has been confirmed on hardware.
UID_BY_FAMILY = {
    'STM32F1': 0x1FFFF7E8,
}


def identify(target) -> Dict[str, Any]:
    """What the chip says about itself, as far as it can be established."""
    core = _core_name(target)
    detected: Dict[str, Any] = {
        "core": core,
        "designer": _designer(target),
        "family": None,
        "dev_id": None,
        "rev_id": None,
        "flash_size": None,
        "uid": None,
    }
    answer = _interrogate(target, core)
    if answer:
        detected.update(answer)
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

    ST's own CMSIS pack rejects a board whose ROM table does not carry ST's
    code, so this is the check the vendor itself trusts for provenance. It says
    nothing about which part it is, and plenty of parts expose only ARM here.
    """
    dp = getattr(target, 'dp', None)
    for ap in getattr(dp, 'aps', {}).values():
        rom = getattr(ap, 'rom_table', None)
        cmpid = getattr(rom, 'cmpid', None)
        if cmpid is None or cmpid.designer == ARM_DESIGNER:
            continue
        return cmpid.designer_name or f"{cmpid.designer:#03x}"
    return None


def _interrogate(target, core: Optional[str]) -> Optional[Dict[str, Any]]:
    """Read the identity registers, or nothing if they do not answer.

    An unknown core reads nothing at all: without it there is no telling which
    address to ask, and these addresses belong to whatever the vendor of an
    unknown part put there.

    The device ID has to land in the published table before any of it is
    believed. A stray value read as a chip identity is worse than admitting we
    do not know, because a flash gets refused on the strength of it.
    """
    for address in IDCODE_BY_CORE.get(core or '', ()):
        raw = _read32(target, address)
        if raw is None:
            continue
        dev_id = raw & 0xFFF
        if dev_id not in ST_DEVICES:
            LOG.debug(f"{address:#010x} answered {dev_id:#05x}, which is not in the table")
            continue

        family, flash_reg = ST_DEVICES[dev_id]
        answer: Dict[str, Any] = {
            "family": family,
            "dev_id": dev_id,
            "rev_id": (raw >> 16) & 0xFFFF,
        }
        if flash_reg is not None:
            answer["flash_size"] = _flash_size(target, flash_reg)
        answer["uid"] = _unique_id(target, UID_BY_FAMILY.get(family))
        return answer
    return None


def _flash_size(target, register: int) -> Optional[int]:
    """Flash size in bytes, from a half word holding it in KB."""
    size_kb = _read16(target, register)
    if size_kb is None:
        return None
    if not 0 < size_kb <= MAX_PLAUSIBLE_FLASH_KB:
        LOG.debug(f"implausible flash size {size_kb} KB at {register:#010x}")
        return None
    return size_kb * 1024


def _unique_id(target, base: Optional[int]) -> Optional[str]:
    if base is None:
        return None
    words = [_read32(target, base + offset) for offset in (0, 4, 8)]
    if any(word is None for word in words):
        return None
    return "".join(f"{word:08X}" for word in reversed(words))


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
