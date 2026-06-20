#!/usr/bin/env python3

import logging
import json
import os
import struct
import time
import asyncio
import websockets
import probe

from probe.debugprobe import DebugProbe
from probe.dummyprobe import DummyProbe
from probe.pyocd_probe import PyOCDProbe
from probe.errors import ProbeError

# OCD/serial drivers (STM32G4, ESP32-C3, TI C2000) are kept out-of-tree for
# future work; import gracefully so the server still runs when they are absent.
try:
    from probe.ocd_esp32c3 import OCD_ESP32C3_Probe
except ImportError:
    OCD_ESP32C3_Probe = None

from typing import Dict, Tuple, Callable, Any, Optional
from desktop.config import VERSION

LOG = logging.getLogger(__name__)


class ErrorCode:
    """Structured error codes sent to the frontend."""
    NO_DEVICES = "NO_DEVICES_FOUND"
    DEVICE_BUSY = "DEVICE_BUSY"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CONNECT_TIMEOUT = "CONNECT_TIMEOUT"
    PROBE_MISMATCH = "PROBE_DRIVER_MISMATCH"
    READ_WRITE_FAILED = "READ_WRITE_FAILED"
    UNKNOWN = "UNKNOWN_CONNECTION_ERROR"

    # Cortex-M family (SWD/CMSIS-DAP debug architecture)
    CORTEX_M_DEBUG_PORT_LOCKED = "CORTEX_M_DEBUG_PORT_LOCKED"
    CORTEX_M_SWD_PROTOCOL_ERROR = "CORTEX_M_SWD_PROTOCOL_ERROR"
    CORTEX_M_TARGET_IN_RESET = "CORTEX_M_TARGET_IN_RESET"
    CORTEX_M_TARGET_NOT_HALTED = "CORTEX_M_TARGET_NOT_HALTED"
    CORTEX_M_FLASH_WRITE_PROTECTED = "CORTEX_M_FLASH_WRITE_PROTECTED"
    CORTEX_M_UNSUPPORTED_TARGET = "CORTEX_M_UNSUPPORTED_TARGET"
    CORTEX_M_HARDFAULT_DETECTED = "CORTEX_M_HARDFAULT_DETECTED"

    # Flash operations
    FLASH_FILE_NOT_FOUND = "FLASH_FILE_NOT_FOUND"
    FLASH_UNSUPPORTED_FORMAT = "FLASH_UNSUPPORTED_FORMAT"
    FLASH_VERIFICATION_FAILED = "FLASH_VERIFICATION_FAILED"
    FLASH_ALREADY_RUNNING = "FLASH_ALREADY_RUNNING"
    FLASH_PROBE_NOT_CONNECTED = "FLASH_PROBE_NOT_CONNECTED"
    FLASH_CANCELLED = "FLASH_CANCELLED"
    FLASH_NO_BOOT_MEMORY = "FLASH_NO_BOOT_MEMORY"
    FLASH_PROGRAM_FAILED = "FLASH_PROGRAM_FAILED"
    BROWSE_PERMISSION_DENIED = "BROWSE_PERMISSION_DENIED"
    BROWSE_INVALID_PATH = "BROWSE_INVALID_PATH"

    # TI C2000 family (Kolbus/XDS protocol) -- placeholders for future
    # TI_C2X_...

    # Espressif family (OpenOCD/JTAG) -- placeholders for future
    # ESP_...


PROBE_MAP = {
    "PyOCDProbe": PyOCDProbe,
    "DummyProbe": DummyProbe,
}
if OCD_ESP32C3_Probe is not None:
    PROBE_MAP["OCD_ESP32C3_Probe"] = OCD_ESP32C3_Probe

ESPRESSIF_VID = 0x303A

class CommandHandler:
    def __init__(self, probe: DebugProbe, on_state_change: Optional[Callable] = None):
        self.probe = probe
        # Fired whenever the active probe changes (so the tray can re-render its
        # demo checkmark / "(Demo)" label even when demo is toggled remotely over
        # the WebSocket, e.g. by the VS Code extension's enter_demo).
        self._on_state_change = on_state_change
        self._capture_task: Optional[asyncio.Task] = None
        self._cancel_capture: bool = False
        self._flash_task: Optional[asyncio.Task] = None
        self._cancel_flash: bool = False
        self._pack_task: Optional[asyncio.Task] = None
        self._websocket = None  # set per-client in handle_client
        self._device_probe_map: Dict[str, str] = {}
        # Per-device target overrides (device_uri -> pyocd target name)
        self._target_overrides: Dict[str, str] = {}
        self._pack_cache = None  # lazy-loaded cmsis_pack_manager.Cache
        self._setup_command_handlers()

    def _setup_command_handlers(self):
        """Setup command handlers with proper argument validation"""
        self._CMD_HANDLERS: Dict[str, Tuple[Callable, int, bool]] = {
            # Command name: (handler_method, required_args, is_async)
            'list_devices': (self._handle_list_devices, 0, False),
            'list_probes': (self._handle_list_probes, 0, False),
            'set_probe': (self._handle_set_probe, 1, False),
            'enter_demo': (self._handle_enter_demo, 0, False),
            'get_driver_list': (self._handle_get_driver_list, 0, False),
            'connect': (self._handle_connect, 1, True),
            'disconnect': (self._handle_disconnect, 0, True),
            'read': (self._handle_read, 2, True),
            'write': (self._handle_write, 2, True),
            'calibrate': (self._handle_calibrate, 0, True),
            'capture': (self._handle_capture, 3, True),
            'stop_capture': (self._handle_stop_capture, 0, True),
            'browse_files': (self._handle_browse_files, 0, False),
            'flash': (self._handle_flash, 1, True),
            'cancel_flash': (self._handle_cancel_flash, 0, True),
            'search_targets': (self._handle_search_targets, 0, False),
            'install_pack': (self._handle_install_pack, 1, True),
            'set_target': (self._handle_set_target, 1, False),
            'get_target_info_ext': (self._handle_get_target_info_ext, 0, False),
        }

    async def execute_command(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """
        Polymorphic command executor - single entry point for all commands
        """
        response = {
            "version": 1,
            "sdk_version": VERSION,
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
            
        except PermissionError as e:
            LOG.error(f"Permission error in '{command_name}': {e}")
            return self._create_error_response(str(e), error_code=ErrorCode.PERMISSION_DENIED)
        except TimeoutError as e:
            LOG.error(f"Timeout in '{command_name}': {e}")
            return self._create_error_response(str(e), error_code=ErrorCode.CONNECT_TIMEOUT)
        except ProbeError as e:
            LOG.error(f"Probe error in '{command_name}': {e}")
            return self._create_error_response(str(e), error_code=e.error_code)
        except Exception as e:
            LOG.error(f"Error executing command '{command_name}': {e}")
            error_code = ErrorCode.UNKNOWN
            msg = str(e).lower()
            if 'busy' in msg or 'in use' in msg:
                error_code = ErrorCode.DEVICE_BUSY
            elif 'transfer' in msg or 'fault' in msg or 'memory' in msg:
                error_code = ErrorCode.READ_WRITE_FAILED
            elif 'no probe' in msg or 'no debug' in msg:
                error_code = ErrorCode.NO_DEVICES
            return self._create_error_response(str(e), error_code=error_code)

    def _create_error_response(self, msg: str, status: int = 1, error_code: str = None) -> Dict[str, Any]:
        """Create standardized error response"""
        resp = {
            "version": 1,
            "sdk_version": VERSION,
            "status": status,
            "msg": msg,
        }
        if error_code:
            resp["error_code"] = error_code
        return resp

    def _create_success_response(self, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create standardized success response"""
        response = {"status": 0}
        if data:
            response.update(data)
        return response

    def _notify_state_change(self) -> None:
        """Tell observers (the tray) the active probe changed, so UI bound to the
        live demo state refreshes. Best-effort: never let a UI hook break a command."""
        if self._on_state_change:
            try:
                self._on_state_change()
            except Exception as e:
                LOG.debug(f"on_state_change hook failed: {e}")

    # Command handlers
    def _handle_list_probes(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Handle get_probe_list command"""
        probes = list(PROBE_MAP.keys())
        active = type(self.probe).__name__
        return self._create_success_response({"probes": probes, "active_probe": active})

    def _handle_set_probe(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Handle set_probe command"""
        probe_name = cmd.get("probe_name")
        if not probe_name:
            raise ValueError("Probe name is required")
        
        if probe_name not in PROBE_MAP:
            raise ValueError(f"Unknown probe type: {probe_name}")
        
        # Note: Set probe re-initializes without args. Mock data would be lost if switching away and back.
        self.probe = PROBE_MAP[probe_name]()
        self._notify_state_change()
        return self._create_success_response({"msg": "Probe set successfully"})

    def _handle_enter_demo(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Switch to DummyProbe in demo mode (synthetic waveforms, no hardware).

        Mirrors the tray 'Demo Mode' toggle so a WebSocket client (e.g. the
        VS Code extension) can show live values without real hardware or an ELF.
        After this, list_devices returns demo_registers and read/capture emit
        the generated waveforms.
        """
        # Lazy import: server_thread imports this module, so a top-level import
        # would be circular. DEMO_KWARGS is the single source of demo settings.
        from desktop.server_thread import DEMO_KWARGS
        self.probe = DummyProbe(**DEMO_KWARGS)
        LOG.info("Entered demo mode via WebSocket (DummyProbe + DEMO_KWARGS)")
        self._notify_state_change()
        return self._create_success_response({"demo": True})

    def _handle_get_driver_list(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Handle get_driver_list command"""
        class_names = self.probe.get_driver_list()
        return self._create_success_response({"drivers": class_names})

    def _handle_list_devices(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Aggregate devices from all probe types with auto-detection."""
        # Demo mode: just use the DummyProbe's device list
        if getattr(self.probe, 'demo', False):
            devices = self.probe.list_devices()
            # Nested synthetic structure (struct + array) so clients can show
            # register drill-down without an ELF. Shape matches DebugInfoNode.
            data: Dict[str, Any] = {
                "devices": devices,
                "demo": True,
                "demo_registers": self.probe.DEMO_TREE,
            }
            return self._create_success_response(data)

        all_devices = []
        self._device_probe_map = {}
        pyocd_uids = set()

        # ARM Cortex-M: scan via PyOCD (CMSIS-DAP probes)
        try:
            for d in PyOCDProbe().list_devices():
                d['family'] = 'ARM Cortex-M'
                all_devices.append(d)
                self._device_probe_map[d['device']] = 'PyOCDProbe'
                pyocd_uids.add(d['device'])
        except Exception as e:
            LOG.debug(f"PyOCD scan skipped: {e}")

        # USB serial: all devices with VID/PID (real hardware)
        try:
            for d in DebugProbe().list_devices():
                if d['device'] in pyocd_uids:
                    continue
                if d.get('vid') == ESPRESSIF_VID:
                    d['family'] = 'ESP32'
                    self._device_probe_map[d['device']] = 'OCD_ESP32C3_Probe'
                all_devices.append(d)
        except Exception as e:
            LOG.debug(f"USB serial scan skipped: {e}")

        return self._create_success_response({"devices": all_devices})

    async def _handle_connect(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Handle connect command — auto-switches probe based on device family."""
        if not cmd or not cmd["uri"]:
            return self._create_error_response("No device uri specified")

        uri = cmd["uri"]
        target_probe = self._device_probe_map.get(uri)
        if target_probe and target_probe in PROBE_MAP:
            current = type(self.probe).__name__
            if current != target_probe:
                LOG.info(f"Auto-switching probe: {current} -> {target_probe}")
                self.probe = PROBE_MAP[target_probe]()
                self._notify_state_change()

        # Apply per-device target override (set via set_target), if any
        override = self._target_overrides.get(uri) or cmd.get("target")
        if override and hasattr(self.probe, 'set_target_override'):
            # Ensure the target is registered (installed pack -> populate_target)
            try:
                from pyocd.target.pack.pack_target import ManagedPacks
                ManagedPacks.populate_target(override)
            except Exception as e:
                LOG.debug(f"populate_target({override}) skipped: {e}")
            self.probe.set_target_override(override)

        await self.probe.set_port(uri)
        is_open = await self.probe.connect()
        data: Dict[str, Any] = {"is_open": is_open}
        target_info = self.probe.get_target_info()
        if target_info:
            data["target"] = target_info
        return self._create_success_response(data)

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

    async def _handle_calibrate(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Measure probe read latency by reading a test address 10 times."""
        if not self.probe.is_open():
            raise ProbeError("Probe not connected", "READ_WRITE_FAILED")

        test_addr = 0x20000000
        num_reads = 10
        loop = asyncio.get_event_loop()

        t0 = loop.time()
        for _ in range(num_reads):
            await self.probe.read(test_addr, 4)
        elapsed = loop.time() - t0

        per_read_ms = round((elapsed / num_reads) * 1000, 2)
        LOG.info(f"Calibration: {num_reads} reads in {elapsed*1000:.1f}ms, per_read_ms={per_read_ms}")
        return self._create_success_response({"per_read_ms": per_read_ms})

    async def _handle_capture(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Start a server-side capture as a background task."""
        if self._capture_task and not self._capture_task.done():
            raise ProbeError("Capture already running", "CAPTURE_BUSY")
        if not self.probe.is_open():
            raise ProbeError("Probe not connected", "READ_WRITE_FAILED")

        channels = cmd.get("channels")
        rate_hz = cmd.get("rate_hz")
        duration_s = cmd.get("duration_s")

        if not channels or not rate_hz or not duration_s:
            raise ValueError("'channels', 'rate_hz', and 'duration_s' are required")

        self._cancel_capture = False
        capture_id = cmd.get("id")
        self._capture_task = asyncio.create_task(
            self._run_capture(self._websocket, channels, rate_hz, duration_s, capture_id)
        )
        return self._create_success_response({"msg": "capture_started"})

    async def _run_capture(self, websocket, channels, rate_hz, duration_s, capture_id):
        """Server-side capture loop. Reads channels at the target rate and pushes results when done."""
        period = 1.0 / rate_hz
        samples = []
        loop = asyncio.get_event_loop()
        start = loop.time()
        max_samples = int(rate_hz * duration_s)

        try:
            for i in range(max_samples):
                if self._cancel_capture:
                    break
                t = (loop.time() - start) * 1000  # ms since capture start
                row = [round(t, 2)]
                for ch in channels:
                    raw = await self.probe.read(ch["addr"], ch["nb"])
                    row.append(self._decode_value(raw, ch.get("type", "U32")))
                samples.append(row)

                # Sleep to maintain target rate
                elapsed = loop.time() - start
                target = (i + 1) * period
                sleep_time = target - elapsed
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
        except Exception as e:
            LOG.error(f"Capture error at sample {len(samples)}: {e}")

        actual_elapsed = loop.time() - start
        actual_hz = round(len(samples) / actual_elapsed, 1) if actual_elapsed > 0 else 0

        try:
            await websocket.send(json.dumps({
                "type": "capture_complete",
                "capture_id": capture_id,
                "samples": samples,
                "actual_hz": actual_hz,
                "total_samples": len(samples)
            }))
        except Exception as e:
            LOG.error(f"Failed to send capture results: {e}")

        self._capture_task = None
        self._cancel_capture = False

    async def _handle_stop_capture(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Stop an active capture. The capture task will send partial results."""
        if self._capture_task and not self._capture_task.done():
            self._cancel_capture = True
            return self._create_success_response({"msg": "capture_stopped"})
        return self._create_success_response({"msg": "no_active_capture"})

    # --- Flash operations ---

    SUPPORTED_FLASH_EXTS = ('.hex', '.bin', '.elf', '.axf')

    def _handle_browse_files(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """List files and directories under the given path. Security: restricted to $HOME."""
        directory = cmd.get("directory") or os.path.expanduser("~")
        extensions = cmd.get("extensions") or list(self.SUPPORTED_FLASH_EXTS)

        # Normalize and resolve to absolute, real path (follows symlinks)
        try:
            resolved = os.path.realpath(os.path.expanduser(directory))
        except Exception as e:
            raise ProbeError(f"Invalid path: {directory}", ErrorCode.BROWSE_INVALID_PATH)

        home = os.path.realpath(os.path.expanduser("~"))
        # Security: reject paths outside $HOME
        if not (resolved == home or resolved.startswith(home + os.sep)):
            raise ProbeError(
                "Access denied: path is outside the home directory",
                ErrorCode.BROWSE_PERMISSION_DENIED
            )

        if not os.path.isdir(resolved):
            raise ProbeError(
                f"Not a directory: {directory}",
                ErrorCode.BROWSE_INVALID_PATH
            )

        ext_set = {e.lower() for e in extensions}
        entries = []

        try:
            for name in os.listdir(resolved):
                if name.startswith('.'):
                    continue
                full_path = os.path.join(resolved, name)
                try:
                    if os.path.isdir(full_path):
                        entries.append({"name": name, "type": "dir"})
                    elif os.path.isfile(full_path):
                        if ext_set:
                            _, ext = os.path.splitext(name)
                            if ext.lower() not in ext_set:
                                continue
                        try:
                            size = os.path.getsize(full_path)
                        except OSError:
                            size = 0
                        entries.append({"name": name, "type": "file", "size": size})
                except OSError:
                    continue  # skip unreadable entries
        except PermissionError:
            raise ProbeError(
                f"Permission denied: {directory}",
                ErrorCode.BROWSE_PERMISSION_DENIED
            )

        # Sort: directories first (alphabetical), then files (alphabetical), case-insensitive
        entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"].lower()))

        # Determine parent (None if already at or above home)
        parent = os.path.dirname(resolved) if resolved != home else None
        if parent and not (parent == home or parent.startswith(home + os.sep)):
            parent = None

        return self._create_success_response({
            "directory": resolved,
            "parent": parent,
            "entries": entries[:500]
        })

    async def _handle_flash(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Start a firmware flash operation as a background task."""
        if self._flash_task and not self._flash_task.done():
            raise ProbeError("Flash operation already running", ErrorCode.FLASH_ALREADY_RUNNING)
        if self._capture_task and not self._capture_task.done():
            raise ProbeError(
                "Cannot flash while capture is running. Stop capture first.",
                ErrorCode.FLASH_ALREADY_RUNNING
            )
        if not self.probe.is_open():
            raise ProbeError("Probe not connected", ErrorCode.FLASH_PROBE_NOT_CONNECTED)

        file_path = cmd.get("file_path")
        if not file_path:
            raise ValueError("'file_path' is required")

        # Expand ~ and resolve to absolute path so the SDK reads the intended file
        file_path = os.path.realpath(os.path.expanduser(file_path))

        if not os.path.isfile(file_path):
            raise ProbeError(f"File not found: {file_path}", ErrorCode.FLASH_FILE_NOT_FOUND)

        _, ext = os.path.splitext(file_path)
        if ext.lower() not in self.SUPPORTED_FLASH_EXTS:
            raise ProbeError(
                f"Unsupported firmware format: {ext}",
                ErrorCode.FLASH_UNSUPPORTED_FORMAT
            )

        chip_erase = cmd.get("chip_erase", "auto")
        verify = cmd.get("verify", True)
        no_reset = cmd.get("no_reset", False)
        flash_id = cmd.get("id")

        self._cancel_flash = False
        self._flash_task = asyncio.create_task(
            self._run_flash(
                self._websocket, file_path, chip_erase, verify, no_reset, flash_id
            )
        )
        return self._create_success_response({"msg": "flash_started"})

    async def _run_flash(self, websocket, file_path, chip_erase,
                         verify, no_reset, flash_id):
        """Background flash task. Delegates to mock or PyOCD implementation."""
        # DummyProbe: simulate flash for demo/testing without hardware
        if isinstance(self.probe, DummyProbe):
            await self._run_flash_mock(websocket, file_path, flash_id)
            return

        loop = asyncio.get_event_loop()
        start_time = loop.time()
        last_send = [0.0]  # mutable box so the nested closure can update it

        def send_push(payload):
            try:
                asyncio.run_coroutine_threadsafe(
                    websocket.send(json.dumps(payload)), loop
                )
            except Exception as e:
                LOG.error(f"Failed to push flash progress: {e}")

        def progress_callback(amount):
            # Called from PyOCD (executor thread). amount is 0.0..1.0.
            now = time.monotonic()
            # Throttle to max 10 updates/sec (but always send the final 1.0)
            if now - last_send[0] < 0.1 and amount < 1.0:
                return
            last_send[0] = now
            if amount < 0.3:
                phase = "erasing"
            elif amount < 0.9:
                phase = "programming"
            else:
                phase = "verifying"
            send_push({
                "type": "flash_progress",
                "flash_id": flash_id,
                "phase": phase,
                "progress": round(float(amount), 3),
            })

        success = False
        error_code = None
        error_msg = None
        try:
            # Pre-flight: ensure target has a usable memory map
            memory_map = getattr(self.probe.target, 'memory_map', None)
            boot_region = None
            if memory_map is not None:
                try:
                    boot_region = memory_map.get_boot_memory()
                except Exception:
                    boot_region = None
            if boot_region is None:
                raise RuntimeError(
                    "No boot memory is defined for this device. "
                    "PyOCD likely auto-detected a generic Cortex-M without a CMSIS memory map. "
                    "Restart the SDK with an explicit --target (e.g. --target stm32g474retx)."
                )

            LOG.info(
                f"Flashing {file_path} "
                f"(size={os.path.getsize(file_path)} bytes, "
                f"boot_region=0x{boot_region.start:08X}-0x{boot_region.end:08X}, "
                f"chip_erase={chip_erase}, smart_flash={verify})"
            )

            # Send an early progress ping so UI knows work has started
            # (PyOCD may spend seconds in setup before firing its callback)
            send_push({
                "type": "flash_progress",
                "flash_id": flash_id,
                "phase": "erasing",
                "progress": 0.0,
            })

            # Halt the target before flashing. Our session connects in "attach"
            # mode (doesn't halt), but a running CPU can race the flash
            # controller and cause PGSERR (result code 0x1).
            try:
                self.probe.target.halt()
                LOG.info("Target halted for flashing")
            except Exception as e:
                LOG.warning(f"Could not halt target before flash: {e}")

            # PyOCD's FileProgrammer.program() is blocking - run in executor
            def do_flash():
                from pyocd.flash.file_programmer import FileProgrammer
                programmer = FileProgrammer(
                    self.probe.session,
                    progress=progress_callback,
                    chip_erase=chip_erase,
                    # smart_flash reads entire flash to skip matching sectors — can
                    # appear stuck on large chips. Disable for responsive progress.
                    smart_flash=False,
                    trust_crc=False,
                    no_reset=True,  # reset handled separately below
                )
                programmer.program(file_path)
                LOG.info("Flash programming completed")

            await loop.run_in_executor(None, do_flash)

            # Auto-reset MCU so new firmware starts running
            if not no_reset:
                try:
                    self.probe.target.reset()
                except Exception as e:
                    LOG.warning(f"Post-flash reset failed: {e}")
            else:
                # If caller asked to skip reset, at least resume so the CPU
                # isn't left halted from our pre-flash halt().
                try:
                    self.probe.target.resume()
                except Exception as e:
                    LOG.warning(f"Post-flash resume failed: {e}")

            success = True
        except asyncio.CancelledError:
            success = False
            error_code = ErrorCode.FLASH_CANCELLED
            error_msg = "Flash cancelled"
            LOG.info("Flash task cancelled")
        except Exception as e:
            success = False
            msg = str(e)
            msg_lower = msg.lower()
            if 'write protected' in msg_lower or 'flash protected' in msg_lower:
                error_code = ErrorCode.CORTEX_M_FLASH_WRITE_PROTECTED
            elif 'page failure' in msg_lower or 'pgserr' in msg_lower or 'result code' in msg_lower:
                error_code = ErrorCode.FLASH_PROGRAM_FAILED
            elif 'no boot memory' in msg_lower or 'no flash' in msg_lower or 'no memory region' in msg_lower:
                error_code = ErrorCode.FLASH_NO_BOOT_MEMORY
            elif 'no such file' in msg_lower or 'not found' in msg_lower:
                error_code = ErrorCode.FLASH_FILE_NOT_FOUND
            elif 'verification' in msg_lower or 'verify' in msg_lower:
                error_code = ErrorCode.FLASH_VERIFICATION_FAILED
            elif 'unsupported' in msg_lower and ('format' in msg_lower or 'file' in msg_lower):
                error_code = ErrorCode.FLASH_UNSUPPORTED_FORMAT
            else:
                error_code = ErrorCode.UNKNOWN
            error_msg = msg
            LOG.error(f"Flash failed: {e}")

            # Try to resume the target so it's not left halted after a failure
            try:
                if self.probe.target is not None and self.probe.is_open():
                    self.probe.target.resume()
            except Exception as resume_err:
                LOG.warning(f"Could not resume target after flash error: {resume_err}")

        # Send completion push message
        try:
            if success:
                elapsed_ms = round((loop.time() - start_time) * 1000)
                try:
                    bytes_programmed = os.path.getsize(file_path)
                except OSError:
                    bytes_programmed = 0
                await websocket.send(json.dumps({
                    "type": "flash_complete",
                    "flash_id": flash_id,
                    "success": True,
                    "duration_ms": elapsed_ms,
                    "bytes_programmed": bytes_programmed,
                }))
            else:
                await websocket.send(json.dumps({
                    "type": "flash_complete",
                    "flash_id": flash_id,
                    "success": False,
                    "error_code": error_code,
                    "msg": error_msg,
                }))
        except Exception as e:
            LOG.error(f"Failed to send flash completion: {e}")

        self._flash_task = None
        self._cancel_flash = False

    async def _run_flash_mock(self, websocket, file_path, flash_id):
        """Simulated flash for DummyProbe. Emits synthetic progress messages."""
        loop = asyncio.get_event_loop()
        start_time = loop.time()

        # Scenario: simulate write-protected flash
        if getattr(self.probe, 'scenario', None) == "CORTEX_M_FLASH_WRITE_PROTECTED":
            await asyncio.sleep(0.3)
            await websocket.send(json.dumps({
                "type": "flash_complete",
                "flash_id": flash_id,
                "success": False,
                "error_code": ErrorCode.CORTEX_M_FLASH_WRITE_PROTECTED,
                "msg": "Flash write protected: region is locked",
            }))
            self._flash_task = None
            return

        phases = [
            ("erasing", 0.0, 0.3),
            ("programming", 0.3, 0.9),
            ("verifying", 0.9, 1.0),
        ]
        try:
            for phase, start, end in phases:
                steps = 8
                for i in range(1, steps + 1):
                    if self._cancel_flash:
                        await websocket.send(json.dumps({
                            "type": "flash_complete",
                            "flash_id": flash_id,
                            "success": False,
                            "error_code": ErrorCode.FLASH_CANCELLED,
                            "msg": "Flash cancelled",
                        }))
                        self._flash_task = None
                        self._cancel_flash = False
                        return
                    progress = start + (end - start) * (i / steps)
                    await websocket.send(json.dumps({
                        "type": "flash_progress",
                        "flash_id": flash_id,
                        "phase": phase,
                        "progress": round(progress, 3),
                    }))
                    await asyncio.sleep(0.12)
        except asyncio.CancelledError:
            try:
                await websocket.send(json.dumps({
                    "type": "flash_complete",
                    "flash_id": flash_id,
                    "success": False,
                    "error_code": ErrorCode.FLASH_CANCELLED,
                    "msg": "Flash cancelled",
                }))
            except Exception:
                pass
            self._flash_task = None
            self._cancel_flash = False
            return

        try:
            file_size = os.path.getsize(file_path) if os.path.isfile(file_path) else 0
        except OSError:
            file_size = 0
        elapsed_ms = round((loop.time() - start_time) * 1000)

        try:
            await websocket.send(json.dumps({
                "type": "flash_complete",
                "flash_id": flash_id,
                "success": True,
                "duration_ms": elapsed_ms,
                "bytes_programmed": file_size,
            }))
        except Exception as e:
            LOG.error(f"Failed to send mock flash completion: {e}")

        self._flash_task = None
        self._cancel_flash = False

    async def _handle_cancel_flash(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Cancel an active flash operation."""
        if self._flash_task and not self._flash_task.done():
            self._cancel_flash = True
            self._flash_task.cancel()
            return self._create_success_response({"msg": "flash_cancelled"})
        return self._create_success_response({"msg": "no_active_flash"})

    # --- CMSIS Pack management ---

    def _get_pack_cache(self):
        """Lazy-load the cmsis_pack_manager Cache (shared across calls)."""
        if self._pack_cache is None:
            try:
                from cmsis_pack_manager import Cache
                self._pack_cache = Cache(True, True)  # silent, no-timeouts
            except Exception as e:
                LOG.error(f"cmsis_pack_manager unavailable: {e}")
                raise
        return self._pack_cache

    def _get_installed_target_names(self) -> set:
        """Return a set of lowercased target names already installed locally."""
        try:
            from pyocd.target.pack.pack_target import ManagedPacks
            return {t.lower() for t in ManagedPacks.get_installed_targets()}
        except Exception as e:
            LOG.debug(f"Could not enumerate installed packs: {e}")
            return set()

    def _handle_search_targets(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Search the CMSIS-Pack index for targets matching a query string."""
        query = (cmd.get("query") or "").strip().lower()
        limit = int(cmd.get("limit", 30))

        try:
            cache = self._get_pack_cache()
        except Exception as e:
            raise ProbeError(f"Pack manager unavailable: {e}", ErrorCode.UNKNOWN)

        index = cache.index or {}
        if not index:
            # Fetch the descriptor list once so future searches work
            try:
                cache.cache_descriptors()
                index = cache.index or {}
            except Exception as e:
                LOG.warning(f"Descriptor download failed: {e}")

        installed = self._get_installed_target_names()
        results = []

        # Exact + substring match
        for name, meta in index.items():
            name_lower = name.lower()
            if query and query not in name_lower:
                continue
            memories = meta.get("memories") or {}
            flash_region = memories.get("IROM1") or memories.get("ROM1") or {}
            ram_region = memories.get("IRAM1") or memories.get("RAM1") or {}
            from_pack = meta.get("from_pack") or {}
            results.append({
                "name": name,
                "vendor": (meta.get("vendor") or "").split(":")[0],
                "flash_size": flash_region.get("size"),
                "ram_size": ram_region.get("size"),
                "pack": from_pack.get("pack"),
                "pack_vendor": from_pack.get("vendor"),
                "pack_version": from_pack.get("version"),
                "installed": name_lower in installed,
            })
            if len(results) >= limit:
                break

        # Sort: installed first, then alphabetical
        results.sort(key=lambda r: (not r["installed"], r["name"].lower()))
        return self._create_success_response({"results": results, "total": len(results)})

    async def _handle_install_pack(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Download and install the CMSIS pack containing a given target."""
        if self._pack_task and not self._pack_task.done():
            raise ProbeError("Pack install already running", ErrorCode.FLASH_ALREADY_RUNNING)

        target = cmd.get("target")
        if not target:
            raise ValueError("'target' is required")

        install_id = cmd.get("id")
        self._pack_task = asyncio.create_task(
            self._run_install_pack(self._websocket, target, install_id)
        )
        return self._create_success_response({"msg": "pack_install_started"})

    async def _run_install_pack(self, websocket, target, install_id):
        """Background pack install. Emits pack_progress + pack_complete push messages."""
        loop = asyncio.get_event_loop()

        def push(payload):
            try:
                asyncio.run_coroutine_threadsafe(
                    websocket.send(json.dumps(payload)), loop
                )
            except Exception as e:
                LOG.error(f"Pack push failed: {e}")

        push({"type": "pack_progress", "install_id": install_id,
              "phase": "preparing", "msg": "Checking index..."})

        try:
            cache = self._get_pack_cache()

            # Ensure we have a fresh descriptor index
            def ensure_index():
                if not cache.index:
                    push({"type": "pack_progress", "install_id": install_id,
                          "phase": "indexing", "msg": "Downloading pack index..."})
                    cache.cache_descriptors()

            await loop.run_in_executor(None, ensure_index)

            index = cache.index or {}
            if target not in index:
                # Try a case-insensitive fallback
                matches = [k for k in index.keys() if k.lower() == target.lower()]
                if matches:
                    target = matches[0]
                else:
                    raise RuntimeError(f"Target '{target}' not found in CMSIS-Pack index")

            push({"type": "pack_progress", "install_id": install_id,
                  "phase": "downloading",
                  "msg": f"Downloading pack for {target}..."})

            def do_install():
                # cmsis_pack_manager downloads all packs covering the requested device
                cache.packs_for_devices([index[target]])

            await loop.run_in_executor(None, do_install)

            push({"type": "pack_progress", "install_id": install_id,
                  "phase": "registering",
                  "msg": "Registering target with PyOCD..."})

            def register():
                from pyocd.target.pack.pack_target import ManagedPacks
                try:
                    ManagedPacks.populate_target(target)
                except Exception as e:
                    LOG.warning(f"populate_target({target}) after install: {e}")

            await loop.run_in_executor(None, register)

            installed_now = self._get_installed_target_names()
            await websocket.send(json.dumps({
                "type": "pack_complete",
                "install_id": install_id,
                "success": True,
                "target": target,
                "installed": target.lower() in installed_now,
            }))
            LOG.info(f"Pack for {target} installed")

        except asyncio.CancelledError:
            try:
                await websocket.send(json.dumps({
                    "type": "pack_complete",
                    "install_id": install_id,
                    "success": False,
                    "error_code": ErrorCode.FLASH_CANCELLED,
                    "msg": "Pack install cancelled",
                }))
            except Exception:
                pass
        except Exception as e:
            LOG.error(f"Pack install failed: {e}")
            try:
                await websocket.send(json.dumps({
                    "type": "pack_complete",
                    "install_id": install_id,
                    "success": False,
                    "error_code": ErrorCode.UNKNOWN,
                    "msg": str(e),
                }))
            except Exception:
                pass
        finally:
            self._pack_task = None

    def _handle_set_target(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Persist a per-device target override for the next connect."""
        target = cmd.get("target")
        uri = cmd.get("uri")
        if not uri:
            raise ValueError("'uri' is required")

        if not target:
            # Clear any existing override
            self._target_overrides.pop(uri, None)
            if hasattr(self.probe, 'set_target_override'):
                self.probe.set_target_override(None)
            return self._create_success_response({"msg": "target_cleared", "uri": uri})

        # Ensure target is known to PyOCD (from installed packs or built-ins)
        try:
            from pyocd.target.pack.pack_target import ManagedPacks
            ManagedPacks.populate_target(target)
        except Exception as e:
            LOG.debug(f"populate_target({target}) skipped: {e}")

        self._target_overrides[uri] = target
        if hasattr(self.probe, 'set_target_override'):
            self.probe.set_target_override(target)
        LOG.info(f"Target override for {uri}: {target}")
        return self._create_success_response({"msg": "target_set", "uri": uri, "target": target})

    def _handle_get_target_info_ext(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Return extended target info: current override, detected target, memory map."""
        data: Dict[str, Any] = {
            "target_override": getattr(self.probe, 'target_override', None),
            "overrides": dict(self._target_overrides),
        }
        target_info = self.probe.get_target_info()
        if target_info:
            data["detected"] = target_info

        # Memory map of the currently-connected target
        regions = []
        target = getattr(self.probe, 'target', None)
        if target is not None:
            memory_map = getattr(target, 'memory_map', None)
            if memory_map is not None:
                for r in memory_map:
                    regions.append({
                        "name": getattr(r, 'name', '?'),
                        "type": getattr(r, 'type', '?').name if hasattr(getattr(r, 'type', None), 'name') else str(getattr(r, 'type', '')),
                        "start": getattr(r, 'start', 0),
                        "length": getattr(r, 'length', 0),
                    })
        data["memory_map"] = regions
        return self._create_success_response(data)

    @staticmethod
    def _decode_value(raw: bytes, type_str: str):
        """Decode raw bytes into a numeric value based on type string.

        Type convention (from ELF parser):
            U08, U16, U32 - unsigned
            I08, I16, I32 - signed
            F32, F64      - float
        """
        t = type_str.upper()
        if "F64" in t and len(raw) >= 8:
            return round(struct.unpack('<d', raw[:8])[0], 6)
        if "F32" in t and len(raw) >= 4:
            return round(struct.unpack('<f', raw[:4])[0], 4)
        if "I32" in t and len(raw) >= 4:
            return struct.unpack('<i', raw[:4])[0]
        if "U32" in t and len(raw) >= 4:
            return struct.unpack('<I', raw[:4])[0]
        if "I16" in t and len(raw) >= 2:
            return struct.unpack('<h', raw[:2])[0]
        if "U16" in t and len(raw) >= 2:
            return struct.unpack('<H', raw[:2])[0]
        if "I08" in t and len(raw) >= 1:
            return struct.unpack('<b', raw[:1])[0]
        if "U08" in t and len(raw) >= 1:
            return raw[0]
        if len(raw) >= 4:
            return struct.unpack('<I', raw[:4])[0]
        return 0


class WebSocketServer:
    def __init__(self, host="ws://127.0.0.1", port=8765, probe_cls=DummyProbe, probe_kwargs=None, scenario=None,
                 on_state_change=None):
        """Initialize WebSocket server"""
        self.host = host
        self.port = port
        self.scenario = scenario
        if probe_kwargs is None:
            probe_kwargs = {}
        self.probe = probe_cls(**probe_kwargs)
        self.clients = set()
        self.handler = CommandHandler(self.probe, on_state_change=on_state_change)

        if hasattr(probe, 'init_probes'):
            probe.init_probes()

    async def handle_client(self, websocket):
        """Handle individual WebSocket client connections"""
        self.clients.add(websocket)
        self.handler._websocket = websocket
        LOG.info(f"Client connected. Total clients: {len(self.clients)}")

        try:
            async for message in websocket:
                try:
                    cmd = json.loads(message)
                    response = await self.process_command(cmd)
                    LOG.info(f"Sending response: {json.dumps(response)}")
                    await websocket.send(json.dumps(response))
                    # SDK_CONNECTION_LOST scenario: close WebSocket after successful connect
                    if (self.scenario == "SDK_CONNECTION_LOST"
                            and cmd.get("cmd") == "connect"
                            and response.get("status") == 0):
                        LOG.info("SDK_CONNECTION_LOST scenario: closing WebSocket in 3s")
                        asyncio.get_event_loop().call_later(
                            3.0, lambda: asyncio.ensure_future(websocket.close()))
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
            # Cancel any active capture for this client
            if self.handler._capture_task and not self.handler._capture_task.done():
                self.handler._cancel_capture = True
                self.handler._capture_task.cancel()
                self.handler._capture_task = None
            # Cancel any active flash for this client
            if self.handler._flash_task and not self.handler._flash_task.done():
                self.handler._cancel_flash = True
                self.handler._flash_task.cancel()
                self.handler._flash_task = None
            self.clients.remove(websocket)
            LOG.info(f"Client disconnected. Total clients: {len(self.clients)}")
            # Release the probe session when the last client goes away so a
            # browser refresh doesn't leave the USB probe claimed. Without
            # this, the next connect() would try to open a second PyOCD
            # session on the already-open probe and fail until the user
            # physically unplugs the device.
            if not self.clients and self.handler.probe.is_open():
                try:
                    await self.handler.probe.disconnect()
                    LOG.info("Released probe session after last client disconnect")
                except Exception as e:
                    LOG.warning(f"Failed to release probe on client disconnect: {e}")

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
        self._ws_server = await websockets.serve(
            self.handle_client,
            self.host,
            self.port
        )
        LOG.info(f"WebSocket server started on ws://{self.host}:{self.port}")

        # Keep the server running
        await self._ws_server.wait_closed()

    async def stop_server(self):
        """Stop the WebSocket server gracefully"""
        if hasattr(self, '_ws_server') and self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            LOG.info("WebSocket server stopped")


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
    parser.add_argument("--probe", action="store", dest="probe",
                        type=str, default="PyOCDProbe",
                        choices=list(PROBE_MAP.keys()),
                        help="Initial probe type")
    parser.add_argument("--mock-file", action="store", dest="mock_file",
                        help="JSON file to initialize mock memory (DummyProbe only)")
    parser.add_argument("--mock-type", action="store", dest="mock_type",
                        help="Type of device to mock (stm, ti) - sets description (DummyProbe only)")
    
    # USB Device Parameters (for realistic USB device mocking)
    parser.add_argument("--device-path", action="store", dest="device_path",
                       help="Device path (e.g., /dev/ttyUSB0, COM3) (DummyProbe only)")
    parser.add_argument("--vid", action="store", dest="vid", type=lambda x: int(x, 0),
                       help="USB Vendor ID (hex or decimal) (DummyProbe only)")
    parser.add_argument("--pid", action="store", dest="pid", type=lambda x: int(x, 0),
                       help="USB Product ID (hex or decimal) (DummyProbe only)")
    parser.add_argument("--manufacturer", action="store", dest="manufacturer",
                       help="USB device manufacturer name (DummyProbe only)")
    parser.add_argument("--product", action="store", dest="product",
                       help="USB device product name (DummyProbe only)")
    parser.add_argument("--description", action="store", dest="description",
                       help="USB device description (DummyProbe only)")
    parser.add_argument("--serial", action="store", dest="serial_number",
                       help="USB device serial number (DummyProbe only)")
    parser.add_argument("--target", action="store", dest="target_override",
                       help="Target override for PyOCD (e.g., stm32g474retx). Auto-detects if omitted.")
    parser.add_argument("--scenario", action="store", dest="scenario",
                       help="Error scenario for DummyProbe fault injection (DummyProbe only)")
    parser.add_argument("--fast", action="store_true", dest="fast",
                       help="Disable simulated read delay in DummyProbe (instant reads)")
    parser.add_argument("--mock-wave-freq", action="store", dest="wave_freq",
                       type=float, default=5.0,
                       help="Mock waveform base frequency in Hz (default: 5.0)")
    parser.add_argument("--mock-wave-amp", action="store", dest="wave_amp",
                       type=float, default=400,
                       help="Mock waveform amplitude (default: 400)")

    (args, _) = parser.parse_known_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    probe_cls = PROBE_MAP[args.probe]
    probe_kwargs = {}
    
    if args.probe == "DummyProbe":
        if args.mock_file:
            probe_kwargs["mock_file"] = args.mock_file
        if args.mock_type:
            probe_kwargs["mock_type"] = args.mock_type
        if args.device_path:
            probe_kwargs["device_path"] = args.device_path
        if args.vid is not None:
            probe_kwargs["vid"] = args.vid
        if args.pid is not None:
            probe_kwargs["pid"] = args.pid
        if args.manufacturer:
            probe_kwargs["manufacturer"] = args.manufacturer
        if args.product:
            probe_kwargs["product"] = args.product
        if args.description:
            probe_kwargs["description"] = args.description
        if args.serial_number:
            probe_kwargs["serial_number"] = args.serial_number
        if args.scenario:
            probe_kwargs["scenario"] = args.scenario
        if args.fast:
            probe_kwargs["read_delay_ms"] = 0
        probe_kwargs["wave_freq"] = args.wave_freq
        probe_kwargs["wave_amp"] = args.wave_amp
    elif args.probe == "PyOCDProbe":
        if args.target_override:
            probe_kwargs["target_override"] = args.target_override

    scenario = getattr(args, 'scenario', None)
    server = WebSocketServer(args.host, args.port, probe_cls, probe_kwargs, scenario=scenario)

    try:
        asyncio.run(server.start_server())
    except KeyboardInterrupt:
        LOG.info("Server stopped by user")


if __name__ == "__main__":
    main()
