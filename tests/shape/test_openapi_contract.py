"""`contracts/openapi/v1.json` is the spec the app actually serves.

CI compares the base branch's committed spec against the change's with
`oasdiff breaking` (`service-kit/ci/openapi_breaking.sh`), and the monorepo does
the same when this service's pin moves. Both judge the COMMITTED file, so a spec
that drifted from the code lets a breaking change through unseen. This test is the
drift gate, and it runs in the ordinary suite so no separate step can be forgotten.

Every arm runs the real CLI as a subprocess and reads its exit code, and each
detector carries its control in the other direction: drift is reported on a copy
with one path removed, the export stays green under an ambient environment that
makes an ordinarily-built config refuse, and a package that cannot be imported is
exit 2. The offline claim needs no socket patch: the exporter's database URL
names the discard port, so an export that connected would fail rather than pass,
and `tests/unit/test_container.py` already holds `build_container()` to zero sockets.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "export_openapi.py"
SPEC = ROOT / "contracts" / "openapi" / "v1.json"
IMPORT_PATH = os.pathsep.join(str(ROOT / p) for p in ("src", "service-kit/vendor"))

#: `PORT=0` is refused by `ServiceConfig`'s own validator, naming the variable.
HOSTILE = {"PORT": "0"}


def _python(*args: str, pythonpath: str = IMPORT_PATH, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": pythonpath, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def _export(*args: str, **kwargs: str) -> subprocess.CompletedProcess:
    return _python(str(SCRIPT), *args, **kwargs)


def test_committed_spec_matches_the_app() -> None:
    done = _export("--check")
    assert done.returncode == 0, (
        f"exit {done.returncode}: {done.stderr}\n{done.stdout[:4000]}\n"
        "Regenerate with `python scripts/export_openapi.py` and commit it."
    )


def test_drift_is_reported_with_a_diff(tmp_path: Path) -> None:
    """Positive control: one path removed from a copy of the spec is drift, named in the diff."""
    document = json.loads(SPEC.read_text())
    removed = sorted(document["paths"])[-1]
    del document["paths"][removed]
    stale = tmp_path / "v1.json"
    stale.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")

    done = _export("--check", "--spec", str(stale))

    assert done.returncode == 1, done.stderr
    assert f'+    "{removed}": {{' in done.stdout
    assert stale.read_text() != SPEC.read_text(), "--check must not rewrite the file it judges"


def test_a_missing_spec_is_drift_and_writing_repairs_it(tmp_path: Path) -> None:
    target = tmp_path / "contracts" / "openapi" / "v1.json"

    assert _export("--check", "--spec", str(target)).returncode == 1
    assert _export("--spec", str(target)).returncode == 0
    assert target.read_text() == SPEC.read_text()


def test_the_export_ignores_the_ambient_environment() -> None:
    """Every `ServiceConfig` field is passed explicitly, so no variable reaches it.

    The control comes first: the same environment makes a config built the
    ordinary way refuse, so a green export under it is evidence and not luck.
    """
    ordinary = _python(
        "-c",
        "from foundry_service_template.composition.config import ServiceConfig\n"
        "ServiceConfig(service_env='local', database_url='postgresql+psycopg://u@h/db')",
        **HOSTILE,
    )
    assert ordinary.returncode != 0 and "set PORT" in ordinary.stderr, ordinary.stderr

    done = _export("--check", **HOSTILE)
    assert done.returncode == 0, done.stderr


def test_an_app_that_cannot_be_built_is_a_named_refusal(tmp_path: Path) -> None:
    """A package that raises on import, first on the path, stands in for any build failure."""
    shadow = tmp_path / "foundry_service_template"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise RuntimeError('planted composition failure')\n")

    done = _export("--check", pythonpath=str(tmp_path))

    assert done.returncode == 2, done.stdout
    assert "RuntimeError: planted composition failure" in done.stderr
