"""The one place a model is chosen. Agents call build_model(); the provider is configuration.

    AGENT_MODEL_PROVIDER   anthropic (default) | bedrock
    AGENT_MODEL_ID         model ID for that provider (anthropic default: claude-opus-5-5;
                           bedrock has no default: set the Bedrock model or inference-profile ID)
    AGENT_MAX_TOKENS       default 16000
    AWS_REGION             for bedrock
    BEDROCK_GUARDRAIL_ID / BEDROCK_GUARDRAIL_VERSION   optional: Bedrock Guardrails on the agent's model calls
    AGENT_REFUSAL_FALLBACK  anthropic only, default 1: server-side refusal fallback (fallbacks="default")

Per-agent overrides use the agent's prefix, e.g. VENDOR_AGENT_MODEL_ID. Credentials come from the
environment (ANTHROPIC_API_KEY for anthropic, the AWS credential chain for bedrock); a git-ignored
.env file in the project root is loaded if present. On AWS with modelProvider=anthropic the key is read
from Secrets Manager instead (ANTHROPIC_API_KEY_SECRET_ARN, set by the CDK stack). Other Strands providers (OpenAI, Ollama,
LiteLLM, ...) can be added as one more branch below; the agent code does not change.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# Verify TLS against the operating system's trust store (corporate networks that inspect TLS install
# their root CA there, not in Python's certifi bundle). Verification stays on. Opt out: USE_OS_TRUSTSTORE=0.
if os.environ.get("USE_OS_TRUSTSTORE", "1") != "0":
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
# Models that accept the server-side refusal fallback ("default" form) on the Claude API.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


def _anthropic_key_from_secrets_manager() -> None:
    """AWS build with modelProvider=anthropic: the key is in Secrets Manager (ANTHROPIC_API_KEY_SECRET_ARN),
    read once with the host's or runtime's IAM role and kept in this process only. Locally, .env is used."""
    arn = os.environ.get("ANTHROPIC_API_KEY_SECRET_ARN")
    if not arn or os.environ.get("ANTHROPIC_API_KEY"):
        return
    import boto3

    value = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"].strip()
    if value.startswith("{"):  # also accept {"ANTHROPIC_API_KEY": "..."} or {"api_key": "..."}
        import json

        body = json.loads(value)
        value = body.get("ANTHROPIC_API_KEY") or body.get("api_key") or ""
    if value.startswith("sk-ant-"):  # the stack's random placeholder is not a key: leave it unset
        os.environ["ANTHROPIC_API_KEY"] = value


def _setting(name: str, prefix: str, default=None):
    return os.environ.get(f"{prefix}_{name}") if prefix and os.environ.get(f"{prefix}_{name}") else os.environ.get(name, default)


def describe(prefix: str = "") -> dict:
    provider = _setting("AGENT_MODEL_PROVIDER", prefix, "anthropic")
    model_id = _setting("AGENT_MODEL_ID", prefix, DEFAULT_ANTHROPIC_MODEL if provider == "anthropic" else None)
    return {"provider": provider, "model_id": model_id}


def available(prefix: str = "") -> tuple:
    """(ok, reason): whether build_model can succeed with the current configuration."""
    d = describe(prefix)
    if d["provider"] == "anthropic":
        try:
            _anthropic_key_from_secrets_manager()
        except Exception as e:
            return False, f"Anthropic key not readable from Secrets Manager: {e}"
    if d["provider"] == "anthropic" and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        where = ("set it with scripts/aws/set_anthropic_key.ps1" if os.environ.get("ANTHROPIC_API_KEY_SECRET_ARN")
                 else "put ANTHROPIC_API_KEY in trusted-rapp/.env")
        return False, f"no Anthropic credentials: {where}"
    if d["provider"] == "bedrock" and not d["model_id"]:
        return False, "AGENT_MODEL_ID is required for bedrock"
    if d["provider"] not in ("anthropic", "bedrock"):
        return False, f"unknown AGENT_MODEL_PROVIDER {d['provider']!r}"
    return True, f"{d['provider']}:{d['model_id']}"


def build_model(prefix: str = ""):
    d = describe(prefix)
    max_tokens = int(_setting("AGENT_MAX_TOKENS", prefix, "16000"))
    if d["provider"] == "anthropic":
        from strands.models.anthropic import AnthropicModel

        _anthropic_key_from_secrets_manager()
        params = {}
        if d["model_id"] in FALLBACK_MODELS and _setting("AGENT_REFUSAL_FALLBACK", prefix, "1") != "0":
            # Server-side refusal fallback: a declined turn is re-run on a fallback model chosen by
            # refusal category, inside the same call. Strands passes these through to messages.stream().
            params = {"extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"},
                      "extra_body": {"fallbacks": "default"}}
        return AnthropicModel(model_id=d["model_id"], max_tokens=max_tokens, params=params)
    if d["provider"] == "bedrock":
        from strands.models.bedrock import BedrockModel

        if not d["model_id"]:
            raise ValueError("AGENT_MODEL_ID is required for bedrock")
        extra = {}
        guardrail = _setting("BEDROCK_GUARDRAIL_ID", prefix)
        if guardrail:  # Bedrock Guardrails on every model call this agent makes
            extra = {"guardrail_id": guardrail, "guardrail_version": _setting("BEDROCK_GUARDRAIL_VERSION", prefix, "DRAFT")}
        return BedrockModel(model_id=d["model_id"], max_tokens=max_tokens,
                            region_name=os.environ.get("AWS_REGION", "us-west-2"), **extra)
    raise ValueError(f"unknown AGENT_MODEL_PROVIDER {d['provider']!r}")
