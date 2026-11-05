#!/usr/bin/env python3
"""S18: pydantic owns the two boundaries, and neither can drift back to hand-rolled.

A service has exactly two edges where untrusted, externally-shaped data becomes
Python: the ENVIRONMENT at the composition root, and the WIRE at the transport
layer. A third edge -- a provider's JSON envelope inside a real adapter -- is the
same problem wearing a vendor's name. S18 says all three are parsed by a declared
pydantic type, and this gate is what stops each from going back one file at a
time.

FIVE CLAUSES, each a finding:

1. **THE CONFIG ROOT IS A `BaseSettings` WITH A DECLARED `model_config`.** The
   shape it replaces is a `@dataclass` plus a hand-written `from_environment()`,
   which spells every field twice and joins the two halves with nothing a tool
   can check: a field added to the class and not to the reader exists, carries
   its default, is never configurable, and is invisible at exit 0. The clause
   also requires `env_prefix` and `frozen=True` to be written out. `env_prefix=""`
   is pydantic-settings' own default, so an omitted key and a declared empty one
   behave identically today and differ entirely in what they tell a reader --
   `.claude/CLAUDE.md`: "a guarantee that is TRUE only because of a library
   DEFAULT is not a guarantee".

2. **EVERY CLASS UNDER `transport/http/schemas/` IS A pydantic `BaseModel`.** A
   `@dataclass` DTO there validates nothing, so a request body reaches a use case
   unchecked while sitting in a directory whose name asserts it was checked.

3. **`domain/` IMPORTS NO pydantic.** The plan said this was "already enforced by
   check_layering.py". MEASURED FALSE on 2026-09-18: that gate's `FRAMEWORK_ROOTS`
   holds ten names and `pydantic` is not among them, and `tests/shape/
   test_layering.py` -- which does list it -- covers `domain/` and `application/`
   but not `ports/`, and is a TEST rather than a gate, so it does not travel with
   the kit into a service whose suite is not run. The clause is here because the
   guarantee was cited and not implemented.

4. **`isinstance(x, dict)` UNDER `adapters/real/` IS A RATCHET, NOT A WALL.** A
   provider envelope read through `isinstance`/`.get` chains is a parser written
   by hand and never named; a `TypeAdapter` or a model is the declared version.
   It is a ratchet because the measured estate-wide count is 21 sites across 12
   files in 4 services (agent-runtime 5, artifact-store 5, llm-gateway 8,
   model-foundry 3), and a wall would be red on every one of them on the day it
   landed. `scripts/pydantic_dict_ratchet.json` records the count AND the
   inventory, and fails in BOTH directions: a bare number cannot tell a repaired
   site from a renamed one, and an unlowered ratchet is a ceiling nobody holds.

5. **NO CONFIG ROOT SETS `populate_by_name` OR `validate_by_name`.** Either,
   set truthy, makes pydantic-settings offer every ALIASED field a SECOND
   environment variable -- its own field name -- which nothing declares and no
   registry can see. This template shipped with it: `env` was aliased to
   `SERVICE_ENV`, and `ENV` was read and took effect, as was `ISSUER` for
   `issuer`. `ENV` is a name shells and CI runners commonly already set.
   Mechanism, pydantic-settings 2.15.0: `sources/utils.py:342` appends the field
   name to the alias list, and `sources/base.py:324` computes
   `include_name = populate_by_name or validate_by_name`, so the flag cannot be
   enabled for construction alone -- which is what it was added for. A key
   present and explicitly `False` is NOT a finding: that is somebody saying no.

DISCOVERY, AND WHY THIS GATE DOES NOT NAME `composition/config.py`.
The obvious glob `src/*/composition/config.py` returns a confident, wrong answer.
Measured over the estate on 2026-09-18: 7 files covering 7 of 9 services, reading
as complete. Two services have a different layout and would have been judged by
nothing at all --

    model-service     src/<pkg>/config.py          (package ROOT)
    workflow-service  src/<pkg>/composition/settings.py  (named settings)

-- which is `.claude/CLAUDE.md`'s "enumerate the artifact, not a list standing in
for it", with the list hidden inside a path. So the config root is DISCOVERED by
basename anywhere under `src/<pkg>/`, and a service where discovery finds NONE is
a REFUSAL (exit 2), never a skip: "found no violation" and "looked at nothing"
are the same output otherwise. Two candidates is a refusal too -- ambiguity is
not a verdict.

The basename set is `config.py` and `settings.py` EXACTLY, not a `config*` prefix,
and that precision is load-bearing: `artifact-store/composition/config_rules.py`
and `llm-gateway/composition/config_validation.py` are companions rather than
roots, and a prefix glob sweeps both in and turns a correct estate into 2
findings. Checked against the real inventory: the rule returns exactly one root
for each of the 9 services and zero companions.

FIXTURES ARE TEMP-MATERIALISED, not tracked, for the reasons `check_orm_standard.py`
states: a tracked offending package under `gate_fixtures/` is found by the OTHER
gates' production scans and moves `EXPECTED_MARKED_FIXTURE_FILES`, which five
gates assert against. The TRACKED fixture tree is still this gate's business in
one direction -- it must never enter the production population -- and that
exclusion is asserted BOTH ways in `main()`: nothing from `FIXTURE_ROOT` may be
scanned, AND the tree must still contain input this analyser WOULD flag, so the
exclusion is demonstrably load-bearing rather than credited for an absence it did
not cause.

Exit codes: `service-kit/gates/gate_exit_codes.py`.
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_common import (  # noqa: E402
    FIXTURE_ROOT,
    MIN_PACKAGES,
    PACKAGE_ROOT,
    imports_in,
    package_of,
    python_sources,
    service_data_file,
    source_roots,
)
from gate_exit_codes import GREEN, finding, refuse  # noqa: E402

#: The basenames a config ROOT may have. EXACT, not a prefix -- see the module
#: docstring's companion measurement.
CONFIG_BASENAMES = frozenset({"config.py", "settings.py"})

#: pydantic's two boundary base classes, by the name a class statement uses.
#: Both spellings, because `class C(BaseSettings)` and
#: `class C(pydantic_settings.BaseSettings)` are the same declaration.
SETTINGS_BASE = "BaseSettings"
MODEL_BASE = "BaseModel"

#: Where the wire schemas live, relative to `src/<pkg>/`.
SCHEMA_PACKAGE = ("transport", "http", "schemas")
DOMAIN_PACKAGE = ("domain",)
REAL_ADAPTER_PACKAGE = ("adapters", "real")

#: The service-owned ratchet for clause 4.
RATCHET_FILE = "pydantic_dict_ratchet.json"

#: The `model_config` keys clause 1 requires to be WRITTEN, whatever their value.
REQUIRED_SETTINGS_KEYS = ("env_prefix", "frozen")

#: CLAUSE 5. Either of these, set truthy, makes pydantic-settings offer every
#: ALIASED field a second environment variable -- its own field name -- which
#: nothing declares and no registry can see. Measured in this template:
#: `env` was aliased to `SERVICE_ENV` and `ISSUER`/`ENV` were read and took
#: effect, with `ENV` a name shells and CI runners commonly already set.
NAME_POPULATION_FLAGS = ("populate_by_name", "validate_by_name")

#: HAND-WRITTEN floors, two-sided. The first says the scan reached a real tree;
#: the second says the classifier has demonstrated it can resolve a pydantic
#: import at all, because a matcher that has never seen pydantic proves nothing
#: by staying quiet about it.
MIN_MODULES = 20
MIN_PYDANTIC_MODULES = 2


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


#: How a package is seen to read the environment. HAND-WRITTEN rather than
#: derived: a list built from what the tree happens to contain would shrink to
#: nothing on a tree that reads the environment in a way nobody anticipated.
ENVIRONMENT_READS = frozenset({"environ", "getenv"})
SETTINGS_BASE_NAMES = frozenset({"BaseSettings"})


def reads_the_environment(package: Path) -> bool:
    """Does ANY module in this package read the environment?

    THIS IS THE DISCRIMINATOR FOR THE CONFIG-ROOT CLAUSE, and it replaces the
    one that reads naturally and is wrong: "every package is a service, so every
    package needs a config root". Measured against agent-runtime, which
    ships three packages -- `foundry_sandbox` is unambiguously a SERVICE, a
    second deployable with all six layers, and reads no environment at all, so
    demanding a config root of it is demanding a module with nothing to put in
    it. Scoping by declared ROLE does not fix that; it moves the false red from
    three packages to two.

    Absence carries the fact here rather than standing near it, which is what
    makes this safe to infer instead of declare. "No environment read anywhere in
    this package" is directly observable in the code, where "has a `runtime/`
    directory" was a proxy that an empty directory defeated one gate over. And it
    fails closed with nothing to maintain: a package that starts reading the
    environment tomorrow is in scope the same day, with no entry to remember.
    """
    for path in python_sources(package, exclude_fixtures=True):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ENVIRONMENT_READS:
                value = node.value
                if isinstance(value, ast.Name) and value.id == "os":
                    return True
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                if root == "os" and any(a.name in ENVIRONMENT_READS for a in node.names):
                    return True
                if root.startswith("pydantic") and any(
                    a.name in SETTINGS_BASE_NAMES for a in node.names
                ):
                    return True
    return False


def discover_config_roots(package: Path) -> list[Path]:
    """Every config ROOT under `src/<pkg>/`, by basename, anywhere in the tree."""
    return sorted(
        path
        for path in python_sources(package, exclude_fixtures=False)
        if path.name in CONFIG_BASENAMES
    )


# ---------------------------------------------------------------------------
# ast helpers
# ---------------------------------------------------------------------------


def _base_names(node: ast.ClassDef) -> list[str]:
    names = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _import_roots(tree: ast.AST, package: str) -> list[tuple[str, int]]:
    """(top-level module, line) per import; relative ones resolve into `package`."""
    return [(s.module.split(".")[0], s.lineno) for s in imports_in(tree, package) if s.module]


def _settings_config_keywords(cls: ast.ClassDef) -> set[str] | None:
    """Keyword names passed to `model_config = SettingsConfigDict(...)`.

    None when the class assigns no `model_config` call at all, which is a
    DIFFERENT finding from assigning one that omits a key -- the two want
    different remedies, so they are distinguished rather than collapsed.
    """
    for statement in cls.body:
        if not isinstance(statement, ast.Assign):
            continue
        targets = [t.id for t in statement.targets if isinstance(t, ast.Name)]
        if "model_config" not in targets:
            continue
        value = statement.value
        if isinstance(value, ast.Call):
            return {kw.arg for kw in value.keywords if kw.arg}
        return set()
    return None


def _settings_config_keyword(cls: ast.ClassDef, key: str) -> ast.expr | None:
    """The VALUE node passed for `key`, or None when the key is absent."""
    for statement in cls.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "model_config" for t in statement.targets):
            continue
        value = statement.value
        if not isinstance(value, ast.Call):
            return None
        for kw in value.keywords:
            if kw.arg == key:
                return kw.value
    return None


def _frozen_is_true(cls: ast.ClassDef) -> bool:
    node = _settings_config_keyword(cls, "frozen")
    return isinstance(node, ast.Constant) and node.value is True


def _name_population_flags(cls: ast.ClassDef) -> list[str]:
    """The flags set TRUTHY that give every aliased field a second env name.

    Both spellings, because pydantic 2.11 split `populate_by_name` into
    `validate_by_name`/`validate_by_alias` and
    `pydantic_settings/sources/base.py:324` reads
    `include_name = populate_by_name or validate_by_name`. A matcher that saw
    only the older name would be blind to the newer one in a service written
    next year, which is the same defect in a fresh spelling.

    A key present and explicitly FALSE is not a finding: that is somebody saying
    no, and recording the decision is the behaviour this gate rewards elsewhere.
    """
    found = []
    for key in NAME_POPULATION_FLAGS:
        node = _settings_config_keyword(cls, key)
        if node is None:
            continue
        if isinstance(node, ast.Constant) and node.value is False:
            continue
        found.append(key)
    return found


def _model_classes(tree: ast.AST) -> dict[str, ast.ClassDef]:
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}


def _derives_from(cls: ast.ClassDef, base: str, local: dict[str, ast.ClassDef]) -> bool:
    """`cls` derives from `base`, following bases declared in the SAME module.

    The chain matters: a schema module that declares one `class _Base(BaseModel)`
    and three DTOs deriving from it is correct, and a matcher reading only the
    immediate base would report all three. A false red naming a real file is the
    worse direction, because the natural response is to rewrite correct code.
    """
    seen: set[str] = set()
    queue = list(_base_names(cls))
    while queue:
        name = queue.pop()
        if name == base:
            return True
        if name in seen:
            continue
        seen.add(name)
        parent = local.get(name)
        if parent is not None:
            queue.extend(_base_names(parent))
    return False


def _isinstance_dict_lines(tree: ast.AST) -> list[int]:
    """`isinstance(x, dict)` and `isinstance(x, (dict, ...))`, by line."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "isinstance"):
            continue
        if len(node.args) != 2:
            continue
        second = node.args[1]
        names: list[str] = []
        if isinstance(second, ast.Name):
            names = [second.id]
        elif isinstance(second, ast.Tuple):
            names = [e.id for e in second.elts if isinstance(e, ast.Name)]
        if "dict" in names:
            lines.append(node.lineno)
    return lines


# ---------------------------------------------------------------------------
# the analyser -- ONE copy, used for production AND every control arm
# ---------------------------------------------------------------------------


def analyse(package: Path) -> tuple[list[str], dict[str, object]]:
    """(findings, counts) for one service's source package.

    `counts["dict_sites"]` is the clause-4 INVENTORY (`relative:line` strings)
    rather than a number: the ratchet compares inventories, because 5 and 5 look
    identical when one site was fixed and another introduced the same day.
    """
    findings: list[str] = []
    counts: dict[str, object] = {
        "modules": 0,
        "pydantic_modules": 0,
        "schema_classes": 0,
        "real_adapter_modules": 0,
        "dict_sites": [],
        "config_roots": [],
    }
    dict_sites: list[str] = []

    roots = discover_config_roots(package)
    counts["config_roots"] = [p.relative_to(package).as_posix() for p in roots]

    for path in python_sources(package, exclude_fixtures=False):
        relative = path.relative_to(package)
        posix = relative.as_posix()
        counts["modules"] = int(counts["modules"]) + 1
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            findings.append(f"{posix}: unparseable ({exc})")
            continue

        import_roots = _import_roots(tree, package_of(path, package.parent))
        if any(root in {"pydantic", "pydantic_settings"} for root, _ in import_roots):
            counts["pydantic_modules"] = int(counts["pydantic_modules"]) + 1
        local = _model_classes(tree)

        # --- CLAUSE 1: the config root is a declared BaseSettings ------------
        if path in roots:
            settings = [c for c in local.values() if _derives_from(c, SETTINGS_BASE, local)]
            if not settings:
                findings.append(
                    f"{posix}:1: the config root declares no {SETTINGS_BASE} subclass. S18 puts "
                    f"the environment behind pydantic-settings; a @dataclass plus a hand-written "
                    f"from_environment() spells every field twice and nothing joins the halves"
                )
            for cls in settings:
                keys = _settings_config_keywords(cls)
                if keys is None:
                    findings.append(
                        f"{posix}:{cls.lineno}: {cls.name} assigns no model_config. Add "
                        f"model_config = SettingsConfigDict(env_prefix=..., frozen=True)"
                    )
                    continue
                for required in REQUIRED_SETTINGS_KEYS:
                    if required not in keys:
                        findings.append(
                            f"{posix}:{cls.lineno}: {cls.name}'s SettingsConfigDict does not "
                            f"declare {required!r}. A value that is right only because it is the "
                            f"library default is not a decision anybody made"
                        )
                # --- CLAUSE 5: no second, undeclared name per field ----------
                for flag in _name_population_flags(cls):
                    findings.append(
                        f"{posix}:{cls.lineno}: {cls.name} sets {flag!r}. That gives every "
                        f"ALIASED field a SECOND environment variable -- its own field name -- "
                        f"which nothing declares and no registry can see. Measured in this "
                        f"template: with it set, ENV reached config.env and ISSUER reached "
                        f"config.issuer. Name the field after its variable instead of aliasing "
                        f"it, and the flag becomes unnecessary"
                    )
                if "frozen" in keys and not _frozen_is_true(cls):
                    findings.append(
                        f"{posix}:{cls.lineno}: {cls.name} declares frozen but not frozen=True. "
                        f"The config reaches every adapter; a mutable one is an ambient global"
                    )

        # --- CLAUSE 2: wire schemas are BaseModel ----------------------------
        if relative.parts[: len(SCHEMA_PACKAGE)] == SCHEMA_PACKAGE:
            for name, cls in sorted(local.items()):
                if name.startswith("_"):
                    continue
                counts["schema_classes"] = int(counts["schema_classes"]) + 1
                if not _derives_from(cls, MODEL_BASE, local):
                    findings.append(
                        f"{posix}:{cls.lineno}: {name} under {'/'.join(SCHEMA_PACKAGE)}/ is not a "
                        f"pydantic {MODEL_BASE}. A DTO here validates nothing while sitting in a "
                        f"directory whose name says the wire was checked"
                    )

        # --- CLAUSE 3: domain/ imports no pydantic ---------------------------
        if relative.parts[: len(DOMAIN_PACKAGE)] == DOMAIN_PACKAGE:
            for root, line in import_roots:
                if root in {"pydantic", "pydantic_settings"}:
                    findings.append(
                        f"{posix}:{line}: domain imports {root!r}. The domain model is plain "
                        f"Python by §1; a validation library there makes the inner layer depend "
                        f"on a wire concern"
                    )

        # --- CLAUSE 4: the adapters/real envelope ratchet --------------------
        if relative.parts[: len(REAL_ADAPTER_PACKAGE)] == REAL_ADAPTER_PACKAGE:
            counts["real_adapter_modules"] = int(counts["real_adapter_modules"]) + 1
            dict_sites.extend(f"{posix}:{line}" for line in _isinstance_dict_lines(tree))

    counts["dict_sites"] = sorted(dict_sites)
    return findings, counts


def load_ratchet(path: Path) -> tuple[int, list[str], list[str]]:
    """(ceiling, inventory, problems). A MISSING file is a problem, not an empty one."""
    if not path.is_file():
        return 0, [], [
            f"{path} is missing. A ratchet with no recorded value is not a ratchet: "
            f"every count would beat it and nothing could ever be seen to grow"
        ]
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return 0, [], [f"{path.name} is not valid JSON: {exc}"]
    if not isinstance(payload, dict) or "ratchet" not in payload or "inventory" not in payload:
        return 0, [], [f"{path.name} must carry both 'ratchet' and 'inventory'"]
    inventory = list(payload["inventory"])
    ceiling = int(payload["ratchet"])
    if ceiling != len(inventory):
        return ceiling, inventory, [
            f"{path.name}: ratchet {ceiling} disagrees with its own inventory of "
            f"{len(inventory)}. A number and a list that describe the same thing and "
            f"disagree mean one of them was maintained and the other was not"
        ]
    return ceiling, inventory, []


def ratchet_findings(current: list[str], ceiling: int, inventory: list[str]) -> list[str]:
    """Both directions: growth is a finding, and so is an unlowered ceiling."""
    if len(current) > ceiling:
        added = sorted(set(current) - set(inventory))
        return [
            f"{RATCHET_FILE}: {len(current)} isinstance(x, dict) site(s) under "
            f"{'/'.join(REAL_ADAPTER_PACKAGE)}/ against a ratchet of {ceiling}. Parse the "
            f"envelope with a pydantic model or TypeAdapter. New: {added}"
        ]
    if len(current) < ceiling:
        removed = sorted(set(inventory) - set(current))
        return [
            f"{RATCHET_FILE}: {len(current)} site(s) BEATS the ratchet of {ceiling} -- lower it "
            f"and update the inventory. Removed: {removed}. An unlowered ratchet is a ceiling "
            f"nobody holds"
        ]
    if sorted(current) != sorted(inventory):
        return [
            f"{RATCHET_FILE}: the count matches at {ceiling} but the inventory does not. "
            f"Only in the tree: {sorted(set(current) - set(inventory))}; only in the file: "
            f"{sorted(set(inventory) - set(current))}. A repaired site and a renamed one are "
            f"indistinguishable by count alone"
        ]
    return []


# ---------------------------------------------------------------------------
# controls
# ---------------------------------------------------------------------------

#: A whole conforming service package, as {relative path: source}. HAND-WRITTEN,
#: and deliberately not generated from the constants this gate asserts against:
#: a fixture built from the same declarations agrees with any of them, including
#: an empty set.
_CONFORMING: dict[str, str] = {
    "__init__.py": "",
    "composition/__init__.py": "",
    "composition/config.py": (
        "from pydantic_settings import BaseSettings, SettingsConfigDict\n\n\n"
        "class ServiceConfig(BaseSettings):\n"
        '    model_config = SettingsConfigDict(env_prefix="", frozen=True)\n\n'
        '    service_env: str = "local"\n'
    ),
    "transport/__init__.py": "",
    "transport/http/__init__.py": "",
    "transport/http/schemas/__init__.py": "",
    "transport/http/schemas/gadgets_schema.py": (
        "from pydantic import BaseModel, ConfigDict\n\n\n"
        "class _Wire(BaseModel):\n"
        "    model_config = ConfigDict(frozen=True)\n\n\n"
        "class GadgetResponse(_Wire):\n"
        "    gadget_id: str\n\n\n"
        "class GadgetListResponse(_Wire):\n"
        "    gadgets: list[GadgetResponse]\n"
    ),
    "domain/__init__.py": "",
    "domain/model.py": (
        "from dataclasses import dataclass\n\n\n"
        "@dataclass(frozen=True)\nclass Gadget:\n    gadget_id: str\n"
    ),
    "adapters/__init__.py": "",
    "adapters/real/__init__.py": "",
    "adapters/real/provider_gateway.py": (
        "from pydantic import TypeAdapter\n\n"
        "from fixture_pkg.transport.http.schemas.gadgets_schema import GadgetResponse\n\n"
        "_ENVELOPE = TypeAdapter(GadgetResponse)\n\n\n"
        "def read(payload: bytes) -> GadgetResponse:\n"
        "    return _ENVELOPE.validate_json(payload)\n"
    ),
}

#: One MUTATION per CLAUSE, applied to the conforming package. Never one per
#: defect class: a fixture that trips two clauses stays red when either is
#: blinded and therefore proves neither.
_MUTATIONS: dict[str, dict[str, str | None]] = {
    "config-is-a-dataclass": {
        "composition/config.py": (
            "import os\n"
            "from dataclasses import dataclass\n\n\n"
            "@dataclass(frozen=True)\n"
            "class ServiceConfig:\n"
            "    env: str\n\n"
            "    @classmethod\n"
            "    def from_environment(cls):\n"
            '        return cls(env=os.environ.get("SERVICE_ENV", "local"))\n'
        )
    },
    "config-omits-env-prefix": {
        "composition/config.py": (
            "from pydantic_settings import BaseSettings, SettingsConfigDict\n\n\n"
            "class ServiceConfig(BaseSettings):\n"
            "    model_config = SettingsConfigDict(frozen=True)\n\n"
            '    env: str = "local"\n'
        )
    },
    "config-populates-by-name": {
        "composition/config.py": (
            "from pydantic import AliasChoices, Field\n"
            "from pydantic_settings import BaseSettings, SettingsConfigDict\n\n\n"
            "class ServiceConfig(BaseSettings):\n"
            "    model_config = SettingsConfigDict(\n"
            '        env_prefix="", frozen=True, populate_by_name=True\n'
            "    )\n\n"
            '    env: str = Field(default="local", validation_alias=AliasChoices("SERVICE_ENV"))\n'
        )
    },
    "config-validates-by-name": {
        "composition/config.py": (
            "from pydantic import AliasChoices, Field\n"
            "from pydantic_settings import BaseSettings, SettingsConfigDict\n\n\n"
            "class ServiceConfig(BaseSettings):\n"
            "    model_config = SettingsConfigDict(\n"
            '        env_prefix="", frozen=True, validate_by_name=True\n'
            "    )\n\n"
            '    env: str = Field(default="local", validation_alias=AliasChoices("SERVICE_ENV"))\n'
        )
    },
    "config-declines-name-population": {
        "composition/config.py": (
            "from pydantic import AliasChoices, Field\n"
            "from pydantic_settings import BaseSettings, SettingsConfigDict\n\n\n"
            "class ServiceConfig(BaseSettings):\n"
            "    model_config = SettingsConfigDict(\n"
            '        env_prefix="", frozen=True, populate_by_name=False\n'
            "    )\n\n"
            '    env: str = Field(default="local", validation_alias=AliasChoices("SERVICE_ENV"))\n'
        )
    },
    "config-is-not-frozen": {
        "composition/config.py": (
            "from pydantic_settings import BaseSettings, SettingsConfigDict\n\n\n"
            "class ServiceConfig(BaseSettings):\n"
            '    model_config = SettingsConfigDict(env_prefix="", frozen=False)\n\n'
            '    env: str = "local"\n'
        )
    },
    "config-named-settings-at-package-root": {
        # THE LAYOUT DEVIATION, as a fixture. model-foundry puts config.py at the
        # package root and workflow-restate calls it settings.py; this arm is both
        # at once, and it must be JUDGED rather than skipped.
        "composition/config.py": None,
        "settings.py": (
            "import os\n"
            "from dataclasses import dataclass\n\n\n"
            "@dataclass(frozen=True)\n"
            "class ServiceConfig:\n"
            "    env: str\n\n"
            "    @classmethod\n"
            "    def from_environment(cls):\n"
            '        return cls(env=os.environ.get("SERVICE_ENV", "local"))\n'
        ),
    },
    "no-config-root-at-all": {"composition/config.py": None},
    "schema-is-a-dataclass": {
        "transport/http/schemas/gadgets_schema.py": (
            "from dataclasses import dataclass\n\n\n"
            "@dataclass\nclass GadgetResponse:\n    gadget_id: str\n"
        )
    },
    "domain-imports-pydantic": {
        "domain/model.py": (
            "from pydantic import BaseModel\n\n\nclass Gadget(BaseModel):\n    gadget_id: str\n"
        )
    },
    "domain-imports-a-local-pydantic-module": {
        "domain/pydantic.py": "class BaseModel:\n    pass\n",
        "domain/model.py": (
            "from .pydantic import BaseModel\n\n\nclass Gadget(BaseModel):\n    gadget_id: str\n"
        ),
    },
    "adapter-parses-by-hand": {
        "adapters/real/provider_gateway.py": (
            "def read(payload):\n"
            "    if not isinstance(payload, dict):\n"
            "        return None\n"
            '    inner = payload.get("body")\n'
            "    if isinstance(inner, dict):\n"
            '        return inner.get("gadget_id")\n'
            "    return None\n"
        )
    },
}


def _control_cases() -> list[tuple[str, int, int, str, str]]:
    """(case, expected_findings, expected_dict_sites, clause_marker, why).

    `expected_*` are exact HAND-WRITTEN numbers and `clause_marker` names the
    finding each red arm must be. A pass/fail arm cannot tell a gate reddening
    for its own clause from one reddening for a neighbour's, and this gate has
    four clauses over one tree.
    """
    return [
        ("conforming", 0, 0, "", "a package on the S18 standard is clean"),
        (
            "config-is-a-dataclass",
            1,
            0,
            "declares no BaseSettings subclass",
            "CLAUSE 1: the shape S18 replaces",
        ),
        (
            "config-omits-env-prefix",
            1,
            0,
            "'env_prefix'",
            "CLAUSE 1: an OMITTED env_prefix behaves exactly like the declared empty one, "
            "so only a gate that reads the KEY can tell a decision from a default",
        ),
        (
            "config-populates-by-name",
            1,
            0,
            "'populate_by_name'",
            "CLAUSE 5: the flag that shipped. It gives every ALIASED field a second, "
            "undeclared environment variable -- its own field name -- and ENV and ISSUER "
            "were read and took effect",
        ),
        (
            "config-validates-by-name",
            1,
            0,
            "'validate_by_name'",
            "CLAUSE 5, THE OTHER SPELLING. pydantic 2.11 split populate_by_name, and "
            "pydantic-settings reads `populate_by_name or validate_by_name`, so a matcher "
            "that saw only the old name is blind to the same defect written next year",
        ),
        (
            "config-declines-name-population",
            0,
            0,
            "",
            "CLAUSE 5'S NEAR MISS, and the reason the matcher reads the VALUE rather than "
            "the key. populate_by_name=False is somebody saying no; flagging it would be a "
            "false red naming a real file, which pushes an author to delete the record of "
            "a decision",
        ),
        (
            "config-is-not-frozen",
            1,
            0,
            "frozen but not frozen=True",
            "CLAUSE 1: frozen=False declares the key and loses the guarantee, which a "
            "key-presence check alone would pass",
        ),
        (
            "config-named-settings-at-package-root",
            1,
            0,
            "declares no BaseSettings subclass",
            "THE DISCOVERY ARM. A config root at the package root, named settings.py -- the "
            "two real layout deviations at once. A gate hardcoding composition/config.py "
            "finds nothing here and reports the service clean",
        ),
        (
            "no-config-root-at-all",
            -1,
            0,
            "",
            "THE REFUSAL ARM: discovery finds nothing, which is a hard error rather than a "
            "verdict. -1 means main() must refuse; analyse() itself reports no finding, and "
            "that is precisely why the refusal has to live above it",
        ),
        (
            "schema-is-a-dataclass",
            1,
            0,
            "is not a pydantic BaseModel",
            "CLAUSE 2: a DTO that validates nothing in a directory that says it does",
        ),
        (
            "domain-imports-pydantic",
            1,
            0,
            "domain imports 'pydantic'",
            "CLAUSE 3, the one the plan said check_layering.py already enforced. It does not",
        ),
        (
            "domain-imports-a-local-pydantic-module",
            0,
            0,
            "",
            "CLAUSE 3'S NEAR MISS. `from .pydantic import BaseModel` resolves through "
            "gate_common.imports_in to the package's own module, not the library",
        ),
        (
            "adapter-parses-by-hand",
            0,
            2,
            "",
            "CLAUSE 4: TWO isinstance(x, dict) sites and ZERO findings from analyse(). The "
            "ratchet is applied above the analyser, so this arm proves the SITES are counted; "
            "_ratchet_control_cases() proves the comparison",
        ),
    ]


def _ratchet_control_cases() -> list[tuple[list[str], int, list[str], int, str]]:
    """(current, ceiling, inventory, expected_findings, why). All three directions."""
    return [
        (["a.py:1"], 1, ["a.py:1"], 0, "count and inventory agree -- quiet"),
        (["a.py:1", "b.py:2"], 1, ["a.py:1"], 1, "growth is a finding"),
        ([], 1, ["a.py:1"], 1, "beating the ratchet without lowering it is a finding too"),
        (
            ["b.py:2"],
            1,
            ["a.py:1"],
            1,
            "THE INVENTORY ARM. One site repaired and one introduced: the COUNT is unchanged "
            "at 1, so a ratchet holding only a number is green here",
        ),
    ]


def _materialise(case: str, root: Path) -> None:
    sources = dict(_CONFORMING)
    for relative, body in _MUTATIONS.get(case, {}).items():
        if body is None:
            sources.pop(relative, None)
        else:
            sources[relative] = body
    for relative, body in sources.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def _self_test() -> int | None:
    """Run the PRODUCTION analyser over every control case, in a temp tree."""
    for case, expected, expected_sites, marker, why in _control_cases():
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "fixture_pkg"
            _materialise(case, package)
            findings, counts = analyse(package)
            modules = int(counts["modules"])
            # A MUTATION THAT NEVER LANDED READS EXACTLY LIKE A CLAUSE THAT
            # SURVIVED, so assert the tree reached the analyser before reading
            # any verdict from it.
            if modules < len(_CONFORMING) - 1:
                return refuse(
                    f"control {case!r} read {modules} module(s) of about {len(_CONFORMING)}; "
                    f"the fixture did not reach the analyser"
                )
            roots = list(counts["config_roots"])  # type: ignore[arg-type]
            if expected == -1:
                if roots:
                    return refuse(
                        f"control {case!r} was built with no config root and discovery found "
                        f"{roots}; the refusal arm is not testing a refusal"
                    )
                continue
            if len(roots) != 1:
                return refuse(
                    f"control {case!r} resolved {len(roots)} config root(s) {roots}, expected "
                    f"exactly 1; discovery is the clause every other clause stands on"
                )
            if len(findings) != expected:
                return refuse(
                    f"control {case!r} produced {len(findings)} finding(s), expected the "
                    f"hand-written {expected} ({why}). findings={findings}"
                )
            if marker and not any(marker in item for item in findings):
                return refuse(
                    f"control {case!r} produced {expected} finding(s) but none names {marker!r}. "
                    f"The count is right and the reason is not, which is a gate reddening for "
                    f"the wrong clause. findings={findings}"
                )
            sites = list(counts["dict_sites"])  # type: ignore[arg-type]
            if len(sites) != expected_sites:
                return refuse(
                    f"control {case!r} counted {len(sites)} isinstance(x, dict) site(s), "
                    f"expected the hand-written {expected_sites}: {sites}"
                )

    for current, ceiling, inventory, expected, why in _ratchet_control_cases():
        produced = ratchet_findings(current, ceiling, inventory)
        if len(produced) != expected:
            return refuse(
                f"ratchet control (current={current}, ratchet={ceiling}, inventory={inventory}) "
                f"produced {len(produced)} finding(s), expected {expected} ({why}): {produced}"
            )
    return None


def _fixture_exclusion_is_load_bearing() -> str | None:
    """The tracked fixture tree must hold input this analyser WOULD flag.

    Asserting only "no fixture path entered the production scan" credits the
    exclusion for an absence it may not have caused: an empty or conforming
    fixture tree satisfies it trivially. `gate_fixtures/conforming/pkg` carries a
    `@dataclass` config root, so running the analyser over it must produce a
    clause-1 finding -- which is what makes the exclusion in `python_sources()`
    load-bearing rather than decorative.
    """
    fixture_pkg = FIXTURE_ROOT / "conforming" / "pkg"
    if not fixture_pkg.is_dir():
        return f"the tracked fixture package {fixture_pkg} is missing"
    findings, _ = analyse(fixture_pkg)
    if not findings:
        return (
            f"{fixture_pkg} produces no finding under this analyser, so excluding it from the "
            f"production population excludes nothing and is credited for an absence it did "
            f"not cause"
        )
    return None


# ---------------------------------------------------------------------------


def main() -> int:
    failed = _self_test()
    if failed is not None:
        return failed

    packages = source_roots()
    if len(packages) < MIN_PACKAGES:
        return refuse(
            f"src/ under {PACKAGE_ROOT} holds {len(packages)} importable package(s), floor is "
            f"{MIN_PACKAGES}, so there is no source tree to judge"
        )

    # --- the fixture exclusion, asserted in BOTH directions ----------------
    production = [f for package in packages for f in python_sources(package)]
    leaked = [p for p in production if FIXTURE_ROOT in p.parents]
    if leaked:
        return refuse(f"gate fixtures leaked into the production population: {leaked}")
    problem = _fixture_exclusion_is_load_bearing()
    if problem is not None:
        return refuse(problem)

    findings = []
    modules = 0
    pydantic_modules = 0
    schema_classes = 0
    real_modules = 0
    sites: list[str] = []
    env_readers: list[str] = []
    config_roots: list[str] = []

    for package in packages:
        package_findings, counts = analyse(package)
        findings += [f"{package.name}/{item}" for item in package_findings]
        modules += int(counts["modules"])
        pydantic_modules += int(counts["pydantic_modules"])
        schema_classes += int(counts["schema_classes"])
        real_modules += int(counts["real_adapter_modules"])
        sites += [f"{package.name}/{site}" for site in counts["dict_sites"]]  # type: ignore[union-attr]

        # THE CONFIG-ROOT CLAUSE APPLIES TO PACKAGES THAT READ THE ENVIRONMENT,
        # and to no others. It remains a HARD ERROR IN BOTH DIRECTIONS for those
        # -- never a skip -- because a package that demonstrably reads the
        # environment and exposes no named config root is one whose composition
        # root this gate cannot see, which is different from one that has none.
        roots = list(counts["config_roots"])  # type: ignore[arg-type]
        if not reads_the_environment(package):
            if roots:
                return refuse(
                    f"{package.name} names {roots} but no module in it reads the "
                    f"environment. Either the config root is dead, or this gate's "
                    f"environment detector has stopped matching -- and the second would "
                    f"silently excuse every package from the clause below"
                )
            continue
        env_readers.append(package.name)
        config_roots += [f"{package.name}/{r}" for r in roots]
        if not roots:
            return refuse(
                f"no config root found under {package}: no file named one of "
                f"{sorted(CONFIG_BASENAMES)}, yet a module in it reads the environment. "
                f"This is a package whose composition root this gate cannot see -- not a "
                f"package without one. Name the module config.py or settings.py"
            )
        if len(roots) > 1:
            return refuse(
                f"{len(roots)} config roots found under {package}: {roots}. Two answers to "
                f"'where does this package read its environment' is not a verdict; S2 "
                f"allows one"
            )

    # A scan in which NO package reads the environment is a broken detector, not
    # an estate of pure libraries: every service deployed from this template
    # reads its configuration somewhere. Without this floor the clause above is
    # skipped for every package and the gate reports a clean tree it never asked
    # the question of.
    # WHICH MUTATION REACHES THIS FLOOR, measured rather than assumed, because a
    # floor whose mutation reddens the gate by ANOTHER name is unproven and reads
    # as dead code to the next author. Blinding `reads_the_environment` on a tree
    # that HAS a config root does NOT arrive here: the clause above fires first,
    # naming the package and saying in its own words that the detector may have
    # stopped matching. The path that reaches this line is a tree where no
    # package names a config root AND none reads the environment -- a pure-library
    # estate, which is exactly the tree this gate must refuse rather than pass.
    # Proved on a synthetic 62-module library tree: exit 2, this message by name.
    if not env_readers:
        return refuse(
            f"no package under {PACKAGE_ROOT / 'src'} was seen to read the environment, so "
            f"the config-root clause was applied to none of {[p.name for p in packages]}. "
            f"A detector that has never fired cannot report that every config root is fine"
        )

    if modules < MIN_MODULES:
        return refuse(
            f"read {modules} module(s), fewer than the floor of {MIN_MODULES}. A scan that "
            f"reached nothing reports the same clean tree as one that read everything"
        )
    if pydantic_modules < MIN_PYDANTIC_MODULES:
        return refuse(
            f"resolved a pydantic import in {pydantic_modules} module(s), fewer than the floor "
            f"of {MIN_PYDANTIC_MODULES}. A classifier that has never seen pydantic has proved "
            f"half of nothing by staying quiet about it"
        )

    ceiling, inventory, problems = load_ratchet(service_data_file(RATCHET_FILE))
    findings = [*problems, *findings]
    if not problems:
        findings.extend(ratchet_findings(sites, ceiling, inventory))

    if findings:
        for item in findings:
            print(f"  {item}", file=sys.stderr)
        scanned = ", ".join(p.name for p in packages)
        return finding(f"{len(findings)} pydantic-boundary violation(s) in {scanned}")

    domain_modules = sum(
        1
        for package in packages
        for p in python_sources(package)
        if p.relative_to(package).parts[: len(DOMAIN_PACKAGE)] == DOMAIN_PACKAGE
    )
    # PER-ARM ACCOUNTING. A clause whose subject directory does not exist is a
    # NAMED skip in this line, never an unmentioned zero: "no schema class was
    # wrong" and "there were no schema classes" are the same exit code.
    arms = [
        ("clause 1+5 config root", len(config_roots)),
        ("clause 2 schema class", schema_classes),
        ("clause 3 domain module", domain_modules),
        ("clause 4 real-adapter module", real_modules),
    ]
    executed = [f"{name} x{n}" for name, n in arms if n]
    skipped = [f"{name} (none present)" for name, n in arms if not n]
    total = sum(n for _, n in arms)
    controls = _control_cases()
    print(
        f"check-pydantic-boundaries: PASS {modules} module(s) read, config root(s) "
        f"{config_roots}. {total} clause-arm(s) over 5 clauses: {', '.join(executed) or 'none'}"
        + (f"; SKIPPED (not a pass): {', '.join(skipped)}" if skipped else "; 0 skipped")
        + f", 0 unaccounted. {pydantic_modules} module(s) import pydantic. "
        f"isinstance(x, dict) under {'/'.join(REAL_ADAPTER_PACKAGE)}/: {len(sites)} "
        f"== ratchet {ceiling}, inventory identical. Controls: {len(controls)} trees "
        f"({sum(1 for c in controls if c[1] == 0)} quiet, "
        f"{sum(1 for c in controls if c[1] > 0)} flagged, "
        f"{sum(1 for c in controls if c[1] == -1)} refusing) + "
        f"{len(_ratchet_control_cases())} ratchet arms."
    )
    return GREEN


if __name__ == "__main__":
    raise SystemExit(main())
