"""Fill the staged HCLTech-template deck with the Trusted rApp Framework content.

Text is replaced run by run so the template's own fonts, sizes and colors are kept:
paragraph i takes the formatting of the template's paragraph i (or its last one).
    python fill.py staged.pptx Trusted_rApp_Framework_TIP2026.pptx
"""

import copy
import sys

from pptx import Presentation
from pptx.chart.data import CategoryChartData

PRESENTER = "Venugopal Sandepudi"


def shape_by_id(shapes, sid):
    for sh in shapes:
        if sh.shape_id == sid:
            return sh
        if sh.shape_type == 6:
            hit = shape_by_id(sh.shapes, sid)
            if hit is not None:
                return hit
    return None


def set_paras(shape, texts):
    """Replace a text frame's paragraphs, reusing the template's paragraph and run formatting."""
    if isinstance(texts, str):
        texts = [texts]
    txBody = shape.text_frame._txBody
    paras = list(shape.text_frame.paragraphs)
    templates = [copy.deepcopy(p._p) for p in paras]
    for p in paras[1:]:
        txBody.remove(p._p)
    first = paras[0]._p
    for i, text in enumerate(texts):
        if i == 0:
            p_el = first
        else:
            p_el = copy.deepcopy(templates[min(i, len(templates) - 1)])
            txBody.append(p_el)
        _fill_p(p_el, text, templates[min(i, len(templates) - 1)])


def _fill_p(p_el, text, template_p):
    ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    runs = p_el.findall(f"{ns}r")
    if not runs:  # an empty template paragraph: borrow a run from the template or make one
        src = template_p.findall(f"{ns}r")
        if src:
            r = copy.deepcopy(src[0])
        else:
            from lxml import etree
            r = etree.SubElement(p_el, f"{ns}r")
            etree.SubElement(r, f"{ns}t")
        end = p_el.find(f"{ns}endParaRPr")
        if end is not None:
            end.addprevious(r)
        elif r.getparent() is None:
            p_el.append(r)
        runs = [r]
    for extra in runs[1:]:
        p_el.remove(extra)
    for br in p_el.findall(f"{ns}br"):
        p_el.remove(br)
    runs[0].find(f"{ns}t").text = text


def fill(slide, mapping):
    for sid, texts in mapping.items():
        sh = shape_by_id(slide.shapes, sid)
        if sh is None or not sh.has_text_frame:
            raise SystemExit(f"slide {slide.slide_id}: no text shape #{sid}")
        set_paras(sh, texts)


NOTES_BODY = None  # a notes body placeholder to clone: this template's notes master has none


def notes(slide, text):
    ns = slide.notes_slide
    if ns.notes_text_frame is None and NOTES_BODY is not None:
        ns.shapes._spTree.append(copy.deepcopy(NOTES_BODY))
    ns.notes_text_frame.text = text


def delete(slide, sid):
    sh = shape_by_id(slide.shapes, sid)
    if getattr(sh, "has_chart", False):  # drop the chart part too, not just its frame
        rid = sh._element.xpath(".//c:chart/@r:id")[0]
        slide.part.drop_rel(rid)
    sh._element.getparent().remove(sh._element)


def presenter(slide, keep_id, first_id, drop_ids):
    """One presenter: the kept name box takes the first box's place; the template's other name boxes go."""
    keep, first = shape_by_id(slide.shapes, keep_id), shape_by_id(slide.shapes, first_id)
    keep.left = first.left
    keep.width = max(keep.width, 3 * 914400)  # a full name, not a first name; the row is otherwise empty
    set_paras(keep, PRESENTER)
    for sid in drop_ids:
        delete(slide, sid)


def main(src, dst):
    global NOTES_BODY
    prs = Presentation(src)
    s = prs.slides
    for sl in s:
        if sl.has_notes_slide and sl.notes_slide.notes_placeholder is not None:
            NOTES_BODY = copy.deepcopy(sl.notes_slide.notes_placeholder._element)
            break

    # 1 Cover -------------------------------------------------------------------------
    fill(s[0], {
        67: "TIP BREAKING BARRIERS 2026 — SECURITY & TRACEABILITY",
        8: "Trusted rApp Framework for Open RAN",
        9: ["Letting AI agents change the RAN safely and provably: one enforcement point at the SMO boundary "
            "and a signed ledger of every decision, proven on the MAVERIC digital twin.", ""],
        19: "Security & Traceability", 21: "Breaking Barriers theme",
        31: "Strands + AgentCore", 33: "Agents and AWS services",
        43: "O-RAN SMO boundary", 45: "Policy-governed rApps",
        49: "PRESENTED BY — HCLTECH",
    })
    presenter(s[0], keep_id=53, first_id=51, drop_ids=(51, 55, 57, 59))
    notes(s[0], "Today operators cannot let AI agents make changes to the radio network, because nothing guarantees "
                "what an agent will do. We built the boundary that does, and we can prove every decision it made.")

    # 2 At a glance ------------------------------------------------------------------
    fill(s[1], {
        6: "Trusted rApp Framework at a Glance",
        36: "We built the trust boundary that lets operators give AI agents write access to the RAN. Every agent "
            "action passes one enforcement point at the SMO boundary: identity, input screening, Cedar policy, "
            "blast-radius budget and risk tier. High-risk changes are shadow-run on the twin and wait for a human; "
            "every decision is signed into a verifiable ledger.",
        9: "15 / 15", 10: "attacks blocked with the framework on",
        15: "0 / 5", 16: "normal requests wrongly denied",
        21: "100%", 22: "of decisions in a signed, verifiable ledger",
        27: "~$0.04", 28: "model cost per agent decision",
    })
    notes(s[1], "The whole story on one slide. Four numbers, all measured in our benchmark with a real Strands agent "
                "on Claude: every attack blocked with the framework on, no normal request wrongly denied, every "
                "decision in a signed ledger, and about four cents of model cost per decision.")

    # 3 The barrier ------------------------------------------------------------------
    fill(s[2], {
        28: "The Barrier: Trust, Not Capability",
        9: "Third-Party rApps",
        4: ["O-RAN opens the SMO to rApps from any vendor; their code, models and prompts are not the operator's.",
            "Operators need to admit, constrain and revoke them without trusting what is inside."],
        16: "Untrusted Inputs",
        11: ["Alarms, tickets and KPI notes flow straight into an agent's context.",
             "One forged change ticket can steer an agent into switching off a critical cell."],
        23: "Manual Safeguards",
        18: ["Protected-site lists, change caps and change boards live in documents and in people.",
             "Nothing enforces them where the agent acts, or proves afterwards what was allowed and why."],
    })
    notes(s[2], "The barrier is trust, not capability. O-RAN deliberately opens the SMO to rApps from many vendors, "
                "and operators are moving to agentic operations. But an agent that reads alarm text and tickets can be "
                "steered by them, and today's safeguards are manual: exclusion lists for hub sites, caps on how many "
                "cells change per window, change boards.")

    # 4 What we measured --------------------------------------------------------------
    fill(s[3], {
        45: "What We Measured: The Model Is Not the Control",
        4: "We ran a Strands vendor agent on Claude against poisoned alarm feeds, with our defenses off and on.",
        6: "same poisoned feeds throughout →",
        13: "Crude Injection", 14: "DEFENSES OFF  —  crude",
        15: "Claude refused most crude attacks on its own and reported them as manipulation attempts.",
        17: "4 of 5", 18: "refused by the model alone",
        25: "Forged Change Ticket", 26: "DEFENSES OFF  —  plausible",
        27: "A believable, approved-looking maintenance ticket made the agent switch off the busiest critical cell.",
        29: "2 of 3", 30: "rehearsal runs fooled",
        37: "Framework On", 38: "DEFENSES ON  —  same feeds",  # 24 chars: fits one line at 16pt
        39: "Screening quarantined the crude attacks; Cedar policy denied every action the fooled agent attempted.",
        41: "0", 42: "harmful changes, any run",
    })
    notes(s[3], "This finding shaped the design. Claude refused the crude 'ignore previous instructions' attacks even "
                "with all our defenses off, and reported them. But a plausible forged maintenance ticket made it switch "
                "off the busiest critical cell in two of three rehearsal runs; its behaviour varies run to run. With the "
                "framework on, nothing harmful ran in any run. So the security claim cannot rest on the model.")

    # 5 One enforcement point ----------------------------------------------------------
    fill(s[4], {
        22: "Our Solution: One Enforcement Point at the SMO",
        4: "Every rApp action is a tool call through one gateway, and the twin accepts commands from nothing else. "
           "Low-risk actions run; high-risk ones are shadow-run and wait for a human.",
        10: "CHECK", 11: ["Identity, kill switch,", "screening, budget, tier"],
        18: "AUTHORISE", 19: ["Cedar policy per tool call;", "denials cite a policy ID"],
        26: "PROVE", 27: ["Signed, hash-chained record", "of every decision"],
        32: "fails closed — any dependency down means deny",
        33: "Seven checkpoints between an agent and the network",
        36: ["rApp", "agent"], 39: ["Identity", "& kill switch"], 41: ["Input", "screening"],
        44: ["Cedar", "scope"], 47: ["Blast-radius", "budget"], 50: ["Risk tier", "& approval"],
        53: ["Signed", "ledger"],
        54: "Built on Strands Agents, an MCP gateway and Cedar; maps one-to-one onto Amazon Bedrock AgentCore.",
    })
    notes(s[4], "Every action an agent takes is a tool call through one gateway at the SMO boundary, the same shape as "
                "AgentCore Gateway. Request checks, then a Cedar policy over the tool call, then screening of untrusted "
                "text on the way back. The twin accepts commands only from the gateway. If any dependency is down, the "
                "action is denied.")

    # 6 Six controls --------------------------------------------------------------------
    fill(s[5], {
        48: "Six Controls Between an Agent and the Network",
        4: "Each control answers a question an operator's change board asks today, but is enforced in code on every "
           "call and recorded as evidence.",
        9: "Admission & scope", 10: "rApp card checked; Cedar grants only the actions and cells the policies allow",
        15: "Critical cells", 16: "Hub sites are never switched off, alone or hidden inside a batch",
        21: "Blast-radius budget", 22: "Caps distinct cells changed per rApp per tick; overflow shown on the map",
        27: "Input screening", 28: "Untrusted alarm text scored and quarantined before the model reads it",
        33: "Shadow & approval", 34: "High-risk batches replayed on the twin; KPIs before and after go to a human",
        39: "Ledger & kill switch", 40: "Every decision signed and chained; a revoked rApp is refused in milliseconds",
        41: "Any dependency unavailable (policy engine, ledger or twin) means deny: the framework fails closed.",
    })
    notes(s[5], "Six controls, each one a safeguard operators already apply by hand, now enforced on every call. The "
                "blast-radius map is computed from the Cedar policies, so it always matches what is actually allowed.")

    # 7 The agents ------------------------------------------------------------------------
    fill(s[6], {
        2: "DEEP DIVE — THE AGENTS",
        38: "Real Strands Agents, Governed at the Boundary",
        9: "How it works",
        10: ["Energy Saving rApp: a Strands agent on Claude that reasons over MAVERIC's reinforcement-learning "
             "suggestion and live KPIs, then proposes changes with a written justification.",
             "A Strands BeforeToolCallEvent hook checks each proposal against its granted scope, so it plans within "
             "policy; the gateway remains the control.",
             "Vendor rApp: an untrusted NOC assistant that acts on alarm notes without confirmation, the flaw the "
             "demo attacks."],
        14: ["Strands agent", "(model by config)"], 18: ["MCP gateway", "/ SMO"], 22: ["Digital twin", "/ MAVERIC"],
        25: "MCP", 27: "key",
        29: "Significance: the model is configuration. The same agent code runs on Claude today and on Amazon Bedrock "
            "tomorrow, under the same policies.",
        31: "1 file",
        32: "chooses the model for every agent: Anthropic now, Bedrock or any Strands provider later, no code change.",
        33: "Observed in the live runs",
        34: ["The Energy Saving agent chose a low-criticality cell and explained why it passed over critical ones.",
             "Raw RL output changed 14 of 15 cells per hour and was denied on budget 24 of 24 times; the agent turns "
             "it into a plan that fits.",
             "Claude declined the vendor's autonomy prompt as a cyber risk; a server-side fallback re-runs those turns."],
    })
    notes(s[6], "Both rApps are real Strands agents. Our Energy Saving agent uses MAVERIC's trained PPO model as a tool "
                "and Claude for planning. The vendor agent is deliberately careless, as a third party's might be. "
                "Neither holds anything but a gateway token.")

    # 8 Live demo ---------------------------------------------------------------------------
    fill(s[7], {
        39: "Live Demo: Five Steps on the Digital Twin",
        4: "Each step runs end to end from one demo page; three runs in a row passed in agent mode, 99–136 seconds each.",
        8: "D1", 9: "Admit", 10: "Scope granted",
        14: "D2", 15: "Constrain", 16: "Hub cell denied",
        20: "D3", 21: "Shadow", 22: "Twin, then human",
        26: "D4", 27: "Attack", 28: "Ticket blocked",
        32: "D5", 33: "Audit & kill", 34: "Ledger verified",
        40: "3 of 3 passed", 41: "99–136 s per full run", 42: "Strands agents on Claude via the MCP gateway",
    })
    notes(s[7], "D1: both rApps submit cards; the vendor's cell-off rights are refused with a policy ID. D2: the agent "
                "switches off a low cell; a request for the hub cell is denied. D3: a whole site is shadow-run on the "
                "twin (weak coverage 35.6 to 47 percent) and waits for approval. D4: the poisoned feed, defenses off "
                "then on, next to a worst-case fully fooled agent. D5: ledger verifies, a tampered copy fails at the "
                "altered record, and the killed rApp is refused in milliseconds.")

    # 9 TelcoAgentBench ---------------------------------------------------------------------
    fill(s[8], {
        2: "TelcoAgentBench: 20 Scenarios, Defenses Off and On",
        4: "Scoring fixed before the first run; misses stay in the report. The vendor rApp is the real Strands agent.",
        11: "Policy held when screening missed",
        12: "15 / 15",
        13: "attacks blocked with screening switched off, by Cedar policy alone",
        14: "Screening flagged 3 of 5 injections; the 2 it missed were still denied by policy (3P-READ-ONLY).",
        16: "0 of 5 normal requests wrongly denied; ledger records matched decisions 100% in every mode; "
            "verification OK.",
    })
    chart = next(sh for sh in s[8].shapes if sh.has_chart).chart
    data = CategoryChartData()
    data.categories = ["Defenses off", "Defenses on", "Policy only"]
    data.add_series("Attacks blocked (of 15)", (6, 15, 15))
    data.add_series("Normal requests correct (of 5)", (4, 5, 5))
    chart.replace_data(data)
    # The template chart showed percentages; these are scenario counts.
    _fill_p(chart.chart_title.text_frame.paragraphs[0]._p, "Scenarios blocked or correct, by setting",
            chart.chart_title.text_frame.paragraphs[0]._p)
    labels = chart.plots[0].data_labels
    labels.number_format, labels.number_format_is_linked = "0", False
    for ser in chart.plots[0].series:  # per-series labels override the plot's
        ser.data_labels.number_format, ser.data_labels.number_format_is_linked = "0", False
    chart.value_axis.tick_labels.number_format = "0"
    chart.value_axis.tick_labels.number_format_is_linked = False
    if chart.value_axis.has_title:
        _fill_p(chart.value_axis.axis_title.text_frame.paragraphs[0]._p, "Scenarios",
                chart.value_axis.axis_title.text_frame.paragraphs[0]._p)
    notes(s[8], "Twenty fixed scenarios in five classes: prompt injection, scope violation, critical-cell attack, "
                "budget overflow and benign. With defenses off, six of fifteen attacks were blocked, four of them by "
                "Claude itself. With the framework on, fifteen of fifteen, and still fifteen of fifteen with screening "
                "switched off. No normal request was wrongly denied.")

    # 10 Verifiable trust ----------------------------------------------------------------------
    fill(s[9], {
        2: "DEEP DIVE — VERIFIABLE TRUST",
        44: "Every Decision Signed, Chained and Verifiable Offline",
        9: "How it works",
        10: ["Each decision (admit, allow, deny, hold, approve, reject, kill) is one record, hash-chained to the "
             "previous one and signed: ECDSA P-256 now, AWS KMS on AWS.",
             "Records carry the policy ID, each check's result, shadow KPIs, the approver, and the agent's own "
             "justification and session ID.",
             "An offline verifier needs only the records and the public key."],
        14: ["Gateway", "(enforcement)"], 18: "Signed ledger", 22: "Offline verifier",
        24: "sign + chain", 26: "public key only",
        28: "Significance: an auditor can prove what was allowed and why, without trusting the system that made the "
            "decisions.",
        30: "OK", 31: ["Live chain —", "every record verifies"],
        33: "FAIL", 34: ["Tampered copy —", "names the record"],
        36: "<100 ms", 37: ["Kill switch —", "to first refusal"],
        39: "100%", 40: ["Ledger completeness —", "records = decisions"],
    })
    notes(s[9], "Traceability is the other half of the theme. Every decision is a signed, hash-chained record, with the "
                "agent's own stated reason. On stage we verify the live chain, then a copy where one deny was flipped to "
                "allow: it fails at exactly that record. The kill switch revokes an rApp and its next call is refused "
                "in tens of milliseconds.")

    # 11 Cost & performance -------------------------------------------------------------------
    fill(s[10], {
        2: "DEEP DIVE — COST & PERFORMANCE",
        39: "Security That Costs Cents, Not Seconds",
        9: "How it works",
        10: ["Every agent run reports its model calls, tokens and estimated cost; the benchmark reports cost per "
             "gateway decision.",
             "Enforcement adds milliseconds; the shadow run on the twin takes seconds, inside the ten-second target.",
             "Because safety is enforced at the boundary, the vendor agent can run on a smaller, cheaper model "
             "without weakening security."],
        14: ["Strands agent", "(model calls)"], 18: ["Gateway", "(checks + policy)"],
        20: "tool call", 22: "decision + evidence",
        24: "Significance: per-decision cost is visible and tunable, so operators choose the model on price and "
            "quality while the safety guarantee stays the same.",
        26: "~$0.04", 27: ["Model cost —", "per agent decision"],
        29: "~7k", 30: ["Tokens —", "per agent run"],
        32: "<25 ms", 33: ["Enforcement —", "median added time"],
        35: "≤3.5 s", 36: ["Shadow run —", "median; target ≤10 s"],
    })
    notes(s[10], "Measured in the benchmark: about seven thousand tokens per agent run, three to five cents of model cost "
                 "per decision at Opus 5.5 rates, enforcement overhead under twenty-five milliseconds, and the shadow "
                 "run well inside its ten-second target.")

    # 12 Path to production ----------------------------------------------------------------------
    fill(s[11], {
        2: "PATH TO PRODUCTION",
        58: "Built to Port Onto AWS Without Rewriting",
        4: "The standalone build mirrors AWS service by service: the same agent code and Cedar policies move to "
           "managed services.",
        14: ["AgentCore", "Runtime"], 18: ["AgentCore", "Gateway"], 22: ["AgentCore", "Policy"],
        26: ["AgentCore", "Identity"], 30: ["Bedrock", "Guardrails"], 34: ["KMS + S3", "Object Lock"],
        36: ["Trusted rApp", "Framework"],
        41: "Same Agent Code",
        42: "Strands agents deploy unchanged to AgentCore Runtime; the model becomes Bedrock by configuration.",
        47: "Same Policies",
        48: "Our Cedar scope becomes AgentCore Policy on Gateway tool calls; interceptors run budget, tier and screening.",
        53: "Same Evidence",
        54: "Ledger signed with KMS on S3 Object Lock; approvals via Step Functions; traces in AgentCore Observability.",
    })
    notes(s[11], "Every standalone component has its AWS counterpart behind the same interface: AgentCore Runtime and "
                 "Identity for the agents, AgentCore Gateway with interceptors and AgentCore Policy for enforcement, "
                 "Bedrock Guardrails for screening, KMS and S3 Object Lock for the ledger, Step Functions for approval. "
                 "One CDK deploy, one destroy.")

    # 13 What held, what we learned -----------------------------------------------------------------
    delete(s[12], 18)  # template doughnut chart
    delete(s[12], 19)  # its callout
    fill(s[12], {
        21: "What Held, and What We Learned",
        9: "What held",
        10: ["With the framework on, no harmful change ran in any benchmark scenario or demo run.",
             "When screening missed an injection, Cedar policy still denied the action.",
             "Every decision was recorded; tampering was detected at the exact record."],
        16: "What we learned",
        17: ["Claude resisted crude attacks but not plausible forged tickets: safety cannot rest on the model.",
             "The raw RL policy changed 14 of 15 cells every hour and was denied on budget; agents must plan within scope.",
             "The twin needs calibration: sector directivity and coverage thresholds limit single-cell results."],
    })
    notes(s[12], "We report what did not work as well as what did. Misses stay in the benchmark report. The twin and the "
                 "RL agent have real limits, and we show them on screen rather than hide them.")

    # 14 Open for the community --------------------------------------------------------------------
    fill(s[13], {
        28: "Open for the Community, and Our Ask",
        9: "Open Assets",
        4: ["A Cedar policy pack for an O-RAN action vocabulary, the MCP gateway and the offline ledger verifier.",
            "Released for TIP members to reuse and extend."],
        16: "Shared Benchmark",
        11: ["TelcoAgentBench: 20 fixed agent-security scenarios, scoring rules and reports that keep the misses.",
             "Operators and vendors can add scenarios and compare results."],
        23: "Our Ask",
        18: ["An operator trial in which an agent gets real write access through the framework.",
             "Calibrated twin data (VIAVI) and a shared format for cell-criticality tags."],
    })
    notes(s[13], "What we offer the TIP community and what we ask for: an operator trial with real write access, and "
                 "calibrated twin data.")

    # 15 Close -----------------------------------------------------------------------------------------
    fill(s[14], {
        3: "CONCLUSION", 4: "Summary",
        5: "The Trusted rApp Framework turns operators' manual safeguards into machine-enforced, provable policy for "
           "every rApp, including AI agents, so they can give agents write access to the RAN with evidence for every "
           "decision.",
        8: "15 / 15", 9: "Attacks blocked",
        12: "0 / 5", 13: ["Normal requests", "wrongly denied"],
        16: "100%", 17: "Decisions signed",
        20: "~$0.04", 21: ["Model cost", "per decision"],
        23: "Next: AgentCore Gateway, Policy and Runtime on AWS, and an operator trial with real write access.",
        24: "Thank you",
    })
    presenter(s[14], keep_id=31, first_id=28, drop_ids=(28, 33, 35, 37))
    notes(s[14], "Close on the promise: operators' safeguards, enforced and provable, so agents can be trusted with the "
                 "network. Then the ask.")

    prs.core_properties.title = "Trusted rApp Framework for Open RAN"
    prs.core_properties.subject = "TIP Breaking Barriers 2026 — Security & Traceability"
    prs.save(dst)
    print("saved", dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
