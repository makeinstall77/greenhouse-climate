"""tg-notifier HTTP client (stdlib only)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Protocol, runtime_checkable

log = logging.getLogger("greenhouse_climate.notifier")


@runtime_checkable
class Notifier(Protocol):
    def send(
        self,
        text: str,
        *,
        event: str = "",
        dedup_key: str = "",
        priority: int = 50,
    ) -> bool:
        """Enqueue a message. Return True on accepted enqueue."""


class NullNotifier:
    def send(
        self,
        text: str,
        *,
        event: str = "",
        dedup_key: str = "",
        priority: int = 50,
    ) -> bool:
        return False


class HttpNotifier:
    """POST form fields to tg-notifier ``/send``."""

    def __init__(
        self,
        url: str,
        *,
        sender: str = "greenhouse-climate",
        timeout: float = 8.0,
    ) -> None:
        self.url = url
        self.sender = sender
        self.timeout = timeout

    def send(
        self,
        text: str,
        *,
        event: str = "",
        dedup_key: str = "",
        priority: int = 50,
    ) -> bool:
        if not self.url or not text:
            return False
        fields = {
            "text": text,
            "sender": self.sender,
            "priority": str(int(priority)),
            "parse_mode": "HTML",
        }
        if event:
            fields["event"] = event
        if dedup_key:
            fields["dedup_key"] = dedup_key
        body = urllib.parse.urlencode(fields).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if resp.status not in (200, 202):
                    log.warning("notifier HTTP %s: %s", resp.status, raw[:200])
                    return False
                if raw:
                    try:
                        payload = json.loads(raw.decode("utf-8"))
                        if isinstance(payload, dict) and payload.get("ok") is False:
                            log.warning("notifier rejected: %s", payload)
                            return False
                    except json.JSONDecodeError:
                        pass
                return True
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            log.warning("notifier send failed: %s", exc)
            return False


def make_notifier(url: Optional[str]) -> Notifier:
    if not url or not str(url).strip():
        return NullNotifier()
    return HttpNotifier(str(url).strip())
