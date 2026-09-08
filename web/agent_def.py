"""
Omnisight agent definition for the web service.

This is the same agent that runs on the Gemini Enterprise Agent Platform: same
model, same instructions, same two MCP toolsets. It is defined here so the chat
page can run it directly rather than proxying to the deployed instance.
"""

import os
from functools import cached_property

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.genai import Client

HERE = os.path.dirname(os.path.abspath(__file__))

OMNISIGHT_MCP = os.environ.get(
    "OMNISIGHT_MCP_URL",
    "https://asia-southeast1-omnisight-setops.cloudfunctions.net/omnisight-mcp",
)
GRAFANA_MCP = os.environ.get(
    "GRAFANA_MCP_URL",
    "https://grafana-mcp-708110251968.asia-southeast1.run.app/mcp",
)


def _load_instruction():
    """The system prompt lives in its own file so it can be edited without code."""
    path = os.path.join(HERE, "system_prompt.md")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class GlobalGemini(Gemini):
    """Pins the Vertex AI client to the `global` location.

    gemini-3 series models are only served from `global`; the default ADK
    Gemini integration builds a client whose location follows the deployment
    region and fails with model-not-found.
    """

    @cached_property
    def api_client(self) -> Client:
        return Client(vertexai=True, location="global")


root_agent = LlmAgent(
    name="Omnisight",
    model=GlobalGemini(model=os.environ.get("OMNISIGHT_MODEL", "gemini-3.5-flash")),
    description=(
        "Camera-fleet monitoring assistant for live television production. "
        "Reads wireless camera telemetry and answers a director's questions "
        "in a director's language."
    ),
    instruction=_load_instruction(),
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(url=OMNISIGHT_MCP),
        ),
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(url=GRAFANA_MCP),
        ),
    ],
)
