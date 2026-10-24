"""HA climate adapter for floor thermostat."""

from __future__ import annotations

import logging
from typing import Optional

from greenhouse_climate.ha_rest import HaRest

log = logging.getLogger("greenhouse_climate.floor")

# LocalTuya / device: DP4 "1"=manual, "0"=program/schedule.
# Cloud Tuya climate only exposes off/heat_cool — program is invisible there,
# and set_temperature is ignored while the device is in schedule mode.
_PROGRAM_DP_VALUES = {"0", 0, "program", "auto", False}


class HaFloorThermostat:
    def __init__(
        self,
        ha: HaRest,
        entity_id: str,
        *,
        dry_run: bool = False,
        tuya_device_id: str = "",
        tuya_host: str = "",
        tuya_local_key: str = "",
        tuya_protocol: float = 3.3,
        mode_dp: int = 4,
        manual_dp_value: str = "1",
    ) -> None:
        self.ha = ha
        self.entity_id = entity_id
        self.dry_run = dry_run
        self.tuya_device_id = tuya_device_id
        self.tuya_host = tuya_host
        self.tuya_local_key = tuya_local_key
        self.tuya_protocol = tuya_protocol
        self.mode_dp = mode_dp
        self.manual_dp_value = manual_dp_value

    def _attrs(self) -> dict:
        state = self.ha.get_state(self.entity_id)
        raw = state.get("state")
        # LocalTuya sometimes reports state=unknown while temps are still valid.
        if raw == "unavailable":
            raise RuntimeError(f"{self.entity_id} state={raw!r}")
        return state.get("attributes") or {}

    def read_current_c(self) -> float:
        attrs = self._attrs()
        temp = attrs.get("current_temperature")
        if temp is None:
            raise RuntimeError(f"{self.entity_id} missing current_temperature")
        return float(temp)

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
        # Leaving schedule for manual is required before set_temperature works.
        if mode in {"heat", "heat_cool"}:
            self.ensure_manual()
        self.ha.call_service(
            "climate",
            "set_hvac_mode",
            {"entity_id": self.entity_id, "hvac_mode": mode},
        )

    def ensure_manual(self) -> None:
        """Force device out of program/schedule into manual (DP4).

        Cloud ``climate.smart_thermostat`` stays ``heat_cool`` in both modes,
        so the governor must set the local DP explicitly each active tick.
        """
        if self.dry_run:
            return
        if self._force_manual_tinytuya():
            return
        self._force_manual_localtuya()

    def _force_manual_tinytuya(self) -> bool:
        if not (self.tuya_device_id and self.tuya_host and self.tuya_local_key):
            return False
        try:
            import tinytuya  # type: ignore
        except ImportError:
            log.warning(
                "tinytuya not installed — cannot force manual via %s",
                self.tuya_host,
            )
            return False
        try:
            dev = tinytuya.Device(
                self.tuya_device_id, self.tuya_host, self.tuya_local_key
            )
            dev.set_version(self.tuya_protocol)
            dev.set_socketTimeout(5)
            status = dev.status() or {}
            dps = status.get("dps") or {}
            # tinytuya may key DPs as str or int
            current = dps.get(str(self.mode_dp), dps.get(self.mode_dp))
            if current is not None and str(current) == str(self.manual_dp_value):
                return True
            if current in _PROGRAM_DP_VALUES or current is not None:
                log.info(
                    "floor mode DP%s %r -> manual %r (local)",
                    self.mode_dp,
                    current,
                    self.manual_dp_value,
                )
            result = dev.set_value(self.mode_dp, self.manual_dp_value)
            if isinstance(result, dict) and result.get("Err"):
                log.warning("tinytuya set_value manual failed: %s", result)
                return False
            return True
        except Exception as exc:
            log.warning("tinytuya force manual failed: %s", exc)
            return False

    def _force_manual_localtuya(self) -> None:
        if not self.tuya_device_id:
            return
        try:
            self.ha.call_service(
                "localtuya",
                "set_dp",
                {
                    "device_id": self.tuya_device_id,
                    "dp": self.mode_dp,
                    "value": self.manual_dp_value,
                },
            )
        except Exception as exc:
            log.warning("localtuya set_dp manual failed: %s", exc)
