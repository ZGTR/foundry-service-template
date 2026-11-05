"""VENDOR.json's content_sha256 is recomputed here, so the field has a reader.

Before this gate the field had a written guarantee -- "answer
'did this copy change' without the monorepo" -- and nothing performed it: `git grep
-ln content_sha256` returned only VENDOR.json itself. A declaration with no consumer
is this estate's recurring defect class, and it is worse here than usual, because the
two recorded values were computed by TWO DIFFERENT ALGORITHMS under a note asserting
one of them:

    foundry_agent_trace  sha256 over the .py file BYTES concatenated in sorted
                          relative-path order
    foundry_contracts    sha256 over (relative path + bytes) per file, same order

Measured 2026-09-18. So a reader who implemented the algorithm the note stated -- the
obvious thing to do -- got a verifier RED on foundry_contracts from its first run,
against a correct unmodified vendored copy. A false red naming a real file, produced
by following the documentation exactly.

WHY path+bytes AND NOT concat-bytes, which is a detection property and not taste.
Concatenating bytes alone cannot see a RENAME, nor content moving between two files
whose combined bytes are unchanged: `a.py`+`b.py` and `b.py`+`a.py` hash identically
if their contents are swapped, and a vendored copy whose files have been reshuffled is
exactly the fork this field exists to catch. Including the relative path makes the
digest sensitive to the tree's SHAPE as well as its bytes. Adopting it also moved only
ONE recorded value, which is the tell that path+bytes was this file's original
convention and concat-bytes was the newer import.

WHAT THIS GATE DOES NOT CLAIM. It answers "have these bytes changed since they were
recorded", not "do they match upstream". The estate's check_vendor_drift.py compares
per-file digests against the source package in the monorepo and remains the real
anti-fork enforcement; this one is the offline half, usable in a service checkout that
has no monorepo beside it.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

from gate_exit_codes import GREEN, finding, refuse

VENDOR_DIR = pathlib.Path(__file__).resolve().parent.parent / "vendor"
MANIFEST = VENDOR_DIR / "VENDOR.json"

# A floor, so an emptied or mis-globbed population refuses instead of reporting a
# green it never measured. Two packages are vendored today; one would be a real
# regression worth stopping on, and the gate says so rather than passing vacuously.
MIN_PACKAGES = 2
MIN_FILES_PER_PACKAGE = 1


def digest(package_root: pathlib.Path) -> tuple[str, int]:
    """sha256 over (relative path + bytes) per .py file, sorted by relative path.

    This is THE statement of the algorithm. Anything that records a content_sha256
    must call this function rather than reimplement it -- five hand-maintained copies
    that agree is the shape this repository has already paid for elsewhere.
    """
    files = sorted(
        (p for p in package_root.rglob("*.py")
         if p.is_file() and "__pycache__" not in p.parts),
        key=lambda p: str(p.relative_to(package_root)),
    )
    h = hashlib.sha256()
    for path in files:
        h.update(str(path.relative_to(package_root)).encode())
        h.update(path.read_bytes())
    return h.hexdigest(), len(files)


def main() -> int:
    if not MANIFEST.is_file():
        return refuse(f"{MANIFEST} is missing; there is no declaration to check.")

    try:
        packages = json.loads(MANIFEST.read_text())["packages"]
    except (ValueError, KeyError) as exc:
        return refuse(f"{MANIFEST} does not parse as a vendor declaration: {exc}")

    if len(packages) < MIN_PACKAGES:
        return refuse(
            f"only {len(packages)} vendored package(s) declared, floor is {MIN_PACKAGES}; "
            "a shrunken population cannot distinguish a clean estate from a broken scan."
        )

    findings: list[str] = []
    checked = 0
    for name in sorted(packages):
        meta = packages[name]
        root = VENDOR_DIR / name
        if not root.is_dir():
            findings.append(
                f"{name}: declared in VENDOR.json but {root} is not a directory -- "
                "either the copy was deleted without updating the declaration, or the "
                "declaration names a package that was never vendored."
            )
            continue
        recorded = meta.get("content_sha256")
        if not recorded:
            findings.append(f"{name}: no content_sha256 recorded.")
            continue
        got, n_files = digest(root)
        if n_files < MIN_FILES_PER_PACKAGE:
            return refuse(
                f"{name}: {n_files} .py file(s) found under {root}; the scan read "
                "nothing, so its digest is not a measurement of the copy."
            )
        checked += 1
        if got != recorded:
            findings.append(
                f"{name}: content_sha256 does not reproduce over {n_files} .py file(s).\n"
                f"    recorded  {recorded}\n"
                f"    computed  {got}\n"
                "    Either the vendored copy was patched in place -- which is a fork "
                "nobody declared -- or it was re-copied without re-recording the digest."
            )

    if checked < MIN_PACKAGES:
        return refuse(
            f"only {checked} package(s) were actually digested against a floor of "
            f"{MIN_PACKAGES}; the verdict would cover less than the declaration."
        )

    if findings:
        return finding(
            "vendored copy does not match its recorded digest:\n"
            + "\n".join(f"  {f}" for f in findings)
        )

    print(
        f"PASS: {checked} vendored package(s) reproduce their content_sha256 "
        f"(sha256 over path+bytes, sorted); {checked}/{len(packages)} declared "
        "packages digested, 0 skipped."
    )
    return GREEN


if __name__ == "__main__":
    sys.exit(main())
