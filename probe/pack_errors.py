"""Turn a CMSIS pack failure into the error code the user sees.

Pack downloads run inside cmsis_pack_manager, whose work happens in Rust and
comes back as a bare ``Exception`` carrying a Rust-written sentence
(``cmsis_pack_manager/__init__.py``, ``_RaiseRust``). There is no type to read
and no status number to look up, so nothing here searches that sentence for a
word: the two things that actually fail a download -- the disk we write to and
the wire we fetch over -- can be asked directly, after the fact.

The order is the usual one, certainty first:

  1. Our own ProbeError already carries a code.
  2. cmsis_pack_manager is missing entirely, which is a packaging fault rather
     than anything the user did.
  3. Python raised an OSError with an errno, which says exactly what went wrong.
  4. The cache directory is out of space or refuses writes -- observed, not
     inferred.
  5. The pack server does not answer a connection.

Steps 4 and 5 only run once something has already failed, so a working install
never pays for them. When none of them is certain the code stays generic.

The strings here are sent on the WebSocket as-is and must match ErrorCode in
server.py and ConnectionErrorCode in the web client.
"""
import errno as errno_module
import logging
import os
import shutil
import socket
from typing import Optional

from .errors import ProbeError

LOG = logging.getLogger("pack-errors")

UNKNOWN = "UNKNOWN_CONNECTION_ERROR"

# The vendor index every pack lookup starts from. Reaching it is not proof that
# a given vendor's pack server answers, which is why the code it produces says
# the pack server was unreachable rather than that the internet is down.
PACK_HOST = ("www.keil.com", 443)
REACHABILITY_TIMEOUT_S = 2.0

# Not "running low" -- gone. An index is ~14 MB and a pack can be far larger, so
# a download can still fail for space above this line; that case stays UNKNOWN
# rather than being blamed on the disk without evidence.
DISK_FLOOR_BYTES = 50 * 1024 * 1024

# Writing is refused for more reasons than one, and all of them mean the cache
# directory cannot be filled.
_WRITE_REFUSED_ERRNOS = frozenset({
    errno_module.EACCES,
    errno_module.EPERM,
    errno_module.EROFS,
})


def classify_pack_failure(exc: BaseException, data_path: Optional[str] = None) -> str:
    """The error code for `exc`, raised while downloading or installing a pack.

    `data_path` is the pack cache directory, when the caller knows it. Without
    it the disk observations are skipped -- an unknown directory cannot be
    measured, and guessing which one it was would be worse than not answering.
    """
    if isinstance(exc, ProbeError):
        return exc.error_code

    if isinstance(exc, ImportError):
        return "PACK_MANAGER_UNAVAILABLE"

    code = _from_errno(exc)
    if code:
        return code

    if data_path:
        code = _from_cache_directory(data_path)
        if code:
            return code

    if not pack_host_reachable():
        return "PACK_NETWORK_UNREACHABLE"

    return UNKNOWN


def _from_errno(exc: BaseException) -> Optional[str]:
    if not isinstance(exc, OSError):
        return None
    if exc.errno == errno_module.ENOSPC:
        return "PACK_DISK_FULL"
    if exc.errno in _WRITE_REFUSED_ERRNOS:
        return "PACK_CACHE_UNWRITABLE"
    return None


def _from_cache_directory(data_path: str) -> Optional[str]:
    free = _free_bytes(data_path)
    if free is not None and free < DISK_FLOOR_BYTES:
        return "PACK_DISK_FULL"
    if _writable(data_path) is False:
        return "PACK_CACHE_UNWRITABLE"
    return None


def _free_bytes(data_path: str) -> Optional[int]:
    """Free space where the cache lives, or None when it cannot be measured."""
    try:
        return shutil.disk_usage(_nearest_existing(data_path)).free
    except OSError as e:
        LOG.debug(f"Could not measure free space at {data_path}: {e}")
        return None


def _writable(data_path: str) -> Optional[bool]:
    """Whether the cache directory takes writes. None when it cannot be told."""
    try:
        return os.access(_nearest_existing(data_path), os.W_OK)
    except OSError as e:
        LOG.debug(f"Could not test writability of {data_path}: {e}")
        return None


def _nearest_existing(path: str) -> str:
    """The closest ancestor that exists.

    A first install has no cache directory yet, and the question is really
    whether the place it would be created can hold it.
    """
    current = os.path.abspath(path)
    while not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def pack_host_reachable() -> bool:
    """Whether the pack index host answers a connection right now.

    A proxy the download honours and this check does not would answer False for
    a link that works, so a True result is the useful one: it stops us blaming
    the network for a failure that happened elsewhere.
    """
    try:
        with socket.create_connection(PACK_HOST, REACHABILITY_TIMEOUT_S):
            return True
    except OSError as e:
        LOG.debug(f"Pack host {PACK_HOST[0]} unreachable: {e}")
        return False
