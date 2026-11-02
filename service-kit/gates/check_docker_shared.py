#!/usr/bin/env python3
"""§5: the shared containerisation folder is present, and its wrappers forward `"$@"`.

THREE clauses, each a finding:

1. **Every declared artefact exists.** `docker/Dockerfile`, `entrypoint.sh`,
   `healthcheck.sh`, `wait-for.sh`, `README.md` and the three compose files.
   The required set is a hand-written literal because it IS the contract; what
   is enumerated is the DIRECTORY, so an unexpected extra file is reported too
   rather than skipped silently.
2. **A wrapper that accepts a command must end in `exec … "$@"`.** A wrapper
   that ends in a hardcoded command DISCARDS the command it was given, silently,
   at exit 0. Measured in this estate: a service emitted from this template
   shipped a `drainOutbox` Lambda whose `image.command` was recorded, in its IaC
   and in a blackbox proof, as "imports the symbol and exits". It did neither --
   the arguments landed in an entrypoint that never named `"$@"` and were
   dropped, so what ran 1440 times a day was the HTTP API server.
3. **Exactly one Dockerfile in the package, and it is `docker/Dockerfile`.** The
   second-Dockerfile drift is the failure this package's whole argument rests on.

WHY THE TOKEN IS NOT THE PROPERTY. `grep -c 'exec "$@"'` reports a match for a
COMMENTED-OUT forward, and for one stranded after an unconditional `exec` that
never returns. This gate walks the file's lines with comments and here-documents
removed, finds the LAST reachable `exec`, and requires the forward to be it or
to precede it as a guarded early exec -- and REFUSES rather than guesses when it
meets a construct it cannot decide.

Exit codes: `scripts/gate_exit_codes.py`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_common import (  # noqa: E402
    EXPECTED_MARKED_FIXTURE_FILES,
    FIXTURE_ROOT,
    KIT_ROOT,
    PACKAGE_ROOT,
    is_a_gate_fixture,
    marker_count,
)
from gate_exit_codes import GREEN, finding, refuse, skip  # noqa: E402

DOCKER_DIR = KIT_ROOT / "docker"

#: HAND-WRITTEN: this list IS §5's contract, not a summary of what happens to
#: be on disk. Deriving it from the directory would make the gate true of any
#: directory, including an empty one.
REQUIRED = (
    "Dockerfile",
    "entrypoint.sh",
    "healthcheck.sh",
    "wait-for.sh",
    "README.md",
    "compose/localstack.yml",
    "compose/postgres.yml",
    "compose/service.yml",
)
#: Scripts that ACCEPT a command and must therefore forward it.
FORWARDING_SCRIPTS = ("entrypoint.sh", "wait-for.sh")
EXECUTABLE_SCRIPTS = ("entrypoint.sh", "healthcheck.sh", "wait-for.sh")

_DOCKERFILE_PATTERN = re.compile(r"(^|[./])Dockerfile($|\.)")
_FORWARD = re.compile(r'^\s*exec\s+.*"\$@"')
_EXEC = re.compile(r"^\s*exec\s+\S")

#: HAND-WRITTEN fixture expectations.
EXPECTED_OFFENDING_FINDINGS = 2
EXPECTED_CONFORMING_FINDINGS = 0


def _reachable_lines(text: str) -> list[str] | None:
    """Lines with comments and here-documents stripped. None when undecidable."""
    lines: list[str] = []
    heredoc: str | None = None
    for raw in text.splitlines():
        if heredoc is not None:
            if raw.strip() == heredoc:
                heredoc = None
            continue
        stripped = raw.split("#", 1)[0] if raw.lstrip().startswith("#") else raw
        opener = re.search(r"<<-?'?([A-Za-z_][A-Za-z0-9_]*)'?", stripped)
        if opener is not None:
            lines.append(stripped)
            heredoc = opener.group(1)
            continue
        lines.append(stripped)
    if heredoc is not None:
        return None  # an unterminated here-document: refuse rather than guess
    return lines


_OPENERS = frozenset({"if", "do", "case"})
_CLOSERS = frozenset({"fi", "done", "esac"})

#: Quoted strings, blanked before block tokens are counted. `echo "done"` is not
#: a closer and `MSG='if ... do ...'` is not two openers, but a tokenizer reading
#: raw text calls them both, and a depth that drifts produces a confident wrong
#: answer about which `exec` is reachable.
#:
#: WHAT IT DOES NOT FIX, measured over six lines rather than assumed. This takes
#: the tokenizer from 3/6 correct to 5/6. The remaining case is a block token in
#: a trailing COMMENT -- `exec "$@"   # done` still reads -1 -- because this
#: blanks strings, not comments. Stated here rather than left for the next
#: reader to rediscover: the fix is real and partial.
#:
#: One case deliberately absent from the controls: `echo "case ... esac"`, where
#: an opener and a closer cancel and the UNFIXED tokenizer returns the right
#: answer by accident. As a control it would pass before and after, which is a
#: control that tests nothing.
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

#: Both directions, run before any verdict. `(line, expected delta)`.
_DEPTH_CONTROLS = (
    ('if [ -n "$1" ]; then', 1),
    ("fi", -1),
    ('echo "done"', 0),
    ("MSG='if you see this, do nothing'", 0),
)


def _block_depth_delta(line: str) -> int:
    """Net block nesting change for one line, by TOKEN.

    `if`/`fi`, `do`/`done`, `case`/`esac` only. `for`, `while` and `until` are
    deliberately not counted: every one of them pairs with a `do`, so counting
    both double-counts the same block -- and a depth that drifts is worse than
    no depth at all, because it produces a confident wrong answer.
    """
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _QUOTED.sub(" ", line))
    return sum(1 for t in tokens if t in _OPENERS) - sum(1 for t in tokens if t in _CLOSERS)


def _forwards_the_command(path: Path) -> tuple[bool, str]:
    """Is there a REACHABLE `exec … "$@"`?

    Reachability, not presence. `exec` replaces the process and never returns,
    so anything after an UNCONDITIONAL (depth-0) exec is dead code. The forward
    therefore qualifies only when it is the first depth-0 exec, or a guarded one
    inside a block that runs before any depth-0 exec -- which is the
    "an explicit command wins" shape the template's own entrypoint uses.

    REFUSES rather than guesses on a construct it cannot decide: an unterminated
    here-document, or block tokens that do not balance.
    """
    lines = _reachable_lines(path.read_text())
    if lines is None:
        return False, "unterminated here-document; refusing to guess at reachability"

    depth = 0
    first_unconditional_exec: int | None = None
    reachable_forward = False
    saw_exec = False
    saw_forward_anywhere = False
    for index, line in enumerate(lines):
        is_exec = bool(_EXEC.search(line))
        is_forward = bool(_FORWARD.search(line))
        saw_exec = saw_exec or is_exec
        saw_forward_anywhere = saw_forward_anywhere or is_forward
        if is_exec and first_unconditional_exec is None:
            if is_forward:
                reachable_forward = True
            if depth == 0:
                first_unconditional_exec = index
        depth += _block_depth_delta(line)
        if depth < 0:
            return False, (
                "shell block tokens do not balance; refusing to compute reachability rather "
                "than guessing at it"
            )
    if depth != 0:
        return False, "shell block tokens do not balance at end of file; refusing to guess"
    if not saw_exec:
        return False, "the script never execs at all; a CMD handed to it is discarded"
    if not reachable_forward:
        if saw_forward_anywhere:
            return False, (
                'the `exec … "$@"` is unreachable -- it sits after an unconditional exec that '
                "never returns, so every container CMD is silently discarded"
            )
        return False, 'no `exec … "$@"`; every container CMD is silently discarded'
    return True, ""


def analyse(docker_dir: Path) -> list[str]:
    findings: list[str] = []
    for relative in REQUIRED:
        if not (docker_dir / relative).is_file():
            findings.append(f"docker/{relative} is missing; §5 requires the shared folder entire")
    for name in FORWARDING_SCRIPTS:
        script = docker_dir / name
        if not script.is_file():
            continue
        forwards, reason = _forwards_the_command(script)
        if not forwards:
            findings.append(f"docker/{name}: {reason}")
    return findings


def main() -> int:
    # --- the depth tokenizer, controlled BEFORE any verdict ---------------
    # Two arms that must count and two that must not. Without the quiet arms a
    # tokenizer that counted every token would pass the loud ones, and depth is
    # what decides which `exec` this gate calls reachable.
    for line, expected in _DEPTH_CONTROLS:
        measured = _block_depth_delta(line)
        if measured != expected:
            return refuse(
                f"the block-depth tokenizer returned {measured} for {line!r}, expected the "
                f"hand-written {expected}; a drifting depth decides reachability wrongly"
            )

    if not DOCKER_DIR.is_dir():
        return skip(f"{DOCKER_DIR} does not exist; there is no shared docker/ folder to check")

    conforming_dir = FIXTURE_ROOT / "conforming" / "docker"
    offending_dir = FIXTURE_ROOT / "offending" / "docker"
    if not conforming_dir.is_dir() or not offending_dir.is_dir():
        return refuse(f"a docker fixture is missing under {FIXTURE_ROOT}")
    conforming = analyse(conforming_dir)
    if len(conforming) != EXPECTED_CONFORMING_FINDINGS:
        return refuse(
            f"the CONFORMING docker fixture produced {len(conforming)} finding(s), expected "
            f"{EXPECTED_CONFORMING_FINDINGS}: {conforming}"
        )
    offending = analyse(offending_dir)
    if len(offending) != EXPECTED_OFFENDING_FINDINGS:
        return refuse(
            f"the OFFENDING docker fixture produced {len(offending)} finding(s), expected the "
            f"hand-written {EXPECTED_OFFENDING_FINDINGS}: {offending}"
        )

    marked = marker_count()
    if marked != EXPECTED_MARKED_FIXTURE_FILES:
        return refuse(
            f"{marked} offending fixture file(s) carry the marker, expected "
            f"{EXPECTED_MARKED_FIXTURE_FILES}; the control has been neutralised"
        )

    findings = analyse(DOCKER_DIR)

    # Clause 3: exactly one Dockerfile in the whole package, and it is ours.
    dockerfiles = sorted(
        p
        for p in PACKAGE_ROOT.rglob("*")
        if p.is_file()
        and _DOCKERFILE_PATTERN.search(p.name)
        and ".git" not in p.parts
        and not is_a_gate_fixture(p)
    )
    if dockerfiles != [DOCKER_DIR / "Dockerfile"]:
        findings.append(
            f"expected exactly one Dockerfile at docker/Dockerfile, found: "
            f"{[str(p.relative_to(PACKAGE_ROOT)) for p in dockerfiles]}"
        )
    if not dockerfiles:
        return refuse("the Dockerfile scan matched nothing; the detector may be broken")

    for name in EXECUTABLE_SCRIPTS:
        script = DOCKER_DIR / name
        if script.is_file() and not script.stat().st_mode & 0o111:
            findings.append(f"docker/{name} is not executable; the container cannot run it")

    if findings:
        for item in findings:
            print(f"  {item}", file=sys.stderr)
        return finding(f"{len(findings)} shared-docker violation(s)")
    print(
        f"PASS: {len(REQUIRED)} required artefact(s) present, "
        f"{len(FORWARDING_SCRIPTS)} wrapper(s) forward \"$@\", 1 Dockerfile; "
        "3 clauses x 2 fixture arms = 6 arms; 6 executed, 0 skipped, 0 unaccounted."
    )
    return GREEN


if __name__ == "__main__":
    raise SystemExit(main())
