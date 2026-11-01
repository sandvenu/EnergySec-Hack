"""SMO-boundary gateway: the rApp API (bearer token per rApp) and the operator API for the demo page.

Standalone stand-in for AgentCore Gateway + PEP Lambda (rApp side) and API Gateway + Lambda
(operator side). The operator API only answers on localhost here; on AWS it sits behind Cognito.

    uvicorn framework.gateway:app --port 8700     (normally started by run_demo.py)
"""

import hmac
import logging
import os
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from framework import mcp_gateway
from framework.ledger import FileLedgerStore, Ledger, LocalSigner, read_records, tamper_copy, verify_records
from framework.pep import EnforcementPoint, Settings, TrustedCaller
from framework.policy_engine import make_engine
from framework.screening import HeuristicScreener
from framework.store import Store
from framework.twin_client import TwinClient, TwinUnavailable

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("gateway")

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.environ.get("RUNTIME_DIR", ROOT / "runtime"))
POLICY_DIR = Path(os.environ.get("POLICY_DIR", ROOT / "policies"))
LEDGER_PATH = RUNTIME / "ledger" / "chain.jsonl"
PUBKEY_PATH = RUNTIME / "ledger" / "ledger_public.pem"

_events: deque = deque(maxlen=500)
_event_seq = 0
_event_lock = threading.Lock()


def _notify(event: dict) -> None:
    global _event_seq
    with _event_lock:
        _event_seq += 1
        _events.append({"id": _event_seq, "time": time.time(), **event})


def build_pep() -> EnforcementPoint:
    """Standalone back ends by default; AWS ones when their environment variables are set (framework/aws_adapters.py)."""
    RUNTIME.mkdir(parents=True, exist_ok=True)
    notify = _notify
    if os.environ.get("LEDGER_KMS_KEY_ID") or os.environ.get("LEDGER_BUCKET") or os.environ.get("KINESIS_STREAM"):
        from framework import aws_adapters as aws
    if os.environ.get("LEDGER_KMS_KEY_ID"):
        signer = aws.KmsSigner(os.environ["LEDGER_KMS_KEY_ID"], PUBKEY_PATH)
    else:
        signer = LocalSigner(RUNTIME / "keys" / "ledger_signing_key.pem", PUBKEY_PATH)
    store_ = FileLedgerStore(LEDGER_PATH)
    if os.environ.get("LEDGER_BUCKET"):
        store_ = aws.S3LedgerStore(os.environ["LEDGER_BUCKET"], store_)
    if os.environ.get("KINESIS_STREAM"):
        sink = aws.KinesisSink(os.environ["KINESIS_STREAM"])

        def notify(event: dict) -> None:
            _notify(event)
            sink({"time": time.time(), **event})
    ledger = Ledger(store_, signer)
    store = Store(RUNTIME / "framework.sqlite")
    twin = TwinClient(os.environ.get("TWIN_URL", "http://127.0.0.1:8701"), os.environ.get("TWIN_KEY", ""))
    pep_ = EnforcementPoint(make_engine(POLICY_DIR), ledger, store, twin, HeuristicScreener(), POLICY_DIR,
                            Settings(approval_timeout_s=float(os.environ.get("APPROVAL_TIMEOUT_S", "300"))), notify=notify)
    if os.environ.get("MODEL_REGISTRY_CHECK") == "1":
        from framework.aws_adapters import ModelRegistryCheck

        pep_.model_check = ModelRegistryCheck()
    return pep_


pep = build_pep()
mcp_app = mcp_gateway.http_app()


@asynccontextmanager
async def lifespan(_app):
    # The demo hosts the Energy Saving rApp in-process; importing torch takes ~20 s, so do it up front.
    threading.Thread(target=lambda: __import__("stable_baselines3"), daemon=True).start()
    async with mcp_app.router.lifespan_context(mcp_app):  # MCP session manager
        yield


app = FastAPI(title="Trusted rApp Framework - SMO boundary gateway", lifespan=lifespan)
app.mount("/agentcore", mcp_app)  # agents' MCP endpoint: /agentcore/mcp


def bearer(authorization: Optional[str] = Header(default=None)) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def operator_only(request: Request):
    if request.client is None or request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(403, "operator API is local only in the standalone build")


# ======================= rApp API (through the enforcement point) ==========================
class ActionRequest(BaseModel):
    actions: List[dict]
    justification: str = ""
    session: Optional[str] = None  # the rApp's session or trace ID (agents on MCP send X-Agent-Session)


@app.post("/v1/admission")
def admission(card: dict = Body(...)):
    return pep.admit(card)


@app.post("/v1/actions")
def actions(body: ActionRequest, token: Optional[str] = Depends(bearer)):
    return pep.submit(token, body.actions, body.justification, session=body.session).as_dict()


class GatewayToolCall(BaseModel):
    rapp_id: str
    client_id: str = ""
    session: Optional[str] = None
    arguments: dict = {}


@app.post("/v1/gateway/tools/{name}")
def gateway_tool(name: str, body: GatewayToolCall, x_gateway_secret: Optional[str] = Header(default=None)):
    """AgentCore Gateway path: Lambda target -> here. The REQUEST interceptor has already mapped the agent's
    Cognito identity to an rApp; this endpoint trusts that mapping only with the shared secret the CDK
    stack generated (Secrets Manager), and is off unless GATEWAY_SHARED_SECRET is set."""
    secret = os.environ.get("GATEWAY_SHARED_SECRET")
    if not secret:
        raise HTTPException(404, "not enabled")
    if not x_gateway_secret or not hmac.compare_digest(x_gateway_secret, secret):
        raise HTTPException(403, "bad gateway secret")
    caller = TrustedCaller(rapp_id=body.rapp_id, client_id=body.client_id)
    return mcp_gateway.call_tool(name, caller, body.session, body.arguments)


@app.post("/v1/what_if")
def rapp_what_if(body: dict = Body(...), token: Optional[str] = Depends(bearer)):
    """Read-only twin prediction for proposed changes (same as the MCP what_if tool)."""
    return pep.what_if(token, body.get("changes") or [])


@app.get("/v1/kpis")
def rapp_kpis(token: Optional[str] = Depends(bearer)):
    return pep.submit(token, [{"name": "read_kpi"}]).as_dict()


@app.get("/v1/inputs/alarms")
def rapp_alarms(token: Optional[str] = Depends(bearer)):
    try:
        return pep.read_inputs(token)
    except Exception as e:  # unknown or revoked credential
        raise HTTPException(403, getattr(e, "reason", str(e)))


@app.get("/v1/scope")
def rapp_scope(token: Optional[str] = Depends(bearer)):
    """An rApp may read the scope admission granted it (so an agent can plan within it)."""
    rapp = pep.store.rapp_for_token(token)
    if rapp is None or rapp["status"] != "admitted":
        raise HTTPException(403, "unknown or revoked credential")
    return {"rapp_id": rapp["rapp_id"], "scope": {**rapp["scope"], "cell_criticality": pep.cells,
                                                  "model_budget": rapp["card"].get("model_budget")}}


@app.get("/v1/requests/{request_id}")
def rapp_request(request_id: str, token: Optional[str] = Depends(bearer)):
    rapp = pep.store.rapp_for_token(token)
    ap = pep.store.get_approval(request_id)
    if rapp is None or ap is None or ap["rapp_id"] != rapp["rapp_id"]:
        raise HTTPException(404, "unknown request")
    return {"request_id": request_id, "status": ap["status"]}


# ======================= operator API (demo page) ===========================================
ops = Depends(operator_only)


@app.get("/ops/overview", dependencies=[ops])
def overview():
    try:
        topology = pep.twin.topology()
        twin_ok = True
    except TwinUnavailable:
        topology, twin_ok = [], False
    return {"settings": pep.settings.__dict__, "rapps": pep.store.list_rapps(), "cells": pep.cells,
            "topology": topology, "twin_ok": twin_ok, "policies": pep.policy.list_policies(),
            "faults": _faults(), "ledger_seq": pep.ledger.seq, "incidents": _incident_counts(),
            "weak_coverage_dbm": float(os.environ.get("TWIN_WEAK_DBM", "-135"))}


@app.get("/ops/map", dependencies=[ops])
def ops_map():
    try:
        return pep.twin.map_view()
    except TwinUnavailable as e:
        raise HTTPException(503, str(e))


@app.get("/ops/state", dependencies=[ops])
def ops_state():
    return pep.twin.state()


@app.get("/ops/blast-radius", dependencies=[ops])
def ops_blast_radius():
    return pep.blast_radius()


@app.get("/ops/decisions", dependencies=[ops])
def ops_decisions(after_seq: int = 0, rapp_id: Optional[str] = None, cell_id: Optional[str] = None, limit: int = 200):
    t0 = time.perf_counter()
    rows = pep.store.decisions(rapp_id, cell_id, after_seq, limit)
    return {"decisions": rows, "query_ms": round((time.perf_counter() - t0) * 1000, 2)}


@app.get("/ops/events", dependencies=[ops])
def ops_events(after: int = 0):
    with _event_lock:
        return {"events": [e for e in _events if e["id"] > after], "last": _event_seq}


@app.get("/ops/approvals", dependencies=[ops])
def ops_approvals(status: Optional[str] = None):
    return {"approvals": pep.store.list_approvals(status)}


class Decision(BaseModel):
    approve: bool
    approver: str = "operator"
    reason: str = ""


@app.post("/ops/approvals/{request_id}", dependencies=[ops])
def ops_decide(request_id: str, body: Decision):
    return pep.decide(request_id, body.approve, body.approver, body.reason)


@app.post("/ops/kill/{rapp_id}", dependencies=[ops])
def ops_kill(rapp_id: str, operator: str = "operator"):
    return pep.kill(rapp_id, operator)


def _incident_counts() -> dict:
    rows = pep.store.list_incidents(1000)
    out = {"incidents": sum(r["kind"] == "incident" for r in rows), "near_misses": sum(r["kind"] == "near_miss" for r in rows),
           "contained": sum(r["status"] == "contained" for r in rows), "recovered": sum(r["status"] == "recovered" for r in rows)}
    for s in ("S1", "S2", "S3", "S4"):
        out[s] = sum(r["severity"] == s for r in rows)
    return out


@app.get("/ops/incidents", dependencies=[ops])
def ops_incidents(limit: int = 50):
    return {"incidents": pep.store.list_incidents(limit), "counts": _incident_counts(),
            "response_policy": pep.response_policy}


@app.get("/ops/sessions", dependencies=[ops])
def ops_sessions(limit: int = 30):
    return {"sessions": pep.store.sessions(limit)}


class Recovery(BaseModel):
    operator: str = "operator"
    residual_risk: str
    lessons: str = ""


@app.post("/ops/recover/{incident_id}", dependencies=[ops])
def ops_recover(incident_id: str, body: Recovery):
    return pep.recover(incident_id, body.operator, body.residual_risk, body.lessons)


@app.get("/ops/self-test", dependencies=[ops])
def ops_self_test():
    return {"tests": pep.self_test([])}


def _evidence(session: Optional[str], incident_id: Optional[str]) -> dict:
    from framework import evidence

    try:
        return evidence.build(pep, session=session, incident_id=incident_id)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'"))


@app.get("/ops/evidence", dependencies=[ops])
def ops_evidence(session: Optional[str] = None, incident_id: Optional[str] = None, download: bool = False):
    """Signed evidence package for one session or incident (JSON). Check it offline with verify_ledger.py --evidence."""
    pkg = _evidence(session, incident_id)
    if download:
        name = f"evidence_{pkg['session'] or pkg['incident_id']}.json"
        return JSONResponse(pkg, headers={"Content-Disposition": f'attachment; filename="{name}"'})
    return pkg


@app.get("/ops/evidence/report", dependencies=[ops])
def ops_evidence_report(session: Optional[str] = None, incident_id: Optional[str] = None):
    """The same package as a readable, printable report (receipt, near-miss or incident report)."""
    from framework import evidence

    return HTMLResponse(evidence.render_html(_evidence(session, incident_id)))


@app.post("/ops/settings", dependencies=[ops])
def ops_settings(body: dict = Body(...)):
    for k in ("enforcement", "screening", "assessment", "containment"):
        if k in body:
            setattr(pep.settings, k, bool(body[k]))
    if "approval_timeout_s" in body:
        pep.settings.approval_timeout_s = float(body["approval_timeout_s"])
    _notify({"type": "settings", "settings": pep.settings.__dict__})
    return pep.settings.__dict__


def _faults() -> dict:
    return {"policy": pep.policy.fault, "ledger": pep.ledger.store.fault, "twin": pep.twin.fault}


@app.post("/ops/faults", dependencies=[ops])
def ops_faults(body: dict = Body(...)):
    """NF-1 test hook: make a dependency unavailable to prove the PEP fails closed."""
    if "policy" in body:
        pep.policy.fault = bool(body["policy"])
    if "ledger" in body:
        pep.ledger.store.fault = bool(body["ledger"])
    if "twin" in body:
        pep.twin.fault = bool(body["twin"])
    return _faults()


@app.post("/ops/alarms", dependencies=[ops])
def ops_add_alarm(body: dict = Body(...)):
    return {"alarm_id": pep.store.add_alarm(body["text"], body.get("source", "oss-feed"))}


@app.delete("/ops/alarms", dependencies=[ops])
def ops_clear_alarms():
    pep.store.clear_alarms()
    return {"ok": True}


@app.get("/ops/alarms", dependencies=[ops])
def ops_alarms():
    return {"alarms": pep.store.alarms(), "screening_events": pep.screen_events[-20:]}


@app.post("/ops/event", dependencies=[ops])
def ops_add_event(body: dict = Body(...)):
    """A planned event from the operator's event and capacity calendar (e.g. a stadium concert)."""
    ev = pep.twin.add_event(**body)
    _notify({"type": "event", "event": ev})
    return ev


@app.delete("/ops/event", dependencies=[ops])
def ops_clear_event():
    pep.twin.clear_event()
    _notify({"type": "event", "event": None})
    return {"ok": True}


@app.get("/ops/kpis", dependencies=[ops])
def ops_kpis():
    return pep.twin.kpis()


@app.post("/ops/tick", dependencies=[ops])
def ops_tick(body: dict = Body(...)):
    state = pep.twin.set_tick(int(body["tick"]))
    _notify({"type": "tick", "tick": state["tick"]})
    return state


@app.post("/ops/reset", dependencies=[ops])
def ops_reset(body: dict = Body(default={})):
    """Resets the twin (and its planned event, unless keep_event) and per-run state. The ledger is never reset."""
    pep.store.reset_runtime()
    if body.get("registrations"):
        pep.store.clear_registrations()
    pep.overflow.clear()
    pep.screen_events.clear()
    if not body.get("keep_event"):
        pep.twin.clear_event()
    state = pep.twin.reset()
    if "tick" in body:
        state = pep.twin.set_tick(int(body["tick"]))
    _notify({"type": "reset", "state": state})
    return state


@app.get("/ops/ledger/verify", dependencies=[ops])
def ops_verify():
    t0 = time.perf_counter()
    ok, lines = verify_records(read_records(LEDGER_PATH), PUBKEY_PATH.read_bytes())
    return {"ok": ok, "lines": lines[-12:], "records": pep.ledger.seq, "ms": round((time.perf_counter() - t0) * 1000, 1)}


@app.post("/ops/ledger/tamper-test", dependencies=[ops])
def ops_tamper():
    copy_path = RUNTIME / "ledger" / "tampered_copy.jsonl"
    seq = tamper_copy(LEDGER_PATH, copy_path)
    ok, lines = verify_records(read_records(copy_path), PUBKEY_PATH.read_bytes())
    return {"ok": ok, "tampered_seq": seq, "copy": str(copy_path), "lines": [l for l in lines if l.startswith("FAIL")][:5]}


@app.get("/ops/ledger/public-key", dependencies=[ops])
def ops_pubkey():
    return PlainTextResponse(PUBKEY_PATH.read_text())


@app.get("/ops/ledger/download", dependencies=[ops])
def ops_ledger_download():
    return FileResponse(LEDGER_PATH, filename="chain.jsonl")


@app.get("/ops/policies/{name}", dependencies=[ops])
def ops_get_policy(name: str):
    if name not in ("scope", "tier"):
        raise HTTPException(404)
    return PlainTextResponse(pep.policy.policy_text(name))


@app.put("/ops/policies/{name}", dependencies=[ops])
def ops_put_policy(name: str, body: str = Body(..., media_type="text/plain")):
    if name not in ("scope", "tier"):
        raise HTTPException(404)
    try:
        pep.policy.write_policy_text(name, body)
    except Exception as e:
        raise HTTPException(400, f"policy rejected: {e}")
    _notify({"type": "policy", "set": name})
    return {"ok": True, "policies": pep.policy.list_policies()}


@app.post("/ops/demo/{step}", dependencies=[ops])
def ops_demo(step: str, body: dict = Body(default={})):
    from demo import steps

    fn = steps.STEPS.get(step)
    if fn is None:
        raise HTTPException(404, f"unknown step {step}")
    return fn(**body)


@app.get("/ops/replay", dependencies=[ops])
def ops_replay(path: Optional[str] = None):
    """NF-7 replay mode: the stored ledger (or a recorded run) as the decision log."""
    src = Path(path) if path else LEDGER_PATH
    recs = read_records(src)
    ok, lines = verify_records(recs, PUBKEY_PATH.read_bytes())
    return {"records": recs, "verified": ok, "summary": lines[-1]}


STATIC = ROOT / "demo_page"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
