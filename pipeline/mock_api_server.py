"""Start and stop the mock API for one pipeline run (D-043).

The server is a real `uvicorn` subprocess, so extraction goes through the same
HTTP path and settings as a manual run. `mock_api_server` is a context manager:
leaving it for any reason, an exception or Ctrl-C included, stops the process.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
READY_PATH = "/rest/agile/1.0/board"
READY_TIMEOUT_SECONDS = 15.0
READY_POLL_SECONDS = 0.2
STOP_TIMEOUT_SECONDS = 10.0


class MockApiError(RuntimeError):
    """The mock API exited early, or never answered."""


def port_in_use(host: str, port: int) -> bool:
    """True if something accepts connections on host:port."""
    try:
        socket.create_connection((host, port), timeout=1).close()
    except OSError:
        return False
    return True


def _wait_until_ready(process: subprocess.Popen, base_url: str, token: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    headers = {"Authorization": f"Bearer {token}"}
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise MockApiError(f"The mock API exited with code {process.returncode} before it answered.")
        try:
            if httpx.get(f"{base_url}{READY_PATH}", params={"maxResults": 1}, headers=headers).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(READY_POLL_SECONDS)
    raise MockApiError(f"The mock API did not answer on {base_url} within {timeout:g} seconds.")


def _stop(process: subprocess.Popen) -> None:
    """SIGTERM, then SIGKILL if it is still running after STOP_TIMEOUT_SECONDS."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


@contextmanager
def mock_api_server(
    host: str, port: int, token: str, ready_timeout: float = READY_TIMEOUT_SECONDS
) -> Iterator[subprocess.Popen]:
    """Serve the mock API on host:port until the block ends.

    Raises:
        MockApiError: If it exits early or does not answer in time.
    """
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_api.app:app", "--host", host, "--port", str(port), "--log-level", "warning"],
        cwd=REPO_ROOT,
    )
    try:
        _wait_until_ready(process, f"http://{host}:{port}", token, ready_timeout)
        yield process
    finally:
        _stop(process)
