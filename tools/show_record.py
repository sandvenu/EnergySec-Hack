"""Read-only live test for the judges: show one signed ledger record of a run, from S3 only.

    python tools/show_record.py --run-id <run_id> --item <n>      # n-th decision of the run (1-based)
    python tools/show_record.py --run-id <run_id>                 # list the run's decisions

A run id names one demo run. evidence/runs/<run_id>.json in the team bucket maps it to the agent sessions it
used (written by tools/declare_run.py after the run). The record comes from the ledger bucket (S3 Object Lock),
and the tool re-checks its hash and its link to the previous record. With --pubkey (the ledger key's public half,
evidence/ledger_public.pem) it also checks the KMS signature. Only S3 reads: works after the account freeze.
"""

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from framework.ledger import record_hash  # noqa: E402

TEAM_BUCKET = os.environ.get("TEAM_BUCKET", "bb26-p-team07-data-041915167631-use2")
LEDGER_PREFIX = "ledger/records/"


def ledger_bucket(s3) -> str:
    if os.environ.get("LEDGER_BUCKET"):
        return os.environ["LEDGER_BUCKET"]
    run_cfg = json.loads(s3.get_object(Bucket=TEAM_BUCKET, Key="evidence/stack_outputs.json")["Body"].read())
    return run_cfg["LedgerBucket"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--item", type=int)
    ap.add_argument("--pubkey", help="ledger public key PEM, to check the signature too")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-2"))
    args = ap.parse_args()
    s3 = boto3.client("s3", region_name=args.region)

    run = json.loads(s3.get_object(Bucket=TEAM_BUCKET, Key=f"evidence/runs/{args.run_id}.json")["Body"].read())
    sessions = set(run["sessions"])
    bucket = ledger_bucket(s3)
    keys = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=LEDGER_PREFIX)
            for o in page.get("Contents", [])]
    records = [json.loads(s3.get_object(Bucket=bucket, Key=k)["Body"].read()) for k in sorted(keys)]
    by_seq = {r["seq"]: r for r in records}
    mine = [r for r in records if r.get("session") in sessions]
    print(f"run {args.run_id}: sessions {sorted(sessions)}, {len(mine)} ledger records in s3://{bucket}/{LEDGER_PREFIX}")
    if args.item is None:
        for i, r in enumerate(mine, 1):
            print(f"  {i:3d}  seq={r['seq']:<5} {r['time']}  {r.get('rapp_id')}  {r.get('decision')}  "
                  f"{r.get('policy_id') or ''}  {r.get('assessment', {}).get('class', '') if isinstance(r.get('assessment'), dict) else ''}")
        return 0
    if not 1 <= args.item <= len(mine):
        print(f"FAIL: item {args.item} not in 1..{len(mine)}")
        return 1
    rec = mine[args.item - 1]
    print(json.dumps(rec, indent=2, sort_keys=True, ensure_ascii=False))
    ok = record_hash({k: v for k, v in rec.items() if k not in ("hash", "signature")}) == rec["hash"]
    prev = by_seq.get(rec["seq"] - 1)
    linked = prev is None and rec["seq"] == 1 or prev is not None and prev["hash"] == rec["prev_hash"]
    print(f"{'OK' if ok else 'FAIL'}: record hash   {'OK' if linked else 'FAIL'}: chained to seq {rec['seq'] - 1}")
    if args.pubkey:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, utils

        key = serialization.load_pem_public_key(Path(args.pubkey).read_bytes())
        try:
            key.verify(base64.b64decode(rec["signature"]), bytes.fromhex(rec["hash"]),
                       ec.ECDSA(utils.Prehashed(hashes.SHA256())))
            print("OK: KMS signature")
        except Exception:
            ok = False
            print("FAIL: KMS signature")
    return 0 if ok and linked else 1


if __name__ == "__main__":
    sys.exit(main())
