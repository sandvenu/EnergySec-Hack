"""MCP face of the gateway: the tools agents call, each one passing the enforcement point.

The tool names are the action names an AgentCore Policy is written against, and the stages line up
with the AWS build:
  REQUEST interceptor   identity (Cognito client -> rApp), kill switch, screening of arguments
  AgentCore Policy      Cedar scope over the tool call (generated from policies/scope.cedar)
  enforcement point     scope with live load, budget, risk tier, impact assessment, ledger (this code)
  RESPONSE interceptor  screening of untrusted tool output before the model reads it (read_alarms)

Each tool is a plain function over (caller, session, arguments), served two ways:
  * standalone: MCP at /agentcore/mcp (mounted by framework/gateway.py); the caller is the bearer
    token admission issued, the session the X-Agent-Session header;
  * AWS: AgentCore Gateway -> Lambda target -> POST /v1/gateway/tools/{name} (framework/gateway.py);
    the caller is a TrustedCaller the interceptor established from the agent's Cognito identity.
"""

from typing import Callable, Dict, List, Optional

import anyio
from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel, Field

INSTRUCTIONS = ("RAN configuration tools at the SMO boundary. Every call is checked against the caller's "
                "admitted scope, blast radius budget and risk tier; denials return the policy ID. "
                "High-impact changes are shadow-run on the digital twin and wait for a human approver.")

mcp = MCPServer("smo-gateway", instructions=INSTRUCTIONS)


def _pep():
    from framework.gateway import pep  # same process as the gateway

    return pep


def _brief(outcome) -> dict:
    """What the agent gets back: the decision, never internal check detail."""
    o = outcome.as_dict()
    out = {"decision": o["decision"], "policy_id": o["policy_id"], "reason": o["reason"], "executed": o["executed"],
           "ledger_seq": o["ledger_seq"]}
    if o["request_id"]:
        out["request_id"] = o["request_id"]
    if o["shadow"]:
        s = o["shadow"]
        out["shadow_kpis"] = {k: {"before": s["before"][k], "after": s["after"][k]}
                              for k in ("weak_coverage_pct", "energy_saving_pct", "active_cells")}
    if o["decision"] == "allow" and o["result"] and "kpis" in o["result"]:
        k = o["result"]["kpis"]
        out["kpis"] = {x: k.get(x) for x in ("weak_coverage_pct", "energy_saving_pct", "active_cells", "max_cell_load",
                                             "excess_ues")}
    assessment = next((c["detail"] for c in o["checks"] if c.get("check") == "impact_assessment"), None)
    if assessment:
        out["assessment"] = {"class": assessment["class"], "why": assessment["why"]}
    return out


# ---- tool implementations (synchronous; caller = admission token or TrustedCaller) --------------
def read_kpi_impl(caller, session: Optional[str]) -> dict:
    o = _pep().submit(caller, [{"name": "read_kpi"}], "", session=session)
    if o.decision != "allow":
        return _brief(o)
    k, st = o.result["kpis"], o.result["state"]
    return {"tick": st["tick"], "weak_coverage_pct": k["weak_coverage_pct"], "energy_saving_pct": k["energy_saving_pct"],
            "active_cells": k["active_cells"], "max_cell_load": k["max_cell_load"], "excess_ues": k["excess_ues"],
            "cell_capacity_ues": st.get("cell_capacity_ues"), "ues_per_cell": k["ues_per_cell"], "cells": st["cells"],
            "planned_event": st.get("event")}


def what_if_impl(caller, session: Optional[str], changes: List[dict]) -> dict:
    acts = [{k: v for k, v in c.items() if v is not None} for c in changes]
    return _pep().what_if(caller, acts, session=session)


def read_alarms_impl(caller, session: Optional[str]) -> dict:
    try:
        feed = _pep().read_inputs(caller)
    except Exception as e:  # unknown or revoked credential
        return {"error": getattr(e, "reason", str(e)), "policy_id": getattr(e, "policy_id", None)}
    # the model sees the (possibly quarantined) text only, not screening internals
    return {"alarms": [{"alarm_id": a["alarm_id"], "source": a["source"], "text": a["text"]} for a in feed["alarms"]]}


def change_impl(name: str):
    def run(caller, session: Optional[str], justification: str = "", cell_ids: Optional[List[str]] = None,
            changes: Optional[List[dict]] = None) -> dict:
        if name == "set_tilt":
            actions = [{"name": "set_tilt", "cell_id": c["cell_id"], "tilt": c["tilt"]} for c in changes or []]
        else:
            actions = [{"name": name, "cell_id": c} for c in cell_ids or []]
        return _brief(_pep().submit(caller, actions, justification, session=session))
    return run


def bulk_reset_site_impl(caller, session: Optional[str], site_id: str = "", justification: str = "") -> dict:
    """Canary (framework/incidents.py): listed, granted to no card; any call is an S1 incident."""
    return _brief(_pep().submit(caller, [{"name": "bulk_reset_site", "site_id": site_id}], justification, session=session))


def get_request_status_impl(caller, session: Optional[str], request_id: str) -> dict:
    pep = _pep()
    rapp = pep.identify(caller)
    ap = pep.store.get_approval(request_id)
    if rapp is None or ap is None or ap["rapp_id"] != rapp["rapp_id"]:
        return {"error": "unknown request"}
    return {"request_id": request_id, "status": ap["status"]}


def read_scope_impl(caller, session: Optional[str]) -> dict:
    pep = _pep()
    rapp = pep.identify(caller)
    if rapp is None or rapp["status"] != "admitted":
        return {"error": "unknown or revoked credential"}
    return {"rapp_id": rapp["rapp_id"], "scope": {**rapp["scope"], "cell_criticality": pep.cells,
                                                  "model_budget": rapp["card"].get("model_budget")}}


TOOLS: Dict[str, Callable] = {
    "read_kpi": read_kpi_impl, "what_if": what_if_impl, "read_alarms": read_alarms_impl,
    "cell_off": change_impl("cell_off"), "cell_on": change_impl("cell_on"), "set_tilt": change_impl("set_tilt"),
    "get_request_status": get_request_status_impl, "read_scope": read_scope_impl,
    "bulk_reset_site": bulk_reset_site_impl,
}
CANARY_DESCRIPTION = ("OPERATOR-ONLY maintenance tool: factory-resets every cell on a site. Agents are not permitted "
                      "to call it; it is not part of any agent's scope.")


def call_tool(name: str, caller, session: Optional[str], arguments: dict) -> dict:
    """One entry point for the AgentCore Gateway path. Unknown tools and bad arguments are errors, not denials."""
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name!r}"}
    args = {k: v for k, v in (arguments or {}).items() if not k.startswith("_")}  # _caller/_session: interceptor-owned
    try:
        return fn(caller, session, **args)
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}


# ---- standalone MCP endpoint -------------------------------------------------------------------
def _caller(ctx: Context):
    headers = {k.lower(): v for k, v in (ctx.headers or {}).items()}
    auth = headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    return token, headers.get("x-agent-session")


async def _run(ctx: Context, fn: Callable, **kw) -> dict:
    token, session = _caller(ctx)
    return await anyio.to_thread.run_sync(lambda: fn(token, session, **kw))


class TiltChange(BaseModel):
    cell_id: str = Field(description="Cell ID, e.g. cell_2_0")
    tilt: float = Field(description="Electrical tilt in degrees: 0 to 20 in steps of 2")


class Change(BaseModel):
    name: str = Field(description="set_tilt, cell_off or cell_on")
    cell_id: str
    tilt: Optional[float] = Field(default=None, description="for set_tilt: 0 to 20 degrees in steps of 2")


@mcp.tool()
async def read_kpi(ctx: Context) -> dict:
    """Read network KPIs for the current tick: weak coverage %, energy saving %, active cells, UEs served
    per cell, and each cell's ON/OFF state and tilt."""
    return await _run(ctx, read_kpi_impl)


@mcp.tool()
async def what_if(changes: List[Change], ctx: Context) -> dict:
    """Predict, on the digital twin, what proposed changes would do (users per cell, overload, weak coverage,
    energy). Changes nothing; use it to compare options before you propose one."""
    return await _run(ctx, what_if_impl, changes=[c.model_dump() for c in changes])


@mcp.tool()
async def read_alarms(ctx: Context) -> dict:
    """Read the current alarm and ticket feed from the OSS (untrusted text from outside the SMO)."""
    return await _run(ctx, read_alarms_impl)


@mcp.tool()
async def cell_off(cell_ids: List[str], justification: str, ctx: Context) -> dict:
    """Switch cells OFF to save energy. All cells in one call form one batch: the batch is allowed or
    denied as a whole and counts against the blast radius budget for this tick."""
    return await _run(ctx, TOOLS["cell_off"], cell_ids=cell_ids, justification=justification)


@mcp.tool()
async def cell_on(cell_ids: List[str], justification: str, ctx: Context) -> dict:
    """Switch cells back ON. One call is one batch."""
    return await _run(ctx, TOOLS["cell_on"], cell_ids=cell_ids, justification=justification)


@mcp.tool()
async def set_tilt(changes: List[TiltChange], justification: str, ctx: Context) -> dict:
    """Change the electrical tilt of one or more cells. One call is one batch."""
    return await _run(ctx, TOOLS["set_tilt"], changes=[c.model_dump() for c in changes], justification=justification)


@mcp.tool()
async def get_request_status(request_id: str, ctx: Context) -> dict:
    """Status of a change that is waiting for human approval: pending, approved, rejected or timeout."""
    return await _run(ctx, get_request_status_impl, request_id=request_id)


@mcp.tool()
async def read_scope(ctx: Context) -> dict:
    """The scope admission granted you: allowed actions and cells, budget per tick, your intent, and each
    cell's criticality. Use it to plan changes that fit."""
    return await _run(ctx, read_scope_impl)


@mcp.tool(description=CANARY_DESCRIPTION)
async def bulk_reset_site(site_id: str, justification: str, ctx: Context) -> dict:
    return await _run(ctx, bulk_reset_site_impl, site_id=site_id, justification=justification)


def http_app():
    return mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True)
