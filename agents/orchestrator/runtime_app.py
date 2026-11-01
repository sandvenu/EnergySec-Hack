"""AgentCore Runtime entrypoint for the Orchestrator agent.

AWS:    deployed to AgentCore Runtime by the CDK stack. Delegates to the domain agents on their own
        runtimes (LB_RUNTIME_ARN, ES_RUNTIME_ARN) with InvokeAgentRuntime: agent-to-agent, each agent
        under its own AgentCore identity.
Local:  python -m scripts.orchestrate runs the same agent with in-process delegates.
"""

import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agents.orchestrator.agent import run_orchestrator
from agents.remote import invoke_runtime

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict) -> dict:
    session = payload.get("session") or ""
    delegates = {
        "load_balancing": lambda task: invoke_runtime("LB", {"intent": task}, session),
        "energy": lambda task: invoke_runtime("ES", {"intent": task}, session),
    }
    return run_orchestrator(payload.get("intent", "Review the network for this hour."), delegates,
                            token=payload.get("gateway_token") or os.environ.get("GATEWAY_TOKEN"), session=session or None)


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "8083")))
