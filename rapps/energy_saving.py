"""Energy Saving rApp: MAVERIC's trained PPO agent (apps/energy_savings), wrapped as a proposer.

The agent maps tick -> per-cell index (0..10 tilt, 11 OFF). The wrapper translates that into
action-vocabulary requests against the live state it reads through the gateway, and submits them.
It never talks to the twin directly.
"""

import json
import logging
from pathlib import Path
from typing import List, Optional

from rapps.client import GatewayClient
from twin.paths import RL_LOG_DIR, RL_MODEL_PATH
from twin.vocabulary import rl_actions_to_vocabulary

logger = logging.getLogger("rapp.energy_saving")

CARD_PATH = Path(__file__).resolve().parents[1] / "policies" / "rapp_cards" / "energy-saving-rl.json"


def latest_agent_path() -> Optional[Path]:
    if RL_MODEL_PATH.exists():
        return RL_MODEL_PATH
    ckpts = sorted(RL_LOG_DIR.glob("energy_rl_model_*_steps.zip"), key=lambda p: int(p.stem.split("_")[-2]))
    return ckpts[-1] if ckpts else None


class EnergySavingRapp:
    rapp_id = "energy-saving-rl"

    def __init__(self, gateway: GatewayClient, agent_path: Optional[Path] = None):
        self.gw = gateway
        self._model = None
        self.agent_path: Optional[Path] = agent_path

    def register(self) -> dict:
        return self.gw.admit(json.loads(CARD_PATH.read_text(encoding="utf-8")))

    def _agent(self):
        if self._model is None:
            from stable_baselines3 import PPO

            self.agent_path = self.agent_path or latest_agent_path()
            if self.agent_path is None:
                raise FileNotFoundError("No trained agent yet: run python -m twin.train_twin --rl")
            self._model = PPO.load(str(self.agent_path), device="cpu")
            logger.info("Loaded RL agent %s", self.agent_path.name)
        return self._model

    def agent_proposal(self) -> dict:
        """What the RL agent wants for the current tick, as vocabulary actions (not yet submitted)."""
        read = self.gw.read_kpi()
        if read.get("decision") != "allow":
            return {"error": read.get("reason"), "policy_id": read.get("policy_id"), "actions": []}
        state = read["result"]["state"]
        indices, _ = self._agent().predict(state["tick"], deterministic=True)
        actions = rl_actions_to_vocabulary(indices, list(state["cells"]), state["cells"])
        return {"tick": state["tick"], "raw_indices": [int(i) for i in indices], "actions": actions,
                "agent": self.agent_path.name if self.agent_path else None}

    def propose(self, actions: List[dict], justification: str = "energy saving proposal") -> dict:
        return self.gw.submit(actions, justification)

    def run_agent(self, only: Optional[str] = None, limit: Optional[int] = None) -> dict:
        """Submit the agent's proposal. `only` keeps one action type (e.g. 'cell_off'); `limit` caps the batch."""
        prop = self.agent_proposal()
        actions = [a for a in prop.get("actions", []) if only is None or a["name"] == only]
        if limit is not None:
            actions = actions[:limit]
        if not actions:
            return {"proposal": prop, "submitted": [], "outcome": None}
        return {"proposal": prop, "submitted": actions,
                "outcome": self.propose(actions, f"RL agent proposal for tick {prop['tick']}")}
