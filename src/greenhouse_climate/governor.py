"""Slow setpoint governor for balcony floor heat vs greenhouse air."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

from greenhouse_climate.delta_model import DeltaModel
from greenhouse_climate.ports import (
    AirTemperatureSource,
    DayNightSource,
    FloorThermostat,
)
from greenhouse_climate.state_store import ControlState

log = logging.getLogger("greenhouse_climate.governor")


@dataclass(frozen=True)
class GovernorParams:
    deadband_c: float = 0.5
    floor_min_c: float = 15.0
    floor_max_c: float = 35.0
    setpoint_step_c: float = 0.5
    min_write_interval_s: float = 180.0
    boost_day_c: float = 2.0
    boost_night_c: float = 3.0
    margin_c: float = 1.0
    solar_offset_c: float = 1.5


@dataclass
class TickResult:
    mode: str
    air_c: float
    floor_c: float
    setpoint_c: Optional[float]
    desired_c: Optional[float]
    written: bool
    solar_guess: bool
    is_day: bool
    delta_c: float


class SetpointGovernor:
    def __init__(
        self,
        air: AirTemperatureSource,
        floor: FloorThermostat,
        day_night: DayNightSource,
        delta_model: DeltaModel,
        params: GovernorParams,
    ) -> None:
        self.air = air
        self.floor = floor
        self.day_night = day_night
        self.delta_model = delta_model
        self.params = params

    def tick(self, state: ControlState, *, now: Optional[float] = None) -> TickResult:
        now = time.time() if now is None else now
        self.delta_model.maybe_refresh(state, now=now)

        air_c = self.air.read_c()
        floor_c = self.floor.read_current_c()
        setpoint_c = self.floor.read_setpoint_c()
        is_day = self.day_night.is_day()
        delta_c = self.delta_model.effective_delta(state)

        solar_guess = self._solar_guess(state, air_c, floor_c, setpoint_c, is_day, now)
        state.solar_guess = solar_guess
        state.last_air_c = air_c
        state.last_air_at = now

        if not state.enabled:
            state.mode = "disabled"
            return TickResult(
                mode="disabled",
                air_c=air_c,
                floor_c=floor_c,
                setpoint_c=setpoint_c,
                desired_c=None,
                written=False,
                solar_guess=solar_guess,
                is_day=is_day,
                delta_c=delta_c,
            )

        target = state.target_c
        err = air_c - target
        db = self.params.deadband_c

        if err < -db:
            mode = "heat"
            boost = self.params.boost_night_c if not is_day else self.params.boost_day_c
            desired = target + delta_c + self.params.margin_c
            desired = max(desired, floor_c + boost * 0.5)
        elif err > db:
            mode = "cool"
            desired = target + delta_c - self.params.margin_c
            if solar_guess:
                desired -= self.params.solar_offset_c
        else:
            mode = "hold"
            desired = target + delta_c
            if solar_guess:
                desired -= self.params.solar_offset_c
            elif abs(floor_c - desired) <= self.params.deadband_c + 0.5:
                desired = floor_c

        desired = self._quantize(self._clamp(desired))
        state.mode = mode

        write_value = self._rate_limited(setpoint_c, desired)
        written = False
        if write_value is not None and self._may_write(state, setpoint_c, write_value, now):
            self._ensure_heat_mode()
            self.floor.write_setpoint_c(write_value)
            state.last_setpoint_c = write_value
            state.last_write_at = now
            written = True
            log.info(
                "setpoint %.1f -> %.1f (°C) mode=%s air=%.1f floor=%.1f delta=%.1f solar=%s",
                setpoint_c if setpoint_c is not None else float("nan"),
                write_value,
                mode,
                air_c,
                floor_c,
                delta_c,
                solar_guess,
            )

        return TickResult(
            mode=mode,
            air_c=air_c,
            floor_c=floor_c,
            setpoint_c=setpoint_c,
            desired_c=desired,
            written=written,
            solar_guess=solar_guess,
            is_day=is_day,
            delta_c=delta_c,
        )

    def _solar_guess(
        self,
        state: ControlState,
        air_c: float,
        floor_c: float,
        setpoint_c: Optional[float],
        is_day: bool,
        now: float,
    ) -> bool:
        if not is_day:
            return False
        if state.last_air_c is None or state.last_air_at <= 0:
            return False
        dt = now - state.last_air_at
        if dt < 60 or dt > 3600:
            return False
        d_air = air_c - state.last_air_c
        floor_driving = setpoint_c is not None and setpoint_c > floor_c + 0.5
        return d_air > 0.3 and not floor_driving

    def _clamp(self, value: float) -> float:
        return max(self.params.floor_min_c, min(self.params.floor_max_c, value))

    def _quantize(self, value: float) -> float:
        step = self.params.setpoint_step_c
        if step <= 0:
            return value
        return round(value / step) * step

    def _rate_limited(self, current_sp: Optional[float], desired: float) -> Optional[float]:
        if current_sp is None:
            return desired
        if abs(current_sp - desired) < self.params.setpoint_step_c / 2:
            return None
        max_step = max(self.params.setpoint_step_c, 1.0)
        if abs(desired - current_sp) <= max_step + 1e-6:
            return desired
        direction = math.copysign(1.0, desired - current_sp)
        return self._quantize(self._clamp(current_sp + direction * max_step))

    def _may_write(
        self,
        state: ControlState,
        current_sp: Optional[float],
        write_value: float,
        now: float,
    ) -> bool:
        if state.last_write_at and (now - state.last_write_at) < self.params.min_write_interval_s:
            if current_sp is None or abs(current_sp - write_value) < self.params.setpoint_step_c * 2:
                return False
        return True

    def _ensure_heat_mode(self) -> None:
        mode = self.floor.read_hvac_mode()
        if mode in {None, "off"}:
            self.floor.set_hvac_mode("heat")
