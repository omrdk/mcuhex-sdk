#!/usr/bin/env python3

import logging
import json
import asyncio
import websockets
import probe

from probe.debugprobe import DebugProbe
from probe.dummyprobe import DummyProbe
from probe.ocd_g474 import OCD_G4x_Probe
from probe.skolbus_ext import SKolbusEx

from typing import Dict, Tuple, Callable, Any, Optional

LOG = logging.getLogger(__name__)


class CommandHandler:
    def __init__(self, probe: DebugProbe):
        self.probe = probe
        self._setup_command_handlers()

    def _setup_command_handlers(self):
        """Setup command handlers with proper argument validation"""
        self._CMD_HANDLERS: Dict[str, Tuple[Callable, int, bool]] = {
            # Command name: (handler_method, required_args, is_async)
            'list_devices': (self._handle_list_devices, 0, False),
            'list_probes': (self._handle_list_probes, 0, False),
            'set_probe': (self._handle_set_probe, 1, False),
            'get_driver_list': (self._handle_get_driver_list, 0, False),
            'connect': (self._handle_connect, 1, True),
            'disconnect': (self._handle_disconnect, 0, True),
            'read': (self._handle_read, 2, True),
            'write': (self._handle_write, 2, True),
        }

    async def execute_command(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """
        Polymorphic command executor - single entry point for all commands
        """
        response = {
            "version": 1,
            **({"id": cmd["id"]} if "id" in cmd else {})
        }
        LOG.info(f"Got command {cmd}")
        command_name = cmd.get("cmd")
        
        if not command_name:
            return self._create_error_response("No command specified")
        
        if command_name not in self._CMD_HANDLERS:
            return self._create_error_response(
                f"Unknown command: {command_name}")
        
        handler, required_args, is_async = self._CMD_HANDLERS[command_name]
        
        # Validate argument count
        if len(cmd) - 1 < required_args:  # -1 for "cmd" key
            return self._create_error_response(
                f"Command '{command_name}' requires {required_args} "
                f"arguments"
            )
        
        try:
            if is_async:
                result = await handler(cmd)
            else:
                result = handler(cmd)
            
            response.update(result)
            response["status"] = 0
            return response
            
        except Exception as e:
            LOG.error(f"Error executing command '{command_name}': {e}")
            return self._create_error_response(str(e))

    def _create_error_response(self, msg: str, status: int = 1) -> Dict[str, Any]:
        """Create standardized error response"""
        return {
            "version": 1,
            "status": status,
            "msg": msg
        }

    def _create_success_response(self, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create standardized success response"""
        response = {"status": 0}
        if data:
            response.update(data)
        return response

    # Command handlers
    def _handle_list_probes(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Handle get_probe_list command"""
        probes = probe.list_probes()
        return self._create_success_response({"probes": probes})

    def _handle_set_probe(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Handle set_probe command"""
        probe_name = cmd.get("probe_name")
        if not probe_name:
            raise ValueError("Probe name is required")
        
        probe_map = {
            "OCD_G4x_Probe": OCD_G4x_Probe,
            "SKolbusEx": SKolbusEx,
            "DummyProbe": DummyProbe
        }
        
        if probe_name not in probe_map:
            raise ValueError(f"Unknown probe type: {probe_name}")
        
        self.probe = probe_map[probe_name]()
        return self._create_success_response({"msg": "Probe set successfully"})

    def _handle_get_driver_list(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Handle get_driver_list command"""
        class_names = self.probe.get_driver_list()
        return self._create_success_response({"drivers": class_names})

    def _handle_list_devices(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Handle get_device_list command"""
        devices = self.probe.list_devices()
        response = self._create_success_response({"devices": devices})
        return response

    async def _handle_connect(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Handle connect command"""
        if not cmd or not cmd["uri"]:
            return self._create_error_response("No device uri specified")
        self.probe.set_port(cmd["uri"])
        is_open = await self.probe.connect()
        return self._create_success_response({"is_open": is_open})

    async def _handle_disconnect(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Handle disconnect command"""
        is_open = await self.probe.disconnect()
        return self._create_success_response({"is_open": is_open})

    async def _handle_read(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Handle read command"""
        addr = cmd.get("addr")
        nb = cmd.get("nb")
        
        if addr is None or nb is None:
            raise ValueError("Both 'addr' and 'nb' are required for read command")
        
        LOG.debug(f"Read {nb} bytes from address {addr}")
        b = await self.probe.read(addr, nb)
        data = b.hex()
        
        LOG.debug(f"Got {len(b)} bytes hex data: {data}")
        return self._create_success_response({"data": data})

    async def _handle_write(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Handle write command"""
        addr = cmd.get("addr")
        data = cmd.get("data")
        
        if addr is None or data is None:
            raise ValueError("Both 'addr' and 'data' are required for write command")
        
        b = bytes.fromhex(data)
        LOG.debug(f"Write {len(b)} bytes to address {addr}")
        await self.probe.write(addr, b)
        return self._create_success_response()


class WebSocketServer:
    def __init__(self, host="ws://127.0.0.1", port=8765):
        """Initialize WebSocket server"""
        self.host = host
        self.port = port
        self.probe = SKolbusEx() # DummyProbe()  # SKolbusEx() # OCD_G4x_Probe(), select probe first
        self.clients = set()
        self.handler = CommandHandler(self.probe)

        probe.init_probes()

    async def handle_client(self, websocket):
        """Handle individual WebSocket client connections"""
        self.clients.add(websocket)
        LOG.info(f"Client connected. Total clients: {len(self.clients)}")

        try:
            async for message in websocket:
                try:
                    cmd = json.loads(message)
                    response = await self.process_command(cmd)
                    LOG.info(f"Sending response: {json.dumps(response)}")
                    await websocket.send(json.dumps(response))
                except json.JSONDecodeError:
                    error_response = {
                        "status": 1,
                        "msg": "Invalid JSON format"
                    }
                    await websocket.send(json.dumps(error_response))
                except Exception as e:
                    LOG.error(f"Error processing command: {e}")
                    error_response = {
                        "status": 1,
                        "msg": str(e)
                    }
                    await websocket.send(json.dumps(error_response))
        except websockets.exceptions.ConnectionClosed:
            LOG.info("Client connection closed")
        finally:
            self.clients.remove(websocket)
            LOG.info(f"Client disconnected. Total clients: {len(self.clients)}")

    async def process_command(self, cmd):
        """Process incoming commands using polymorphic handler"""
        LOG.info(f"Got command {cmd}")
        return await self.handler.execute_command(cmd)

    async def broadcast(self, message):
        """Broadcast message to all connected clients"""
        if self.clients:
            await asyncio.wait([
                client.send(json.dumps(message)) for client in self.clients
            ])

    async def start_server(self):
        """Start the WebSocket server"""
        server = await websockets.serve(
            self.handle_client,
            self.host,
            self.port
        )
        LOG.info(f"WebSocket server started on ws://{self.host}:{self.port}")

        # Keep the server running
        await server.wait_closed()


def main():
    import argparse

    parser = argparse.ArgumentParser(prog="cmwebsocket")
    parser.add_argument("-H", "--host", action="store", dest="host", 
                        type=str, default="localhost",
                        help="Host where WebSocket will accept connections")
    parser.add_argument("-p", "--port", action="store", dest="port", 
                        type=int, default=8765,
                        help="Port to use for WebSocket server")
    parser.add_argument("-d", "--debug", action="store_true", dest="debug",
                        help="Enable debug output")
    (args, _) = parser.parse_known_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    server = WebSocketServer(args.host, args.port)

    try:
        asyncio.run(server.start_server())
    except KeyboardInterrupt:
        LOG.info("Server stopped by user")


if __name__ == "__main__":
    main()
