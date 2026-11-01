"""Load-balancing agent's gateway identity: registers its card (with its intent) and holds its token."""

import json
from pathlib import Path

from rapps.client import GatewayClient

CARD_PATH = Path(__file__).resolve().parents[1] / "policies" / "rapp_cards" / "load-balancing-agent.json"


class LoadBalancingRapp:
    rapp_id = "load-balancing-agent"

    def __init__(self, gateway: GatewayClient):
        self.gw = gateway

    def register(self) -> dict:
        return self.gw.admit(json.loads(CARD_PATH.read_text(encoding="utf-8")))

    def propose(self, actions, justification: str = "load balancing") -> dict:
        return self.gw.submit(actions, justification)
