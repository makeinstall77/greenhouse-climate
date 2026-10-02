"""Build paired air/floor history samples from HA recorder."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from greenhouse_climate.ha_rest import HaRest
from greenhouse_climate.ports import TempPairSample

log = logging.getLogger("greenhouse_climate.history")


def _parse_ts(value: str) -> float:
    # HA: 2026-10-02T10:00:00.000000+00:00 or ...Z
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).timestamp()


def _as_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class HaTemperatureHistory:
    def __init__(
        self,
        ha: HaRest,
        *,
        air_entity: str,
        floor_climate_entity: str,
        sun_entity: str = "sun.sun",
    ) -> None:
        self.ha = ha
        self.air_entity = air_entity
        self.floor_climate_entity = floor_climate_entity
        self.sun_entity = sun_entity

    def fetch_pairs(self, *, hours: float) -> list[TempPairSample]:
        rows = self.ha.history_period(
            [self.air_entity, self.floor_climate_entity, self.sun_entity],
            hours=hours,
            significant_changes_only=False,
        )
        by_entity: dict[str, list[dict]] = {}
        for series in rows:
            if not series:
                continue
            eid = series[0].get("entity_id")
            if eid:
                by_entity[eid] = series

        air_pts = [
            (_parse_ts(p["last_changed"]), _as_float(p.get("state")))
            for p in by_entity.get(self.air_entity, [])
        ]
        air_pts = [(t, v) for t, v in air_pts if v is not None]

        floor_pts: list[tuple[float, float, Optional[float]]] = []
        for p in by_entity.get(self.floor_climate_entity, []):
            ts = _parse_ts(p["last_changed"])
            attrs = p.get("attributes") or {}
            cur = _as_float(attrs.get("current_temperature"))
            sp = _as_float(attrs.get("temperature"))
            if cur is not None:
                floor_pts.append((ts, cur, sp))

        sun_pts = [
            (_parse_ts(p["last_changed"]), p.get("state") == "above_horizon")
            for p in by_entity.get(self.sun_entity, [])
        ]

        if not air_pts or not floor_pts:
            return []

        # Sample on floor history timestamps (usually slower / more relevant)
        samples: list[TempPairSample] = []
        for ts, floor_c, sp in floor_pts:
            air_c = _nearest(air_pts, ts)
            if air_c is None:
                continue
            is_day = _nearest_bool(sun_pts, ts, default=True)
            samples.append(
                TempPairSample(
                    timestamp=ts,
                    air_c=air_c,
                    floor_c=floor_c,
                    setpoint_c=sp,
                    is_day=is_day,
                )
            )
        return samples


def _nearest(points: list[tuple[float, float]], ts: float) -> Optional[float]:
    if not points:
        return None
    best = min(points, key=lambda p: abs(p[0] - ts))
    if abs(best[0] - ts) > 1800:
        return None
    return best[1]


def _nearest_bool(
    points: list[tuple[float, bool]], ts: float, *, default: bool
) -> bool:
    if not points:
        return default
    # last known at or before ts
    prior = [p for p in points if p[0] <= ts]
    if prior:
        return prior[-1][1]
    return points[0][1]
