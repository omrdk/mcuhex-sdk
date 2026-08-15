"""Behavioral tests for taking a firmware image over the wire."""
import asyncio
import base64
import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server
from server import CommandHandler, ErrorCode, ProbeError, loggable


class StubProbe:
    def __init__(self, open_=False):
        self._open = open_

    def is_open(self):
        return self._open


@pytest.fixture
def handler():
    h = CommandHandler(StubProbe())
    yield h
    h._discard_staged()


@pytest.fixture
def connected(handler):
    handler.probe = StubProbe(open_=True)
    return handler


def b64(payload):
    return base64.b64encode(payload).decode()


def code_of(excinfo):
    return excinfo.value.error_code


def test_staged_image_is_byte_for_byte_what_was_sent(handler):
    payload = os.urandom(64 * 1024)

    path = handler._stage_upload('firmware.bin', b64(payload))

    staged = Path(path).read_bytes()
    assert hashlib.md5(staged).digest() == hashlib.md5(payload).digest()


def test_staged_file_keeps_its_extension(handler):
    # _image_ranges picks its parser from the extension: an ELF that lost it
    # would be read as raw binary and programmed at the wrong addresses.
    path = handler._stage_upload('firmware.elf', b64(b'\x7fELF'))

    assert os.path.basename(path) == 'firmware.elf'


@pytest.mark.parametrize('name', ['firmware.HEX', 'firmware.Bin', 'firmware.ELF'])
def test_extension_check_ignores_case(handler, name):
    path = handler._stage_upload(name, b64(b'\x00\x01'))

    assert os.path.isfile(path)


def test_refuses_a_file_that_is_not_firmware(handler):
    with pytest.raises(ProbeError) as excinfo:
        handler._stage_upload('notes.txt', b64(b'hello'))

    assert code_of(excinfo) == ErrorCode.FLASH_UNSUPPORTED_FORMAT


def test_refuses_a_name_with_no_extension(handler):
    with pytest.raises(ProbeError) as excinfo:
        handler._stage_upload('firmware', b64(b'\x00'))

    assert code_of(excinfo) == ErrorCode.FLASH_UNSUPPORTED_FORMAT


@pytest.mark.parametrize('name', ['../evil.bin', '../../../../etc/passwd.bin', '/tmp/absolute.bin'])
def test_a_traversing_name_still_lands_in_the_staging_directory(handler, name):
    path = handler._stage_upload(name, b64(b'\x00'))

    assert os.path.dirname(path) == handler._staged_dir


def test_accepts_an_image_exactly_at_the_limit(handler, monkeypatch):
    monkeypatch.setattr(server, 'MAX_UPLOAD_BYTES', 1024)

    path = handler._stage_upload('firmware.bin', b64(b'\xff' * 1024))

    assert os.path.getsize(path) == 1024


def test_refuses_an_image_one_byte_over_the_limit(handler, monkeypatch):
    monkeypatch.setattr(server, 'MAX_UPLOAD_BYTES', 1024)

    with pytest.raises(ProbeError) as excinfo:
        handler._stage_upload('firmware.bin', b64(b'\xff' * 1025))

    assert code_of(excinfo) == ErrorCode.FLASH_FILE_TOO_LARGE


def test_refuses_a_payload_that_is_not_base64(handler):
    with pytest.raises(ProbeError) as excinfo:
        handler._stage_upload('firmware.bin', 'not base64 at all!!')

    assert code_of(excinfo) == ErrorCode.FLASH_UPLOAD_FAILED


def test_an_empty_file_says_so_rather_than_blaming_the_transfer(handler):
    with pytest.raises(ProbeError) as excinfo:
        handler._stage_upload('firmware.bin', '')

    assert code_of(excinfo) == ErrorCode.FLASH_FILE_EMPTY
    assert 'firmware.bin' in str(excinfo.value)


def test_a_new_upload_replaces_the_one_before_it(handler):
    first = handler._stage_upload('first.bin', b64(b'\x01'))
    first_dir = os.path.dirname(first)

    handler._stage_upload('second.bin', b64(b'\x02'))

    assert not os.path.exists(first_dir)


def test_a_refused_upload_leaves_the_accepted_one_alone(handler):
    path = handler._stage_upload('firmware.bin', b64(b'\x01'))

    with pytest.raises(ProbeError):
        handler._stage_upload('notes.txt', b64(b'\x02'))

    # Validation runs before anything is staged, so a rejected file cannot cost
    # the user the image they already had ready to flash.
    assert Path(path).read_bytes() == b'\x01'


def test_discarding_is_safe_to_repeat(handler):
    handler._stage_upload('firmware.bin', b64(b'\x01'))

    handler._discard_staged()
    handler._discard_staged()

    assert handler._staged_dir is None


def test_flash_without_a_payload_is_rejected(connected):
    with pytest.raises(ValueError):
        asyncio.run(connected._handle_flash({'cmd': 'flash'}))


def test_logging_a_command_does_not_repeat_the_whole_image():
    # Writing a megabyte to a terminal blocks until it has rendered, and the
    # event loop stalls there long enough for a flash to look hung.
    payload = b64(os.urandom(512 * 1024))
    cmd = {'cmd': 'flash', 'file_name': 'firmware.bin', 'data': payload}

    logged = loggable(cmd)

    assert len(str(logged)) < 200
    assert logged['file_name'] == 'firmware.bin'


def test_logging_leaves_a_short_payload_readable():
    cmd = {'cmd': 'write', 'data': 'deadbeef'}

    assert loggable(cmd) == cmd


def test_logging_leaves_a_command_without_a_payload_alone():
    cmd = {'cmd': 'connect', 'uri': 'stlink://0'}

    assert loggable(cmd) == cmd
