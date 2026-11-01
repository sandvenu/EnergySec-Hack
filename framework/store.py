"""Framework state in SQLite: registry and tokens, kill switch, budget counters, decision index,
approvals, screening taint, the alarm feed, and incident response (incidents, ended sessions, agent
transcripts, model spend). On AWS these tables become DynamoDB tables.
"""

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS rapps (
  rapp_id TEXT PRIMARY KEY, card TEXT, scope TEXT, token_hash TEXT,
  status TEXT, admitted_at REAL, revoked_at REAL);
CREATE TABLE IF NOT EXISTS budget (
  rapp_id TEXT, tick INTEGER, cell_id TEXT, PRIMARY KEY (rapp_id, tick, cell_id));
CREATE TABLE IF NOT EXISTS decisions (
  seq INTEGER PRIMARY KEY, time TEXT, rapp_id TEXT, decision TEXT, policy_id TEXT,
  tier TEXT, cells TEXT, record TEXT);
CREATE INDEX IF NOT EXISTS decisions_rapp ON decisions (rapp_id, seq);
CREATE TABLE IF NOT EXISTS approvals (
  request_id TEXT PRIMARY KEY, rapp_id TEXT, actions TEXT, shadow TEXT, tick INTEGER,
  status TEXT, created REAL, deadline REAL, decided_by TEXT, decided_at REAL, context TEXT);
CREATE TABLE IF NOT EXISTS taint (
  rapp_id TEXT PRIMARY KEY, score REAL, input_id TEXT, until REAL);
CREATE TABLE IF NOT EXISTS alarms (
  alarm_id TEXT PRIMARY KEY, created REAL, source TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS changes (
  ts REAL, tick INTEGER, rapp_id TEXT, cell_id TEXT, action TEXT, tilt REAL, load_before INTEGER, seq INTEGER);
CREATE TABLE IF NOT EXISTS baselines (
  rapp_id TEXT, kpi TEXT, value REAL, tick INTEGER, ts REAL, PRIMARY KEY (rapp_id, kpi));
CREATE TABLE IF NOT EXISTS denials (
  ts REAL, tick INTEGER, rapp_id TEXT, cell_id TEXT, action TEXT, policy_id TEXT);
CREATE TABLE IF NOT EXISTS incidents (
  incident_id TEXT PRIMARY KEY, seq INTEGER, rapp_id TEXT, session TEXT, severity TEXT, kind TEXT,
  type TEXT, title TEXT, status TEXT, created REAL, responses TEXT, contained TEXT, recovery_seq INTEGER);
CREATE TABLE IF NOT EXISTS ended_sessions (
  session TEXT PRIMARY KEY, rapp_id TEXT, incident_id TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS traces (
  session TEXT PRIMARY KEY, rapp_id TEXT, ts REAL, seq INTEGER, sha256 TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS spend (
  rapp_id TEXT, day TEXT, session TEXT, usd REAL, tokens INTEGER, PRIMARY KEY (rapp_id, day, session));
"""


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        if "session" not in {r[1] for r in self.db.execute("PRAGMA table_info(decisions)")}:  # stores from before v3
            self.db.execute("ALTER TABLE decisions ADD COLUMN session TEXT")
        self.db.execute("CREATE INDEX IF NOT EXISTS decisions_session ON decisions (session, seq)")
        self._lock = threading.Lock()

    def _q(self, sql: str, args=()) -> List[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    # ---- registry ----------------------------------------------------------------------
    def register(self, rapp_id: str, card: dict, scope: dict) -> str:
        token = "rapp_" + secrets.token_urlsafe(24)
        self._q("INSERT OR REPLACE INTO rapps VALUES (?,?,?,?,?,?,NULL)",
                (rapp_id, json.dumps(card), json.dumps(scope), _hash_token(token), "admitted", time.time()))
        return token

    def update_scope(self, rapp_id: str, card: dict, scope: dict) -> None:
        """Re-admission of a registered rApp: new card and scope, same credential and status."""
        self._q("UPDATE rapps SET card=?, scope=? WHERE rapp_id=?", (json.dumps(card), json.dumps(scope), rapp_id))

    def clear_registrations(self) -> None:
        """Operator reset for a fresh demo run: every rApp must be admitted again."""
        self._q("DELETE FROM rapps")

    def rapp_for_token(self, token: Optional[str]) -> Optional[dict]:
        if not token:
            return None
        rows = self._q("SELECT * FROM rapps WHERE token_hash=?", (_hash_token(token),))
        return self._rapp(rows[0]) if rows else None

    def get_rapp(self, rapp_id: str) -> Optional[dict]:
        rows = self._q("SELECT * FROM rapps WHERE rapp_id=?", (rapp_id,))
        return self._rapp(rows[0]) if rows else None

    def list_rapps(self) -> List[dict]:
        return [self._rapp(r) for r in self._q("SELECT * FROM rapps ORDER BY admitted_at")]

    @staticmethod
    def _rapp(r: sqlite3.Row) -> dict:
        return {"rapp_id": r["rapp_id"], "card": json.loads(r["card"]), "scope": json.loads(r["scope"]),
                "status": r["status"], "admitted_at": r["admitted_at"], "revoked_at": r["revoked_at"]}

    def revoke(self, rapp_id: str) -> bool:
        with self._lock:
            cur = self.db.execute("UPDATE rapps SET status='revoked', revoked_at=? WHERE rapp_id=?", (time.time(), rapp_id))
            return cur.rowcount > 0

    def suspend(self, rapp_id: str) -> bool:
        """Incident containment: admitted -> suspended (a revoked rApp stays revoked)."""
        with self._lock:
            cur = self.db.execute("UPDATE rapps SET status='suspended' WHERE rapp_id=? AND status='admitted'", (rapp_id,))
            return cur.rowcount > 0

    def resume(self, rapp_id: str) -> bool:
        """After the recovery gate: suspended -> admitted."""
        with self._lock:
            cur = self.db.execute("UPDATE rapps SET status='admitted' WHERE rapp_id=? AND status='suspended'", (rapp_id,))
            return cur.rowcount > 0

    # ---- blast radius budget -------------------------------------------------------------
    def cells_changed(self, rapp_id: str, tick: int) -> set:
        return {r["cell_id"] for r in self._q("SELECT cell_id FROM budget WHERE rapp_id=? AND tick=?", (rapp_id, tick))}

    def consume_budget(self, rapp_id: str, tick: int, cells: List[str]) -> None:
        with self._lock:
            self.db.executemany("INSERT OR IGNORE INTO budget VALUES (?,?,?)", [(rapp_id, tick, c) for c in cells])

    def reset_budget(self) -> None:
        self._q("DELETE FROM budget")

    # ---- decision index (LG-3) ------------------------------------------------------------
    def index_decision(self, rec: dict) -> None:
        cells = sorted({a.get("cell_id") for a in rec.get("actions") or [] if a.get("cell_id")})
        self._q("INSERT OR REPLACE INTO decisions (seq, time, rapp_id, decision, policy_id, tier, cells, record, session) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (rec["seq"], rec["time"], rec.get("rapp_id"), rec.get("decision"), rec.get("policy_id"),
                 rec.get("tier"), ",".join(cells), json.dumps(rec), rec.get("session")))

    def decisions(self, rapp_id: Optional[str] = None, cell_id: Optional[str] = None,
                  after_seq: int = 0, limit: int = 200) -> List[dict]:
        sql, args = "SELECT record FROM decisions WHERE seq > ?", [after_seq]
        if rapp_id:
            sql += " AND rapp_id = ?"
            args.append(rapp_id)
        if cell_id:
            sql += " AND (',' || cells || ',') LIKE ?"
            args.append(f"%,{cell_id},%")
        sql += " ORDER BY seq DESC LIMIT ?"
        args.append(limit)
        return [json.loads(r["record"]) for r in self._q(sql, args)][::-1]

    # ---- approvals ------------------------------------------------------------------------
    def add_approval(self, request_id: str, rapp_id: str, actions: list, shadow: dict, tick: int,
                     timeout_s: float, context: dict) -> None:
        now = time.time()
        self._q("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?)",
                (request_id, rapp_id, json.dumps(actions), json.dumps(shadow), tick, "pending", now, now + timeout_s,
                 json.dumps(context)))

    def get_approval(self, request_id: str) -> Optional[dict]:
        rows = self._q("SELECT * FROM approvals WHERE request_id=?", (request_id,))
        return self._approval(rows[0]) if rows else None

    def list_approvals(self, status: Optional[str] = None, limit: int = 50) -> List[dict]:
        if status:
            rows = self._q("SELECT * FROM approvals WHERE status=? ORDER BY created DESC LIMIT ?", (status, limit))
        else:
            rows = self._q("SELECT * FROM approvals ORDER BY created DESC LIMIT ?", (limit,))
        return [self._approval(r) for r in rows]

    def settle_approval(self, request_id: str, status: str, by: str) -> bool:
        """Moves pending -> status exactly once (approve/reject/timeout race safely)."""
        with self._lock:
            cur = self.db.execute("UPDATE approvals SET status=?, decided_by=?, decided_at=? WHERE request_id=? AND status='pending'",
                                  (status, by, time.time(), request_id))
            return cur.rowcount == 1

    @staticmethod
    def _approval(r: sqlite3.Row) -> dict:
        return {"request_id": r["request_id"], "rapp_id": r["rapp_id"], "actions": json.loads(r["actions"]),
                "shadow": json.loads(r["shadow"]), "tick": r["tick"], "status": r["status"], "created": r["created"],
                "deadline": r["deadline"], "decided_by": r["decided_by"], "decided_at": r["decided_at"],
                "context": json.loads(r["context"] or "{}")}

    # ---- screening taint -----------------------------------------------------------------
    def set_taint(self, rapp_id: str, score: float, input_id: str, ttl_s: float) -> None:
        self._q("INSERT OR REPLACE INTO taint VALUES (?,?,?,?)", (rapp_id, score, input_id, time.time() + ttl_s))

    def get_taint(self, rapp_id: str) -> Optional[dict]:
        rows = self._q("SELECT * FROM taint WHERE rapp_id=? AND until > ?", (rapp_id, time.time()))
        return dict(rows[0]) if rows else None

    def clear_taint(self) -> None:
        self._q("DELETE FROM taint")

    # ---- alarm feed (untrusted input for the third-party rApp) ------------------------------
    def add_alarm(self, text: str, source: str = "oss-feed") -> str:
        alarm_id = "alm_" + secrets.token_hex(4)
        self._q("INSERT INTO alarms VALUES (?,?,?,?)", (alarm_id, time.time(), source, text))
        return alarm_id

    def alarms(self, limit: int = 20) -> List[dict]:
        return [dict(r) for r in self._q("SELECT * FROM alarms ORDER BY created DESC LIMIT ?", (limit,))]

    def clear_alarms(self) -> None:
        self._q("DELETE FROM alarms")

    # ---- what the impact assessor needs: executed changes, intent baselines, denials ----------
    def log_change(self, tick: int, rapp_id: str, action: dict, load_before: int, seq: int) -> None:
        self._q("INSERT INTO changes VALUES (?,?,?,?,?,?,?,?)",
                (time.time(), tick, rapp_id, action.get("cell_id"), action.get("name"), action.get("tilt"),
                 load_before, seq))

    def changes(self, tick: int) -> List[dict]:
        return [dict(r) for r in self._q("SELECT * FROM changes WHERE tick=? ORDER BY ts", (tick,))]

    def set_baseline(self, rapp_id: str, kpis: dict, tick: int) -> None:
        """The KPI values an intent's owner left the network at; other agents are measured against them."""
        with self._lock:
            self.db.executemany("INSERT OR REPLACE INTO baselines VALUES (?,?,?,?,?)",
                                [(rapp_id, k, float(v), tick, time.time()) for k, v in kpis.items()])

    def baselines(self) -> dict:
        out: dict = {}
        for r in self._q("SELECT * FROM baselines"):
            out.setdefault(r["rapp_id"], {})[r["kpi"]] = {"value": r["value"], "tick": r["tick"]}
        return out

    def log_denial(self, tick: int, rapp_id: str, action: dict, policy_id: str) -> None:
        self._q("INSERT INTO denials VALUES (?,?,?,?,?,?)",
                (time.time(), tick, rapp_id, action.get("cell_id"), action.get("name"), policy_id))

    def denials(self, rapp_id: str, tick: int) -> List[dict]:
        return [dict(r) for r in self._q("SELECT * FROM denials WHERE rapp_id=? AND tick=? ORDER BY ts", (rapp_id, tick))]

    def action_history(self, rapp_id: str) -> dict:
        """How often this agent's actions were allowed or approved before: {action name: count}."""
        counts: dict = {}
        for r in self._q("SELECT record FROM decisions WHERE rapp_id=? AND decision IN ('allow','approve')", (rapp_id,)):
            for a in json.loads(r["record"]).get("actions") or []:
                counts[a.get("name")] = counts.get(a.get("name"), 0) + 1
        return counts

    # ---- incident response ------------------------------------------------------------------
    def add_incident(self, incident_id: str, rec: dict, cls: dict, responses: List[str], contained: List[str]) -> None:
        status = "contained" if contained else ("review" if "flag_for_review" in responses else "logged")
        self._q("INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (incident_id, rec["seq"], rec.get("rapp_id"), rec.get("session"), cls["severity"], cls["kind"],
                 cls["type"], cls["title"], status, time.time(), json.dumps(responses), json.dumps(contained)))

    def get_incident(self, incident_id: str) -> Optional[dict]:
        rows = self._q("SELECT * FROM incidents WHERE incident_id=?", (incident_id,))
        return self._incident(rows[0]) if rows else None

    def list_incidents(self, limit: int = 100) -> List[dict]:
        return [self._incident(r) for r in self._q("SELECT * FROM incidents ORDER BY seq DESC LIMIT ?", (limit,))]

    def set_incident_status(self, incident_id: str, status: str, recovery_seq: Optional[int] = None) -> None:
        self._q("UPDATE incidents SET status=?, recovery_seq=COALESCE(?, recovery_seq) WHERE incident_id=?",
                (status, recovery_seq, incident_id))

    @staticmethod
    def _incident(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["responses"], d["contained"] = json.loads(d["responses"] or "[]"), json.loads(d["contained"] or "[]")
        return d

    def end_session(self, session: str, rapp_id: str, incident_id: str) -> None:
        self._q("INSERT OR IGNORE INTO ended_sessions VALUES (?,?,?,?)", (session, rapp_id, incident_id, time.time()))

    def session_ended(self, session: Optional[str]) -> Optional[dict]:
        if not session:
            return None
        rows = self._q("SELECT * FROM ended_sessions WHERE session=?", (session,))
        return dict(rows[0]) if rows else None

    def session_denials(self, session: Optional[str]) -> int:
        if not session:
            return 0
        return self._q("SELECT COUNT(*) AS n FROM decisions WHERE decision='deny' AND session=?", (session,))[0]["n"]

    def add_trace(self, session: str, rapp_id: str, seq: int, sha: str, body: dict) -> None:
        self._q("INSERT OR REPLACE INTO traces VALUES (?,?,?,?,?,?)",
                (session, rapp_id, time.time(), seq, sha, json.dumps(body, default=str)))

    def get_trace(self, session: str) -> Optional[dict]:
        rows = self._q("SELECT * FROM traces WHERE session=?", (session,))
        if not rows:
            return None
        d = dict(rows[0])
        d["body"] = json.loads(d["body"])
        return d

    def add_spend(self, rapp_id: str, day: str, session: str, usd: float, tokens: int) -> None:
        self._q("INSERT OR REPLACE INTO spend VALUES (?,?,?,?,?)", (rapp_id, day, session, usd, tokens))

    def spend(self, rapp_id: str, day: str) -> dict:
        rows = self._q("SELECT COALESCE(SUM(usd),0) AS usd, COALESCE(SUM(tokens),0) AS tokens FROM spend "
                       "WHERE rapp_id=? AND day=?", (rapp_id, day))
        return {"usd": round(rows[0]["usd"], 5), "tokens": int(rows[0]["tokens"])}

    def record(self, seq: int) -> Optional[dict]:
        rows = self._q("SELECT record FROM decisions WHERE seq=?", (seq,))
        return json.loads(rows[0]["record"]) if rows else None

    def decisions_in_session(self, session: str) -> List[dict]:
        return [json.loads(r["record"]) for r in self._q("SELECT record FROM decisions WHERE session=? ORDER BY seq",
                                                         (session,))]

    def sessions(self, limit: int = 30) -> List[dict]:
        """Recent agent sessions in the decision index, newest first, with decision counts and worst severity."""
        out: dict = {}
        for r in self._q("SELECT session, record FROM decisions WHERE session IS NOT NULL ORDER BY seq DESC LIMIT 3000"):
            rec = json.loads(r["record"])
            row = out.setdefault(r["session"], {"session": r["session"], "rapp_id": rec.get("rapp_id"),
                                                "first_seq": rec["seq"], "last_seq": rec["seq"], "decisions": {},
                                                "severity": None})
            row["first_seq"] = min(row["first_seq"], rec["seq"])
            row["decisions"][rec.get("decision")] = row["decisions"].get(rec.get("decision"), 0) + 1
            sev = (rec.get("incident") or {}).get("severity")
            if sev and (row["severity"] is None or sev < row["severity"]):
                row["severity"] = sev
        return sorted(out.values(), key=lambda x: -x["last_seq"])[:limit]

    def reset_runtime(self) -> None:
        """Clears per-run state (not the ledger, which is append-only, nor incidents and transcripts, which
        are evidence). Agents suspended by incident response are resumed: a reset starts a new run."""
        for t in ("budget", "approvals", "taint", "alarms", "changes", "baselines", "denials", "ended_sessions", "spend"):
            self._q(f"DELETE FROM {t}")
        self._q("UPDATE rapps SET status='admitted' WHERE status='suspended'")
