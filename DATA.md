# Data lineage (team07)

Synthetic and public data only. No operator logs or customer data.

## Datasets

| Dataset | Source | Licence | How it is made |
|---|---|---|---|
| MAVERIC energy-saving sample data (cell topology, UE positions per hour) | MAVERIC 1.0.0, `apps/energy_savings` sample data (Meta Platforms / TIP RIC project) | MIT | `twin/train_twin.py` runs MAVERIC's own `UEDataPreprocessor` on the bundled sample, into `twin_data/energy_saving_data/generated_data` |
| Twin models (per-cell Bayesian digital twin, 15 cells) | trained by us from the dataset above | MIT (derived) | `python -m twin.train_twin` → `twin_data/models/bdt_model_map.pickle` (42 s on CPU) |
| PPO energy-saving policy | trained by us on the twin above | MIT (derived) | `scripts/setup_env.ps1` (25k steps, ~5 h CPU); retrain on SageMaker with `python -m scripts.aws.train_rl_sagemaker` |
| TelcoAgentBench scenarios (29) | written by us | this repository | `bench/scenarios.py`, frozen in Git; results in `reports/` with sha256 |
| Planned events, cell criticality, KPI floors, agent intents | written by us | this repository | `policies/*.json`, `policies/rapp_cards/*.json` |

The demo uses our own MAVERIC deployment, not the event's hosted 4-cell twin: the framework needs 15 cells,
cell on/off, cell criticality, planned events and a `what_if` prediction before a change runs, which the hosted
twin's `/reset` `/action` `/advance` interface does not offer (see GAPS.md).

## Model outputs we rely on

| Use | Model id | Where the prompt lives | Output record |
|---|---|---|---|
| Energy, load-balancing, vendor and orchestrator agents | Bedrock `us.anthropic.claude-opus-5-5` (us-east-2) | `agents/*/agent.py` (system prompts), the agent card in `policies/rapp_cards/` | every tool call's decision is a signed ledger record; the agent's full transcript is in the session's evidence package and its SHA-256 is in the ledger |
| Prompt-attack screening of alarm text | Bedrock Guardrail `trusted-rapp-untrusted-input` | `infra/trusted_rapp_infra/stack.py` | guardrail verdict recorded with the decision |

Earlier laptop runs (before Nov 1) used `claude-opus-5-5` through the Anthropic API with the server-side refusal
fallback; those reports say so and are kept as they are.

## Grader

The grader is not a contestant. Allow / hold / deny is decided by Cedar policy, the risk tier and the impact
assessor (deterministic code on the twin's prediction), and the benchmark scores outcomes with rule-based checks
in `bench/run_bench.py`. No language model grades another model's output.
