"""The agent's own identity towards AgentCore Gateway: an OAuth access token (Cognito, client credentials).

Each agent is one Cognito app client; the gateway's JWT authorizer accepts only those clients, the
REQUEST interceptor maps the client ID to the agent's rApp, and AgentCore Policy names the client ID
as the principal. Two ways to get the token, tried in order:

  1. AgentCore Identity (on AgentCore Runtime): the runtime's workload identity token is exchanged for
     the agent's access token through its OAuth2 credential provider ({PREFIX}_IDENTITY_PROVIDER).
     The agent never holds the client secret.
  2. Direct client-credentials grant (agents hosted on the twin host for the demo page): the client
     secret is read from Secrets Manager ({PREFIX}_CLIENT_SECRET_ARN) and exchanged at COGNITO_TOKEN_URL.

    {PREFIX}_COGNITO_CLIENT_ID, COGNITO_TOKEN_URL, COGNITO_SCOPE   from the CDK stack outputs
"""

import asyncio
import base64
import json
import os
import threading
import time
from typing import Dict, Tuple

import httpx

_cache: Dict[str, Tuple[str, float]] = {}
_lock = threading.Lock()


def _env(prefix: str, name: str, default=None):
    return os.environ.get(f"{prefix}_{name}") or os.environ.get(name, default)


def access_token(prefix: str) -> str:
    with _lock:
        tok, exp = _cache.get(prefix, (None, 0))
        if tok and exp - time.time() > 120:
            return tok
        tok, ttl = _from_agentcore_identity(prefix) or _from_client_credentials(prefix)
        _cache[prefix] = (tok, time.time() + ttl)
        return tok


def _from_agentcore_identity(prefix: str):
    provider = _env(prefix, "IDENTITY_PROVIDER")
    if not provider:
        return None
    try:
        from bedrock_agentcore.runtime.context import BedrockAgentCoreContext
        from bedrock_agentcore.services.identity import IdentityClient
    except ImportError:
        return None
    workload_token = BedrockAgentCoreContext.get_workload_access_token()
    if not workload_token:  # not inside AgentCore Runtime
        return None
    client = IdentityClient(os.environ.get("AWS_REGION", "us-east-2"))
    token = _run(client.get_token(provider_name=provider, agent_identity_token=workload_token,
                                  scopes=[os.environ["COGNITO_SCOPE"]], auth_flow="M2M"))
    return token, _ttl(token)


def _run(coro):
    """asyncio.run, also when this thread already runs an event loop (the Runtime's)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    out = {}

    def target():
        try:
            out["v"] = asyncio.run(coro)
        except BaseException as e:  # re-raised in the caller's thread
            out["e"] = e

    t = threading.Thread(target=target)
    t.start()
    t.join()
    if "e" in out:
        raise out["e"]
    return out["v"]


def _from_client_credentials(prefix: str):
    import boto3

    client_id = _env(prefix, "COGNITO_CLIENT_ID")
    secret_arn = _env(prefix, "CLIENT_SECRET_ARN")
    if not (client_id and secret_arn):
        raise RuntimeError(f"no AgentCore identity configured for {prefix}: set {prefix}_IDENTITY_PROVIDER (Runtime) "
                           f"or {prefix}_COGNITO_CLIENT_ID and {prefix}_CLIENT_SECRET_ARN")
    raw = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]
    secret = json.loads(raw).get("client_secret", raw) if raw.startswith("{") else raw
    r = httpx.post(os.environ["COGNITO_TOKEN_URL"], timeout=15,
                   data={"grant_type": "client_credentials", "scope": os.environ["COGNITO_SCOPE"]},
                   auth=(client_id, secret))
    r.raise_for_status()
    body = r.json()
    return body["access_token"], float(body.get("expires_in", 3600))


def _ttl(jwt: str) -> float:
    try:
        payload = jwt.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return max(60.0, float(claims["exp"]) - time.time())
    except Exception:
        return 300.0
