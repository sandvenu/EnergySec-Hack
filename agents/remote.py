"""Invoke an agent hosted on AgentCore Runtime (InvokeAgentRuntime), for agent-to-agent delegation.

    {NAME}_RUNTIME_ARN   from the CDK stack outputs (ES_RUNTIME_ARN, LB_RUNTIME_ARN, VENDOR_RUNTIME_ARN)
"""

import json
import os
import uuid

import boto3


def runtime_arn(prefix: str):
    return os.environ.get(f"{prefix}_RUNTIME_ARN")


def invoke_runtime(prefix: str, payload: dict, session: str = "") -> dict:
    arn = runtime_arn(prefix)
    if not arn:
        raise RuntimeError(f"{prefix}_RUNTIME_ARN is not set")
    client = boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    session_id = (session or "orch") + "-" + uuid.uuid4().hex  # runtime session IDs are at least 33 characters
    resp = client.invoke_agent_runtime(agentRuntimeArn=arn, runtimeSessionId=session_id[:100], qualifier="DEFAULT",
                                       payload=json.dumps(payload).encode())
    body = resp["response"].read()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body.decode(errors="replace")}
