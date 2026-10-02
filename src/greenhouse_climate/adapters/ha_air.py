"""HA sensor adapter for air temperature."""

from __future__ import annotations

from greenhouse_climate.ha_rest import HaRest


class HaAirTemperature:
    def __init__(self, ha: HaRest, entity_id: str) -> None:
        self.ha = ha
        self.entity_id = entity_id

    def read_c(self) -> float:
        state = self.ha.get_state(self.entity_id)
        return float(state["state"])
