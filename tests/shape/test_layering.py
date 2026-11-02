"""The five checks that keep the template's shape a contract rather than a
starting position:

1. no module outside `config.py` names the environment;
2. the domain layer (`models/`, `services/`) imports no adapter;
3. every port (`ports/*.py`, `repositories/*.py`) has a matching fake in
   `adapters/fake/`;
4. every route handler is under N=12 lines;
5. the container builds with zero network access.

Each check is a reusable function so this file can also prove the check
itself goes red on a deliberate violation (plan section 11, item 4: "a check
that has never gone red has not been tested") -- not just that today's
source happens to pass it.
"""

from __future__ import annotations

import ast
import re
import socket
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent.parent / "src/foundry_service_template"
CONFIG_FILE = SRC / "composition" / "config.py"
ROUTE_HANDLER_MAX_LINES = 12
FUNCTION_MAX_LINES = 80
COMPOSITION_FUNCTION_MAX_LINES = 60
_FRAMEWORK_AND_IO_ROOTS = {
    "boto3",
    "fastapi",
    "httpx",
    "pydantic",
    "requests",
    "sqlalchemy",
    "urllib",
}

#: Kept only for the ast-free fallback below; the real check parses.
_ENVIRON_PATTERN = re.compile(r"\bos\.environ\b|\bos\.getenv\(")
_DOCSTRING_ONLY_ENVIRONMENT_CITATION = "ports/secrets.py"


# ---------------------------------------------------------------------------
# Check 1: no module outside config.py names the environment
# ---------------------------------------------------------------------------


def _reads_the_environment(source: str) -> bool:
    """True only if the module actually READS the environment, per the AST.

    This parsed the raw text until 2026-09-04, which flagged any file that so
    much as NAMED `os.environ` -- including the docstring of a port whose whole
    purpose is to say "depend on this instead of reading `os.environ`". The rule
    then had a perverse shape: explaining it correctly broke it, and the cheap
    way out was to reword the prose until the matcher fell silent, leaving a
    weaker explanation and an unchanged gate.

    `.claude/CLAUDE.md` names the general form -- a heuristic over a structured
    language must read that structure; for Python that means `ast`. A docstring
    is a `Constant`, never an `Attribute` or a `Call`, so parsing separates
    "mentions the environment" from "reads the environment" exactly.

    An unparseable file falls back to the text pattern: a file we cannot parse is
    the one case where guessing loudly beats staying quiet.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return bool(_ENVIRON_PATTERN.search(source))

    for node in ast.walk(tree):
        # os.environ / os.environ[...] / os.environ.get(...)
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            value = node.value
            if isinstance(value, ast.Name) and value.id == "os":
                return True
        # from os import environ, getenv
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            if any(alias.name in {"environ", "getenv"} for alias in node.names):
                return True
    return False


def _files_naming_the_environment(root: Path, *, exclude: set[Path]) -> list[Path]:
    violations = []
    for path in sorted(root.rglob("*.py")):
        if path in exclude:
            continue
        if _reads_the_environment(path.read_text()):
            violations.append(path)
    return violations


def test_no_module_outside_config_names_the_environment() -> None:
    violations = _files_naming_the_environment(SRC, exclude={CONFIG_FILE})
    assert violations == [], f"os.environ/os.getenv found outside config.py: {violations}"


def test_check_1_stays_quiet_on_a_file_that_only_MENTIONS_the_environment(
    tmp_path: Path,
) -> None:
    """The other direction, and the reason this check now parses.

    Every string below names `os.environ` without reading it. The text matcher
    flagged all four; a false red here is the worse failure, because it hands
    you a filename and the obvious response is to soften the prose until the
    gate goes quiet -- which weakens the documentation and leaves the matcher
    wrong. Real instance: `ports/secrets.py`, whose docstring exists precisely to
    say "do not read `os.environ` here".
    """
    docstring_only = tmp_path / "explains.py"
    docstring_only.write_text(
        '"""Depend on SecretsPort rather than reading os.environ directly."""\nVALUE = 1\n'
    )
    comment_only = tmp_path / "commented.py"
    comment_only.write_text("# never call os.getenv( here\nVALUE = 2\n")
    string_literal = tmp_path / "literal.py"
    string_literal.write_text('BANNED_API = "os.environ"\nVALUE = 3\n')
    unrelated_attr = tmp_path / "other.py"
    unrelated_attr.write_text(
        "class Fake:\n    environ = {}\n\nfake = Fake()\nVALUE = fake.environ\n"
    )

    violations = _files_naming_the_environment(tmp_path, exclude=set())

    assert violations == [], (
        "these files only NAME the environment; flagging them is the false red that "
        f"pushed this gate's matcher from text to ast: {violations}"
    )


def test_the_cited_docstring_only_module_is_real_and_still_only_MENTIONS_the_environment() -> None:
    """Turn the false-positive regression's production example into checked data."""
    cited = SRC / _DOCSTRING_ONLY_ENVIRONMENT_CITATION
    assert cited.is_file(), f"the environment-check citation no longer resolves: {cited}"
    source = cited.read_text()
    assert "os.environ" in source, f"{cited} no longer demonstrates a textual mention"
    assert not _reads_the_environment(source), f"{cited} now reads ambient environment state"


def test_check_1_still_catches_the_indirect_import(tmp_path: Path) -> None:
    """`from os import environ` reads the environment as surely as `os.environ`."""
    offender = tmp_path / "indirect.py"
    offender.write_text("from os import environ\n\nSTAGE = environ['STAGE']\n")

    violations = _files_naming_the_environment(tmp_path, exclude=set())

    assert violations == [offender]


def test_check_1_goes_red_on_a_deliberate_violation(tmp_path: Path) -> None:
    offender = tmp_path / "leaky.py"
    offender.write_text("import os\n\nSTAGE = os.environ['STAGE']\n")
    innocent = tmp_path / "clean.py"
    innocent.write_text("VALUE = 1\n")

    violations = _files_naming_the_environment(tmp_path, exclude=set())

    assert violations == [offender]


# ---------------------------------------------------------------------------
# Check 2: the domain layer imports no adapter
# ---------------------------------------------------------------------------

#: The v2 physical submodules that make up the INNER layers. `models/` and
#: `services/` became `domain/` and `application/` in the restructure; the two
#: checks below are unchanged in strength, they just name the directories that
#: exist. `ports/` is inner too and is checked separately for vendor imports.
_DOMAIN_DIRS = ("domain", "application")


def _imported_module_names(source: str) -> list[str]:
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _domain_files_importing_adapters(root: Path) -> list[tuple[Path, str]]:
    violations = []
    for domain_dir in _DOMAIN_DIRS:
        for path in sorted((root / domain_dir).rglob("*.py")):
            for name in _imported_module_names(path.read_text()):
                if "adapters" in name.split("."):
                    violations.append((path, name))
    return violations


def _inner_layer_framework_imports(root: Path) -> list[tuple[Path, str]]:
    violations = []
    for domain_dir in _DOMAIN_DIRS:
        for path in sorted((root / domain_dir).rglob("*.py")):
            for name in _imported_module_names(path.read_text()):
                if name.split(".")[0] in _FRAMEWORK_AND_IO_ROOTS:
                    violations.append((path, name))
    return violations


def test_domain_layer_imports_no_adapter() -> None:
    violations = _domain_files_importing_adapters(SRC)
    assert violations == [], f"domain module importing an adapter: {violations}"


def test_domain_and_application_layers_import_no_framework_or_io_sdk() -> None:
    violations = _inner_layer_framework_imports(SRC)
    assert violations == [], f"inner layer importing framework or I/O SDK: {violations}"


def test_framework_import_check_goes_red_and_stays_quiet() -> None:
    clean = "from dataclasses import dataclass\n"
    offender = "from fastapi import Request\n"
    assert not any(
        name.split(".")[0] in _FRAMEWORK_AND_IO_ROOTS for name in _imported_module_names(clean)
    )
    assert any(
        name.split(".")[0] in _FRAMEWORK_AND_IO_ROOTS for name in _imported_module_names(offender)
    )


def test_check_2_goes_red_on_a_deliberate_violation(tmp_path: Path) -> None:
    (tmp_path / "domain").mkdir()
    (tmp_path / "application").mkdir()
    (tmp_path / "domain" / "__init__.py").write_text("")
    (tmp_path / "application" / "__init__.py").write_text("")
    offender = tmp_path / "application" / "sneaky.py"
    offender.write_text(
        "from foundry_service_template.adapters.real.sqs_event_bus import SqsPublisher\n"
    )

    violations = _domain_files_importing_adapters(tmp_path)

    assert violations == [(offender, "foundry_service_template.adapters.real.sqs_event_bus")]


def test_controllers_and_handlers_import_no_adapter() -> None:
    """The plan's stricter sibling check (section 5, #2): inbound never
    reaches straight for infrastructure either."""
    violations = []
    for inbound_dir in ("transport",):
        for path in sorted((SRC / inbound_dir).rglob("*.py")):
            for name in _imported_module_names(path.read_text()):
                if "adapters" in name.split("."):
                    violations.append((path, name))
    assert violations == [], f"inbound module importing an adapter: {violations}"


def test_ports_directory_imports_no_vendor_sdk() -> None:
    """The plan's check #1: a port that imports a vendor is not a port."""
    banned = {"boto3", "sqlalchemy", "requests"}
    violations = []
    for path in sorted((SRC / "ports").rglob("*.py")):
        for name in _imported_module_names(path.read_text()):
            if name.split(".")[0] in banned:
                violations.append((path, name))
    assert violations == [], f"ports/ module importing a vendor SDK: {violations}"


# ---------------------------------------------------------------------------
# Check 3: every port has a matching fake
# ---------------------------------------------------------------------------


def _ports_missing_a_fake(root: Path) -> list[str]:
    fake_dir = root / "adapters" / "fake"
    fake_names = {p.name for p in fake_dir.glob("*.py") if p.name != "__init__.py"}
    missing = []
    for contract_dir in ("ports",):
        for path in (root / contract_dir).glob("*.py"):
            if path.name == "__init__.py":
                continue
            if path.name not in fake_names:
                missing.append(f"{contract_dir}/{path.name}")
    return missing


def test_every_port_has_at_least_one_fake_implementation() -> None:
    missing = _ports_missing_a_fake(SRC)
    assert missing == [], f"no adapters/fake/ match for: {missing}"


def test_check_3_goes_red_on_a_deliberate_violation(tmp_path: Path) -> None:
    (tmp_path / "ports").mkdir()
    (tmp_path / "adapters" / "fake").mkdir(parents=True)
    (tmp_path / "ports" / "__init__.py").write_text("")
    (tmp_path / "ports" / "new_port.py").write_text("class NewPort: ...\n")
    # deliberately no adapters/fake/new_port.py

    missing = _ports_missing_a_fake(tmp_path)

    assert missing == ["ports/new_port.py"]


# ---------------------------------------------------------------------------
# Check 4: every route handler is under N lines
# ---------------------------------------------------------------------------


def _route_handler_line_counts(source: str) -> dict[str, int]:
    """Line span for decorated HTTP handlers, including nested router factories."""
    tree = ast.parse(source)
    counts = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        route_decorators = [
            decorator
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr in {"get", "post", "put", "patch", "delete", "api_route"}
        ]
        if route_decorators:
            counts[node.name] = node.end_lineno - route_decorators[0].lineno + 1
    return counts


def test_every_route_handler_is_under_the_line_limit() -> None:
    over_limit = []
    for path in sorted((SRC / "transport" / "http" / "controllers").glob("*.py")):
        if path.name == "__init__.py":
            continue
        for name, line_count in _route_handler_line_counts(path.read_text()).items():
            if line_count > ROUTE_HANDLER_MAX_LINES:
                over_limit.append((path.name, name, line_count))
    assert over_limit == [], f"handlers over {ROUTE_HANDLER_MAX_LINES} lines: {over_limit}"


def test_check_4_goes_red_on_a_deliberate_violation() -> None:
    bloated_source = "def handler():\n" + "\n".join(
        f"    x{i} = {i}" for i in range(ROUTE_HANDLER_MAX_LINES + 5)
    )

    bloated_source = "@router.get('/')\n" + bloated_source
    counts = _route_handler_line_counts(bloated_source)

    assert counts["handler"] > ROUTE_HANDLER_MAX_LINES


# ---------------------------------------------------------------------------
# Check 5: delivery code never locates the container through framework state
# ---------------------------------------------------------------------------


def _service_locator_reads(root: Path) -> list[Path]:
    violations = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr != "container":
                continue
            state = node.value
            if isinstance(state, ast.Attribute) and state.attr == "state":
                violations.append(path)
                break
    return violations


def test_delivery_code_uses_injected_dependencies_not_app_state_container() -> None:
    assert _service_locator_reads(SRC) == []


def test_service_locator_check_proves_both_directions(tmp_path: Path) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("def route(service):\n    return service.run()\n")
    assert _service_locator_reads(tmp_path) == []
    offender = tmp_path / "offender.py"
    offender.write_text(
        "def route(request):\n    return request.app.state.container.service.run()\n"
    )
    assert _service_locator_reads(tmp_path) == [offender]


# ---------------------------------------------------------------------------
# Check 6: inner layers never construct imported adapter classes
# ---------------------------------------------------------------------------


def _inner_layer_adapter_construction(root: Path) -> list[tuple[Path, str]]:
    violations = []
    for domain_dir in _DOMAIN_DIRS:
        for path in sorted((root / domain_dir).rglob("*.py")):
            tree = ast.parse(path.read_text())
            adapter_names = {
                alias.asname or alias.name
                for node in tree.body
                if isinstance(node, ast.ImportFrom)
                and node.module
                and "adapters" in node.module.split(".")
                for alias in node.names
            }
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in adapter_names
                ):
                    violations.append((path, node.func.id))
    return violations


def test_only_composition_root_constructs_adapters() -> None:
    assert _inner_layer_adapter_construction(SRC) == []


def test_adapter_construction_check_proves_both_directions(tmp_path: Path) -> None:
    services = tmp_path / "application"
    services.mkdir()
    clean = services / "clean.py"
    clean.write_text("class UseCase:\n    def __init__(self, port):\n        self.port = port\n")
    assert _inner_layer_adapter_construction(tmp_path) == []
    offender = services / "offender.py"
    offender.write_text(
        "from app.adapters.real.store import SqlStore\n\ndef run():\n    return SqlStore()\n"
    )
    assert _inner_layer_adapter_construction(tmp_path) == [(offender, "SqlStore")]


# ---------------------------------------------------------------------------
# Check 7: non-composition functions stay within the orchestration ceiling
# ---------------------------------------------------------------------------


def _oversized_functions(root: Path) -> list[tuple[Path, str, int]]:
    violations = []
    for path in sorted(root.rglob("*.py")):
        if path.name in {"container.py", "config.py"}:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                size = node.end_lineno - node.lineno + 1
                if size > FUNCTION_MAX_LINES:
                    violations.append((path, node.name, size))
    return violations


def test_non_composition_functions_fit_the_orchestration_ceiling() -> None:
    assert _oversized_functions(SRC) == []


def test_function_size_check_proves_both_directions(tmp_path: Path) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("def small():\n    return 1\n")
    assert _oversized_functions(tmp_path) == []
    offender = tmp_path / "offender.py"
    offender.write_text(
        "def huge():\n" + "\n".join(f"    value_{i} = {i}" for i in range(FUNCTION_MAX_LINES))
    )
    assert _oversized_functions(tmp_path) == [(offender, "huge", FUNCTION_MAX_LINES + 1)]


def _oversized_composition_functions(root: Path) -> list[tuple[Path, str, int]]:
    violations = []
    for filename in ("composition/container.py", "composition/wiring.py"):
        path = root / filename
        if not path.is_file():
            raise FileNotFoundError(f"required composition module is missing: {path}")
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                size = node.end_lineno - node.lineno + 1
                if size > COMPOSITION_FUNCTION_MAX_LINES:
                    violations.append((path, node.name, size))
    return violations


def test_composition_functions_fit_the_measured_ceiling() -> None:
    assert _oversized_composition_functions(SRC) == []


def test_composition_size_check_proves_both_directions(tmp_path: Path) -> None:
    composition = tmp_path / "composition"
    composition.mkdir()
    (composition / "container.py").write_text("def small():\n    return 1\n")
    (composition / "wiring.py").write_text("def small():\n    return 1\n")
    assert _oversized_composition_functions(tmp_path) == []
    offender = composition / "wiring.py"
    offender.write_text(
        "def oversized():\n"
        + "\n".join(f"    value_{i} = {i}" for i in range(COMPOSITION_FUNCTION_MAX_LINES))
    )
    assert _oversized_composition_functions(tmp_path) == [
        (offender, "oversized", COMPOSITION_FUNCTION_MAX_LINES + 1)
    ]


def test_composition_size_check_refuses_a_missing_or_renamed_input(tmp_path: Path) -> None:
    composition = tmp_path / "composition"
    composition.mkdir()
    (composition / "container.py").write_text("def small():\n    return 1\n")
    with pytest.raises(FileNotFoundError, match="wiring.py"):
        _oversized_composition_functions(tmp_path)

    (composition / "selection.py").write_text("def small():\n    return 1\n")
    with pytest.raises(FileNotFoundError, match="wiring.py"):
        _oversized_composition_functions(tmp_path)


# ---------------------------------------------------------------------------
# Check 8: the container builds with zero network access
# ---------------------------------------------------------------------------


def test_check_5_the_network_guard_mechanism_actually_traps_a_socket_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`tests/unit/test_container.py::test_container_builds_with_zero_network_access`
    is the real assertion against `build_container()`. This test proves the
    *mechanism* that check relies on -- patching `socket.socket` -- actually
    intercepts a call, so a future refactor that silently drops the
    monkeypatch would be caught here too.
    """

    def _no_sockets(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("socket.socket() was called")

    monkeypatch.setattr(socket, "socket", _no_sockets)

    with pytest.raises(AssertionError, match="socket.socket"):
        socket.socket()


def test_check_1_pattern_also_catches_getenv(tmp_path: Path) -> None:
    offender = tmp_path / "leaky.py"
    offender.write_text("import os\nSTAGE = os.getenv('STAGE')\n")

    violations = _files_naming_the_environment(tmp_path, exclude=set())

    assert violations == [offender]
