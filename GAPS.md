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

## Platform blocks

| Time (UTC) | Service | Exact error | Request id | Who we told |
|---|---|---|---|---|
| | | | | |
