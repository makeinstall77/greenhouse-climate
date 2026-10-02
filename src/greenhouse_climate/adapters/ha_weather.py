"""HA sun + weather proxy for likely direct sun (until local lux)."""

from __future__ import annotations

import logging
from typing import Optional

from greenhouse_climate.ha_rest import HaRest
from greenhouse_climate.ports import DayNightSource

log = logging.getLogger("greenhouse_climate.solar_hint")

# Met.no / HA weather conditions that usually mean useful insolation.
_SUNNY = frozenset({"sunny", "clear", "clear-night"})
_PARTLY = frozenset({"partlycloudy", "partly-cloudy"})
_OVERCAST = frozenset(
    {
        "cloudy",
        "fog",
        "rainy",
        "pouring",
        "snowy",
        "snowy-rainy",
        "hail",
        "lightning",
        "lightning-rainy",
        "windy",
        "windy-variant",
        "exceptional",
    }
)


class HaSunWeatherHint:
    """Combine sun.sun day/night with optional weather.* cloud/condition."""

    def __init__(
        self,
        ha: HaRest,
        day_night: DayNightSource,
        weather_entity: str = "",
        *,
        cloud_partly_max: float = 60.0,
    ) -> None:
        self.ha = ha
        self.day_night = day_night
        self.weather_entity = (weather_entity or "").strip()
        self.cloud_partly_max = cloud_partly_max

    def likely_sun(self) -> Optional[bool]:
        try:
            is_day = bool(self.day_night.is_day())
        except Exception as exc:
            log.warning("sun read for solar hint failed: %s", exc)
            return None
        if not is_day:
            return False
        if not self.weather_entity:
            return None

        try:
            state = self.ha.get_state(self.weather_entity)
        except Exception as exc:
            log.warning("weather read failed (%s): %s", self.weather_entity, exc)
            raise

        raw = (state.get("state") or "").strip().lower()
        if raw in {"unknown", "unavailable", ""}:
            return None

        attrs = state.get("attributes") or {}
        cloud = attrs.get("cloud_coverage")
        try:
            cloud_f = float(cloud) if cloud is not None else None
        except (TypeError, ValueError):
            cloud_f = None

        if raw in _SUNNY:
            return True
        if raw in _PARTLY:
            if cloud_f is None:
                return True
            return cloud_f <= self.cloud_partly_max
        if raw in _OVERCAST:
            return False
        # Unknown condition string — treat as inconclusive.
        log.debug("unmapped weather condition %r", raw)
        return None
