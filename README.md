# MCUHex SDK

MCUHex SDK is the local backend that connects the [MCUHex](https://mcuhex.vercel.app/) web app to physical debug probes. It runs on the developer's machine as a lightweight **WebSocket server** (default `ws://127.0.0.1:8765`) and exposes a single JSON protocol the web app drives to talk to the target MCU.

Through that protocol the SDK provides:

- **Memory access** — read and write arbitrary addresses on the target.
- **Live signal capture** — sample one or more variables/registers at a fixed rate and stream the buffered result back.
- **Firmware flashing** — program `.elf` / `.out` / images with live progress, with CMSIS-Pack discovery and install for target support.
- **Device & target management** — enumerate attached probes, auto-select the matching driver, and override the target chip.

It connects to **ARM Cortex-M** targets over **SWD** using [PyOCD](https://pyocd.io/). The SDK also ships as a small **tray application** for macOS and Windows that manages the server lifecycle and auto-updates.

## Connectivity Support

> [!NOTE]
> **Only the ARM Cortex-M (SWD) path is supported today.** Drivers for OCD (STM32G4) and serial transports are out-of-tree and planned for future releases.

| Transport | Targets | Driver | Status |
| :--- | :--- | :--- | :---: |
| **SWD** | ARM Cortex-M | PyOCD | ✅&nbsp; **Supported** |
| OCD | STM32 family | — | 🚧&nbsp; Planned |
| Serial / UART | TI C2000, ESP32 / Espressif | — | 🚧&nbsp; Planned |

<sub>✅ Available now &nbsp;·&nbsp; 🚧 On the roadmap — not yet wired in</sub>

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Requires Python 3.8+.

## Quick Start

```bash
# Mock an ARM Cortex-M device (no hardware needed)
python mock_device.py cortex_m

# Mock with no simulated read delay (instant reads)
python mock_device.py cortex_m --fast

# Real hardware via the PyOCD driver (ARM Cortex-M over SWD)
python mock_device.py -t pyocd

# Run the server directly with a specific probe class
python server.py --probe PyOCDProbe

# Interactive CLI client
python client.py
```

## `mock_device.py` Launcher

Despite its name, `mock_device.py` is a universal launcher that starts the server in either **Mock** mode or **Real Hardware** mode.

```text
usage: mock_device.py [-h] [-f MOCK_FILE] [-t {mock,pyocd}] [--debug] [-P PORT]
                      [-s SCENARIO] [--fast] ...
                      [{cortex_m}]
```

| Argument | Description |
| :--- | :--- |
| `{cortex_m}` | **Device family profile** (mock mode): `cortex_m` (ARM SWD). Sets the simulated device identity. |
| `-t`, `--type` | **Driver type**: `mock` (default, simulation) or `pyocd` (ARM Cortex-M over SWD). |
| `-f`, `--file` | **Custom file** (mock only): path to a JSON symbol/memory file. |
| `-P`, `--port` | WebSocket server port (default: `8765`). |
| `--fast` | Disable the simulated read delay in mock mode (instant reads). |
| `--debug` | Enable verbose logging. |
| `--mock-wave-freq` / `--mock-wave-amp` | Tune the mock waveform generator (Hz / amplitude). |
| `--vid` / `--pid` / `--manufacturer` / `--product` / `--serial` / `--device-path` | Override the simulated USB device descriptors. |

## `server.py` (Direct)

Run the server directly and choose the initial probe class:

```bash
python server.py --probe {PyOCDProbe,DummyProbe} [--port 8765] [--debug]
```

## Architecture

```
server.py               WebSocket server, CommandHandler, ErrorCode, PROBE_MAP
mock_device.py          Universal launcher (mock + real hardware)
client.py               Interactive CLI client

probe/
  debugprobe.py         DebugProbe abstract base
  dummyprobe.py         Mock/simulator + waveform generation + scenario injection
  pyocd_probe.py        PyOCD driver (ARM Cortex-M over SWD)
  remoteprobe.py        Remote proxy
  errors.py             ProbeError

desktop/                pystray tray app, auto-update, build scripts (macOS + Windows)
firmware/               Demo firmware used by the mock profile data
```

Drivers are loaded individually in `probe/__init__.py`; a missing native dependency (e.g. `serial`, `pyocd`) logs a warning and skips that driver rather than failing the whole import.

## WebSocket Protocol

All communication is JSON over a single WebSocket connection. Every request carries a `cmd` and an optional client-chosen `id`; the server echoes that `id` back so the client can match responses to requests.

### Message envelopes

```jsonc
// Request  (client → server)
{ "cmd": "<command>", "id": <number>, /* ...command-specific args */ }

// Success response  (server → client)
{ "version": 1, "sdk_version": "x.y.z", "status": 0, "id": <number>, /* ...data */ }

// Error response  (server → client)
{ "version": 1, "sdk_version": "x.y.z", "status": 1, "error_code": "<CODE>", "msg": "<human readable>" }
```

- `status` is `0` on success and `1` on error.
- On error, `error_code` is a stable machine-readable string (see [Error codes](#error-codes)) and `msg` is a human-readable explanation.
- Long-running commands (`capture`, `flash`, `install_pack`) return immediately with an acknowledgement, then **push** progress/completion messages identified by a `type` field (see [Async push messages](#async-push-messages)).

### Command reference

| Command | Required args | Optional args | Success payload |
| :--- | :--- | :--- | :--- |
| `list_devices` | — | — | `{ devices: [...] }` (+ `demo`, `demo_registers` in demo mode) |
| `list_probes` | — | — | `{ probes: [...], active_probe }` |
| `set_probe` | `probe_name` | — | `{ msg }` |
| `enter_demo` | — | — | `{ demo: true, ... }` |
| `get_driver_list` | — | — | `{ drivers: [...] }` |
| `connect` | `uri` | `target` | `{ is_open, target? }` |
| `disconnect` | — | — | `{ is_open }` |
| `read` | `addr`, `nb` | — | `{ data: "<hex>" }` |
| `write` | `addr`, `data` (hex) | — | `{}` (status only) |
| `calibrate` | — | — | `{ per_read_ms }` |
| `capture` | `channels`, `rate_hz`, `duration_s` | — | `{ msg: "capture_started" }` → async `capture_complete` |
| `stop_capture` | — | — | `{ msg }` |
| `browse_files` | — | `directory`, `extensions` | `{ directory, parent, entries }` (restricted to `$HOME`) |
| `flash` | `file_path` | `chip_erase`, `verify`, `no_reset` | `{ msg: "flash_started" }` → async `flash_progress` / `flash_complete` |
| `cancel_flash` | — | — | `{ msg }` |
| `search_targets` | — | `query`, `limit` | `{ results: [...], total }` |
| `install_pack` | `target` | — | `{ msg }` → async progress |
| `set_target` | `uri` | `target` | `{ msg, uri, target? }` |
| `get_target_info_ext` | — | — | `{ target_override, overrides, detected?, memory_map }` |

> `read` returns memory as a hex string; `write` takes `data` as a hex string. `channels` is a list of `{ addr, nb, type }` objects (see [Data encoding](#data-encoding) for `type`).

### Examples

```jsonc
// read 4 bytes at 0x20000000
→ { "cmd": "read", "id": 7, "addr": 536870912, "nb": 4 }
← { "version": 1, "sdk_version": "1.2.3", "status": 0, "id": 7, "data": "39300000" }

// connect to a probe that isn't there
→ { "cmd": "connect", "id": 8, "uri": "pyocd:0x0483" }
← { "version": 1, "sdk_version": "1.2.3", "status": 1,
    "error_code": "NO_DEVICES_FOUND", "msg": "No debug probes found" }
```

### Async push messages

These are emitted by the server without a matching request `id`; clients dispatch on `type`.

```jsonc
// capture finished — buffered samples streamed back at once
{ "type": "capture_complete", "capture_id": <id>,
  "samples": [[t, v1, v2, ...], ...], "actual_hz": <float>, "total_samples": <int> }

// flash progress (repeated; phase ∈ "erasing" | "programming" | "verifying")
{ "type": "flash_progress", "flash_id": <id>, "phase": "programming", "progress": 0.62 }

// flash finished — success
{ "type": "flash_complete", "flash_id": <id>, "success": true,
  "duration_ms": <int>, "bytes_programmed": <int> }

// flash finished — failure
{ "type": "flash_complete", "flash_id": <id>, "success": false,
  "error_code": "<CODE>", "msg": "<...>" }
```

### Error codes

`error_code` values are grouped by category — generic connection (`NO_DEVICES_FOUND`, `DEVICE_BUSY`, `PERMISSION_DENIED`, `CONNECT_TIMEOUT`, `READ_WRITE_FAILED`, …), Cortex-M specific (`CORTEX_M_DEBUG_PORT_LOCKED`, `CORTEX_M_SWD_PROTOCOL_ERROR`, …), flash operations (`FLASH_FILE_NOT_FOUND`, `FLASH_UNSUPPORTED_FORMAT`, `FLASH_VERIFICATION_FAILED`, `CORTEX_M_FLASH_WRITE_PROTECTED`, …), and file browse (`BROWSE_PERMISSION_DENIED`, `BROWSE_INVALID_PATH`). The authoritative list is `ErrorCode` in `server.py`, kept in sync with `ConnectionErrorCode` on the web-app side.

### Data encoding

Values are exchanged as little-endian hex strings. ELF-parser type tags: `U08`/`U16`/`U32` (unsigned), `I08`/`I16`/`I32` (signed), `F32`/`F64`.

## Mock Data Format

When using `-f` in mock mode, the JSON file defines symbols and initial values:

```json
{
  "lst": [
    { "nam": "myVariable", "adr": "0x20000000", "sze": 4, "val": 12345 }
  ]
}
```

- `adr`: hex string address.
- `val`: (optional) initial integer value loaded into simulated memory.

## License

All rights reserved. This source is published for reference; no license to use, copy, modify, or distribute is granted.
