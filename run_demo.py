"""Start the standalone Trusted rApp Framework: twin service (8701) + gateway and demo page (8700).

    .venv\\Scripts\\python run_demo.py            then open http://127.0.0.1:8700
"""

import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parent


def wait_for(url: str, proc: subprocess.Popen, timeout: float = 120) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            sys.exit(f"twin service exited with code {proc.returncode}; see runtime/twin.log")
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    sys.exit(f"timed out waiting for {url}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway-port", type=int, default=8700)
    ap.add_argument("--twin-port", type=int, default=8701)
    args = ap.parse_args()

    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    env = {**os.environ, "TWIN_KEY": secrets.token_urlsafe(32), "PYTHONWARNINGS": "ignore",
           "TWIN_URL": f"http://127.0.0.1:{args.twin_port}", "GATEWAY_URL": f"http://127.0.0.1:{args.gateway_port}"}
    log = (runtime / "twin.log").open("w", encoding="utf-8")
    twin = subprocess.Popen([sys.executable, "-m", "uvicorn", "twin.service:app", "--host", "127.0.0.1",
                             "--port", str(args.twin_port), "--log-level", "warning"],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        print(f"Starting twin service on :{args.twin_port} ...")
        wait_for(f"http://127.0.0.1:{args.twin_port}/health", twin)
        os.environ.update(env)
        print(f"Twin ready. Demo page: http://127.0.0.1:{args.gateway_port}")
        # BIND_HOST=0.0.0.0 on AWS so the gateway Lambda target can reach /v1 (the operator API stays local-only)
        uvicorn.run("framework.gateway:app", host=os.environ.get("BIND_HOST", "127.0.0.1"), port=args.gateway_port,
                    log_level="warning")
    finally:
        twin.terminate()
        twin.wait(10)


if __name__ == "__main__":
    main()
