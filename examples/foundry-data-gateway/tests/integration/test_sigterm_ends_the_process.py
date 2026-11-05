"""SIGTERM must END the real service process, inside its grace ceiling.

The unit tests in `tests/unit/test_runtime_shutdown.py` prove the handler hands
the signal on. This proves the thing an operator depends on: the REAL
entrypoint, started the way the container starts it, exits after SIGTERM. The
unit tests could not have caught `an earlier change` on their own, because the
defect was an interaction -- the handler is installed inside `uvicorn.run`, so
the handler it displaced was uvicorn's, and only a running server shows whether
anything still tells uvicorn to stop. Before the fix this process was alive 80s
after two SIGTERMs; ECS would have SIGKILLed it at `stopTimeout`.
"""

from __future__ import annotations

import signal
import subprocess
import time

from tests.support.engines import local_database_url
from tests.support.serving import free_port, start_service, wait_until_serving

GRACE_SECONDS = 5
#: Slack over the grace ceiling for interpreter exit and a loaded machine. The
#: defect is unbounded, so any finite wait separates the two outcomes.
EXIT_SLACK_SECONDS = 15


def test_sigterm_ends_the_real_process_within_its_grace_ceiling() -> None:
    port = free_port()
    process = start_service(
        port,
        {
            "SERVICE_ENV": "local",
            "DATABASE_URL": local_database_url("sigterm-"),
            "SHUTDOWN_GRACE_SECONDS": str(GRACE_SECONDS),
        },
    )
    try:
        wait_until_serving(port, process, time.monotonic() + 60)

        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=GRACE_SECONDS + EXIT_SLACK_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(
                f"still running {GRACE_SECONDS + EXIT_SLACK_SECONDS}s after SIGTERM: the "
                "shutdown handler is not handing the signal to the server"
            ) from exc
        output = process.stdout.read() if process.stdout else ""
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

    # Uvicorn restores the original handler after serving and re-raises the
    # signal it caught, so a clean shutdown ends as "killed by SIGTERM".
    assert process.returncode in (0, -signal.SIGTERM), (process.returncode, output)
    assert "shutdown.complete" in output, (
        "the process exited without running the drain -- it must stop THROUGH the "
        f"lifecycle, not around it:\n{output}"
    )
