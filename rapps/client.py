"""What an rApp holds: the gateway URL and the token admission issued. No twin key, no AWS credentials."""

from typing import List, Optional

import httpx


class GatewayClient:
    def __init__(self, base_url: str, token: Optional[str] = None, timeout: float = 60.0):
        self.base_url = base_url
        self.token = token
        self.session: Optional[str] = None  # sent with each request: groups its decisions into one session
        self._http = httpx.Client(base_url=base_url, timeout=timeout)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def admit(self, card: dict) -> dict:
        r = self._http.post("/v1/admission", json=card).json()
        if r.get("token"):
            self.token = r["token"]
        return r

    def submit(self, actions: List[dict], justification: str = "") -> dict:
        return self._http.post("/v1/actions", json={"actions": actions, "justification": justification,
                                                    "session": self.session}, headers=self._headers()).json()

    def what_if(self, changes: List[dict]) -> dict:
        return self._http.post("/v1/what_if", json={"changes": changes}, headers=self._headers()).json()

    def read_kpi(self) -> dict:
        return self._http.get("/v1/kpis", headers=self._headers()).json()

    def alarms(self) -> dict:
        r = self._http.get("/v1/inputs/alarms", headers=self._headers())
        return r.json() if r.status_code == 200 else {"error": r.json().get("detail"), "status": r.status_code, "alarms": []}

    def scope(self) -> dict:
        r = self._http.get("/v1/scope", headers=self._headers())
        r.raise_for_status()
        return r.json()["scope"]

    def request_status(self, request_id: str) -> dict:
        return self._http.get(f"/v1/requests/{request_id}", headers=self._headers()).json()
