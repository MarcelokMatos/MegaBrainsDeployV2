"""Gateway MCP client for MegaBrainsFC tactical tools.

The AgentCore Gateway is expected to expose the following MCP tools:
- calculate_pass_options
- evaluate_shot
- find_open_space
- get_defensive_assignment

The Gateway endpoint URL is read from the GATEWAY_URL environment variable.
If unset (local dev, no gateway deployed), get_gateway_mcp_client() returns None
and the agent runs without tool-based tactical analysis.
"""
import os
import logging
from typing import Optional

from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

logger = logging.getLogger(__name__)


def get_gateway_mcp_client(url: Optional[str] = None) -> Optional[MCPClient]:
    """Return a Strands-compatible MCP client pointed at the tactical Gateway.

    Args:
        url: Explicit endpoint override. Defaults to reading GATEWAY_URL env var.
             The URL should end with /mcp.

    Returns:
        MCPClient instance, or None if no endpoint is configured.
    """
    endpoint = url or os.getenv("GATEWAY_URL")
    if not endpoint:
        return None

    # If the Gateway requires bearer authentication (Cognito etc.), append headers here.
    # Example: headers={"Authorization": f"Bearer {os.getenv('GATEWAY_TOKEN')}"}
    return MCPClient(lambda: streamablehttp_client(endpoint))