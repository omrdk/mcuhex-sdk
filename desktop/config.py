"""MCUHex Desktop App Configuration"""

import os
import sys

APP_NAME = "MCUHex"
VERSION = "0.1.0"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

WEB_APP_URL = "https://mcuhex.vercel.app"
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
