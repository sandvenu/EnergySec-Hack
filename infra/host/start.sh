#!/bin/bash
# Service entry: fetch the gateway shared secret (never written to disk), then start twin + gateway.
set -euo pipefail
GATEWAY_SHARED_SECRET="$(aws secretsmanager get-secret-value --secret-id "$GATEWAY_SECRET_ARN" --query SecretString --output text)"
export GATEWAY_SHARED_SECRET
exec /opt/trusted-rapp/.venv/bin/python /opt/trusted-rapp/run_demo.py
