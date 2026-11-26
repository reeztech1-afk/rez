"""Higgsfield driver workflow.

Calls the Higgsfield MCP connector (higgsfield_8114) on behalf of the
triggering user. Accepts a batch of tool calls and returns raw results so the
chat agent can iterate against the Higgsfield app interactively.
"""
from typing import Any

import mistralai.workflows as workflows
from mistralai.workflows import Depends
from mistralai.workflows.plugins.mistralai.connectors import (
    ToolCallClient,
    connector,
    uses_connectors,
)

higgsfield_connector = connector("higgsfield_8114")


@workflows.activity(name="call-higgsfield-tool")
async def call_higgsfield_tool(
    tool_name: str,
    arguments: dict[str, Any],
    hf: ToolCallClient = Depends(higgsfield_connector),
) -> Any:
    """Dispatch one tool call to the Higgsfield MCP connector."""
    result = await hf.call_tool(tool_name=tool_name, arguments=arguments)
    return result


@workflows.workflow.define(
    name="higgsfield-driver",
    workflow_display_name="Higgsfield Driver",
    workflow_description="Runs batches of tool calls against the Higgsfield MCP connector on behalf of the triggering user.",
    on_behalf_of=True,
)
@uses_connectors(higgsfield_connector)
class HiggsfieldWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, tool_name: str = "use_higgsfield", arguments: dict[str, Any] | None = None) -> Any:
        return await call_higgsfield_tool(tool_name, arguments or {})
