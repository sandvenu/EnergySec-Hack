"""Load-balancing agent (Strands), owned by the RAN capacity team.

Intent: keep every cell below capacity during busy hours and planned events. It reads users per cell,
compares tilt plans on the twin with `what_if`, and proposes the best one through the gateway.
Its intent card's guards (max cell load, users above capacity) are what the framework protects when
other agents act later. The model comes from agents/model_factory.py (prefix LB_).
"""

from typing import Optional

from strands import Agent

from agents.budget import from_scope
from agents.common import final_text, gateway_tools, new_session, read_scope, transcript
from agents.model_factory import build_model, describe
from agents.usage import usage_of
from rapps.load_balancing import LoadBalancingRapp

SYSTEM_PROMPT = """You are the Load-balancing agent of the RAN capacity team, for a 15-cell network: 5 sites
(Site1..Site5) with 3 sectors each, cell IDs cell_<site>_<sector>. You act only through the tools provided.

Intent: keep every cell below 250 users, and as few users as possible above the {capacity}-user cell capacity,
during busy hours and planned events.

How to work:
1. Call read_kpi: users per cell, max_cell_load, excess_ues (users above capacity) and any planned event.
2. If a cell is above 250 users, or excess_ues is high, use what_if to compare two to four tilt plans before
   acting. Tilt is 0 to 20 degrees in steps of 2: a higher tilt shrinks a cell's footprint, a lower tilt
   extends it. Useful plans shrink the overloaded site and extend a neighbouring site that can absorb users.
   In this network model the three sectors of a site have nearly the same footprint, so users move freely
   between sectors of one site: shrinking one sector only moves its users to a sibling. Shrink all sectors of
   the overloaded site together, and extend the neighbouring site's sectors.
3. Choose the plan with the lowest max_cell_load that does not raise weak coverage by more than 2 points,
   and submit it as ONE set_tilt call with all its changes (at most 6 cells per hour) and a one-sentence
   justification citing the predicted numbers.
4. If the call returns pending_approval, a human decides; do not resubmit.

Finish with a short summary: the plans you compared, what you proposed, and the decision with its policy_id."""


def run_lb_agent(rapp: LoadBalancingRapp, intent: str, session: Optional[str] = None, capacity: int = 150) -> dict:
    session = session or new_session("lb")
    token = rapp.gw.token if rapp is not None and rapp.gw is not None else None
    with gateway_tools(token, session, "LB") as mcp:
        budget = from_scope(read_scope(mcp))
        agent = Agent(model=build_model("LB"), system_prompt=SYSTEM_PROMPT.format(capacity=capacity),
                      tools=mcp.list_tools_sync(), hooks=[budget], callback_handler=None, name="load-balancing-agent")
        result = agent(intent)
        calls = transcript(agent.messages)
    model = describe("LB")
    return {"agent": "load-balancing-agent", "session": session, "model": model, "intent": intent,
            "tool_calls": calls, "summary": final_text(result), "stop_reason": str(result.stop_reason),
            "usage": usage_of(result, model), "token_budget": budget.report()}
