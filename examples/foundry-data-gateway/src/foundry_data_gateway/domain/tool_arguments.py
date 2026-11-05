"""Arguments checked against a tool's declared parameters, defaults applied.

The model writes these, so they are hostile input: unknown keys, wrong types and values
outside the declared bounds are refused before anything is read. The tenant scope
parameter can never arrive this way: a tool may not declare it (`tool_pack.py`), so an
argument with its name is "unexpected" like any other.
"""

from __future__ import annotations

from foundry_data_gateway.domain.errors import ToolRefused
from foundry_data_gateway.domain.tool_pack import Parameter, Tool

_PY_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
}


def _check(param: Parameter, value: object) -> None:
    def refuse(why: str) -> ToolRefused:
        return ToolRefused("invalid_arguments", f"{param.name} {why}")

    if isinstance(value, bool) and param.type != "boolean":
        raise refuse(f"must be {param.type}")
    if not isinstance(value, _PY_TYPES[param.type]):
        raise refuse(f"must be {param.type}")
    if param.enum and value not in param.enum:
        raise refuse(f"must be one of {list(param.enum)}")
    if param.max_length is not None and len(value) > param.max_length:  # type: ignore[arg-type]
        raise refuse("is too long")
    if param.minimum is not None and value < param.minimum:  # type: ignore[operator]
        raise refuse(f"is below {param.minimum}")
    if param.maximum is not None and value > param.maximum:  # type: ignore[operator]
        raise refuse(f"is above {param.maximum}")


def bind_arguments(tool: Tool, args: object) -> dict[str, object]:
    if not isinstance(args, dict):
        raise ToolRefused("invalid_arguments", "arguments must be one JSON object")
    declared = {p.name: p for p in tool.parameters}
    unexpected = set(args) - set(declared)
    if unexpected:
        raise ToolRefused("invalid_arguments", f"unexpected arguments {sorted(unexpected)}")
    bound: dict[str, object] = {}
    for name, param in declared.items():
        if name not in args:
            if param.required:
                raise ToolRefused("invalid_arguments", f"missing argument {name!r}")
            bound[name] = param.default
            continue
        _check(param, args[name])
        bound[name] = args[name]
    return bound
