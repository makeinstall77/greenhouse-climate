"""Device ports — swap adapters without touching the governor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class AirTemperatureSource(Protocol):
    def read_c(self) -> float:
        """Current air temperature in °C."""


@runtime_checkable
class FloorThermostat(Protocol):
    def read_current_c(self) -> float:
        """Measured floor (or thermostat) temperature °C."""

    def read_setpoint_c(self) -> Optional[float]:
        """Current setpoint °C, or None if unavailable."""

    def write_setpoint_c(self, temperature: float) -> None:
        """Apply a new floor setpoint °C."""

    def read_hvac_mode(self) -> Optional[str]:
        """e.g. heat / off / auto."""

    def set_hvac_mode(self, mode: str) -> None:
        """Set HVAC mode when the controller needs heat enabled."""


@runtime_checkable
class DayNightSource(Protocol):
    def is_day(self) -> bool:
        """True when daytime (sun above horizon or equivalent)."""


@runtime_checkable
class SolarHintSource(Protocol):
    """Optional sun/weather proxy until a local illuminance sensor exists."""

    def likely_sun(self) -> Optional[bool]:
        """True/False when known; None when weather/sun unavailable."""


@runtime_checkable
class HumiditySource(Protocol):
    """Optional future feedforward (air thermal inertia proxy)."""

    def read_pct(self) -> Optional[float]:
        ...


@runtime_checkable
class IlluminanceSource(Protocol):
    """Optional future solar-gain proxy."""

    def read_lux(self) -> Optional[float]:
        ...


@dataclass(frozen=True)
class TempPairSample:
    timestamp: float
    air_c: float
    floor_c: float
    setpoint_c: Optional[float]
    is_day: bool


@runtime_checkable
class TemperatureHistorySource(Protocol):
    """Paired air/floor history for Δ learning."""

    def fetch_pairs(self, *, hours: float) -> list[TempPairSample]:
        ...
