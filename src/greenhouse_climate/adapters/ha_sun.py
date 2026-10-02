"""HA sun.sun day/night adapter."""

from __future__ import annotations

import logging

from greenhouse_climate.ha_rest import HaRest

log = logging.getLogger("greenhouse_climate.sun")


class HaDayNight:
    def __init__(self, ha: HaRest, entity_id: str = "sun.sun") -> None:
        self.ha = ha
        self.entity_id = entity_id

    def is_day(self) -> bool:
        state = self.ha.get_state(self.entity_id)
        value = state.get("state")
        if value in {"unknown", "unavailable", None}:
            raise RuntimeError(f"{self.entity_id} state={value!r}")
        return value == "above_horizon"
