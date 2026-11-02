"""Guards against the exact failure mode `an older backend service` lived
with: a second Dockerfile (`Dockerfile.lambda`) that quietly diverged from
the first -- different base image, installed the UNPINNED requirements file
instead of the hash-checked lock, ran as root. Nothing detected that until
it was noticed by hand.

This package's whole argument (README.md's "Motivation" section) is that one
image serves both targets, so there is no second Dockerfile to drift in the
first place. These tests make that structural, not a convention someone has
to remember: exactly one Dockerfile must exist, and it must install its
third-party dependencies with `--require-hashes` against a lockfile where
every dependency actually carries a hash.
"""

from __future__ import annotations

import re
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parent.parent.parent
#: The Dockerfile moved to `docker/` when the shared containerisation folder
#: landed (contract §5). The check is UNCHANGED in strength -- still "exactly
#: one Dockerfile in this package, at a named path" -- only the path moved.
#: Naming it once here is what keeps that true: a test that looked for
#: "a Dockerfile anywhere" would pass with the root copy left behind, which is
#: the two-Dockerfile drift this file exists to rule out.
DOCKERFILE = PACKAGE_ROOT / "service-kit" / "docker" / "Dockerfile"
LOCKFILE = PACKAGE_ROOT / "requirements/service.lock"
#: The §9 gates' fixture tree. `check_docker_shared.py` needs a conforming AND an
#: offending `docker/` folder, and each holds a Dockerfile -- so a scan honest
#: enough to find the real one finds those too, and this test went red on a tree
#: nobody had changed the moment the fixtures were committed.
#: Excluding it is a strengthening, not a loophole, and the exclusion is asserted
#: in BOTH directions below: a fixture path must not re-enter the production
#: scan, and the fixture tree must still hold a Dockerfile, or the exclusion is
#: credited for an absence it did not cause.
GATE_FIXTURE_ROOT = PACKAGE_ROOT / "service-kit" / "gates" / "gate_fixtures"


_DOCKERFILE_PATTERN = re.compile(r"(^|[./])Dockerfile($|\.)")


def _dockerfiles(root: Path) -> list[Path]:
    """Every file under `root` that IS a Dockerfile or claims to be a variant
    of one -- `Dockerfile`, `Dockerfile.lambda`, `foo.Dockerfile`, etc.
    Deliberately broad: an earlier incident's second file was named
    `Dockerfile.lambda`, not `Dockerfile`, so a check that only looked for
    the exact name `Dockerfile` would have missed exactly the case it exists
    to catch.
    """
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and _DOCKERFILE_PATTERN.search(p.name)
        and ".git" not in p.parts
        and GATE_FIXTURE_ROOT not in p.parents
    )


def test_exactly_one_dockerfile_exists() -> None:
    dockerfiles = _dockerfiles(PACKAGE_ROOT)
    assert dockerfiles == [DOCKERFILE], (
        f"expected exactly one Dockerfile, at {DOCKERFILE}, found: {dockerfiles}. "
        "A second Dockerfile (e.g. a Lambda-specific one) is exactly the drift this "
        "template exists to rule out -- see README.md's Motivation section."
    )


def test_the_gate_fixture_exclusion_is_real_in_BOTH_directions() -> None:
    """An exclusion credited for an absence it did not cause is not an exclusion.

    Half one: no fixture path may re-enter the production scan. Half two: the
    fixture tree must STILL hold a Dockerfile -- if the fixtures are renamed away
    the exclusion above silently becomes a no-op, and the test that depends on it
    goes on reading as a working control.
    """
    production = _dockerfiles(PACKAGE_ROOT)
    leaked = [p for p in production if GATE_FIXTURE_ROOT in p.parents]
    assert leaked == [], f"gate fixtures leaked into the production scan: {leaked}"

    fixture_dockerfiles = sorted(
        p
        for p in GATE_FIXTURE_ROOT.rglob("*")
        if p.is_file() and _DOCKERFILE_PATTERN.search(p.name)
    )
    # HAND-WRITTEN literal: one conforming, one offending. Deriving it from the
    # tree would make the assertion true of an empty tree.
    assert len(fixture_dockerfiles) == 2, (
        f"expected 2 fixture Dockerfiles (conforming + offending), found "
        f"{[str(p.relative_to(PACKAGE_ROOT)) for p in fixture_dockerfiles]}. The exclusion "
        "above is excluding nothing."
    )


def test_check_goes_red_on_a_second_dockerfile(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "Dockerfile.lambda").write_text("FROM scratch\n")

    found = _dockerfiles(tmp_path)

    assert len(found) == 2, "the detector itself must be able to see a second Dockerfile"


def test_dockerfile_installs_with_require_hashes() -> None:
    dockerfile = DOCKERFILE.read_text()
    assert "--require-hashes" in dockerfile, (
        "Dockerfile must install third-party dependencies with --require-hashes -- "
        "an earlier Lambda image installed the unpinned requirements.txt instead "
        "of the hash-checked .lock, and nothing caught that two images had drifted."
    )
    assert "requirements.txt" not in dockerfile, (
        "the Dockerfile must reference only the hash-pinned .lock file, never an "
        "unpinned .txt/.in requirements file, as a second unhashed install path."
    )


def test_the_lockfile_the_dockerfile_installs_from_exists_and_is_referenced() -> None:
    dockerfile = DOCKERFILE.read_text()
    assert "requirements/service.lock" in dockerfile
    assert LOCKFILE.is_file(), f"{LOCKFILE} is referenced by Dockerfile but does not exist"


def _dependency_records(lock_text: str) -> list[str]:
    """Split a pip-compile --generate-hashes lockfile into one string per
    dependency (a top-level, non-indented, non-comment line plus every
    indented continuation line that follows it)."""
    records: list[str] = []
    current: list[str] = []
    for line in lock_text.splitlines():
        if line.startswith("#"):
            continue
        if line and not line[0].isspace():
            if current:
                records.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        records.append("\n".join(current))
    return records


def test_every_dependency_in_the_lockfile_is_hash_pinned() -> None:
    records = _dependency_records(LOCKFILE.read_text())
    assert records, "lockfile parsed to zero dependency records -- parser or fixture is broken"

    unpinned = [r.splitlines()[0] for r in records if "--hash=sha256:" not in r]
    assert unpinned == [], f"dependencies with no --hash=sha256: entry: {unpinned}"


def test_check_goes_red_on_an_unhashed_dependency() -> None:
    fake_lock = (
        "requests==2.31.0\n"
        "    # via -r requirements.in\n"
        "pydantic==2.7.0 \\\n"
        "    --hash=sha256:abc123\n"
    )

    records = _dependency_records(fake_lock)
    unpinned = [r.splitlines()[0] for r in records if "--hash=sha256:" not in r]

    assert unpinned == ["requests==2.31.0"]


def test_lockfile_coverage_check_still_catches_a_genuinely_absent_requirement() -> None:
    """The red direction, so stripping extras cannot quietly disarm the check."""
    lock_names = {
        r.splitlines()[0].split("==")[0] for r in _dependency_records(LOCKFILE.read_text())
    }
    fabricated = ["definitely-not-a-real-package[extra]", "another-absent-one"]
    missing = [name.split("[")[0] for name in fabricated if name.split("[")[0] not in lock_names]
    assert missing == [n.split("[")[0] for n in fabricated], (
        "the extras-stripping comparison must still report absent packages"
    )


def test_lockfile_covers_every_top_level_requirement() -> None:
    requirements_in = (PACKAGE_ROOT / "requirements/service.in").read_text()
    # Strip EXTRAS before comparing: `pyjwt[crypto]` locks as `pyjwt`, plus the
    # extra's own transitive pins. Without this the check reported `pyjwt[crypto]`
    # missing from a lockfile that pins it correctly -- a false red on a green
    # supply chain, which invites "fixing" the lockfile that was already right.
    top_level = [
        line.split(">=")[0].split("==")[0].split("[")[0].strip()
        for line in requirements_in.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    lock_names = {
        r.splitlines()[0].split("==")[0] for r in _dependency_records(LOCKFILE.read_text())
    }

    missing = [name for name in top_level if name not in lock_names]
    assert missing == [], f"top-level requirement(s) missing from the lockfile: {missing}"
