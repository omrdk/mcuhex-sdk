import struct
import threading


class DebugProbe(object):
    """@brief Abstract debug probe class.
        Subclasses of this abstract class are drivers for different debug probe interfaces, either hardware such as a
        USB based probe, or software such as connecting with a simulator.
    """

    def __init__(self) -> None:
        """@brief Constructor."""
        self._lock = threading.Lock()

    def is_open(self):
        raise NotImplementedError()

    async def connect(self):
        """@brief Open the USB interface to the probe for sending commands."""
        raise NotImplementedError()

    async def disconnect(self):
        """@brief Close the probe's USB interface."""
        raise NotImplementedError()

    async def read(self, addr, nb: int):
        raise NotImplementedError()

    async def write(self, addr, data: bytes):
        raise NotImplementedError()

    def lock(self) -> None:
        """@brief Lock the probe from access by other threads.
        This lock is recursive, so locking multiple times from a single thread is acceptable as long
        as the thread unlocks the same number of times.
        This method does not return until the calling thread has ownership of the lock.
        """
        self._lock.acquire()

    def unlock(self) -> None:
        """@brief Unlock the probe.
        Only when the thread unlocks the probe the same number of times it has called lock() will
        the lock actually be released and other threads allowed access.
        """
        self._lock.release()

    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
    #                                READ FUNCTIONS                               #
    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

    @staticmethod
    def to_list(u_iter):
        ls = []
        for i in u_iter: ls.append(i[0])
        return ls

    async def read_u08(self, addr, fmt=None):  # TODO Sadece TI için
        u = await self.read_u16(addr)
        if   fmt == 'dec': return str(u)
        elif fmt == 'hex': return '0x{:02x}'.format(u)
        else             : return u

    async def read_i08(self, addr, fmt=None):  # TODO Sadece TI için
        u = await self.read_i16(addr)
        if   fmt == 'dec': return str(u)
        elif fmt == 'hex': return '0x{:02x}'.format(u)
        else             : return u

    async def read_u16(self, addr, fmt=None):
        b = await self.read(addr, 2)
        u = int.from_bytes(b, byteorder='little', signed=False)
        if   fmt == 'dec': return str(u)
        elif fmt == 'hex': return '0x{:04x}'.format(u)
        else             : return u

    async def read_i16(self, addr, fmt=None):
        b = await self.read(addr, 2)
        u = int.from_bytes(b, byteorder='little', signed=True)
        if   fmt == 'dec': return str(u)
        elif fmt == 'hex': return '0x{:04x}'.format(u)
        else             : return u

    async def read_u32(self, addr, fmt=None):
        b = await self.read(addr, 4)
        u = int.from_bytes(b, byteorder='little', signed=False)
        if   fmt == 'dec': return str(u)
        elif fmt == 'hex': return '0x{:08x}'.format(u)
        else             : return u

    async def read_i32(self, addr, fmt=None):
        b = await self.read(addr, 4)
        u = int.from_bytes(b, byteorder='little', signed=True)
        if   fmt == 'dec': return str(u)
        elif fmt == 'hex': return '0x{:08x}'.format(u)
        else             : return u

    async def read_u64(self, addr, fmt=None):
        b = await self.read(addr, 8)
        u = int.from_bytes(b, byteorder='little', signed=False)
        if   fmt == 'dec': return str(u)
        elif fmt == 'hex': return '0x{:016x}'.format(u)
        else             : return u

    async def read_i64(self, addr, fmt=None):
        b = await self.read(addr, 8)
        u = int.from_bytes(b, byteorder='little', signed=True)
        if   fmt == 'dec': return str(u)
        elif fmt == 'hex': return '0x{:016x}'.format(u)
        else             : return u

    async def read_f32(self, addr, fmt=None):
        b = await self.read(addr, 4)
        u = struct.unpack('<f', b)[0]  # '<' -> little endian
        if   fmt == 'dec': return str(u)
        elif fmt == 'hex':
            u = int.from_bytes(b, byteorder='little', signed=False)
            return '0x{:08x}'.format(u)
        else             : return u

    async def read_f64(self, addr, fmt=None):
        b = await self.read(addr, 8)
        u = struct.unpack('<f', b)[0]  # '<' -> little endian
        if   fmt == 'dec': return str(u)
        elif fmt == 'hex':
            u = int.from_bytes(b, byteorder='little', signed=False)
            return '0x{:016x}'.format(u)
        else             : return u

    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
    #                           READ LIST FUNCTIONS                               #
    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

    async def read_ascii(self, addr, nb):  # TODO Eksik
        ls = await self.read_u08_list(addr, nb)
        res = ''
        for val in ls:
            c = chr(val)
            if val == 32: c = chr(160)
            if val ==  0: c = ' '
            if val ==  1: c = ' '
            if val ==  2: c = ' '
            if val ==  3: c = ' '
            if val ==  4: c = '|'
            if val ==  47: c = '/'
            if val ==  45: c = '-'
            if val ==  251: c = chr(92)
            res = res + c
        return res

    async def read_cp1254(self, addr, nb):
        b = await self.read(addr, nb)
        return b.decode('cp1254')
    
    async def read_u08_list(self, addr, nb):  # returns list
        b = await self.read(addr, nb)
        u = struct.iter_unpack('<B', b)  # B -> unsigned char
        ls = self.to_list(u)
        return ls

    async def read_u16_list(self, addr, nw):  # returns list
        b = await self.read(addr, nw * 2)
        u = struct.iter_unpack('<H', b)  # https://docs.python.org/3/library/struct.html
        return self.to_list(u)

    async def read_i16_list(self, addr, nw):  # returns list
        b = await self.read(addr, nw * 2)
        u = struct.iter_unpack('<h', b)  # https://docs.python.org/3/library/struct.html
        return self.to_list(u)

    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
    #                              WRITE FUNCTIONS                                #
    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

    async def write_u08(self, addr, val):  # TODO Sadece TI için
        return await self.write_u16(addr, val)

    async def write_i08(self, addr, val):   # TODO Sadece TI için
        return await self.write_i16(addr, val)

    async def write_u16(self, addr, val):
        v = int.to_bytes(val, 2, byteorder='little', signed=False)
        r = await self.write(addr, v)
        return r

    async def write_i16(self, addr, val):
        v = int.to_bytes(val, 2, byteorder='little', signed=True)
        r = await self.write(addr, v)
        return r

    async def write_u32(self, addr, val):
        v = int.to_bytes(val, 4, byteorder='little', signed=False)
        r = await self.write(addr, v)
        return r

    async def write_i32(self, addr, val):
        v = int.to_bytes(val, 4, byteorder='little', signed=True)
        r = await self.write(addr, v)
        return r

    async def write_u64(self, addr, val):
        v = int.to_bytes(val, 8, byteorder='little', signed=False)
        r = await self.write(addr, v)
        return r

    async def write_i64(self, addr, val):
        v = int.to_bytes(val, 8, byteorder='little', signed=True)
        r = await self.write(addr, v)
        return r

    async def write_f32(self, addr, val):
        raise  # TODO

    async def write_f64(self, addr, val):
        raise  # TODO

    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
    #                                                                             #
    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

    async def read_symbol(self, xvar, fmt=None):
        typ = xvar['typ']
        adr = int(xvar['adr'], 16)  # to unsigned
        r = 0 if fmt is None else "Not Supported"
        if   typ == 'U08':
            r = await self.read_u08(adr, fmt)
        elif typ == 'I08':
            r = await self.read_i08(adr, fmt)
        elif typ == 'U16':
            r = await self.read_u16(adr, fmt)
        elif typ == 'I16':
            r = await self.read_i16(adr, fmt)
        elif typ == 'PTR':
            r = await self.read_u32(adr, fmt)
        elif typ == 'U32':
            r = await self.read_u32(adr, fmt)
        elif typ == 'I32':
            r = await self.read_i32(adr, fmt)
        elif typ == 'U64':
            r = await self.read_u64(adr, fmt)
        elif typ == 'I64':
            r = await self.read_i64(adr, fmt)
        elif typ == 'F32':
            r = await self.read_f32(adr, fmt)
        elif typ == 'F64':
            r = await self.read_f64(adr, fmt)
        return r

    # F32 : 0x43f80f5c --> 496.12
    #
    async def write_symbol(self, xvar, val: str):
        typ = xvar['typ']
        adr = int(xvar['adr'], 16)  # to unsigned
        bas = 16 if '0x' in val else 10
        if typ == 'F32':
            f = 0
            if bas == 16: f = struct.unpack('f', struct.pack('i', int(val, 16)))[0]
            else        : f = float(val)
            print("float:" + str(f))
            await self.write_f32(adr, f)
        elif typ == 'F64':
            f = 0
            if bas == 16: f = struct.unpack('q', struct.pack('i', int(val, 16)))[0]
            else        : f = float(val)
            print("double:" + str(f))
            await self.write_f64(adr, f)
        else:
            i = int(val, base=bas)
            print("int:" + str(i))
            if   typ == 'U08':
                r = await self.write_u08(adr, i)
            elif typ == 'I08':
                r = await self.write_i08(adr, i)
            elif typ == 'U16':
                r = await self.write_u16(adr, i)
            elif typ == 'I16':
                r = await self.write_i16(adr, i)
            elif typ == 'PTR':
                r = await self.write_u32(adr, i)
            elif typ == 'U32':
                r = await self.write_u32(adr, i)
            elif typ == 'I32':
                r = await self.write_i32(adr, i)
            elif typ == 'U64':
                r = await self.write_u64(adr, i)
            elif typ == 'I64':
                r = await self.write_i64(adr, i)
