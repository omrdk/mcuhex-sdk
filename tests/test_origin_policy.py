"""Behavioral tests for who is allowed to open a WebSocket to the SDK."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from probe.dummyprobe import DummyProbe
from server import WebSocketServer

WEB_APP = "https://mcuhex.com"
DRIVE_BY_SITE = "https://totally-unrelated.example"


async def serve():
    """Run the real server on a free port and hand back its URL."""
    server = WebSocketServer(host="127.0.0.1", port=0, probe_cls=DummyProbe)
    task = asyncio.create_task(server.start_server())
    for _ in range(100):
        if getattr(server, "_ws_server", None):
            break
        await asyncio.sleep(0.01)
    port = server._ws_server.sockets[0].getsockname()[1]
    return server, task, f"ws://127.0.0.1:{port}"


async def with_server(body):
    server, task, url = await serve()
    try:
        return await body(url)
    finally:
        await server.stop_server()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def run(body):
    return asyncio.run(with_server(body))


async def ask_list_probes(url, origin):
    kwargs = {"additional_headers": {"Origin": origin}} if origin else {}
    async with websockets.connect(url, **kwargs) as ws:
        await ws.send(json.dumps({"cmd": "list_probes", "id": 1}))
        return json.loads(await asyncio.wait_for(ws.recv(), timeout=5))


def test_a_random_site_cannot_reach_the_probe():
    async def body(url):
        with pytest.raises(websockets.exceptions.InvalidStatus) as excinfo:
            await ask_list_probes(url, DRIVE_BY_SITE)
        return excinfo.value

    assert run(body).response.status_code == 403


def test_the_web_app_is_served():
    resp = run(lambda url: ask_list_probes(url, WEB_APP))

    assert resp["status"] == 0
    assert "PyOCDProbe" in resp["probes"]


def test_a_client_without_an_origin_is_served():
    """The CLI and the VS Code extension send no Origin header."""
    resp = run(lambda url: ask_list_probes(url, None))

    assert resp["status"] == 0
