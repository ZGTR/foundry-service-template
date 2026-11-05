"""The data-tool use cases, bundled for the composition root to hand to controllers."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_data_gateway.application.call_data_tool import CallDataTool
from foundry_data_gateway.application.data_tool_catalogue import (
    AgentDefinition,
    ListDataTools,
    ReadAudit,
)


@dataclass(frozen=True, slots=True)
class DataToolUseCases:
    call: CallDataTool
    list: ListDataTools
    agent: AgentDefinition
    audit: ReadAudit
