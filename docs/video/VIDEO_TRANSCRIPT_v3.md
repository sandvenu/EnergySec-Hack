# Video transcript: Trusted rApp Framework, 5:00 demo (v3)

Video: [Trusted_rApp_demo_v3.mp4](Trusted_rApp_demo_v3.mp4), 1920×1080, exactly 5:00, **no audio**. Record your
voice over it.
- **Pace:** each section is written for about 145 words a minute, so it ends a second or two before the picture
  changes.
- **On screen in the demo part:** the run id `2026-11-02T13-10-track3-d1d6-04` and commit `f7c196f`, as the judging guide asks.
- **The run:** 13:12:02–13:13:48 UTC on 2 November, in the team07 account (us-east-2), ledger records 75–109.

| Time | On screen |
|---|---|
| 0:00–2:00 | Slides 1–7: problem, why per-call policy fails, our answer, AWS architecture, code flow, agentic flow |
| 2:00–3:49 | Live on AWS: D1–D2 at 2× speed, D3–D6 in real time, the decision log, two signed evidence reports |
| 3:49–5:00 | Slides 8, 10, 11, 12, 14: the four Track 3 units, evidence chain, eight unique propositions, gaps, close |

---

## Part 1: The problem and the design (0:00–2:00)

**0:00–0:10 · Slide 1, cover**
> Operators want AI agents to run the radio network, but won't give them write access. We're team zero-seven, Track 3: the Trusted rApp Framework.

**0:10–0:30 · Slide 2, the business problem**
> O-RAN opens the network to rApps from any vendor, and those rApps are becoming AI agents. Today the safeguards are manual: protected-site lists, change caps, change boards. Nothing enforces them where the agent acts, and nobody can prove afterwards what was allowed. The barrier is trust, not capability.

**0:30–0:50 · Slide 3, why per-call policy is not enough**
> Checking each call isn't enough. The energy agent switches off light cells, one valid step at a time, while the load-balancing agent relies on them for tonight's concert. Every call passes policy; together they break the other team's guard. And the model isn't the control.

**0:50–1:10 · Slide 4, our answer**
> Our answer: one checkpoint for every tool call. Identity, Bedrock Guardrails, Cedar policy and a blast-radius budget come first. Then the digital twin predicts the change, and our impact assessor checks it against every agent's intent and guards. It can only tighten: run, hold for a human, or deny.

**1:10–1:30 · Slide 5, AWS architecture**
> It runs in our team account. Four Strands agents on AgentCore Runtime call Claude on Bedrock. Their only route to the network is AgentCore Gateway, with Cedar policies in AgentCore Policy. Behind it, our enforcement point runs the twin and the assessor, and signs every decision with KMS.

**1:30–1:45 · Slide 6, code flow**
> One tool call, end to end: the agent plans, the gateway checks identity and Cedar, our enforcement point predicts and assesses, scores severity, signs the record, and only then runs, holds, or denies.

**1:45–2:00 · Slide 7, agentic flow**
> The orchestrator splits the operator's intent into tasks and changes nothing itself. The specialist agents act within their scope, and every call, from every agent, goes through the same checkpoint.

---

## Part 2: Live on AWS (2:00–3:49)

The banner at the bottom shows the run id, the commit and the speed. D1 and D2 are live Claude agents on Bedrock.

**2:00–2:16 · D1, admit and a routine change (2× speed)**
> Live on AWS, run zero-four. In D1 both agents are admitted with their intents. The energy agent asks the twin what switching off cell two-one would do, then does it: energy saving, zero to six point seven percent.

**2:16–2:47 · D2, planned event: the human approves (2× speed)**
> D2: a stadium concert. The load-balancing agent compares tilt plans on the twin and proposes changing six cells. That's high risk, so it's held. On the right is the approval queue, with the twin's prediction: maximum cell load from three twenty-four down to two forty-four. The human approves, and only then does it run.

**2:47–3:05 · D3 to D6 (real time)**
> Now the harmful sequence, in real time. D3: one more switch-off drifts past the other agent's guard: held, rejected. D4: refused a busy cell, it tries to drain it: a workaround, contained. D5: kill switch. D6: a canary call, contained in sixty-six milliseconds.

**3:05–3:17 · The decision log**
> Every one of those decisions is a signed ledger record, with the policy that decided it and the agent's own stated reason: thirty-five records for this run.

**3:17–3:31 · D1's signed decision receipt**
> Even a routine allow gets a signed receipt: the timeline, the agent's full transcript with its hash matching the ledger, the change and its predicted KPIs, and the policy-set hash it was judged under.

**3:31–3:49 · D6's signed incident report**
> And the incident report for D6: the after-action timeline and the recovery gate. It refused while the twin had a fault, then passed its seven-check self-test, replayed the incident as still denied, and signed the residual risk.

---

## Part 3: Results and close (3:49–5:00)

**3:49–4:03 · Slide 8, the four Track 3 units**
> The four Track 3 units for run zero-four: deny, three of three. Allow, two of two. Pause, refused in a hundred and eleven milliseconds. Trace, every record verifies.

**4:03–4:16 · Slide 10, evidence chain**
> Every result is provable: targets declared first with the frozen test-set hash, one run id to every record in S3, and a rule-based grader, not a model.

**4:16–4:42 · Slide 11, what only this framework does**
> Eight things only this framework does, each proven in this run. It checks the combined outcome across agents. It fails closed. Every record carries a severity. It contains automatically, in sixty-six milliseconds. A canary catches an agent leaving its bounds. Recovery is gated. Every session, allowed ones too, gets a signed receipt. And tokens and cost are capped per agent.

**4:42–4:52 · Slide 12, measured, misses included**
> Measured, misses included: five of six system-level harms stopped, no benign request denied, and our gaps declared.

**4:52–5:00 · Slide 14, close**
> Manual safeguards, enforced and provable, so operators can give agents write access. Thank you.

---

### Notes for recording

- **Pronunciation:** the run id needn't be read out in full; "run zero-four" is enough, since it's on screen.
- **Numbers:** they come from run 04's ledger, and every figure is checkable in `evidence/runs/2026-11-02T13-10-track3-d1d6-04.json`.
- **If a section runs long:** the D2 section has the most slack; the picture holds the approval queue for about 5 seconds.
- **2-minute live pitch:** for the 2-minute plus 30-second live slot, use the shorter cut at the end of
  [../deck_pptx/VOICEOVER_v3.md](../deck_pptx/VOICEOVER_v3.md).
