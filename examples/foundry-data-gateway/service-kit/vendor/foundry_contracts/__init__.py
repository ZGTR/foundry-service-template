"""Primitives every Foundry service shares.

Nothing here reads the environment, opens a socket, or knows an application's
domain types. Everything above this package depends on it; it depends on nothing.
"""

from foundry_contracts.artifact_publication import (
    ARTIFACT_PUBLICATION_CREATE_ACTION,
    ARTIFACT_PUBLICATION_CREATE_ROUTE,
    ARTIFACT_PUBLICATION_VIEW_ACTION,
    ARTIFACT_PUBLICATION_VIEW_ROUTE,
    GENERATED_DOCUMENT_MEDIA_TYPE,
    GENERATED_DOCUMENT_SNAPSHOT_SCHEMA_V1,
    MAX_GENERATED_DOCUMENT_BYTES,
    ArtifactPublicationV1,
    ArtifactRevisionV1,
    GeneratedByV1,
    GeneratedDocumentSnapshotV1,
    PrivateArtifactDeliveryV1,
)
from foundry_contracts.canon import (
    canonical_digest,
    canonical_json,
    jcs_canonical_digest,
    jcs_canonical_json,
)
from foundry_contracts.clock import Clock, FrozenClock, SystemClock
from foundry_contracts.errors import (
    ConfigurationError,
    Forbidden,
    PolicyRefused,
    PossibleSend,
    ProviderFailed,
    FoundryError,
    Unauthorized,
)
from foundry_contracts.ids import derive_id, new_id
from foundry_contracts.output import (
    AGENT_STRUCTURED_TURN_CONTRACT_V2,
    AGENT_STRUCTURED_TURN_CONTRACT_V3,
    DEFAULT_OUTPUT_CONTRACTS,
    JUDGE_VERDICT_CONTRACT_V1,
    AgentStructuredTurnOutputV2,
    JudgeVerdictV1Model,
    OutputContractRegistry,
    OutputContractUnsupported,
    OutputContractV1,
    OutputValidationFailed,
    StrictOutputModel,
    default_output_contract_registry,
)
from foundry_contracts.principal import CallerPrincipalV1, PrincipalMinter
from foundry_contracts.receipt import ReceiptV1
from foundry_contracts.unit_of_work import UnitOfWork

__all__ = [
    "ARTIFACT_PUBLICATION_CREATE_ACTION",
    "ARTIFACT_PUBLICATION_CREATE_ROUTE",
    "ARTIFACT_PUBLICATION_VIEW_ACTION",
    "ARTIFACT_PUBLICATION_VIEW_ROUTE",
    "AGENT_STRUCTURED_TURN_CONTRACT_V2",
    "AGENT_STRUCTURED_TURN_CONTRACT_V3",
    "DEFAULT_OUTPUT_CONTRACTS",
    "JUDGE_VERDICT_CONTRACT_V1",
    "AgentStructuredTurnOutputV2",
    "JudgeVerdictV1Model",
    "ArtifactPublicationV1",
    "ArtifactRevisionV1",
    "FoundryError",
    "CallerPrincipalV1",
    "Clock",
    "ConfigurationError",
    "FrozenClock",
    "GENERATED_DOCUMENT_MEDIA_TYPE",
    "GENERATED_DOCUMENT_SNAPSHOT_SCHEMA_V1",
    "GeneratedByV1",
    "GeneratedDocumentSnapshotV1",
    "MAX_GENERATED_DOCUMENT_BYTES",
    "PolicyRefused",
    "OutputContractRegistry",
    "OutputContractUnsupported",
    "OutputContractV1",
    "OutputValidationFailed",
    "PossibleSend",
    "ProviderFailed",
    "PrincipalMinter",
    "PrivateArtifactDeliveryV1",
    "ReceiptV1",
    "SystemClock",
    "StrictOutputModel",
    "Forbidden",
    "Unauthorized",
    "UnitOfWork",
    "canonical_digest",
    "default_output_contract_registry",
    "canonical_json",
    "derive_id",
    "jcs_canonical_digest",
    "jcs_canonical_json",
    "new_id",
]
