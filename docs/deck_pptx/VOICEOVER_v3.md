# Voice-over script: Trusted rApp Framework (v3 deck)

Deck: `Trusted_rApp_Framework_TIP2026_v3.pptx` (14 slides). Run id on screen: `2026-11-02T13-10-track3-d1d6-04`, commit `f7c196f`.
The same text is in each slide's speaker notes. Times assume about 150 words per minute.

## Full voice-over for the demo video (about 4:50; the limit is 5:00)

### Slide 1: Cover  (0:00, about 16 s)

Operators want AI agents to run parts of the radio network, but they will not give them write access. We are team zero-seven, Track 3. This is the Trusted rApp Framework, running live on Amazon Bedrock AgentCore in our team account.

### Slide 2: The business problem  (0:16, about 22 s)

The business problem: O-RAN opens the network to rApps from any vendor, and they are becoming AI agents. But the safeguards are manual: protected-site lists, change caps, change boards. Nothing enforces them where the agent acts, and when a cell goes dark, nobody can prove what was allowed, or why. The barrier is trust, not capability.

### Slide 3: Why per-call policy is not enough  (0:38, about 23 s)

Per-call policy is not enough. The energy agent switches off light cells, one valid step at a time, while the load-balancing agent relies on them for tonight's concert. Every call passes policy; together they break the other team's guard. And the model is not the control: a forged change ticket fooled Claude in two of three runs.

### Slide 4: Our answer  (1:01, about 24 s)

Our answer: one checkpoint for every tool call. Identity, Bedrock Guardrails, Cedar policy and a blast-radius budget come first. Then the twin predicts the change, and our impact assessor checks it against every agent's intent and guards, and the agent's own history. It can only tighten: run, hold for a human, or deny. Every decision becomes a signed record.

### Slide 5: AWS architecture  (1:25, about 24 s)

On AWS: four Strands agents on AgentCore Runtime, each with its own identity, call Claude on Bedrock. Their only route to the network is AgentCore Gateway, with fourteen Cedar policies in AgentCore Policy. Behind it, our enforcement point runs the MAVERIC twin and the assessor. Decisions are KMS-signed into S3 Object Lock. One CDK deploy, every role under the account's boundary.

### Slide 6: Code flow  (1:49, about 26 s)

The code flow for one tool call: the agent plans, with hooks for scope and token budget. The gateway checks the token and Cedar. Our enforcement point runs containment, canary and cost checks, gets the twin's prediction, applies scope, budget and tier, and the assessor classifies it. Severity is scored, the record is signed, and only then does the change run, wait, or stop.

### Slide 7: Agentic flow  (2:15, about 23 s)

The agentic flow: the orchestrator splits the operator's intent into tasks and changes nothing itself; the specialist agents act within their scope, all through the same checkpoint. On AWS, the load-balancing agent compared tilt plans on the twin and was held for a human with the prediction: maximum cell load from 324 down to 244. Approved, executed, recorded.

### Slide 8: Live on AWS: the four units  (2:38, about 29 s)

These are the four Track 3 units, live on AWS, with the run id on screen. Deny: three of three harmful requests refused with a named policy or class: drift, a workaround, and a canary. Allow: two of two, both live Bedrock agents. Pause: a killed agent's next call was refused in 111 milliseconds. Trace: all 35 records verify, and eight sessions rebuild from S3. Every target was declared before the run.

### Slide 9: Incident readiness  (3:07, about 24 s)

Incident readiness is built in, from prepare to report. In D6 an agent touched a canary tool. Sixty-six milliseconds later its session was ended, it was suspended, and its same-vendor sibling paused. The recovery gate refused a restart while the twin had a fault; once cleared, the incident replayed as refused under today's policy, and the residual risk was signed.

### Slide 10: Evidence chain  (3:31, about 22 s)

Every result is provable. The declaration was written before the run, with the frozen test-set hash. One run id leads to every session and record. The grader is rule-based code, not a model. The live test uses only S3 reads, so it works after the freeze, and the offline rebuild needs only the public key.

### Slide 11: What only this framework does  (3:53, about 20 s)

What only this framework does: it checks the combined outcome across agents, fails closed, scores severity in every record, contains automatically, trips a canary, gates recovery, signs a receipt for every session, and caps tokens and cost per agent, all outside the agent, so the model is just configuration.

### Slide 12: Measured, misses included  (4:13, about 18 s)

Measured, misses included: per-call policy stopped two of six system-level harms; with the impact assessment, five of six, and no benign request denied. Our gaps are declared: our own twin, state still on the twin host, and about two seconds per change for the prediction.

### Slide 13: Judging guide map  (4:31, about 8 s)

Here is how this maps to the judging guide, criterion by criterion, with the slide that shows each one.

### Slide 14: Conclusion  (4:39, about 11 s)

The Trusted rApp Framework turns manual safeguards into enforced, provable policy, so operators can finally give agents write access to the network. Next: an operator trial. Thank you.

## Two-minute live pitch (about 2:07; the guide allows 2:00 plus 30 s)

Use the slides listed; skip the others or leave them up while you talk.

**Slide 1.** Operators want AI agents to run parts of the radio network, but they will not give them write access. We are team zero-seven, Track 3. This is the Trusted rApp Framework, running live on Amazon Bedrock AgentCore in our team account.

**Slide 2.** O-RAN opens the network to agents from any vendor, but the safeguards are manual and nothing proves afterwards what was allowed or why. The barrier is trust, not capability.

**Slide 3.** And per-call policy is not enough: two well-meaning agents, every call allowed, together break another team's guard. Per-call policy alone stopped two of six of these harms.

**Slide 4.** Our answer: one checkpoint for every tool call on AgentCore, then the twin predicts the change and our impact assessor checks it against every agent's goals. It can only tighten: run, hold for a human, or deny, and every decision is KMS-signed.

**Slide 5.** It runs live in our team account: agents on AgentCore Runtime, AgentCore Gateway and Policy, our enforcement point with the MAVERIC twin, and a KMS-signed ledger in S3 Object Lock.

**Slide 8.** On AWS, all four Track 3 units pass: deny three of three, allow two of two, a paused agent refused in 111 milliseconds, and all 35 records verify, rebuilt from S3. Targets were declared before the run.

**Slide 9.** Incident response is built in: a canary tool tripped, and in 66 milliseconds the agent and its same-vendor sibling were contained, with a recovery gate that replays the incident before restart.

**Slide 10.** Every result is provable from read-only records: targets declared first, one run id, a rule-based grader, and an offline rebuild with just the public key.

**Slide 11.** What only this framework does: a system-level check across agents, automatic containment with a recovery gate, and a signed receipt for every session, all outside the agent.

**Slide 14.** The Trusted rApp Framework turns manual safeguards into enforced, provable policy, so operators can finally give agents write access to the network. Next: an operator trial. Thank you.
