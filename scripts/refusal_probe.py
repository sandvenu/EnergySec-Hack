"""Send the vendor agent's first request straight to the Messages API and print stop_details."""

import sys
import warnings

warnings.filterwarnings("ignore")

import anthropic  # noqa: E402
import httpx  # noqa: E402

from agents import model_factory  # noqa: E402,F401  (loads .env and the OS trust store)
from agents.common import gateway_tools  # noqa: E402
from agents.vendor.agent import DEFAULT_TASK, SYSTEM_PROMPT  # noqa: E402
from rapps.client import GatewayClient  # noqa: E402
from rapps.third_party_llm import ThirdPartyLlmRapp  # noqa: E402

GW = "http://127.0.0.1:8700"
n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
ops = httpx.Client(base_url=GW, timeout=120)
ops.post("/ops/reset", json={"registrations": True, "tick": 4}).raise_for_status()
tp = ThirdPartyLlmRapp(GatewayClient(GW))
tp.register()
with gateway_tools(tp.gw.token, "probe") as mcp:
    specs = [t.tool_spec for t in mcp.list_tools_sync()]
tools = [{"name": s["name"], "description": s["description"], "input_schema": s["inputSchema"]["json"]} for s in specs]
client = anthropic.Anthropic()
fallback = "--fallback" in sys.argv
for i in range(n):
    if fallback:
        r = client.beta.messages.create(model="claude-opus-5-5", max_tokens=4000, system=SYSTEM_PROMPT, tools=tools,
                                        messages=[{"role": "user", "content": DEFAULT_TASK}],
                                        betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        served = [e.type for e in (r.usage.iterations or [])]
    else:
        r = client.messages.create(model="claude-opus-5-5", max_tokens=4000, system=SYSTEM_PROMPT, tools=tools,
                                   messages=[{"role": "user", "content": DEFAULT_TASK}])
        served = None
    det = r.stop_details
    print(f"try {i + 1}: stop={r.stop_reason} category={getattr(det, 'category', None)} model={r.model} "
          f"blocks={[b.type for b in r.content]} iterations={served}")
