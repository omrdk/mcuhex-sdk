#!/usr/bin/env python3

import sys
import os
import logging
import signal
import json
import asyncio
import websockets
from probe.dummyprobe import DummyProbe
from probe.ocd_g474 import OCD_G4x_Probe
from probe.skolbus_ext import SKolbusEx

import probe

logger = logging.getLogger(__name__)

class WebSocketServer:
    def __init__(self, host="localhost", port=8765):
        """Initialize WebSocket server"""
        self.host = host
        self.port = port
        self.probe = DummyProbe() #SKolbusEx() # OCD_G4x_Probe(), selected probe
        self.clients = set()
        
        # Initialize probes
        probe.init_probes()
        

    async def handle_client(self, websocket):
        """Handle individual WebSocket client connections"""
        self.clients.add(websocket)
        logger.info(f"Client connected. Total clients: "
                    f"{len(self.clients)}")
        
        try:
            async for message in websocket:
                try:
                    cmd = json.loads(message)
                    response = await self.process_command(cmd)
                    await websocket.send(json.dumps(response))
                except json.JSONDecodeError:
                    error_response = {
                        "status": 1,
                        "msg": "Invalid JSON format"
                    }
                    await websocket.send(json.dumps(error_response))
                except Exception as e:
                    logger.error(f"Error processing command: {e}")
                    error_response = {
                        "status": 1,
                        "msg": str(e)
                    }
                    await websocket.send(json.dumps(error_response))
        except websockets.exceptions.ConnectionClosed:
            logger.info("Client connection closed")
        finally:
            self.clients.remove(websocket)
            logger.info(f"Client disconnected. Total clients: "
                       f"{len(self.clients)}")

    async def process_command(self, cmd):
        """Process incoming commands and return responses"""
        response = {"version": 1}
        logger.info(f"Got command {cmd}")
        if cmd["cmd"] == "get_probe_list":
            response["probes"] = probe.get_probe_list()
            response["status"] = 0
        elif cmd["cmd"] == "set_probe":
            probe_name = cmd["probe_name"]
            if probe_name == "OCD_G4x_Probe":
                self.probe = OCD_G4x_Probe()
                response["status"] = 0
                response["msg"] = "Probe set successfully"
                return response
            elif probe_name == "SKolbusEx":
                self.probe = SKolbusEx()  # TODO: SKolbusExt için probe oluştur
                response["status"] = 0
                response["msg"] = "Probe set successfully"
                return response
            else:
                self.probe = DummyProbe()
                response["status"] = 1
                response["msg"] = "Probe name is required"
                return response
            
        elif cmd["cmd"] == "get_driver_list":
            try:
                class_names = self.probe.get_driver_list()
                response["drivers"] = class_names
                response["status"] = 0
            except Exception as e:
                response["status"] = 1
                response["msg"] = str(e)
                
        elif cmd["cmd"] == "get_device_list":
            try:
                response["devices"] = self.probe.get_device_list()
                response["status"] = 0
            except Exception as e:
                response["status"] = 1
                response["msg"] = str(e)
                
        elif cmd["cmd"] == "connect":
            try:
                # Note: uri is not used in current implementation
                await self.probe.connect()
                response["status"] = 0
            except Exception as e:
                response["status"] = 1
                response["msg"] = str(e)
                
        elif cmd["cmd"] == "disconnect":
            try:
                await self.probe.disconnect()
                response["status"] = 0
            except Exception as e:
                response["status"] = 1
                response["msg"] = str(e)
                
        elif cmd["cmd"] == "read":
            try:
                addr = cmd["addr"]
                nb = cmd["nb"]

                logger.debug(f"Read {nb} bytes from address {addr}")

                b = await self.probe.read(addr, nb)
                data = b.hex()
                response["data"] = data

                logger.debug(f"Got {len(b)} bytes hex data: {data}")
                response["status"] = 0
            except Exception as e:
                logger.error(e)
                response["status"] = 1
                response["msg"] = str(e)

        elif cmd["cmd"] == "write":
            try:
                addr = cmd["addr"]
                data = cmd["data"]
                b = bytes.fromhex(data)
                logger.debug(f"Write {len(b)} bytes to address {addr}")
                await self.probe.write(addr, b)
                response["status"] = 0
            except Exception as e:
                response["status"] = 1
                response["msg"] = str(e)

        else:
            response["status"] = 0xFF
            response["msg"] = f"Unknown command {cmd['cmd']}"
            
        return response

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
        logger.info(f"WebSocket server started on ws://{self.host}:{self.port}")
        
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

    # Create and start WebSocket server
    server = WebSocketServer(args.host, args.port)
    
    try:
        asyncio.run(server.start_server())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")


if __name__ == "__main__":
    main()
