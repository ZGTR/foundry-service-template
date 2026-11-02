"""`check_service_iac.sh` reads a print's two streams apart, and says what it read.

`serverless print` writes the resolved config to stdout and a configuration warning to
stderr, and exits 0 on a warning. The gate used to merge the two into the one file four
of its arms parse, so a warning turned those arms red through a YAML parser traceback
instead of through a finding (first seen with the mutant
below).

The tests of the whole gate run it on a committed copy of this template, four runs in
all, so their assertions are about what the gate prints. The gate needs terraform or
tofu, and npx, exactly as it does in CI. The tests of the two classifiers run each
one's own text, read out of the gate, against a file: they hold the clauses no
config can reach, and they are fast.

Two of the four runs have `FORCE_COLOR=1` and `SLS_LOG_LEVEL=error` in the gate's
environment. With the first the framework colours its stderr even into a file, and the
heading the classifier looks for then starts with an escape sequence. With the second
it writes no warning at all. Either way the gate was green on a warned config.

This file is template-only (`TEMPLATE_ONLY` in `scripts/new_service.sh`). It edits
`deploy/`, which a service owns, by exact text, so in a service that changed its
config it went red on a correct one.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[2]
GATE = "scripts/check_service_iac.sh"
WORKFLOW = ".github/workflows/ci.yml"
SERVERLESS = "deploy/iac/serverless.yml"
CALLER_VPC = "deploy/iac/caller_vpc.yml"

PRINT_ARM = "serverless print resolves the config"
API_ARM = "the resolved config declares the api function"
NONE_ARM = "custom.callerVpc none attaches no function to a VPC"
ESTATE_ARM = (
    "custom.callerVpc estate attaches the caller group and the private subnets, "
    "and no function drops the group"
)
NEEDS_ARM = "custom.callerVpc estate without its two variables is refused"
# The arms that parse the committed config's print. Named here by hand: a list read
# out of the gate would follow the gate.
PARSING_ARMS = (
    "no resolved function is deployed with an import-only command",
    "no non-HTTP function competes with the adapter's runtime client",
    "custom.callerVpc is none or estate",
    "custom.callerVpc estate has its two variables declared in deploy/deploy_env.json",
)

SELECTOR = "  vpc: ${file(./caller_vpc.yml):${self:custom.callerVpc}, null}\n"
ESTATE_KEY = "estate:\n  securityGroupIds:\n"
LISTENER = "${env:INTERNAL_LISTENER_ARN}"
MEMORY = "    memorySize: 512\n"

# In place of npx: it says on stdout what environment the gate gave it, writes one line
# to stderr, and exits 2. The framework itself exits 1 when it refuses a print.
STAND_IN = """#!/bin/sh
echo "stand-in stdout: FORCE_COLOR=${FORCE_COLOR-unset} SLS_LOG_LEVEL=${SLS_LOG_LEVEL-unset}" \\
  "AWS_REGION=${AWS_REGION-unset}"
echo "stand-in stderr: the framework died" >&2
exit 2
"""
# What it says when the gate removed both variables. `AWS_REGION` is the control: the
# stand-in does see what the gate passes it.
GIVEN = "stand-in stdout: FORCE_COLOR=unset SLS_LOG_LEVEL=unset AWS_REGION=eu-west-1"

# The framework's own text (3.40.0) for a `provider.vpc` that is an empty mapping.
INVALID_VPC = (
    "\nWarning: Invalid configuration encountered\n"
    "  at 'provider.vpc': must have required property 'securityGroupIds'\n"
    "  at 'provider.vpc': must have required property 'subnetIds'\n"
    "\nLearn more about configuration validation here: http://slss.io/configuration-validation\n"
    "\n1 deprecation found: run 'serverless doctor' for more details\n"
)
INVALID_VPC_IN_ONE_LINE = (
    "Warning: Invalid configuration encountered; "
    "at 'provider.vpc': must have required property 'securityGroupIds'; "
    "at 'provider.vpc': must have required property 'subnetIds'"
)


def _coloured(on: str) -> str:
    """The same warning as the framework wrote it to a FILE with `FORCE_COLOR` set
    (3.40.0, node 26.10.0): every line opens with the colour and closes with `\\x1b[39m`,
    so the heading does not start the line and neither does the indentation under it."""
    off, grey = "\x1b[39m", "\x1b[90m"
    return (
        f"\n{on}Warning:{off} {on}Invalid configuration encountered{off}\n"
        f"{on}  at 'provider.vpc': must have required property 'securityGroupIds'{off}\n"
        f"{on}  at 'provider.vpc': must have required property 'subnetIds'{off}\n"
        f"{on}{off}\n"
        f"{on}Learn more about configuration validation here: "
        f"http://slss.io/configuration-validation{off}\n"
        f"\n{on}{grey}1 deprecation found: run 'serverless doctor' for more details{off}{on}{off}\n"
    )


# `FORCE_COLOR=1` alone, and with `CI=true` beside it.
ORANGE, YELLOW = "\x1b[38;2;255;165;0m", "\x1b[93m"
# And for a variable nothing sets. It is written to stdout, in place of the config.
REFUSAL = (
    "Error:\nCannot resolve serverless.yml: Variables resolution errored with:\n"
    '  - Cannot resolve variable at "functions.api.events.0.alb.listenerArn": '
    'Value not found at "env" source\n'
)


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"{path.name} does not hold {old!r} exactly once"
    path.write_text(text.replace(old, new), encoding="utf-8")


def _committed_copy(dest: Path) -> Path:
    """What the gate reads, as it is on disk, in `dest`: the gate, the deploy tree and
    the workflow. `_run_gate` commits it, because one arm asks git whether a lock file
    was rewritten.

    Copied by path and not from `git ls-files`: the gate under test is the one on
    disk, committed or not.
    """
    built = shutil.ignore_patterns(".terraform", ".serverless", "node_modules", "__pycache__")
    shutil.copytree(TEMPLATE / "deploy", dest / "deploy", ignore=built)
    for name in (GATE, WORKFLOW):
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(TEMPLATE / name, target)
    return dest


def _run_gate(root: Path, **gate_env: str) -> subprocess.CompletedProcess[str]:
    """Commit `root` and run its gate. `gate_env` is added to this process's environment
    for the gate alone; with none, the gate inherits it as it is."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "copy"]):
        done = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            env=env,
        )
        assert done.returncode == 0, f"git {args} failed: {done.stderr}"
    return subprocess.run(
        ["bash", GATE],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
        env={**os.environ, **gate_env},
    )


def _red_arms(result: subprocess.CompletedProcess[str]) -> list[str]:
    prefix = "[service-iac] FAIL: "
    return [
        line[len(prefix) :]
        for line in result.stderr.splitlines()
        if line.startswith(prefix) and " arm(s) red; " not in line
    ]


def _said(result: subprocess.CompletedProcess[str]) -> str:
    return f"exit {result.returncode}\n{result.stdout}\n{result.stderr}"


def _under_arm(result: subprocess.CompletedProcess[str], arm: str) -> str:
    """What the gate wrote to stderr for `arm`: the lines between the red arm before
    it, or the start, and its own. An arm's findings are printed before its FAIL line."""
    marker = f"[service-iac] FAIL: {arm}\n"
    assert result.stderr.count(marker) == 1, _said(result)
    before = result.stderr[: result.stderr.index(marker)]
    return before[max(before.rfind("[service-iac] FAIL: "), 0) :]


def _classifier(tag: str, target: Path) -> subprocess.CompletedProcess[str]:
    """Run the gate's own `tag` heredoc against `target`, as the gate runs it."""
    text = (TEMPLATE / GATE).read_text(encoding="utf-8")
    bodies = re.findall(rf"<<'{tag}'\n(.*?)\n{tag}\n", text, flags=re.S)
    assert len(bodies) == 1, f"the gate holds {len(bodies)} {tag} heredoc(s), not one"
    return subprocess.run(
        ["/usr/bin/python3", "-", str(target)],
        input=bodies[0],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _warning(tmp_path: Path, text: str | None) -> subprocess.CompletedProcess[str]:
    target = tmp_path / "print.err"
    if text is not None:
        target.write_text(text, encoding="utf-8")
    return _classifier("PYWARNING", target)


def _reads(tmp_path: Path, text: str | None) -> subprocess.CompletedProcess[str]:
    target = tmp_path / "print.out"
    if text is not None:
        target.write_text(text, encoding="utf-8")
    return _classifier("PYREADS", target)


@pytest.fixture(scope="module")
def warned(tmp_path_factory: pytest.TempPathFactory) -> subprocess.CompletedProcess[str]:
    """The gate over a config the framework resolves with two configuration warnings,
    one on each value of the switch, in ONE run.

    `none` selects an empty mapping for `provider.vpc` instead of null: the committed
    print warns (the committed switch is `none`), and so does the forced-`none` one.
    `estate` carries a property `provider.vpc` does not have: only the forced-`estate`
    print warns about it, and that print resolves to the right groups and subnets all
    the same, so the warning is the one thing that arm has to say."""
    root = _committed_copy(tmp_path_factory.mktemp("warned"))
    _replace_once(
        root / SERVERLESS, SELECTOR, "  vpc: ${file(./caller_vpc.yml):${self:custom.callerVpc}}\n"
    )
    _replace_once(
        root / CALLER_VPC,
        ESTATE_KEY,
        "none: {}\nestate:\n  assignPublicIp: true\n  securityGroupIds:\n",
    )
    return _run_gate(root)


@pytest.fixture(scope="module")
def unresolved(tmp_path_factory: pytest.TempPathFactory) -> subprocess.CompletedProcess[str]:
    """The gate over a config the framework refuses: it reads a variable nothing sets."""
    root = _committed_copy(tmp_path_factory.mktemp("unresolved"))
    _replace_once(root / SERVERLESS, LISTENER, "${env:NO_SUCH_VARIABLE_FOR_THIS_TEST}")
    return _run_gate(root)


@pytest.fixture(scope="module")
def misspelt_under_colour(
    tmp_path_factory: pytest.TempPathFactory,
) -> subprocess.CompletedProcess[str]:
    """The gate with `FORCE_COLOR=1` and `SLS_LOG_LEVEL=error` in its environment, over a
    misspelt `reservedConcurrency`: the framework warns, resolves, and deploys without
    the cap. A print that kept either variable has no warning the classifier finds."""
    root = _committed_copy(tmp_path_factory.mktemp("misspelt"))
    _replace_once(root / SERVERLESS, MEMORY, MEMORY + "    reservedConcurency: 5\n")
    return _run_gate(root, FORCE_COLOR="1", SLS_LOG_LEVEL="error")


@pytest.fixture(scope="module")
def stood_in(tmp_path_factory: pytest.TempPathFactory) -> subprocess.CompletedProcess[str]:
    """The gate with `STAND_IN` first on PATH as npx, and `FORCE_COLOR=1` and
    `SLS_LOG_LEVEL=error` in its environment. Every print then exits 2, and each arm
    that ran one shows what the stand-in wrote, which is the environment the gate gave
    the print."""
    root = _committed_copy(tmp_path_factory.mktemp("stood-in"))
    bin_dir = tmp_path_factory.mktemp("stand-in-bin")
    (bin_dir / "npx").write_text(STAND_IN, encoding="utf-8")
    (bin_dir / "npx").chmod(0o755)
    return _run_gate(
        root,
        FORCE_COLOR="1",
        SLS_LOG_LEVEL="error",
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    )


def test_a_configuration_warning_turns_the_print_arm_red_by_name(
    warned: subprocess.CompletedProcess[str],
) -> None:
    assert warned.returncode == 1, _said(warned)
    assert PRINT_ARM in _red_arms(warned), _said(warned)
    under = _under_arm(warned, PRINT_ARM)
    assert "Warning: Invalid configuration encountered" in under, _said(warned)
    assert "at 'provider.vpc': must have required property 'securityGroupIds'" in under
    assert "at 'provider.vpc': must have required property 'subnetIds'" in under


#: Three ways the framework can fail the live control, each a stand-in npx that runs the
#: real one. Each must be refused at 2 by its own clause of the control:
#: SILENT reintroduces the ambient silence AFTER the gate removed it, so the print of
#: the planted property carries no warning (the classifier clause);
#: OTHER makes the control's copy warn about a property other than the plant (the
#: clause that the warning names the plant);
#: EXIT makes the control's print warn as it should and then exit 1 (the clause that
#: the print itself succeeded).
LIVE_CONTROL_STAND_INS = {
    "silent": 'SLS_LOG_LEVEL=error exec {npx} "$@"\n',
    "other": (
        'case "$PWD" in */warning-control)\n'
        "  sed -i.bak 's/^eilWarningControlUnknown:/eilSomeOtherUnknown:/' serverless.yml ;;\n"
        "esac\n"
        'exec {npx} "$@"\n'
    ),
    "exit": (
        '{npx} "$@" || exit $?\n'
        'case "$PWD" in */warning-control) exit 1 ;; esac\n'
    ),
}


@pytest.mark.parametrize("stand_in", sorted(LIVE_CONTROL_STAND_INS))
def test_a_warning_silent_framework_is_refused_by_the_live_control(
    tmp_path: Path, stand_in: str
) -> None:
    root = _committed_copy(tmp_path / "service")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npx = shutil.which("npx")
    assert npx, "the live warning control needs the framework"
    wrapper = bin_dir / "npx"
    # Each stand-in exercises the real framework's warning path, not a fabricated
    # stderr fixture.
    wrapper.write_text(
        "#!/bin/sh\n" + LIVE_CONTROL_STAND_INS[stand_in].format(npx=shlex.quote(npx))
    )
    wrapper.chmod(0o755)
    done = _run_gate(root, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert done.returncode == 2, _said(done)
    assert "live print-warning control did not find the planted" in done.stderr
    assert "PASS" not in done.stdout


def _gate_function(name: str) -> str:
    """The gate's own definition of the shell function `name`, as written."""
    text = (TEMPLATE / GATE).read_text(encoding="utf-8")
    bodies = re.findall(rf"^{name}\(\) {{\n.*?^}}\n", text, flags=re.S | re.M)
    assert len(bodies) == 1, f"the gate holds {len(bodies)} definition(s) of {name}, not one"
    return bodies[0]


PLANT = "eilWarningControlUnknown"
FOUND = f"serverless print resolved the config with a warning, which ... : {PLANT}"


#: The live control's cases, run on the gate's own function with the print and the
#: classifier stood in: (print exit, classifier exit, classifier output, the control
#: finds the plant). `found` is the positive case. Three red cases each break exactly
#: one clause: classifier-fault, names-another-property and print-failed.
#: classifier-quiet breaks two, the classifier-status and the required-match clause,
#: and is kept as the quiet case. The classifier-fault case is the one the review of
#: An earlier review reproduced with a write quota:
#: the classifier wrote the matching line and then died, so only the classifier-status
#: clause tells it from a find.
LIVE_CONTROL_CLAUSES = {
    "found": (0, 1, FOUND, True),
    "classifier-fault": (0, 120, FOUND, False),
    "classifier-quiet": (0, 0, "", False),
    "names-another-property": (0, 1, FOUND.replace(PLANT, "eilSomeOtherUnknown"), False),
    "print-failed": (1, 1, FOUND, False),
}


@pytest.mark.parametrize("case", sorted(LIVE_CONTROL_CLAUSES))
def test_each_clause_of_the_live_control_refuses_on_its_own(tmp_path: Path, case: str) -> None:
    printed, classified, says, found = LIVE_CONTROL_CLAUSES[case]
    sls_dir = tmp_path / "sls"
    sls_dir.mkdir()
    (sls_dir / "serverless.yml").write_text("service: s\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "npx").write_text(f"#!/bin/sh\nexit {printed}\n", encoding="utf-8")
    (bin_dir / "npx").chmod(0o755)
    script = (
        _gate_function("refuse")
        + _gate_function("sls_live_warning_control")
        + "sls_print_has_no_warning() {\n"
        + f"  printf '%s\\n' {shlex.quote(says)} >&2\n  return {classified}\n}}\n"
        + f'WORK={shlex.quote(str(tmp_path / "work"))}; mkdir -p "$WORK"\n'
        + f"SLS_DIR={shlex.quote(str(sls_dir))}\n"
        + "SLS_PRINT_ENV=(); SLS_PLACEHOLDERS=(); CALLER_VPC_PLACEHOLDERS=()\n"
        + "sls_live_warning_control\n"
    )
    done = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
    )
    if found:
        assert done.returncode == 0, _said(done)
        assert f"live print-warning control found {PLANT}" in done.stdout, _said(done)
    else:
        assert done.returncode == 2, _said(done)
        assert "live print-warning control did not find the planted" in done.stderr, _said(done)
        assert f"print={printed} classifier={classified}" in done.stderr, _said(done)


def test_under_a_warning_no_arm_dies_in_the_yaml_parser(
    warned: subprocess.CompletedProcess[str],
) -> None:
    said = _said(warned)
    assert "Traceback" not in said, said
    assert "ScannerError" not in said, said


def test_under_a_warning_the_arms_that_parse_the_config_still_read_it(
    warned: subprocess.CompletedProcess[str],
) -> None:
    """The warnings are the print arm's finding and the two forced arms'. The config on
    stdout is whole, so an arm that only parses it has nothing to refuse."""
    assert sorted(_red_arms(warned)) == sorted([PRINT_ARM, NONE_ARM, ESTATE_ARM]), _said(warned)
    for arm in PARSING_ARMS:
        assert f"[service-iac] ok: {arm}" in warned.stdout, _said(warned)
    assert "3 of 20 arm(s) red; all 20 were executed" in warned.stderr, _said(warned)


def test_the_forced_none_arm_says_what_none_resolved_to(
    warned: subprocess.CompletedProcess[str],
) -> None:
    """It names no warning of its own: its `provider.vpc` is null or is this finding,
    and the committed print already carries any other warning."""
    under = _under_arm(warned, NONE_ARM)
    assert "custom.callerVpc none must resolve to no provider.vpc, got {}" in under, _said(warned)
    assert "Warning:" not in under, _said(warned)


def test_a_warning_only_the_forced_estate_print_carries_is_that_arms_finding(
    warned: subprocess.CompletedProcess[str],
) -> None:
    """No other arm can see it: the committed print resolves `none`, and the estate
    attachment holds the caller group and the subnets, so the arm's own reading of the
    config passes."""
    said = _said(warned)
    under = _under_arm(warned, ESTATE_ARM)
    assert "Warning: Invalid configuration encountered" in under, said
    assert "at 'provider.vpc': unrecognized property 'assignPublicIp'" in under, said
    assert "required property" not in under, said
    assert "custom.callerVpc estate: 1 resolved function(s)" in warned.stdout, said
    # Two warnings in the whole run: the committed print's and this one.
    assert warned.stderr.count("Warning: Invalid configuration encountered") == 2, said
    assert "unrecognized property" not in _under_arm(warned, PRINT_ARM), said


def test_a_print_the_framework_refuses_shows_the_frameworks_reason(
    unresolved: subprocess.CompletedProcess[str],
) -> None:
    assert unresolved.returncode == 1, _said(unresolved)
    assert PRINT_ARM in _red_arms(unresolved), _said(unresolved)
    # The framework names the property, not the variable, and writes it to stdout.
    # Every later print is refused for the same reason, so the text is looked for
    # under this arm and not anywhere in the output.
    under = _under_arm(unresolved, PRINT_ARM)
    assert "serverless print exited 1" in under, _said(unresolved)
    assert 'Cannot resolve variable at "functions.api.events.0.alb.listenerArn"' in under, _said(
        unresolved
    )


def test_an_arm_with_no_config_to_parse_says_so_instead_of_crashing(
    unresolved: subprocess.CompletedProcess[str],
) -> None:
    said = _said(unresolved)
    assert "Traceback" not in said, said
    red = _red_arms(unresolved)
    for arm in PARSING_ARMS:
        assert arm in red, said
    assert unresolved.stderr.count("is not a resolved config") >= len(PARSING_ARMS), said


def test_the_arm_that_greps_the_print_says_it_had_nothing_to_read(
    unresolved: subprocess.CompletedProcess[str],
) -> None:
    """It is the fifth reader of the committed print. It parses nothing, so without the
    reader in front of it a refused print left it red with no line of its own."""
    under = _under_arm(unresolved, API_ARM)
    assert "sls.out is not a resolved config: it does not parse as YAML" in under, _said(unresolved)


@pytest.mark.parametrize("arm", [PRINT_ARM, ESTATE_ARM], ids=["committed", "forced-estate"])
def test_neither_removed_variable_in_the_environment_hides_a_warning(
    misspelt_under_colour: subprocess.CompletedProcess[str], arm: str
) -> None:
    """One arm for each print command: the committed print, and a forced one."""
    said = _said(misspelt_under_colour)
    assert misspelt_under_colour.returncode == 1, said
    assert "PASS: all" not in misspelt_under_colour.stdout, said
    assert arm in _red_arms(misspelt_under_colour), said
    assert (
        "Warning: Invalid configuration encountered; "
        "at 'functions.api': unrecognized property 'reservedConcurency'"
    ) in _under_arm(misspelt_under_colour, arm), said


def test_a_print_that_exits_above_one_is_refused(
    stood_in: subprocess.CompletedProcess[str],
) -> None:
    assert stood_in.returncode == 1, _said(stood_in)
    assert PRINT_ARM in _red_arms(stood_in), _said(stood_in)
    assert "serverless print exited 2" in _under_arm(stood_in, PRINT_ARM), _said(stood_in)


def test_a_refused_print_shows_both_of_its_streams(
    stood_in: subprocess.CompletedProcess[str],
) -> None:
    under = _under_arm(stood_in, PRINT_ARM)
    assert "stand-in stdout: " in under, _said(stood_in)
    assert "stand-in stderr: the framework died" in under, _said(stood_in)


def test_the_committed_print_runs_without_the_two_variables_the_gate_removes(
    stood_in: subprocess.CompletedProcess[str],
) -> None:
    assert GIVEN in _under_arm(stood_in, PRINT_ARM), _said(stood_in)


@pytest.mark.parametrize("arm", [NONE_ARM, ESTATE_ARM, NEEDS_ARM], ids=["none", "estate", "needs"])
def test_a_forced_print_runs_without_the_two_variables_the_gate_removes(
    stood_in: subprocess.CompletedProcess[str], arm: str
) -> None:
    assert GIVEN in _under_arm(stood_in, arm), _said(stood_in)


def test_no_print_of_the_run_saw_a_variable_the_gate_removes(
    stood_in: subprocess.CompletedProcess[str],
) -> None:
    """Four prints in a run: the committed one and the three forced ones. A print added
    later shows what it was given the same way, so it is counted here too."""
    assert stood_in.stderr.count(GIVEN) == 4, _said(stood_in)
    assert stood_in.stderr.count("stand-in stdout: ") == 4, _said(stood_in)
    assert "FORCE_COLOR=1" not in stood_in.stderr, _said(stood_in)
    assert "SLS_LOG_LEVEL=error" not in stood_in.stderr, _said(stood_in)


def test_the_warning_classifier_names_a_warning_in_one_line(tmp_path: Path) -> None:
    done = _warning(tmp_path, INVALID_VPC)
    assert done.returncode == 1, done.stderr
    assert done.stderr.count("\n") == 1, done.stderr
    assert INVALID_VPC_IN_ONE_LINE in done.stderr
    assert "deprecation" not in done.stderr, done.stderr


@pytest.mark.parametrize("on", [ORANGE, YELLOW], ids=["force-color", "force-color-in-ci"])
def test_the_warning_classifier_reads_a_coloured_warning(tmp_path: Path, on: str) -> None:
    """The finding is the one the plain stream gives, with no escape sequence in it."""
    done = _warning(tmp_path, _coloured(on))
    assert done.returncode == 1, done.stderr
    assert done.stderr.count("\n") == 1, done.stderr
    assert done.stderr.endswith(f"deploys past: {INVALID_VPC_IN_ONE_LINE}\n"), done.stderr
    assert "\x1b" not in done.stderr, repr(done.stderr)


@pytest.mark.parametrize(
    "text",
    ["WARNING: Invalid configuration encountered\n  at 'a': x\n", "Warnings: 2\n  at 'a': x\n"],
    ids=["capitals", "a-longer-word"],
)
def test_a_line_that_only_resembles_the_heading_is_not_a_warning(tmp_path: Path, text: str) -> None:
    done = _warning(tmp_path, text)
    assert done.returncode == 0, done.stderr
    assert done.stderr == "", done.stderr


def test_the_warning_classifier_reads_a_stream_that_is_not_utf8(tmp_path: Path) -> None:
    """A byte that is not UTF-8 is replaced, and the warning around it is still named."""
    target = tmp_path / "print.err"
    target.write_bytes(b"Warning: caf\xe9\n  at 'a': x\n")
    done = _classifier("PYWARNING", target)
    assert done.returncode == 1, done.stderr
    assert "Traceback" not in done.stderr, done.stderr
    assert "deploys past: Warning: caf�; at 'a': x\n" in done.stderr, done.stderr


@pytest.mark.parametrize(
    "text",
    [
        "",
        "npm warn exec The following package was not found and will be installed: serverless@3\n",
        "\n1 deprecation found: run 'serverless doctor' for more details\n",
        "Environment: darwin, node 26.10.0, framework 3.40.0\nDocs:        docs.serverless.com\n",
    ],
    ids=["empty", "npx-install", "deprecation-count", "environment-footer"],
)
def test_the_warning_classifier_is_quiet_on_a_stream_with_no_warning(
    tmp_path: Path, text: str
) -> None:
    done = _warning(tmp_path, text)
    assert done.returncode == 0, done.stderr
    assert done.stderr == "", done.stderr


def test_a_print_with_no_stderr_capture_is_not_a_print_with_no_warning(tmp_path: Path) -> None:
    done = _warning(tmp_path, None)
    assert done.returncode == 1, done.stderr
    assert "no stderr capture at print.err" in done.stderr, done.stderr
    assert "Traceback" not in done.stderr, done.stderr


def test_the_reader_passes_a_resolved_config(tmp_path: Path) -> None:
    done = _reads(tmp_path, "service: example\nprovider:\n  name: aws\nfunctions:\n  api: {}\n")
    assert done.returncode == 0, done.stderr
    assert done.stderr == "", done.stderr


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (REFUSAL, "it does not parse as YAML (mapping values are not allowed here)"),
        ("", "it is NoneType, not a mapping"),
        ("nothing to print\n", "it is str, not a mapping"),
        ("service: example\n", "it has no provider key"),
        ("provider:\n  name: aws\n", "it has no service key"),
        ("functions: {}\n", "it has no service or provider key"),
    ],
    ids=["refusal", "empty", "scalar", "no-provider", "no-service", "neither"],
)
def test_the_reader_says_why_a_print_is_not_a_config(
    tmp_path: Path, text: str, reason: str
) -> None:
    done = _reads(tmp_path, text)
    assert done.returncode == 1, done.stderr
    assert f"print.out is not a resolved config: {reason}." in done.stderr, done.stderr
    assert "Traceback" not in done.stderr, done.stderr


def test_the_reader_says_when_no_print_wrote_the_file(tmp_path: Path) -> None:
    done = _reads(tmp_path, None)
    assert done.returncode == 1, done.stderr
    assert "print.out is not a resolved config: no print wrote it" in done.stderr, done.stderr
    assert "Traceback" not in done.stderr, done.stderr
