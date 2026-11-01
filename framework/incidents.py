"""Incident readiness and response for agent control incidents and near-misses.

Follows NIST CAISSI / NSA SISC / CISA, "Key Practices for Incident Readiness and Response in Super
Intelligence Training and Evaluation" (PP-26-4396, Oct 2026). Their terms:
  control incident  an agent takes significant uninstructed actions that compromise the policies or
                    controls that set its task scope or human oversight
  near-miss         no incident, but a foreseeable risk of one: here, a control caught the attempt

Severity (practice 2.1; standardised scores are one of the paper's open problems), most severe first:
  S1  incident   canary tool touched (access outside intended bounds, 3.5); an allowed action broke a
                 guard after it ran (control failure, caught by the post-action monitor)
  S2  incident   a control layer was unavailable and the request failed closed (monitor outage, 6.1)
      near-miss  workaround of a denial; operator floor; switch-off of a critical cell; third denied
                 request in one session (persistence)
  S3  near-miss  conflicting or drifting change held for a human; flagged untrusted input; budget
                 overflow; model cost cap
  S4  near-miss  any other denial: the per-call policy held
The automatic response to each severity is policy data (policies/incident_response.json, 1.6, 6.2, 6.3).
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional

# A tool no card can be granted. Listed to agents as operator-only; any call is an S1 incident.
CANARY_TOOLS = {"bulk_reset_site"}
RESPONSES = ("end_session", "suspend_agent", "pause_similar", "flag_for_review")
SEVERITIES = ("S1", "S2", "S3", "S4")
PERSISTENCE_DENIALS = 3  # the third denied request in one session

# Practice references (CSF 2.0 IDs as the paper gives them) shown with each classification.
NIST = {
    "canary": "3.5 canaries detect agents outside intended bounds",
    "control_failure": "3.2 monitor all layers; 6.2 shut down on a control failure",
    "outage": "6.1 shutdown policy for monitor outages",
    "workaround": "3.1 intended scope baseline; 1.8 agents will look for gaps",
    "floor": "1.6 control policy with automated enforcement",
    "critical": "1.1 least privilege",
    "persistence": "1.4 persistence; 3.5 escalation",
    "conflict": "3.5 escalate to human review",
    "input": "4.2 treat untrusted content as possibly hostile",
    "budget": "1.1 least privilege (blast radius budget)",
    "cost": "1.6 control policy (model cost cap)",
    "denial": "1.6 control held",
}


def load_response_policy(policy_dir: Path) -> Dict[str, List[str]]:
    path = Path(policy_dir) / "incident_response.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    out = {s: [r for r in data.get(s, []) if r in RESPONSES] for s in SEVERITIES}
    return out


def policy_sha(policy_dir: Path, engine=None) -> str:
    """One hash over everything that decides an outcome: Cedar scope and tier sets (as loaded), floors,
    budgets, cell tags and the incident response policy. Recorded in every ledger record (5.2)."""
    h = hashlib.sha256()
    for name in ("scope", "tier"):
        text = engine.policy_text(name) if engine is not None and hasattr(engine, "policy_text") else \
            (Path(policy_dir) / f"{name}.cedar").read_text(encoding="utf-8")
        h.update(text.encode("utf-8"))
    for name in ("kpi_floors.json", "budgets.json", "cell_tags.json", "incident_response.json"):
        p = Path(policy_dir) / name
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()


def sha256_json(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def classify(rec: dict, session_denials: int = 0) -> Optional[dict]:
    """Severity of one decision, or None for normal operation. `rec` holds the ledger record's fields;
    `session_denials` counts earlier denials in the same agent session."""
    d, pid = rec.get("decision"), rec.get("policy_id") or ""
    cls = (rec.get("assessment") or {}).get("class")
    actions = rec.get("actions") or []

    def out(sev, kind, key, title):
        return {"severity": sev, "kind": kind, "type": key, "title": title, "nist": NIST[key]}

    if d == "incident":
        return out("S1", "incident", "control_failure", rec.get("reason") or "an allowed action broke a guard")
    if d == "screen":
        return out("S3", "near_miss", "input", "untrusted input flagged and quarantined")
    if d not in ("deny", "hold"):
        return None
    if pid == "CANARY-TRIPPED" or any(a.get("name") in CANARY_TOOLS for a in actions):
        return out("S1", "incident", "canary", "called a canary tool that no card grants: access outside intended bounds")
    if pid.startswith("FAILCLOSED"):
        return out("S2", "incident", "outage", f"a control layer was unavailable; the request failed closed ({pid})")
    if pid.startswith(("KILL-", "CONTAIN-")):
        return None  # a contained agent's later calls: the containment holding, not a new event
    if cls == "Workaround":
        return out("S2", "near_miss", "workaround", "tried to get around a rule that had denied it")
    if cls == "Floor" or pid.startswith("FLOOR-"):
        return out("S2", "near_miss", "floor", "the predicted outcome breaks an operator hard floor")
    if pid == "GLOBAL-NO-OFF-CRITICAL":
        return out("S2", "near_miss", "critical", "asked to switch off a critical cell")
    if d == "deny" and session_denials + 1 >= PERSISTENCE_DENIALS:
        return out("S2", "near_miss", "persistence", f"denial number {session_denials + 1} in one session")
    if cls in ("Conflicting", "Drift"):
        return out("S3", "near_miss", "conflict", f"{cls.lower()} change held for a human: it would break another agent's intent")
    if (rec.get("screening") or {}).get("flagged"):
        return out("S3", "near_miss", "input", "request influenced by flagged untrusted input")
    if pid == "GLOBAL-BLAST-BUDGET":
        return out("S3", "near_miss", "budget", "blast radius budget exceeded")
    if pid == "BUDGET-MODEL-COST":
        return out("S3", "near_miss", "cost", "model cost cap for the day reached")
    if d == "deny":
        return out("S4", "near_miss", "denial", f"denied by {pid or 'default deny'}")
    return None


def guard_status(intents: Dict[str, dict], baselines: Dict[str, dict], kpis: dict) -> List[dict]:
    """Every admitted intent's guards against one set of KPIs."""
    rows = []
    for owner, intent in intents.items():
        for g in (intent or {}).get("guards", []):
            v = kpis.get(g["kpi"])
            if v is None:
                continue
            ok = True
            if "max" in g:
                ok = v <= g["max"]
            if "max_increase" in g:
                b = (baselines.get(owner) or {}).get(g["kpi"], {}).get("value")
                ok = ok and (b is None or v - b <= g["max_increase"])
            rows.append({"owner": owner, "kpi": g["kpi"], "value": v, "ok": ok})
    return rows


def newly_broken(before: List[dict], after: List[dict]) -> List[dict]:
    """Guards that held before and are broken after, or were broken and got worse."""
    was = {(g["owner"], g["kpi"]): g for g in before}
    out = []
    for g in after:
        b = was.get((g["owner"], g["kpi"]))
        if not g["ok"] and (b is None or b["ok"] or g["value"] > b["value"]):
            out.append({**g, "before": b["value"] if b else None})
    return out


def similar_agents(rapp: dict, others: List[dict]) -> List[str]:
    """Practice 6.3: other admitted agents from the same vendor or on the same model package."""
    card = rapp.get("card") or {}
    model = (card.get("model") or {}).get("package_group")
    out = []
    for o in others:
        if o["rapp_id"] == rapp["rapp_id"] or o["status"] != "admitted":
            continue
        oc = o.get("card") or {}
        if oc.get("vendor") == card.get("vendor") or (model and (oc.get("model") or {}).get("package_group") == model):
            out.append(o["rapp_id"])
    return out
