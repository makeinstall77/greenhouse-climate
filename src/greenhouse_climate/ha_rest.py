"""Minimal Home Assistant REST client (stdlib only)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger("greenhouse_climate.ha")


class HaRest:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[dict] = None,
        query: Optional[dict[str, str]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HA {method} {path} -> {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"HA {method} {path} failed: {exc}") from exc

    def get_state(self, entity_id: str) -> dict:
        return self._request("GET", f"/api/states/{entity_id}")

    def set_state(self, entity_id: str, state: str, attributes: Optional[dict] = None) -> dict:
        body: dict[str, Any] = {"state": state}
        if attributes is not None:
            body["attributes"] = attributes
        return self._request("POST", f"/api/states/{entity_id}", body=body)

    def call_service(self, domain: str, service: str, data: dict) -> Any:
        return self._request("POST", f"/api/services/{domain}/{service}", body=data)

    def history_period(
        self,
        entity_ids: list[str],
        *,
        hours: float,
        significant_changes_only: bool = False,
    ) -> list:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)
        # HA expects ISO8601 in path for start
        start_s = start.isoformat().replace("+00:00", "Z")
        path = f"/api/history/period/{urllib.parse.quote(start_s)}"
        query = {
            "filter_entity_id": ",".join(entity_ids),
            "end_time": end.isoformat().replace("+00:00", "Z"),
            "significant_changes_only": "1" if significant_changes_only else "0",
        }
        return self._request("GET", path, query=query) or []
