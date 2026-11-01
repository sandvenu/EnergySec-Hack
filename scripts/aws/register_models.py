"""Register the rApp's models in SageMaker Model Registry, and approve or reject a version.

    python -m scripts.aws.register_models                    # register current PPO + twin models (PendingManualApproval)
    python -m scripts.aws.register_models --approve          # approve the newest version of each group
    python -m scripts.aws.register_models --reject           # reject it (the energy agent is then refused admission)
    python -m scripts.aws.register_models --from-training-job <job>   # register the PPO model a SageMaker job produced
    python -m scripts.aws.register_models --list

Admission on AWS (MODEL_REGISTRY_CHECK=1) refuses an rApp whose card names a model group with no
Approved version (ADMIT-MODEL-NOT-APPROVED): the model a network agent acts on is a promoted, traceable
artifact, with its hash in the registry, not whatever file is on the host.
"""

import argparse
import hashlib
import io
import tarfile
import time
from pathlib import Path

from botocore.exceptions import ClientError

from scripts.aws.stack import REGION, client, outputs
from twin.paths import BDT_MODEL_PATH, RL_MODEL_PATH

GROUPS = {"trusted-rapp-energy-saver-ppo": RL_MODEL_PATH, "trusted-rapp-twin-bdt": BDT_MODEL_PATH}
# Only used if the registry insists on an inference specification (the models are served by the SMO host, not SageMaker).
DLC = "763104351884.dkr.ecr.{region}.amazonaws.com/pytorch-inference:2.4.0-cpu-py311-ubuntu22.04-sagemaker"


def _tar(path: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        t.add(path, arcname=path.name)
    return buf.getvalue()


def register(group: str, model_url: str, sha256: str, source: str) -> str:
    sm = client("sagemaker")
    base = {"ModelPackageGroupName": group, "ModelApprovalStatus": "PendingManualApproval",
            "ModelPackageDescription": f"{source} sha256={sha256[:16]}",
            "CustomerMetadataProperties": {"sha256": sha256, "source": source, "registered_by": "scripts.aws.register_models"}}
    try:
        arn = sm.create_model_package(**base, SourceUri=model_url)["ModelPackageArn"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ValidationException":
            raise
        arn = sm.create_model_package(**base, InferenceSpecification={
            "Containers": [{"Image": DLC.format(region=REGION), "ModelDataUrl": model_url}],
            "SupportedContentTypes": ["application/json"], "SupportedResponseMIMETypes": ["application/json"]})["ModelPackageArn"]
    print(f"registered {group}: {arn} (PendingManualApproval)")
    return arn


def latest(group: str):
    res = client("sagemaker").list_model_packages(ModelPackageGroupName=group, SortBy="CreationTime",
                                                   SortOrder="Descending", MaxResults=1)["ModelPackageSummaryList"]
    return res[0] if res else None


def set_status(status: str) -> None:
    for group in GROUPS:
        pkg = latest(group)
        if not pkg:
            print(f"{group}: nothing registered")
            continue
        client("sagemaker").update_model_package(ModelPackageArn=pkg["ModelPackageArn"], ModelApprovalStatus=status,
                                                 ApprovalDescription=f"{status} by operator ({time.strftime('%Y-%m-%d %H:%M')})")
        print(f"{group} v{pkg.get('ModelPackageVersion')}: {status}")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--approve", action="store_true")
    g.add_argument("--reject", action="store_true")
    g.add_argument("--list", action="store_true")
    g.add_argument("--from-training-job")
    a = ap.parse_args()
    if a.approve or a.reject:
        return set_status("Approved" if a.approve else "Rejected")
    if a.list:
        for group in GROUPS:
            for p in client("sagemaker").list_model_packages(ModelPackageGroupName=group)["ModelPackageSummaryList"]:
                print(group, p.get("ModelPackageVersion"), p["ModelApprovalStatus"], p["CreationTime"])
        return
    if a.from_training_job:
        job = client("sagemaker").describe_training_job(TrainingJobName=a.from_training_job)
        url = job["ModelArtifacts"]["S3ModelArtifacts"]
        register("trusted-rapp-energy-saver-ppo", url, job.get("TrainingJobArn", "")[-64:], f"sagemaker:{a.from_training_job}")
        return
    bucket = outputs()["ArtifactsBucket"]
    s3 = client("s3")
    for group, path in GROUPS.items():
        if not path.exists():
            print(f"{group}: {path} not found, skipped")
            continue
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        key = f"models/{group}/{sha[:16]}/model.tar.gz"
        s3.put_object(Bucket=bucket, Key=key, Body=_tar(path))
        register(group, f"s3://{bucket}/{key}", sha, f"local:{path.name}")


if __name__ == "__main__":
    main()
