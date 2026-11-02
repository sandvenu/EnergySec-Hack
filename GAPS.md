# Gaps (team07)

What does not work, or works only partly, with the unit it limits. Platform blocks list the time, the service,
the exact error and who we told.

## Design gaps

| Gap | Unit it limits | Detail |
|---|---|---|
| Own twin, not the event's hosted twin | none of the four Track 3 units; comparability with Track 1 | The hosted twin is a fixed 4-cell power/tilt simulator with no prediction call. Our framework needs 15 cells, cell on/off, criticality, planned events and `what_if`. We run unmodified MAVERIC 1.0.0 sample data in our own account. |
| Gateway-level denials are not in our ledger | trace | AgentCore Policy denials stop the request before it reaches the SMO, so they are logged by AgentCore, not signed into our ledger. Correlate by the agent's session id. |
| `set_tilt` cell limits are checked by the SMO, not AgentCore Policy | deny | Cedar in AgentCore Policy cannot read cell ids inside the `changes` records. The SMO's own Cedar engine enforces the same policy IDs. |
| Framework state is SQLite on the twin host | pause, trace | Budgets, approvals and intent baselines are not yet in DynamoDB / Step Functions. The ledger itself is KMS-signed into S3 Object Lock. |
| PPO model only on the twin host | allow | On AgentCore Runtime the energy agent's `rl_recommend` reports "unavailable" and the agent plans from KPIs and `what_if` alone. |
| Raw PPO output is not deployable | allow | All 24 hourly PPO proposals change 14 of 15 cells and are denied by `GLOBAL-BLAST-BUDGET` (budget 3). The agent treats the policy as input and plans a change that fits its scope. |
| Twin directivity | deny (D3) | The trained twin predicts near-identical RSRP for the 3 sectors of a site, so one sector off is invisible; D3 switches off a whole site. |
| Weak-coverage threshold | allow | The twin predicts RSRP around -130 dBm, so we use -135 dBm (`TWIN_WEAK_DBM`) instead of the app's -95 dBm. Shown on the demo page. |
| Benchmark misses | deny | CF-1 and BO-2 ran in every mode; both are scenario artefacts explained in README "Measured so far". 5/6 system-level harms stopped, not 6/6. |
| Enforcement latency | production readiness | Each mutating request waits for a twin prediction: 1.6–2.4 s median on the laptop. Reads skip it. |

| AgentCore Policy analyzer findings ignored | deny | The service's Cedar analyzer flags two deliberate shapes: the critical-cell `forbid` ("Overly Restrictive") and, before we scoped it per client, the canary pass-through ("Overly Permissive"). All 14 policies use `ValidationMode: IGNORE_ALL_FINDINGS`; Cedar syntax and schema are still validated. |

## Platform blocks

None blocked us for long. Three deploy attempts failed on our own template, each fixed in a commit:

| Time (UTC) | Service | Exact error | Request id | Fix |
|---|---|---|---|---|
| 2026-11-01 22:37 | EC2 (SecurityGroupIngress) | `Invalid rule description ... from the following set: a-zA-Z0-9. _-:/()#,@[]+=&;{}!$*` (our description contained `>`) | 1c1667b7-71ee-4d69-a910-d046160bae54 | `8585389` |
| 2026-11-01 23:03 | AgentCore Policy | `Overly Permissive: Policy Engine will allow every request ... (smo-tools___bulk_reset_site)` | RequestToken 6f9598ce-33a2-1404-6591-6b9f8a489e38 | `1c71d5a` (one canary permit per agent client) |
| 2026-11-02 00:03 (approx.) | AgentCore Policy | `Overly Restrictive: Policy Engine will deny every request ... (smo-tools___cell_off)` on `global_no_off_critical` | RequestToken a9dd751f-f3c2-1bfc-f318-68ef3d4c10bd | `31e35f3` (ValidationMode) |

### Run 2026-11-02T12-33-track3-d1d6-02 did not finish

The first attempt to record the demo video, declared at 12:32 UTC. D1 passed (ledger seq 36–43). D2's live
load-balancing plan was held for a human at 12:45:28 (seq 50, `TIER-DEFAULT-HIGH`). Then the SSM port-forward
from the laptop to the twin host was closed by the network:
`write tcp 192.168.1.6:61435->3.146.14.246:443: wsasend: An existing connection was forcibly closed by the remote host`.
The page's approve click never reached the host, and the hold was rejected on timeout at 12:50:28 (seq 52,
`AP-TIMEOUT`, after the 300 s approval timeout). The framework behaved as designed: an unanswered hold is rejected,
not run. D3–D6 did not run. Records 36–52 stay in the ledger. The next run was declared after this one.

### Run 2026-11-02T12-56-track3-d1d6-03 did not finish

The second attempt, on a fresh tunnel. D1, D2 (approved, seq 69) and D3 (drift held and rejected, seq 72–74) ran
and passed. At about 12:59:30 the network closed the tunnel again, before D4's request reached the host, so D4–D6
did not run. Records 53–74 stay in the ledger. After two drops in 20 minutes, the next run was driven on the twin
host itself (`scripts.rehearse` through SSM Run Command), with the laptop only watching the page through a tunnel
that reconnects itself.

Account guardrails we designed around (not blocks): every IAM role under `/bb26/` with `bb26-Boundary` (CDK Aspect in
`infra/app.py`), and no `cdk bootstrap` (assets go to the team bucket with the CLI's own credentials).
