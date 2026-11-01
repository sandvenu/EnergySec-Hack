"""Twin wrapper service: the only way to reach the MAVERIC twin.

Runs as its own process and accepts commands only with the enforcement point's key (EN-1).
On AWS the same app runs on EC2 in a private subnet behind IAM auth.

    TWIN_KEY=... uvicorn twin.service:app --port 8701
"""

import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from twin.engine import ActionError, TwinEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s twin: %(message)s")
logger = logging.getLogger("twin.service")

TWIN_KEY = os.environ.get("TWIN_KEY", "")
_engine: Optional[TwinEngine] = None


@asynccontextmanager
async def lifespan(_app):
    t0 = time.time()
    engine().kpis()
    logger.info("Twin warm in %.1fs", time.time() - t0)
    yield


app = FastAPI(title="MAVERIC twin wrapper", lifespan=lifespan)


def engine() -> TwinEngine:
    global _engine
    if _engine is None:
        _engine = TwinEngine(test_day=int(os.environ.get("TWIN_TEST_DAY", "3")))
    return _engine


def require_pep(x_twin_key: Optional[str] = Header(default=None), authorization: Optional[str] = Header(default=None)):
    if not TWIN_KEY:
        raise HTTPException(503, "twin key not configured; refusing all commands")
    if not x_twin_key or not hmac.compare_digest(x_twin_key, TWIN_KEY):
        who = "rApp credential" if authorization else "no credential"
        logger.warning("Refused direct call to twin (%s)", who)
        raise HTTPException(403, f"twin accepts commands only from the enforcement point ({who} refused)")


class ActionList(BaseModel):
    actions: List[dict]
    tick: Optional[int] = None


class TickBody(BaseModel):
    tick: int


@app.get("/health")
def health():
    return {"ok": True, "cells": len(engine().cell_ids)}


@app.get("/topology", dependencies=[Depends(require_pep)])
def topology():
    return {"cells": engine().topology()}


@app.get("/state", dependencies=[Depends(require_pep)])
def state():
    return engine().state_dict()


@app.get("/kpis", dependencies=[Depends(require_pep)])
def kpis(tick: Optional[int] = None):
    return engine().kpis(tick).as_dict()


@app.get("/map", dependencies=[Depends(require_pep)])
def map_view():
    return engine().map_view()


@app.post("/shadow", dependencies=[Depends(require_pep)])
def shadow(body: ActionList):
    try:
        return engine().shadow(body.actions, body.tick)
    except ActionError as e:
        raise HTTPException(400, str(e))


@app.post("/apply", dependencies=[Depends(require_pep)])
def apply(body: ActionList):
    try:
        return engine().apply(body.actions)
    except ActionError as e:
        raise HTTPException(400, str(e))


@app.post("/tick", dependencies=[Depends(require_pep)])
def set_tick(body: TickBody):
    try:
        engine().set_tick(body.tick)
    except ActionError as e:
        raise HTTPException(400, str(e))
    return engine().state_dict()


class EventBody(BaseModel):
    name: str
    lon: float
    lat: float
    ues: int
    sigma_deg: float = 0.004
    ticks: List[int]


@app.post("/event", dependencies=[Depends(require_pep)])
def add_event(body: EventBody):
    try:
        return engine().add_event(body.name, body.lon, body.lat, body.ues, body.sigma_deg, body.ticks)
    except ActionError as e:
        raise HTTPException(400, str(e))


@app.delete("/event", dependencies=[Depends(require_pep)])
def clear_event():
    engine().clear_event()
    return {"ok": True}


@app.post("/reset", dependencies=[Depends(require_pep)])
def reset():
    engine().reset()
    return engine().state_dict()
