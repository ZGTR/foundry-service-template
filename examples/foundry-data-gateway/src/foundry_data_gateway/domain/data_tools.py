"""The curated, read-only tools this gateway exposes over company data, and their policy.

A tool is NOT a table. Each one is a reviewed question with a fixed SQL shape, a fixed
set of output columns and a typed input. An agent can never send SQL, name a table or
widen a column list, so "which data can leave" is answered by reading this file.

Row scope is not decided here. It is enforced by the database (row-level security keyed
on `app.org_id`), so a mistake in a tool's SQL still cannot read another org's rows.
This module decides the two things a database cannot: which tools an org may call, and
how many rows one call may return.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

AGE_GROUPS = ("3-4", "5-6", "7-8")


@dataclass(frozen=True, slots=True)
class DataTool:
    name: str
    description: str
    input_schema: Mapping[str, object]
    #: The columns a result may carry. Anything else in a row is a defect, refused.
    columns: tuple[str, ...]


CATALOGUE: dict[str, DataTool] = {
    tool.name: tool
    for tool in (
        DataTool(
            name="list_subjects",
            description="List the catalogue's learning subjects, optionally for one age group.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"age_group": {"type": "string", "enum": list(AGE_GROUPS)}},
            },
            columns=("slug", "title_en", "title_ar", "age_group", "bytes"),
        ),
        DataTool(
            name="subject_progress",
            description=(
                "Per-learner progress in one subject for the caller's school: bytes "
                "completed and average score."
            ),
            input_schema={
                "type": "object",
                "required": ["subject_slug"],
                "additionalProperties": False,
                "properties": {"subject_slug": {"type": "string", "maxLength": 64}},
            },
            columns=("learner", "age_group", "bytes_completed", "avg_score"),
        ),
        DataTool(
            name="struggling_learners",
            description=(
                "Learners in the caller's school whose average score is below a threshold "
                "(0-100), weakest first."
            ),
            input_schema={
                "type": "object",
                "required": ["below_score"],
                "additionalProperties": False,
                "properties": {"below_score": {"type": "integer", "minimum": 0, "maximum": 100}},
            },
            columns=("learner", "subject", "avg_score", "attempts"),
        ),
    )
}


class ToolRefused(Exception):
    """Nothing was read. `rule` names the check that refused, for the receipt and the caller."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule


@dataclass(frozen=True, slots=True)
class OrgPolicy:
    tools: frozenset[str]
    max_rows: int = 50


@dataclass(frozen=True, slots=True)
class DataPolicy:
    """Default deny: an org absent from the policy may call nothing."""

    orgs: Mapping[str, OrgPolicy] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: str) -> DataPolicy:
        parsed = json.loads(raw) if raw.strip() else {}
        return cls(
            orgs={
                org: OrgPolicy(
                    tools=frozenset(spec.get("tools", ())), max_rows=int(spec.get("max_rows", 50))
                )
                for org, spec in parsed.items()
            }
        )

    def allowed(self, org_id: str) -> list[DataTool]:
        policy = self.orgs.get(org_id)
        return [] if policy is None else [t for n, t in CATALOGUE.items() if n in policy.tools]

    def decide(self, org_id: str, tool_name: str) -> tuple[DataTool, int]:
        tool = CATALOGUE.get(tool_name)
        if tool is None:
            raise ToolRefused("unknown_tool", f"no tool named {tool_name!r}")
        policy = self.orgs.get(org_id)
        if policy is None:
            raise ToolRefused("unknown_org", "this organisation has no data policy")
        if tool_name not in policy.tools:
            raise ToolRefused(
                "tool_not_granted", f"{tool_name} is not granted to this organisation"
            )
        return tool, policy.max_rows


def check_arguments(tool: DataTool, args: object) -> dict[str, object]:
    """The input schema's subset these tools use: object, required, enum, types, bounds."""
    if not isinstance(args, dict):
        raise ToolRefused("invalid_arguments", "arguments must be one JSON object")
    schema = tool.input_schema
    props: Mapping[str, Mapping[str, object]] = schema.get("properties", {})  # type: ignore[assignment]
    for name in schema.get("required", ()):  # type: ignore[union-attr]
        if name not in args:
            raise ToolRefused("invalid_arguments", f"missing argument {name!r}")
    for name, value in args.items():
        rule = props.get(name)
        if rule is None:
            raise ToolRefused("invalid_arguments", f"unexpected argument {name!r}")
        kind = rule.get("type")
        if kind == "string" and not isinstance(value, str):
            raise ToolRefused("invalid_arguments", f"{name} must be a string")
        if kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            raise ToolRefused("invalid_arguments", f"{name} must be an integer")
        if "enum" in rule and value not in rule["enum"]:  # type: ignore[operator]
            raise ToolRefused("invalid_arguments", f"{name} must be one of {rule['enum']}")
        if "maxLength" in rule and len(value) > rule["maxLength"]:  # type: ignore[arg-type,operator]
            raise ToolRefused("invalid_arguments", f"{name} is too long")
        if "minimum" in rule and value < rule["minimum"]:  # type: ignore[operator]
            raise ToolRefused("invalid_arguments", f"{name} is below {rule['minimum']}")
        if "maximum" in rule and value > rule["maximum"]:  # type: ignore[operator]
            raise ToolRefused("invalid_arguments", f"{name} is above {rule['maximum']}")
    return dict(args)
