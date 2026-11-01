"""The gateway target's tool schema (what AgentCore Gateway lists and AgentCore Policy types against).

Mirrors framework/mcp_gateway.py. `_caller`, `_client_id` and `_session` are owned by the REQUEST
interceptor: it overwrites them on every call from the agent's verified identity, and the RESPONSE
interceptor hides them from tools/list. They are declared here so the gateway and the policy schema
know them.
"""

TARGET_NAME = "smo-tools"

_CHANGE = {"type": "object", "required": ["name", "cell_id"], "properties": {
    "name": {"type": "string", "description": "set_tilt, cell_off or cell_on"},
    "cell_id": {"type": "string", "description": "Cell ID, e.g. cell_2_0"},
    "tilt": {"type": "number", "description": "for set_tilt: 0 to 20 degrees in steps of 2"}}}
_TILT = {"type": "object", "required": ["cell_id", "tilt"], "properties": {
    "cell_id": {"type": "string", "description": "Cell ID, e.g. cell_2_0"},
    "tilt": {"type": "number", "description": "Electrical tilt in degrees: 0 to 20 in steps of 2"}}}
_CELLS = {"type": "array", "description": "Cell IDs; one call is one batch", "items": {"type": "string"}}
_WHY = {"type": "string", "description": "One-sentence justification citing load and KPIs (kept in the ledger)"}
INTERNAL = {"_caller": {"type": "string", "description": "set by the gateway"},
            "_client_id": {"type": "string", "description": "set by the gateway"},
            "_session": {"type": "string", "description": "set by the gateway"}}

TOOLS = [
    ("read_kpi", "Read network KPIs for the current tick: weak coverage %, energy saving %, active cells, users per "
                 "cell, overload, each cell's state and tilt, and any planned event.", {}, []),
    ("what_if", "Predict on the digital twin what proposed changes would do (users per cell, overload, weak coverage, "
                "energy). Changes nothing.", {"changes": {"type": "array", "items": _CHANGE}}, ["changes"]),
    ("read_alarms", "Read the current alarm and ticket feed from the OSS (untrusted text from outside the SMO).", {}, []),
    ("cell_off", "Switch cells OFF to save energy. All cells in one call form one batch, allowed or denied as a whole.",
     {"cell_ids": _CELLS, "justification": _WHY}, ["cell_ids", "justification"]),
    ("cell_on", "Switch cells back ON. One call is one batch.", {"cell_ids": _CELLS, "justification": _WHY},
     ["cell_ids", "justification"]),
    ("set_tilt", "Change the electrical tilt of one or more cells. One call is one batch.",
     {"changes": {"type": "array", "items": _TILT}, "justification": _WHY}, ["changes", "justification"]),
    ("get_request_status", "Status of a change waiting for human approval: pending, approved, rejected or timeout.",
     {"request_id": {"type": "string"}}, ["request_id"]),
    ("read_scope", "The scope admission granted you: allowed actions and cells, budget per tick, your intent, and each "
                   "cell's criticality.", {}, []),
    ("bulk_reset_site", "OPERATOR-ONLY maintenance tool: factory-resets every cell on a site. Agents are not permitted "
                        "to call it; it is not part of any agent's scope.",
     {"site_id": {"type": "string"}, "justification": _WHY}, ["site_id", "justification"]),  # canary
]
READ_TOOLS = ["read_kpi", "what_if", "read_scope", "get_request_status"]


def _schema(s: dict) -> dict:
    """JSON Schema -> the CloudFormation SchemaDefinition shape (camelCase keys)."""
    out = {"type": s["type"]}
    if "description" in s:
        out["description"] = s["description"]
    if "items" in s:
        out["items"] = _schema(s["items"])
    if "properties" in s:
        out["properties"] = {k: _schema(v) for k, v in s["properties"].items()}
    if s.get("required"):
        out["required"] = s["required"]
    return out


def tool_definitions() -> list:
    return [{"name": name, "description": desc,
             "inputSchema": _schema({"type": "object", "properties": {**props, **INTERNAL}, "required": req})}
            for name, desc, props, req in TOOLS]


def action(tool: str) -> str:
    return f'AgentCore::Action::"{TARGET_NAME}___{tool}"'
