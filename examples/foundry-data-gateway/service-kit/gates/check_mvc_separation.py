#!/usr/bin/env python3
"""§2: Model, View, Controller and Schema are SEPARATE FILES, always.

Every HTTP resource gets four files:

    domain/model/<entity>.py
    transport/http/views/<resource>_view.py
    transport/http/controllers/<resource>_controller.py
    transport/http/schemas/<resource>_schema.py

TWO clauses, each a finding:

1. **A controller must have all four.** The resource is discovered from the
   controller filename -- ENUMERATED from `transport/http/controllers/*.py`,
   never a hardcoded resource list, so a resource added tomorrow is covered by
   default rather than by somebody remembering to register it.
2. **A controller may not shape a response inline.** A handler that builds a
   dict literal, or runs a comprehension over domain objects, has taken the
   presenter's job -- and the wire shape stops being reviewable in one place.
   Delegating to a view (`SomethingView.one(...)`) is the conforming form.

THE VIEW IS RESOLVED BY IMPORT, NOT BY FILENAME, and that is this gate's most
recently corrected mistake. Deriving `<resource>_view.py` from the controller
stem assumes one view file per controller, named after it. Measured across the
estate's 35 controllers, that assumption holds for 18 and fails for 13 whose
view exists under another name -- a plural controller presenting a singular
view, or several controllers sharing one presenter because the thing they
present is the same thing. `references_controller` presenting `MessageView` is
not a §2 violation; a reference IS a transcript entry, and that is recorded in
the service's own `domain/model/reference_envelope.py`. Flagging those 13 is a
false red naming a real file -- the worse direction, because the obvious
response is to create a duplicate presenter rather than doubt the gate.

So the gate asks what the controller IMPORTS from `transport/http/views/` and
resolves that name to the file defining it. A controller that imports no view
at all, and has no `<resource>_view.py` either, is still a finding: measured,
that is 4 controllers, and they are genuinely presenter-less.

WHAT THIS GATE DELIBERATELY DOES NOT COVER: probe endpoints. A probe answers a
question about the PROCESS. It has no domain model, no use case and no
tenant-visible payload, and demanding a `health` entity would be a false red of
the same kind.

The exemption is DERIVED FROM THE ROUTES, never from the filename. A controller
is a probe controller when EVERY route it declares is a probe path; one that
serves `/healthz` and `/v1/widgets` is not exempt, which is the case a
name-based list would get wrong. Clause 2 still applies to it in full: a probe
that shapes its own body is the same defect as any other handler doing so.

Exit codes: `scripts/gate_exit_codes.py`.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_common import (  # noqa: E402
    EXPECTED_MARKED_FIXTURE_FILES,
    FIXTURE_ROOT,
    MIN_PACKAGES,
    marker_count,
    service_data_file,
    source_roots,
    unmarked_flagged_files,
)
from gate_exit_codes import GREEN, finding, refuse, skip  # noqa: E402

#: Every transport/http file carries a role suffix, so a basename is unique
#: across the three directories. Views already did; controllers and schemas
#: did not, and `controllers/widgets.py` vs `schemas/widgets.py` was the same
#: basename twice.
CONTROLLER_SUFFIX = "_controller"
SCHEMA_SUFFIX = "_schema"
VIEW_SUFFIX = "_view"
CONTROLLER_DIR = ("transport", "http", "controllers")
VIEW_DIR = ("transport", "http", "views")
SCHEMA_DIR = ("transport", "http", "schemas")
MODEL_DIR = ("domain", "model")

ROUTE_DECORATORS = frozenset({"get", "post", "put", "patch", "delete", "api_route"})

#: A route path that asks about the PROCESS rather than about a resource.
#: `/healthz` and `/readyz` are the legacy names this estate's deployed probe
#: configuration and `docker/healthcheck.sh` already use; renaming them would
#: take an instance out of rotation at the moment of the deploy.
#: `/startupz` was missing here until it was MEASURED. It is the third sibling of
#: `/healthz` and `/readyz`, and `STARTUP_PATH` was already declared below -- so
#: wherever a service registers its probes through the single-sourced constants
#: the omission is invisible, and it fires only where the paths are written as
#: literals. That is three services in this estate, each of whose probe
#: controller was flagged for a missing model link: a false red naming a real
#: file, and the obvious response to it is to write a `health` domain entity
#: that should not exist. Widened rather than renaming a deployed probe path,
#: which would take an instance out of rotation at the moment of the deploy.
PROBE_PATHS = ("/health/", "/healthz", "/readyz", "/startupz")

#: The NAMES the probe paths are declared under in `ports/`. A route decorator
#: may be given a constant rather than a literal -- and in this estate it now is,
#: because the paths were single-sourced so `runtime/` and `transport/` could
#: not drift. A matcher that reads only `ast.Constant` went blind at exactly that
#: moment and silently un-exempted the probe controller, which is the shape this
#: file already records for the model-link clause: the fix is to widen the
#: matcher, never to put the literals back.
PROBE_PATH_NAMES = (
    "LIVE_PATH",
    "READY_PATH",
    "STARTUP_PATH",
    "LEGACY_LIVE_PATH",
    "LEGACY_READY_PATH",
)

#: The submodules a VIEW may present FROM. Widened from `domain` alone, and the
#: widening is the rule rather than a convenience: a presenter turns an INNER
#: result into a wire shape, and in these services those results are named in
#: `application` (`GatewayResult`, `ProfiledCompletionResult`) and in `ports`
#: (`ToolResult`, `EmbeddingProfile`) as often as in `domain`. What the clause is
#: really refusing is a view that names NOTHING but `transport` -- a presenter
#: whose input is already a wire type has not presented anything, it has copied.
#:
#: Widened rather than reworded: the views this first flagged are correct, and
#: rewriting them to mention `domain` would have turned the control into
#: decoration and left the matcher wrong.
INNER_LAYERS_A_VIEW_MAY_PRESENT = ("domain", "application", "ports")

#: Service-owned, OUTSIDE the kit: a router with no resource is a fact about one
#: service's surface, so the kit must not carry the list. A 501 catch-all is the
#: measured case -- it has no resource, no model and no presenter, and every file
#: it could honestly be given to satisfy clause 1 would be fiction. Writing
#: `deferred_view.py` to clear the "no view" finding ADDS the model-link one,
#: so the clause cannot be satisfied by any file a service can honestly write.
#:
#: DECLARED, never inferred. Deciding from the handler's status code or its name
#: that it "is really a catch-all" would be a heuristic over intent, which this
#: estate has recorded being wrong in both directions. It fails closed on both
#: sides: an unregistered controller is still a finding, and an entry naming no
#: live controller is a hard error, so the file cannot rot into fiction.
EXEMPTIONS_FILE = service_data_file("mvc_exemptions.yaml")

#: HAND-WRITTEN. Never derived from the fixture: a count read off the artifact
#: under test makes the assertion `n == n`, true for any detector including a
#: blinded one.
#: 2 -> 3 when `offending/pkg/transport/http/controllers/widgets_controller.py`
#: was planted for the MODEL-LINK clause. That clause had no fixture arm, and
#: measurement showed what that cost: regressing the call site back to the bare
#: substring -- and even deleting its inline control outright -- left this gate
#: GREEN. The inline controls exercise `_presents_an_inner_type` directly, so
#: they prove the helper is correct and are structurally blind to whether
#: `analyse()` still calls it. A unit test cannot see its own caller; only a
#: fixture the production scan reads can.
EXPECTED_OFFENDING_FINDINGS = 3
EXPECTED_CONFORMING_FINDINGS = 0
#: The offending controller the exemption control exempts, and what must survive
#: it. 3 -> 2: its clause-1 finding goes, its dict-literal one stays. Both
#: hand-written, for the same reason as every other expectation in this file.
EXEMPTION_CONTROL_CONTROLLER = "transport/http/controllers/gadgets_controller.py"
EXPECTED_EXEMPTED_FINDINGS = 2
#: Two-sided floor: the gate must have found controllers AND handlers inside
#: them. A scan that resolved zero handlers reports a clean tree it never read.
MIN_CONTROLLERS = 1
MIN_HANDLERS = 3

#: The view-by-import resolver, controlled in both directions BEFORE any verdict.
#: The second case is the one this clause exists for -- a controller presenting a
#: view that is NOT named after it -- and the last two are the floor the module
#: docstring promises: a controller that imports no view is still a finding, so a
#: future widening cannot quietly take the genuine findings to zero.
VIEW_RESOLUTION_CONTROLS = (
    ("from pkg.transport.http.views import GadgetView\n", ["GadgetView"]),
    ("from pkg.transport.http.views import MessageView\n", ["MessageView"]),
    ("from pkg.transport.http.views.gadgets_view import GadgetView\n", ["GadgetView"]),
    ("from pkg.application import RegisterGadget\n", []),
    ("import json\n", []),
)

#: The model-link clause, controlled in both directions. A view that presents an
#: inner-layer type passes; one that names only transport, or only says the word
#: in prose, does not. The fifth case is the defect this clause replaced
#:: a bare substring over raw source was cleared by the word
#: "domain" appearing in a docstring, so the cheapest way to pass the clause was
#: to mention the layer in a comment.
MODEL_LINK_CONTROLS = (
    ("from pkg.domain import Widget\n", True),
    ("from pkg.application import GatewayResult\n", True),
    ("from pkg.ports import ToolResult\n", True),
    ("from pkg.transport.http.schemas.widgets import WidgetOut\n", False),
    ('"""This view presents a domain type."""\n', False),
    ("import json\n", False),
)

#: The probe exemption, controlled in both directions BEFORE any verdict.
PROBE_EXEMPTION_CONTROLS = (
    ("@r.get('/health/live')\ndef h():\n    return V.live()\n", True),
    ("@r.get('/healthz')\ndef h():\n    return V.up()\n", True),
    ("@r.get('/readyz')\ndef h():\n    return V.ready()\n", True),
    # `/startupz` written as a LITERAL -- the form three services use, and the
    # one the constant-only estate never exercised.
    ("@r.get('/startupz')\ndef h():\n    return V.started()\n", True),
    (
        "@r.get('/healthz')\ndef a():\n    return V.up()\n"
        "@r.get('/startupz')\ndef b():\n    return V.started()\n"
        "@r.get('/readyz')\ndef c():\n    return V.ready()\n",
        True,
    ),
    # a MIXED controller is not exempt -- the resource route is the thing the
    # gate exists to hold, and a name-based exemption would have let it through
    (
        "@r.get('/health/live')\ndef h():\n    return V.live()\n"
        "@r.post('/v1/widgets')\ndef w():\n    return V.one()\n",
        False,
    ),
    ("@r.post('/v1/completions')\ndef c():\n    return V.one()\n", False),
    # a controller that registers nothing is not a probe controller
    ("def build_router(x):\n    return x\n", False),
    # A route given a CONSTANT, which is what single-sourcing the paths produced.
    ("@r.get(LIVE_PATH)\ndef h():\n    return V.live()\n", True),
    ("@r.get(LEGACY_READY_PATH)\ndef h():\n    return V.ready()\n", True),
    # ...and a constant that is NOT a declared probe path is not a probe route.
    ("@r.get(WIDGETS_PATH)\ndef h():\n    return V.one()\n", False),
)


def _presents_an_inner_type(source: str) -> bool:
    """True when the view imports from at least one inner submodule.

    Reads the IMPORTS, not the raw text: a view whose docstring says the word
    "domain" has not imported anything, and a prose match would make the cheapest
    way to pass this clause "mention the layer in a comment".
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        parts = node.module.split(".")
        if len(parts) >= 2 and parts[1] in INNER_LAYERS_A_VIEW_MAY_PRESENT:
            return True
    return False


def _imported_view_names(source: str) -> list[str]:
    """The view class names this controller imports, in source order.

    Matches the views PACKAGE (`...http.views`) and a view MODULE inside it
    (`...http.views.gadgets_view`), because a controller may import either and
    the choice is a style question rather than a §2 one.
    """
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        module = node.module
        views_package = ".".join(VIEW_DIR)
        if module.endswith(views_package) or f"{views_package}." in module:
            names.extend(alias.name for alias in node.names)
    return names


def _view_file_defining(views_dir: Path, name: str) -> Path | None:
    """The view file that DEFINES `name`, or None.

    Resolved by reading the files rather than by importing them: this gate runs
    over fixture trees that are not importable, and an import here would make it
    depend on the package under test being installable.
    """
    if not views_dir.is_dir():
        return None
    for path in sorted(views_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            tree = ast.parse(path.read_text())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == name:
                return path
    return None


def _route_paths(tree: ast.AST) -> list[str]:
    """Every path this module registers a route for."""
    paths = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in ROUTE_DECORATORS):
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                paths.append(argument.value)
            elif isinstance(argument, ast.Name) and argument.id in PROBE_PATH_NAMES:
                # Resolved by NAME against the declared probe constants rather
                # than by importing the module: this gate runs over fixture trees
                # that are not importable, and an import here would make the gate
                # depend on the package under test being installable.
                paths.append(PROBE_PATHS[0])
    return paths


def is_probe_controller(tree: ast.AST) -> bool:
    """True only when EVERY route in the module is a probe path.

    "Every", not "any": a controller that mixes a probe with a resource route is
    not exempt, because the resource route is exactly the thing this gate exists
    to hold. A controller with no routes at all is not a probe controller either
    -- it is a controller that registers nothing, which is its own problem.
    """
    paths = _route_paths(tree)
    return bool(paths) and all(
        any(path.startswith(prefix) or path == prefix for prefix in PROBE_PATHS) for path in paths
    )


def _route_handlers(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    handlers = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr in ROUTE_DECORATORS
            ):
                handlers.append(node)
                break
    return handlers


def _shapes_inline(handler: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Does this handler build its own response payload?

    A dict LITERAL or a comprehension in the return position is the presenter's
    job. A `Something(**View.one(x))` call is not -- the shaping happened in the
    view and the schema is validating it -- so the check looks at what is being
    RETURNED, not at whether a dict appears anywhere in the body.
    """
    for node in ast.walk(handler):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        value = node.value
        if isinstance(value, ast.Dict) and value.keys:
            return "returns a dict literal"
        if isinstance(value, (ast.ListComp, ast.DictComp, ast.SetComp)):
            return "returns a comprehension"
        if isinstance(value, ast.Call):
            for argument in list(value.args) + [kw.value for kw in value.keywords]:
                if isinstance(argument, (ast.ListComp, ast.DictComp, ast.SetComp)):
                    return "passes a comprehension into its response"
    return None


def load_exemptions() -> tuple[dict[str, dict[str, str]], list[str]]:
    """The service's declared non-resource routers, and anything wrong with them.

    Parsed without PyYAML, which the gates cannot depend on: they run on a bare
    interpreter in every service. The subset is the one
    `check_test_first.py` already reads -- a top-level key, then indented
    `reason:` and `bead:` -- so a service has one shape of exemption file to
    learn rather than two.

    An entry missing either field is a PROBLEM rather than a silent partial
    entry: a declared exemption whose reason is blank is a hole with a name.
    """
    entries: dict[str, dict[str, str]] = {}
    if not EXEMPTIONS_FILE.is_file():
        return entries, []
    current: str | None = None
    for line in EXEMPTIONS_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" ") and stripped.endswith(":"):
            current = stripped[:-1].strip("\"'")
            entries[current] = {}
        elif current is not None:
            for field in ("reason", "bead"):
                if stripped.startswith(f"{field}:"):
                    entries[current][field] = stripped.split(":", 1)[1].strip().strip("\"'")
    problems = [
        f"{EXEMPTIONS_FILE.name}: {key!r} declares no {field}"
        for key, value in sorted(entries.items())
        for field in ("reason", "bead")
        if not value.get(field)
    ]
    return entries, problems


def analyse(root: Path, exempt: frozenset[str] = frozenset()) -> tuple[list[str], int, int]:
    """Return (findings, controllers_examined, handlers_examined).

    `exempt` holds controller paths, relative to `root`, that the service has
    DECLARED to be non-resource routers -- a 501 catch-all being the measured
    case. It excuses CLAUSE 1 ONLY. The controller is still enumerated, still
    counted, and clause 2 still applies to every handler in it: a catch-all that
    shapes its own body is the same defect as any other handler doing so. An
    exemption that silenced the whole file would be a way to delete a controller
    from the gate's population by writing a sentence about it.
    """
    controllers_dir = root.joinpath(*CONTROLLER_DIR)
    if not controllers_dir.is_dir():
        return ([f"{'/'.join(CONTROLLER_DIR)} does not exist"], 0, 0)

    findings: list[str] = []
    controllers = 0
    handlers = 0
    views_dir = root.joinpath(*VIEW_DIR)
    for path in sorted(controllers_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        controllers += 1
        # The RESOURCE is the controller stem with its role suffix removed. A
        # controller filename that omits the suffix is a FINDING rather than a
        # silent pass, or a half-migrated tree would read as conforming.
        if not path.stem.endswith(CONTROLLER_SUFFIX):
            findings.append(
                f"{path.relative_to(root)}: controller filename must end in "
                f"'{CONTROLLER_SUFFIX}.py' so no two transport/http files share a basename"
            )
            continue
        resource = path.stem[: -len(CONTROLLER_SUFFIX)]
        relative = path.relative_to(root)
        source = path.read_text()
        tree = ast.parse(source)
        probe_only = is_probe_controller(tree)

        # Clause 1: all four files exist, for THIS resource.
        #
        # The VIEW is resolved by IMPORT (see the module docstring): whichever
        # view the controller actually presents through is the one whose model
        # link is checked, so a shared or differently-named presenter is not a
        # violation. The filename-derived path is still honoured as a fallback,
        # which keeps a controller that declares its view only by convention
        # covered. The MODEL is not derivable either way and deliberately is not
        # guessed: a resource may present several entities, and singularising
        # `gadgets` to `gadget` would be a heuristic over English -- the same
        # coincidence-of-English trap .claude/CLAUDE.md records twice.
        schema_path = root.joinpath(*SCHEMA_DIR, f"{resource}{SCHEMA_SUFFIX}.py")
        derived_view = root.joinpath(*VIEW_DIR, f"{resource}{VIEW_SUFFIX}.py")
        view_path = None
        for name in _imported_view_names(source):
            view_path = _view_file_defining(views_dir, name)
            if view_path is not None:
                break
        if view_path is None and derived_view.is_file():
            view_path = derived_view

        missing = []
        if not probe_only and str(relative) not in exempt:
            if view_path is None:
                missing.append(
                    f"view (imports none from {'/'.join(VIEW_DIR)}/, "
                    f"and no {resource}{VIEW_SUFFIX}.py)"
                )
            if not schema_path.is_file():
                missing.append(f"schema ({'/'.join(SCHEMA_DIR)}/{resource}{SCHEMA_SUFFIX}.py)")
            models = root.joinpath(*MODEL_DIR)
            entities = (
                []
                if not models.is_dir()
                else [p for p in models.glob("*.py") if p.name != "__init__.py"]
            )
            if not entities:
                missing.append(f"model ({'/'.join(MODEL_DIR)}/<entity>.py)")
            elif view_path is not None and not _presents_an_inner_type(view_path.read_text()):
                missing.append(f"model link ({view_path.name} presents no inner type)")
        if missing:
            findings.append(
                f"{relative}: resource {resource!r} has no {sorted(missing)}; §2 requires "
                "model, view, controller and schema to be separate files"
            )

        for handler in _route_handlers(tree):
            handlers += 1
            # Clause 2: no inline response shaping. It applies to a probe
            # controller in full -- a probe that shapes its own body is the same
            # defect as any other handler doing so.
            reason = _shapes_inline(handler)
            if reason is not None:
                findings.append(
                    f"{relative}:{handler.lineno}: handler {handler.name!r} {reason}; "
                    "response shaping belongs in transport/http/views/"
                )
    return findings, controllers, handlers


def _control_failures() -> list[str]:
    """Every inline control, run in BOTH directions before any verdict.

    A detector never shown to fire cannot report a measured negative, and one
    never shown to stay quiet cannot report a measured positive. These run every
    invocation rather than in a test, because the gate is copied into services
    where this file's own test suite does not travel with it.
    """
    failures = []
    for source, expected in PROBE_EXEMPTION_CONTROLS:
        actual = is_probe_controller(ast.parse(source))
        if actual != expected:
            failures.append(f"probe exemption control: {source!r} -> {actual}, expected {expected}")
    for source, expected in MODEL_LINK_CONTROLS:
        actual = _presents_an_inner_type(source)
        if actual != expected:
            failures.append(f"model link control: {source!r} -> {actual}, expected {expected}")
    for source, expected_names in VIEW_RESOLUTION_CONTROLS:
        actual_names = _imported_view_names(source)
        if actual_names != expected_names:
            failures.append(
                f"view resolution control: {source!r} -> {actual_names}, expected {expected_names}"
            )
    return failures


def main() -> int:
    packages = source_roots()
    if len(packages) < MIN_PACKAGES:
        return refuse(
            f"{len(packages)} package(s) found under src/, floor is {MIN_PACKAGES}. "
            "A glob that matched nothing is a broken scan, not a clean repository."
        )
    # A package with no `transport/http/controllers/` has no HTTP surface, so §2
    # has nothing to hold in it. It is excluded by the ABSENCE of the directory
    # and never by name, so a library package is skipped for the right reason and
    # one that grows a surface tomorrow is covered by default rather than by
    # somebody remembering to register it.
    roots = [p for p in packages if p.joinpath(*CONTROLLER_DIR).is_dir()]
    if not roots:
        return skip(
            f"none of the {len(packages)} package(s) under src/ declares "
            f"{'/'.join(CONTROLLER_DIR)}/; there is no HTTP surface to MVC-check"
        )

    failures = _control_failures()
    if failures:
        return refuse(
            "the gate's own controls disagree with it, so no verdict it produces can be "
            f"trusted: {failures}"
        )

    conforming_pkg = FIXTURE_ROOT / "conforming" / "pkg"
    offending_pkg = FIXTURE_ROOT / "offending" / "pkg"
    if not conforming_pkg.is_dir() or not offending_pkg.is_dir():
        return refuse(f"a gate fixture is missing under {FIXTURE_ROOT}")

    conforming, _c_controllers, _c_handlers = analyse(conforming_pkg)
    if len(conforming) != EXPECTED_CONFORMING_FINDINGS:
        return refuse(
            f"the CONFORMING fixture produced {len(conforming)} finding(s), expected "
            f"{EXPECTED_CONFORMING_FINDINGS}: {conforming}. Widen the matcher -- never reword "
            "the fixture, which would turn the control into decoration."
        )
    offending, _o_controllers, _o_handlers = analyse(offending_pkg)
    if len(offending) != EXPECTED_OFFENDING_FINDINGS:
        return refuse(
            f"the OFFENDING fixture produced {len(offending)} finding(s), expected the "
            f"hand-written {EXPECTED_OFFENDING_FINDINGS}: {offending}"
        )
    marked = marker_count()
    if marked != EXPECTED_MARKED_FIXTURE_FILES:
        return refuse(
            f"{marked} offending fixture file(s) carry the marker, expected "
            f"{EXPECTED_MARKED_FIXTURE_FILES}; the control has been neutralised"
        )
    stray = unmarked_flagged_files(offending_pkg, offending)
    if stray:
        return refuse(f"the gate flagged unmarked fixture file(s) {stray}; the fixture drifted")

    # The exemption, controlled against the OFFENDING fixture before any verdict.
    # Exempting `gadgets_controller` must remove its clause-1 finding and LEAVE
    # its clause-2 one: the handler still returns a dict literal. One arm, two
    # properties -- that the exemption is consumed at all, and that it does not
    # excuse inline shaping. A control that only counted the drop would pass
    # just as happily on an exemption that silenced the whole file.
    exempted, _e_controllers, _e_handlers = analyse(
        offending_pkg, frozenset({EXEMPTION_CONTROL_CONTROLLER})
    )
    if len(exempted) != EXPECTED_EXEMPTED_FINDINGS:
        return refuse(
            f"exempting {EXEMPTION_CONTROL_CONTROLLER!r} left {len(exempted)} finding(s), "
            f"expected the hand-written {EXPECTED_EXEMPTED_FINDINGS}: {exempted}"
        )
    if not any("dict literal" in item for item in exempted):
        return refuse(
            "the exemption silenced clause 2 as well; it must excuse the four-file clause "
            "ONLY, or a sentence in a registry would delete a controller from this gate"
        )

    declared, problems = load_exemptions()
    if problems:
        return refuse(f"the exemption registry is malformed: {problems}")
    live = {
        str(p.relative_to(r))
        for r in roots
        for p in sorted(r.joinpath(*CONTROLLER_DIR).glob("*.py"))
        if p.name != "__init__.py"
    }
    stale = sorted(set(declared) - live)
    if stale:
        return refuse(
            f"{EXEMPTIONS_FILE.name} exempts {stale}, which name no controller under "
            f"{'/'.join(CONTROLLER_DIR)}/. An exemption that outlives its subject reads as "
            "coverage; delete the entry or restore the file"
        )

    findings: list[str] = []
    controllers = handlers = 0
    for r in roots:
        package_findings, package_controllers, package_handlers = analyse(r, frozenset(declared))
        # Prefix the package name: two packages can hold the same relative path,
        # and an unprefixed finding would read as one file rather than two.
        findings += [f"{r.name}/{item}" for item in package_findings]
        controllers += package_controllers
        handlers += package_handlers
    if controllers < MIN_CONTROLLERS or handlers < MIN_HANDLERS:
        return refuse(
            f"{controllers} controller(s) and {handlers} handler(s) examined; floors are "
            f"{MIN_CONTROLLERS}/{MIN_HANDLERS}. An empty scan reports a clean tree it never read."
        )
    if findings:
        for item in findings:
            print(f"  {item}", file=sys.stderr)
        scanned = ", ".join(r.name for r in roots)
        return finding(f"{len(findings)} MVC separation violation(s) in {scanned}")
    controls = (
        len(PROBE_EXEMPTION_CONTROLS) + len(MODEL_LINK_CONTROLS) + len(VIEW_RESOLUTION_CONTROLS)
    )
    #: Named in PARTS and summed, then the total checked against its own parts:
    #: a coverage line whose arithmetic nobody checks is the defect the per-arm
    #: accounting rule exists to prevent, and this gate's own first line read
    #: "4 arms, plus 20 inline controls; 24 executed" while two registry arms ran
    #: unnamed.
    fixture_arms = 4
    registry_arms = 2  # malformed entry, and an entry naming no live controller
    exemption_arms = 2  # the count drops, and clause 2 survives the exemption
    total = fixture_arms + controls + registry_arms + exemption_arms
    assert total == 4 + controls + 4, "the coverage line disagrees with its own parts"
    print(
        f"PASS: {len(roots)} of {len(packages)} package(s) with a surface, "
        f"{controllers} controller(s), {handlers} handler(s), "
        f"{len(declared)} declared exemption(s); {fixture_arms} fixture arms + {controls} "
        f"inline controls + {exemption_arms} exemption arms + {registry_arms} registry arms "
        f"= {total} arms; {total} executed, 0 skipped, 0 unaccounted."
    )
    return GREEN


if __name__ == "__main__":
    raise SystemExit(main())
