import logging

from pyocd.core.helpers import ConnectHelper
from pyocd.target.pack import (cmsis_pack, pack_target)
from pyocd.core.session import Session
from pyocd.core.soc_target import SoCTarget
from pyocd.board.board import Board

import zipfile
from .debugprobe import DebugProbe
from typing import Optional

LOG = logging.getLogger("kdb-probe")


class OCD_G4x_Probe(DebugProbe):

    def __init__(self):
        super().__init__()

        pack_path = ("/Users/merdak/Projects/probe/"
                     "Keil.STM32G4xx_DFP.1.4.0.pack")
        z = zipfile.ZipFile(pack_path, 'r')  # TODO
        pack = cmsis_pack.CmsisPack(z)
        pack_target.PackTargets.populate_targets_from_pack(pack)

        self.session: Optional[Session] = None
        self.target: Optional[SoCTarget] = None

    async def connect(self):
        session = ConnectHelper.session_with_chosen_probe(
            blocking=False, auto_open=False
        )
        print(session)
        session.open()    # Manually open the session.

        target = session.target
        # target.elf = "/Users/dak/hadrone/build/hadrone.elf"  # TODO

        self.session = session
        self.target = target
        LOG.info("session open, probe = " + repr(session.probe.product_name))
        # provider = ELFSymbolProvider(target.elf)
        # adr = provider.get_symbol_value("deneme")
        # v = target.read32(adr)
        # print(v)
        # pc = target.read_core_register("pc")
        # print("pc: 0x%X" % pc)

    async def disconnect(self):
        self.session.close()  # Close the session and connection.
        LOG.info("session closed")

    async def read(self, addr, nb: int):
        ls = self.target.read_memory_block8(addr, nb)
        return bytes(ls)

    async def write(self, addr, data: bytes):
        self.target.write_memory_block8(addr, data)

    async def write_u32(self, addr, value):
        self.target.write_memory_block32(addr, [value])

    def init_drivers(self):
        """Initialize drivers - placeholder method"""
        LOG.info("Initializing drivers")
        # This method is called by the server but may not be needed
        # for the current implementation
        pass

    def get_driver_list(self):
        """Get list of available drivers - placeholder method"""
        LOG.info("Getting driver list")
        # Return empty list for now - can be implemented later
        return []

    def get_device_list(self) -> list[dict]:
        """Get list of available devices - placeholder method"""
        LOG.info("Getting device list")
        # Return empty list for now - can be implemented later

        # Bağlı cihazları listele
        all_probes = ConnectHelper.get_all_connected_probes(blocking=False)
        result = []
        for i, probe in enumerate(all_probes):
            print(f"{i}: {probe.description} - {probe.unique_id}")
            probe_dict = {
                "uid": probe.unique_id,
                "device": probe.description
            }
            result.append(probe_dict)

        return result


"""
if __name__ == '__main__':
    probe = OCD_G4x_Probe()
    probe.open()

    # Read PC register to see where the program is currently executing
    pc = probe.target.read_core_register("pc")
    print("PC: 0x%X" % pc)

    # Read some memory at a known address (example: 0x20000000 for STM32 RAM)
    # You can change this address to whatever you want to read
    test_addr = 0x20000000
    try:
        # Write a test value
        await probe.write_u32(0x20000000, 0x12345678)

        # Then read it back
        v = probe.target.read32(0x20000000)
        print(f"Value: 0x{v:X}")
    except Exception as e:
        print(f"Error reading memory at 0x{test_addr:X}: {e}")
        print("Try a different address or make sure the target is running")

    # Example: Read multiple registers
    try:
        sp = probe.target.read_core_register("sp")
        lr = probe.target.read_core_register("lr")
        print("SP: 0x%X" % sp)
        print("LR: 0x%X" % lr)
    except Exception as e:
        print(f"Error reading registers: {e}")

    probe.close()
"""