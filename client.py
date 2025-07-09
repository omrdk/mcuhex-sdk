#!/usr/bin/env python3

import asyncio
import logging
from probe.remoteprobe import RemoteProbe
from typing import Dict, Tuple, Callable, List

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class InteractiveCommandHandler:
    def __init__(self, probe: RemoteProbe):
        self.probe = probe
        self._setup_command_handlers()

    def _setup_command_handlers(self):
        """Setup command handlers for interactive mode"""
        self._CMD_HANDLERS: Dict[str, Tuple[Callable, int, bool]] = {
            # Command name: (handler_method, required_args, is_async)
            'devices': (self._handle_devices, 0, True),
            'connect': (self._handle_connect, 0, True),
            'disconnect': (self._handle_disconnect, 0, True),
            'probes': (self._handle_probes, 0, True),
            'set_probe': (self._handle_set_probe, 1, True),
            'read': (self._handle_read, 2, True),
            'write': (self._handle_write, 2, True),
            'quit': (self._handle_quit, 0, False),
            'help': (self._handle_help, 0, False),
        }

    async def execute_command(self, command_parts: List[str]) -> bool:
        """
        Polymorphic command executor for interactive mode
        Returns True if should continue, False if should quit
        """
        if not command_parts:
            return True
        
        cmd_name = command_parts[0].lower()
        
        if cmd_name not in self._CMD_HANDLERS:
            print(f"Unknown command: {cmd_name}")
            print("Type 'help' for available commands")
            return True
        
        handler, required_args, is_async = self._CMD_HANDLERS[cmd_name]
        
        # Validate argument count
        if len(command_parts) - 1 < required_args:
            print(f"Command '{cmd_name}' requires {required_args} arguments")
            return True
        
        try:
            if is_async:
                result = await handler(command_parts)
            else:
                result = handler(command_parts)
            
            return result
            
        except Exception as e:
            print(f"Error executing command '{cmd_name}': {e}")
            return True


    async def _handle_devices(self, command_parts: List[str]) -> bool:
        """Handle devices command"""
        await self.probe.get_devices()
        return True

    # Command handlers
    async def _handle_probes(self, command_parts: List[str]) -> bool:
        """Handle probes command"""
        await self.probe.get_probe_list()
        return True

    async def _handle_set_probe(self, command_parts: List[str]) -> bool:
        """Handle set_probe command"""
        probe_name = command_parts[1]
        await self.probe.set_probe(probe_name)
        return True

    async def _handle_connect(self, command_parts: List[str]) -> bool:
        """Handle connect command"""
        await self.probe.connect()
        return True

    async def _handle_disconnect(self, command_parts: List[str]) -> bool:
        """Handle disconnect command"""
        await self.probe.disconnect()
        return True

    async def _handle_read(self, command_parts: List[str]) -> bool:
        """Handle read command"""
        addr = int(command_parts[1], 0)  # Auto-detect hex/decimal
        nb = int(command_parts[2])
        
        try:
            data = await self.probe.read(addr, nb)
            print(f"Read {len(data)} bytes from 0x{addr:X}: {data.hex()}")
        except Exception as e:
            print(f"Read failed: {e}")
        
        return True

    async def _handle_write(self, command_parts: List[str]) -> bool:
        """Handle write command"""
        addr = int(command_parts[1], 0)  # Auto-detect hex/decimal
        data_hex = command_parts[2]
        
        try:
            data = bytes.fromhex(data_hex)
            await self.probe.write(addr, data)
            print(f"Wrote {len(data)} bytes to 0x{addr:X}: {data.hex()}")
        except Exception as e:
            print(f"Write failed: {e}")
        
        return True

    def _handle_quit(self, command_parts: List[str]) -> bool:
        """Handle quit command"""
        print("Goodbye!")
        return False

    def _handle_help(self, command_parts: List[str]) -> bool:
        """Handle help command"""
        print("Available commands:")
        print("  devices                   - List available devices")
        print("  connect                   - Connect to device")
        print("  disconnect                - Disconnect from device")
        print("  probes                    - List available probes")
        print("  set_probe <name>          - Set probe type")
        print("  read <addr> <bytes>       - Read memory (addr in hex/dec)")
        print("  write <addr> <hex_data>   - Write memory (addr in hex/dec)")
        print("  help                      - Show this help"    )
        print("  quit                      - Exit interactive mode")
        print("\nExamples:")
        print("  read 0x20000000 4         - Read 4 bytes from 0x20000000")
        print("  write 0x20000000 12345678 - Write hex data to address")
        return True


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
    """Interactive test for manual testing using polymorphic command handling"""
    probe = RemoteProbe(host="localhost", port=8765)
    handler = InteractiveCommandHandler(probe)
    
    try:
        print("RemoteProbe WebSocket Client - Interactive Mode")
        print("Type 'help' for available commands")
        
        while True:
            try:
                command = input("> ").strip().split()
                if not command:
                    continue
                
                should_continue = await handler.execute_command(command)
                if not should_continue:
                    break
                    
            except KeyboardInterrupt:
                print("\nGoodbye!")
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