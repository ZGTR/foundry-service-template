#!/usr/bin/env python3
"""Re-vendor one platform package: bytes and VENDOR.json record in ONE step.

WHY. Template commits af17d42 and 349b35f re-copied
`service-kit/vendor/foundry_contracts` by hand and left VENDOR.json's
`content_sha256` and `monorepo_revision` behind. `check_vendor_digests.py` was red
on the template and on every synced service for four days. The reader existed; the
WRITER was two hand steps, and a commit could carry one without the other.

So there is one writer, and it takes both halves from the same source:

  * the BYTES come from git at a NAMED monorepo revision (`git archive`), never from
    a working tree, so the recorded `monorepo_revision` is true of them by
    construction -- an uncommitted edit in the monorepo cannot be vendored under a
    commit that does not contain it;
  * the DIGEST is computed by `check_vendor_digests.digest()`, the one statement of
    the algorithm, over the bytes just written.

WHAT IT REFUSES, and why that is the design rather than caution. There is no mode
that records whatever is on disk. If the vendored copy no longer matches its own
recorded digest, somebody patched it in place -- a fork, possibly deliberate (see
an earlier change's DEFAULT_OUTPUT_CONTRACTS). Overwriting it would silently delete that
fork, and re-recording it would launder it. So the script refuses (exit 2) and
names the package; `--discard-local-changes` is the explicit, reviewable way to
throw the fork away. Recording a fork as upstream is not offered at all.

A package VENDOR.json does not already declare is refused unless
`--source-package` names its monorepo path: adding a vendored dependency is a
reviewed decision, not a side effect of a typo in the package name.

    python3 scripts/vendor_package.py foundry_contracts \
        --monorepo <path to the source monorepo>
    python3 scripts/vendor_package.py foundry_contracts \
        --monorepo <root> --revision <sha> --into <service root>

Exit codes: 0 vendored (or already current), 2 refusal. Nothing is written on a
refusal.
"""

from __future__ import annotations

import argparse
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile

TEMPLATE_ROOT = pathlib.Path(__file__).resolve().parent.parent
GATES = TEMPLATE_ROOT / "service-kit" / "gates"
sys.path.insert(0, str(GATES))
from check_vendor_digests import digest  # noqa: E402 -- the ONE algorithm, by path


class Refusal(RuntimeError):
    """The requested vendoring cannot be done truthfully; nothing was written."""


def _git(monorepo: pathlib.Path, *args: str, binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(monorepo), *args], capture_output=True,
                          text=not binary, check=False)


def resolve_revision(monorepo: pathlib.Path, revision: str) -> str:
    done = _git(monorepo, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}")
    if done.returncode != 0 or not done.stdout.strip():
        raise Refusal(f"{revision!r} is not a commit in {monorepo}; there is no named "
                      "revision to vendor from, so no record could be true.")
    return done.stdout.strip()


def extract(monorepo: pathlib.Path, revision: str, source_dir: str, into: pathlib.Path) -> int:
    """Write the package's bytes at `revision` into `into`; return the file count."""
    done = _git(monorepo, "archive", "--format=tar", revision, "--", source_dir, binary=True)
    if done.returncode != 0:
        raise Refusal(f"git archive found no {source_dir} at {revision[:12]}: "
                      f"{done.stderr.decode(errors='replace').strip()}")
    prefix = source_dir.rstrip("/") + "/"
    count = 0
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
        for member in tar.getmembers():
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            relative = member.name[len(prefix):]
            if "__pycache__" in relative.split("/"):
                continue
            target = into / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            assert source is not None  # isfile() above
            target.write_bytes(source.read())
            count += 1
    if count == 0:
        raise Refusal(f"{source_dir} at {revision[:12]} holds no files; refusing to "
                      "vendor an empty package.")
    return count


def _files(root: pathlib.Path) -> dict[str, bytes]:
    """Every file under `root` by relative path, `__pycache__` aside: the complete set,
    data files included, which a `.py` digest does not cover."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.relative_to(root).parts
    }


def _is_commit_id(revision: object) -> bool:
    return (isinstance(revision, str) and len(revision) == 40
            and all(c in "0123456789abcdef" for c in revision))


def _files_at(monorepo: pathlib.Path, revision: str, source_dir: str) -> dict[str, bytes] | None:
    """The complete file set of `source_dir` at `revision`, or None when it cannot be
    read there."""
    if _git(monorepo, "cat-file", "-e", f"{revision}^{{commit}}").returncode != 0:
        return None
    with tempfile.TemporaryDirectory() as scratch:
        staged = pathlib.Path(scratch) / "recorded"
        staged.mkdir()
        try:
            extract(monorepo, revision, source_dir, staged)
        except Refusal:
            return None
        return _files(staged)


def vendor(package: str, monorepo: pathlib.Path, revision: str, into: pathlib.Path,
           source_package: str | None, discard_local_changes: bool) -> str:
    vendor_dir = into / "service-kit" / "vendor"
    manifest_path = vendor_dir / "VENDOR.json"
    if not manifest_path.is_file():
        raise Refusal(f"{manifest_path} is missing; there is no declaration to update.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    packages = manifest["packages"]
    entry = packages.get(package)
    if entry is None:
        if not source_package:
            raise Refusal(f"{package} is not declared in VENDOR.json. Adding a vendored "
                          "package is a reviewed decision: pass --source-package "
                          "<monorepo path> to declare it.")
        entry = {"source_package": source_package}
    source_package = source_package or entry["source_package"]

    destination = vendor_dir / package
    recorded = entry.get("content_sha256")
    if destination.is_dir() and recorded and not discard_local_changes:
        current, _ = digest(destination)
        if current != recorded:
            raise Refusal(
                f"{package}: the vendored copy does not reproduce its recorded digest "
                f"({current[:12]} vs {recorded[:12]}), so it was patched in place. "
                "Re-vendoring would silently delete that fork, and recording it would "
                "launder it as upstream. Upstream the change to the monorepo, or pass "
                "--discard-local-changes to throw it away deliberately."
            )

    sha = resolve_revision(monorepo, revision)
    source_dir = f"{source_package}/src/{package}"
    with tempfile.TemporaryDirectory() as scratch:
        staged = pathlib.Path(scratch) / package
        staged.mkdir()
        extract(monorepo, sha, source_dir, staged)
        new_digest, n_py = digest(staged)
        if n_py == 0:
            raise Refusal(f"{source_dir} at {sha[:12]} has no .py files to digest.")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(staged, destination)

    # The postcondition, re-measured over the bytes where they now LIVE rather than
    # where they were staged -- the same function the gate will call.
    final_digest, _ = digest(destination)
    if final_digest != new_digest:
        raise Refusal(f"{package}: digest moved between staging and destination "
                      f"({new_digest[:12]} -> {final_digest[:12]}).")
    # UNCHANGED BYTES KEEP THEIR RECORD. The recorded revision is still a true statement
    # of where these bytes came from when it holds the same digest, so moving it to HEAD
    # only rewrites VENDOR.json. `scripts/new_service.sh` re-vendors on every emission in
    # monorepo mode, and that rewrite left each new service failing its own kit drift gate
    # on arrival (an earlier change: once the template was a directory of the
    # monorepo, every checkout, CI's included, emits in that mode).
    # The record is kept only when it is still TRUE: the same source path, a recorded
    # revision that is an immutable commit id (a branch or `HEAD` names different bytes
    # tomorrow), and that revision, extracted afresh, holds exactly the COMPLETE file set
    # now installed -- data files included, since the digest covers `.py` files only. A
    # digest equal to the record proves nothing about the revision beside it: a record
    # edited by hand to name an older commit would otherwise be laundered as provenance.
    kept = entry.get("monorepo_revision")
    if (recorded == final_digest and _is_commit_id(kept)
            and entry.get("source_package") == source_package
            and _files_at(monorepo, kept, source_dir) == _files(destination)):
        return (f"{package}: {n_py} .py file(s) at {sha[:12]} are the bytes recorded at "
                f"{kept[:12]}, content_sha256 {final_digest[:12]}; record kept")
    entry.update({
        "content_sha256": final_digest,
        "monorepo_revision": sha,
        "source_package": source_package,
        "source_present_at_vendor_time": True,
    })
    packages[package] = entry
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True,
                                        ensure_ascii=False) + "\n", encoding="utf-8")
    return f"{package}: {n_py} .py file(s) at {sha[:12]}, content_sha256 {final_digest[:12]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("package", help="import name, e.g. foundry_contracts")
    parser.add_argument("--monorepo", required=True, type=pathlib.Path,
                        help="a checkout of the Foundry monorepo")
    parser.add_argument("--revision", default="HEAD",
                        help="monorepo commit to vendor from (default HEAD)")
    parser.add_argument("--into", type=pathlib.Path, default=TEMPLATE_ROOT,
                        help="repository root holding service-kit/vendor (default: this template)")
    parser.add_argument("--source-package",
                        help="monorepo path of a package VENDOR.json does not yet declare")
    parser.add_argument("--discard-local-changes", action="store_true",
                        help="overwrite a vendored copy that was patched in place")
    args = parser.parse_args(argv)
    try:
        line = vendor(args.package, args.monorepo.resolve(), args.revision,
                      args.into.resolve(), args.source_package, args.discard_local_changes)
    except Refusal as refusal:
        print(f"vendor-package: REFUSE {refusal}", file=sys.stderr)
        return 2
    print(f"vendor-package: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
