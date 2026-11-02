"""The one reader of a service's `[tool.foundry.kit]` declaration.

`service-kit/` is template-owned and `sync.py` replaces it wholesale. Some bytes inside
or beside it nevertheless belong to the SERVICE:
a comms service vendors `comms_contracts` and `comms_eventing` under
`service-kit/vendor/`, and comms-email owns its five-function release in
`.github/workflows/ci.yml`. Before this module nothing said so, so every sync deleted
the packages and overwrote the release, and each service re-emitted its own
MANIFEST.json to get the drift gate green again -- which made that gate compare the
service against bytes the service had approved itself.

The service now DECLARES what it owns, in its own pyproject.toml:

    [tool.foundry.kit]
    flavour = "comms"
    service_vendored = ["comms_contracts", "comms_eventing"]
    owned_placements = [".github/workflows/ci.yml"]

`sync.py` preserves exactly that and refuses a collision; `gates/check_kit_drift.py`
checks everything else against the template's unmodified manifest and puts floors on
what the service owns. Both import THIS module, which imports neither, so there is one
parser of the declaration rather than two that drift apart -- the flavour reader used
to be restated in both files. `scripts/check_kit_ownership_single_reader.py` (template)
holds that line.

Stdlib only, because `sync.py` runs in a fresh checkout before anything is installed.
Every refusal is an `OwnershipRefused` naming its reason; each caller turns it into its
own exit 2, so a malformed declaration is never read as "nothing declared".
"""

from __future__ import annotations

import json
import keyword
from dataclasses import dataclass
from pathlib import Path

#: The declaration's home, spelled the two ways a pyproject.toml can write it.
KIT_TABLE = "[tool.foundry.kit]"
KIT_DOTTED = "tool.foundry.kit"
DEFAULT_FLAVOUR = "default"
FLAVOURS = (DEFAULT_FLAVOUR, "comms")
#: Kit-owned files that cannot live inside the kit, because the tool that reads them
#: only looks in one place. GitHub reads workflows from `.github/workflows/` and
#: nowhere else. `owned_placements` may name only these.
PLACEMENTS = (".github/workflows/ci.yml",)
KNOWN_KEYS = frozenset({"flavour", "service_vendored", "owned_placements"})
#: Where a declared package lives, relative to the service root.
VENDOR_DIR = "service-kit/vendor"
VENDOR_JSON = f"{VENDOR_DIR}/VENDOR.json"


class OwnershipRefused(Exception):
    """The declaration cannot be read; the message names why and what to change."""


@dataclass(frozen=True)
class KitOwnership:
    flavour: str
    service_vendored: frozenset[str]  # package dirs under service-kit/vendor/
    owned_placements: frozenset[str]  # members of PLACEMENTS


def read_ownership(package_root: Path, *, module: str | None = None) -> KitOwnership:
    """Parse and validate the service's declaration.

    `module` is the service's one package under `src/`, which the caller resolves (this
    module reads pyproject.toml and nothing else). A `comms_*` package must declare a
    flavour: judged as the default it would regain the SQS path the comms flavour
    removes.

    The file is always PARSED, never sniffed for the table header, so a dotted key or an
    inline table is the same declaration.
    """
    table = _kit_table(package_root / "pyproject.toml")
    unknown = sorted(set(table) - KNOWN_KEYS)
    if unknown:
        raise OwnershipRefused(
            f"{KIT_TABLE} has unknown key(s) {', '.join(unknown)}; known: "
            f"{', '.join(sorted(KNOWN_KEYS))}"
        )
    return KitOwnership(
        flavour=_flavour(table.get("flavour"), module),
        service_vendored=_package_names(table.get("service_vendored", [])),
        owned_placements=_placements(table.get("owned_placements", [])),
    )


def strip_declared_rows(vendor_json: str, declared: frozenset[str]) -> str:
    """The VENDOR.json bytes this file would have if the declared rows were absent.

    Rows removed, re-dumped in the template's own canonical form (`canonical_json`), so a
    service carrying declared rows on top of the template's file strips back to the
    template's bytes exactly.
    """
    document = _vendor_document(vendor_json)
    for name in declared:
        document["packages"].pop(name, None)
    return canonical_json(document)


def declared_rows(vendor_json: str, declared: frozenset[str]) -> dict[str, object]:
    """The declared packages' rows; a declared name with no row is refused."""
    packages = _vendor_document(vendor_json)["packages"]
    missing = sorted(name for name in declared if name not in packages)
    if missing:
        raise OwnershipRefused(
            f"{VENDOR_JSON} has no row for declared package(s) {', '.join(missing)}; "
            f"vendor them with scripts/vendor_package.py, or remove them from "
            f"service_vendored"
        )
    return {name: packages[name] for name in declared}


def merge_rows(vendor_json: str, rows: dict[str, object]) -> str:
    """The template's VENDOR.json with the service's declared rows added, canonical."""
    document = _vendor_document(vendor_json)
    clash = sorted(name for name in rows if name in document["packages"])
    if clash:
        raise OwnershipRefused(
            f"the template's {VENDOR_JSON} already has row(s) {', '.join(clash)}; a declared "
            f"package may not shadow a template package"
        )
    document["packages"].update(rows)
    return canonical_json(document)


def canonical_json(document: object) -> str:
    """VENDOR.json's bytes as `scripts/vendor_package.py` writes them.

    `ensure_ascii=False` is that writer's choice and has to be this one's. With the
    default, one character outside ASCII in a TEMPLATE row came back escaped from the
    strip, so its hash was not the manifest's and every service that declares a package
    read as drifted straight after a clean sync, while a service that declares none
    stayed green.
    """
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _kit_table(pyproject: Path) -> dict:
    if not pyproject.is_file():
        return {}
    try:
        import tomllib
    except ImportError:
        raise OwnershipRefused(
            "reading pyproject.toml needs Python 3.11 or newer (tomllib); the kit targets 3.12"
        ) from None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise OwnershipRefused(f"{pyproject} is not valid TOML: {exc}") from None
    table = data.get("tool", {}).get("foundry", {}).get("kit", {})
    if not isinstance(table, dict):
        raise OwnershipRefused(f"{KIT_DOTTED} in {pyproject} is not a table")
    return table


def _flavour(value: object, module: str | None) -> str:
    if value is None:
        if (module or "").startswith("comms_"):
            raise OwnershipRefused(
                f"{module} is a comms_* package and pyproject.toml declares no kit flavour; "
                f'add `{KIT_TABLE}` with `flavour = "comms"` (or "{DEFAULT_FLAVOUR}" to opt '
                f"out)"
            )
        return DEFAULT_FLAVOUR
    if value not in FLAVOURS:
        raise OwnershipRefused(
            f"pyproject.toml declares flavour {value!r}; known: {', '.join(FLAVOURS)}"
        )
    return value  # type: ignore[return-value]


def _string_list(value: object, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise OwnershipRefused(f"{KIT_DOTTED}.{key} must be a list of strings, got {value!r}")
    if len(set(value)) != len(value):
        raise OwnershipRefused(f"{KIT_DOTTED}.{key} names an entry twice: {value!r}")
    return value


def _package_names(value: object) -> frozenset[str]:
    names = _string_list(value, "service_vendored")
    bad = [name for name in names if not name.isidentifier() or keyword.iskeyword(name)]
    if bad:
        raise OwnershipRefused(
            f"{KIT_DOTTED}.service_vendored names {bad!r}, which are not importable package "
            f"names; each must be a directory under {VENDOR_DIR}/ that Python can import"
        )
    return frozenset(names)


def _placements(value: object) -> frozenset[str]:
    names = _string_list(value, "owned_placements")
    bad = [name for name in names if name not in PLACEMENTS]
    if bad:
        raise OwnershipRefused(
            f"{KIT_DOTTED}.owned_placements names {bad!r}; only a kit placement can be owned "
            f"({', '.join(PLACEMENTS)}). A file outside service-kit/ that is not a placement "
            f"is the service's already"
        )
    return frozenset(names)


def _vendor_document(vendor_json: str) -> dict:
    try:
        document = json.loads(vendor_json)
    except json.JSONDecodeError as exc:
        raise OwnershipRefused(f"{VENDOR_JSON} is not valid JSON: {exc}") from None
    if not isinstance(document, dict) or not isinstance(document.get("packages"), dict):
        raise OwnershipRefused(f"{VENDOR_JSON} has no `packages` object")
    return document
