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
    solar_guess: bool = False
    mode: str = "idle"
    helper_enabled_seen: Optional[str] = None
    helper_target_seen: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ControlState":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in known}
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
