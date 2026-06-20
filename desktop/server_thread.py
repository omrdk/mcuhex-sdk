"""Background thread that runs the WebSocketServer in its own asyncio event loop.

The pystray tray icon blocks the main thread (required by macOS AppKit),
so the WebSocket server runs on a separate daemon thread.
"""

import asyncio
import logging
import threading
import sys
import os

# In frozen builds, PyInstaller bundles all modules flat — no path fixup needed.
# In dev mode, add the project root so `import server` / `import probe` resolve.
if not getattr(sys, 'frozen', False):
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

from server import WebSocketServer
from probe.pyocd_probe import PyOCDProbe
from probe.dummyprobe import DummyProbe
from desktop.config import DEFAULT_HOST, DEFAULT_PORT

# Ensure probe drivers are initialized (probe.__init__ may be empty in frozen builds)
import probe
if hasattr(probe, 'init_probes'):
    probe.init_probes()

LOG = logging.getLogger(__name__)

PROBE_MAP = {
    "PyOCDProbe": PyOCDProbe,
    "DummyProbe": DummyProbe,
}

DEMO_KWARGS = {"mock_type": "stm", "demo": True, "read_delay_ms": 0, "wave_freq": 1.0}


class ServerThread:
    """Manages the WebSocket server lifecycle on a background thread."""

    def __init__(self, probe_name="PyOCDProbe", host=DEFAULT_HOST, port=DEFAULT_PORT,
                 probe_kwargs=None, on_state_change=None):
        self._host = host
        self._port = port
        self._probe_name = probe_name
        self._probe_kwargs = probe_kwargs or {}
        # Forwarded to the server so probe changes (incl. WebSocket enter_demo)
        # can notify the tray to re-render its demo state.
        self._on_state_change = on_state_change
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: WebSocketServer | None = None
        self._running = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    @property
    def probe_name(self) -> str:
        return self._probe_name

    @property
    def is_demo(self) -> bool:
        """True when the *live* probe is in demo mode.

        Reads the running server's actual probe instead of a cached flag, so it
        reflects demo entered either via the tray menu or the WebSocket
        `enter_demo` command (e.g. from the VS Code extension)."""
        if not self.is_running or self._server is None:
            return False
        probe = getattr(getattr(self._server, "handler", None), "probe", None)
        return bool(getattr(probe, "demo", False))

    @property
    def port(self) -> int:
        return self._port

    def start(self):
        """Start the server in a background thread."""
        if self.is_running:
            LOG.warning("Server already running")
            return

        self._thread = threading.Thread(target=self._run, daemon=True, name="ws-server")
        self._thread.start()

    def _run(self):
        """Thread target: create event loop, start server, run until stopped."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        probe_cls = PROBE_MAP.get(self._probe_name, PyOCDProbe)
        self._server = WebSocketServer(
            self._host, self._port, probe_cls, self._probe_kwargs,
            on_state_change=self._on_state_change
        )
        self._running.set()
        LOG.info(f"Server thread started: {self._probe_name} on "
                 f"ws://{self._host}:{self._port}")

        try:
            self._loop.run_until_complete(self._server.start_server())
        except Exception as e:
            LOG.error(f"Server error: {e}")
        finally:
            self._running.clear()
            self._loop.close()
            LOG.info("Server thread exited")

    def stop(self):
        """Stop the server gracefully."""
        if not self.is_running or not self._loop or not self._server:
            return

        future = asyncio.run_coroutine_threadsafe(
            self._server.stop_server(), self._loop
        )
        try:
            future.result(timeout=5)
        except Exception as e:
            LOG.error(f"Error stopping server: {e}")

        self._running.clear()
        LOG.info("Server stopped")

    def restart(self, probe_name=None, probe_kwargs=None):
        """Stop and restart with optionally different probe settings."""
        self.stop()
        if probe_name:
            self._probe_name = probe_name
        if probe_kwargs is not None:
            self._probe_kwargs = probe_kwargs
        self.start()
