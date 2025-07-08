#!/usr/bin/env python3

import asyncio
import logging
from probe.remoteprobe import RemoteProbe

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_remoteprobe():
    """Test the WebSocket remote probe client"""
    probe = RemoteProbe(host="localhost", port=8765)

    try:
        logger.info("Testing get_devices...")
        await probe.get_devices()
        
        logger.info("Testing connect...")
        await probe.connect()
        
        logger.info("Testing read operation...")
        try:
            # Read 4 bytes from address 0x20000000 (STM32 RAM)
            # data = await probe.read_u32(0x20000000)
            # testing ti dev
            data = await probe.read_u16(0x0000AC25)
            logger.info(f"Read data: {data.hex()}")
        except Exception as e:
            logger.warning(f"Read failed (expected if no device): {e}")
        
        # Test write operation
        logger.info("Testing write operation...")
        try:
            # Write test data to address 0x20000000
            await probe.write_u32(0x20000000, 0x12345678)
            logger.info(f"Write successful: {0x12345678.hex()}")
        except Exception as e:
            logger.warning(f"Write failed (expected if no device): {e}")
        
        logger.info("Testing disconnect...")
        await probe.disconnect()
        
    except Exception as e:
        logger.error(f"Test failed: {e}")
    finally:
        await probe.close()


async def interactive_test():
    """Interactive test for manual testing"""
    probe = RemoteProbe(host="localhost", port=8765)
    
    try:
        print("RemoteProbe WebSocket Client - Interactive Mode")
        print("Available commands: probes, set_probe <name>, devices, connect, disconnect, read <addr> <bytes>, write <addr> <bytes>, quit")
        while True:
            try:
                command = input("> ").strip().split()
                if not command:
                    continue
                
                cmd = command[0].lower()
                
                if cmd == "quit":
                    break
                elif cmd == "probes":
                    await probe.get_probe_list()
                elif cmd == "set_probe":
                    probe_name = command[1]  # TODO: ensure passed name expected
                    await probe.set_probe(probe_name)
                elif cmd == "devices":
                    await probe.get_devices()
                elif cmd == "connect":
                    await probe.connect()
                elif cmd == "disconnect":
                    await probe.disconnect()
                elif cmd == "read":
                    # data = await probe.read_u32(0x20000000)
                    data = await probe.read_u16(0x0000AC23)
                    print(f"Read data: {hex(data)}")
                elif cmd == "write":
                    # await probe.write_u32(0x20000000, 0x12345678)
                    await probe.write_u16(0x0000AC23, 0x1234)
                else:
                    print("Invalid command")
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Error: {e}")
    finally:
        await probe.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        asyncio.run(interactive_test())
    else:
        asyncio.run(test_remoteprobe())