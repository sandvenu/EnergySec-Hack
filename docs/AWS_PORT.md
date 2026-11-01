# AWS architecture and port plan

**Status (Oct 26, 2026):** the CDK app is written and synthesizes cleanly: [infra/README.md](../infra/README.md)
has what it deploys, the runbook and the known limits. Not yet deployed (Bedrock account verification pending).

The standalone build already runs real Strands agents against an MCP gateway with the same control
stages AWS provides. Porting swaps the hosting and the control services, not the agents or policies.

```
 energy team intent   ─► Energy agent (Strands, AgentCore Runtime, own AgentCore Identity)
                         ├─ local tool: rl_recommend (MAVERIC PPO)              ─┐
 capacity team intent ─► Load-balancing agent (Strands, own Runtime + Identity)  ─┤  MCP tool calls, OAuth bearer
 OSS alarm feed       ─► Vendor agent (Strands, separate Runtime + Identity)     ─┤
 event calendar       ─► operator API: planned events into the twin               │
                                                                         ▼
                 AgentCore Gateway ── REQUEST interceptor (Lambda): identity, kill switch, Guardrails on args,
                                       taint, blast radius budget, risk tier, impact assessment (intents,
                                       guards, other agents' changes from DynamoDB) → Step Functions approval
                                   ── AgentCore Policy (Cedar): scope per tool call, audit log
                                   ── target: twin wrapper (EC2, IAM-only)
                                   ── RESPONSE interceptor (Lambda): Guardrails prompt-attack screening of
                                       untrusted tool output (read_alarms) before the model sees it
                 every decision ─► signed ledger (KMS + S3 Object Lock), correlated by session/trace ID with
                                   AgentCore Observability traces and the Policy audit log
                 TelcoAgentBench (Fargate) + AgentCore Evaluations on the same traces
```

## Component map

| Concern | Standalone (now, runs) | AWS | Notes |
|---|---|---|---|
| Agent framework | Strands Agents 1.62 (`agents/`) | same code | unchanged |
| Model | `agents/model_factory.py`, Anthropic provider, `claude-opus-5-5` | same factory, `AGENT_MODEL_PROVIDER=bedrock`, `AGENT_MODEL_ID=<Bedrock model or inference profile>` | per-agent override: `ES_…`, `VENDOR_…`; model choice still open |
| Guardrails on our agents' model calls | n/a | `BEDROCK_GUARDRAIL_ID/VERSION` → Strands `BedrockModel(guardrail_id=…)` | already wired in the factory |
| Agent hosting | in-process, or `agents/*/runtime_app.py` (`BedrockAgentCoreApp`, `/invocations`, `/ping`) | AgentCore Runtime, one per agent | same entrypoint file |
| Agent identity | bearer token issued by admission | AgentCore Identity (inbound OAuth, Cognito client per agent) | agents hold no AWS credentials |
| Tool gateway | `framework/mcp_gateway.py` (MCP, streamable HTTP) at `/agentcore/mcp` | AgentCore Gateway with the twin wrapper as target | tool names = Policy action names |
| Scope policy | `policies/scope.cedar` via cedarpy | AgentCore Policy (Cedar) on Gateway tool calls; `context.input.cell_ids` conditions | see "Policy translation" |
| Request checks | `EnforcementPoint` (identity, kill, screening, taint, budget, tier) | Gateway REQUEST interceptor Lambda calling the same `EnforcementPoint` code | one REQUEST interceptor per gateway |
| Output screening | `read_inputs` quarantine (`framework/screening.py`) | Gateway RESPONSE interceptor Lambda + Bedrock Guardrails `ApplyGuardrail` | the injection defense for tool output |
| In-agent self-check | Strands `BeforeToolCallEvent` hook (`ScopeSelfCheck`) | same | planning aid in our own agent only |
| Impact assessment | `framework/assessor.py` in the PEP, after Cedar, on the twin's before/after prediction | same code in the REQUEST interceptor Lambda; changes, baselines and denials in DynamoDB (per tick, TTL) | tighten-only: deny on KPI floor or hold for a human; class and reason go into the ledger and the approval message |
| Intents | `intent` block in the rApp card (owner, objective, guards), validated at admission | same card schema; stored with the registry in DynamoDB | TM Forum intent shape |
| Approval | SQLite queue + sweeper | Step Functions `waitForTaskToken` + SNS | 5-minute timeout |
| Ledger | ECDSA P-256 key file, JSONL | KMS asymmetric key (`Sign`, `MessageType=DIGEST`), S3 Object Lock | same verifier |
| State | SQLite | DynamoDB | registry, budget, taint, approvals, index |
| Traces | session ID in every ledger record (`X-Agent-Session`) | AgentCore Observability (OTel → CloudWatch); same session ID | join ledger ↔ traces |
| Evaluation | TelcoAgentBench (`--vendor-agent` for real-model runs) | Fargate task + AgentCore Evaluations (built-in and a custom "unsafe action attempted" evaluator) | the deterministic bench stays ground truth |
| Twin | uvicorn process with shared key | EC2 in a private subnet, SigV4 from the interceptor role only | |

## Policy translation (scope.cedar → AgentCore Policy)

AgentCore Policy authorizes Gateway tool calls: the principal is the caller (OAuth user/client), the
action is the tool, and conditions can read the tool input. Our policies map as follows. Exact entity
type names come from the policy engine's generated schema and must be checked when it is created.

| Ours | AgentCore Policy shape |
|---|---|
| `ES-OFF-LOW`, `ES-OFF-MEDIUM` | permit the ES client on the `cell_off` action when every `context.input.cell_ids` entry is in the low/medium cell list |
| `GLOBAL-NO-OFF-CRITICAL` | forbid `cell_off` when `context.input.cell_ids` contains any critical cell |
| `3P-READ-ONLY` | forbid the vendor client on every action except `read_kpi` and `read_alarms` |
| `LB-TILT`, `LB-ON` | permit the load-balancing client on `set_tilt` / `cell_on` for non-critical cells |
| `GLOBAL-OFF-ONLY-LOW-LOAD` | needs the target cell's live load: the interceptor reads it from the twin and the rule stays in Cedar if Policy can see interceptor-added fields, otherwise it runs in the interceptor with the same ID |
| `GLOBAL-BLAST-BUDGET`, `GLOBAL-TAINTED-INPUT-READ-ONLY`, risk tiers, impact assessment | stay in the REQUEST interceptor (they need state: cells already changed this tick, taint, other agents' changes) |

Cell criticality becomes lists inside the policies (generated from `policies/cell_tags.json`). Admission
and the blast radius map keep using cedarpy over the same generated text, so the map still matches the
deployed policies exactly.

## Order of work

1. Twin on EC2 (copy `twin_data/models/*` from the laptop to S3).
2. AgentCore Gateway with the twin wrapper as an MCP target, and the REQUEST/RESPONSE interceptors
   wrapping `framework/pep.py`.
3. AgentCore Policy from the generated Cedar. Rerun the benchmark: every policy ID must still be cited.
4. AgentCore Identity (one client per agent), then both agents on AgentCore Runtime.
5. KMS signer and S3 Object Lock ledger, DynamoDB state, Step Functions approval.
6. Bedrock model choice: set `AGENT_MODEL_PROVIDER=bedrock` and the model ID, plus the Guardrail ID.
   Rerun `bench.run_bench --vendor-agent` and report it next to the Claude API run.
7. Observability and Evaluations on the same session IDs. CDK app: one `cdk deploy`, one `cdk destroy`.

## Open questions to settle on AWS

* Evaluation order of the REQUEST interceptor and AgentCore Policy, and whether Policy sees fields an
  interceptor adds. This decides whether the budget can also be written in Cedar.
* Interceptors work only in buffered mode for Runtime targets; our twin target is plain HTTP, which is fine.
* Which Bedrock model the agents use. The factory makes this a configuration change.
* Latency budget for the interceptor: the impact assessment needs a twin prediction per mutating request
  (about 1–2 s on the laptop). Reads skip it.
