"""AgentCore Runtime entrypoint for the Load-balancing agent.

Local:  GATEWAY_TOKEN=<token from admission> PORT=8082 python -m agents.load_balancing.runtime_app
AWS:    deployed to AgentCore Runtime by the CDK stack; authenticates with its own AgentCore identity.
"""

import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agents.common import aws_mode
from agents.load_balancing.agent import run_lb_agent
from rapps.client import GatewayClient
from rapps.load_balancing import LoadBalancingRapp

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict) -> dict:
    token = payload.get("gateway_token") or os.environ.get("GATEWAY_TOKEN")
    rapp = None
    if not aws_mode():
        rapp = LoadBalancingRapp(GatewayClient(os.environ.get("GATEWAY_URL", "http://127.0.0.1:8700"), token))
    return run_lb_agent(rapp, payload.get("intent", "Check cell loads and rebalance so no cell is overloaded."),
                        session=payload.get("session"))


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "8082")))
