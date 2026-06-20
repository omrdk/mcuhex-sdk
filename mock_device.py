#!/usr/bin/env python3
"""
Mock Device Server Launcher

This script launches a WebSocket server that can mock USB debug probes
with realistic device information. The mock device will appear in device
lists with the same structure as real USB devices.

The currently supported architecture is ARM Cortex-M via SWD:
    cortex_m  - ARM Cortex-M (STM32, nRF, RP2040, etc.) via SWD/CMSIS-DAP

Usage Examples:
    # Quick start - mock Cortex-M device with default ST-Link VID/PID
    python mock_device.py cortex_m

    # Mock Cortex-M device with custom JSON file
    python mock_device.py cortex_m -f path/to/symbols.json

    # Simulate a specific error scenario
    python mock_device.py cortex_m --scenario CORTEX_M_DEBUG_PORT_LOCKED

    # Run with debug output
    python mock_device.py cortex_m --debug

    # Run on custom port
    python mock_device.py cortex_m -P 9000

    # Use the real PyOCD hardware driver instead of mock
    python mock_device.py -t pyocd
"""
import subprocess
import sys
import os
import argparse

# Map friendly driver names to server probe classes.
# Only SWD (ARM Cortex-M) is supported today; OCD (STM32G4) and serial
# (TI C2000 / Espressif) drivers are kept out-of-tree for future work.
DRIVER_MAP = {
    "mock": "DummyProbe",
    "pyocd": "PyOCDProbe",
}

# Valid scenario names (must match ConnectionErrorCode values in web client)
SCENARIO_NAMES = [
    # Generic connection errors
    "NO_DEVICES_FOUND",
    "DEVICE_BUSY",
    "PERMISSION_DENIED",
    "CONNECT_TIMEOUT",
    "PROBE_DRIVER_MISMATCH",
    "READ_WRITE_FAILED",
    "SDK_CONNECTION_LOST",
    # Cortex-M family
    "CORTEX_M_DEBUG_PORT_LOCKED",
    "CORTEX_M_SWD_PROTOCOL_ERROR",
    "CORTEX_M_TARGET_IN_RESET",
    "CORTEX_M_TARGET_NOT_HALTED",
    "CORTEX_M_FLASH_WRITE_PROTECTED",
    "CORTEX_M_UNSUPPORTED_TARGET",
    "CORTEX_M_HARDFAULT_DETECTED",
]


def main():
    """
    Run the server. By default runs in mock mode (DummyProbe),
    but can also launch real drivers.
    """
    parser = argparse.ArgumentParser(
        description="Device Server Launcher - Mock USB debug probes with "
                    "realistic device information",
        epilog="""
Examples:
  # Quick start - mock Cortex-M device
  %(prog)s cortex_m

  # Mock Cortex-M device with custom memory data
  %(prog)s cortex_m -f path/to/symbols.json

  # Simulate debug port locked error
  %(prog)s cortex_m --scenario CORTEX_M_DEBUG_PORT_LOCKED

  # Simulate device busy error
  %(prog)s cortex_m --scenario DEVICE_BUSY

  # Run with debug output
  %(prog)s cortex_m --debug
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    # Positional argument for quick profile selection (by debug architecture)
    parser.add_argument(
        "profile", nargs="?", choices=["cortex_m"],
        help="Device family profile: cortex_m (ARM SWD)")

    # Symbol/memory file selection
    parser.add_argument(
        "-f", "--file", dest="mock_file",
        help="Specific JSON symbol/memory file (Mock driver only)")

    # Driver Selection
    parser.add_argument(
        "-t", "--type", dest="driver_type",
        choices=list(DRIVER_MAP.keys()), default="mock",
        help="Driver type to use "
             "(mock=simulation, pyocd=ARM Cortex-M via SWD)")

    parser.add_argument(
        "--debug", action="store_true", help="Enable debug output")
    parser.add_argument(
        "-P", "--port", type=int, default=8765,
        help="Port to run server on")
    parser.add_argument(
        "-s", "--scenario", dest="scenario",
        choices=SCENARIO_NAMES,
        help="Error scenario to simulate (mock driver only). "
             "Names match ConnectionErrorCode in the web client.")

    # USB Device Parameters (for realistic USB device mocking)
    parser.add_argument(
        "--device-path", dest="device_path",
        help="Device path (e.g., /dev/ttyUSB0, COM3, "
             "/dev/cu.usbserial-0001)")
    parser.add_argument(
        "--vid", type=lambda x: int(x, 0),
        help="USB Vendor ID (hex or decimal, e.g., 0x0483 or 1155)")
    parser.add_argument(
        "--pid", type=lambda x: int(x, 0),
        help="USB Product ID (hex or decimal, e.g., 0x3748 or 14152)")
    parser.add_argument(
        "--manufacturer", help="USB device manufacturer name")
    parser.add_argument(
        "--product", help="USB device product name")
    parser.add_argument(
        "--description", help="USB device description")
    parser.add_argument(
        "--serial", dest="serial_number",
        help="USB device serial number")
    parser.add_argument(
        "--fast", action="store_true",
        help="Disable simulated read delay in mock probe (instant reads)")
    parser.add_argument(
        "--mock-wave-freq", dest="wave_freq", type=float, default=5.0,
        help="Mock waveform base frequency in Hz (default: 5.0)")
    parser.add_argument(
        "--mock-wave-amp", dest="wave_amp", type=float, default=400,
        help="Mock waveform amplitude (default: 400)")

    args = parser.parse_args()
    
    # Handle quick profile (family names map to mock_type for DummyProbe)
    PROFILE_TO_MOCK_TYPE = {
        "cortex_m": "stm",   # Default Cortex-M mock uses ST-Link VID/PID
    }

    driver_type = args.driver_type
    mock_type = None

    if args.profile in PROFILE_TO_MOCK_TYPE:
        driver_type = "mock"
        mock_type = PROFILE_TO_MOCK_TYPE[args.profile]

    probe_class = DRIVER_MAP[driver_type]
    print(f"Starting Server using driver: {probe_class} ({driver_type})...")

    # Get the directory of this script to find server.py
    script_dir = os.path.dirname(os.path.abspath(__file__))
    server_path = os.path.join(script_dir, "server.py")
    
    # Resolve mock file (only relevant for DummyProbe)
    mock_file = args.mock_file

    cmd = [sys.executable, server_path, "--probe", probe_class]
    
    # Pass mock settings if using DummyProbe/mock driver
    if driver_type == "mock":
        print("Running in MOCK mode (simulation).")
        
        if mock_type:
            cmd.extend(["--mock-type", mock_type])
            
        if mock_file:
            if not os.path.exists(mock_file):
                print(f"Error: Mock file not found: {mock_file}")
                sys.exit(1)
            cmd.extend(["--mock-file", mock_file])
            print(f"Loading mock project data from {mock_file}")
        else:
            print("Warning: No project data specified. Memory will be empty.")
        
        # Pass USB device parameters for realistic device mocking
        if args.device_path:
            cmd.extend(["--device-path", args.device_path])
        if args.vid is not None:
            cmd.extend(["--vid", hex(args.vid)])
        if args.pid is not None:
            cmd.extend(["--pid", hex(args.pid)])
        if args.manufacturer:
            cmd.extend(["--manufacturer", args.manufacturer])
        if args.product:
            cmd.extend(["--product", args.product])
        if args.description:
            cmd.extend(["--description", args.description])
        if args.serial_number:
            cmd.extend(["--serial", args.serial_number])
    elif mock_file:
        print("Warning: Mock file ignored because "
              "real hardware driver selected.")

    if args.scenario and driver_type != "mock":
        print("Warning: --scenario ignored because "
              "real hardware driver selected.")

    if args.scenario and driver_type == "mock":
        cmd.extend(["--scenario", args.scenario])
        print(f"Scenario: {args.scenario}")

    if driver_type == "mock":
        if args.fast:
            cmd.append("--fast")
            print("Fast mode: simulated read delay disabled")
        cmd.extend(["--mock-wave-freq", str(args.wave_freq)])
        cmd.extend(["--mock-wave-amp", str(args.wave_amp)])

    if args.debug:
        cmd.append("--debug")
        
    if args.port:
        cmd.extend(["--port", str(args.port)])
    
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\nServer stopped.")
    except Exception as e:
        print(f"Error running server: {e}")


if __name__ == "__main__":
    main()
