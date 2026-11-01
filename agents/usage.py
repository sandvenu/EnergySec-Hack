"""Token usage and estimated cost of one agent invocation (theme item: optimize token cost).

Prices are USD per million tokens (input, output, cache read, cache write), Claude API first-party
rates. Bedrock and other providers price differently: set AGENT_PRICES to a JSON object
{"<model_id>": [input, output, cache_read, cache_write]} to add or override a model. Without a price
the cost is reported as null rather than guessed.

Turns declined by the primary model and re-run by the server-side refusal fallback are billed at the
fallback model's rates; Strands does not report which model served a turn, so the estimate prices all
tokens at the primary model's rates and says so.
"""

import json
import os
from typing import Optional

PRICES = {  # USD / 1M tokens: input, output, cache read, cache write (5-minute TTL = 1.25 x input)
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-opus-5": (5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-8": (5.00, 25.00, 0.50, 6.25),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}


def _prices() -> dict:
    table = dict(PRICES)
    extra = os.environ.get("AGENT_PRICES")
    if extra:
        table.update({k: tuple(v) for k, v in json.loads(extra).items()})
    return table


def price_for(model_id: Optional[str]):
    return _prices().get(model_id or "")


def usage_of(result, model: dict) -> dict:
    """`result` is a Strands AgentResult; `model` is model_factory.describe(...)."""
    u = result.metrics.accumulated_usage
    inp, out = int(u.get("inputTokens", 0)), int(u.get("outputTokens", 0))
    cr, cw = int(u.get("cacheReadInputTokens", 0) or 0), int(u.get("cacheWriteInputTokens", 0) or 0)
    p = price_for(model.get("model_id"))
    cost = None
    if p:
        cost = round((inp * p[0] + out * p[1] + cr * p[2] + cw * p[3]) / 1e6, 5)
    return {"model_calls": int(result.metrics.cycle_count), "input_tokens": inp, "output_tokens": out,
            "cache_read_tokens": cr, "cache_write_tokens": cw, "total_tokens": inp + out + cr + cw,
            "est_cost_usd": cost, "price_basis": model.get("model_id") if p else None}


def add(a: dict, b: dict) -> dict:
    keys = ("model_calls", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens")
    out = {k: (a.get(k) or 0) + (b.get(k) or 0) for k in keys}
    costs = [x.get("est_cost_usd") for x in (a, b) if x]
    out["est_cost_usd"] = None if any(c is None for c in costs) else round(sum(costs), 5)
    return out
