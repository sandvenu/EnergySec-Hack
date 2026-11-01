"""Reproduce vendor-agent runs with the D4 alarm feed and print stop reasons and tool calls."""

import sys
import warnings

warnings.filterwarnings("ignore")

import httpx  # noqa: E402

from agents.vendor.agent import run_vendor_agent  # noqa: E402
from demo.steps import BENIGN_ALARM, PLAUSIBLE_TICKET, POISONED_ALARM  # noqa: E402
from rapps.client import GatewayClient  # noqa: E402
from rapps.third_party_llm import ThirdPartyLlmRapp  # noqa: E402

GW = "http://127.0.0.1:8700"
n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
defenses = (sys.argv[2] if len(sys.argv) > 2 else "on") == "on"
ops = httpx.Client(base_url=GW, timeout=300)
ops.post("/ops/reset", json={"registrations": True, "tick": 4}).raise_for_status()
ops.post("/ops/settings", json={"enforcement": defenses, "screening": defenses}).raise_for_status()
tp = ThirdPartyLlmRapp(GatewayClient(GW))
tp.register()
for text in (BENIGN_ALARM, POISONED_ALARM, PLAUSIBLE_TICKET):
    ops.post("/ops/alarms", json={"text": text})
for i in range(n):
    run = run_vendor_agent(tp.gw.token)
    calls = [(c["tool"], (c.get("result") or {}).get("decision") if isinstance(c.get("result"), dict) else c.get("status"))
             for c in run["tool_calls"]]
    print(f"run {i + 1}: stop={run['stop_reason']} calls={calls} summary_len={len(run['summary'])}")
    ops.post("/ops/reset", json={"tick": 4})
    for text in (BENIGN_ALARM, POISONED_ALARM, PLAUSIBLE_TICKET):
        ops.post("/ops/alarms", json={"text": text})
ops.post("/ops/settings", json={"enforcement": True, "screening": True})
