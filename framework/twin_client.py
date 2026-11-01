"""Client the enforcement point uses to reach the twin wrapper (the only holder of the twin key)."""

from typing import List, Optional

import httpx


class TwinUnavailable(RuntimeError):
    pass


class TwinClient:
    def __init__(self, base_url: str, key: str, timeout: float = 30.0):
        self._http = httpx.Client(base_url=base_url, headers={"X-Twin-Key": key}, timeout=timeout)
        self.fault = False  # NF-1 test hook

    def _call(self, method: str, path: str, **kw):
        if self.fault:
            raise TwinUnavailable("twin unreachable (fault injected)")
        try:
            r = self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise TwinUnavailable(f"twin unreachable: {e}") from e
        if r.status_code >= 400:
            raise TwinUnavailable(f"twin error {r.status_code}: {r.text[:200]}")
        return r.json()

    def topology(self) -> list:
        return self._call("GET", "/topology")["cells"]

    def state(self) -> dict:
        return self._call("GET", "/state")

    def kpis(self, tick: Optional[int] = None) -> dict:
        return self._call("GET", "/kpis", params={"tick": tick} if tick is not None else None)

    def map_view(self) -> dict:
        return self._call("GET", "/map")

    def shadow(self, actions: List[dict], tick: Optional[int] = None) -> dict:
        return self._call("POST", "/shadow", json={"actions": actions, "tick": tick})

    def apply(self, actions: List[dict]) -> dict:
        return self._call("POST", "/apply", json={"actions": actions})

    def set_tick(self, tick: int) -> dict:
        return self._call("POST", "/tick", json={"tick": tick})

    def reset(self) -> dict:
        return self._call("POST", "/reset")

    def add_event(self, **event) -> dict:
        return self._call("POST", "/event", json=event)

    def clear_event(self) -> dict:
        return self._call("DELETE", "/event")
