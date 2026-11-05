"""The runner for a service's own gates, driven over scratch roots.

Each arm builds a service root with a `scripts/` directory and runs the kit gate as a
subprocess with `--root`, the way CI runs it, so the verdict comes from the real entry
point and the real exit code.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[2]
GATE = TEMPLATE / "service-kit" / "gates" / "check_service_gates.py"


def _root(tmp_path: Path, **gates: str) -> Path:
    (tmp_path / "scripts").mkdir()
    for name, body in gates.items():
        (tmp_path / "scripts" / f"check_{name}.py").write_text(body + "\n")
    return tmp_path


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(GATE), "--root", str(root)],
                          capture_output=True, text=True, timeout=300)


def test_green_and_named_skip_gates_pass_and_are_counted_apart(tmp_path) -> None:
    done = _run(_root(tmp_path, a="raise SystemExit(0)", b="raise SystemExit(3)"))
    assert done.returncode == 0, done.stderr
    assert "2 service gate(s)" in done.stdout
    assert "1 green, 1 named skip(s), 0 red" in done.stdout


@pytest.mark.parametrize("body, reason", [
    ("raise SystemExit(1)", "(finding)"),
    ("raise SystemExit(2)", "(refusal)"),
    ("raise SystemExit(7)", "(unexpected exit)"),
    ("raise RuntimeError('boom')", "crashed"),
])
def test_every_other_outcome_is_red_and_named(tmp_path, body, reason) -> None:
    done = _run(_root(tmp_path, ok="raise SystemExit(0)", bad=body))
    assert done.returncode == 1, (done.stdout, done.stderr)
    assert "check_bad.py" in done.stdout and reason in done.stdout
    assert "1 red" in done.stderr


def test_a_service_with_no_gates_of_its_own_is_green_and_says_so(tmp_path) -> None:
    done = _run(_root(tmp_path))
    assert done.returncode == 0, done.stderr
    assert "0 service gate(s)" in done.stdout


def test_a_gate_that_exits_0_after_a_caught_traceback_is_green(tmp_path) -> None:
    body = (
        "import traceback\ntry:\n    raise ValueError('handled')\n"
        "except ValueError:\n    traceback.print_exc()\nraise SystemExit(0)"
    )
    done = _run(_root(tmp_path, logs=body))
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert "1 green, 0 named skip(s), 0 red" in done.stdout


def test_a_named_skip_quoting_a_traceback_header_is_still_a_skip(tmp_path) -> None:
    body = (
        "import sys\n"
        "print('Traceback (most recent call last) was expected', file=sys.stderr)\n"
        "raise SystemExit(3)"
    )
    done = _run(_root(tmp_path, skips=body))
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert "0 green, 1 named skip(s), 0 red" in done.stdout


def test_a_gate_in_another_language_is_refused_by_name(tmp_path) -> None:
    root = _root(tmp_path, ok="raise SystemExit(0)")
    (root / "scripts" / "check_x.sh").write_text("exit 0\n")
    done = _run(root)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert "check_x.sh" in done.stderr


def test_the_iac_gate_is_counted_as_run_elsewhere_not_refused(tmp_path) -> None:
    root = _root(tmp_path, ok="raise SystemExit(0)")
    (root / "scripts" / "check_service_iac.sh").write_text("exit 1\n")
    done = _run(root)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert "1 run by their own ci.yml step (check_service_iac.sh)" in done.stdout


def test_a_non_gate_script_is_not_run(tmp_path) -> None:
    root = _root(tmp_path, ok="raise SystemExit(0)")
    (root / "scripts" / "prove_something.py").write_text("raise SystemExit(1)\n")
    done = _run(root)
    assert done.returncode == 0 and "1 service gate(s)" in done.stdout


def test_a_classifier_that_cannot_fail_is_refused_before_any_verdict(tmp_path, monkeypatch) -> None:
    """The inline control is what stops a blinded runner reporting green. With the
    classifier forced to tolerate everything, the run must refuse (exit 2), not pass."""
    import importlib.util

    monkeypatch.syspath_prepend(str(GATE.parent))
    spec = importlib.util.spec_from_file_location("check_service_gates", GATE)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    root = _root(tmp_path, bad="raise SystemExit(1)")
    assert gate.main(["--root", str(root)]) == 1
    def blinded(g, r):
        return True, f"{g.name}: blinded"

    assert gate.main(["--root", str(root)], classify=blinded) == 2


def test_the_verdict_uses_the_classifier_the_control_ran(tmp_path, monkeypatch, capsys) -> None:
    """an earlier change. The control and the verdict must share one classifier.

    This classifier agrees with `run_one` on the control's own scratch gates and calls
    every gate under the service root red. A `main()` whose verdict went back to
    `run_one` would pass the control and then report a green gate as green; with the
    injected classifier reaching the verdict, the one green gate is a finding.
    """
    import importlib.util

    monkeypatch.syspath_prepend(str(GATE.parent))
    spec = importlib.util.spec_from_file_location("check_service_gates", GATE)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    root = _root(tmp_path, good="print('ok')")
    assert gate.main(["--root", str(root)]) == 0

    def red_outside_the_control(g, r):
        if "service-gates-control-" in str(r):
            return gate.run_one(g, r)
        return False, f"{g.name}: forced"

    capsys.readouterr()
    assert gate.main(["--root", str(root)], classify=red_outside_the_control) == 1
    assert "check_good.py: forced" in capsys.readouterr().out
