"""Token budget for one agent run (the card's model_budget.max_tokens_per_run, read through read_scope).

A Strands hook cancels the next model call once the run has used its budget, so a looping agent stops
on its own. The daily cost cap is enforced at the gateway (framework/pep.py, BUDGET-MODEL-COST) from
the runs' recorded usage; this hook is the per-run limit inside the harness.
"""

from typing import Optional

from strands.hooks import BeforeModelCallEvent, HookProvider, HookRegistry


class TokenBudget(HookProvider):
    def __init__(self, max_tokens: Optional[int]):
        self.max_tokens = max_tokens
        self.stopped_at: Optional[int] = None

    def register_hooks(self, registry: HookRegistry, **_) -> None:
        if self.max_tokens:
            registry.add_callback(BeforeModelCallEvent, self.check)

    def check(self, event: BeforeModelCallEvent) -> None:
        used = int(event.agent.event_loop_metrics.accumulated_usage.get("totalTokens", 0) or 0)
        if used >= self.max_tokens:
            self.stopped_at = used
            event.cancel = f"Token budget reached ({used} of {self.max_tokens} tokens for this run). Stop and summarise."

    def report(self) -> dict:
        return {"max_tokens_per_run": self.max_tokens, "stopped": self.stopped_at is not None, "stopped_at": self.stopped_at}


def from_scope(scope: dict) -> TokenBudget:
    return TokenBudget((scope.get("model_budget") or {}).get("max_tokens_per_run"))
