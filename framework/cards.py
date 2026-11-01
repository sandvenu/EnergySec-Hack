"""rApp cards (AD-1) and admission (AD-2): validate the card, then grant a scope by asking the
policy engine, per requested action and cell, what this rApp would be allowed to do.
The same computation draws the blast radius map (BR-1), so the map always matches the policies.
"""

import json
from pathlib import Path
from typing import Dict, List

import jsonschema

from framework.policy_engine import DEFAULT_DENY_ID, PolicyEngine, build_entities, principal_uid

VOCABULARY = ["read_kpi", "what_if", "set_tilt", "cell_off", "cell_on"]
READ_ACTIONS = {"read_kpi", "what_if"}  # what_if = a prediction on the twin; changes nothing

INTENT_SCHEMA = {
    "type": "object",
    "required": ["owner", "goal", "objective", "guards"],
    "properties": {
        "owner": {"type": "string", "minLength": 1},
        "goal": {"type": "string", "minLength": 3},
        "objective": {"type": "object", "required": ["kpi", "direction"],
                      "properties": {"direction": {"enum": ["maximize", "minimize"]}}},
        "guards": {"type": "array", "items": {
            "type": "object", "required": ["kpi"],
            "properties": {"kpi": {"type": "string"}, "max": {"type": "number"}, "max_increase": {"type": "number"},
                           "why": {"type": "string"}}}},
    },
}

CARD_SCHEMA = {
    "type": "object",
    "required": ["rapp_id", "vendor", "version", "purpose", "requested_actions", "requested_cells",
                 "max_cells_per_tick", "reads_untrusted_input", "autonomy_tier"],
    "properties": {
        "rapp_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{2,62}$"},
        "vendor": {"type": "string", "minLength": 1},
        "version": {"type": "string", "minLength": 1},
        "purpose": {"type": "string", "minLength": 3},
        "requested_actions": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "requested_cells": {"oneOf": [{"const": "all"}, {"type": "array", "items": {"type": "string"}}]},
        "max_cells_per_tick": {"type": "integer", "minimum": 0},
        "reads_untrusted_input": {"type": "boolean"},
        "autonomy_tier": {"enum": ["supervised", "autonomous"]},
        "intent": INTENT_SCHEMA,  # an agent's goal and the KPIs it must not harm (TM Forum-style intent)
        # the ML model the rApp acts on; on AWS, admission requires an Approved version in the model registry
        "model": {"type": "object", "required": ["package_group"],
                  "properties": {"registry": {"enum": ["sagemaker"]}, "package_group": {"type": "string", "minLength": 1}}},
        # the agent's LLM budget: tokens per run (enforced in the agent harness) and USD per day (at the gateway)
        "model_budget": {"type": "object", "properties": {"max_tokens_per_run": {"type": "integer", "minimum": 1000},
                                                          "max_usd_per_day": {"type": "number", "minimum": 0}}},
    },
}


class CardError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field


def validate_card(card: dict) -> None:
    if not isinstance(card, dict):
        raise CardError("<card>", "must be a JSON object")
    for f in CARD_SCHEMA["required"]:
        if f not in card:
            raise CardError(f, "missing required field")
    try:
        jsonschema.validate(card, CARD_SCHEMA)
    except jsonschema.ValidationError as e:
        raise CardError(".".join(str(p) for p in e.absolute_path) or "<card>", e.message)


def load_budgets(policy_dir: Path) -> Dict[str, int]:
    data = json.loads((policy_dir / "budgets.json").read_text(encoding="utf-8"))
    return {k: int(v) for k, v in data.items() if not k.startswith("_")}


def load_cell_tags(policy_dir: Path) -> Dict[str, str]:
    data = json.loads((policy_dir / "cell_tags.json").read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def granted_budget(card: dict, budgets: Dict[str, int]) -> int:
    return min(int(card["max_cells_per_tick"]), budgets.get(card["rapp_id"], budgets.get("default", 0)))


def reach(engine: PolicyEngine, rapp_id: str, budget: int, actions: List[str], cells: Dict[str, str]) -> Dict[str, dict]:
    """Per action: cells it may touch and, if none, the policies that forbid it.

    Evaluated as a single-cell request on a fresh tick, so the budget policy only bites when the
    budget is zero. This is the blast radius: what the policies allow, not what the card asked for.
    """
    rapp = {"rapp_id": rapp_id, "admitted": True, "max_cells_per_tick": budget}
    entities = build_entities(rapp, cells)
    ctx = {"batch_size": 1, "cells_changed_this_tick": 1, "input_flagged": False, "cell_load": 0}
    out = {}
    for action in actions:
        if action in READ_ACTIONS:
            d = engine.is_authorized(principal_uid(rapp_id), action, 'Network::"ran"', {**ctx, "cells_changed_this_tick": 0}, entities)
            out[action] = {"allowed": d.allowed, "cells": list(cells) if d.allowed else [],
                           "denied_by": [] if d.allowed else d.policy_ids or [DEFAULT_DENY_ID], "allowed_by": d.policy_ids if d.allowed else []}
            continue
        allowed, denied_by, allowed_by = [], {}, set()
        for cell_id in cells:
            d = engine.is_authorized(principal_uid(rapp_id), action, f'Cell::"{cell_id}"', ctx, entities)
            if d.allowed:
                allowed.append(cell_id)
                allowed_by.update(d.policy_ids)
            else:
                for p in d.policy_ids or [DEFAULT_DENY_ID]:
                    denied_by.setdefault(p, []).append(cell_id)
        out[action] = {"allowed": bool(allowed), "cells": allowed, "denied_by": sorted(denied_by),
                       "denied_cells": denied_by, "allowed_by": sorted(allowed_by)}
    return out


def admit(engine: PolicyEngine, card: dict, cells: Dict[str, str], budgets: Dict[str, int]) -> dict:
    """Returns {'admitted', 'scope', 'refused'}; raises CardError for an invalid card."""
    validate_card(card)
    budget = granted_budget(card, budgets)
    requested = list(dict.fromkeys(card["requested_actions"]))
    if card["requested_cells"] != "all":
        unknown = [c for c in card["requested_cells"] if c not in cells]
        if unknown:
            raise CardError("requested_cells", f"unknown cells {unknown}")
        cells = {c: cells[c] for c in card["requested_cells"]}
    refused = [{"action": a, "policy_id": "VOCAB-UNKNOWN-ACTION", "reason": "not in the O-RAN action vocabulary"}
               for a in requested if a not in VOCABULARY]
    radius = reach(engine, card["rapp_id"], budget, [a for a in requested if a in VOCABULARY], cells)
    granted = {}
    for action, r in radius.items():
        if r["allowed"]:
            granted[action] = {"cells": r["cells"],
                               "criticality": ["any"] if action in READ_ACTIONS else sorted({cells[c] for c in r["cells"]})}
        else:
            refused.append({"action": action, "policy_id": r["denied_by"][0], "policy_ids": r["denied_by"],
                            "reason": f"{action} is not permitted on any requested cell"})
    scope = {"actions": granted, "max_cells_per_tick": budget, "requested_max_cells_per_tick": card["max_cells_per_tick"],
             "intent": card.get("intent")}
    return {"admitted": bool(granted), "scope": scope, "refused": refused}
