"""The kit gates resolve relative imports, through one resolver in `gate_common.py`.

One shared resolver. Before this change `check_layering.py` kept an
`ImportFrom` only when `node.level == 0`, so `from ..transport import http` inside
`application/` was invisible: planted in this repository's own source tree, the absolute
spelling produced a finding and the relative spelling printed PASS. `from .. import
transport` and its absolute twin `from foundry_service_template import transport` were
invisible as well, because the statement's module is the package root and the layer
is one of the imported NAMES.

`check_orm_standard.py`, `check_pydantic_boundaries.py` and `check_test_first.py` each
carried their own copy of the same `level == 0` walk. Every arm below drives the
PRODUCTION analyser of one gate over a throwaway package, so the verdict comes from
the code CI runs and not from a re-implementation of it.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

GATES = Path(__file__).resolve().parents[2] / "service-kit" / "gates"
sys.path.insert(0, str(GATES))

gate_common = importlib.import_module("gate_common")
check_layering = importlib.import_module("check_layering")
check_orm_standard = importlib.import_module("check_orm_standard")
check_pydantic_boundaries = importlib.import_module("check_pydantic_boundaries")
check_test_first = importlib.import_module("check_test_first")


def _package(tmp_path: Path, files: dict[str, str]) -> Path:
    """A package `pkg` under `tmp_path/src`, every directory carrying an `__init__.py`."""
    root = tmp_path / "src" / "pkg"
    for relative, body in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        for parent in [path.parent, *path.parent.parents]:
            if parent == root.parent:
                break
            (parent / "__init__.py").touch()
        path.write_text(body)
    return root


# ---------------------------------------------------------------------------
# the resolver itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative, expected",
    [
        ("application/use_case.py", "pkg.application"),
        ("application/__init__.py", "pkg.application"),
        ("transport/http/controllers.py", "pkg.transport.http"),
        ("__init__.py", "pkg"),
    ],
)
def test_package_of_is_pythons_dunder_package(tmp_path: Path, relative: str, expected: str) -> None:
    root = tmp_path / "src"
    assert gate_common.package_of(root / "pkg" / relative, root) == expected


@pytest.mark.parametrize(
    "package, statement, module",
    [
        ("pkg.application", "from ..transport import http", "pkg.transport"),
        ("pkg.application", "from .. import transport", "pkg"),
        ("pkg.application", "from . import sibling", "pkg.application"),
        ("pkg.application", "from .sibling import helper", "pkg.application.sibling"),
        ("pkg.transport.http", "from ...domain.model import Widget", "pkg.domain.model"),
        ("pkg.application", "from pkg.transport import http", "pkg.transport"),
        # Three dots from `pkg.application` climbs above `pkg`: Python raises
        # "attempted relative import beyond top-level package", and the resolver says None.
        ("pkg.application", "from ... import anything", None),
    ],
)
def test_imports_in_resolves_every_spelling_to_one_absolute_module(
    package: str, statement: str, module: str | None
) -> None:
    (found,) = gate_common.imports_in(ast.parse(statement), package)
    assert found.module == module
    assert found.is_from


def test_imports_in_keeps_the_bound_name_of_an_aliased_import() -> None:
    tree = ast.parse("import pkg.transport.http as wire\nfrom ..transport import http as h\n")
    plain, relative = gate_common.imports_in(tree, "pkg.application")
    assert (plain.module, plain.is_from, plain.names) == (
        "pkg.transport.http",
        False,
        (("pkg.transport.http", "wire"),),
    )
    assert (relative.module, relative.names) == ("pkg.transport", (("http", "h"),))


@pytest.mark.parametrize(
    "statement, bound",
    [("import a.b.c", "a"), ("import a.b.c as x", "x"), ("import a", "a")],
)
def test_imports_in_binds_what_python_binds(statement: str, bound: str) -> None:
    """`import a.b.c` binds `a`, not `a.b.c`; the module stays the full dotted name."""
    (found,) = gate_common.imports_in(ast.parse(statement), "pkg")
    assert found.module == statement.split()[1]
    assert found.names == ((statement.split()[1], bound),)


# ---------------------------------------------------------------------------
# check_layering: red on both spellings, quiet on the same layer
# ---------------------------------------------------------------------------

_LAYERED = {
    "application/sibling.py": "def helper():\n    return 1\n",
    "domain/model/widget.py": "class Widget:\n    pass\n",
    "ports/store.py": "",
    "transport/http/controllers.py": "",
}


def _layering_findings(tmp_path: Path, probe: str, **inits: str) -> list[str]:
    """Findings for `probe` planted in `application/probe.py`.

    `inits` overrides a package's `__init__.py` body, keyed by its dotted tail with `_`
    for the root (`_="__all__ = [...]"`, `domain="..."`).
    """
    files = {**_LAYERED, "application/probe.py": probe + "\n"}
    for tail, body in inits.items():
        files["__init__.py" if tail == "_" else f"{tail.replace('.', '/')}/__init__.py"] = body
    root = _package(tmp_path, files)
    findings, *_ = check_layering.analyse(root, is_fixture=True)
    return findings


@pytest.mark.parametrize(
    "probe",
    [
        "from pkg.transport import http",
        "from ..transport import http",
        "from pkg import transport",
        "from .. import transport",
        "import pkg.transport.http as wire",
        "from ..transport import http as wire",
    ],
)
def test_layering_is_red_on_application_reaching_transport_in_every_spelling(
    tmp_path: Path, probe: str
) -> None:
    findings = _layering_findings(tmp_path, probe)
    assert len(findings) == 1, findings
    assert "application imports transport" in findings[0]
    assert findings[0].startswith("application/probe.py:1:")


@pytest.mark.parametrize(
    "probe",
    ["from pkg.domain.model.widget import Widget", "from ..domain.model.widget import Widget"],
)
def test_layering_deep_import_rule_reads_both_spellings(tmp_path: Path, probe: str) -> None:
    findings = _layering_findings(tmp_path, probe)
    assert len(findings) == 1, findings
    assert "deep cross-submodule import pkg.domain.model.widget" in findings[0]


@pytest.mark.parametrize(
    "probe",
    [
        "from . import sibling",
        "from .sibling import helper",
        "from .sibling import helper as h",
        "from .. import domain",
        "from ..domain import model",
        "from ..domain.model import Widget",
        "from pkg import __name__ as package_name",
    ],
)
def test_layering_stays_quiet_on_an_allowed_import_in_either_spelling(
    tmp_path: Path, probe: str
) -> None:
    assert _layering_findings(tmp_path, probe) == []


@pytest.mark.parametrize(
    "probe",
    ["from pkg.domain.model import widget", "from ..domain.model import widget"],
)
def test_layering_deep_import_rule_reads_a_submodule_imported_as_a_member(
    tmp_path: Path, probe: str
) -> None:
    """`from pkg.domain.model import widget` imports the MODULE `pkg.domain.model.widget`.

    QA round 1: the edge stopped at `pkg.domain.model`, a package, so
    the deep-import rule never saw the leaf module the statement actually loads.
    """
    findings = _layering_findings(tmp_path, probe)
    assert len(findings) == 1, findings
    assert "deep cross-submodule import pkg.domain.model.widget" in findings[0]


@pytest.mark.parametrize("probe", ["from pkg.ports import store", "from ..ports import store"])
def test_layering_flags_an_unpublished_leaf_module_imported_as_a_member(
    tmp_path: Path, probe: str
) -> None:
    findings = _layering_findings(tmp_path, probe)
    assert len(findings) == 1, findings
    assert "deep cross-submodule import pkg.ports.store" in findings[0]


@pytest.mark.parametrize("probe", ["from pkg.ports import store", "from ..ports import store"])
def test_layering_accepts_a_leaf_module_its_package_publishes_in_all(
    tmp_path: Path, probe: str
) -> None:
    """The shape of `transport/http/controllers/__init__.py`, which publishes its modules."""
    assert _layering_findings(tmp_path, probe, ports='__all__ = ["store"]\n') == []


#: `ports/__init__.py` bodies that bind `store` themselves, so `from pkg.ports import
#: store` returns that binding, not the sibling module `ports/store.py`.
_BOUND_BY_THE_PACKAGE = {
    "re-exported function": "from .store import store\n",
    "aliased re-export": "from .store import make as store\n",
    "plain assignment": "store = object()\n",
    "function": "def store():\n    return None\n",
}


@pytest.mark.parametrize("probe", ["from pkg.ports import store", "from ..ports import store"])
@pytest.mark.parametrize("init", sorted(_BOUND_BY_THE_PACKAGE))
def test_layering_accepts_a_name_its_package_binds_itself(
    tmp_path: Path, probe: str, init: str
) -> None:
    assert _layering_findings(tmp_path, probe, ports=_BOUND_BY_THE_PACKAGE[init]) == []


@pytest.mark.parametrize("probe", ["from pkg.ports import store", "from ..ports import store"])
def test_layering_still_flags_a_submodule_the_package_only_imports(
    tmp_path: Path, probe: str
) -> None:
    """`from . import store` binds the module itself, so the edge is still the leaf."""
    findings = _layering_findings(tmp_path, probe, ports="from . import store\n")
    assert len(findings) == 1, findings
    assert "deep cross-submodule import pkg.ports.store" in findings[0]


#: `ports/__init__.py` bodies whose `__all__` cannot be read as a literal, so it
#: publishes nothing and the leaf import is still a finding.
_UNREADABLE_ALL = {
    "cleared": '__all__ = ["store"]\n__all__.clear()\n',
    "removed": '__all__ = ["store"]\n__all__.remove("store")\n',
    "reassigned under if": '__all__ = ["store"]\nif False:\n    __all__ = []\n',
    "extended": '__all__ = []\n__all__ += ["store"]\n',
}


@pytest.mark.parametrize("probe", ["from pkg.ports import store", "from ..ports import store"])
@pytest.mark.parametrize("init", sorted(_UNREADABLE_ALL))
def test_layering_does_not_trust_an_all_that_is_touched_again(
    tmp_path: Path, probe: str, init: str
) -> None:
    findings = _layering_findings(tmp_path, probe, ports=_UNREADABLE_ALL[init])
    assert len(findings) == 1, findings
    assert "deep cross-submodule import pkg.ports.store" in findings[0]


#: Root `__init__.py` bodies that each expose `pkg.transport` to `from pkg import *`.
#: QA rounds 1 and 2 found that every attempt to read `__all__`
#: left one of these behind, so a wildcard inside the package is now refused outright.
_ROOT_EXPORTS = {
    "literal": '__all__ = ["transport"]\n',
    "append": '__all__ = []\n__all__.append("transport")\n',
    "conditional": '__all__ = []\nif True:\n    __all__ = ["transport"]\n',
    "alias": 'from . import transport as wire\n__all__ = ["wire"]\n',
    "computed": "__all__ = [name for name in ('transport',)]\n",
    "none": "",
}


@pytest.mark.parametrize("probe", ["from pkg import *", "from .. import *"])
@pytest.mark.parametrize("init", sorted(_ROOT_EXPORTS))
def test_layering_refuses_a_root_wildcard_whatever_the_root_exports(
    tmp_path: Path, probe: str, init: str
) -> None:
    findings = _layering_findings(tmp_path, probe, _=_ROOT_EXPORTS[init])
    assert len(findings) == 1, findings
    assert "wildcard import from pkg inside the package" in findings[0]


@pytest.mark.parametrize(
    "probe, target",
    [
        ("from pkg.domain import *", "pkg.domain"),
        ("from ..domain import *", "pkg.domain"),
        ("from pkg.application.sibling import *", "pkg.application.sibling"),
        ("from .sibling import *", "pkg.application.sibling"),
    ],
)
def test_layering_refuses_a_wildcard_from_any_module_inside_the_package(
    tmp_path: Path, probe: str, target: str
) -> None:
    """Even an allowed target: the edges a wildcard brings in cannot be read from source."""
    findings = _layering_findings(tmp_path, probe, domain='__all__ = ["model"]\n')
    assert len(findings) == 1, findings
    assert f"wildcard import from {target} inside the package" in findings[0]


def test_layering_leaves_a_third_party_wildcard_alone(tmp_path: Path) -> None:
    assert _layering_findings(tmp_path, "from os.path import *") == []


def test_layering_reports_a_relative_import_that_climbs_above_the_package(tmp_path: Path) -> None:
    findings = _layering_findings(tmp_path, "from ... import anything")
    assert len(findings) == 1, findings
    assert "climbs above" in findings[0]


def test_layering_counts_a_relative_cross_submodule_import(tmp_path: Path) -> None:
    root = _package(tmp_path, {**_LAYERED, "application/probe.py": "from ..domain import model\n"})
    _, _, cross, _, _ = check_layering.analyse(root, is_fixture=True)
    assert cross == 1


# ---------------------------------------------------------------------------
# the gates whose clauses name THIRD-PARTY roots
# ---------------------------------------------------------------------------
#
# A relative import can only ever name a module inside the package, so it can never be
# `sqlite3` or `pydantic`. What these arms pin is that the shared resolver puts a
# relative import in the package's namespace: a local module that happens to be called
# `sqlite3` or `pydantic_compat` is not the driver or the library. A "fix" that read
# `node.module` and dropped the level would flag both, naming a real file.


def test_orm_standard_flags_the_absolute_driver_and_not_a_local_module_named_like_it(
    tmp_path: Path,
) -> None:
    root = _package(
        tmp_path,
        {
            "application/absolute.py": "import sqlite3\n",
            "application/relative.py": "from .sqlite3 import connect\nfrom . import psycopg\n",
            "application/sqlite3.py": "def connect():\n    return None\n",
            "application/psycopg.py": "",
        },
    )
    findings, counts = check_orm_standard.analyse(root, {})
    assert len(findings) == 1, findings
    assert findings[0].startswith("application/absolute.py:1:")
    # pkg/__init__.py, application/__init__.py and the four planted modules.
    assert counts["modules"] == 6


def test_orm_standard_still_resolves_sqlalchemy_text_through_an_alias(tmp_path: Path) -> None:
    root = _package(
        tmp_path,
        {
            "adapters/real/probe.py": (
                "import sqlalchemy as sa\nfrom sqlalchemy import text as raw\n\n"
                "def probe(session):\n    session.execute(sa.text('SELECT 1'))\n"
                "    return session.execute(raw('SELECT 1'))\n"
            ),
        },
    )
    _, counts = check_orm_standard.analyse(root, {"adapters/real/probe.py": "readiness probe"})
    assert counts["text_calls"] == 2
    assert counts["sqlalchemy_modules"] == 1


def test_pydantic_boundaries_flags_domain_pydantic_and_not_a_local_compat_module(
    tmp_path: Path,
) -> None:
    root = _package(
        tmp_path,
        {
            "domain/absolute.py": "from pydantic import BaseModel\n",
            "domain/relative.py": "from .pydantic import BaseModel\n",
            "domain/pydantic.py": "class BaseModel:\n    pass\n",
        },
    )
    findings, counts = check_pydantic_boundaries.analyse(root)
    domain = [item for item in findings if "domain" in item and "pydantic" in item.lower()]
    assert [item.split(":", 1)[0] for item in domain] == ["domain/absolute.py"], findings
    assert counts["pydantic_modules"] == 1


# ---------------------------------------------------------------------------
# check_test_first: a relative edge is an edge
# ---------------------------------------------------------------------------


def test_test_first_follows_a_relative_import_into_the_package(tmp_path: Path) -> None:
    root = _package(
        tmp_path, {"application/a.py": "from .b import helper\nfrom .. import domain\n"}
    )
    found = check_test_first.intra_package_imports(
        root / "application" / "a.py", "pkg", import_root=root.parent
    )
    assert {"pkg.application.b", "pkg.application.b.helper", "pkg", "pkg.domain"} <= found
