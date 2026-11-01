#!/usr/bin/env python3
"""CDK app for the Trusted rApp Framework on AWS. See infra/README.md (runbook) before deploying."""

import os

import aws_cdk as cdk

from trusted_rapp_infra.stack import TrustedRappStack

app = cdk.App()
region = app.node.try_get_context("region") or os.environ.get("CDK_DEFAULT_REGION", "us-east-1")
TrustedRappStack(app, "TrustedRapp", env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=region),
                 description="Trusted rApp Framework: agentic RAN security on AgentCore, Bedrock and MAVERIC")
app.synth()
