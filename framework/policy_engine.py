"""Policy decision point.

PolicyEngine is the interface the PEP uses. LocalCedarEngine evaluates the same .cedar files
with the Cedar engine in-process (cedarpy); the AWS port adds a Verified Permissions adapter
behind the same interface (IsAuthorized with the same entities and context).
"""

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import cedarpy

logger = logging.getLogger(__name__)

DEFAULT_DENY_ID = "DEFAULT-DENY"  # no permit matched
DEFAULT_HIGH_TIER_ID = "TIER-DEFAULT-HIGH"  # no low-tier policy matched


class PolicyUnavailable(RuntimeError):
    pass


@dataclass
class AuthzDecision:
    allowed: bool
    policy_ids: List[str]  # determining policies (forbids on deny, permits on allow)
    errors: List[str] = field(default_factory=list)

    @property
    def policy_id(self) -> str:
        return self.policy_ids[0] if self.policy_ids else DEFAULT_DENY_ID


def _uid(kind: str, ident: str) -> dict:
    return {"__entity": {"type": kind, "id": ident}}


def build_entities(rapp: dict, cells: Dict[str, str]) -> List[dict]:
    """rapp: {rapp_id, admitted, max_cells_per_tick, vendor}; cells: {cell_id: criticality}."""
    ents = [{
        "uid": _uid("Rapp", rapp["rapp_id"]),
        "attrs": {"admitted": bool(rapp.get("admitted", False)),
                  "max_cells_per_tick": int(rapp.get("max_cells_per_tick", 0)),
                  "vendor": str(rapp.get("vendor", ""))},
        "parents": [],
    }, {"uid": _uid("Network", "ran"), "attrs": {"criticality": "none"}, "parents": []}]
    for cell_id, crit in cells.items():
        ents.append({"uid": _uid("Cell", cell_id), "attrs": {"criticality": crit, "site": cell_id.split("_")[1]}, "parents": []})
    return ents


def resource_for(action: dict) -> str:
    cell = action.get("cell_id")
    return f'Cell::"{cell}"' if cell else 'Network::"ran"'


class PolicyEngine:
    def is_authorized(self, principal: str, action: str, resource: str, context: dict, entities: List[dict]) -> AuthzDecision:
        raise NotImplementedError

    def tier_low(self, principal: str, resource: str, context: dict, entities: List[dict]) -> AuthzDecision:
        raise NotImplementedError

    def list_policies(self) -> List[dict]:
        raise NotImplementedError


class LocalCedarEngine(PolicyEngine):
    """Evaluates policies/*.cedar with cedarpy; reloads a file as soon as it changes (AD-3)."""

    def __init__(self, scope_file: Path, tier_file: Path):
        self.files = {"scope": Path(scope_file), "tier": Path(tier_file)}
        self._sets: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self.fault = False  # NF-1 test hook: simulate the policy store being unreachable
        for name in self.files:
            self._load(name)

    def _load(self, name: str) -> None:
        path = self.files[name]
        text = path.read_text(encoding="utf-8")
        mtime = path.stat().st_mtime
        # cedarpy reports positional ids (policy0, policy1...); map them back to @id annotations.
        est = json.loads(cedarpy.policies_to_json_str(text))
        id_map = {pos: (body.get("annotations") or {}).get("id", pos) for pos, body in est.get("staticPolicies", {}).items()}
        self._sets[name] = {"text": text, "mtime": mtime, "ids": id_map, "est": est}
        logger.info("Loaded %d %s policies from %s", len(id_map), name, path.name)

    def _policy_set(self, name: str) -> dict:
        with self._lock:
            path = self.files[name]
            try:
                if path.stat().st_mtime != self._sets[name]["mtime"]:
                    self._load(name)
            except Exception as e:  # a broken edit keeps the last good set, loudly
                logger.error("Policy reload of %s failed, keeping previous set: %s", path.name, e)
            return self._sets[name]

    def _eval(self, name: str, request: dict, entities: List[dict]) -> AuthzDecision:
        if self.fault:
            raise PolicyUnavailable("policy store unreachable (fault injected)")
        ps = self._policy_set(name)
        try:
            r = cedarpy.is_authorized(request, ps["text"], entities)
        except Exception as e:
            raise PolicyUnavailable(f"policy evaluation failed: {e}") from e
        ids = [ps["ids"].get(p, p) for p in r.diagnostics.reasons]
        return AuthzDecision(r.decision == cedarpy.Decision.Allow, ids, [str(x) for x in r.diagnostics.errors])

    def is_authorized(self, principal, action, resource, context, entities):
        req = {"principal": principal, "action": f'Action::"{action}"', "resource": resource, "context": context}
        return self._eval("scope", req, entities)

    def tier_low(self, principal, resource, context, entities):
        req = {"principal": principal, "action": 'Action::"auto_approve"', "resource": resource, "context": context}
        return self._eval("tier", req, entities)

    def list_policies(self) -> List[dict]:
        out = []
        for name in self.files:
            ps = self._policy_set(name)
            for pos, body in ps["est"].get("staticPolicies", {}).items():
                out.append({"set": name, "id": ps["ids"][pos], "effect": body.get("effect")})
        return out

    def policy_text(self, name: str) -> str:
        return self._policy_set(name)["text"]

    def write_policy_text(self, name: str, text: str) -> None:
        cedarpy.policies_to_json_str(text)  # raises on a syntax error before anything is written
        self.files[name].write_text(text, encoding="utf-8")
        with self._lock:
            self._load(name)


def make_engine(policy_dir: Path) -> PolicyEngine:
    return LocalCedarEngine(policy_dir / "scope.cedar", policy_dir / "tier.cedar")


def principal_uid(rapp_id: str) -> str:
    return f'Rapp::"{rapp_id}"'
