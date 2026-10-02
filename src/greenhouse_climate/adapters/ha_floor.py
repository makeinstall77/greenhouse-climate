"""HA climate adapter for floor thermostat."""

from __future__ import annotations

from typing import Optional

from greenhouse_climate.ha_rest import HaRest


class HaFloorThermostat:
    def __init__(self, ha: HaRest, entity_id: str, *, dry_run: bool = False) -> None:
        self.ha = ha
        self.entity_id = entity_id
        self.dry_run = dry_run

    def _attrs(self) -> dict:
        return self.ha.get_state(self.entity_id).get("attributes") or {}

    def read_current_c(self) -> float:
        attrs = self._attrs()
        return float(attrs["current_temperature"])

    def read_setpoint_c(self) -> Optional[float]:
        attrs = self._attrs()
        temp = attrs.get("temperature")
        if temp is None:
            return None
        return float(temp)

    def write_setpoint_c(self, temperature: float) -> None:
        if self.dry_run:
            return
        self.ha.call_service(
            "climate",
            "set_temperature",
            {"entity_id": self.entity_id, "temperature": temperature},
        )

    def read_hvac_mode(self) -> Optional[str]:
        state = self.ha.get_state(self.entity_id)
        return state.get("state")

    def set_hvac_mode(self, mode: str) -> None:
        if self.dry_run:
            return
        self.ha.call_service(
            "climate",
            "set_hvac_mode",
            {"entity_id": self.entity_id, "hvac_mode": mode},
        )
