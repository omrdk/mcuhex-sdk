"""Behavioral tests for reading the error code out of a pack failure."""
import errno
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from probe import pack_errors
from probe.errors import ProbeError
from probe.pack_errors import DISK_FLOOR_BYTES, UNKNOWN, classify_pack_failure


def rust_failure(message="Could not download pack"):
    """What cmsis_pack_manager raises: a bare Exception with a Rust sentence."""
    return Exception(message)


@pytest.fixture
def online(monkeypatch):
    """A reachable pack host, so the network is never the answer by default."""
    monkeypatch.setattr(pack_errors, "pack_host_reachable", lambda: True)


@pytest.fixture
def roomy(monkeypatch):
    monkeypatch.setattr(pack_errors, "_free_bytes", lambda path: DISK_FLOOR_BYTES * 10)
    monkeypatch.setattr(pack_errors, "_writable", lambda path: True)


CACHE = "/home/user/.cache/cmsis-pack-manager"


def test_our_own_code_is_never_second_guessed(online, roomy):
    exc = ProbeError("Target 'stm32f103cb' is not in the index", "CORTEX_M_UNSUPPORTED_TARGET")

    assert classify_pack_failure(exc, CACHE) == "CORTEX_M_UNSUPPORTED_TARGET"


def test_a_missing_pack_manager_is_named_as_such(online, roomy):
    assert (
        classify_pack_failure(ImportError("No module named 'cmsis_pack_manager'"), CACHE)
        == "PACK_MANAGER_UNAVAILABLE"
    )
    assert (
        classify_pack_failure(ModuleNotFoundError("No module named 'cmsis_pack_manager'"))
        == "PACK_MANAGER_UNAVAILABLE"
    )


class TestErrno:
    def test_no_space_left_is_a_full_disk(self, online, roomy):
        exc = OSError(errno.ENOSPC, "No space left on device")

        assert classify_pack_failure(exc, CACHE) == "PACK_DISK_FULL"

    @pytest.mark.parametrize("code", [errno.EACCES, errno.EPERM, errno.EROFS])
    def test_a_refused_write_names_the_cache(self, code, online, roomy):
        exc = OSError(code, os.strerror(code))

        assert classify_pack_failure(exc, CACHE) == "PACK_CACHE_UNWRITABLE"

    def test_an_errno_we_do_not_recognise_moves_on(self, online, roomy):
        exc = OSError(errno.EPIPE, "Broken pipe")

        assert classify_pack_failure(exc, CACHE) == UNKNOWN


class TestObservations:
    def test_a_disk_with_nothing_left_explains_the_failure(self, monkeypatch, online):
        monkeypatch.setattr(pack_errors, "_free_bytes", lambda path: 1024)

        assert classify_pack_failure(rust_failure(), CACHE) == "PACK_DISK_FULL"

    def test_a_cache_that_refuses_writes_explains_the_failure(self, monkeypatch, online):
        monkeypatch.setattr(pack_errors, "_free_bytes", lambda path: DISK_FLOOR_BYTES * 10)
        monkeypatch.setattr(pack_errors, "_writable", lambda path: False)

        assert classify_pack_failure(rust_failure(), CACHE) == "PACK_CACHE_UNWRITABLE"

    def test_a_silent_pack_host_explains_the_failure(self, monkeypatch, roomy):
        monkeypatch.setattr(pack_errors, "pack_host_reachable", lambda: False)

        assert classify_pack_failure(rust_failure(), CACHE) == "PACK_NETWORK_UNREACHABLE"

    def test_a_reachable_host_and_a_healthy_disk_leave_us_without_an_answer(
        self, online, roomy
    ):
        assert classify_pack_failure(rust_failure(), CACHE) == UNKNOWN

    def test_the_disk_is_not_measured_when_the_caller_cannot_say_where(
        self, monkeypatch, online
    ):
        monkeypatch.setattr(
            pack_errors,
            "_free_bytes",
            lambda path: pytest.fail("measured a directory nobody named"),
        )

        assert classify_pack_failure(rust_failure()) == UNKNOWN

    def test_an_observation_that_cannot_be_made_is_not_an_answer(self, monkeypatch, online):
        monkeypatch.setattr(pack_errors, "_free_bytes", lambda path: None)
        monkeypatch.setattr(pack_errors, "_writable", lambda path: None)

        assert classify_pack_failure(rust_failure(), CACHE) == UNKNOWN


class TestObservationsSurviveTheirOwnFailure:
    def test_an_unmeasurable_path_answers_nothing_instead_of_raising(self, monkeypatch):
        monkeypatch.setattr(
            pack_errors.shutil,
            "disk_usage",
            lambda path: (_ for _ in ()).throw(OSError(errno.EIO, "I/O error")),
        )

        assert pack_errors._free_bytes(CACHE) is None

    def test_an_untestable_path_answers_nothing_instead_of_raising(self, monkeypatch):
        monkeypatch.setattr(
            pack_errors.os,
            "access",
            lambda path, mode: (_ for _ in ()).throw(OSError(errno.EIO, "I/O error")),
        )

        assert pack_errors._writable(CACHE) is None

    def test_an_unresolvable_host_is_simply_unreachable(self, monkeypatch):
        monkeypatch.setattr(
            pack_errors.socket,
            "create_connection",
            lambda addr, timeout: (_ for _ in ()).throw(OSError("Name or service not known")),
        )

        assert pack_errors.pack_host_reachable() is False


class TestNearestExisting:
    def test_a_cache_that_does_not_exist_yet_is_measured_where_it_would_go(self, tmp_path):
        missing = tmp_path / "cmsis" / "packs" / "not-created-yet"

        assert pack_errors._nearest_existing(str(missing)) == str(tmp_path)

    def test_an_existing_cache_is_measured_where_it_is(self, tmp_path):
        assert pack_errors._nearest_existing(str(tmp_path)) == str(tmp_path)
