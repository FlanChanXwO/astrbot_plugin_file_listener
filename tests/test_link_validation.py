from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from astrbot_plugin_file_listener.core.link_validation import (
    LinkValidationResult,
    probe_file_link,
)


class _ProbeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/file":
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", "bytes 0-0/4")
            self.send_header("Content-Length", "1")
            self.end_headers()
            self.wfile.write(b"x")
            return

        if self.path == "/missing":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if self.path == "/server-error":
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self.send_response(500)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        del format, args


@pytest.fixture
def probe_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_probe_file_link_distinguishes_file_missing_and_transient_failure(
    probe_server: str,
) -> None:
    assert (
        await probe_file_link(f"{probe_server}/file")
        is LinkValidationResult.VALID
    )
    assert (
        await probe_file_link(f"{probe_server}/missing")
        is LinkValidationResult.INVALID
    )
    assert (
        await probe_file_link(f"{probe_server}/server-error")
        is LinkValidationResult.INDETERMINATE
    )
