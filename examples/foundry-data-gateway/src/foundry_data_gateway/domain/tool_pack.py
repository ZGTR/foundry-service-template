"""A tool pack: the declarative definition of the data a business lets agents read.

One YAML file per product (`packs/*.tools.yaml`), format `foundry.dev/tool-pack/v1`.
It follows the shape the market converged on (MCP Toolbox's `sources` / `tools` /
`toolsets`, each tool a named SQL statement with typed parameters) and maps one to one
onto MCP's tool fields, so nothing has to be written twice:

| pack field     | becomes                                              |
|----------------|------------------------------------------------------|
| `parameters`   | MCP `inputSchema` and the foundry-agent tool schema  |
| `returns`      | MCP `outputSchema`; the only columns a row may carry |
| `annotations`  | MCP `annotations` (v1 accepts read-only tools only)  |
| `examples`     | documentation, evals, and the deterministic e2e model|

The pack never holds credentials (a source names a `connection`, which the deployment maps
to a DSN) and never says who may call what (that is the policy file's job). Every rule a
reviewer would check by eye is checked here at load time instead, so a pack that would
leak across tenants does not start.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from foundry_data_gateway.domain.errors import InvalidToolPack

API_VERSION = "foundry.dev/tool-pack/v1"
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_BIND = re.compile(r"(?<!:):([a-z_][a-z0-9_]*)")
_READ_ONLY_START = re.compile(r"^\s*(select|with)\b", re.I)
_FORBIDDEN = re.compile(r";|\b(insert|update|delete|drop|alter|create|grant|truncate|call)\b", re.I)
_JSON_TYPES = {"string", "integer", "number", "boolean"}
SCOPE_STRATEGIES = ("bound_param", "rls")


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str
    type: str
    description: str = ""
    required: bool = True
    default: object = None
    enum: tuple[object, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    max_length: int | None = None

    def json_schema(self) -> dict[str, object]:
        schema: dict[str, object] = {"type": self.type}
        optional = {
            "description": self.description or None,
            "enum": list(self.enum) or None,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "maxLength": self.max_length,
            "default": self.default,
        }
        schema.update({k: v for k, v in optional.items() if v is not None})
        return schema


@dataclass(frozen=True, slots=True)
class Scope:
    """How a source keeps one tenant's rows away from another's.

    `bound_param`: the statement must reference `:<param>`, and the gateway binds it from
    the VERIFIED caller, never from the model's arguments (any dialect).
    `rls`: additionally, the source sets `app.<param>` and drops to a reader role whose
    row-level-security policies filter by it (PostgreSQL).
    """

    strategy: str
    param: str


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    kind: str  # "mysql" | "postgres"
    connection: str
    scope: Scope
    reader_role: str | None = None


@dataclass(frozen=True, slots=True)
class Example:
    question: str
    arguments: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Tool:
    pack: str
    name: str
    source: Source
    description: str
    statement: str
    parameters: tuple[Parameter, ...]
    returns: Mapping[str, str]
    examples: tuple[Example, ...] = ()
    annotations: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({"readOnlyHint": True})
    )

    def input_schema(self) -> dict[str, object]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [p.name for p in self.parameters if p.required],
            "properties": {p.name: p.json_schema() for p in self.parameters},
        }

    def output_schema(self) -> dict[str, object]:
        row = {
            "type": "object",
            "additionalProperties": False,
            "properties": {col: {"type": [kind, "null"]} for col, kind in self.returns.items()},
        }
        return {
            "type": "object",
            "properties": {
                "rows": {"type": "array", "items": row},
                "row_count": {"type": "integer"},
            },
        }

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(self.returns)

    def conform(self, row: Mapping[str, object]) -> dict[str, object]:
        """`row` with each value in its declared JSON type (drivers return DECIMAL for SUM)."""
        out: dict[str, object] = {}
        for col, value in row.items():
            kind = self.returns.get(col)
            if (
                value is not None
                and kind == "integer"
                and isinstance(value, float)
                and value.is_integer()
            ):
                value = int(value)
            elif (
                value is not None
                and kind == "number"
                and isinstance(value, int)
                and not isinstance(value, bool)
            ):
                value = float(value)
            out[col] = value
        return out


@dataclass(frozen=True, slots=True)
class ToolPack:
    name: str
    description: str
    sources: Mapping[str, Source]
    tools: Mapping[str, Tool]
    toolsets: Mapping[str, tuple[str, ...]]

    def toolset(self, name: str) -> tuple[Tool, ...]:
        return tuple(self.tools[t] for t in self.toolsets.get(name, ()))


def _need(mapping: Mapping[str, object], key: str, where: str) -> object:
    if key not in mapping or mapping[key] in (None, ""):
        raise InvalidToolPack(f"{where}: missing `{key}`")
    return mapping[key]


def _name(value: object, where: str) -> str:
    if not isinstance(value, str) or not _NAME.match(value):
        raise InvalidToolPack(f"{where}: {value!r} is not a snake_case name")
    return value


def _source(name: str, raw: Mapping[str, object]) -> Source:
    where = f"source {name}"
    kind = _need(raw, "kind", where)
    if kind not in ("mysql", "postgres"):
        raise InvalidToolPack(f"{where}: kind must be mysql or postgres, not {kind!r}")
    scope_raw = _need(raw, "scope", where)
    assert isinstance(scope_raw, Mapping)
    scope = Scope(str(_need(scope_raw, "strategy", where)), _name(scope_raw.get("param"), where))
    if scope.strategy not in SCOPE_STRATEGIES:
        raise InvalidToolPack(f"{where}: scope.strategy must be one of {SCOPE_STRATEGIES}")
    if scope.strategy == "rls" and kind != "postgres":
        raise InvalidToolPack(f"{where}: rls needs a postgres source; use bound_param")
    role = raw.get("reader_role")
    if scope.strategy == "rls" and not role:
        raise InvalidToolPack(f"{where}: rls needs `reader_role`")
    return Source(name, str(kind), str(_need(raw, "connection", where)), scope, role and str(role))


def _parameter(name: str, raw: Mapping[str, object], where: str) -> Parameter:
    kind = raw.get("type")
    if kind not in _JSON_TYPES:
        raise InvalidToolPack(f"{where}: parameter {name} type must be one of {_JSON_TYPES}")
    return Parameter(
        name=_name(name, where),
        type=str(kind),
        description=str(raw.get("description", "")),
        required="default" not in raw and not raw.get("optional", False),
        default=raw.get("default"),
        enum=tuple(raw.get("enum", ()) or ()),  # type: ignore[arg-type]
        minimum=raw.get("minimum"),  # type: ignore[arg-type]
        maximum=raw.get("maximum"),  # type: ignore[arg-type]
        max_length=raw.get("max_length"),  # type: ignore[arg-type]
    )


def _tool(pack: str, name: str, raw: Mapping[str, object], sources: Mapping[str, Source]) -> Tool:
    where = f"tool {name}"
    source_name = _need(raw, "source", where)
    if source_name not in sources:
        raise InvalidToolPack(f"{where}: unknown source {source_name!r}")
    source = sources[str(source_name)]
    statement = str(_need(raw, "statement", where))
    if not _READ_ONLY_START.match(statement) or _FORBIDDEN.search(statement):
        raise InvalidToolPack(f"{where}: statement must be one read-only SELECT/WITH")
    raw_params = raw.get("parameters") or {}
    assert isinstance(raw_params, Mapping)
    params = tuple(_parameter(n, p, where) for n, p in raw_params.items())  # type: ignore[arg-type]
    declared, bound = {p.name for p in params}, set(_BIND.findall(statement))
    scope_param = source.scope.param
    if scope_param in declared:
        raise InvalidToolPack(f"{where}: `{scope_param}` is bound from the caller, not a parameter")
    if scope_param not in bound:
        raise InvalidToolPack(
            f"{where}: statement never references :{scope_param}; it would cross tenants"
        )
    if bound - declared - {scope_param}:
        raise InvalidToolPack(
            f"{where}: statement binds undeclared {sorted(bound - declared - {scope_param})}"
        )
    if declared - bound:
        raise InvalidToolPack(f"{where}: parameters never used: {sorted(declared - bound)}")
    returns = raw.get("returns") or {}
    if not isinstance(returns, Mapping) or not returns:
        raise InvalidToolPack(f"{where}: `returns` must declare the output columns")
    if set(returns.values()) - _JSON_TYPES:
        raise InvalidToolPack(f"{where}: return types must be one of {_JSON_TYPES}")
    annotations = dict(raw.get("annotations") or {"readOnlyHint": True})  # type: ignore[arg-type]
    if annotations.get("readOnlyHint") is not True:
        raise InvalidToolPack(f"{where}: v1 serves read-only tools only (readOnlyHint: true)")
    examples = tuple(
        Example(str(e["question"]), dict(e.get("arguments") or {}))
        for e in (raw.get("examples") or ())  # type: ignore[union-attr]
    )
    return Tool(
        pack,
        _name(name, where),
        source,
        str(_need(raw, "description", where)).strip(),
        statement.strip(),
        params,
        MappingProxyType(dict(returns)),
        examples,
        MappingProxyType(annotations),
    )


def parse_pack(document: Mapping[str, object]) -> ToolPack:
    """A validated `ToolPack` from one parsed YAML document, or `InvalidToolPack`."""
    if document.get("apiVersion") != API_VERSION:
        raise InvalidToolPack(f"apiVersion must be {API_VERSION}")
    pack = _name(document.get("pack"), "pack")
    raw_sources = document.get("sources") or {}
    raw_tools = document.get("tools") or {}
    assert isinstance(raw_sources, Mapping) and isinstance(raw_tools, Mapping)
    sources = {n: _source(_name(n, "source"), s) for n, s in raw_sources.items()}  # type: ignore[arg-type]
    tools = {n: _tool(pack, n, t, sources) for n, t in raw_tools.items()}  # type: ignore[arg-type]
    raw_sets = document.get("toolsets") or {"all": list(tools)}
    assert isinstance(raw_sets, Mapping)
    toolsets: dict[str, tuple[str, ...]] = {}
    for set_name, members in raw_sets.items():
        unknown = set(members) - set(tools)  # type: ignore[arg-type]
        if unknown:
            raise InvalidToolPack(f"toolset {set_name}: unknown tools {sorted(unknown)}")
        toolsets[_name(set_name, "toolset")] = tuple(members)  # type: ignore[arg-type]
    return ToolPack(pack, str(document.get("description", "")), sources, tools, toolsets)


def merge_packs(packs: Sequence[ToolPack]) -> dict[str, ToolPack]:
    seen: dict[str, str] = {}
    for pack in packs:
        for tool in pack.tools:
            if tool in seen:
                raise InvalidToolPack(
                    f"tool {tool} is defined by both {seen[tool]} and {pack.name}"
                )
            seen[tool] = pack.name
    return {p.name: p for p in packs}
