"""Build the twin host's code bundles: an explicit list of what the host needs, zipped once.

    infra/.venv/Scripts/python.exe infra/build_host_bundle.py     -> infra/build/app_bundle.zip, maveric_bundle.zip

The CDK stack uploads these zips as they are. Packaging the project folder directly made CDK walk
(and, with its own output folders inside, recursively copy) the whole tree on every synth; an explicit
include list is fast and cannot pick up secrets (.env), local ledger keys or build output.
"""

import stat
import sys
import zipfile
from pathlib import Path

INFRA = Path(__file__).resolve().parent
PROJECT = INFRA.parent
MAVERIC = PROJECT.parent / "maveric-1.0.0" / "maveric-1.0.0"
BUILD = INFRA / "build"
APP_INCLUDE = ["agents", "bench", "demo", "demo_page", "framework", "policies", "rapps", "scripts", "twin", "twin_data",
               "infra/host", "requirements.txt", "run_demo.py", "verify_ledger.py", "README.md"]
SKIP_PARTS = {"__pycache__", ".pytest_cache", "rl_training_logs", ".git", ".venv"}
SKIP_NAMES = {".env"}


def _files(root: Path, rel: str):
    src = root / rel
    if src.is_file():
        yield src
        return
    for p in sorted(src.rglob("*")):
        if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_PARTS) and p.name not in SKIP_NAMES \
                and not p.name.endswith((".pyc", ".log")):
            yield p


def _zip(out: Path, root: Path, includes) -> None:
    out.unlink(missing_ok=True)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in includes:
            for p in _files(root, rel):
                info = zipfile.ZipInfo(p.relative_to(root).as_posix())
                mode = 0o755 if p.suffix == ".sh" else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, p.read_bytes())
                n += 1
    print(f"{out.name}: {n} files, {out.stat().st_size / 1e6:.1f} MB")


def main():
    BUILD.mkdir(exist_ok=True)
    missing = [r for r in APP_INCLUDE if not (PROJECT / r).exists()]
    if missing:
        sys.exit(f"missing: {missing}")
    _zip(BUILD / "app_bundle.zip", PROJECT, APP_INCLUDE)
    with zipfile.ZipFile(BUILD / "app_bundle.zip") as z:  # belt and braces: no secrets or keys in the bundle
        leaked = [n for n in z.namelist() if n.endswith(".env") or "/keys/" in n or n.endswith(".pem")]
    if leaked:
        sys.exit(f"refusing: sensitive files in bundle: {leaked}")
    _zip(BUILD / "maveric_bundle.zip", MAVERIC, [p.name for p in sorted(MAVERIC.iterdir())])


if __name__ == "__main__":
    main()
