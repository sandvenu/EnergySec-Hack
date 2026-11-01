#!/usr/bin/env python3
"""CDK app for the Trusted rApp Framework on AWS. See infra/README.md (runbook) before deploying.

Team-account guardrails (FYUZ 2026 / TIP organization), all set in cdk.json:
  rolePath, permissionsBoundary  every IAM role (ours and CDK's own custom-resource roles) gets the path and the
                                 boundary, and every instance profile the path; the account refuses roles without them
  assetsBucket                   file assets go to this existing bucket with the CLI's own credentials, so no
                                 `cdk bootstrap` (whose roles sit outside the path) is needed
"""

import os

import aws_cdk as cdk
import jsii

from trusted_rapp_infra.stack import TrustedRappStack


@jsii.implements(cdk.IAspect)
class RoleGuardrails:
    def __init__(self, path: str, boundary: str):
        self.path = path
        self.boundary_arn = cdk.Fn.sub(f"arn:${{AWS::Partition}}:iam::${{AWS::AccountId}}:policy/{boundary}")

    def visit(self, node):
        if not isinstance(node, cdk.CfnResource):
            return
        if node.cfn_resource_type in ("AWS::IAM::Role", "AWS::IAM::InstanceProfile"):
            node.add_property_override("Path", self.path)
        if node.cfn_resource_type == "AWS::IAM::Role":
            node.add_property_override("PermissionsBoundary", self.boundary_arn)


app = cdk.App()
ctx = app.node.try_get_context
region = ctx("region") or os.environ.get("CDK_DEFAULT_REGION", "us-east-2")
synthesizer = (cdk.CliCredentialsStackSynthesizer(file_assets_bucket_name=ctx("assetsBucket"),
                                                  bucket_prefix="cdk-assets/trusted-rapp/")
               if ctx("assetsBucket") else None)
stack = TrustedRappStack(app, "TrustedRapp", synthesizer=synthesizer,
                         env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=region),
                         description="Trusted rApp Framework: agentic RAN security on AgentCore, Bedrock and MAVERIC")
if ctx("rolePath"):
    cdk.Aspects.of(stack).add(RoleGuardrails(ctx("rolePath"), ctx("permissionsBoundary")))
app.synth()
