# Trusted rApp Framework on AWS — CDK app and runbook

One stack (`TrustedRapp`, us-east-1) built from the same code as the standalone build. Nothing is
deployed until you run the commands in "Deploy" below.

## The model switch: Bedrock or the Claude API

`modelProvider` in `cdk.json` (or `-c modelProvider=...` on the command line) decides where every agent's
model runs, on the twin host and on AgentCore Runtime. Same agents, same model, same framework.

| `modelProvider` | Model | Needs | Bedrock Guardrail |
|---|---|---|---|
| `anthropic` (current setting) | `anthropicModelId`, default `claude-opus-5-5`, via the Anthropic API | your Anthropic API key, stored in Secrets Manager by `scripts\aws\set_anthropic_key.ps1` | off (no Bedrock call at all); the SMO's own screening of alarm text stays on |
| `bedrock` | `modelId`, default `us.anthropic.claude-opus-5-5` | Bedrock model access in the account | on: alarm text in the gateway interceptor and the agents' model calls |

`useGuardrail` overrides the last column (for example `anthropic` plus the Guardrail once Bedrock works).
Switching later is `cdk deploy -c modelProvider=bedrock`. Locally, the standalone build uses
`AGENT_MODEL_PROVIDER` in `trusted-rapp/.env` the same way (default `anthropic`).

## What gets deployed

| Blueprint block | Service | What it does here |
|---|---|---|
| Agentic Network Framework | **AgentCore Gateway** | The agents' only route to the network (MCP). JWT authorizer accepts only the four agent clients. |
| | **Gateway interceptors** (Lambda, REQUEST + RESPONSE) | REQUEST: maps the caller's Cognito client to its rApp and overwrites `_caller` (no spoofing). RESPONSE: Bedrock Guardrails screens alarm text before the model reads it; hides internal arguments from `tools/list`. |
| | **AgentCore Policy** (Cedar, ENFORCE) | 11 policies generated from `policies/scope.cedar` (`infra/trusted_rapp_infra/policies.py`): per-agent tool scope, no switch-off of critical cells, orchestrator read-only, and the canary passed through to the SMO (which denies it and contains the caller). Same policy IDs as the SMO's own checks. |
| | **AgentCore Identity** | One OAuth2 credential provider per agent (Cognito client credentials); agents on Runtime get tokens without holding secrets. |
| | **AgentCore Runtime** (×4, direct code deploy, ARM64) | Energy, load-balancing, vendor and orchestrator agents (Strands). The orchestrator calls the others with `InvokeAgentRuntime`. |
| | **Bedrock** + **Guardrails** | Agents' model (`modelId` in `cdk.json`), prompt-attack guardrail on alarm text and on agent model calls. |
| | **Strands SDK**, **MCP** | Unchanged agent code; tools served by the gateway's Lambda target. |
| SMO / twin | **EC2** (t3.xlarge, no inbound ports) | MAVERIC twin + enforcement point + demo page, as systemd service. Reached with SSM port forwarding. |
| Evaluation and promotion | **SageMaker Model Registry** | Groups for the PPO policy and the twin. Admission refuses the energy agent unless its model has an *Approved* version (`ADMIT-MODEL-NOT-APPROVED`). |
| | **SageMaker training** | `scripts/aws/train_rl_sagemaker.py` retrains the PPO policy; `register_models.py` registers and approves it. |
| | **Bedrock Evaluations** | `scripts/aws/bedrock_eval.py`: LLM-as-judge comparison of candidate agent models on the agents' real decisions. |
| Data foundation | **Kinesis** → **Firehose** → **S3** → **Glue** → **Athena** | Every decision and twin change is streamed; table `trusted_rapp.events`; saved queries `decisions-by-class`, `held-and-denied`, `kpi-timeline`. |
| Cross-cutting | **KMS** (ECC P-256) | Signs every ledger record (`kms:Sign` only; the key never leaves KMS). |
| | **S3 Object Lock** | One ledger object per record, governance retention (`ledgerRetentionDays`). |
| | **IAM** | One role per agent runtime; host, gateway, target and interceptor roles least-privilege. |
| | **CloudWatch** | Lambda, Runtime and Gateway logs; budget alarm (optional). |
| Network | **VPC** with S3 gateway endpoint and Secrets Manager endpoint | No NAT gateway; the tool target reaches the host privately. |

Not deployed (outside this stream or not worth the cost for the demo): Crosstrainer/federated-learning
pieces, Transit Gateway / VPN / Direct Connect (production path to an operator SMO), Neptune and a
Knowledge Base (see "Optional next").

## Before you deploy

1. AWS CLI signed in (`aws sts get-caller-identity` shows account 752018682700), region us-east-1.
2. **Only for `modelProvider=bedrock`:** Bedrock model access works (on Oct 27, 2026 every model still
   returned *"Operation not allowed"*, and the Anthropic use-case form was not submitted). Check:
   `aws bedrock-runtime converse --model-id us.amazon.nova-micro-v1:0 --messages '[{"role":"user","content":[{"text":"ok"}]}]'`
3. Corporate network: `AWS_CA_BUNDLE=C:\Users\svenugopal\.aws\ca-bundle-hcl.pem` (already set with `setx`).
   Node (CDK CLI) needs the same file: `setx NODE_EXTRA_CA_CERTS C:\Users\svenugopal\.aws\ca-bundle-hcl.pem`.
4. Session Manager plugin for the demo-page tunnel: `winget install -e --id Amazon.SessionManagerPlugin`.
5. Optional: put your email in `cdk.json` → `budgetEmail` for an 80 % budget alert.

## Deploy

```powershell
cd trusted-rapp\infra
.venv\Scripts\python.exe build_host_bundle.py             # twin host code + MAVERIC (zips, ~100 MB, seconds)
.venv\Scripts\python.exe build_runtime_bundle.py          # agents for AgentCore Runtime (ARM64 zip, ~30 MB)
cdk bootstrap aws://752018682700/us-east-1                  # once per account/region (S3 bucket + roles; removed Oct 27, redo)
cdk deploy                                                  # ~15 min; the host then installs itself (~10 min)
```

After deploy (outputs are printed; also `aws cloudformation describe-stacks --stack-name TrustedRapp`):

```powershell
powershell -ExecutionPolicy Bypass -File ..\scripts\aws\set_anthropic_key.ps1   # modelProvider=anthropic only: hidden prompt
python -m scripts.aws.register_models            # PPO + twin -> Model Registry (PendingManualApproval)
python -m scripts.aws.register_models --approve  # promote; without this D1 refuses the energy agent (the point)
aws ssm start-session --target <TwinHostId> --document-name AWS-StartPortForwardingSession --parameters portNumber=8700,localPortNumber=8700
# then open http://127.0.0.1:8700 — the same demo page, now on AWS: agents through AgentCore Gateway,
# ledger signed by KMS into S3 Object Lock, events in Athena
```

Host logs: `aws ssm start-session --target <TwinHostId>` then `sudo journalctl -u trusted-rapp -f`
(first boot: `sudo tail -f /var/log/cloud-init-output.log`).

## Day-2

| Task | Command |
|---|---|
| Orchestrator on Runtime (agent-to-agent) | `aws bedrock-agentcore invoke-agent-runtime --agent-runtime-arn <ORCHRuntimeArn> --runtime-session-id <33+ chars> --payload '{"intent":"Stadium concert tonight near Site 2/3"}' out.json` |
| Orchestrator from the host | `python -m scripts.orchestrate --event --tick 20 "Stadium concert tonight ..."` |
| Retrain PPO on SageMaker | `python -m scripts.aws.train_rl_sagemaker --timesteps 50000 --wait` then `register_models --from-training-job <job>` |
| Compare agent models | `python -m scripts.aws.bedrock_eval start` / `status` |
| Query the audit trail | Athena workgroup `trusted-rapp`, saved queries |
| Verify the ledger offline | download `s3://<LedgerBucket>/ledger/records/`, `aws kms get-public-key`, `python verify_ledger.py` |
| Policy dry run | `cdk deploy -c policyMode=LOG_ONLY` |
| Pause cost | `aws ec2 stop-instances --instance-ids <TwinHostId>` (start again before the demo) |
| Remove | `cdk destroy` (the KMS key and the ledger bucket are retained on purpose: audit evidence; delete them by hand after the retention period) |

## Cost (us-east-1, rough)

| Item | While running |
|---|---|
| EC2 t3.xlarge | ~$4.0 / day |
| Kinesis (1 shard), Secrets Manager endpoint (1 AZ), 6 secrets, KMS key | ~$0.7 / day |
| Gateway, Policy, Identity, Runtime, Lambda, Firehose, Glue, Athena | cents at demo volume |
| Model tokens (Bedrock or Anthropic API, same rates) | ~$0.3 per full agent-mode D1–D5 on Opus 5.5 |
| SageMaker training (ml.c5.2xlarge) | ~$0.4 / h, only while a job runs |

## Known limits (honest list)

* Gateway-level policy denials (AgentCore Policy) are logged by AgentCore, not in the SMO ledger: the
  request never reaches the SMO. Correlate by the agent's session ID.
* `set_tilt` cell limits are enforced by the SMO, not AgentCore Policy: Cedar cannot read cell IDs out of
  the `changes` records.
* Framework state (budgets, approvals, intents' baselines) is SQLite on the host; DynamoDB and Step
  Functions approvals are the next port step. The ledger itself is already KMS + S3.
* The PPO model runs on the host; on AgentCore Runtime the energy agent's `rl_recommend` reports
  "unavailable" and the agent plans from KPIs and `what_if` alone.
* With `modelProvider=anthropic`, Bedrock Evaluations and Guardrails are unavailable until Bedrock access works.
* Not yet run against AWS: validated by `cdk synth` in both model modes (CloudFormation validation clean),
  40 offline tests with stubbed AWS clients, and the standalone stack. The CDK bootstrap stack was removed
  on Oct 27, 2026 to stop all billing: run `cdk bootstrap` again first.

## Optional next

* Neptune for the cell/site topology graph (blast areas as a graph query).
* Bedrock Knowledge Base with operator runbooks for agents to cite in justifications.
* AgentCore Evaluations online evaluation on the runtimes' traces; AgentCore Policy temporal rules
  (session history) as a gateway-level complement to the drift check.
