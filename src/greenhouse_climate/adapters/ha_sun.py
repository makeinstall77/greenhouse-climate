"""HA sun.sun day/night adapter."""

from __future__ import annotations

from greenhouse_climate.ha_rest import HaRest


class HaDayNight:
    def __init__(self, ha: HaRest, entity_id: str = "sun.sun") -> None:
        self.ha = ha
        self.entity_id = entity_id

    def is_day(self) -> bool:
        state = self.ha.get_state(self.entity_id)
        return state.get("state") == "above_horizon"
