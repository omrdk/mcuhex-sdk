"""Behavioral tests for a capture reaching the client while it is still running."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server as server_mod
from server import CommandHandler

CHANNELS = [{"addr": 0x20000000, "nb": 4, "type": "U32"}]


class CountingProbe:
    """Answers every read with a fresh value, so samples are distinguishable."""

    demo = False

    def __init__(self):
        self.value = 0

    def is_open(self):
        return True

    async def read(self, addr, nb):
        self.value += 1
        return self.value.to_bytes(4, "little")


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, raw):
        self.messages.append(json.loads(raw))


class DeadWebSocket(FakeWebSocket):
    """A socket that has gone away mid-capture."""

    async def send(self, raw):
        raise ConnectionResetError("socket closed")


def of_type(ws, kind):
    return [m for m in ws.messages if m.get("type") == kind]


@pytest.fixture
def handler():
    return CommandHandler(CountingProbe())


@pytest.fixture
def fast_progress(monkeypatch):
    monkeypatch.setattr(server_mod, "CAPTURE_PROGRESS_S", 0.02)


def run_capture(handler, ws, rate_hz, duration_s, capture_id):
    asyncio.run(handler._run_capture(ws, CHANNELS, rate_hz, duration_s, capture_id))


def test_samples_arrive_before_the_capture_ends(handler, fast_progress):
    ws = FakeWebSocket()

    run_capture(handler, ws, rate_hz=200, duration_s=0.5, capture_id=7)

    progress = of_type(ws, "capture_progress")
    assert progress, "a half-second capture said nothing until it was over"
    assert all(m["capture_id"] == 7 for m in progress)


def test_progress_carries_each_sample_once(handler, fast_progress):
    ws = FakeWebSocket()

    run_capture(handler, ws, rate_hz=200, duration_s=0.5, capture_id=1)

    progress = of_type(ws, "capture_progress")
    streamed = [row for m in progress for row in m["samples"]]
    complete = of_type(ws, "capture_complete")[0]

    # Offsets have to describe where each batch sits in the final run, or a
    # client cannot stitch them into one series.
    offsets = [m["offset"] for m in progress]
    assert offsets == sorted(offsets)
    assert offsets[0] == 0
    assert streamed == complete["samples"][:len(streamed)]


def test_final_message_still_carries_the_whole_run(handler, fast_progress):
    """A client that ignores progress messages must see no change at all."""
    ws = FakeWebSocket()

    run_capture(handler, ws, rate_hz=100, duration_s=0.3, capture_id=2)

    complete = of_type(ws, "capture_complete")
    assert len(complete) == 1
    assert complete[0]["total_samples"] == len(complete[0]["samples"])
    assert complete[0]["samples"], "the run ended with nothing in it"


def test_a_dead_socket_does_not_strand_the_capture(handler, fast_progress):
    """The task still has to unwind and release its own state."""
    ws = DeadWebSocket()

    async def flow():
        await asyncio.wait_for(
            handler._run_capture(ws, CHANNELS, 100, 0.3, 3), timeout=5)

    asyncio.run(flow())

    assert handler._capture_task is None
    assert handler._cancel_capture is False


def test_stopping_early_still_delivers_what_was_taken(handler, fast_progress):
    ws = FakeWebSocket()

    async def flow():
        task = asyncio.create_task(
            handler._run_capture(ws, CHANNELS, 100, 10, 4))
        await asyncio.sleep(0.15)
        handler._cancel_capture = True
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(flow())

    complete = of_type(ws, "capture_complete")[0]
    assert 0 < complete["total_samples"] < 1000


def run_unlimited(handler, ws, rate_hz, run_for, mid=None):
    """Start a capture with no duration, optionally act, then stop it."""
    async def flow():
        task = asyncio.create_task(
            handler._run_capture(ws, CHANNELS, rate_hz, 0, 9))
        await asyncio.sleep(run_for)
        if mid is not None:
            mid()
            await asyncio.sleep(run_for)
        handler._cancel_capture = True
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(flow())


def test_a_capture_with_no_duration_runs_until_stopped(handler, fast_progress):
    ws = FakeWebSocket()

    run_unlimited(handler, ws, rate_hz=200, run_for=0.25)

    streamed = sum(len(m["samples"]) for m in of_type(ws, "capture_progress"))
    assert streamed > 0, "a live capture delivered nothing"
    assert of_type(ws, "capture_complete")[0]["total_samples"] >= streamed


def test_a_live_capture_keeps_no_history(handler, fast_progress):
    """An endless run must not accumulate a list that grows until the process dies."""
    ws = FakeWebSocket()

    run_unlimited(handler, ws, rate_hz=200, run_for=0.25)

    complete = of_type(ws, "capture_complete")[0]
    assert complete["samples"] == []
    assert complete["total_samples"] > 0


def test_every_live_sample_is_delivered_exactly_once(handler, fast_progress):
    ws = FakeWebSocket()

    run_unlimited(handler, ws, rate_hz=200, run_for=0.25)

    progress = of_type(ws, "capture_progress")
    offset = 0
    for m in progress:
        assert m["offset"] == offset, "a batch did not start where the last ended"
        offset += len(m["samples"])
    assert offset == of_type(ws, "capture_complete")[0]["total_samples"]


def test_changing_the_rate_does_not_restart_the_run(handler, fast_progress):
    """The trace on screen has to survive a turn of the timebase knob."""
    ws = FakeWebSocket()

    run_unlimited(handler, ws, rate_hz=50, run_for=0.3,
                  mid=lambda: setattr(handler, "_capture_rate_hz", 400.0))

    # One run, one ending -- not a teardown and a fresh start.
    assert len(of_type(ws, "capture_complete")) == 1

    # Timestamps keep climbing across the change; a restart would reset them.
    stamps = [row[0] for m in of_type(ws, "capture_progress") for row in m["samples"]]
    assert stamps == sorted(stamps)

    # And the new rate really took hold: samples land closer together after the
    # change than before it. Compared as spacing rather than as counts, since
    # the two halves cover different spans of time.
    change_ms = 300
    before = [s for s in stamps if s < change_ms]
    after = [s for s in stamps if s > change_ms + 20]
    spacing = lambda xs: (xs[-1] - xs[0]) / (len(xs) - 1)
    assert spacing(after) < spacing(before) / 2
