"""Export this service's OpenAPI document to `contracts/openapi/v1.json`, or check it.

The committed spec is the contract other services and the estate's pin-bump hook
read: `oasdiff breaking <old> <new>` runs against it in this service's CI (base
branch against the change) and in the monorepo when the pin moves. A spec that
drifted from the code makes both comparisons judge a document nobody serves, so
`--check` is run by `tests/shape/test_openapi_contract.py` on every suite run.

THE APP IS BUILT THROUGH THE REAL COMPOSITION ROOT -- `build_container()` then
`create_app()`, the two calls `composition/main.py` makes -- never by importing
`main.py`, which reads the process environment at import time. The configuration
is HERMETIC: every `ServiceConfig` field is passed explicitly, so no ambient
variable can reach it (`BaseSettings` consults the environment for any field a
caller leaves out), and the values chosen open no socket and touch no database:
`SERVICE_ENV=local` selects the header principal adapter, no secret id means no
AWS client, and the SQLAlchemy engine connects lazily, at first query, which an
export never makes. `main.py`'s third call, `mount_non_http_dispatch`, is a no-op
for the HTTP function this spec describes, so it is not repeated here.

FastAPI derives operation ids from function names, paths and methods, not from
`hash()`, so the output does not depend on PYTHONHASHSEED; `sort_keys` removes
the remaining dict-order freedom. The test measures both.

Exit codes follow the kit's convention: 0 current (or written), 1 drift, with a
unified diff, 2 a refusal naming why the app could not be built.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / "contracts" / "openapi" / "v1.json"

#: Offline values for the two fields that refuse an empty default. Port 9 is the
#: discard port; nothing connects to it, because nothing queries.
_OFFLINE_OVERRIDES = {
    "service_env": "local",
    "database_url": "postgresql+psycopg://openapi-export@127.0.0.1:9/openapi_export",
}


class AppUnbuildable(Exception):
    """The composition root refused or failed; the message names the cause."""


def build_spec() -> dict[str, Any]:
    try:
        from foundry_service_template.composition.app_factory import create_app
        from foundry_service_template.composition.config import ServiceConfig
        from foundry_service_template.composition.container import build_container

        fields = {name: field.default for name, field in ServiceConfig.model_fields.items()}
        config = ServiceConfig(**{**fields, **_OFFLINE_OVERRIDES})
        return create_app(build_container(config)).openapi()
    except Exception as exc:  # every cause is a refusal; the type is in the message
        raise AppUnbuildable(f"{type(exc).__name__}: {exc}") from exc


def render(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def drift_diff(committed_path: Path, current: str) -> str:
    """A unified diff from the committed spec to the current one; empty when equal."""
    committed = committed_path.read_text() if committed_path.is_file() else ""
    return "".join(
        difflib.unified_diff(
            committed.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile=f"{committed_path} (committed)",
            tofile=f"{committed_path} (from the app)",
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 with a diff on drift")
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC, help="the spec file")
    args = parser.parse_args(argv)

    try:
        current = render(build_spec())
    except AppUnbuildable as exc:
        print(f"export_openapi: REFUSED -- the app could not be built: {exc}", file=sys.stderr)
        return 2

    if not args.check:
        args.spec.parent.mkdir(parents=True, exist_ok=True)
        args.spec.write_text(current)
        print(f"export_openapi: wrote {args.spec}")
        return 0

    diff = drift_diff(args.spec, current)
    if diff:
        sys.stdout.write(diff)
        print(
            f"export_openapi: DRIFT -- {args.spec} does not match the app; "
            "run `python scripts/export_openapi.py` and commit the result",
            file=sys.stderr,
        )
        return 1
    print(f"export_openapi: {args.spec} matches the app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
