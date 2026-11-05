"""`scripts/vendor_package.py` writes vendored bytes and their VENDOR.json record together.

Template commits af17d42 and 349b35f re-copied
`service-kit/vendor/foundry_contracts` by hand and left `content_sha256` and
`monorepo_revision` behind, so `check_vendor_digests.py` was red on the template and
every synced service for four days. Every arm here builds a throwaway monorepo that is
a real git repository, because the writer reads bytes from git at a named commit and
never from a working tree -- a fixture that is a plain directory would test a writer
this script is not.

The READER in every arm is the kit's own `check_vendor_digests.py`, run against the
tree the writer produced, so the two cannot drift apart about what "reproduces" means.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[2]
WRITER = TEMPLATE / "scripts" / "vendor_package.py"
GATE = TEMPLATE / "service-kit" / "gates" / "check_vendor_digests.py"
SOURCE = "platform/packages/foundry-widgets"
PKG = "foundry_widgets"
# The kit gate refuses a population below two packages, so the fixture carries a
# second one that no arm touches -- it is the population floor, not a subject.
OTHER_SOURCE = "platform/packages/foundry-gadgets"
OTHER = "foundry_gadgets"


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(cwd), *args],
        capture_output=True, text=True, check=False,
    )
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout.strip()


def _write_pkg(mono: Path, body: str, source: str = SOURCE, package: str = PKG) -> str:
    pkg = mono / source / "src" / package
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(body)
    _git(mono, "add", "-A")
    _git(mono, "commit", "-q", "-m", body.strip())
    return _git(mono, "rev-parse", "HEAD")


def _vendor(
    into: Path, mono: Path, *extra: str, package: str = PKG
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WRITER), package,
         "--monorepo", str(mono), "--into", str(into), *extra],
        capture_output=True, text=True, check=False,
    )


def _gate(into: Path) -> subprocess.CompletedProcess[str]:
    """Run the fixture's OWN copy of the gate.

    The gate resolves the tree it judges from its own file location, so running the
    template's copy with `cwd=into` judges the TEMPLATE's VENDOR.json and every arm
    reads green whatever the writer did. Measured: that is how this helper was first
    written, and only the founding-shape control below went red. The package count is
    asserted too, so a gate that digested some other manifest cannot pass.
    """
    done = subprocess.run([sys.executable, str(into / "service-kit" / "gates" / GATE.name)],
                          cwd=into, capture_output=True, text=True, check=False)
    if done.returncode == 0:
        assert "2/2 declared packages digested" in done.stdout, done.stdout
    return done


def _record(into: Path) -> dict:
    manifest = into / "service-kit" / "vendor" / "VENDOR.json"
    return json.loads(manifest.read_text())["packages"][PKG]


@pytest.fixture
def estate(tmp_path: Path):
    """A monorepo at commit v1, and a service that vendored v1 through the writer."""
    mono = tmp_path / "mono"
    mono.mkdir()
    _git(mono, "init", "-q")
    _write_pkg(mono, "GADGET = 1\n", OTHER_SOURCE, OTHER)
    v1 = _write_pkg(mono, "VERSION = 1\n")
    into = tmp_path / "svc"
    shutil.copytree(TEMPLATE / "service-kit" / "gates", into / "service-kit" / "gates",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (into / "service-kit" / "vendor").mkdir(parents=True)
    manifest = into / "service-kit" / "vendor" / "VENDOR.json"
    manifest.write_text(json.dumps({"packages": {}}) + "\n")
    for source, package in ((SOURCE, PKG), (OTHER_SOURCE, OTHER)):
        first = _vendor(into, mono, "--source-package", source, package=package)
        assert first.returncode == 0, first.stderr
    return mono, into, v1


def test_the_first_vendoring_is_green_under_the_kit_gate(estate) -> None:
    _, into, v1 = estate
    gate = _gate(into)
    assert gate.returncode == 0, gate.stdout + gate.stderr
    assert _record(into)["monorepo_revision"] == v1


def test_re_vendoring_at_the_recorded_revision_is_byte_identical(estate) -> None:
    mono, into, v1 = estate
    manifest = into / "service-kit" / "vendor" / "VENDOR.json"
    before = manifest.read_bytes()
    again = _vendor(into, mono, "--revision", v1)
    assert again.returncode == 0, again.stderr
    assert manifest.read_bytes() == before


def test_a_later_revision_with_the_same_bytes_keeps_the_record(estate) -> None:
    """The monorepo moved, but not this package: VENDOR.json is not rewritten, so a
    service emitted at a later HEAD carries the template's record byte for byte."""
    mono, into, v1 = estate
    manifest = into / "service-kit" / "vendor" / "VENDOR.json"
    before = manifest.read_bytes()
    v2 = _write_pkg(mono, "GADGET = 2\n", OTHER_SOURCE, OTHER)
    again = _vendor(into, mono)
    assert again.returncode == 0, again.stderr
    assert "record kept" in again.stdout, again.stdout
    assert v2 != v1 and manifest.read_bytes() == before
    assert _gate(into).returncode == 0


def test_a_record_naming_a_revision_without_these_bytes_is_rewritten(estate) -> None:
    """The negative control for the kept record: equal digests beside a revision that does
    NOT hold those bytes (a hand edit) must be replaced by the revision vendored from."""
    mono, into, v1 = estate
    v2 = _write_pkg(mono, "VERSION = 2\n")
    assert _vendor(into, mono).returncode == 0
    manifest = into / "service-kit" / "vendor" / "VENDOR.json"
    data = json.loads(manifest.read_text())
    data["packages"][PKG]["monorepo_revision"] = v1
    manifest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    again = _vendor(into, mono)
    assert again.returncode == 0, again.stderr
    assert "record kept" not in again.stdout, again.stdout
    assert _record(into)["monorepo_revision"] == v2 != v1


def test_a_record_whose_data_file_differs_is_rewritten(estate) -> None:
    """The digest covers `.py` files only. A revision whose Python is identical but whose
    DATA differs does not hold these bytes, so naming it would be false provenance."""
    mono, into, _ = estate
    data = mono / SOURCE / "src" / PKG / "schema.json"
    data.write_text('{"v": 1}\n')
    _git(mono, "add", "-A")
    _git(mono, "commit", "-q", "-m", "schema v1")
    assert _vendor(into, mono).returncode == 0
    data.write_text('{"v": 2}\n')
    _git(mono, "add", "-A")
    _git(mono, "commit", "-q", "-m", "schema v2")
    v2 = _git(mono, "rev-parse", "HEAD")
    again = _vendor(into, mono)
    assert again.returncode == 0, again.stderr
    assert "record kept" not in again.stdout, again.stdout
    assert _record(into)["monorepo_revision"] == v2
    assert (into / "service-kit/vendor" / PKG / "schema.json").read_text() == '{"v": 2}\n'


def test_a_record_naming_a_mutable_ref_is_rewritten(estate) -> None:
    """`HEAD` holds these bytes today and other bytes tomorrow; only a commit id is a
    record, so a mutable ref is replaced even when the bytes match."""
    mono, into, _ = estate
    manifest = into / "service-kit" / "vendor" / "VENDOR.json"
    data = json.loads(manifest.read_text())
    data["packages"][PKG]["monorepo_revision"] = "HEAD"
    manifest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    v2 = _write_pkg(mono, "GADGET = 2\n", OTHER_SOURCE, OTHER)
    again = _vendor(into, mono)
    assert again.returncode == 0, again.stderr
    assert "record kept" not in again.stdout, again.stdout
    assert _record(into)["monorepo_revision"] == v2


def test_an_upstream_move_updates_bytes_and_record_together(estate) -> None:
    mono, into, v1 = estate
    v2 = _write_pkg(mono, "VERSION = 2\n")
    moved = _vendor(into, mono)
    assert moved.returncode == 0, moved.stderr
    assert "VERSION = 2" in (into / "service-kit/vendor" / PKG / "__init__.py").read_text()
    assert _record(into)["monorepo_revision"] == v2 != v1
    gate = _gate(into)
    assert gate.returncode == 0, gate.stdout + gate.stderr


def test_the_founding_shape_is_what_the_gate_catches(estate) -> None:
    """Positive control for every green arm above: bytes moved by hand, record left behind."""
    _, into, _ = estate
    (into / "service-kit/vendor" / PKG / "__init__.py").write_text("VERSION = 'hand copied'\n")
    gate = _gate(into)
    assert gate.returncode != 0, "the kit gate cannot see a hand copy; every green arm is vacuous"


def test_an_in_place_fork_is_refused_and_nothing_is_written(estate) -> None:
    mono, into, _ = estate
    _write_pkg(mono, "VERSION = 2\n")
    forked = into / "service-kit/vendor" / PKG / "__init__.py"
    forked.write_text("VERSION = 'local fork'\n")
    manifest = into / "service-kit" / "vendor" / "VENDOR.json"
    before = manifest.read_bytes()
    refused = _vendor(into, mono)
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "patched in place" in refused.stderr
    assert forked.read_text() == "VERSION = 'local fork'\n"
    assert manifest.read_bytes() == before


def test_discard_local_changes_throws_the_fork_away_deliberately(estate) -> None:
    mono, into, _ = estate
    v2 = _write_pkg(mono, "VERSION = 2\n")
    (into / "service-kit/vendor" / PKG / "__init__.py").write_text("VERSION = 'local fork'\n")
    done = _vendor(into, mono, "--discard-local-changes")
    assert done.returncode == 0, done.stderr
    assert _record(into)["monorepo_revision"] == v2
    assert _gate(into).returncode == 0


def test_an_uncommitted_monorepo_edit_is_not_vendored(estate) -> None:
    mono, into, v1 = estate
    (mono / SOURCE / "src" / PKG / "__init__.py").write_text("VERSION = 'draft'\n")
    done = _vendor(into, mono)
    assert done.returncode == 0, done.stderr
    assert "draft" not in (into / "service-kit/vendor" / PKG / "__init__.py").read_text()
    assert _record(into)["monorepo_revision"] == v1


def test_a_revision_that_is_not_a_commit_is_refused(estate) -> None:
    mono, into, _ = estate
    refused = _vendor(into, mono, "--revision", "no-such-ref")
    assert refused.returncode == 2
    assert "is not a commit" in refused.stderr


def test_an_undeclared_package_is_refused_without_a_source(estate) -> None:
    mono, into, _ = estate
    refused = _vendor(into, mono, package="foundry_typo")
    assert refused.returncode == 2
    assert "not declared in VENDOR.json" in refused.stderr
