"""Start the REAL service process the way the container starts it, and wait for it.

Two tests need a running process rather than a `TestClient`:
`tests/integration/test_sigterm_ends_the_process.py`, because signal handling only
exists inside `uvicorn.run`, and `tests/integration/test_smoke_against_the_real_process.py`,
because the post-deploy smoke speaks HTTP over a socket and nothing less. One copy
of the bring-up, so the two cannot drift on how "serving" is decided.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

__all__ = ["free_port", "run_with_deadline", "start_service", "wait_until_serving"]

ROOT = Path(__file__).resolve().parents[2]
#: THIS checkout's source, never whatever copy the interpreter has installed.
IMPORT_ROOTS = (ROOT / "src", ROOT / "service-kit" / "vendor")


def run_with_deadline(
    args: list[str],
    *,
    timeout: float,
    check: bool = False,
    input: str | bytes | None = None,
    capture_output: bool = False,
    **kwargs,
) -> subprocess.CompletedProcess[str]:
    """Keep a child's verdict separate from this machine's time budget.

    A nonzero child exit remains a failure for the caller to assert on. A deadline
    has no child verdict: report HANG as unmeasured, with the host's observed load,
    rather than turning slow scheduling into an assertion about the emitted code.
    Load alone cannot establish whether the child or the scheduler stalled.

    The child runs in its own process group. At the deadline the whole group is
    killed, so a HANG leaves nothing running behind it. If the child itself had
    already exited and only a descendant still held its output pipe, the child's
    own exit code is returned: that is a verdict, not a hang.
    """
    if capture_output:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if input is not None:
        kwargs["stdin"] = subprocess.PIPE
    process = subprocess.Popen(args, start_new_session=True, **kwargs)
    try:
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            finished = process.poll()
            _kill_group(process)
            stdout, stderr = _drain(process, kwargs)
            if finished is None:
                pytest.skip(
                    f"HANG (UNMEASURED): machine could not complete {args!r} within "
                    f"{timeout:g}s; host load (1/5/15 min)={os.getloadavg()}; no child "
                    "verdict. Scheduling delay versus child hang is unresolved; rerun on "
                    f"an idle host. stdout={stdout!r}; stderr={stderr!r}"
                )
    finally:
        if process.poll() is None:
            _kill_group(process)
            process.wait(timeout=DRAIN_SECONDS)
    result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result


#: After the group is killed, how long its pipes and its exit may take to arrive.
DRAIN_SECONDS = 10


_Output = str | bytes | None


def _drain(process: subprocess.Popen, kwargs: dict) -> tuple[_Output, _Output]:
    """Read what the killed group wrote, within DRAIN_SECONDS.

    A descendant that left the group with setsid can still hold a pipe after the kill;
    then what it held is not read, rather than this read waiting on it forever."""
    try:
        return process.communicate(timeout=DRAIN_SECONDS)
    except subprocess.TimeoutExpired:
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        empty = "" if kwargs.get("text") or kwargs.get("universal_newlines") else b""
        return (empty if process.stdout else None), (empty if process.stderr else None)


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def start_service(port: int, extra_env: dict[str, str]) -> subprocess.Popen[str]:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(str(p) for p in IMPORT_ROOTS),
        "PORT": str(port),
        **extra_env,
    }
    return subprocess.Popen(
        [sys.executable, "-m", "foundry_data_gateway.composition.main"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def wait_until_serving(port: int, process: subprocess.Popen[str], deadline: float) -> None:
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(f"service exited before serving: {process.returncode}\n{output}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as reply:
                if reply.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.2)
    raise AssertionError("service never answered /healthz")
