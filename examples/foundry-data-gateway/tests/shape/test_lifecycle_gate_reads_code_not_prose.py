"""The lifecycle gate must read CODE, not the prose explaining it.

This is a regression test for a gate that was GREEN on the defect it exists to
catch. `check_lifecycle.py` searched raw file text for tokens like `SIGTERM`
across `runtime/`. Renaming `signal.SIGTERM` to `signal.SIGUSR1` in
`runtime/shutdown.py` -- deleting the only real handler registration, so the
service could no longer drain on shutdown -- left it at exit 0, because
`runtime/probes.py` and `runtime/middleware.py` each mention SIGTERM in prose
explaining why they behave as they do.

It then printed its full success line: "24 arms executed, 0 skipped, 0
unaccounted". A reader had no signal at all.

That is the worst shape a gate can have. The better the code is explained, the
less the gate checks, and the cheapest way to make it fire again is to DELETE the
explanation -- so the rule punishes exactly the thing you want people to do.
"""

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "service-kit" / "gates" / "check_lifecycle.py"
SHUTDOWN = ROOT / "src" / "foundry_data_gateway" / "runtime" / "shutdown.py"


def _gate() -> object:
    spec = importlib.util.spec_from_file_location("check_lifecycle", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prose_alone_does_not_satisfy_a_clause():
    """A docstring naming the token must NOT count as the token being present."""
    gate = _gate()
    prose_only = '"""This module installs the SIGTERM handler and drains."""\nVALUE = 1\n'
    assert "SIGTERM" not in gate._strip_prose(prose_only)

    comment_only = "# SIGTERM is handled here, honestly\nVALUE = 1\n"
    assert "SIGTERM" not in gate._strip_prose(comment_only)


def test_real_code_still_counts():
    """The other direction: stripping prose must not blind the gate to code.

    Without this, 'strip everything' would pass the test above and break the gate.
    """
    gate = _gate()
    real = "import signal\nsignal.signal(signal.SIGTERM, handler)\n"
    assert "SIGTERM" in gate._strip_prose(real)

    # a test function's NAME is code, and naming the behaviour is legitimate
    named = "def test_sigterm_drains_in_flight():\n    pass\n"
    assert "sigterm" in gate._strip_prose(named).lower()


def test_the_gate_goes_red_when_the_real_handler_is_removed():
    """End to end, against the REAL gate and the REAL module.

    The founding mutation: prose untouched, handler gone. Before the fix this
    returned exit 0.
    """
    original = SHUTDOWN.read_text()
    assert "signal.SIGTERM" in original, "the mutation target is not present"
    try:
        SHUTDOWN.write_text(original.replace("signal.SIGTERM", "signal.SIGUSR1"))
        red = subprocess.run(
            [sys.executable, str(GATE)], capture_output=True, text=True, cwd=ROOT, timeout=300
        )
        assert red.returncode != 0, (
            "the gate stayed green with no SIGTERM handler -- it is reading prose again"
        )
        assert "SIGTERM" in red.stdout + red.stderr
    finally:
        SHUTDOWN.write_text(original)

    green = subprocess.run(
        [sys.executable, str(GATE)], capture_output=True, text=True, cwd=ROOT, timeout=300
    )
    assert green.returncode == 0, "restore did not return the gate to green"


def test_the_stripper_leaves_syntactically_valid_code():
    """A module whose docstring is its ONLY statement must not become invalid."""
    gate = _gate()
    ast.parse(gate._strip_prose('"""Only a docstring."""\n'))
