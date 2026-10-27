"""Persisted controller state (JSON file)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

log = logging.getLogger("greenhouse_climate.state")


@dataclass
class ControlState:
    enabled: bool = False
    target_c: float = 22.0
    delta_c: Optional[float] = None
    delta_samples: int = 0
    delta_updated_at: float = 0.0
    last_setpoint_c: Optional[float] = None
    last_write_at: float = 0.0
    last_air_c: Optional[float] = None
    last_air_at: float = 0.0
    # Recent air samples [[unix_ts, temp_c], ...] for rate-based trend.
    air_history: list[list[float]] = field(default_factory=list)
    solar_guess: bool = False
    falling_guess: bool = False
    likely_sun: Optional[bool] = None
    degraded: list[str] = field(default_factory=list)
    mode: str = "idle"
    helper_enabled_seen: Optional[str] = None
    helper_target_seen: Optional[float] = None
    # Alert debounce: fault key -> unix ts when continuous fault started.
    alert_fault_since: dict[str, float] = field(default_factory=dict)
    # Alert debounce: fault key -> True after down notification was sent.
    alert_sent: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ControlState":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in known}
        hist = kwargs.get("air_history")
        if hist is not None:
            cleaned: list[list[float]] = []
            for item in hist:
                try:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        cleaned.append([float(item[0]), float(item[1])])
                except (TypeError, ValueError):
                    continue
            kwargs["air_history"] = cleaned
        since = kwargs.get("alert_fault_since")
        if isinstance(since, dict):
            cleaned_since: dict[str, float] = {}
            for key, val in since.items():
                try:
                    cleaned_since[str(key)] = float(val)
                except (TypeError, ValueError):
                    continue
            kwargs["alert_fault_since"] = cleaned_since
        elif since is not None:
            kwargs["alert_fault_since"] = {}
        sent = kwargs.get("alert_sent")
        if isinstance(sent, dict):
            kwargs["alert_sent"] = {str(k): bool(v) for k, v in sent.items()}
        elif sent is not None:
            kwargs["alert_sent"] = {}
        return cls(**kwargs)


class StateStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self.state = ControlState()

    def load(self) -> ControlState:
        if not os.path.isfile(self.path):
            return self.state
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self.state = ControlState.from_dict(data)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("failed to load state %s: %s", self.path, exc)
        return self.state

    def save(self) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        payload = json.dumps(self.state.to_dict(), indent=2, sort_keys=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.write("\n")
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
