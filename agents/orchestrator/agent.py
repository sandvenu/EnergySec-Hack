"""Orchestrator agent (Strands): turns an operator intent into tasks for the domain agents.

The blueprint's multi-agent orchestration: the orchestrator reads the network (read-only scope,
ORCH-READ-ONLY) and delegates every change to a domain agent, which acts through the gateway under its
own identity, intent and scope. The framework checks each resulting action against all agents' intents,
so a well-meant plan from the orchestrator cannot bypass it.

Delegates are plain callables (task -> result). Standalone they run the domain agents in-process; on
AWS they call the agents on AgentCore Runtime (agents/remote.py). The model comes from
agents/model_factory.py (prefix ORCH_).
"""

from typing import Callable, Dict, Optional

from strands import Agent, tool

from agents.common import final_text, gateway_tools, new_session, transcript
from agents.model_factory import build_model, describe
from agents.usage import usage_of

SYSTEM_PROMPT = """You are the Orchestrator agent of the network operations team for a 15-cell RAN (5 sites x 3 sectors,
cell IDs cell_<site>_<sector>). You do not change the network yourself: you may read it (read_kpi, what_if)
and you delegate work to domain agents, each owned by a team with its own intent:

- delegate_to_load_balancing: the RAN capacity team's agent. Keeps cells below capacity (tilt changes).
- delegate_to_energy: the RAN energy team's agent. Saves energy (switches lightly loaded cells off).

How to work:
1. Read the network with read_kpi and understand the operator's intent.
2. Split the intent into one task per domain agent that is needed, in a sensible order (capacity before
   energy during busy hours). Write each task as one or two plain sentences with the context it needs.
3. Delegate. Each agent's changes are checked by the SMO framework and may be held for a human or denied;
   report those outcomes, do not try to get around them.

Finish with a short summary: the tasks you delegated, what each agent did, and every decision with its policy_id."""


def run_orchestrator(intent: str, delegates: Dict[str, Callable[[str], dict]], token: Optional[str] = None,
                     session: Optional[str] = None) -> dict:
    """`delegates` maps "load_balancing" / "energy" to a callable that runs that agent on a task."""
    session = session or new_session("orch")
    runs = []

    def _delegate(name: str, task: str) -> dict:
        if name not in delegates:
            return {"error": f"no {name} agent available"}
        out = delegates[name](task)
        runs.append({"agent": name, "task": task, "run": out})
        calls = [{"tool": c["tool"], "input": c.get("input"), "result": c.get("result")}
                 for c in out.get("tool_calls", []) if c["tool"] in ("cell_off", "cell_on", "set_tilt")]
        return {"summary": out.get("summary"), "changes": calls}

    @tool
    def delegate_to_load_balancing(task: str) -> dict:
        """Give the load-balancing agent (RAN capacity team) a task, e.g. rebalance cells for a planned event."""
        return _delegate("load_balancing", task)

    @tool
    def delegate_to_energy(task: str) -> dict:
        """Give the energy agent (RAN energy team) a task, e.g. save energy this hour where traffic allows."""
        return _delegate("energy", task)

    with gateway_tools(token, session, "ORCH") as mcp:
        reads = [t for t in mcp.list_tools_sync() if t.tool_name.split("___")[-1] in ("read_kpi", "what_if")]
        agent = Agent(model=build_model("ORCH"), system_prompt=SYSTEM_PROMPT, callback_handler=None,
                      tools=[delegate_to_load_balancing, delegate_to_energy, *reads], name="orchestrator-agent")
        result = agent(intent)
        calls = transcript(agent.messages)
    model = describe("ORCH")
    return {"agent": "orchestrator-agent", "session": session, "model": model, "intent": intent, "tool_calls": calls,
            "delegations": runs, "summary": final_text(result), "stop_reason": str(result.stop_reason),
            "usage": usage_of(result, model)}
