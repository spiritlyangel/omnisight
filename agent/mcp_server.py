"""
Omnisight — MCP server.

Wraps the Grafana telemetry tool in the Model Context Protocol so it can be
attached to a Gemini Enterprise agent via Agent Designer's MCP Server option.

Speaks JSON-RPC 2.0 over HTTP. Implements the three methods a client needs:
initialize, tools/list and tools/call.

Deploy alongside grafana_tool.py with --entry-point=mcp
"""

import json

from grafana_tool import GrafanaError, get_fleet_status

PROTOCOL_VERSION = "2025-06-18"

TOOL_SPEC = {
    "name": "get_fleet_status",
    "description": (
        "Returns the current state of every wireless camera on the shoot, with "
        "rates of change already computed. For each camera: signal strength in "
        "dBm and its trend in dBm per minute, battery percentage, measured "
        "drain rate, and estimated minutes of battery remaining, plus "
        "temperature, link state, dropped frames, latency, bitrate, whether it "
        "is transmitting, whether it is recording locally, and how many seconds "
        "it has been offline. Also returns the current setup and location, and "
        "whether the entire fleet is offline (which indicates transit between "
        "locations, not failure). Call this before answering any question about "
        "current fleet status, which camera will fail next, or how much battery "
        "time remains."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "at": {
                "type": "string",
                "description": (
                    "ISO timestamp to evaluate, e.g. 2026-09-01T09:15:00. "
                    "Defaults to now."
                ),
            },
            "lookback_minutes": {
                "type": "integer",
                "description": (
                    "How far back to look when computing trends and drain "
                    "rates. Defaults to 30."
                ),
            },
        },
        "required": [],
    },
}


def _result(request_id, payload):
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code, message):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _handle(message):
    """Route one JSON-RPC message. Returns a response dict, or None for notifications."""
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "omnisight", "version": "1.0.0"},
            },
        )

    # Notifications carry no id and expect no response.
    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": [TOOL_SPEC]})

    if method == "tools/call":
        if params.get("name") != TOOL_SPEC["name"]:
            return _error(request_id, -32602, f"Unknown tool: {params.get('name')}")

        args = params.get("arguments") or {}
        try:
            data = get_fleet_status(
                at_iso=args.get("at"),
                lookback_minutes=int(args.get("lookback_minutes", 30)),
            )
        except GrafanaError as exc:
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": f"Telemetry error: {exc}"}],
                    "isError": True,
                },
            )
        except Exception as exc:  # noqa: BLE001 - surface anything to the agent
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": f"Unexpected error: {exc}"}],
                    "isError": True,
                },
            )

        return _result(
            request_id,
            {"content": [{"type": "text", "text": json.dumps(data)}]},
        )

    return _error(request_id, -32601, f"Method not found: {method}")


def mcp(request):
    """Google Cloud Function entry point for the MCP endpoint."""
    if request.method != "POST":
        return ("This endpoint speaks MCP over JSON-RPC. Use POST.", 405)

    body = request.get_json(silent=True)
    if body is None:
        return (json.dumps(_error(None, -32700, "Parse error")), 400,
                {"Content-Type": "application/json"})

    # A client may batch several messages in one array.
    if isinstance(body, list):
        responses = [r for r in (_handle(m) for m in body) if r is not None]
        payload = json.dumps(responses) if responses else ""
        return (payload, 200 if responses else 202,
                {"Content-Type": "application/json"})

    response = _handle(body)
    if response is None:
        return ("", 202)

    return (json.dumps(response), 200, {"Content-Type": "application/json"})
