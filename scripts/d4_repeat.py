"""Run D4 (agent mode) N times after one D1 and print, per run, what the vendor agent did off and on."""

import sys

import httpx

N = int(sys.argv[1]) if len(sys.argv) > 1 else 3
ops = httpx.Client(base_url="http://127.0.0.1:8700", timeout=900)
ops.post("/ops/demo/D1", json={}).raise_for_status()
for i in range(N):
    r = ops.post("/ops/demo/D4", json={"mode": "agent"}).json()
    for label in ("defenses_off", "defenses_on"):
        m = r[label]
        run = m["run"]
        calls = [f"{c['tool']}({','.join((c.get('input') or {}).get('cell_ids') or [x['cell_id'] for x in (c.get('input') or {}).get('changes', [])])})"
                 f"->{(c.get('result') or {}).get('decision', '-') if isinstance(c.get('result'), dict) else c.get('status')}"
                 for c in run.get("transcript", [])]
        print(f"run {i + 1} {label:13s} stop={run.get('stop_reason', '?'):9s} changed={sorted(m['cells_changed'])} calls={calls}")
