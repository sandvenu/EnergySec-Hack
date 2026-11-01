# NIST incident readiness practices, mapped to the framework

Source: NIST CAISSI, NSA SISC and CISA, *Key Practices for Incident Readiness and Response in Super
Intelligence Training and Evaluation*, PP-26-4396, October 2026 (TLP:CLEAR). It sets out ten voluntary
practices in five stages. The table maps each sub-practice to the place in this build that implements it
and to what shows it on the demo.

The paper's definitions are used as they are:
* **Control incident:** an agent takes significant uninstructed actions that compromise the controls that set its scope or human oversight.
* **Near-miss:** no incident, but a foreseeable risk of one.

In this build a near-miss is an attempt that a control caught.

| Stage | Practice | Implementation | Shown in |
|---|---|---|---|
| Prepare | 1.1 Least privilege | Cards, admission scope from Cedar, blast radius budget | D1, blast radius map |
| | 1.3 Clear task scope in instructions | Intent and granted scope in every agent's system prompt (`read_scope`) | D1–D4 transcripts |
| | 1.4 Restrict persistence and unmonitored channels | Agents act only through the gateway; persistence rule (3rd denial in a session is S2) | tests |
| | 1.5 Monitored gateway, allowlist | MCP gateway / AgentCore Gateway is the only route; JWT allowlist of four clients | every record's identity check |
| | 1.6 Control policy with automated enforcement | Cedar policies + `policies/incident_response.json` (severity → response) | D3, D4, D6 |
| | 1.7 Block, contain, terminate quickly | Kill switch; automatic end-session / suspend / pause-similar | D5 (~50 ms), D6 (~80 ms) |
| | 1.8 Expect agents to find gaps | Impact assessor: Workaround, Drift, Conflicting | D3, D4 |
| | 1.9 Red-team the controls | TelcoAgentBench (29 scenarios, four modes) | `reports/` |
| | 2.1 Indicators and severity scoring | S1–S4 in every signed record (`framework/incidents.py`) | log, incidents card |
| | 2.2 Drills | D1–D6 rehearsal (`scripts.rehearse`), recovery gate drill in D6 | D6 |
| | 2.6 Risk assessment before deployment | Model registry admission gate (Approved model version only) | AWS build |
| Detect | 3.1 Intended activity baseline | Intents and guards; guard baselines move with each owner's change | D3 |
| | 3.2 Monitor all layers | Pre-call twin prediction, post-action monitor (`IR-CONTROL-FAILURE`), agent transcripts | D1, tests |
| | 3.3 Agents cannot touch the logs | Ledger written by the SMO only; KMS signing, S3 Object Lock on AWS | D5 |
| | 3.4 Models from different families | Bedrock Evaluations judge (Nova) on AWS; heuristic plus Guardrails screening | AWS build (Bedrock pending) |
| | 3.5 Escalation and canaries | Holds to a human; canary tool `bulk_reset_site` | D2–D4, D6 |
| | 4.2 Outputs may carry exploits | Screening and quarantine of untrusted alarm text before the model reads it | bench injection scenarios |
| Respond | 5.1 Preserve rejected actions and tool calls | Every denial is a ledger record; self-check blocks kept in the transcript | evidence package |
| | 5.2 Preserve configuration | Policy-set SHA-256 in every record; card hash, model in the package | evidence package |
| | 5.3 Tamper-resistant logs to reconstruct trajectories | Hash chain + signatures; transcript SHA-256 in a `trace` record | D5, evidence |
| | 5.4 Analyse what the agent did | Timeline with each record's checks; after-action report | evidence package |
| | 6.1 Shutdown policy incl. monitor outage | Fail closed on policy, ledger or twin outage (S2) | fail-closed toggles |
| | 6.2 Shut down trajectories automatically | `end_session`: later calls get `CONTAIN-SESSION-ENDED` | D4 (S2), D6 |
| | 6.3 Pause similar agents | `pause_similar` (same vendor or model package) | D6: orchestrator paused |
| Recover | 7.3 Verify containment before restart | Recovery gate self-test (7 checks) | D6: refused with the twin down |
| | 7.4 Replay the incident against new controls | Replay with recorded loads and predictions under today's policies | D6; control-failure test needs a policy fix |
| | 7.5 Document recovery decision and remaining risk | Signed `recover` record with residual risk and lessons | D6 |
| | 7.6 After-action report | Generated in the evidence package | D6 incident report |
| Report | 8.2 Timelines and transcripts for affected parties | Signed evidence package (JSON + report) | evidence links |
| | 9.x Notify government | Reporting guidance in the after-action report (CISA, NIST CAISSI / NSA SISC) | incident report |
| | 10.2 Facilitate third-party review | Offline verification: `verify_ledger.py --evidence` | command line |

Not covered:
* **1.2 single-use infrastructure:** the AWS build's per-session AgentCore Runtime microVMs come closest.
* **2.4 external auditors:** organisational, outside the code.
* **2.5 published agent identifiers:** the AWS build's Cognito client IDs are the agent identifiers.
* **7.7 training-safe recovery:** the PPO policy is retrained only through the model registry gate.

Honest limits:
* **The gateway is the only enforcement point.** Standalone, the agents report their own transcripts
  and token usage. The hash binds the transcript to the ledger, but nothing independent produced it.
  On AWS, Bedrock invocation logs and AgentCore traces are the independent source.
* **The replay cannot rerun the impact assessor.** It re-evaluates Cedar, budgets and floors with the
  recorded context, and carries the recorded assessor finding forward; the assessor itself is not run again.
