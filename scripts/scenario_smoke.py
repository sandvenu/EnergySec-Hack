"""Scripted run of the multi-agent scenario through the real gateway and twin (no LLM).
    python -m scripts.scenario_smoke        (needs python run_demo.py)
"""

import json

import httpx

from rapps.client import GatewayClient

GW = "http://127.0.0.1:8700"
STADIUM = {"name": "Stadium concert", "lon": -74.00607, "lat": 40.76665, "ues": 350, "sigma_deg": 0.004, "ticks": [20]}


def card(name):
    return json.load(open(f"policies/rapp_cards/{name}.json", encoding="utf-8"))


def show(label, o):
    a = next((c["detail"] for c in o.get("checks", []) if c.get("check") == "impact_assessment"), None)
    print(f"{label:46s} {o['decision']:17s} {o.get('policy_id') or '':28s} "
          f"{(a or {}).get('class', '-'):12s} {((a or {}).get('why') or o.get('reason') or '')[:150]}")
    return o


def main():
    ops = httpx.Client(base_url=GW, timeout=300)
    ops.post("/ops/settings", json={"enforcement": True, "screening": True, "assessment": True})
    ops.post("/ops/reset", json={"registrations": True, "tick": 2})
    ops.delete("/ops/event")
    es, lb = GatewayClient(GW), GatewayClient(GW)
    print("admit ES:", es.admit(card("energy-saving-rl"))["admitted"], "| admit LB:", lb.admit(card("load-balancing-agent"))["admitted"])

    show("02:00 ES cell_off cell_2_1 (routine)", es.submit([{"name": "cell_off", "cell_id": "cell_2_1"}], "night, 15 users"))

    ops.post("/ops/reset", json={"tick": 20})
    ops.post("/ops/event", json=STADIUM)
    k = ops.get("/ops/kpis").json()
    print(f"20:00 concert: max load {k['max_cell_load']} excess {k['excess_ues']} weak {k['weak_coverage_pct']}")
    lb_batch = [{"name": "set_tilt", "cell_id": f"cell_3_{i}", "tilt": 20.0} for i in range(3)] + \
               [{"name": "set_tilt", "cell_id": f"cell_2_{i}", "tilt": 8.0} for i in range(3)]
    o = show("LB rebalances (6 tilts)", lb.submit(lb_batch, "spread the stadium crowd"))
    if o.get("request_id"):
        r = ops.post(f"/ops/approvals/{o['request_id']}", json={"approve": True, "approver": "smoke"}).json()
        print("   approved ->", r.get("decision"), "max load now", r["result"]["kpis"]["max_cell_load"], "excess", r["result"]["kpis"]["excess_ues"])
    show("ES cell_off cell_2_2 (78 users)", es.submit([{"name": "cell_off", "cell_id": "cell_2_2"}], "low load"))
    o = show("ES cell_off cell_2_0 (71 users)", es.submit([{"name": "cell_off", "cell_id": "cell_2_0"}], "low load"))
    if o.get("request_id"):
        ops.post(f"/ops/approvals/{o['request_id']}", json={"approve": False, "approver": "smoke"})
    show("ES cell_off cell_2_1 (busy)", es.submit([{"name": "cell_off", "cell_id": "cell_2_1"}], "save energy"))
    o = show("ES set_tilt cell_2_1 -> 12 (drain it)", es.submit([{"name": "set_tilt", "cell_id": "cell_2_1", "tilt": 12.0}], "reduce load"))
    if o.get("request_id"):
        ops.post(f"/ops/approvals/{o['request_id']}", json={"approve": False, "approver": "smoke"})
    print("what_if:", json.dumps(httpx.get(f"{GW}/ops/kpis").json()["max_cell_load"]))


if __name__ == "__main__":
    main()
