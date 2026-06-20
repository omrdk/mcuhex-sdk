#!/usr/bin/env python3
"""MCUHex SDK Tray Application

System tray app that runs the WebSocket server for connecting
the MCUHex web app to ARM Cortex-M debug probes.
"""

import logging
import sys
import os
import threading
import webbrowser

# In dev mode, add project root so `import server` / `import probe` resolve.
# In frozen builds, PyInstaller handles module resolution.
if not getattr(sys, 'frozen', False):
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

from desktop.pyocd_bootstrap import bootstrap_pyocd
from desktop.server_thread import ServerThread, DEMO_KWARGS
from desktop.config import APP_NAME, VERSION, WEB_APP_URL, DEFAULT_PORT
from desktop.autostart import is_autostart_enabled, self_heal_autostart_path, set_autostart
from desktop.updater import auto_check_for_update, download_and_apply_update

import pystray
from PIL import Image

LOG = logging.getLogger(__name__)

# Resolve resource paths (works both dev and frozen)
if getattr(sys, 'frozen', False):
    RESOURCES_DIR = os.path.join(sys._MEIPASS, 'resources')
else:
    RESOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources')


def load_icon(name="icon.png"):
    path = os.path.join(RESOURCES_DIR, name)
    if os.path.exists(path):
        return Image.open(path)
    return Image.new('RGBA', (64, 64), color=(0, 0, 0, 0))


class MCUHexTray:

    def __init__(self):
        self._server = ServerThread(
            probe_name="PyOCDProbe", port=DEFAULT_PORT,
            on_state_change=self._on_server_state_change
        )
        self._icon: pystray.Icon | None = None
        self._default_icon = load_icon()
        self._update_info = None  # populated by background check

    def _update_icon(self):
        # Single icon regardless of state; this just refreshes the menu labels
        # (e.g. "Server: Running"). Windows needs the explicit refresh.
        if self._icon:
            self._icon.update_menu()

    def _on_server_state_change(self):
        # Fired from the server thread when the active probe changes (including a
        # remote WebSocket enter_demo from the VS Code extension). Re-render so the
        # Demo Mode checkmark / "(Demo)" label always mirror the live probe state.
        # Calling update_menu() off-thread is the same pattern used by the
        # background update check.
        self._update_icon()

    def _server_status_text(self, _=None):
        if self._server.is_running:
            mode = " (Demo)" if self._server.is_demo else ""
            return f"Server: Running{mode}"
        return "Server: Stopped"

    def _on_open_web(self, icon, item):
        webbrowser.open(WEB_APP_URL)

    def _on_toggle_server(self, icon, item):
        if self._server.is_running:
            self._server.stop()
        else:
            self._server.restart(probe_name="PyOCDProbe", probe_kwargs={})
        self._update_icon()

    def _server_toggle_text(self, _=None):
        return "Stop Server" if self._server.is_running else "Start Server"

    def _on_toggle_demo(self, icon, item):
        if self._server.is_demo:
            # Exit demo → switch back to real probe
            self._server.restart(probe_name="PyOCDProbe", probe_kwargs={})
        else:
            self._server.restart(probe_name="DummyProbe", probe_kwargs=dict(DEMO_KWARGS))
        self._update_icon()

    def _demo_checked(self, _=None):
        return self._server.is_demo

    def _on_toggle_autostart(self, icon, item):
        set_autostart(not is_autostart_enabled())

    def _autostart_checked(self, _=None):
        return is_autostart_enabled()

    def _update_text(self, _=None):
        if self._update_info:
            return f"Update to v{self._update_info.version}"
        return "Up to Date"

    def _update_enabled(self, _=None):
        return self._update_info is not None

    def _on_update(self, icon, item):
        if not self._update_info:
            return
        if self._update_info.asset_url and getattr(sys, "frozen", False):
            download_and_apply_update(
                self._update_info.asset_url,
                notify_cb=lambda msg, title=APP_NAME: icon.notify(msg, title),
            )
        else:
            webbrowser.open(self._update_info.html_url)

    def _background_update_check(self):
        """Check for updates on launch (respects 24h cooldown)."""
        self._update_info = auto_check_for_update()
        if self._update_info:
            LOG.info("Update available: v%s", self._update_info.version)
            if self._icon:
                self._icon.update_menu()

    def _on_quit(self, icon, item):
        self._server.stop()
        icon.stop()

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem(f"{APP_NAME} v{VERSION}", None, enabled=False),
            pystray.MenuItem(self._server_status_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open MCUHex", self._on_open_web, default=True),
            pystray.MenuItem(self._server_toggle_text, self._on_toggle_server),
            pystray.MenuItem("Demo Mode", self._on_toggle_demo, checked=self._demo_checked),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Port: {DEFAULT_PORT}", None, enabled=False),
            pystray.MenuItem("Start at Login", self._on_toggle_autostart, checked=self._autostart_checked),
            pystray.MenuItem(self._update_text, self._on_update, enabled=self._update_enabled),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        )

    def run(self):
        bootstrap_pyocd()
        self_heal_autostart_path()
        self._server.start()

        # Background update check (non-blocking, respects 24h cooldown)
        threading.Thread(target=self._background_update_check, daemon=True).start()

        self._icon = pystray.Icon(
            APP_NAME,
            icon=self._default_icon,
            title=f"{APP_NAME} — ws://127.0.0.1:{DEFAULT_PORT}",
            menu=self._build_menu(),
        )

        LOG.info(f"{APP_NAME} v{VERSION} tray app started")
        self._icon.run()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    MCUHexTray().run()


if __name__ == "__main__":
    main()
