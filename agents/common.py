"""Shared plumbing for the Strands agents: the gateway MCP connection and a readable transcript."""

import json
import os
import uuid
from typing import List, Optional

from strands.tools.mcp import MCPClient

GATEWAY_MCP_URL = os.environ.get("GATEWAY_MCP_URL", os.environ.get("GATEWAY_URL", "http://127.0.0.1:8700") + "/agentcore/mcp")
# Set on AWS (CDK stack output): the AgentCore Gateway MCP endpoint. Agents then authenticate with their
# own Cognito identity (agents/identity.py) instead of the admission token.
AGENTCORE_GATEWAY_URL = os.environ.get("AGENTCORE_GATEWAY_URL")
TOOL_SEP = "___"  # AgentCore Gateway names tools <target>___<tool>


def new_session(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def aws_mode() -> bool:
    return bool(AGENTCORE_GATEWAY_URL)


def gateway_tools(token: Optional[str], session: str, agent: str = "") -> MCPClient:
    """The agent's only route to the network: the gateway's MCP endpoint with its own credential.

    Standalone: the framework's MCP endpoint with the bearer token admission issued.
    AWS: the AgentCore Gateway endpoint with the agent's OAuth token (`agent` is its prefix: ES, LB, VENDOR, ORCH).
    """
    if aws_mode():
        from agents.identity import access_token

        url, bearer = AGENTCORE_GATEWAY_URL, access_token(agent)
    else:
        url, bearer = GATEWAY_MCP_URL, token
    return MCPClient(url=url, headers={"Authorization": f"Bearer {bearer}", "X-Agent-Session": session})


def base_tool(name: str) -> str:
    """read_kpi for both read_kpi and smo-tools___read_kpi."""
    return name.split(TOOL_SEP, 1)[-1]


def read_scope(mcp: MCPClient) -> dict:
    """The agent's admitted scope, through the gateway's read_scope tool (works the same on both paths)."""
    name = next(t.tool_name for t in mcp.list_tools_sync() if base_tool(t.tool_name) == "read_scope")
    res = mcp.call_tool_sync(f"scope-{uuid.uuid4().hex[:6]}", name, {})
    body = _parse(res.get("content"))
    if not isinstance(body, dict) or "scope" not in body:
        raise RuntimeError(f"read_scope failed: {body}")
    return body["scope"]


def _parse(content) -> object:
    for block in content or []:
        if "text" in block:
            try:
                return json.loads(block["text"])
            except (json.JSONDecodeError, TypeError):
                return block["text"]
        if "json" in block:
            return block["json"]
    return None


def transcript(messages: List[dict]) -> List[dict]:
    """Tool calls in order, each with its input and the gateway's answer."""
    calls, results = [], {}
    for m in messages:
        for block in m.get("content", []):
            if "toolUse" in block:
                calls.append(block["toolUse"])
            elif "toolResult" in block:
                tr = block["toolResult"]
                results[tr["toolUseId"]] = {"status": tr.get("status"), "result": _parse(tr.get("content"))}
    return [{"tool": base_tool(c["name"]), "input": c.get("input"), **results.get(c["toolUseId"], {"status": "not run"})}
            for c in calls]


def final_text(result) -> str:
    return str(result).strip()
