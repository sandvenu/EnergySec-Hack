"""AgentCore Runtime entrypoint for the third-party vendor agent.

Local:  GATEWAY_TOKEN=<token from admission> PORT=8081 python -m agents.vendor.runtime_app
        POST /invocations {"task": "..."}   GET /ping
AWS:    deployed to its own AgentCore Runtime with its own AgentCore Identity, isolated from ours.
"""

import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agents.vendor.agent import DEFAULT_TASK, run_vendor_agent

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict) -> dict:
    token = payload.get("gateway_token") or os.environ.get("GATEWAY_TOKEN")  # None on AWS: own identity
    return run_vendor_agent(token, payload.get("task", DEFAULT_TASK), session=payload.get("session"))


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "8081")))
