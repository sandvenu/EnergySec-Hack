"""Evidence package: everything needed to reconstruct and check one agent session or one incident.

For every session, allowed or not, the package holds:
  timeline        every ledger record of the session, each with the checks it passed or failed
  transcript      the agent's tool calls and final summary, checked against the hash in its ledger record
  rejected calls  denials, holds, rejections, and proposals the agent's own self-check never sent
  changes         what actually ran on the network, with the predicted KPIs before and after
  configuration   the policy-set hash every decision was made under, the card's hash, the model
  integrity       the full chain verified, the hashes of the included records, the signing key
  incidents       severity, containment and recovery records, and an after-action report
  practices       which NIST CAISSI/NSA/CISA key practices each part evidences
The package is hashed and signed with the ledger's key, so it can be checked offline:
    python verify_ledger.py --evidence package.json
NIST key practices 5.1-5.3 (evidence), 7.5-7.6 (recovery decision, after-action report), 8.2 and 10.2
(timelines and transcripts for affected parties and third-party review).
"""

import base64
import html
import json
from datetime import datetime, timezone
from typing import List, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

from framework import incidents
from framework.ledger import verify_records

MUTATING = {"set_tilt", "cell_off", "cell_on"}
REJECTED = {"deny", "reject", "recover_refused"}
KPIS = ("max_cell_load", "excess_ues", "weak_coverage_pct", "energy_saving_pct")


def _acts(actions) -> str:
    out = []
    for a in actions or []:
        if a.get("cell_id"):
            out.append(f"{a['name']}:{a['cell_id']}" + (f"→{a['tilt']}°" if a.get("tilt") is not None else ""))
        else:
            out.append(a.get("name", "?") + (f":{a['site_id']}" if a.get("site_id") else ""))
    return ", ".join(out)


def _row(r: dict) -> dict:
    a = r.get("assessment") or {}
    return {"seq": r["seq"], "time": r["time"], "rapp_id": r.get("rapp_id"), "decision": r.get("decision"),
            "policy_id": r.get("policy_id"), "actions": _acts(r.get("actions")), "tier": r.get("tier"),
            "class": a.get("class"), "severity": (r.get("incident") or {}).get("severity"),
            "reason": r.get("reason"), "justification": r.get("justification"), "approver": r.get("approver"),
            "pep_ms": r.get("pep_ms"), "hash": r.get("hash"),
            "checks": [{"check": c.get("check"), "result": c.get("result"), "policy_ids": c.get("policy_ids") or []}
                       for c in r.get("checks") or []]}


def _predicted(r: dict) -> Optional[dict]:
    d = next((c.get("detail") for c in r.get("checks") or [] if c.get("check") == "impact_prediction"), None)
    return {k: {"before": v[0], "after": v[1]} for k, v in d.items()} if d else None


def _records(pep, session: Optional[str], incident_id: Optional[str]) -> List[dict]:
    if session:
        return pep.store.decisions_in_session(session)
    inc = pep.store.get_incident(incident_id)
    every = pep.store.decisions(limit=100000)
    return [r for r in every if r["seq"] == inc["seq"] or r.get("incident_id") == incident_id
            or (r.get("incident") or {}).get("incident_id") == incident_id]


def build(pep, session: Optional[str] = None, incident_id: Optional[str] = None) -> dict:
    """The package for one session (or, if the incident had no session, one incident)."""
    inc = pep.store.get_incident(incident_id) if incident_id else None
    if incident_id and inc is None:
        raise KeyError(f"unknown incident {incident_id}")
    session = session or (inc or {}).get("session")
    if not session and not inc:
        raise KeyError("give a session or an incident_id")
    recs = _records(pep, session, incident_id if not session else None)
    if not recs:
        raise KeyError(f"no ledger records for session {session}")
    rapp_id = next((r.get("rapp_id") for r in recs if r.get("rapp_id")), None)
    rapp = pep.store.get_rapp(rapp_id) if rapp_id else None
    first = recs[0]["seq"]
    admission = next((r for r in reversed(pep.store.decisions(rapp_id=rapp_id, limit=100000))
                      if r.get("decision") == "admit" and r["seq"] < first), None) if rapp_id else None

    trace_rec = next((r for r in recs if r.get("decision") == "trace"), None)
    trace = pep.store.get_trace(session) if session else None
    transcript = None
    if trace:
        sha = incidents.sha256_json(trace["body"])
        transcript = {**trace["body"], "sha256": sha, "ledger_seq": trace_rec["seq"] if trace_rec else None,
                      "matches_ledger": bool(trace_rec and trace_rec.get("transcript_sha256") == sha)}

    settled = {r.get("request_id") for r in recs if r.get("decision") in ("approve", "reject") and r.get("request_id")}
    rejected = [_row(r) for r in recs if r.get("decision") in REJECTED or
                (r.get("decision") == "hold" and r.get("request_id") not in settled)]  # still waiting
    for b in (transcript or {}).get("self_check_blocked") or []:
        rejected.append({"seq": None, "decision": "self-check", "policy_id": None, "actions": _acts(
            [{"name": b.get("tool"), "cell_id": c} for c in (b.get("input") or {}).get("cell_ids") or []]
            or [{"name": b.get("tool")}]), "reason": b.get("reason"), "checks": []})

    changes = []
    for r in recs:
        if r.get("decision") in ("allow", "approve") and any(a.get("name") in MUTATING for a in r.get("actions") or []):
            src = r
            if r.get("decision") == "approve":  # the prediction is on the held request
                src = next((x for x in recs if x.get("request_id") == r.get("request_id") and x.get("decision") == "hold"), r)
            changes.append({"seq": r["seq"], "decision": r["decision"], "actions": _acts(r.get("actions")),
                            "approver": r.get("approver"), "predicted": _predicted(src) or (
                                {k: {"before": src["shadow_kpi"]["before"].get(k), "after": src["shadow_kpi"]["after"].get(k)}
                                 for k in KPIS} if src.get("shadow_kpi") else None)})

    found = [r["incident"] for r in recs if r.get("incident")]
    inc_ids = sorted({i["incident_id"] for i in found} | ({incident_id} if incident_id else set()))
    inc_rows = [x for x in (pep.store.get_incident(i) for i in inc_ids) if x]
    contain = [_row(r) | {"contained": r.get("contained"), "responses": r.get("responses")}
               for r in recs if r.get("decision") == "contain"]
    recovery = [{**_row(r), "self_test": r.get("self_test"), "replay": r.get("replay"),
                 "residual_risk": r.get("residual_risk"), "lessons": r.get("lessons"), "resumed": r.get("resumed")}
                for r in recs if r.get("decision") in ("recover", "recover_refused")]

    all_recs = pep.ledger.store.read_all()
    pub = pep.ledger.signer.public_key_pem()
    chain_ok, lines = verify_records(all_recs, pub)
    by_seq = {r["seq"]: r for r in all_recs}
    included_ok = all(by_seq.get(r["seq"], {}).get("hash") == r.get("hash") for r in recs)
    policy_shas = sorted({r.get("policy_sha") for r in recs if r.get("policy_sha")})
    model = (transcript or {}).get("model") or (trace_rec or {}).get("model")
    usage = (transcript or {}).get("usage") or (trace_rec or {}).get("usage")
    cap = ((rapp or {}).get("card", {}).get("model_budget") or {})
    worst = min((i["severity"] for i in found), default=None)
    decisions = {}
    for r in recs:
        decisions[r.get("decision")] = decisions.get(r.get("decision"), 0) + 1

    pkg = {
        "type": "trusted-rapp-evidence", "version": 1,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": "incident" if any(i["kind"] == "incident" for i in found) else ("near_miss" if found else "receipt"),
        "session": session, "incident_id": incident_id, "rapp_id": rapp_id,
        "summary": {"records": len(recs), "first_seq": first, "last_seq": recs[-1]["seq"],
                    "start": recs[0]["time"], "end": recs[-1]["time"], "decisions": decisions,
                    "changes_executed": len(changes), "rejected": len(rejected), "worst_severity": worst,
                    "intent": (transcript or {}).get("intent"),
                    "agent_summary": (transcript or {}).get("summary")},
        "admission": {"seq": admission["seq"], "time": admission["time"], "scope": admission.get("scope"),
                      "refused": admission.get("refused")} if admission else None,
        "timeline": [_row(r) for r in recs],
        "transcript": transcript,
        "rejected_calls": rejected,
        "changes": changes,
        "incidents": inc_rows, "classifications": found, "containment": contain, "recovery": recovery,
        "configuration": {"policy_sha": policy_shas, "policy_unchanged": len(policy_shas) <= 1,
                          "card_sha256": incidents.sha256_json(rapp["card"]) if rapp else None,
                          "vendor": (rapp or {}).get("card", {}).get("vendor"),
                          "model": model, "model_registry": (rapp or {}).get("card", {}).get("model"),
                          "modes": sorted({json.dumps(r.get("mode"), sort_keys=True) for r in recs if r.get("mode")})},
        "cost": {"usage": usage, "cap_usd_per_day": cap.get("max_usd_per_day"),
                 "max_tokens_per_run": cap.get("max_tokens_per_run"),
                 "spent_today": pep.store.spend(rapp_id, datetime.now(timezone.utc).strftime("%Y-%m-%d")) if rapp_id else None},
        "integrity": {"chain_ok": chain_ok, "chain_summary": lines[-1], "included_match_chain": included_ok,
                      "key_id": pep.ledger.signer.key_id, "public_key_pem": pub.decode(),
                      "records": [{"seq": r["seq"], "hash": r.get("hash")} for r in recs]},
    }
    pkg["practices"] = _practices(pkg)
    pkg["after_action"] = _after_action(pkg) if found else None
    return sign(pkg, pep.ledger.signer)


def sign(pkg: dict, signer) -> dict:
    body = {k: v for k, v in pkg.items() if k not in ("package_sha256", "signature")}
    sha = incidents.sha256_json(body)
    return {**body, "package_sha256": sha, "signature": base64.b64encode(signer.sign_digest(bytes.fromhex(sha))).decode()}


def verify_package(pkg: dict, public_key_pem: bytes) -> tuple:
    """(ok, message): the package's content hash and its signature by the ledger key."""
    body = {k: v for k, v in pkg.items() if k not in ("package_sha256", "signature")}
    sha = incidents.sha256_json(body)
    if sha != pkg.get("package_sha256"):
        return False, "FAIL: package content was modified (hash mismatch)"
    try:
        serialization.load_pem_public_key(public_key_pem).verify(
            base64.b64decode(pkg.get("signature", "")), bytes.fromhex(sha), ec.ECDSA(Prehashed(hashes.SHA256())))
    except (InvalidSignature, ValueError):
        return False, "FAIL: signature invalid"
    return True, f"OK: evidence package {sha[:16]}... signed by the ledger key ({len(pkg["timeline"])} records)"


def _practices(p: dict) -> List[dict]:
    t, tr = p["timeline"], p["transcript"]
    checks = {c["check"] for r in t for c in r["checks"]}
    rows = [
        ("1.1", "Least privilege", "admission scope and per-call scope checks", bool(p["admission"]) or "scope" in checks),
        ("1.5", "Monitored gateway as the only route", "every call identified at the gateway", "identity" in checks),
        ("1.6", "Control policy with automated enforcement", "Cedar policy IDs on every decision; incident response policy",
         any(r["policy_id"] for r in t)),
        ("1.7", "Block, contain or terminate", "containment records", bool(p["containment"])),
        ("3.1", "Intended scope baseline", "agent intent and guards evaluated", "impact_assessment" in checks),
        ("3.2", "Monitor all layers", "pre-call prediction, post-action monitor, agent transcript",
         "impact_prediction" in checks or bool(tr)),
        ("3.5", "Escalate to humans; canaries", "holds and approvals; canary check",
         any(r["decision"] in ("hold", "approve", "reject") for r in t) or "canary" in checks),
        ("5.1", "Preserve rejected actions and tool calls", "rejected calls section", True),
        ("5.2", "Preserve configuration", "policy-set hash in every record, card hash, model",
         bool(p["configuration"]["policy_sha"])),
        ("5.3", "Tamper-resistant logs to reconstruct trajectories", "signed hash chain; transcript hash in the ledger",
         p["integrity"]["chain_ok"] and (tr is None or tr["matches_ledger"])),
        ("7.3", "Verify containment before resuming", "recovery self-test", any(x.get("self_test") for x in p["recovery"])),
        ("7.4", "Replay the incident against new controls", "recovery replay", any(x.get("replay") for x in p["recovery"])),
        ("7.5", "Document the recovery decision and remaining risk", "signed recovery record",
         any(x.get("residual_risk") for x in p["recovery"])),
        ("7.6", "After-action report", "this package's after-action section", bool(p["classifications"])),
        ("8.2/10.2", "Timelines and transcripts for affected parties and reviewers", "this package, signed",
         True),
    ]
    return [{"practice": a, "name": b, "evidence": c, "present": bool(d)} for a, b, c, d in rows]


def _after_action(p: dict) -> dict:
    """Practice 7.6 / 9.3: what happened, what the agent did, which control caught it, what was done."""
    cls = sorted(p["classifications"], key=lambda c: c["severity"])[0]
    trigger = next((r for r in p["timeline"] if r["severity"] == cls["severity"]), p["timeline"][0])
    rec = p["recovery"][-1] if p["recovery"] else None
    return {
        "incident_id": cls["incident_id"], "severity": cls["severity"], "kind": cls["kind"].replace("_", "-"),
        "what_happened": f"{p['rapp_id']} {cls['title']} (ledger #{trigger['seq']}: {trigger['actions'] or 'no action'}).",
        "agent_actions": [f"#{r['seq']} {r['decision']} {r['actions']}" for r in p["timeline"] if r["actions"]],
        "detected_by": f"{trigger['policy_id'] or 'post-action monitor'} — practice {cls['nist']}",
        "impact": "None on the network: the request was refused before it ran." if trigger["decision"] in ("deny", "hold")
                  else "Ran on the twin before the monitor caught it; see changes.",
        "response": [r["reason"] for r in p["containment"]] or ["recorded; no automatic containment at this severity"],
        "recovery": (f"{rec['decision']} by {rec['approver']}: {rec['reason']}" if rec else "no recovery decision yet"),
        "residual_risk": rec["residual_risk"] if rec else None,
        "lessons": rec["lessons"] if rec else None,
        "reporting": "Affected parties: none outside the operator's twin. For a production network: notify affected "
                     "parties with this timeline (8.1-8.2); report to CISA if it is also a cybersecurity incident "
                     "(9.1); share agent-specific details with NIST CAISSI / NSA SISC voluntarily (9.3).",
    }


# ---- printable report -------------------------------------------------------------------------
def render_html(p: dict) -> str:
    e = lambda s: html.escape("" if s is None else str(s))  # noqa: E731
    chip = lambda d: f'<span class="chip c-{e(d)}">{e(d)}</span>' if d else ""  # noqa: E731
    title = {"incident": "Incident report", "near_miss": "Near-miss report", "receipt": "Decision receipt"}[p["kind"]]
    s = p["summary"]
    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)} {e(p['session'] or p['incident_id'])}</title>
<style>
:root {{ --bg:#fff; --panel:#f6f8fa; --line:#d0d7de; --text:#1f2328; --muted:#59636e; --accent:#0969da;
  --allow:#1a7f37; --deny:#cf222e; --hold:#9a6700; --info:#8250df; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1419; --panel:#161c23; --line:#2a3440; --text:#e6edf3;
  --muted:#8b98a5; --accent:#4ea1ff; --allow:#3fb950; --deny:#f85149; --hold:#d29922; --info:#a371f7; }} }}
body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.5 system-ui,"Segoe UI",sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:15px; margin:28px 0 8px; border-bottom:1px solid var(--line); padding-bottom:4px; }}
.muted {{ color:var(--muted); }} .nw {{ white-space:nowrap; }} .mono {{ font-family:ui-monospace,Consolas,monospace; font-size:12px; overflow-wrap:anywhere; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:10px; margin-top:12px; }}
.box {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }}
.box .v {{ font-size:20px; font-weight:650; }} .box .l {{ font-size:12px; color:var(--muted); }}
.scroll {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:12.5px; }} th,td {{ text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--muted); font-size:11px; text-transform:uppercase; }}
.chip {{ display:inline-block; padding:0 7px; border-radius:999px; font-size:11px; font-weight:650; border:1px solid currentColor; text-transform:uppercase; }}
.c-allow,.c-admit,.c-approve,.c-recover,.c-ok {{ color:var(--allow); }} .c-deny,.c-reject,.c-kill,.c-recover_refused,.c-S1,.c-S2,.c-fail {{ color:var(--deny); }}
.c-hold,.c-contain,.c-S3 {{ color:var(--hold); }} .c-trace,.c-screen,.c-S4,.c-self-check {{ color:var(--info); }}
.call {{ border-left:3px solid var(--line); padding:4px 10px; margin:6px 0; }}
pre {{ white-space:pre-wrap; margin:4px 0 0; }}
@media print {{ body {{ background:#fff; color:#000; }} }}
</style></head><body><main>
<h1>{e(title)}</h1>
<div class="muted">Agent <b>{e(p['rapp_id'])}</b> · session <span class="mono">{e(p['session'])}</span> · ledger #{s['first_seq']}–#{s['last_seq']} · {e(s['start'])} → {e(s['end'])}</div>
<div class="grid">
<div class="box"><div class="v">{s['records']}</div><div class="l">ledger records</div></div>
<div class="box"><div class="v">{s['changes_executed']}</div><div class="l">changes executed</div></div>
<div class="box"><div class="v">{s['rejected']}</div><div class="l">rejected or held calls</div></div>
<div class="box"><div class="v">{e(s['worst_severity'] or 'none')}</div><div class="l">worst severity</div></div>
<div class="box"><div class="v">{'OK' if p['integrity']['chain_ok'] else 'FAIL'}</div><div class="l">signed chain</div></div>
<div class="box"><div class="v">{('$%.4f' % p['cost']['usage']['est_cost_usd']) if (p['cost']['usage'] or {}).get('est_cost_usd') is not None else '–'}</div><div class="l">model cost of this run</div></div>
</div>"""]
    if s.get("intent"):
        parts.append(f"<h2>Intent and outcome</h2><div><b>Asked:</b> {e(s['intent'])}</div>"
                     f"<div style='margin-top:6px'><b>Agent's summary:</b> {e(s['agent_summary'])}</div>")
    if p["after_action"]:
        a = p["after_action"]
        parts.append(f"<h2>After-action report ({e(a['incident_id'])}, {chip(a['severity'])} {e(a['kind'])})</h2><table>" + "".join(
            f"<tr><th>{e(k)}</th><td>{e(v) if not isinstance(v, list) else '<br>'.join(e(x) for x in v)}</td></tr>"
            for k, v in (("What happened", a["what_happened"]), ("Agent actions", a["agent_actions"]),
                         ("Detected by", a["detected_by"]), ("Impact", a["impact"]), ("Response", a["response"]),
                         ("Recovery", a["recovery"]), ("Residual risk", a["residual_risk"] or "–"),
                         ("Lessons learned", a["lessons"] or "–"), ("Reporting", a["reporting"]))) + "</table>")
    parts.append("<h2>Timeline</h2><div class='scroll'><table><tr><th>#</th><th>Time</th><th>Decision</th><th>Policy</th>"
                 "<th>Actions</th><th>Checks</th><th>Reason</th></tr>" + "".join(
        f"<tr><td class='mono nw'>{r['seq']}</td><td class='mono nw'>{e((r['time'] or '')[11:23])}</td><td>{chip(r['decision'])} "
        f"{chip(r['severity'])} {e(r['class'] or '')}</td><td class='mono nw'>{e(r['policy_id'])}</td><td class='mono'>{e(r['actions'])}</td>"
        f"<td class='mono'>{e(' · '.join(c['check'] + '=' + str(c['result']) for c in r['checks']))}</td>"
        f"<td>{e(r['reason'])}{('<div class=muted>stated: ' + e(r['justification']) + '</div>') if r['justification'] else ''}</td></tr>"
        for r in p["timeline"]) + "</table></div>")
    tr = p["transcript"]
    if tr:
        u = tr.get("usage") or {}
        parts.append(f"<h2>Agent transcript</h2><div class='muted'>model {e((tr.get('model') or {}).get('provider'))}:"
                     f"{e((tr.get('model') or {}).get('model_id'))} · {u.get('model_calls', '–')} model calls · "
                     f"{u.get('total_tokens', '–')} tokens · SHA-256 <span class='mono'>{e(tr['sha256'][:16])}…</span> "
                     f"{'matches' if tr['matches_ledger'] else '<b style=color:var(--deny)>does NOT match</b>'} ledger #{e(tr['ledger_seq'])}</div>")
        for i, c in enumerate(tr.get("tool_calls") or [], 1):
            res = c.get("result")
            brief = res if not isinstance(res, dict) else {k: res[k] for k in ("decision", "policy_id", "reason", "request_id",
                                                                               "error", "max_cell_load", "weak_coverage_pct") if k in res}
            parts.append(f"<div class='call'><b>{i}. {e(c.get('tool'))}</b> <span class='mono'>{e(json.dumps(c.get('input'))[:400])}</span>"
                         f"<pre class='mono muted'>→ {e(json.dumps(brief, default=str)[:600])}</pre></div>")
    parts.append("<h2>Rejected, held and self-blocked calls</h2>" + ("<table><tr><th>#</th><th>Decision</th><th>Policy</th>"
                 "<th>Actions</th><th>Reason</th></tr>" + "".join(
        f"<tr><td class='mono nw'>{e(r['seq'] or '–')}</td><td>{chip(r['decision'])}</td><td class='mono nw'>{e(r['policy_id'])}</td>"
        f"<td class='mono'>{e(r['actions'])}</td><td>{e(r['reason'])}</td></tr>" for r in p["rejected_calls"]) + "</table>"
        if p["rejected_calls"] else "<div class='muted'>None: every call in this session was allowed.</div>"))
    parts.append("<h2>Changes executed on the network</h2>" + ("<table><tr><th>#</th><th>How</th><th>Actions</th>"
                 + "".join(f"<th>{k}</th>" for k in KPIS) + "</tr>" + "".join(
        f"<tr><td class='mono'>{c['seq']}</td><td>{chip(c['decision'])} {e(c['approver'] or '')}</td><td class='mono'>{e(c['actions'])}</td>"
        + "".join(f"<td class='mono'>{e((c['predicted'] or {}).get(k, {}).get('before'))} → <b>{e((c['predicted'] or {}).get(k, {}).get('after'))}</b></td>" for k in KPIS)
        + "</tr>" for c in p["changes"]) + "</table>" if p["changes"] else "<div class='muted'>No change ran.</div>"))
    if p["recovery"]:
        for r in p["recovery"]:
            parts.append(f"<h2>Recovery gate: {chip(r['decision'])} by {e(r['approver'])}</h2><div>{e(r['reason'])}</div>"
                         "<table><tr><th>Self-test</th><th>Practice</th><th>Result</th></tr>" + "".join(
                f"<tr><td>{e(t['test'])}</td><td class='muted'>{e(t['practice'])}</td><td>{chip('ok' if t['ok'] else 'fail')} {e(t['detail'])}</td></tr>"
                for t in r.get("self_test") or []) + "</table>"
                f"<div style='margin-top:6px'><b>Replay of ledger #{e((r.get('replay') or {}).get('replayed_seq'))}</b> under today's policies: "
                f"{chip((r.get('replay') or {}).get('decision'))} <span class='mono'>{e((r.get('replay') or {}).get('policy_id'))}</span> "
                f"{e((r.get('replay') or {}).get('why'))}</div><div><b>Residual risk:</b> {e(r.get('residual_risk'))}</div>"
                f"<div><b>Resumed:</b> {e(', '.join(r.get('resumed') or []) or 'nothing')}</div>")
    c = p["configuration"]
    parts.append(f"<h2>Configuration snapshot</h2><table>"
                 f"<tr><th>Policy set SHA-256</th><td class='mono'>{e(', '.join(c['policy_sha']))} "
                 f"({'unchanged for the whole session' if c['policy_unchanged'] else 'CHANGED during the session'})</td></tr>"
                 f"<tr><th>Card SHA-256</th><td class='mono'>{e(c['card_sha256'])}</td></tr>"
                 f"<tr><th>Vendor</th><td>{e(c['vendor'])}</td></tr>"
                 f"<tr><th>Agent model</th><td class='mono'>{e(json.dumps(c['model']))}</td></tr>"
                 f"<tr><th>Model registry</th><td class='mono'>{e(json.dumps(c['model_registry']))}</td></tr>"
                 f"<tr><th>Framework modes</th><td class='mono'>{e(' | '.join(c['modes']))}</td></tr>"
                 f"<tr><th>Model cost cap</th><td>{e(p['cost']['cap_usd_per_day'])} USD/day · spent today "
                 f"{e((p['cost']['spent_today'] or {}).get('usd'))} USD · max {e(p['cost']['max_tokens_per_run'])} tokens/run</td></tr></table>")
    i = p["integrity"]
    parts.append(f"<h2>Integrity</h2><div>{chip('ok' if i['chain_ok'] else 'fail')} {e(i['chain_summary'])} · included records "
                 f"{'match' if i['included_match_chain'] else 'DO NOT match'} the chain · key <span class='mono'>{e(i['key_id'])}</span></div>"
                 f"<div class='mono muted' style='margin-top:6px'>package SHA-256 {e(p['package_sha256'])}<br>signature {e(p['signature'][:48])}…</div>"
                 f"<div class='muted'>Check offline: <span class='mono'>python verify_ledger.py --evidence package.json</span></div>")
    parts.append("<h2>NIST key practices evidenced</h2><div class='muted'>NIST CAISSI / NSA SISC / CISA, Key Practices for Incident "
                 "Readiness and Response (Oct 2026)</div><table><tr><th>Practice</th><th>What</th><th>Evidence here</th><th></th></tr>" + "".join(
        f"<tr><td class='mono'>{e(x['practice'])}</td><td>{e(x['name'])}</td><td class='muted'>{e(x['evidence'])}</td>"
        f"<td>{chip('ok' if x['present'] else '') if x['present'] else '<span class=muted>not in this session</span>'}</td></tr>"
        for x in p["practices"]) + "</table>")
    parts.append("</main></body></html>")
    return "\n".join(parts)
