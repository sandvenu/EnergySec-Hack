"""Run D1..D5 once in agent mode, print what each agent did and the framework's verdicts, save the transcripts.

    python -m scripts.agent_demo_run [--mode agent|scripted] [--no-approve]
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def calls(run):
    out = []
    for c in (run or {}).get("tool_calls", []):
        r = c.get("result") if isinstance(c.get("result"), dict) else {}
        tag = f" -> {r['decision']} {r.get('policy_id') or ''}".rstrip() if "decision" in r else ""
        if c.get("status") == "error":
            tag = " -> cancelled/error"
        name = c.get("tool") or c.get("name")
        out.append(f"{name}({json.dumps(c.get('input') or c.get('args') or {}, separators=(',', ':'))[:90]}){tag}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="agent")
    ap.add_argument("--no-approve", action="store_true")
    args = ap.parse_args()
    ops = httpx.Client(base_url="http://127.0.0.1:8700", timeout=900)
    results, t_all = {}, time.time()
    for step in ("D1", "D2", "D3", "D4", "D5"):
        body = {"mode": args.mode} if step in ("D2", "D3", "D4") else {}
        if step == "D3" and not args.no_approve:
            body["approve"] = True
        t0 = time.time()
        r = ops.post(f"/ops/demo/{step}", json=body)
        r.raise_for_status()
        res = r.json()
        results[step] = res
        print(f"\n=== {step} ({time.time() - t0:.1f}s) mode={res.get('mode', '-')} {res.get('mode_note') or ''}")
        if step == "D1":
            print("  ES admitted:", res["energy_saving"]["admitted"], "| 3P refused:",
                  [(x["action"], x["policy_id"]) for x in res["third_party"]["refused"]])
        elif step == "D2":
            run = res.get("agent_run")
            for line in calls(run):
                print("  ES agent:", line)
            if run:
                print("  ES agent said:", run["summary"][:600].replace("\n", " "))
                if run["self_check_blocked"]:
                    print("  self-check blocked:", run["self_check_blocked"])
            for k in ("es_low_cell_off", "es_critical_cell_off", "third_party_cell_off"):
                o = res[k]
                print(f"  {k}: {o.get('decision')} {o.get('policy_id')}")
        elif step == "D3":
            run = res.get("agent_run")
            for line in calls(run):
                print("  ES agent:", line)
            if run:
                print("  ES agent said:", run["summary"][:600].replace("\n", " "))
            o = res["outcome"]
            sh = o.get("shadow")
            print("  outcome:", o.get("decision"), o.get("policy_id"), "| proposal:", res.get("proposal"))
            if sh:
                print(f"  shadow: weak {sh['before']['weak_coverage_pct']} -> {sh['after']['weak_coverage_pct']}, "
                      f"energy {sh['before']['energy_saving_pct']} -> {sh['after']['energy_saving_pct']} ({sh['seconds']}s)")
            if res.get("approval"):
                print("  approval:", res["approval"].get("decision"))
        elif step == "D4":
            for label in ("defenses_off", "defenses_on"):
                m = res[label]
                run = m["run"]
                print(f"  [{label}] tool calls:", " | ".join(calls({"tool_calls": run.get("transcript") or run.get("tool_calls")})))
                print(f"  [{label}] flagged inputs:", [a["screening"]["score"] for a in run.get("alarms_seen", [])
                                                     if a.get("screening") and a["screening"].get("flagged")])
                print(f"  [{label}] cells changed on twin:", m["cells_changed"])
                if run.get("summary"):
                    print(f"  [{label}] agent said:", run["summary"][:500].replace("\n", " "))
        elif step == "D5":
            print("  ledger:", res["ledger"]["summary"], "| tampered:", res["tampered_copy"]["ok"],
                  "seq", res["tampered_copy"]["tampered_seq"], "| post-kill:", res["next_call"]["decision"],
                  res["next_call"]["policy_id"], f"{res['refused_ms_after_kill']} ms")
    print(f"\nTotal {time.time() - t_all:.1f}s")
    for step in ("D2", "D3"):
        u = (results[step].get("agent_run") or {}).get("usage")
        if u:
            print(f"  {step} ES agent usage: {u['model_calls']} calls, {u['input_tokens']:,} in / {u['output_tokens']:,} out, ~${u['est_cost_usd']}")
    for label in ("defenses_off", "defenses_on"):
        u = results["D4"][label]["run"].get("usage")
        if u:
            print(f"  D4 vendor agent [{label}] usage: {u['model_calls']} calls, {u['input_tokens']:,} in / {u['output_tokens']:,} out, ~${u['est_cost_usd']}")
    recs = httpx.get("http://127.0.0.1:8700/ops/decisions", params={"limit": 400}).json()["decisions"]
    with_why = [r for r in recs if r.get("justification")]
    print(f"  ledger records with a justification: {len(with_why)}; latest: "
          f"#{with_why[-1]['seq']} {with_why[-1]['decision']} '{with_why[-1]['justification'][:90]}'" if with_why else "  none")
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    path = out / f"agent_demo_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print("Saved", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
