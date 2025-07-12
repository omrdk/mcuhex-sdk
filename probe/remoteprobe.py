import logging
import json
import websockets

from .debugprobe import DebugProbe

logger = logging.getLogger(__name__)


class RemoteProbe(DebugProbe):

    def __init__(self, host="ws://127.0.0.1", port=8765):
        super().__init__()
        
        self.host = host
        self.port = port
        self.websocket = None
        self.connected = False

    def is_open(self):
        return self.connected and self.websocket is not None

    async def _ensure_connection(self):
        """Ensure WebSocket connection is established"""
        if not self.connected or self.websocket is None:
            await self._connect_websocket()

    async def _connect_websocket(self):
        """Establish WebSocket connection to the server"""
        try:
            uri = f"ws://{self.host}:{self.port}"
            self.websocket = await websockets.connect(uri)
            self.connected = True
            logger.info(f"Connected to WebSocket server at {uri}")
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket server: {e}")
            raise

    async def _send_command(self, cmd):
        """Send command to WebSocket server and get response"""
        await self._ensure_connection()
        
        try:
            await self.websocket.send(json.dumps(cmd))
            response = await self.websocket.recv()
            return json.loads(response)
        except Exception as e:
            logger.error(f"Error communicating with server: {e}")
            self.connected = False
            raise

    async def get_devices(self):
        cmd = {
            "cmd": "list_devices",
        }
        r = await self._send_command(cmd)

        logger.info(f"Devices: {r['devices']}")

        if r["status"] != 0:
            print(f"fail! {r['msg']}")
            raise Exception(r["msg"])

    async def list_probes(self):
        cmd = {
            "cmd": "list_probes",
        }
        r = await self._send_command(cmd)

        logger.info(f"Got probes: {r['probes']}")

        if r["status"] != 0:
            print(f"fail! {r['msg']}")
            raise Exception(r["msg"])
        
    async def set_probe(self, probe_name):
        cmd = {
            "cmd": "set_probe",
            "probe_name": probe_name
        }
        r = await self._send_command(cmd)

        logger.info(f"Set probe: {r['msg']}")

        if r["status"] != 0:
            print(f"fail! {r['msg']}")
            raise Exception(r["msg"])

    async def connect(self):
        cmd = {
            "cmd": "connect",
            "uri": "COM1"  # TODO: 
        }
        print(f"Connecting to {cmd['uri']} ...", end=' ')
        resp = await self._send_command(cmd)

        if resp["status"] != 0:
            print(f"fail! {resp['msg']}")
            raise Exception(resp["msg"])
        else:
            print("success!")

    async def disconnect(self):
        cmd = {
            "cmd": "disconnect",
        }
        print("Disconnecting ...", end=' ')
        resp = await self._send_command(cmd)

        if resp["status"] != 0:
            print(f"fail! {resp['msg']}")
            raise Exception(resp["msg"])
        else:
            print("success!")

    async def read(self, addr, nb: int):
        cmd = {
            "cmd": "read",
            "addr": addr,
            "nb": nb,
        }
        logger.debug(f"Read {nb} bytes from address {addr}")
        resp = await self._send_command(cmd)

        if resp["status"] != 0:
            print(f"fail! {resp['msg']}")
            raise Exception(resp["msg"])

        data = resp["data"]
        b = bytes.fromhex(data)
        logger.debug(f"Got {len(b)} bytes hex data: {data}")

        return b

    async def write(self, addr, data: bytes):
        cmd = {
            "cmd": "write",
            "addr": addr,
            "data": data.hex(),
        }
        logger.debug(f"Write {len(data)} bytes to address {addr}")
        resp = await self._send_command(cmd)

        if resp["status"] != 0:
            print(f"fail! {resp['msg']}")
            raise Exception(resp["msg"])

    async def close(self):
        """Close the WebSocket connection"""
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
            self.connected = False
            logger.info("WebSocket connection closed")
