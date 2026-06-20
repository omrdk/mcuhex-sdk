import logging
import json
import os
import platform
import time
import math
import asyncio
import struct
from .debugprobe import DebugProbe
from .errors import ProbeError

LOG = logging.getLogger(__name__)

class DummyProbe(DebugProbe):

    # Scenario definitions: {scenario_name: (trigger_method, error_code, message)}
    # scenario_name matches the ConnectionErrorCode enum value in the web client.
    # Use None for error_code when the scenario needs special handling.
    SCENARIOS = {
        # Generic errors
        "NO_DEVICES_FOUND":          ("list_devices", None, None),
        "DEVICE_BUSY":               ("connect", "DEVICE_BUSY", "Device is in use by another debug session"),
        "PERMISSION_DENIED":         ("connect", "PERMISSION_DENIED", "USB device access denied: insufficient permissions"),
        "CONNECT_TIMEOUT":           ("connect", "CONNECT_TIMEOUT", "Debug probe did not respond within 3000ms"),
        "PROBE_DRIVER_MISMATCH":     ("connect", "PROBE_DRIVER_MISMATCH", "Probe driver does not match connected hardware"),
        "READ_WRITE_FAILED":         ("read", "READ_WRITE_FAILED", "Memory transfer fault at address 0x{addr:08X}"),
        # Cortex-M family
        "CORTEX_M_DEBUG_PORT_LOCKED":    ("connect", "CORTEX_M_DEBUG_PORT_LOCKED", "Debug port is locked (RDP/APPROTECT active)"),
        "CORTEX_M_SWD_PROTOCOL_ERROR":   ("connect", "CORTEX_M_SWD_PROTOCOL_ERROR", "SWD protocol error: no ACK from target"),
        "CORTEX_M_TARGET_IN_RESET":      ("connect", "CORTEX_M_TARGET_IN_RESET", "Target is held in reset (check NRST pin)"),
        "CORTEX_M_UNSUPPORTED_TARGET":   ("connect", "CORTEX_M_UNSUPPORTED_TARGET", "Unknown target: IDCODE 0x00000000 not recognized"),
        "CORTEX_M_TARGET_NOT_HALTED":    ("read", "CORTEX_M_TARGET_NOT_HALTED", "Target is running, cannot access memory"),
        "CORTEX_M_FLASH_WRITE_PROTECTED": ("write", "CORTEX_M_FLASH_WRITE_PROTECTED", "Flash write protected: region is locked"),
        "CORTEX_M_HARDFAULT_DETECTED":   ("read", "CORTEX_M_HARDFAULT_DETECTED", "Target entered HardFault (CFSR=0x00000001)"),
        # SDK_CONNECTION_LOST is handled at the WebSocketServer level, not here.
    }

    # Demo mode dataset: a synthetic *nested* structure (a struct that contains
    # a sub-struct, an array, and a scalar) so a WebSocket client (e.g. the VS
    # Code extension) can demonstrate nested-register drill-down without a real
    # ELF. The shape matches the frontend DebugInfoNode
    # (symbol/address/numberOfBytes/type/data). Each *leaf* address selects its
    # waveform via (addr >> 2) & 0x3 — see _generate_waveform:
    #   0x2000 → sine, 0x2004 → cosine, 0x2008 → triangle, 0x200C → sawtooth, ...
    DEMO_TREE = [
        {
            "symbol": "sys", "address": "0x2000", "numberOfBytes": 32, "type": "struct",
            "data": [
                {
                    "symbol": "motor", "address": "0x2000", "numberOfBytes": 16, "type": "struct",
                    "data": [
                        {"symbol": "rpm",        "address": "0x2000", "numberOfBytes": 4, "type": "I32"},
                        {"symbol": "current_ma", "address": "0x2004", "numberOfBytes": 4, "type": "I32"},
                        {"symbol": "temp_c",     "address": "0x2008", "numberOfBytes": 4, "type": "I32"},
                        {"symbol": "duty",       "address": "0x200C", "numberOfBytes": 1, "type": "U08"},
                    ],
                },
                {
                    "symbol": "adc_mv", "address": "0x2010", "numberOfBytes": 8, "type": "I16[4]",
                    "data": [
                        {"symbol": "[0]", "address": "0x2010", "numberOfBytes": 2, "type": "I16"},
                        {"symbol": "[1]", "address": "0x2012", "numberOfBytes": 2, "type": "I16"},
                        {"symbol": "[2]", "address": "0x2014", "numberOfBytes": 2, "type": "I16"},
                        {"symbol": "[3]", "address": "0x2016", "numberOfBytes": 2, "type": "I16"},
                    ],
                },
                {"symbol": "uptime_ms", "address": "0x201C", "numberOfBytes": 4, "type": "U32"},
            ],
        },
    ]

    @classmethod
    def demo_leaves(cls):
        """Flatten DEMO_TREE into [(addr_int, numberOfBytes), ...] for every leaf
        (a node with no children), so demo memory can be seeded for reads."""
        out = []

        def walk(nodes):
            for n in nodes:
                kids = n.get("data")
                if kids:
                    walk(kids)
                else:
                    out.append((int(n["address"], 0), n["numberOfBytes"]))

        walk(cls.DEMO_TREE)
        return out

    def __init__(self, mock_file=None, mock_type=None, device_path=None, vid=None, pid=None,
                 manufacturer=None, product=None, description=None, serial_number=None, scenario=None,
                 read_delay_ms=2.0, wave_freq=5.0, wave_amp=400, wave_offset=500, demo=False):
        super().__init__()
        self._memory = {}
        self._is_open = False
        self._connect_time = None
        self._read_delay = read_delay_ms / 1000  # convert to seconds
        self._wave_freq = wave_freq
        self._wave_amp = wave_amp
        self._wave_offset = wave_offset
        self.mock_file = mock_file
        self.mock_type = mock_type
        self.scenario = scenario
        self.demo = demo
        
        # Determine device path - use provided, or generate based on type, or use default
        if device_path is None:
            if self.mock_type == "stm":
                device_path = self._generate_device_path("ST-Link")
            elif self.mock_type == "ti":
                device_path = self._generate_device_path("XDS110")
            else:
                device_path = self._generate_device_path()
        
        # Define mock metadata based on type or custom parameters
        self.device_info = {
            "device": device_path,
            "description": description or "Simulated Device (DummyProbe)",
            "manufacturer": manufacturer or "Mock Corp",
            "vid": vid or 0x1234,
            "pid": pid or 0x5678,
            "hwid": self._generate_hwid(vid or 0x1234, pid or 0x5678, serial_number),
            "product": product or "Mock Product"
        }
        
        # Override with type-specific defaults if mock_type is set and no custom values provided
        if self.mock_type == "stm":
            if vid is None:
                self.device_info["vid"] = 0x0483
            if pid is None:
                self.device_info["pid"] = 0x3748
            if manufacturer is None:
                self.device_info["manufacturer"] = "STMicroelectronics"
            if product is None:
                self.device_info["product"] = "STM32 Debugger"
            if description is None:
                self.device_info["description"] = "STM32 STLink"
            # Update hwid with correct VID/PID
            self.device_info["hwid"] = self._generate_hwid(
                self.device_info["vid"], self.device_info["pid"], serial_number)
            print("\n*** MOCK STM32 PROBE INITIALIZED ***")
            print("Emulating: ST-Link V2 with STM32G4xx target")
            print(f"Device: {self.device_info['device']}")
            print(f"VID:PID = {self.device_info['vid']:04X}:{self.device_info['pid']:04X}")
            
        elif self.mock_type == "ti":
            if vid is None:
                self.device_info["vid"] = 0x0451
            if pid is None:
                self.device_info["pid"] = 0xbef3
            if manufacturer is None:
                self.device_info["manufacturer"] = "Texas Instruments"
            if product is None:
                self.device_info["product"] = "XDS110 Debug Probe"
            if description is None:
                self.device_info["description"] = "XDS110 USB Debug Probe"
            # Update hwid with correct VID/PID
            self.device_info["hwid"] = self._generate_hwid(
                self.device_info["vid"], self.device_info["pid"], serial_number)
            print("\n*** MOCK TI PROBE INITIALIZED ***")
            print("Emulating: XDS110 with TMS320F28004x target")
            print(f"Device: {self.device_info['device']}")
            print(f"VID:PID = {self.device_info['vid']:04X}:{self.device_info['pid']:04X}")

        elif self.mock_type == "esp":
            if vid is None:
                self.device_info["vid"] = 0x303A
            if pid is None:
                self.device_info["pid"] = 0x1001
            if manufacturer is None:
                self.device_info["manufacturer"] = "Espressif"
            if product is None:
                self.device_info["product"] = "ESP32 JTAG Debug Probe"
            if description is None:
                self.device_info["description"] = "ESP32 USB JTAG/serial"
            self.device_info["hwid"] = self._generate_hwid(
                self.device_info["vid"], self.device_info["pid"], serial_number)
            print("\n*** MOCK ESP PROBE INITIALIZED ***")
            print("Emulating: ESP32-C3 built-in JTAG")
            print(f"Device: {self.device_info['device']}")
            print(f"VID:PID = {self.device_info['vid']:04X}:{self.device_info['pid']:04X}")

        if self.mock_file:
            self.load_from_json(self.mock_file)

        # Demo mode: seed every leaf register so waveform generation kicks in
        if self.demo:
            leaves = self.demo_leaves()
            for addr, nb in leaves:
                for i in range(nb):
                    self._memory[addr + i] = 0
            LOG.info(f"Demo mode: seeded {len(leaves)} nested leaf registers from DEMO_TREE")

        if self.scenario and self.scenario in self.SCENARIOS:
            trigger, code, msg = self.SCENARIOS[self.scenario]
            print(f"\n*** SCENARIO ACTIVE: {self.scenario} ***")
            print(f"    Trigger: {trigger}()")
            print(f"    Error code: {code}")
            print(f"    Message: {msg}\n")
        elif self.scenario == "SDK_CONNECTION_LOST":
            print(f"\n*** SCENARIO ACTIVE: SDK_CONNECTION_LOST ***")
            print(f"    Trigger: WebSocket close after successful connect\n")
    
    def _generate_device_path(self, device_type=None):
        """Generate a realistic device path based on the operating system"""
        system = platform.system()
        
        if system == "Linux":
            if device_type == "ST-Link":
                return "/dev/ttyACM0"  # ST-Link typically appears as ACM device
            elif device_type == "XDS110":
                return "/dev/ttyUSB0"  # XDS110 typically appears as USB serial
            else:
                return "/dev/ttyUSB0"  # Default to USB serial
        elif system == "Darwin":  # macOS
            if device_type == "ST-Link":
                return "/dev/cu.usbmodem14103"  # Typical ST-Link on macOS
            elif device_type == "XDS110":
                return "/dev/cu.usbserial-1410"  # Typical XDS110 on macOS
            else:
                return "/dev/cu.usbserial-0001"  # Default macOS USB serial
        elif system == "Windows":
            if device_type == "ST-Link":
                return "COM3"  # Typical ST-Link COM port
            elif device_type == "XDS110":
                return "COM4"  # Typical XDS110 COM port
            else:
                return "COM3"  # Default Windows COM port
        else:
            return "/dev/ttyUSB0"  # Fallback
    
    def _generate_hwid(self, vid, pid, serial_number=None):
        """Generate a realistic hardware ID string matching USB device format"""
        if serial_number:
            return f"USB VID:{vid:04X} PID:{pid:04X} SER:{serial_number}"
        else:
            return f"USB VID:{vid:04X} PID:{pid:04X}"
    
    def load_from_json(self, filename):
        try:
            with open(filename, 'r') as f:
                data = json.load(f)
                
            # Check for different formats
            if 'lst' in data:
                # Format: {"lst": [{"nam": "var", "adr": "0x...", "sze": 4}, ...]}
                LOG.info(f"Detected symbol list format in {filename}")
                self._load_symbol_list(data['lst'])
            else:
                # Format: {"memory": {"0x...": val, ...}} or {"0x...": val}
                mem_map = data.get('memory', data)
                self._load_memory_map(mem_map)
                
            LOG.info(f"Loaded mock data from {filename}")
            
        except Exception as e:
            LOG.error(f"Failed to load mock file {filename}: {e}")

    def _load_symbol_list(self, lst):
        for item in lst:
            try:
                if 'adr' in item and 'sze' in item:
                    addr_str = item['adr']
                    size = item['sze']
                    addr = int(addr_str, 0)
                    
                    # Initialize memory with zeros (or 'val' if present)
                    if 'val' in item:
                        # Convert val to bytes based on size
                        # Assume val is an integer here for simplicity
                        val = item['val']
                        try:
                            # Little endian default
                            bytes_val = val.to_bytes(size, 'little')
                        except OverflowError:
                            # Handle case where value might be too big for size or negative
                            # Try signed
                            try:
                                bytes_val = val.to_bytes(size, 'little', signed=True)
                            except:
                                # Fallback or just ignore value
                                bytes_val = bytes(size)
                        
                        # DEBUG: Print loaded value
                        print(f"[DEBUG] Loaded {item.get('nam')} @ {hex(addr)} = {val} ({bytes_val.hex()})")

                        for i, b in enumerate(bytes_val):
                            self._memory[addr + i] = b
                    else:
                        # Initialize with zeros if not already set
                        for i in range(size):
                            if (addr + i) not in self._memory:
                                self._memory[addr + i] = 0
                            
                    # Recursively handle nested lists (struct members) if any
                    if 'lst' in item and isinstance(item['lst'], list):
                        self._load_symbol_list(item['lst'])
            except Exception as e:
                LOG.warning(f"Error processing symbol item {item}: {e}")

    def _load_memory_map(self, mem_map):
        for addr_str, value in mem_map.items():
            try:
                addr = int(addr_str, 0) # Handle hex strings
                
                # Handle different value types
                if isinstance(value, list):
                    bytes_val = bytes(value)
                elif isinstance(value, str):
                    # Assume hex string "0xAABB..." or "AABB..."
                    hex_str = value.replace('0x', '')
                    if len(hex_str) % 2 != 0:
                        hex_str = '0' + hex_str
                    bytes_val = bytes.fromhex(hex_str)
                elif isinstance(value, int):
                    # Assume 4 bytes little endian for single integers
                    bytes_val = value.to_bytes(4, 'little')
                else:
                    continue
                    
                for i, b in enumerate(bytes_val):
                    self._memory[addr + i] = b
                    
            except ValueError:
                LOG.warning(f"Invalid address or value in mock file: {addr_str}: {value}")

    def _check_scenario(self, method_name, **kwargs):
        """Check if the active scenario should trigger an error for this method."""
        if not self.scenario or self.scenario not in self.SCENARIOS:
            return
        trigger, error_code, msg_template = self.SCENARIOS[self.scenario]
        if trigger != method_name:
            return
        if error_code is None:
            return  # Special handling (e.g. NO_DEVICES_FOUND returns [] instead of raising)
        msg = msg_template.format(**kwargs) if kwargs else msg_template
        # Use built-in exceptions where CommandHandler already has specific catch clauses
        if error_code == "PERMISSION_DENIED":
            raise PermissionError(msg)
        elif error_code == "CONNECT_TIMEOUT":
            raise TimeoutError(msg)
        else:
            raise ProbeError(msg, error_code)

    def list_devices(self):
        """Return a fake device list so clients can 'select' something"""
        if self.scenario == "NO_DEVICES_FOUND":
            return []
        if self.scenario == "PROBE_DRIVER_MISMATCH":
            # Show a second incompatible device to simulate multi-probe setup
            incompatible = {
                "device": self._generate_device_path("XDS110") if self.mock_type != "ti" else self._generate_device_path("ST-Link"),
                "description": "ESP32 JTAG/serial debug probe" if self.mock_type != "esp" else "STM32 STLink",
                "manufacturer": "Espressif" if self.mock_type != "esp" else "STMicroelectronics",
                "vid": 0x303A if self.mock_type != "esp" else 0x0483,
                "pid": 0x1001 if self.mock_type != "esp" else 0x3748,
                "hwid": self._generate_hwid(0x303A, 0x1001),
                "product": "ESP32-C3 Built-in JTAG" if self.mock_type != "esp" else "STM32 Debugger",
            }
            return [self.device_info, incompatible]
        return [self.device_info]

    def is_open(self):
        return self._is_open

    async def set_port(self, port: str):
        pass

    async def connect(self):
        if self.scenario == "CONNECT_TIMEOUT":
            await asyncio.sleep(3)
        self._check_scenario("connect")
        self._is_open = True
        self._connect_time = time.monotonic()
        LOG.info("open")
        return True

    async def disconnect(self):
        self._is_open = False
        LOG.info("close")
        return False

    def _generate_waveform(self, addr, t, nb=4):
        """Generate a demo value for the register at ``addr``.

        In demo mode the value is keyed to the DEMO_TREE variable's *meaning*
        (rpm, current, temperature, duty, ADC mV, uptime) so the live demo reads
        realistic telemetry instead of an abstract waveform. Non-demo mocks (and
        any demo address outside the tree) keep the original address-bit waveform.
        Returns a float; read() truncates and packs it into ``nb`` bytes.
        """
        freq = self._wave_freq

        # Demo mode: realistic, per-variable values (addresses match DEMO_TREE leaves).
        if self.demo:
            w = 2 * math.pi * freq * t  # base angular phase
            if addr == 0x2000:            # sys.motor.rpm (I32) — ~100..2900 rpm
                return 1500 + 1400 * math.sin(w)
            if addr == 0x2004:            # sys.motor.current_ma (I32) — ~150..1450 mA, trails rpm
                return 800 + 650 * math.sin(w - 0.4)
            if addr == 0x2008:            # sys.motor.temp_c (I32) — slow drift 25..75 C
                return 50 + 25 * math.sin(2 * math.pi * (freq / 8.0) * t)
            if addr == 0x200C:            # sys.motor.duty (U08) — PWM duty 5..95 %
                return 50 + 45 * math.sin(w + 0.6)
            if 0x2010 <= addr <= 0x2016:  # sys.adc_mv[0..3] (I16) — 150..3150 mV, 90deg/channel
                ch = (addr - 0x2010) // 2
                return 1650 + 1500 * math.sin(w + ch * (math.pi / 2))
            if addr == 0x201C:            # sys.uptime_ms (U32) — monotonic ms since connect
                return t * 1000.0
            # demo address outside DEMO_TREE → fall through to the generic waveform

        # Original generic waveform: type selected by address bits. Used by non-demo
        # mocks and any demo leaf not mapped above.
        mode = (addr >> 2) & 0x3
        phase_shift = (addr % 10) * 0.7  # per-address phase offset

        # Scale offset and amplitude to fit within the data type range
        if nb == 1:     # U08: 0-255
            offset, amp = 128, 100
        elif nb == 2:   # U16: 0-65535
            offset, amp = 500, 400
        else:           # U32/I32/F32: use configured defaults
            offset, amp = self._wave_offset, self._wave_amp

        if mode == 0:  # Sine wave
            return offset + amp * math.sin(2 * math.pi * freq * t + phase_shift)
        elif mode == 1:  # Cosine
            return offset + amp * math.cos(2 * math.pi * freq * t + phase_shift)
        elif mode == 2:  # Triangle wave
            period = 1.0 / freq
            phase = (t % period) / period
            if phase < 0.5:
                return offset - amp + (4 * amp * phase)
            else:
                return offset + amp - (4 * amp * (phase - 0.5))
        else:  # Sawtooth / counter
            period = 1.0 / freq
            phase = (t % period) / period
            return offset - amp + (2 * amp * phase)

    async def read(self, addr, nb: int):
        self._check_scenario("read", addr=addr)
        if not self._is_open:
             LOG.warning("Read attempted on closed probe")

        # Simulate probe read latency
        if self._read_delay > 0:
            await asyncio.sleep(self._read_delay)

        # Generate dynamic data for addresses that exist in memory
        if addr in self._memory and self._connect_time is not None:
            t = time.monotonic() - self._connect_time
            val = self._generate_waveform(addr, t, nb)

            # Pack the waveform value into the requested byte size as integer
            int_val = int(val)
            if nb == 1:
                int_val = max(0, min(255, int_val))
                self._memory[addr] = int_val & 0xFF
            elif nb == 2:
                int_val = max(0, min(65535, int_val))
                packed = struct.pack('<H', int_val)
                for i in range(2):
                    self._memory[addr + i] = packed[i]
            elif nb == 4:
                packed = struct.pack('<i', int_val)
                for i in range(4):
                    self._memory[addr + i] = packed[i]
            elif nb == 8:
                packed = struct.pack('<q', int_val)
                for i in range(8):
                    self._memory[addr + i] = packed[i]

        LOG.debug(f"read addr={hex(addr)} nb={nb}")
        data = bytearray()
        for i in range(nb):
            v = self._memory.get(addr + i, 0)
            data.append(v)

        return bytes(data)

    async def write(self, addr, data: bytes):
        self._check_scenario("write", addr=addr)
        if not self._is_open:
             LOG.warning("Write attempted on closed probe")

        LOG.debug(f"write addr={hex(addr)} len={len(data)}")
        for i, b in enumerate(data):
            self._memory[addr + i] = b
            # DEBUG
            # print(f"[DEBUG] Write @ {hex(addr+i)} = {hex(b)}")
