import serial.tools.list_ports
import threading

import asyncio
import aioserial

import time

from .debugprobe import DebugProbe

# Extended Kolbus -> 20bit adresleme ile TI 280049 erişimi
class SKolbusEx(DebugProbe):
    # /dev/cu.usbmodemCL4910351 -> LAUNCHXL49 Virtual Comport
    # /dev/cu.usbmodemCL4910354
    RD_16 = 0x00
    WR_16 = 0x10
    RD_32 = 0x80
    WR_32 = 0x90

    def __init__(self):
        self.lock = threading.Lock()
        self.print()
        self.serial = aioserial.AioSerial()
        self.serial.port = "COM1"  # /dev/tty.usbmodemCL3910781 # "/dev/ttyACM0"  # "/dev/cu.usbserial-1130" # /dev/tty.usbmodemCL3910781
        self.serial.baudrate = 460800
        self.serial.bytesize = aioserial.EIGHTBITS
        self.serial.parity   = aioserial.PARITY_NONE
        self.serial.stopbits = aioserial.STOPBITS_ONE
        self.serial.timeout = 2

    def set_port(self, port: str):
        # TODO: Handle if is_open
        self.serial.port = port
        print(f"Port number set to {port}")

    def is_open(self):
        return self.serial.is_open

    async def connect(self) -> bool:
        with self.lock:
            if not self.serial.is_open:
                print("Opening serial port:", self.serial.port)
                self.serial.open()
            else:
                print("Serial port already open.")
        return self.serial.is_open

    async def disconnect(self):
        with self.lock:
            self.serial.close()

    async def test(self):
        encoded_string = "a_string".encode()
        await self.serial.write_async(bytearray(encoded_string))
        self.serial.flush()
        ii = self.serial.isOpen()
        print(ii)

    @staticmethod
    def print():
        ports = serial.tools.list_ports.comports()
        for i in ports:
            print(str(i.device) + " " + str(i.interface) + " " + str(i.description) + " " + str(i.manufacturer))

    def frame(self, cmd, addr: int, nw: int, data: bytearray):
        addr_bytes = addr.to_bytes(4, byteorder='little')
        if nw < 1: nw = 1  # En az 1 Word

        nb = nw * 2                              # number of bytes
        if nb % 4 != 0: nb = nb + 4 - (nb % 4)
        txbuf = bytearray(nb + 4)

        if (addr & 1) == 1 and (cmd == self.RD_32 or cmd == self.WR_32):
            raise NotImplementedError("Adresi tek sayı olan değişken üzerinde 32 bitlik operasyon yapılamaz!")

        txbuf[0] = (cmd | addr_bytes[2])        # [cmd+a_2]
        txbuf[1] = nw
        txbuf[2] = (addr_bytes[0])
        txbuf[3] = (addr_bytes[1])
        if data is not None:
            for i in range(0, len(data)):
                txbuf[i+4] = data[i]
        # print(binascii.hexlify(addr_bytes))
        # print(binascii.hexlify(txbuf))
        # print(len(txbuf))
        return txbuf

    def frame_test(self):
        self.frame(a.RD_16, 0x000CBBAA, 1, None)
        self.frame(a.RD_16, 0x000CBBAA, 2, None)
        self.frame(a.WR_16, 0x000CBBAA, 2, bytearray([1,2,3,4]))
        self.frame(a.WR_32, 0x000CBBAA, 3, bytearray([1,2,3,4,5]))
        self.frame(a.WR_16, 0x000CBBAA, 4, bytearray([1, 2, 3, 4, 5, 6, 7, 8]))

    async def read(self, addr, nb: int):
        with self.lock:
            cmd = self.RD_16
            nw = nb // 2
            if (nb % 4) == 0: cmd = self.RD_32
            if (addr & 1) == 1: cmd = self.RD_16
            frame = self.frame(cmd, addr, nw, None)
            self.serial.flushInput()
            self.serial.flushInput()
            await self.serial.write_async(frame)
            self.serial.flush()
            rxbuf = await self.serial.read_async(len(frame))
            if rxbuf is None:
                print("read error occured")
                raise
            return rxbuf[4:nb+4]

    async def write(self, addr, data: bytes):
        with self.lock:
            if data is None:
                raise
            cmd = self.WR_16
            nb = len(data)
            nw = nb // 2
            if (nb % 4) == 0: cmd = self.WR_32
            if (addr & 1) == 1: cmd = self.WR_16
            try:
                frame = self.frame(cmd, addr, nw, data)
                self.serial.flushInput()
                self.serial.flushInput()
                await self.serial.write_async(frame)
                self.serial.flush()
                rxbuf = await self.serial.read_async(len(frame))
                return rxbuf[4:nb+4]
            except Exception as e:
                print(e)

    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
    #                              TEST  FUNCTIONS                                #
    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

    async def read_test(self):
        # self.serial.open()
        # u = await self.read_u16(0x00000c00)
        u = await self.read_u16(0x00000c00)
        print(u)

        # rxbuf = self.read(0x0000C280, 2)
        # rxbuf = self.read(0x0000C28A, 4)
        # print(binascii.hexlify(rxbuf))
        # u = self.read_u16(0x0000C280)
        # u = self.read_i16(0x0000C280)
        # u = self.read_u32(0x0000C28A)
        # u = self.read_i32(0x0000C28A)
        # u = self.read_f32(0x0000C28A)
        # self.write_u16(0x12000, 0x6169)


async def main():
    a = SKolbusExAsync()
    await a.connect()
    # a.frame_test()
    while True:
        await a.read_test()
        time.sleep(0.2)


if __name__ == '__main__':
    asyncio.run(main())
        

'''
encoded_string = "a_string".encode()
a.serial.write_async(bytearray(encoded_string))
for port, desc, hwid in sorted(ports):
        print("{}: {} [{}]".format(port, desc, hwid))
        #print(port.device)
'''