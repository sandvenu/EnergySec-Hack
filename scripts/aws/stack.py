"""Read the TrustedRapp stack outputs (deployed with infra/), for the scripts in scripts/aws/."""

import functools
import os

import boto3

STACK = os.environ.get("TRUSTED_RAPP_STACK", "TrustedRapp")
REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))


@functools.lru_cache()
def outputs() -> dict:
    stack = boto3.client("cloudformation", region_name=REGION).describe_stacks(StackName=STACK)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def client(name: str):
    return boto3.client(name, region_name=REGION)
