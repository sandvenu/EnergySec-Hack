"""Input screening (SC-1): scores untrusted text for injected instructions before an LLM rApp sees it.

HeuristicScreener is the standalone detector. On AWS, GuardrailsScreener calls Bedrock
ApplyGuardrail (prompt-attack filter) and combines its verdict with the heuristic score.
The detector is deliberately simple; TelcoAgentBench reports its misses, and the scope check
at the enforcement point (SC-2) is the control that must still hold.
"""

import re
from dataclasses import dataclass, field
from typing import List

FLAG_THRESHOLD = 0.5

# (pattern, weight, label). Weights add up and are capped at 1.0.
PATTERNS = [
    (r"\bignore (all |any )?(previous|prior|above|earlier) (instructions|rules|guidance)", 0.7, "override-instructions"),
    (r"\b(disregard|forget) (your|all|the) (rules|instructions|policy|policies)", 0.7, "override-instructions"),
    (r"\byou are now\b|\bact as (the )?(admin|operator|root)", 0.5, "role-hijack"),
    (r"\b(system|assistant)\s*:\s", 0.4, "fake-role-tag"),
    (r"<\s*/?\s*(system|instructions?|tool_call)\s*>", 0.5, "fake-markup"),
    (r"\b(new|updated|urgent) instructions?\b", 0.4, "instruction-injection"),
    (r"\b(you must|immediately|right now|without (asking|approval|confirmation))\b", 0.25, "urgency"),
    (r"\b(switch|turn|shut|power)(ing)?\s+(off|down)\b.*\b(cell|sector|site)", 0.35, "embedded-action-off"),
    (r"\b(set|change|adjust)\s+(the\s+)?(e-?tilt|tilt)\b", 0.3, "embedded-action-tilt"),
    (r"\b(grant|give|escalate|elevate|widen|expand)\b.*\b(scope|rights|permissions?|privileges?|admin)\b", 0.5, "scope-escalation"),
    (r"\b(do not|don't) (tell|inform|log|notify)\b", 0.4, "concealment"),
    (r"[A-Za-z0-9+/]{40,}={0,2}", 0.3, "encoded-payload"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), w, label) for p, w, label in PATTERNS]


@dataclass
class ScreeningResult:
    flagged: bool
    score: float
    matches: List[str] = field(default_factory=list)
    engine: str = "heuristic"

    def as_dict(self) -> dict:
        return {"flagged": self.flagged, "score": self.score, "matches": self.matches, "engine": self.engine}


class Screener:
    def screen(self, text: str) -> ScreeningResult:
        raise NotImplementedError


class HeuristicScreener(Screener):
    def __init__(self, threshold: float = FLAG_THRESHOLD):
        self.threshold = threshold

    def screen(self, text: str) -> ScreeningResult:
        score, matches = 0.0, []
        for rx, weight, label in _COMPILED:
            if rx.search(text or ""):
                score += weight
                matches.append(label)
        score = round(min(score, 1.0), 2)
        return ScreeningResult(score >= self.threshold, score, matches)


def quarantine(text: str, result: ScreeningResult) -> str:
    return f"[QUARANTINED by screening: injection score {result.score} ({', '.join(result.matches)}); original withheld]"
