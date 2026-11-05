"""A test that hangs dumps every thread's stack, and nothing asserted that.

WHAT WENT WRONG. comms-core's full suite hung for 25 minutes in an untimed lock
wait and printed nothing. The only evidence was a process that
had stopped producing output, so the wait had to be found by attaching to it.

`faulthandler_timeout` in `pyproject.toml` makes pytest dump all thread stacks
to stderr once one test has run that many seconds. The test is not failed and
not stopped; the dump says which line each thread is on. `new_service.sh` copies
both the setting and this file, so an emitted service starts with them.

WHY THIS READS `pytestconfig` AND NOT `pyproject.toml`. The line in the file is
not the setting. `-o faulthandler_timeout=0` on a command line, `-p
no:faulthandler`, or a parent inifile that pytest picked instead of this one
each leave the file unchanged and the suite without a dump. `getini` returns
what this run was configured with.

WHAT IT DOES NOT PROVE. It does not hang a test to watch the dump appear; that
is pytest's own behaviour and would cost the timeout on every run. And pytest
times TESTS: a hang in `pytest_sessionstart`, where the kit's real profile
brings its containers up, gets no dump at any value (measured: a 3-second sleep
there under a 1-second timeout printed nothing). The ceiling
keeps the value useful: a dump that arrives after ten minutes is a hang nobody
waited for.
"""

from __future__ import annotations

import pytest

CEILING_SECONDS = 600


def test_a_test_that_hangs_gets_its_thread_stacks_dumped(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.pluginmanager.has_plugin("faulthandler"), (
        "pytest's faulthandler plugin is not loaded, so no timeout can dump a stack"
    )
    seconds = float(pytestconfig.getini("faulthandler_timeout") or 0)
    assert 0 < seconds <= CEILING_SECONDS, (
        f"faulthandler_timeout is {seconds!r} for this run; a hung test prints nothing "
        f"unless it is above 0 and at most {CEILING_SECONDS}"
    )
