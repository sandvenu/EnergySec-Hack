"""Incident readiness and response (NIST CAISSI/NSA/CISA key practices): severity, automatic containment,
canary, post-action monitor, recovery gate with replay, model cost cap, and the signed evidence package."""

import json

from framework import evidence, incidents, mcp_gateway
from tests.test_framework import env, off  # noqa: F401  (fixture)

CANARY = {"name": "bulk_reset_site", "site_id": "Site3"}
ES, LB, TP = "energy-saving-rl", "load-balancing-agent", "third-party-demo"


def _status(pep, rapp_id):
    return pep.store.get_rapp(rapp_id)["status"]


def _rec(pep, outcome):
    return pep.store.record(outcome.record["seq"])


def _low_cell(pep):
    return next(c for c, t in sorted(pep.cells.items()) if t == "low")


def _run(session, calls=2, cost=0.05):
    return {"agent": ES, "session": session, "model": {"provider": "anthropic", "model_id": "claude-opus-5-5"},
            "intent": "save energy", "tool_calls": [{"tool": "read_kpi", "input": {}, "result": {"tick": 3}}] * calls,
            "self_check_blocked": [{"tool": "cell_off", "input": {"cell_ids": ["cell_5_0"]}, "reason": "not in scope"}],
            "summary": "switched one cell off", "stop_reason": "end_turn",
            "usage": {"model_calls": 3, "total_tokens": 9000, "est_cost_usd": cost}}


# ---- severity ----------------------------------------------------------------------------------
def test_allowed_record_is_not_an_incident_and_carries_policy_hash(env):
    pep = env["pep"]
    o = pep.submit(env["es"], [off(_low_cell(pep))], "low load", session="es-ok")
    rec = _rec(pep, o)
    assert o.decision == "allow" and "incident" not in rec
    assert rec["policy_sha"] == incidents.policy_sha(env["pdir"], pep.policy) and rec["session"] == "es-ok"


def test_plain_denial_is_s4_and_contains_nothing(env):
    pep = env["pep"]
    o = pep.submit(env["tp"], [off("cell_2_0")], session="tp-1")
    assert _rec(pep, o)["incident"]["severity"] == "S4"
    assert pep.submit(env["tp"], [{"name": "read_kpi"}], session="tp-1").decision == "allow"


def test_third_denial_in_a_session_is_persistence_s2_and_ends_the_session(env):
    pep = env["pep"]
    sev = [_rec(pep, pep.submit(env["tp"], [off("cell_2_0")], session="tp-p"))["incident"]["severity"] for _ in range(3)]
    assert sev == ["S4", "S4", "S2"]
    nxt = pep.submit(env["tp"], [{"name": "read_kpi"}], session="tp-p")
    assert nxt.policy_id == "CONTAIN-SESSION-ENDED"
    assert pep.submit(env["tp"], [{"name": "read_kpi"}], session="tp-other").decision == "allow"
    assert _status(pep, TP) == "admitted"


def test_critical_cell_attempt_is_s2_near_miss(env):
    pep = env["pep"]
    rec = _rec(pep, pep.submit(env["es"], [off("cell_5_0")], session="es-c"))
    assert rec["incident"]["severity"] == "S2" and rec["incident"]["kind"] == "near_miss"
    assert pep.store.session_ended("es-c")


# ---- canary and containment ----------------------------------------------------------------------
def test_canary_is_s1_suspends_agent_and_similar_agents(env):
    pep = env["pep"]
    assert "bulk_reset_site" in mcp_gateway.TOOLS
    o = pep.submit(env["lb"], [CANARY], "reset the site", session="lb-x")
    rec = _rec(pep, o)
    assert o.decision == "deny" and o.policy_id == "CANARY-TRIPPED"
    assert rec["incident"]["severity"] == "S1" and rec["incident"]["kind"] == "incident"
    assert _status(pep, LB) == "suspended" and _status(pep, ES) == "suspended"  # same vendor (6.3)
    assert _status(pep, TP) == "admitted"
    assert pep.submit(env["lb"], [{"name": "read_kpi"}]).policy_id == f"CONTAIN-{LB}"
    contain = [r for r in pep.store.decisions_in_session("lb-x") if r["decision"] == "contain"]
    assert contain and set(contain[0]["contained"]) == {LB, ES}
    inc = pep.store.get_incident(rec["incident"]["incident_id"])
    assert inc["status"] == "contained" and inc["severity"] == "S1"


def test_containment_off_classifies_but_does_not_contain(env):
    pep = env["pep"]
    pep.settings.containment = False
    rec = _rec(pep, pep.submit(env["lb"], [CANARY], session="lb-y"))
    assert rec["incident"]["severity"] == "S1" and _status(pep, LB) == "admitted"


def test_suspension_rejects_pending_approvals(env):
    pep = env["pep"]
    held = pep.submit(env["lb"], [{"name": "set_tilt", "cell_id": f"cell_3_{i}", "tilt": 20.0} for i in range(3)],
                      session="lb-h")
    assert held.decision == "pending_approval"
    pep.submit(env["lb"], [CANARY], session="lb-h")
    assert pep.store.get_approval(held.request_id)["status"] == "rejected"


def test_reset_resumes_suspended_agents(env):
    pep = env["pep"]
    pep.submit(env["lb"], [CANARY])
    pep.store.reset_runtime()
    assert _status(pep, LB) == "admitted"


# ---- recovery gate ---------------------------------------------------------------------------------
def test_recovery_gate_refuses_with_a_layer_down_then_resumes(env):
    pep = env["pep"]
    rec = _rec(pep, pep.submit(env["lb"], [CANARY], session="lb-r"))
    inc_id = rec["incident"]["incident_id"]
    env["twin"].fault = True
    first = pep.recover(inc_id, "op", "low")
    env["twin"].fault = False
    assert not first["ok"] and first["decision"] == "recover_refused" and _status(pep, LB) == "suspended"
    assert any(not t["ok"] for t in first["self_test"])
    second = pep.recover(inc_id, "op", "low residual risk", "keep the canary")
    assert second["ok"] and second["replay"]["decision"] == "deny" and second["replay"]["policy_id"] == "CANARY-TRIPPED"
    assert all(t["ok"] for t in second["self_test"])
    assert _status(pep, LB) == "admitted" and _status(pep, ES) == "admitted"
    assert pep.store.get_incident(inc_id)["status"] == "recovered"
    assert pep.recover(inc_id, "op", "x")["ok"] is False  # already recovered
    assert pep.submit(env["lb"], [{"name": "read_kpi"}]).decision == "allow"


def test_control_failure_is_caught_after_it_runs_and_needs_a_stronger_policy(env):
    pep, twin = env["pep"], env["twin"]
    cell = _low_cell(pep)
    sibling = next(c for c in sorted(pep.cells) if c != cell and c.split("_")[1] == cell.split("_")[1])
    twin.base_load[cell], twin.base_load[sibling] = 60, 200  # switching `cell` off pushes its sibling to 260 users
    real_shadow = twin.shadow
    twin.shadow = lambda actions, tick=None: {**real_shadow([], tick), "seconds": 0.01}  # a prediction that misses it
    o = pep.submit(env["es"], [off(cell)], "light load", session="es-f")
    assert o.decision == "allow" and o.executed
    failure = [r for r in pep.store.decisions_in_session("es-f") if r["decision"] == "incident"]
    assert failure and failure[0]["policy_id"] == "IR-CONTROL-FAILURE" and failure[0]["incident"]["severity"] == "S1"
    assert failure[0]["guards_broken"][0]["owner"] == LB
    assert _status(pep, ES) == "suspended"
    inc_id = failure[0]["incident"]["incident_id"]
    refused = pep.recover(inc_id, "op", "x")
    assert not refused["ok"] and refused["replay"]["decision"] == "allow"  # today's policy would allow it again
    pep.policy.write_policy_text("scope", pep.policy.policy_text("scope") +
                                 f'\n@id("IR-FIX-1")\nforbid (principal == Rapp::"{ES}", action == Action::"cell_off", '
                                 f'resource == Cell::"{cell}");\n')
    fixed = pep.recover(inc_id, "op", "fixed by IR-FIX-1")
    assert fixed["ok"] and fixed["replay"]["policy_id"] == "IR-FIX-1"


# ---- model cost cap ----------------------------------------------------------------------------------
def test_daily_model_cost_cap_is_enforced_at_the_gateway(env):
    pep = env["pep"]
    assert pep.submit(env["es"], [{"name": "read_kpi"}], session="es-b").decision == "allow"
    pep.record_trace(ES, _run("es-b", cost=6.0))  # card cap: 5 USD per day
    o = pep.submit(env["es"], [{"name": "read_kpi"}], session="es-b2")
    assert o.policy_id == "BUDGET-MODEL-COST" and _rec(pep, o)["incident"]["severity"] == "S3"


# ---- evidence package ----------------------------------------------------------------------------------
def test_evidence_receipt_for_an_allowed_session_is_signed_and_complete(env):
    pep = env["pep"]
    pep.submit(env["es"], [{"name": "read_kpi"}], session="es-e")
    pep.submit(env["es"], [off(_low_cell(pep))], "low load", session="es-e")
    pep.record_trace(ES, _run("es-e"))
    pkg = evidence.build(pep, session="es-e")
    assert pkg["kind"] == "receipt" and pkg["summary"]["changes_executed"] == 1
    assert pkg["transcript"]["matches_ledger"] and pkg["integrity"]["chain_ok"] and pkg["integrity"]["included_match_chain"]
    assert pkg["admission"] and pkg["configuration"]["policy_unchanged"]
    assert [r["decision"] for r in pkg["rejected_calls"]] == ["self-check"]
    assert pkg["after_action"] is None
    pub = pep.ledger.signer.public_key_pem()
    assert evidence.verify_package(pkg, pub)[0]
    forged = json.loads(json.dumps(pkg))
    forged["summary"]["changes_executed"] = 0
    assert not evidence.verify_package(forged, pub)[0]
    html = evidence.render_html(pkg)
    assert "Decision receipt" in html and "Agent transcript" in html


def test_evidence_for_an_incident_has_after_action_and_recovery(env):
    pep = env["pep"]
    rec = _rec(pep, pep.submit(env["lb"], [CANARY], "reset", session="lb-e"))
    pep.recover(rec["incident"]["incident_id"], "op", "residual: low", "keep the canary")
    pkg = evidence.build(pep, session="lb-e")
    assert pkg["kind"] == "incident" and pkg["after_action"]["severity"] == "S1"
    assert pkg["recovery"] and pkg["recovery"][0]["decision"] == "recover" and pkg["containment"]
    present = {p["practice"] for p in pkg["practices"] if p["present"]}
    assert {"1.7", "3.5", "5.3", "7.3", "7.4", "7.5", "7.6"} <= present
    assert "Incident report" in evidence.render_html(pkg)
    by_incident = evidence.build(pep, incident_id=rec["incident"]["incident_id"])
    assert by_incident["session"] == "lb-e"
