"""Retrain the energy agent's PPO policy as a SageMaker training job (instead of ~5 h on the laptop CPU).

    python -m scripts.aws.train_rl_sagemaker --timesteps 50000 [--instance ml.c5.2xlarge] [--wait]
    then: python -m scripts.aws.register_models --from-training-job <job name>   (PendingManualApproval)
          python -m scripts.aws.register_models --approve                        (after review)

The job runs MAVERIC's own RL trainer (twin/train_twin.py --rl) on the trained digital twin, in the
AWS PyTorch training container. Code, MAVERIC and twin data are uploaded to the stack's artifacts bucket;
the trained model lands in s3://<artifacts>/training/<job>/output/model.tar.gz.
"""

import argparse
import io
import tarfile
import time
from pathlib import Path

from scripts.aws.stack import REGION, client, outputs

ROOT = Path(__file__).resolve().parents[2]
MAVERIC = ROOT.parent / "maveric-1.0.0" / "maveric-1.0.0"
IMAGE = "763104351884.dkr.ecr.{region}.amazonaws.com/pytorch-training:2.4.0-cpu-py311-ubuntu22.04-sagemaker"
ENTRY = '''"""SageMaker entry: install the twin's deps, then run MAVERIC's RL trainer; the model goes to /opt/ml/model."""
import os, shutil, subprocess, sys
src = os.path.dirname(os.path.abspath(__file__))
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "gpytorch==1.13", "stable-baselines3==2.3.2",
                "gymnasium==0.29.1", "shapely==2.0.6", "pyarrow==17.0.0", "tensorboard==2.18.0"], check=True)
out = "/opt/ml/model"
os.makedirs(out, exist_ok=True)
shutil.copy2(os.path.join(src, "twin_data", "models", "bdt_model_map.pickle"), out)
env = {**os.environ, "MAVERIC_ROOT": os.path.join(src, "maveric"), "TWIN_MODELS": out,
       "TWIN_DATA": os.path.join(src, "twin_data", "energy_saving_data")}
steps = os.environ.get("SM_HP_TIMESTEPS", "25000")
subprocess.run([sys.executable, "-m", "twin.train_twin", "--rl", "--timesteps", steps], cwd=src, env=env, check=True)
os.remove(os.path.join(out, "bdt_model_map.pickle"))  # the twin is registered on its own
'''


def _source_tar() -> bytes:
    buf = io.BytesIO()
    skip = {"__pycache__", "rl_training_logs"}
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        info = tarfile.TarInfo("sagemaker_rl_entry.py")
        data = ENTRY.encode()
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
        for rel in ("twin", "twin_data"):
            t.add(ROOT / rel, arcname=rel, filter=lambda ti: None if set(Path(ti.name).parts) & skip else ti)
        t.add(MAVERIC, arcname="maveric", filter=lambda ti: None if "__pycache__" in ti.name or ".git" in ti.name else ti)
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timesteps", type=int, default=25000)
    ap.add_argument("--instance", default="ml.c5.2xlarge")
    ap.add_argument("--max-hours", type=float, default=8)
    ap.add_argument("--wait", action="store_true")
    a = ap.parse_args()
    out = outputs()
    bucket, role = out["ArtifactsBucket"], out["SageMakerRoleArn"]
    job = f"trusted-rapp-ppo-{time.strftime('%Y%m%d-%H%M%S')}"
    key = f"training/{job}/source/sourcedir.tar.gz"
    client("s3").put_object(Bucket=bucket, Key=key, Body=_source_tar())
    client("sagemaker").create_training_job(
        TrainingJobName=job, RoleArn=role,
        AlgorithmSpecification={"TrainingImage": IMAGE.format(region=REGION), "TrainingInputMode": "File"},
        HyperParameters={"sagemaker_program": '"sagemaker_rl_entry.py"',
                         "sagemaker_submit_directory": f'"s3://{bucket}/{key}"', "timesteps": str(a.timesteps)},
        OutputDataConfig={"S3OutputPath": f"s3://{bucket}/training/"},
        ResourceConfig={"InstanceType": a.instance, "InstanceCount": 1, "VolumeSizeInGB": 30},
        StoppingCondition={"MaxRuntimeInSeconds": int(a.max_hours * 3600)},
        Tags=[{"Key": "project", "Value": "trusted-rapp"}])
    print(f"started {job} on {a.instance} ({a.timesteps} steps)")
    if a.wait:
        client("sagemaker").get_waiter("training_job_completed_or_stopped").wait(
            TrainingJobName=job, WaiterConfig={"Delay": 60, "MaxAttempts": int(a.max_hours * 60) + 10})
        status = client("sagemaker").describe_training_job(TrainingJobName=job)["TrainingJobStatus"]
        print(f"{job}: {status}")


if __name__ == "__main__":
    main()
