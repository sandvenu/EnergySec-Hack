"""Write evidence/runs/<run_id>.json for a finished run from the S3 ledger, and refresh the ledger export.

    python tools/run_map.py --run-id <run_id> --from-seq <first seq of the run> [--to-seq <last>] [--upload]

The run map lists the run's sessions and decisions (seq, time, session, rApp, decision, policy), which is what
tools/show_record.py reads to find a run's records. --upload copies it to the team bucket under evidence/runs/.
evidence/ledger_chain.jsonl is rewritten with the whole chain, for the offline rebuild (verify_ledger.py).
"""

import argparse
import json
import os
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.show_record import LEDGER_PREFIX, TEAM_BUCKET, ledger_bucket  # noqa: E402

EVIDENCE = Path(__file__).resolve().parents[1] / "evidence"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--from-seq", type=int, required=True)
    ap.add_argument("--to-seq", type=int, help="last seq of the run (default: the end of the chain)")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-2"))
    args = ap.parse_args()
    s3 = boto3.client("s3", region_name=args.region)
    bucket = ledger_bucket(s3)
    keys = sorted(o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=LEDGER_PREFIX)
                  for o in page.get("Contents", []))
    chain = [json.loads(s3.get_object(Bucket=bucket, Key=k)["Body"].read()) for k in keys]
    chain.sort(key=lambda r: r["seq"])
    (EVIDENCE / "ledger_chain.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in chain), encoding="utf-8")
    mine = [r for r in chain if args.from_seq <= r["seq"] <= (args.to_seq or r["seq"])]
    sessions = list(dict.fromkeys(r["session"] for r in mine if r.get("session")))
    run = {"run_id": args.run_id, "team_code": "team07", "started": mine[0]["time"], "ended": mine[-1]["time"],
           "sessions": sessions, "ledger_bucket": bucket, "ledger_seq": [mine[0]["seq"], mine[-1]["seq"]],
           "records": len(mine),
           "decisions": [{k: r.get(k) for k in ("seq", "time", "session", "rapp_id", "decision", "policy_id")}
                         for r in mine]}
    out = EVIDENCE / "runs" / f"{args.run_id}.json"
    out.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    print(f"{out.name}: seq {run['ledger_seq']}, {len(mine)} records, {len(sessions)} sessions; chain {len(chain)} records")
    if args.upload:
        s3.upload_file(str(out), TEAM_BUCKET, f"evidence/runs/{args.run_id}.json")
        print(f"uploaded s3://{TEAM_BUCKET}/evidence/runs/{args.run_id}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
