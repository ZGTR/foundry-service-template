"""Versioned contracts for the private accepted-blog publication slice.

These are values only.  Policy remains in governance, persistence remains in
BB02, and HTTP/session handling remains in the Foundry backend.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

GENERATED_DOCUMENT_MEDIA_TYPE = "application/vnd.foundry.generated-document+json"
GENERATED_DOCUMENT_SNAPSHOT_SCHEMA_V1 = "foundry.generated-document-snapshot/v1"
MAX_GENERATED_DOCUMENT_BYTES = 2 * 1024 * 1024

ARTIFACT_PUBLICATION_CREATE_ACTION = "artifact.publication.create"
ARTIFACT_PUBLICATION_VIEW_ACTION = "artifact.view"
ARTIFACT_PUBLICATION_CREATE_ROUTE = "/api/v1/agent-runs/{run_id}/publication"
ARTIFACT_PUBLICATION_VIEW_ROUTE = "/s/{publication_id}"

Identifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
Sha256Digest = Annotated[
    str,
    StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$"),
]
MediaType = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=127,
        pattern=r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$",
    ),
]


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GeneratedByV1(_StrictContract):
    employee_id: Identifier
    run_id: Identifier


class GeneratedDocumentSnapshotV1(_StrictContract):
    """Safe, inert snapshot of one accepted ``GeneratedDocument`` row."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
    )

    contract_schema: Literal[GENERATED_DOCUMENT_SNAPSHOT_SCHEMA_V1] = Field(alias="schema")
    title: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    generated_by: GeneratedByV1
    source_document_digest: Sha256Digest
    markdown_source: Annotated[
        str,
        StringConstraints(min_length=1, max_length=MAX_GENERATED_DOCUMENT_BYTES),
    ]

    @field_validator("title")
    @classmethod
    def _bound_title_utf8_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 2_048:
            raise ValueError("generated document title exceeds its UTF-8 byte bound")
        return value

    @field_validator("markdown_source")
    @classmethod
    def _bound_markdown_utf8_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_GENERATED_DOCUMENT_BYTES:
            raise ValueError("generated document source exceeds its UTF-8 byte bound")
        return value


class ArtifactRevisionV1(_StrictContract):
    """One exact immutable BB02 artifact revision."""

    artifact_id: Identifier
    version: int = Field(strict=True, ge=1)
    digest: Sha256Digest
    kind: Identifier
    media_type: MediaType


class ArtifactPublicationV1(_StrictContract):
    """Private V1 policy head around one immutable artifact revision."""

    contract_version: Literal["artifact-publication/v1"]
    publication_id: Identifier
    org_id: Identifier
    controller_actor_id: Identifier
    source_artifact: ArtifactRevisionV1
    served_artifact: ArtifactRevisionV1
    visibility: Literal["restricted"]
    state: Literal["active", "revoked"]
    policy_revision: int = Field(strict=True, ge=1)
    acl_epoch: int = Field(strict=True, ge=1)

    @model_validator(mode="after")
    def _restricted_v1_serves_the_exact_source(self) -> Self:
        if self.source_artifact != self.served_artifact:
            raise ValueError("restricted V1 must serve its exact source artifact revision")
        return self


class PrivateArtifactDeliveryV1(_StrictContract):
    """Non-secret metadata accompanying one admitted private byte delivery."""

    contract_version: Literal["artifact-private-delivery/v1"]
    publication_id: Identifier
    publication_revision: int = Field(strict=True, ge=1)
    artifact: ArtifactRevisionV1
    renderer: Literal["escaped_source"]

    @model_validator(mode="after")
    def _renderer_accepts_only_the_generated_document_envelope(self) -> Self:
        if (
            self.artifact.kind != "generated_document_snapshot"
            or self.artifact.media_type != GENERATED_DOCUMENT_MEDIA_TYPE
        ):
            raise ValueError("escaped_source admits only generated-document snapshots")
        return self
