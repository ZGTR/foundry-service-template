#!/usr/bin/env python3
"""Strip the SQS point-to-point publish path from a `comms-*` emission.

comms-02 rule 1: a producer holds only `events:PutEvents` on its own bus --
no queue URL and no consumer endpoint in code, config or IaC. The template's
default flavour (`SqsPublisher`, `EVENT_BUS_MODE=sqs`, the outbox relay's
downstream `aws_sqs_queue`) is exactly the point-to-point coupling that rule
forbids, so a service emitted under the `comms-` prefix must not carry it
(an earlier change; found by comms-e2e's check_producer_consumer_separation.py,
26 clause-1 findings, all template-inherited).

DECLARED IN ONE PLACE rather than scattered `sed`s inside `new_service.sh`:
`DELETE_FILES` and `MARKED_FILES` below are the whole manifest a reviewer
needs to know what a comms flavour removes. `new_service.sh` calls this
script once, after the copy/rename/rewrite pass, only for a `comms-*` name.

MARKER MECHANISM, generic across Python/YAML/HCL because all three use `#`
comments. A single line tagged with a trailing `# COMMS-FLAVOUR-STRIP-LINE`
comment is deleted whole; a `# COMMS-FLAVOUR-STRIP:BEGIN` / `:END` pair
deletes every line between them, both markers included. The source files
carry the markers (see e.g. `composition/wiring.py`'s sqs branch or
`deploy/iac/resources/main.tf`'s outbox queue resources) -- this script only
knows how to read them, so the manifest and the markers cannot drift apart
silently: `_strip_markers` refuses a file that declares no marker at all.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BEGIN = "COMMS-FLAVOUR-STRIP:BEGIN"
END = "COMMS-FLAVOUR-STRIP:END"
LINE_MARKER = "COMMS-FLAVOUR-STRIP-LINE"

#: Deleted outright. `{module}` is the emitted package's rewritten module name
#: (new_service.sh's $NEW_MODULE, already renamed by the time this runs).
DELETE_FILES = (
    "src/{module}/adapters/real/sqs_event_bus.py",
    "tests/unit/test_sqs_event_bus_import_guard.py",
)

#: Carry COMMS-FLAVOUR-STRIP markers. Every entry here MUST contain at least
#: one marker, or the manifest and the source have drifted -- see the refusal
#: in `_apply_to_file` below.
MARKED_FILES = (
    "src/{module}/adapters/real/__init__.py",
    "src/{module}/composition/config.py",
    "src/{module}/composition/wiring.py",
    "tests/integration/test_sqs_publisher_real_endpoint.py",
    "tests/unit/test_container.py",
    "tests/unit/test_config.py",
    "deploy/iac/serverless.yml",
    "deploy/iac/app/main.tf",
    "deploy/iac/app/variables.tf",
    "deploy/iac/app/tests/estate_caller_sg.tftest.hcl",
    "deploy/iac/resources/main.tf",
    "deploy/iac/resources/outputs.tf",
    ".github/workflows/ci.yml",
)


#: The flavour this script applies, as a service declares it in its own
#: pyproject.toml. `service-kit/sync.py` and `gates/check_kit_drift.py` read the
#: declaration to pick the placement digest the manifest recorded for it
#:: a comms emission's ci.yml has these markers' blocks
#: removed, so the template's unflavoured digest can never match it.
FLAVOUR = "comms"
FLAVOUR_TABLE = "[tool.foundry.kit]"


def _strip_markers(text: str, *, source: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_block = False
    block_opened_at = -1
    marker_seen = False
    for lineno, line in enumerate(lines, start=1):
        if BEGIN in line:
            if in_block:
                raise SystemExit(f"{source}:{lineno}: nested {BEGIN}")
            in_block = True
            block_opened_at = lineno
            marker_seen = True
            continue
        if END in line:
            if not in_block:
                raise SystemExit(f"{source}:{lineno}: {END} with no matching {BEGIN}")
            in_block = False
            continue
        if in_block:
            continue
        if LINE_MARKER in line:
            marker_seen = True
            continue
        out.append(line)
    if in_block:
        raise SystemExit(f"{source}:{block_opened_at}: {BEGIN} with no matching {END}")
    if not marker_seen:
        raise SystemExit(
            f"comms flavour: {source} carries no {LINE_MARKER}/{BEGIN} marker -- "
            "the manifest (MARKED_FILES) and the source have drifted"
        )
    return "".join(out)


def strip_markers(text: str, *, source: str) -> str:
    """The public name of the one strip. `service-kit/sync.py` calls it on a
    placement, both when it records the manifest in the template and when it
    writes the placement into a comms service, so the bytes a comms service holds
    and the digest its manifest records come from the same function."""
    return _strip_markers(text, source=source)


def declare_flavour(dest: Path) -> None:
    """Record the flavour in the emitted service's pyproject.toml."""
    pyproject = dest / "pyproject.toml"
    if not pyproject.is_file():
        raise SystemExit("comms flavour: the emission has no pyproject.toml to declare it in")
    text = pyproject.read_text()
    if FLAVOUR_TABLE in text:
        raise SystemExit(f"comms flavour: pyproject.toml already carries {FLAVOUR_TABLE}")
    pyproject.write_text(
        text.rstrip("\n")
        + f"\n\n{FLAVOUR_TABLE}\n"
        + "# Written by scripts/apply_comms_flavour.py at emission. check_kit_drift and\n"
        + "# sync.py read it to use the comms placement digest.\n"
        + f'flavour = "{FLAVOUR}"\n'
    )


def apply(dest: Path, module: str) -> None:
    removed_files = 0
    for template_rel in DELETE_FILES:
        rel = template_rel.format(module=module)
        target = dest / rel
        if not target.is_file():
            raise SystemExit(f"comms flavour: expected file to delete is missing: {rel}")
        target.unlink()
        removed_files += 1

    stripped_files = 0
    for template_rel in MARKED_FILES:
        rel = template_rel.format(module=module)
        target = dest / rel
        if not target.is_file():
            raise SystemExit(f"comms flavour: expected marked file is missing: {rel}")
        before = target.read_text()
        after = _strip_markers(before, source=rel)
        target.write_text(after)
        stripped_files += 1

    declare_flavour(dest)

    print(
        f"new_service: comms flavour removed {removed_files} file(s), "
        f"stripped {stripped_files} marked file(s) -- no SQS point-to-point publish path"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--module", required=True)
    args = parser.parse_args(argv)
    apply(args.dest, args.module)
    return 0


if __name__ == "__main__":
    sys.exit(main())
