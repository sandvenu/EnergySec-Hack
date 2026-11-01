"""Acceptance rehearsal: D1..D6 end to end N times in a row through the operator API, timed.

    python -m scripts.rehearse [N] [--mode agent|scripted]
The rehearsal approves D2's rebalancing and rejects what D3 and D4 escalate, as the human would.
"""

import argparse
import time

import httpx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, default=3)
    ap.add_argument("--mode", default=None, help="force agent or scripted for D1-D4 (default: per-step default)")
    a = ap.parse_args()
    ops = httpx.Client(base_url="http://127.0.0.1:8700", timeout=900)
    ok_all = True
    for run in range(1, a.n + 1):
        t0, res = time.time(), {}
        for step, approve in (("D1", None), ("D2", True), ("D3", False), ("D4", False), ("D5", None), ("D6", None)):
            body = {} if approve is None else {"approve": approve}
            if a.mode and step not in ("D5", "D6"):
                body["mode"] = a.mode
            res[step] = ops.post(f"/ops/demo/{step}", json=body).json()
        passed = {s: bool(r.get("pass")) for s, r in res.items()}
        ok = all(passed.values())
        ok_all &= ok
        classes = {s: [(x.get("decision"), x.get("class")) for x in res[s].get("rows", [])] for s in ("D1", "D2", "D3", "D4")}
        print(f"run {run}: {'PASS' if ok else 'FAIL'} {passed} {time.time() - t0:.1f}s | modes "
              f"{ {s: res[s].get('mode') for s in ('D1', 'D2', 'D3', 'D4')} }")
        print(f"   decisions: {classes}")
        print(f"   D2 max load {res['D2']['before']['max_cell_load']} -> {res['D2']['after']['max_cell_load']} | "
              f"guards after D3 {res['D3']['guards']['ok']} after D4 {res['D4']['guards']['ok']} | "
              f"{res['D5']['ledger']['summary']} | pause->refusal {res['D5']['refused_ms_after_kill']} ms")
    raise SystemExit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
