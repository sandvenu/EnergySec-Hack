"""Energy Saving rApp agent (Strands): reasons over MAVERIC's PPO recommendation and live KPIs,
then proposes cell changes through the SMO gateway with a justification.

Tools: `rl_recommend` (local: this rApp's own RL model) + the gateway's MCP tools (read_kpi,
cell_off, cell_on, set_tilt, get_request_status). A Strands BeforeToolCallEvent hook checks each
proposal against the scope admission granted, so the agent plans within policy. That check is a
planning aid inside our own code; the gateway's checks are the control.

The model comes from agents/model_factory.py (prefix ES_), so the same code runs on Claude now and
on Bedrock (or another Strands provider) later.
"""

import logging
from typing import Optional

from strands import Agent, tool
from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

from agents.budget import from_scope
from agents.common import base_tool, final_text, gateway_tools, new_session, read_scope, transcript
from agents.model_factory import build_model, describe
from agents.usage import usage_of
from rapps.energy_saving import EnergySavingRapp

logger = logging.getLogger("agent.energy_saving")

MUTATING = {"cell_off": "cell_off", "cell_on": "cell_on", "set_tilt": "set_tilt"}

SYSTEM_PROMPT = """You are the Energy agent of the RAN energy team, for a 15-cell network: 5 sites (Site1..Site5)
with 3 sectors each, cell IDs cell_<site>_<sector>, for example cell_2_0. You act only through the tools provided.

Intent: minimise network energy use whenever traffic allows, by switching cells off (or adjusting tilt),
without raising weak coverage (the share of users below the coverage threshold) by more than 8 points.

Your admitted scope:
{scope}

How to work:
1. Call rl_recommend for your reinforcement-learning model's suggestion for this hour, and read_kpi for
   current KPIs, users per cell and cell states.
2. Choose a plan. Prefer cells that serve few users. You may use what_if to predict a change before you
   propose it. Stay within your budget and scope; the RL suggestion is input, not an order.
3. Submit each change with cell_off, cell_on or set_tilt and a one-sentence justification citing load and KPIs.
4. If a call is denied, note the policy_id and do not resend the same request.
5. If a call returns pending_approval, a human decides; do not resubmit. Report its request_id.

Finish with a short summary: what you proposed, each decision with its policy_id and reason, and the energy saving."""


class ScopeSelfCheck(HookProvider):
    """Cancels a proposal that is outside the granted scope before it leaves the agent."""

    def __init__(self, scope: dict):
        self.scope = scope
        self.blocked = []

    def register_hooks(self, registry: HookRegistry, **_) -> None:
        registry.add_callback(BeforeToolCallEvent, self.check)

    def check(self, event: BeforeToolCallEvent) -> None:
        name = base_tool(event.tool_use["name"])
        if name not in MUTATING:
            return
        args = event.tool_use.get("input") or {}
        cells = args.get("cell_ids") or [c.get("cell_id") for c in args.get("changes") or []]
        granted = set((self.scope["actions"].get(MUTATING[name]) or {}).get("cells") or [])
        budget = self.scope["max_cells_per_tick"]
        problems = []
        outside = sorted(set(cells) - granted)
        if outside:
            problems.append(f"{name} is not in your scope for {', '.join(outside)}")
        if len(set(cells)) > budget:
            problems.append(f"{len(set(cells))} cells exceed your budget of {budget} cells per tick")
        if problems:
            msg = "Self-check, not sent to the gateway: " + "; ".join(problems) + ". Revise the plan."
            self.blocked.append({"tool": name, "input": args, "reason": msg})
            event.cancel_tool = msg


def scope_text(scope: dict) -> str:
    crit = scope.get("cell_criticality", {})
    lines = [f"- budget: at most {scope['max_cells_per_tick']} distinct cells changed per tick"]
    for action, s in scope["actions"].items():
        if action in ("read_kpi", "what_if"):
            continue
        by = {}
        for c in s["cells"]:
            by.setdefault(crit.get(c, "unknown"), []).append(c)
        groups = "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(by.items()))
        lines.append(f"- {action}: {len(s['cells'])} cells ({groups})")
    lines.append("- risk tiers: one cell_off on a low-criticality cell runs immediately; cell_off on a "
                 "medium-criticality cell, or any batch of 3 or more cells, is shadow-run and waits for "
                 "human approval. Critical cells are never switched off.")
    lines.append("- operator rule: a cell may only be switched off while it carries 80 users or fewer.")
    return "\n".join(lines)


def run_energy_agent(rapp: EnergySavingRapp, intent: str, self_check: bool = True,
                     session: Optional[str] = None) -> dict:
    """One agent invocation. `rapp` holds the gateway token admission issued (standalone); on AWS the agent
    uses its own Cognito identity and `rapp` may have no gateway client (AgentCore Runtime)."""
    session = session or new_session("es")

    @tool
    def rl_recommend() -> dict:
        """Your reinforcement-learning model's recommended cell changes for the current hour (MAVERIC PPO agent)."""
        try:
            prop = rapp.agent_proposal()
        except Exception as e:  # no trained model here (e.g. AgentCore Runtime: the PPO model runs on the twin host)
            return {"available": False, "error": str(e)}
        return {"available": True, "tick": prop.get("tick"), "agent": prop.get("agent"), "proposed_actions": prop.get("actions")}

    token = rapp.gw.token if rapp is not None and rapp.gw is not None else None
    with gateway_tools(token, session, "ES") as mcp:
        scope = read_scope(mcp)
        budget = from_scope(scope)
        hooks = ([ScopeSelfCheck(scope)] if self_check else []) + [budget]
        agent = Agent(model=build_model("ES"), system_prompt=SYSTEM_PROMPT.format(scope=scope_text(scope)),
                      tools=[rl_recommend, *mcp.list_tools_sync()], hooks=hooks, callback_handler=None,
                      name="energy-saving-rl")
        result = agent(intent)
        calls = transcript(agent.messages)
    model = describe("ES")
    return {"agent": "energy-saving-rl", "session": session, "model": model, "intent": intent,
            "tool_calls": calls, "self_check_blocked": hooks[0].blocked if self_check else [],
            "summary": final_text(result), "stop_reason": str(result.stop_reason), "usage": usage_of(result, model),
            "token_budget": budget.report()}
