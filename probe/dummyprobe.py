import sys

import logging
from .debugprobe import DebugProbe

LOG = logging.getLogger(__name__)


class DummyProbe(DebugProbe):

    def __init__(self):
        super().__init__()

    def get_device_list(self):
        port_list = []

        port_list.append({
        "device": "/dev/ttyUSB0",
        "description": "USB Serial Device",
        "hwid": "USB VID:PID=0403:6001",
        "vid": 1027,
        "pid": 24577,
        "manufacturer": "FTDI",
        "serial_number": "A50285BI",
        "location": "1-1.2"
        })
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
