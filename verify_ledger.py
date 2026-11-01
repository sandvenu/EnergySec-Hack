"""Offline ledger verifier (LG-2). Needs only the ledger file and the public key.

    python verify_ledger.py                                  # runtime/ledger/chain.jsonl
    python verify_ledger.py path/to/chain.jsonl --pubkey path/to/ledger_public.pem
    python verify_ledger.py --evidence evidence_<session>.json   # a signed evidence package
An evidence package carries its signing key's public half; --pubkey checks it against the key you trust.
Exit code 0 = OK, 1 = FAIL.
"""

import argparse
import json
import sys
from pathlib import Path

from framework.ledger import read_records, verify_records

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ledger", nargs="?", default=str(HERE / "runtime" / "ledger" / "chain.jsonl"))
    ap.add_argument("--pubkey", default=str(HERE / "runtime" / "ledger" / "ledger_public.pem"))
    ap.add_argument("--evidence", help="verify a signed evidence package (JSON) instead of the chain")
    args = ap.parse_args()
    if args.evidence:
        from framework.evidence import verify_package

        pkg = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
        pub = Path(args.pubkey).read_bytes() if Path(args.pubkey).exists() else pkg["integrity"]["public_key_pem"].encode()
        if pub.strip() != pkg["integrity"]["public_key_pem"].encode().strip():
            print("FAIL: package was not signed with the trusted ledger key")
            return 1
        ok, msg = verify_package(pkg, pub)
        print(msg)
        if ok and Path(args.ledger).exists():  # the package's records must be the chain's records
            chain = {r["seq"]: r.get("hash") for r in read_records(Path(args.ledger))}
            bad = [r["seq"] for r in pkg["integrity"]["records"] if chain.get(r["seq"]) != r["hash"]]
            ok = not bad
            print(f"{'OK' if ok else 'FAIL'}: {len(pkg['integrity']['records'])} records "
                  f"{'match the chain' if ok else 'differ from the chain: ' + str(bad)}")
        return 0 if ok else 1
    ok, lines = verify_records(read_records(Path(args.ledger)), Path(args.pubkey).read_bytes())
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
