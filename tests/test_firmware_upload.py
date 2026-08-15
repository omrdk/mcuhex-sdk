"""Behavioral tests for taking a firmware image over the wire."""
import asyncio
import base64
import hashlib
import io
import os
import struct
import sys
from pathlib import Path

import pytest
from intelhex import IntelHex

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


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


def run_flash(handler, cmd, run_flash_impl):
    """Drive _handle_flash the way a client does, to completion.

    The staged image is dropped by a done-callback _handle_flash attaches, so
    the cleanup only shows up if the real command path runs and its task is
    allowed to finish.
    """
    async def main():
        handler._websocket = FakeSocket()
        handler._run_flash = run_flash_impl
        await handler._handle_flash(cmd)
        task = handler._flash_task
        try:
            await task
        except (Exception, asyncio.CancelledError):
            pass
        # Done-callbacks run on the next loop iteration.
        await asyncio.sleep(0)
        return task

    return asyncio.run(main())


def flash_cmd():
    return {'cmd': 'flash', 'file_name': 'firmware.bin', 'data': b64(b'\x00' * 32)}


def test_a_finished_flash_drops_the_image_it_programmed(connected):
    async def succeeds(*args, **kwargs):
        return None

    run_flash(connected, flash_cmd(), succeeds)

    assert connected._staged_dir is None


def test_a_failed_flash_drops_the_image_too(connected):
    async def fails(*args, **kwargs):
        raise RuntimeError('programming rejected')

    run_flash(connected, flash_cmd(), fails)

    assert connected._staged_dir is None


def test_a_cancelled_flash_drops_the_image_too(connected):
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    run_flash(connected, flash_cmd(), cancelled)

    assert connected._staged_dir is None


def test_the_flash_reads_the_staged_copy(connected):
    seen = {}

    async def record(websocket, file_path, *args, **kwargs):
        seen['path'] = file_path
        seen['contents'] = Path(file_path).read_bytes()

    run_flash(connected, flash_cmd(), record)

    assert seen['contents'] == b'\x00' * 32
    assert os.path.basename(seen['path']) == 'firmware.bin'


def test_a_bin_image_is_measured_from_the_boot_address(handler):
    # _image_ranges is what the flash uses to decide what will be written, and
    # it picks its parser from the staged file's extension.
    path = handler._stage_upload('firmware.bin', b64(b'\xaa' * 256))

    assert CommandHandler._image_ranges(path, 0x08000000) == [(0x08000000, 256)]


def build_elf(load_address, load_size, trailing_bytes):
    """A minimal ARM ELF32 with one PT_LOAD segment and dead weight after it.

    The trailing bytes stand in for the symbol and debug sections a real build
    carries: they make the file far larger than what gets programmed.
    """
    header_size, phentsize = 52, 32
    offset = header_size + phentsize
    elf_header = struct.pack(
        '<4sBBBBB7xHHIIIIIHHHHHH',
        b'\x7fELF', 1, 1, 1, 0, 0,     # ident: 32-bit, little-endian, SysV
        2, 40, 1,                       # ET_EXEC, EM_ARM, version
        load_address, header_size, 0, 0,
        header_size, phentsize, 1,      # one program header
        0, 0, 0,                        # no sections
    )
    program_header = struct.pack(
        '<IIIIIIII',
        1, offset, load_address, load_address,  # PT_LOAD
        load_size, load_size, 5, 4,             # filesz, memsz, RX, align
    )
    return elf_header + program_header + b'\xaa' * load_size + b'\x00' * trailing_bytes


def test_an_elf_is_measured_by_its_loadable_segments_not_its_size(handler):
    # What makes the extension worth preserving: read as raw binary this file
    # would be programmed whole, debug weight and all, at the wrong offset.
    payload = build_elf(0x08000000, load_size=512, trailing_bytes=64 * 1024)

    path = handler._stage_upload('firmware.elf', b64(payload))
    ranges = CommandHandler._image_ranges(path, 0x08000000)

    assert ranges == [(0x08000000, 512)]
    assert sum(length for _, length in ranges) < len(payload) / 100


def test_a_hex_image_is_measured_by_the_addresses_it_covers(handler):
    # A hex file is sparse: two 16-byte chunks far apart are 32 bytes to write,
    # not the span between them and not the file's size on disk.
    hexfile = IntelHex()
    for address in (0x08000000, 0x08008000):
        for offset in range(16):
            hexfile[address + offset] = 0xAA
    text = io.StringIO()
    hexfile.write_hex_file(text)

    path = handler._stage_upload('firmware.hex', b64(text.getvalue().encode()))
    ranges = CommandHandler._image_ranges(path, 0x08000000)

    assert ranges == [(0x08000000, 16), (0x08008000, 16)]


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
