"""AgentCore Runtime entrypoint for the Energy agent.

Local:  GATEWAY_TOKEN=<token from admission> python -m agents.energy_saving.runtime_app   (serves :8080)
        POST /invocations {"intent": "..."}   GET /ping
AWS:    deployed to AgentCore Runtime by the CDK stack (infra/). The agent authenticates to AgentCore
        Gateway with its own identity (AgentCore Identity, agents/identity.py); there is no admission
        token in the runtime, and the PPO model stays on the twin host (rl_recommend reports unavailable).
"""

import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agents.common import aws_mode
from agents.energy_saving.agent import run_energy_agent
from rapps.client import GatewayClient
from rapps.energy_saving import EnergySavingRapp

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict) -> dict:
    token = payload.get("gateway_token") or os.environ.get("GATEWAY_TOKEN")
    rapp = None
    if not aws_mode():
        rapp = EnergySavingRapp(GatewayClient(os.environ.get("GATEWAY_URL", "http://127.0.0.1:8700"), token))
    return run_energy_agent(rapp, payload.get("intent", "Reduce energy for the current hour."),
                            session=payload.get("session"))


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "8080")))
