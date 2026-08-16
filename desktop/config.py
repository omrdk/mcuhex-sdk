"""MCUHex Desktop App Configuration"""

import os
import sys

APP_NAME = "MCUHex"
# The one place the version is written. The release script bumps it, CI checks
# the tag against it, and pyproject.toml reads it from here — a second copy
# anywhere would be the one that goes stale.
VERSION = "0.2.2"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

WEB_APP_URL = "https://mcuhex.vercel.app"

# Browsers put the page's origin on the WebSocket handshake, so this list is what
# keeps an arbitrary site the user happens to visit from driving their debug probe.
# None admits clients that send no Origin at all — the CLI and the VS Code
# extension — which no web page can impersonate.
ALLOWED_ORIGINS = [
    None,
    "https://mcuhex.com",
    "https://www.mcuhex.com",
    "https://mcuhex.vercel.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
]

# Escape hatch for preview deployments and one-off debugging, comma separated.
_extra_origins = os.environ.get("MCUHEX_ALLOWED_ORIGINS", "")
if _extra_origins:
    ALLOWED_ORIGINS += [o.strip() for o in _extra_origins.split(",") if o.strip()]

GITHUB_REPO = "omrdk/mcuhex-sdk"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
REPO_URL = f"https://github.com/{GITHUB_REPO}"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

BUNDLE_ID = "com.mcuhex.sdk"

UPDATE_CHECK_INTERVAL = 86400  # 24 hours in seconds

if sys.platform == "win32":
    APP_SUPPORT_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "MCUHex"
    )
    ASSET_SUFFIX = "-windows-amd64.zip"
else:
    APP_SUPPORT_DIR = os.path.join(
        os.path.expanduser("~"), "Library", "Application Support", "MCUHex"
    )
    ASSET_SUFFIX = "-macos-arm64.zip"
