"""MVC views for data tools: one tool or result, rendered for each door that serves it.

The same `Tool` renders as an MCP tool, a REST catalogue entry and (via the application's
`AgentDefinition`) a foundry-agent tool, so a field added to the pack shows up in all
three from one place.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from foundry_data_gateway.application import ToolResult, result_payload
from foundry_data_gateway.domain import Tool, ToolRefused


class DataToolView:
    @staticmethod
    def mcp_tool(tool: Tool) -> dict[str, object]:
        return {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema(),
            "outputSchema": tool.output_schema(),
            "annotations": dict(tool.annotations),
        }

    @staticmethod
    def catalogue_entry(tool: Tool) -> dict[str, object]:
        return {
            "pack": tool.pack,
            "name": tool.name,
            "description": tool.description,
            "source": {
                "name": tool.source.name,
                "kind": tool.source.kind,
                "scope": tool.source.scope.strategy,
            },
            "input_schema": tool.input_schema(),
            "returns": dict(tool.returns),
            "examples": [
                {"question": e.question, "arguments": dict(e.arguments)} for e in tool.examples
            ],
        }

    @staticmethod
    def answer(result: ToolResult) -> dict[str, object]:
        return {**result_payload(result), "columns": list(result.tool.columns)}

    @staticmethod
    def refusal(refused: ToolRefused) -> dict[str, object]:
        return {"ok": False, "refused": refused.rule, "reason": str(refused)}

    @staticmethod
    def mcp_call(answer: Mapping[str, object]) -> dict[str, object]:
        return {
            "content": [{"type": "text", "text": json.dumps(answer, default=str)}],
            "structuredContent": dict(answer),
            "isError": not answer["ok"],
        }

    @staticmethod
    def chat_card(call_id: str, tool_name: str, answer: Mapping[str, object]) -> dict[str, object]:
        return {"id": call_id, "kind": "data_table", "title": tool_name, "data": dict(answer)}
