"""Versioned, allowlisted structured-output contracts."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Annotated, Literal

import pydantic
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    TypeAdapter,
    ValidationError,
)

from foundry_contracts.canon import canonical_digest
from foundry_contracts.errors import FoundryError

EnforcementMode = Literal["local_only", "provider_and_local"]
_ID = re.compile(r"^[a-z][a-z0-9.-]{0,127}$")
_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "exclusiveMaximum",
        "exclusiveMinimum",
        "maxItems",
        "maxLength",
        "maximum",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "uniqueItems",
    }
)


class OutputContractUnsupported(FoundryError):
    """A contract identity or schema is not admitted."""


class OutputValidationFailed(FoundryError):
    """Provider bytes did not satisfy their pinned local contract."""


class StrictOutputModel(BaseModel):
    """Base for models whose JSON objects reject undeclared fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


class AgentStructuredTurnOutputV2(StrictOutputModel):
    """Domain-neutral envelope; the configured step schema validates its native JSON value."""

    protocol: Literal["foundry.agent.turn.v1"]
    kind: Literal["final"]
    output: JsonValue


class AgentStructuredFinalTurnV3(StrictOutputModel):
    """V2's only shape, unchanged, so a `final` turn validates identically under both."""

    protocol: Literal["foundry.agent.turn.v1"]
    kind: Literal["final"]
    output: JsonValue


class AgentStructuredToolCallTurnV3(StrictOutputModel):
    """The turn V2 could not express, and the omission was not cosmetic.

    A runtime routing its tools through a gateway pins an output contract on every
    completion, so the contract decides what a model is ALLOWED to say. With only
    `final` admitted, a model that asked for a tool was refused by the gateway before
    the runtime saw the turn -- tool routing that worked against an in-process model
    and could not work over HTTP, with nothing red to show it (measured by the cross-service proof in `agent-runtime-e2e`).

    `arguments` is a JSON object rather than `JsonValue`: a tool call's arguments are
    named, and a bare scalar or list here would reach a connector as a shape no
    `input_schema` can describe.
    """

    protocol: Literal["foundry.agent.turn.v1"]
    kind: Literal["tool_call"]
    name: str
    arguments: dict[str, JsonValue]


class AgentStructuredTurnOutputV3(RootModel):
    """A `kind`-discriminated union, as a RootModel so it stays a single `BaseModel`.

    `OutputContractV1.model` is typed `type[BaseModel]` and every consumer treats the
    validated result as one: the gateway calls `model_dump(mode="json")` and the
    runtime reads the resulting dict. A `RootModel` dumps to the INNER object, so the
    wire shape of a `final` turn is byte-identical to V2's and no caller changes.

    DISCRIMINATED, not a plain union, because the error a model has to act on is the
    difference. Against an untagged union pydantic reports both branches' failures,
    so a `tool_call` missing its `name` comes back as four errors including "kind must
    be 'final'" -- and the gateway's self-correction step feeds those loci back to the
    model, which would be told to change the one field it got right.
    """

    root: Annotated[
        AgentStructuredFinalTurnV3 | AgentStructuredToolCallTurnV3,
        Field(discriminator="kind"),
    ]


class JudgeVerdictV1Model(StrictOutputModel):
    """One judge verdict, the shape every scorer in the estate validates against.

    `passed` is a strict bool: a judge that answers `"yes"` or `"false"` has not
    answered the question, and coercing it would turn a malformed answer into a
    confident one. `reason` is bounded so a runaway judge cannot fill a ledger row.

    `feedback` defaults to empty on purpose. The two-field `{passed, reason}` shape
    predates this contract (an evaluation harness's V2 parsing), and a required third field
    would refuse every verdict already recorded in that shape.
    """

    passed: bool
    reason: str = Field(min_length=1, max_length=512)
    feedback: str = Field(default="", max_length=4000)


@dataclass(frozen=True, slots=True)
class OutputContractV1:
    contract_id: str
    version: int
    model: type[BaseModel]
    enforcement_mode: EnforcementMode
    pydantic_version: str = field(init=False)
    schema_digest: str = field(init=False)
    _schema: Mapping[str, object] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.contract_id) or self.version <= 0:
            raise OutputContractUnsupported("OUTPUT_CONTRACT_UNKNOWN")
        schema = self.model.model_json_schema(mode="validation")
        _validate_supported_schema(
            schema, provider_constraints=self.enforcement_mode != "local_only"
        )
        version = pydantic.VERSION
        object.__setattr__(self, "pydantic_version", version)
        object.__setattr__(self, "_schema", MappingProxyType(deepcopy(schema)))
        object.__setattr__(
            self,
            "schema_digest",
            canonical_digest({"pydantic_version": version, "schema": schema}),
        )

    @property
    def schema(self) -> dict[str, object]:
        return deepcopy(dict(self._schema))

    def validate_json(self, payload: bytes | str) -> BaseModel:
        try:
            return TypeAdapter(self.model).validate_json(payload, strict=True)
        except (ValidationError, ValueError, TypeError) as error:
            raise OutputValidationFailed("OUTPUT_VALIDATION_FAILED") from error


class OutputContractRegistry:
    def __init__(self, contracts: Iterable[OutputContractV1]) -> None:
        values: dict[tuple[str, int], OutputContractV1] = {}
        for contract in contracts:
            key = (contract.contract_id, contract.version)
            if key in values:
                raise OutputContractUnsupported("OUTPUT_CONTRACT_DUPLICATE")
            values[key] = contract
        self._contracts = MappingProxyType(values)

    def resolve(self, contract_id: str, version: int) -> OutputContractV1:
        try:
            return self._contracts[(contract_id, version)]
        except KeyError as error:
            raise OutputContractUnsupported("OUTPUT_CONTRACT_UNKNOWN") from error


def _validate_supported_schema(schema: object, *, provider_constraints: bool = True) -> None:
    definitions = schema.get("$defs", {}) if isinstance(schema, Mapping) else {}
    refs: dict[str, set[str]] = {}

    def walk(value: object, owner: str = "#") -> None:
        if isinstance(value, Mapping):
            if provider_constraints and (
                _UNSUPPORTED_KEYWORDS.intersection(value)
                or value.get("minItems")
                not in (
                    None,
                    0,
                    1,
                )
            ):
                raise OutputContractUnsupported("OUTPUT_SCHEMA_UNSUPPORTED")
            if (
                provider_constraints
                and value.get("type") == "object"
                and value.get("additionalProperties") is not False
            ):
                raise OutputContractUnsupported("OUTPUT_SCHEMA_UNSUPPORTED")
            ref = value.get("$ref")
            if ref is not None:
                if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
                    raise OutputContractUnsupported("OUTPUT_SCHEMA_UNSUPPORTED")
                refs.setdefault(owner, set()).add(ref.removeprefix("#/$defs/"))
            for key, child in value.items():
                walk(child, key if owner == "$defs" else owner)
        elif isinstance(value, list):
            for child in value:
                walk(child, owner)

    if isinstance(definitions, Mapping):
        for name, definition in definitions.items():
            walk(definition, str(name))
    root_schema = (
        {key: value for key, value in schema.items() if key != "$defs"}
        if isinstance(schema, Mapping)
        else schema
    )
    walk(root_schema)

    def cyclic(node: str, trail: frozenset[str]) -> bool:
        if node in trail:
            return True
        return any(cyclic(child, trail | {node}) for child in refs.get(node, set()))

    if provider_constraints and any(cyclic(node, frozenset()) for node in refs):
        raise OutputContractUnsupported("OUTPUT_SCHEMA_UNSUPPORTED")


#: LOCAL_ONLY is a decision, not an oversight, and it is written here because the
#: alternative is diffing four vendored copies to learn what the estate chose.
#:
#: `enforcement_mode` is NOT part of `schema_digest` -- the digest covers
#: `pydantic_version` plus the schema -- so the identity check a runtime performs
#: against a gateway response cannot see a disagreement about this field. Two sides
#: can agree on the digest while enforcing differently, which is why a stale vendored
#: copy of this file is invisible to every check except a byte comparison
#: (`scripts/check_vendor_drift.py`).
AGENT_STRUCTURED_TURN_CONTRACT_V2 = OutputContractV1(
    contract_id="agent.structured-generation.turn",
    version=2,
    model=AgentStructuredTurnOutputV2,
    enforcement_mode="local_only",
)


#: V3 IS A NEW VERSION RATHER THAN A WIDENING OF V2, and the reason is on the wire.
#: A caller pins `(contract_id, version, schema_digest)` and the digest covers
#: `pydantic_version` plus the schema -- but NOT `enforcement_mode`, so two sides can
#: already agree on a digest while enforcing differently. Widening V2 in place would add
#: a second way to disagree that no digest check could see: a caller on an older vendored
#: copy would send V2's digest, a gateway on a newer one would compute a different digest
#: for the same version, and every completion would fail on an identity mismatch that
#: reads as corruption rather than as a version skew.
#:
#: Both are registered. V2 is not deprecated here: it is what every caller that does not
#: route tools still pins, and removing it would be a breaking change to services this
#: contract has nothing to do with.
AGENT_STRUCTURED_TURN_CONTRACT_V3 = OutputContractV1(
    contract_id="agent.structured-generation.turn",
    version=3,
    model=AgentStructuredTurnOutputV3,
    enforcement_mode="local_only",
)


#: LOCAL_ONLY for the same reason as the turn contracts, and here it is load-bearing:
#: the string bounds on `reason` and `feedback` are keywords the provider-side schema
#: check refuses (`_UNSUPPORTED_KEYWORDS`), so `provider_and_local` could not be
#: constructed at all. Local validation stays strict either way.
JUDGE_VERDICT_CONTRACT_V1 = OutputContractV1(
    contract_id="judge.verdict",
    version=1,
    model=JudgeVerdictV1Model,
    enforcement_mode="local_only",
)


#: Every output contract this package ships, as a value a CONSUMER can compose with.
#:
#: It exists because a consumer that needs its own contracts alongside these has to
#: build its own registry, and the only way to do that was to name the package's
#: contracts again by hand. The llm-gateway did exactly that -- `OutputContractRegistry([
#: AGENT_STRUCTURED_TURN_CONTRACT_V2, RAG_QUERY_REWRITE_CONTRACT, ...])` -- so adding V3
#: here left the gateway unable to resolve it, and a runtime pinning V3 got
#: OUTPUT_CONTRACT_UNKNOWN from a service whose vendored copy defined V3 perfectly well.
#: Nothing was red: the package's tests passed, the gateway's tests passed, and the two
#: disagreed about what contracts exist.
#:
#: A consumer composing from this tuple covers a contract added here by DEFAULT, rather
#: than by remembering to. That is the same reason `check_vendor_drift.py` discovers
#: packages by glob instead of listing them.
DEFAULT_OUTPUT_CONTRACTS = (
    AGENT_STRUCTURED_TURN_CONTRACT_V2,
    AGENT_STRUCTURED_TURN_CONTRACT_V3,
    JUDGE_VERDICT_CONTRACT_V1,
)


def default_output_contract_registry() -> OutputContractRegistry:
    return OutputContractRegistry(DEFAULT_OUTPUT_CONTRACTS)
