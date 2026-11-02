"""`service-kit/ci/openapi_breaking.sh` compares the base spec to this tree's, or says why not.

These arms run the real script inside a throwaway git repository with a stub
`oasdiff` first on PATH. The stub records its arguments and a copy of the base
spec it was handed, so "the base branch's committed spec was compared" is read
from state, and an arm that must never reach oasdiff proves it by an empty log.

What oasdiff itself decides (a removed path is an error; a path removed after its
`x-sunset` is not) is its own behaviour and is not claimed here: that was proved
with the real 1.33.0 binary when this step landed, and CI runs the real binary.
The `install` arm here is the refusal only -- a tarball whose sha256 is not the
pinned one never yields a binary -- because the accepting arm needs the network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "service-kit" / "ci" / "openapi_breaking.sh"
CI = ROOT / ".github" / "workflows" / "ci.yml"
SPEC = "contracts/openapi/v1.json"
#: The estate directory's default path, where a service kept its spec before the kit.
LEGACY_SPEC = "openapi/v1.json"
ZERO_SHA = "0" * 40

STUB_OASDIFF = """#!/usr/bin/env bash
if [ "$1" = "--version" ]; then echo "oasdiff version stub"; exit 0; fi
echo "oasdiff $1 $4 $5 new=$3" >> "$STUB_LOG"
cp "$2" "$STUB_BASE_COPY"
exit "${STUB_OASDIFF_EXIT:-0}"
"""


@dataclass
class Repo:
    root: Path
    bin_dir: Path
    log: Path
    base_copy: Path

    def git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.strip()

    def commit_spec(self, body: str, path: str = SPEC) -> str:
        spec = self.root / path
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(body)
        self.git("add", path)
        self.git("commit", "-q", "-m", "spec")
        return self.git("rev-parse", "HEAD")

    def move_spec(self, old: str, new: str) -> None:
        """Move the spec in the working tree only; the base commit keeps the old path."""
        target = self.root / new
        target.parent.mkdir(parents=True, exist_ok=True)
        (self.root / old).rename(target)

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        cleared = {k: v for k, v in os.environ.items() if k not in {"BASE_REF", "BEFORE_SHA"}}
        full_env = {
            **cleared,
            "PATH": f"{self.bin_dir}{os.pathsep}{os.environ['PATH']}",
            "STUB_LOG": str(self.log),
            "STUB_BASE_COPY": str(self.base_copy),
            **env,
        }
        return subprocess.run(
            ["bash", str(self.root / "service-kit" / "ci" / "openapi_breaking.sh"), *args],
            cwd=self.root,
            env=full_env,
            capture_output=True,
            text=True,
            check=False,
        )

    def oasdiff_calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.is_file() else []


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    root = tmp_path / "service"
    (root / "service-kit" / "ci").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "service-kit" / "ci" / "openapi_breaking.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "oasdiff"
    stub.write_text(STUB_OASDIFF)
    stub.chmod(0o755)
    made = Repo(root, bin_dir, tmp_path / "oasdiff.log", tmp_path / "base-copy.json")
    made.git("init", "-q")
    made.git("add", "service-kit")
    made.git("commit", "-q", "-m", "kit only")
    return made


@pytest.fixture
def nested(tmp_path: Path) -> Repo:
    """A service in a SUBDIRECTORY of a larger repository, as every absorbed service is.

    `git show <rev>:<path>` resolves <path> against the repository root, not against
    `git -C`, so a script that worked in a one-service repository found no spec here
    and skipped at exit 3 on every run.
    """
    estate = tmp_path / "estate"
    root = estate / "services" / "svc"
    (root / "service-kit" / "ci").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "service-kit" / "ci" / "openapi_breaking.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "oasdiff"
    stub.write_text(STUB_OASDIFF)
    stub.chmod(0o755)
    subprocess.run(["git", "init", "-q", str(estate)], check=True)
    made = Repo(root, bin_dir, tmp_path / "oasdiff.log", tmp_path / "base-copy.json")
    made.git("add", "service-kit")
    made.git("commit", "-q", "-m", "kit only")
    return made


def test_base_without_a_spec_is_a_named_skip(repo: Repo) -> None:
    no_spec = repo.git("rev-parse", "HEAD")
    repo.commit_spec('{"v": "head"}\n')

    done = repo.run("compare", no_spec)

    assert done.returncode == 3, done.stderr
    assert "SKIP" in done.stderr and SPEC in done.stderr
    assert repo.oasdiff_calls() == []


def test_compares_the_base_commits_spec_against_this_tree(repo: Repo) -> None:
    base = repo.commit_spec('{"v": "base"}\n')
    (repo.root / SPEC).write_text('{"v": "changed"}\n')

    done = repo.run("compare", base)

    assert done.returncode == 0, done.stderr
    assert repo.oasdiff_calls() == [f"oasdiff breaking --fail-on ERR new={repo.root / SPEC}"]
    assert repo.base_copy.read_text() == '{"v": "base"}\n'


def test_a_breaking_verdict_is_exit_one(repo: Repo) -> None:
    base = repo.commit_spec('{"v": "base"}\n')

    done = repo.run("compare", base, STUB_OASDIFF_EXIT="1")

    assert done.returncode == 1
    assert "BREAKING" in done.stderr
    # The removed-spec message carries the same word under the same exit code. Only a
    # verdict that came from oasdiff leaves a call in the log.
    assert len(repo.oasdiff_calls()) == 1


@pytest.mark.parametrize("path", [SPEC, LEGACY_SPEC])
def test_an_oasdiff_that_fails_is_exit_one_and_not_a_verdict(repo: Repo, path: str) -> None:
    """oasdiff exits above 1 when it cannot load a spec (102 for a dangling `$ref`). That is
    not "no breaking change", and it is not a breaking change either."""
    base = repo.commit_spec('{"v": "base"}\n', path)

    done = repo.run("compare", base, STUB_OASDIFF_EXIT="102")

    assert done.returncode == 1
    assert "oasdiff itself failed (exit 102) -- no verdict on the change" in done.stderr
    assert "BREAKING" not in done.stderr
    assert len(repo.oasdiff_calls()) == 1


def test_ci_base_is_the_pre_push_tip(repo: Repo) -> None:
    before = repo.commit_spec('{"v": "before"}\n')
    repo.commit_spec('{"v": "pushed"}\n')

    done = repo.run("compare", BEFORE_SHA=before)

    assert done.returncode == 0, done.stderr
    assert repo.base_copy.read_text() == '{"v": "before"}\n'


@pytest.mark.parametrize("env", [{}, {"BEFORE_SHA": ZERO_SHA}])
def test_no_base_at_all_is_a_named_skip(repo: Repo, env: dict[str, str]) -> None:
    repo.commit_spec('{"v": "head"}\n')

    done = repo.run("compare", **env)

    assert done.returncode == 3, done.stderr
    # The message, not only the code: a zero sha that fell through to the fetch
    # would also exit 3, as an unfetchable tip, after a network round trip.
    assert "no base to compare against" in done.stderr
    assert repo.oasdiff_calls() == []


def test_a_service_with_no_spec_on_either_side_is_a_named_skip(repo: Repo) -> None:
    """An existing service receives this step through a kit sync before it has a spec."""
    done = repo.run("compare", "HEAD")

    assert done.returncode == 3, done.stderr
    assert repo.oasdiff_calls() == []


def test_removing_the_committed_spec_is_breaking(repo: Repo) -> None:
    base = repo.commit_spec('{"v": "base"}\n')
    (repo.root / SPEC).unlink()

    done = repo.run("compare", base)

    assert done.returncode == 1
    assert "removed it" in done.stderr
    assert repo.oasdiff_calls() == []


def test_a_spec_at_the_directory_default_path_is_compared_not_skipped(repo: Repo) -> None:
    """Nine services keep `openapi/v1.json`. Reading one path only, the kit would skip each of
    them on every run once it synced this step. None had when this was written."""
    base = repo.commit_spec('{"v": "base"}\n', LEGACY_SPEC)
    (repo.root / LEGACY_SPEC).write_text('{"v": "changed"}\n')

    done = repo.run("compare", base)

    assert done.returncode == 0, done.stderr
    assert repo.oasdiff_calls() == [f"oasdiff breaking --fail-on ERR new={repo.root / LEGACY_SPEC}"]
    assert repo.base_copy.read_text() == '{"v": "base"}\n'


def test_a_breaking_verdict_at_the_directory_default_path_is_exit_one(repo: Repo) -> None:
    base = repo.commit_spec('{"v": "base"}\n', LEGACY_SPEC)

    done = repo.run("compare", base, STUB_OASDIFF_EXIT="1")

    assert done.returncode == 1
    assert "BREAKING" in done.stderr
    assert len(repo.oasdiff_calls()) == 1


@pytest.mark.parametrize(("old", "new"), [(LEGACY_SPEC, SPEC), (SPEC, LEGACY_SPEC)])
def test_a_spec_that_moved_is_compared_across_the_move(repo: Repo, old: str, new: str) -> None:
    """Adopting the kit moves the file. The move must not hide a break made with it."""
    base = repo.commit_spec('{"v": "base"}\n', old)
    repo.move_spec(old, new)
    (repo.root / new).write_text('{"v": "moved and changed"}\n')

    done = repo.run("compare", base)

    assert done.returncode == 0, done.stderr
    assert repo.oasdiff_calls() == [f"oasdiff breaking --fail-on ERR new={repo.root / new}"]
    assert repo.base_copy.read_text() == '{"v": "base"}\n'


def test_two_specs_in_this_tree_are_refused(repo: Repo) -> None:
    """One service publishes one contract. With two, which one callers read is a guess."""
    base = repo.commit_spec('{"v": "base"}\n', LEGACY_SPEC)
    (repo.root / SPEC).parent.mkdir(parents=True)
    (repo.root / SPEC).write_text('{"v": "second"}\n')

    done = repo.run("compare", base)

    assert done.returncode == 2, done.stderr
    assert f"both {SPEC} and {LEGACY_SPEC} exist in this tree" in done.stderr
    assert repo.oasdiff_calls() == []


def test_two_specs_in_the_base_are_refused(repo: Repo) -> None:
    repo.commit_spec('{"v": "one"}\n', LEGACY_SPEC)
    base = repo.commit_spec('{"v": "two"}\n', SPEC)
    (repo.root / LEGACY_SPEC).unlink()

    done = repo.run("compare", base)

    assert done.returncode == 2, done.stderr
    assert f"both {SPEC} and {LEGACY_SPEC} exist at {base}" in done.stderr
    assert repo.oasdiff_calls() == []


def test_removing_a_spec_at_the_directory_default_path_is_breaking(repo: Repo) -> None:
    base = repo.commit_spec('{"v": "base"}\n', LEGACY_SPEC)
    (repo.root / LEGACY_SPEC).unlink()

    done = repo.run("compare", base)

    assert done.returncode == 1
    # `openapi/v1.json` is a substring of the kit's own path, so the phrase is the check.
    assert f"commits {LEGACY_SPEC} and this tree removed it" in done.stderr
    assert repo.oasdiff_calls() == []


def test_the_skip_names_every_path_it_looked_for(repo: Repo) -> None:
    """A skip that names one path reads as 'no spec'; the reader must see both were tried."""
    done = repo.run("compare", "HEAD")

    assert done.returncode == 3, done.stderr
    assert f"looked for {SPEC}, {LEGACY_SPEC}" in done.stderr


def test_an_unresolvable_base_is_refused(repo: Repo) -> None:
    repo.commit_spec('{"v": "head"}\n')

    done = repo.run("compare", "no-such-revision")

    assert done.returncode == 2
    assert "does not resolve" in done.stderr


def test_install_refuses_a_tarball_that_is_not_the_pinned_one(repo: Repo, tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    fake = tmp_path / "oasdiff"
    fake.write_text("#!/bin/sh\necho not the real binary\n")
    for platform in ("darwin_all", "linux_amd64", "linux_arm64"):
        with tarfile.open(release / f"oasdiff_1.33.0_{platform}.tar.gz", "w:gz") as tar:
            tar.add(fake, arcname="oasdiff")
    target = tmp_path / "installed"

    done = repo.run("install", str(target), OASDIFF_RELEASE_URL=release.as_uri())

    assert done.returncode == 2, done.stderr
    assert "sha256 mismatch" in done.stderr
    assert not (target / "oasdiff").exists()


def test_the_workflow_installs_then_compares_and_tolerates_only_the_skip() -> None:
    run_lines = [ln.strip() for ln in CI.read_text().splitlines() if ln.strip().startswith("run:")]
    install = [i for i, ln in enumerate(run_lines) if "openapi_breaking.sh install" in ln]
    compare = [i for i, ln in enumerate(run_lines) if "openapi_breaking.sh compare" in ln]

    assert len(install) == 1 and len(compare) == 1, (install, compare)
    assert install[0] < compare[0]
    assert run_lines[compare[0]].endswith('openapi_breaking.sh compare || [ "$?" -eq 3 ]')


def test_a_service_in_a_subdirectory_compares_its_own_spec(nested: Repo) -> None:
    assert nested.git("rev-parse", "--show-prefix") == "services/svc/"
    base = nested.commit_spec('{"v": "base"}\n')
    (nested.root / SPEC).write_text('{"v": "changed"}\n')

    done = nested.run("compare", base)

    assert done.returncode == 0, done.stderr
    assert nested.oasdiff_calls() == [f"oasdiff breaking --fail-on ERR new={nested.root / SPEC}"]
    assert nested.base_copy.read_text() == '{"v": "base"}\n'


def test_a_service_in_a_subdirectory_that_removes_its_spec_is_breaking(nested: Repo) -> None:
    base = nested.commit_spec('{"v": "base"}\n')
    (nested.root / SPEC).unlink()

    done = nested.run("compare", base)

    assert done.returncode == 1, done.stderr
    assert "BREAKING" in done.stderr
