import logging
from .debugprobe import DebugProbe

LOG = logging.getLogger(__name__)

class DummyProbe(DebugProbe):

    def __init__(self):
        super().__init__()
    
    async def connect(self):
        LOG.info("open")

    async def disconnect(self):
        LOG.info("close")

    async def read(self, addr, nb: int):
        LOG.debug("read")
        return bytes(nb)

    async def write(self, addr, data: bytes):
        LOG.debug("write")
