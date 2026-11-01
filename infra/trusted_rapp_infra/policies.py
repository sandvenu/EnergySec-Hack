"""AgentCore Policy statements generated from the framework's own policy files.

policies/scope.cedar stays the source of truth: the enforcement point evaluates it on every call
(with live load, budget and taint, which AgentCore Policy cannot see). This module emits the part
that can be decided from the caller's identity and the tool input alone, so the gateway refuses it
before it reaches the SMO at all: defense in depth with the same policy IDs.

    principal   AgentCore::OAuthUser::"<Cognito client ID>"   (one app client per agent)
    action      AgentCore::Action::"smo-tools___<tool>"
    context     context.input.cell_ids (cell_off / cell_on)    criticality from policies/cell_tags.json

set_tilt's `changes` is a set of records, which Cedar cannot project cell IDs out of, so tilt cell
limits stay with the enforcement point (LB-TILT, critical cells).
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple

from trusted_rapp_infra.tools import READ_TOOLS, action

ROOT = Path(__file__).resolve().parents[2]


def _cells(tag: str) -> List[str]:
    tags = json.loads((ROOT / "policies" / "cell_tags.json").read_text(encoding="utf-8"))
    return sorted(c for c, t in tags.items() if not c.startswith("_") and t == tag)


def _set(items: List[str]) -> str:
    return "[" + ", ".join(f'"{c}"' for c in items) + "]"


def statements(clients: Dict[str, str], gateway_arn: str) -> List[Tuple[str, str, str]]:
    """(name, policy ID it mirrors, Cedar statement). `clients` maps agent key -> Cognito client ID (tokens)."""
    gw = f'AgentCore::Gateway::"{gateway_arn}"'
    low, medium, critical = _cells("low"), _cells("medium"), _cells("critical")
    non_critical = sorted(low + medium)

    def who(key: str) -> str:
        return f'AgentCore::OAuthUser::"{clients[key]}"'

    def acts(tools: List[str]) -> str:
        return "[" + ", ".join(action(t) for t in tools) + "]"

    out = [
        ("es_read", "ES-READ", f"permit(principal == {who('es')}, action in {acts(READ_TOOLS)}, resource == {gw});"),
        ("es_tilt_on", "ES-TILT,ES-ON", f"permit(principal == {who('es')}, action in {acts(['set_tilt', 'cell_on'])}, resource == {gw});"),
        ("es_off", "ES-OFF-LOW,ES-OFF-MEDIUM",
         f"permit(principal == {who('es')}, action == {action('cell_off')}, resource == {gw}) "
         f"when {{ {_set(non_critical)}.containsAll(context.input.cell_ids) }};"),
        ("lb_read", "LB-READ", f"permit(principal == {who('lb')}, action in {acts(READ_TOOLS)}, resource == {gw});"),
        ("lb_tilt_on", "LB-TILT,LB-ON", f"permit(principal == {who('lb')}, action in {acts(['set_tilt', 'cell_on'])}, resource == {gw});"),
        ("vendor_read", "3P-READ",
         f"permit(principal == {who('vendor')}, action in {acts(READ_TOOLS + ['read_alarms'])}, resource == {gw});"),
        ("orch_read", "ORCH-READ", f"permit(principal == {who('orch')}, action in {acts(READ_TOOLS)}, resource == {gw});"),
        # The vendor agent is let through to the SMO for write tools only so the framework can show (and
        # record) the denial; AgentCore Policy keeps the hard line on critical cells for every caller.
        ("vendor_write_to_pep", "3P-READ-ONLY (decided by the SMO, recorded in the ledger)",
         f"permit(principal == {who('vendor')}, action in {acts(['cell_off', 'cell_on', 'set_tilt'])}, resource == {gw});"),
        ("global_no_off_critical", "GLOBAL-NO-OFF-CRITICAL",
         f"forbid(principal, action == {action('cell_off')}, resource == {gw}) "
         f"when {{ context.input.cell_ids.containsAny({_set(critical)}) }};"),
        ("orch_read_only", "ORCH-READ-ONLY",
         f"forbid(principal == {who('orch')}, action in {acts(['cell_off', 'cell_on', 'set_tilt', 'read_alarms'])}, resource == {gw});"),
    ]
    # The canary must reach the SMO, which denies it, records the S1 incident and contains the caller.
    # One permit per agent client: AgentCore Policy refuses an "any principal" permit as overly permissive.
    out += [(f"canary_to_smo_{key}", "CANARY-TRIPPED",
             f"permit(principal == {who(key)}, action == {action('bulk_reset_site')}, resource == {gw});")
            for key in ("es", "lb", "vendor", "orch")]
    return out
