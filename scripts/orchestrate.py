"""Run the Orchestrator agent on an operator intent (needs the stack running and a model configured).

    python -m scripts.orchestrate "Stadium concert tonight at 20:00 near Site 2/3. Prepare the network."
    python -m scripts.orchestrate --event --tick 20 "..."     # also add the stadium event to the twin first

Delegates run in-process (standalone, and on the twin host on AWS). If ES_RUNTIME_ARN / LB_RUNTIME_ARN
are set, delegation goes to the agents on AgentCore Runtime instead (agent-to-agent).
"""

import argparse
import json

import httpx

from agents.common import aws_mode
from agents.energy_saving.agent import run_energy_agent
from agents.load_balancing.agent import run_lb_agent
from agents.orchestrator.agent import run_orchestrator
from agents.remote import invoke_runtime, runtime_arn
from demo.steps import GATEWAY_URL, STADIUM
from rapps.client import GatewayClient
from rapps.energy_saving import EnergySavingRapp
from rapps.load_balancing import LoadBalancingRapp

ORCH_CARD = "policies/rapp_cards/orchestrator-agent.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("intent")
    ap.add_argument("--event", action="store_true", help="add the stadium concert to the twin first")
    ap.add_argument("--tick", type=int, default=None)
    a = ap.parse_args()
    ops = httpx.Client(base_url=GATEWAY_URL, timeout=600)
    if a.tick is not None:
        ops.post("/ops/tick", json={"tick": a.tick}).raise_for_status()
    if a.event:
        ops.post("/ops/event", json=STADIUM).raise_for_status()

    es, lb = EnergySavingRapp(GatewayClient(GATEWAY_URL)), LoadBalancingRapp(GatewayClient(GATEWAY_URL))
    orch = GatewayClient(GATEWAY_URL)
    for name, adm in (("energy", es.register()), ("load_balancing", lb.register()),
                      ("orchestrator", orch.admit(json.load(open(ORCH_CARD, encoding="utf-8"))))):
        print(f"admission {name}: admitted={adm.get('admitted')} {adm.get('error') or ''}")

    delegates = {
        "load_balancing": (lambda t: invoke_runtime("LB", {"intent": t})) if runtime_arn("LB") else (lambda t: run_lb_agent(lb, t)),
        "energy": (lambda t: invoke_runtime("ES", {"intent": t})) if runtime_arn("ES") else (lambda t: run_energy_agent(es, t)),
    }
    out = run_orchestrator(a.intent, delegates, token=None if aws_mode() else orch.token)
    print(json.dumps({"summary": out["summary"], "delegations": [
        {"agent": d["agent"], "task": d["task"],
         "decisions": [(c["tool"], (c.get("result") or {}).get("decision"), (c.get("result") or {}).get("policy_id"))
                       for c in d["run"].get("tool_calls", []) if c["tool"] in ("cell_off", "cell_on", "set_tilt")]}
        for d in out["delegations"]], "usage": out["usage"]}, indent=2))


if __name__ == "__main__":
    main()
