import logging
from .debugprobe import DebugProbe

LOG = logging.getLogger(__name__)
import serial

class DummyProbe(DebugProbe):

    def __init__(self):
        super().__init__()
    
    def get_device_list(self):
        port_list = []
        ports = serial.tools.list_ports.comports()
        
        for port in ports:
            port_info = {
                "device": port.device,
                "description": port.description,
                "manufacturer": port.manufacturer,
            }
            port_list.append(port_info)

        return port_list

    async def connect(self):
        LOG.info("open")

    async def disconnect(self):
        LOG.info("close")

    async def read(self, addr, nb: int):
        LOG.debug("read")
        return bytes(nb)

    async def write(self, addr, data: bytes):
        LOG.debug("write")
