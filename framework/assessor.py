"""Impact assessor: is an action that passed the per-action policy safe for the whole network?

Per-action policy (Cedar) asks "may this agent do this?". The assessor asks "given every agent's
intent, what the other agents changed recently, and what this does to the network, should it run?".
It runs after Cedar, on the twin's prediction of the request (before/after KPIs), and can only make
the outcome stricter: escalate to a human, or deny on an operator floor. It never allows what Cedar denied.

Inputs (all recorded in the decision): the requester's intent and every other admitted agent's
intent and guards; each guard owner's baseline (the KPI value it left the network at); this hour's
changes by every agent; this agent's denials this hour; its action history from the ledger; the
operator's hard KPI floors; and the twin's predicted KPIs.

Classes, most severe first:
  Floor        an operator hard floor would break                          -> deny
  Workaround   the agent is getting around a rule that denied it            -> human
  Conflicting  another agent's guard would break                            -> human
  Drift        small changes add up past another agent's guard budget      -> human
  High impact  the agent's own guard would break                            -> human
  Overlap      another agent changed nearby cells this hour (guards hold)   -> runs, noted
  Novel        first time the ledger sees this agent take this action       -> runs, noted
  Routine      none of the above                                            -> runs
"""

import math
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

ESCALATE = {"Workaround", "Conflicting", "Drift", "High impact"}
ORDER = ["Floor", "Workaround", "Conflicting", "Drift", "High impact", "Overlap", "Novel", "Routine"]
LOAD_GUARD = 80          # GLOBAL-OFF-ONLY-LOW-LOAD in scope.cedar
LOAD_GUARD_POLICY = "GLOBAL-OFF-ONLY-LOW-LOAD"
NEIGHBOUR_KM = 4.0       # sites closer than this share users, so their cells share a blast area


@dataclass
class Assessment:
    cls: str
    escalate: bool
    deny_policy: Optional[str]
    why: str
    findings: List[dict] = field(default_factory=list)
    guards: List[dict] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["class"] = d.pop("cls")
        return d


def blast_areas(topology: List[dict]) -> Dict[str, set]:
    """cell -> cells whose users it can take or give: its own site and sites within NEIGHBOUR_KM."""
    sites: Dict[str, tuple] = {}
    cells_of: Dict[str, list] = {}
    for c in topology:
        sites[c["site_id"]] = (c["cell_lat"], c["cell_lon"])
        cells_of.setdefault(c["site_id"], []).append(c["cell_id"])

    def km(a, b):
        dlat = (a[0] - b[0]) * 111.0
        dlon = (a[1] - b[1]) * 111.0 * math.cos(math.radians(a[0]))
        return math.hypot(dlat, dlon)

    area = {}
    for s, pos in sites.items():
        near = [t for t, p in sites.items() if t == s or km(pos, p) < NEIGHBOUR_KM]
        cells = {c for t in near for c in cells_of[t]}
        for c in cells_of[s]:
            area[c] = cells
    return area


class ImpactAssessor:
    def __init__(self, floors: dict, topology: List[dict]):
        self.floors = {k: v for k, v in floors.items() if not k.startswith("_")}
        self.area = blast_areas(topology)
        self.site_of = {c["cell_id"]: c["site_id"] for c in topology}

    def assess(self, rapp_id: str, actions: List[dict], shadow: dict, intents: Dict[str, dict],
               baselines: Dict[str, dict], changes: List[dict], denials: List[dict], history: Dict[str, int],
               tick: int) -> Assessment:
        before, after = shadow["before"], shadow["after"]
        cells = sorted({a["cell_id"] for a in actions if a.get("cell_id")})
        findings: List[dict] = []
        classes: List[str] = []

        # 1. operator hard floors
        deny_policy = None
        for kpi, f in self.floors.items():
            if after.get(kpi) is not None and after[kpi] > f["max"] and after[kpi] > before.get(kpi, -1e9):
                deny_policy = f["policy_id"]
                classes.append("Floor")
                findings.append({"type": "floor", "text": f"{kpi} would reach {after[kpi]}, above the operator floor "
                                                          f"of {f['max']} ({f['policy_id']})"})

        # 2. every intent's guards, against the predicted outcome
        guard_rows = []
        for owner, intent in intents.items():
            for g in (intent or {}).get("guards", []):
                kpi = g["kpi"]
                b, a = before.get(kpi), after.get(kpi)
                if a is None:
                    continue
                row = {"owner": owner, "kpi": kpi, "before": b, "after": a, "why": g.get("why", ""), "ok": True}
                worsens = a > b
                if "max" in g:
                    row.update(rule=f"<= {g['max']}", limit=g["max"])
                    row["ok"] = not (a > g["max"] and worsens)
                if "max_increase" in g and row["ok"]:
                    base = (baselines.get(owner) or {}).get(kpi, {}).get("value", b)
                    row.update(rule=f"at most +{g['max_increase']} over {base:g} (left by {owner})",
                               baseline=base, limit=base + g["max_increase"])
                    row["ok"] = not (a - base > g["max_increase"] and worsens)
                    row["cumulative"] = round(a - base, 2)
                    row["this_step"] = round(a - b, 2)
                guard_rows.append(row)
                if row["ok"]:
                    continue
                if owner == rapp_id:
                    classes.append("High impact")
                    findings.append({"type": "self", "text": f"breaks its own guard: {kpi} {b} -> {a} ({row['rule']})"})
                elif "cumulative" in row and row["before"] - row.get("baseline", row["before"]) > 0:
                    classes.append("Drift")
                    findings.append({"type": "drift", "owner": owner,
                                     "text": f"{kpi} has crept from {row['baseline']:g} to {b} since {owner} acted, and "
                                             f"this step takes it to {a}: +{row['cumulative']:g} against a budget of "
                                             f"+{row['limit'] - row['baseline']:g} ({g.get('why', '')})"})
                else:
                    classes.append("Conflicting")
                    findings.append({"type": "conflict", "owner": owner,
                                     "text": f"breaks {owner}'s intent: {kpi} {b} -> {a} ({row['rule']}; {g.get('why', '')})"})

        # 3. other agents' changes in this blast area this hour
        reach = set().union(*(self.area.get(c, {c}) for c in cells)) if cells else set()
        others = [c for c in changes if c["rapp_id"] != rapp_id and c["cell_id"] in reach]
        if others:
            who = sorted({c["rapp_id"] for c in others})
            touched = sorted({c["cell_id"] for c in others})
            classes.append("Overlap")
            findings.append({"type": "overlap", "agents": who,
                             "text": f"{', '.join(who)} changed {', '.join(touched)} this hour; this request's blast "
                                     f"area overlaps it"})

        # 4. getting around a rule that denied it
        refused = {d["cell_id"] for d in denials if d["policy_id"] == LOAD_GUARD_POLICY}
        mine = [c for c in changes if c["rapp_id"] == rapp_id]
        for a in actions:
            cell = a.get("cell_id")
            if a.get("name") == "set_tilt" and cell in refused:
                if after["ues_per_cell"].get(cell, 0) < before["ues_per_cell"].get(cell, 0):
                    classes.append("Workaround")
                    findings.append({"type": "workaround",
                                     "text": f"{cell} was refused to this agent this hour as too busy to switch off "
                                             f"({LOAD_GUARD_POLICY}); this tilt drains it "
                                             f"({before['ues_per_cell'].get(cell, 0)} -> {after['ues_per_cell'].get(cell, 0)} users)"})
            if a.get("name") == "cell_off":
                drained = [c for c in mine if c["cell_id"] == cell and c["action"] == "set_tilt"
                           and (c["load_before"] or 0) > LOAD_GUARD]
                if drained or cell in refused:
                    classes.append("Workaround")
                    findings.append({"type": "workaround",
                                     "text": f"{cell} carried more than {LOAD_GUARD} users until this agent changed it "
                                             f"this hour; the low-load condition for switching it off was created by "
                                             f"the agent itself"})

        # 5. novelty, from the ledger
        novel = sorted({a["name"] for a in actions if history.get(a["name"], 0) == 0})
        if novel:
            classes.append("Novel")
            findings.append({"type": "novel", "text": f"first {', '.join(novel)} by this agent in the ledger"})
        else:
            seen = ", ".join(f"{a['name']} x{history.get(a['name'], 0)}" for a in actions)
            findings.append({"type": "history", "text": f"seen before: {seen}"})

        cls = next((c for c in ORDER if c in classes), "Routine")
        escalate = cls in ESCALATE
        why = self._why(cls, findings, before, after)
        inputs = {"intents": {k: (v or {}).get("goal") for k, v in intents.items()},
                  "cells": cells, "tick": tick, "other_changes_this_hour": len(others),
                  "denials_this_hour": len(denials), "predicted": {k: [before.get(k), after.get(k)] for k in
                                                                    ("max_cell_load", "excess_ues", "weak_coverage_pct",
                                                                     "energy_saving_pct")}}
        return Assessment(cls, escalate, deny_policy, why, findings, guard_rows, inputs)

    @staticmethod
    def _why(cls, findings, before, after) -> str:
        kind = {"Floor": "floor", "Workaround": "workaround", "Conflicting": "conflict", "Drift": "drift",
                "High impact": "self"}.get(cls)
        main = next((f["text"] for f in findings if f["type"] == kind), None)
        also = [f["text"] for f in findings if f["type"] in ("floor", "workaround", "conflict", "drift", "self")
                and f["text"] != main]
        tail = f" Also: {'; '.join(also)}." if also else ""
        if cls == "Floor":
            return f"Denied: {main}.{tail}"
        if cls in ESCALATE:
            return f"Needs a human: {main}.{tail}"
        extra = "; ".join(f["text"] for f in findings if f["type"] in ("overlap", "novel"))
        return (f"Allowed: every agent's guards hold (max cell load {before['max_cell_load']} -> "
                f"{after['max_cell_load']}, weak coverage {before['weak_coverage_pct']} -> {after['weak_coverage_pct']}%)"
                + (f"; {extra}" if extra else "") + ".")
