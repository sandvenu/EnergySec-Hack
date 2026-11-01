"""AgentCore Gateway Lambda target for the SMO tools: forwards each tool call to the enforcement point.

The gateway invokes this function with the tool arguments as the event and the tool name in the client
context (bedrockAgentCoreToolName = "smo-tools___<tool>"). The REQUEST interceptor has put the caller's
rApp ID into `_caller`; without it the call is refused here (fail closed). The enforcement point on the
twin host makes the decision and writes the ledger record; this function only carries the call across
the VPC, authenticated with the stack's shared secret.
"""

import json
import logging
import os
import urllib.error
import urllib.request

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

PEP_URL = os.environ["PEP_URL"].rstrip("/")            # http://<twin host private IP>:8700
SECRET_ARN = os.environ["GATEWAY_SECRET_ARN"]
SEP = "___"
_secret = None


def _shared_secret() -> str:
    global _secret
    if _secret is None:
        _secret = boto3.client("secretsmanager").get_secret_value(SecretId=SECRET_ARN)["SecretString"]
    return _secret


def handler(event, context):
    custom = {}
    if context is not None and getattr(context, "client_context", None) is not None:
        custom = getattr(context.client_context, "custom", None) or {}
    tool = (custom.get("bedrockAgentCoreToolName") or event.get("_tool") or "").split(SEP)[-1]
    args = dict(event or {})
    caller = args.get("_caller")
    if not caller:
        log.warning("refused %s: no _caller (interceptor did not run)", tool)
        return {"error": "refused: caller identity missing (TARGET-NO-CALLER)"}
    body = {"rapp_id": caller, "client_id": args.get("_client_id", ""), "session": args.get("_session") or None,
            "arguments": {k: v for k, v in args.items() if not k.startswith("_")}}
    req = urllib.request.Request(f"{PEP_URL}/v1/gateway/tools/{tool}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Gateway-Secret": _shared_secret()})
    try:
        with urllib.request.urlopen(req, timeout=110) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        out = {"error": f"SMO returned {e.code}", "detail": e.read().decode(errors="replace")[:500]}
    except Exception as e:  # SMO unreachable: nothing was executed
        out = {"error": f"SMO unreachable: {e}", "decision": "deny", "policy_id": "FAILCLOSED-SMO"}
    log.info(json.dumps({"tool": tool, "rapp_id": caller, "decision": out.get("decision"), "policy_id": out.get("policy_id")}))
    return out
