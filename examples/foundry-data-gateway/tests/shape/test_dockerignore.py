"""The build context excludes bytecode and tool caches, and nothing the Dockerfile COPYs.

Two directions, because a .dockerignore can fail either way: missing a pattern lets
stale bytecode into a locally built image, and an over-broad
pattern silently drops a file the Dockerfile needs, which surfaces as a build error
several steps from its cause. Docker's own matcher is not importable here, so the
second direction uses fnmatch per path, which is stricter than Docker's `**` handling
for the plain patterns this file uses.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERIGNORE = ROOT / ".dockerignore"
DOCKERFILE = ROOT / "service-kit" / "docker" / "Dockerfile"

REQUIRED_PATTERNS = ("**/__pycache__", "**/*.pyc", ".venv")


def _patterns() -> list[str]:
    lines = DOCKERIGNORE.read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def _ignored(rel: str, patterns: list[str]) -> bool:
    parts = rel.strip("/").split("/")
    prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    for pattern in patterns:
        bare = pattern[3:] if pattern.startswith("**/") else pattern
        for prefix in prefixes:
            if fnmatch.fnmatch(prefix, pattern) or fnmatch.fnmatch(prefix.split("/")[-1], bare):
                return True
    return False


def test_bytecode_and_virtualenvs_are_excluded_from_the_build_context() -> None:
    patterns = _patterns()
    missing = [p for p in REQUIRED_PATTERNS if p not in patterns]
    assert missing == [], f".dockerignore lost {missing}"
    for rel in (
        "src/pkg/__pycache__/mod.cpython-312.pyc",
        ".venv/bin/python",
        "migrations/__pycache__",
    ):
        assert _ignored(rel, patterns), f"{rel} would enter the build context"


def test_nothing_the_dockerfile_copies_is_excluded() -> None:
    patterns = _patterns()
    sources = []
    for line in DOCKERFILE.read_text().splitlines():
        match = re.match(r"^COPY\s+(?!--from)(\S+)\s+\S+", line)
        if match:
            source = match.group(1)
            sources.append(source.replace("${SERVICE_PACKAGE}", "foundry_data_gateway"))
    assert len(sources) >= 6, f"parsed only {len(sources)} COPY source(s) from the Dockerfile"
    excluded = [s for s in sources if _ignored(s, patterns)]
    assert excluded == [], f".dockerignore excludes Dockerfile COPY source(s): {excluded}"
