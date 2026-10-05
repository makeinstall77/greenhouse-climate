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
    SolarHintSource,
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
    # Air trend thresholds in °C/hour over [trend_min_s, trend_window_s].
    trend_rise_c: float = 0.8
    trend_fall_c: float = 0.1
    trend_window_s: float = 1200.0
    trend_min_s: float = 600.0
    trend_max_s: float = 7200.0


@dataclass
class TickResult:
    mode: str
    air_c: Optional[float]
    floor_c: Optional[float]
    setpoint_c: Optional[float]
    desired_c: Optional[float]
    written: bool
    solar_guess: bool
    falling_guess: bool
    likely_sun: Optional[bool]
    is_day: bool
    delta_c: float
    degraded: list[str]


class _NullSolarHint:
    def likely_sun(self) -> Optional[bool]:
        return None


class SetpointGovernor:
    def __init__(
        self,
        air: AirTemperatureSource,
        floor: FloorThermostat,
        day_night: DayNightSource,
        delta_model: DeltaModel,
        params: GovernorParams,
        solar_hint: Optional[SolarHintSource] = None,
    ) -> None:
        self.air = air
        self.floor = floor
        self.day_night = day_night
        self.delta_model = delta_model
        self.params = params
        self.solar_hint: SolarHintSource = solar_hint or _NullSolarHint()

    def tick(self, state: ControlState, *, now: Optional[float] = None) -> TickResult:
        now = time.time() if now is None else now
        self.delta_model.maybe_refresh(state, now=now)
        degraded: list[str] = []

        air_c = self._safe_air()
        floor_c = self._safe_floor_current()
        setpoint_c = self._safe_floor_setpoint()
        is_day_opt = self._safe_is_day()
        is_day = False if is_day_opt is None else is_day_opt
        if is_day_opt is None:
            degraded.append("sun")
        likely_sun, weather_failed = self._safe_likely_sun()
        if weather_failed:
            degraded.append("weather")

        delta_c = self.delta_model.effective_delta(state)

        if air_c is None:
            degraded.append("air")
        if floor_c is None:
            degraded.append("floor")

        solar_guess = False
        falling_guess = False
        if air_c is not None:
            air_rate = self._air_rate_c_per_h(state, air_c, now)
            solar_guess = self._solar_guess(
                air_rate, floor_c, setpoint_c, is_day
            )
            falling_guess = self._falling_guess(air_rate)
            state.last_air_c = air_c
            state.last_air_at = now

        state.solar_guess = solar_guess
        state.falling_guess = falling_guess
        state.likely_sun = likely_sun
        state.degraded = list(degraded)

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
                falling_guess=falling_guess,
                likely_sun=likely_sun,
                is_day=is_day,
                delta_c=delta_c,
                degraded=degraded,
            )

        if air_c is None or floor_c is None:
            state.mode = "degraded"
            log.warning("degraded tick: missing %s — skip write", ",".join(degraded) or "sensors")
            return TickResult(
                mode="degraded",
                air_c=air_c,
                floor_c=floor_c,
                setpoint_c=setpoint_c,
                desired_c=None,
                written=False,
                solar_guess=solar_guess,
                falling_guess=falling_guess,
                likely_sun=likely_sun,
                is_day=is_day,
                delta_c=delta_c,
                degraded=degraded,
            )

        target = state.target_c
        err = air_c - target
        db = self.params.deadband_c
        apply_solar = self._should_apply_solar(
            solar_guess=solar_guess,
            falling_guess=falling_guess,
            likely_sun=likely_sun,
            air_c=air_c,
            target=target,
        )

        if err < -db:
            mode = "heat"
            boost = self.params.boost_night_c if not is_day else self.params.boost_day_c
            desired = target + delta_c + self.params.margin_c
            desired = max(desired, floor_c + boost * 0.5)
        elif err > db:
            if falling_guess:
                mode = "hold"
                desired = target + delta_c
            else:
                mode = "cool"
                desired = target + delta_c - self.params.margin_c
                if apply_solar:
                    desired -= self.params.solar_offset_c
        else:
            mode = "hold"
            desired = target + delta_c
            if apply_solar:
                desired -= self.params.solar_offset_c
            elif abs(floor_c - desired) <= self.params.deadband_c + 0.5:
                desired = floor_c

        # While air is falling, never lower the floor setpoint (hold/cool/solar).
        # Heat may still raise it. Daytime cool resumes once the fall stops.
        if falling_guess and setpoint_c is not None:
            desired = max(desired, setpoint_c)

        desired = self._quantize(self._clamp(desired))
        state.mode = mode

        # Keep thermostat on even when setpoint does not need a write
        # (e.g. someone turned climate off in HA).
        self._ensure_heat_mode()

        write_value = self._rate_limited(setpoint_c, desired)
        # Extra guard: never commit a lower setpoint while falling.
        if (
            falling_guess
            and write_value is not None
            and setpoint_c is not None
            and write_value < setpoint_c - 1e-6
        ):
            write_value = None
        written = False
        if write_value is not None and self._may_write(state, setpoint_c, write_value, now):
            try:
                self.floor.write_setpoint_c(write_value)
                state.last_setpoint_c = write_value
                state.last_write_at = now
                written = True
                log.info(
                    "setpoint %.1f -> %.1f (°C) mode=%s air=%.1f floor=%.1f "
                    "delta=%.1f solar=%s falling=%s likely_sun=%s",
                    setpoint_c if setpoint_c is not None else float("nan"),
                    write_value,
                    mode,
                    air_c,
                    floor_c,
                    delta_c,
                    solar_guess,
                    falling_guess,
                    likely_sun,
                )
            except Exception as exc:
                degraded.append("write")
                state.degraded = list(degraded)
                log.warning("setpoint write failed: %s", exc)

        return TickResult(
            mode=mode,
            air_c=air_c,
            floor_c=floor_c,
            setpoint_c=setpoint_c,
            desired_c=desired,
            written=written,
            solar_guess=solar_guess,
            falling_guess=falling_guess,
            likely_sun=likely_sun,
            is_day=is_day,
            delta_c=delta_c,
            degraded=degraded,
        )

    def _should_apply_solar(
        self,
        *,
        solar_guess: bool,
        falling_guess: bool,
        likely_sun: Optional[bool],
        air_c: float,
        target: float,
    ) -> bool:
        # Trend wins over weather.
        if falling_guess:
            return False
        if solar_guess:
            return True
        # Soft early hint from sun+weather only when already at/above target
        # and there is no contrary falling trend.
        if likely_sun is True and air_c >= target:
            return True
        return False

    def _update_air_history(
        self, state: ControlState, air_c: float, now: float
    ) -> list[list[float]]:
        hist = list(state.air_history or [])
        if (
            not hist
            and state.last_air_c is not None
            and state.last_air_at > 0
            and state.last_air_at < now
        ):
            hist.append([float(state.last_air_at), float(state.last_air_c)])
        hist.append([float(now), float(air_c)])
        window = max(self.params.trend_window_s, self.params.trend_min_s)
        cutoff = now - window
        hist = [pair for pair in hist if pair[0] >= cutoff]
        # Cap length so state.json stays small (≈ loop 90s → ~14 pts / 20 min).
        max_points = max(8, int(window / 60.0) + 4)
        if len(hist) > max_points:
            hist = hist[-max_points:]
        state.air_history = hist
        return hist

    def _air_rate_c_per_h(
        self, state: ControlState, air_c: float, now: float
    ) -> Optional[float]:
        """Mean air change rate (°C/h) from oldest sample in the trend window."""
        hist = self._update_air_history(state, air_c, now)
        if len(hist) < 2:
            return None
        t0, c0 = hist[0]
        dt = now - t0
        if dt < self.params.trend_min_s or dt > self.params.trend_max_s:
            return None
        return (air_c - c0) / (dt / 3600.0)

    def _solar_guess(
        self,
        air_rate: Optional[float],
        floor_c: Optional[float],
        setpoint_c: Optional[float],
        is_day: bool,
    ) -> bool:
        if not is_day or air_rate is None:
            return False
        floor_driving = (
            floor_c is not None
            and setpoint_c is not None
            and setpoint_c > floor_c + 0.5
        )
        return air_rate > self.params.trend_rise_c and not floor_driving

    def _falling_guess(self, air_rate: Optional[float]) -> bool:
        if air_rate is None:
            return False
        return air_rate < -self.params.trend_fall_c

    def _safe_air(self) -> Optional[float]:
        try:
            return float(self.air.read_c())
        except Exception as exc:
            log.warning("air read failed: %s", exc)
            return None

    def _safe_floor_current(self) -> Optional[float]:
        try:
            return float(self.floor.read_current_c())
        except Exception as exc:
            log.warning("floor current read failed: %s", exc)
            return None

    def _safe_floor_setpoint(self) -> Optional[float]:
        try:
            sp = self.floor.read_setpoint_c()
            return None if sp is None else float(sp)
        except Exception as exc:
            log.warning("floor setpoint read failed: %s", exc)
            return None

    def _safe_is_day(self) -> Optional[bool]:
        try:
            return bool(self.day_night.is_day())
        except Exception as exc:
            log.warning("sun/day-night read failed: %s", exc)
            return None

    def _safe_likely_sun(self) -> tuple[Optional[bool], bool]:
        try:
            return self.solar_hint.likely_sun(), False
        except Exception as exc:
            log.warning("solar hint failed: %s", exc)
            return None, True

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
        try:
            mode = self.floor.read_hvac_mode()
        except Exception as exc:
            log.warning("hvac mode read failed: %s", exc)
            return
        # Floor thermostat should stay in heat while the governor is active.
        # LocalTuya may briefly report unknown; treat anything but heat as off.
        if mode == "heat":
            return
        try:
            self.floor.set_hvac_mode("heat")
            log.info("hvac mode %s -> heat", mode)
        except Exception as exc:
            log.warning("hvac mode set failed: %s", exc)
