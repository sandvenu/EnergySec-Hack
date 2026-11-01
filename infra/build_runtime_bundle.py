"""Build the AgentCore Runtime code bundle (direct code deployment, no Docker needed).

    infra/.venv/Scripts/python.exe infra/build_runtime_bundle.py      -> infra/build/runtime_bundle.zip

AgentCore Runtime runs Linux ARM64, so dependencies are installed for aarch64-manylinux2014 / Python 3.12
with binary wheels only (the service rejects non-ARM64 native files). The bundle holds the agents and
the light modules they import; the PPO model, torch and the twin stay on the twin host. One root-level
entry file per agent (runtime_*.py), because the runtime runs the entry file as a script.
"""

import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

INFRA = Path(__file__).resolve().parent
PROJECT = INFRA.parent
BUILD = INFRA / "build"
STAGE = BUILD / "runtime_stage"
OUT = BUILD / "runtime_bundle.zip"
DEPS = ["strands-agents[anthropic]==1.62.0", "anthropic==0.125.0", "bedrock-agentcore==1.24.2", "mcp==2.1.1",
        "httpx==0.28.1", "python-dotenv==1.0.1", "boto3"]  # anthropic: modelProvider=anthropic
CODE = ["agents", "rapps/__init__.py", "rapps/client.py", "rapps/energy_saving.py", "rapps/load_balancing.py",
        "twin/__init__.py", "twin/paths.py", "twin/vocabulary.py", "policies/rapp_cards"]
ENTRIES = {"runtime_energy.py": "agents.energy_saving.runtime_app", "runtime_load_balancing.py": "agents.load_balancing.runtime_app",
           "runtime_vendor.py": "agents.vendor.runtime_app", "runtime_orchestrator.py": "agents.orchestrator.runtime_app"}


def main():
    shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True)
    env = {**os.environ, "UV_NATIVE_TLS": "1"}
    subprocess.run(["uv", "pip", "install", "--python-platform", "aarch64-manylinux2014", "--python-version", "3.12",
                    "--only-binary=:all:", "--target", str(STAGE), *DEPS], check=True, env=env)
    for rel in CODE:
        src = PROJECT / rel
        if src.is_dir():
            shutil.copytree(src, STAGE / rel, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"), dirs_exist_ok=True)
        elif src.exists():
            (STAGE / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, STAGE / rel)
    for name, module in ENTRIES.items():
        (STAGE / name).write_text(f'"""AgentCore Runtime entry: {module}"""\nfrom {module} import app\n\n'
                                  f'if __name__ == "__main__":\n    app.run()\n', encoding="utf-8")
    OUT.unlink(missing_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(STAGE.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                info = zipfile.ZipInfo(p.relative_to(STAGE).as_posix())
                info.external_attr = (stat.S_IFREG | 0o755) << 16  # POSIX permissions (built on Windows)
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, p.read_bytes())
    size = OUT.stat().st_size / 1e6
    print(f"{OUT} {size:.1f} MB (limit 250 MB compressed)")
    if size > 250:
        sys.exit("bundle too large")


if __name__ == "__main__":
    main()
