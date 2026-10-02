"""Learn typical floor−air delta from HA history (night + steady preferred)."""

from __future__ import annotations

import logging
import statistics
import time
from typing import Optional

from greenhouse_climate.ports import TemperatureHistorySource, TempPairSample
from greenhouse_climate.state_store import ControlState

log = logging.getLogger("greenhouse_climate.delta")


class DeltaModel:
    def __init__(
        self,
        history: TemperatureHistorySource,
        *,
        hours: float = 48.0,
        refresh_s: float = 1800.0,
        ema_alpha: float = 0.2,
        steady_tol_c: float = 1.0,
        default_delta_c: float = 4.0,
    ) -> None:
        self.history = history
        self.hours = hours
        self.refresh_s = refresh_s
        self.ema_alpha = ema_alpha
        self.steady_tol_c = steady_tol_c
        self.default_delta_c = default_delta_c

    def effective_delta(self, state: ControlState) -> float:
        if state.delta_c is not None:
            return state.delta_c
        return self.default_delta_c

    def maybe_refresh(self, state: ControlState, *, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        if state.delta_updated_at and (now - state.delta_updated_at) < self.refresh_s:
            return False
        try:
            samples = self.history.fetch_pairs(hours=self.hours)
        except Exception as exc:
            log.warning("delta history fetch failed: %s", exc)
            return False
        estimate = estimate_delta(
            samples,
            steady_tol_c=self.steady_tol_c,
        )
        if estimate is None:
            log.info("delta: not enough steady samples (%d raw)", len(samples))
            state.delta_updated_at = now
            return False

        mean_delta, n = estimate
        if state.delta_c is None:
            state.delta_c = mean_delta
        else:
            a = self.ema_alpha
            state.delta_c = (1.0 - a) * state.delta_c + a * mean_delta
        state.delta_samples = n
        state.delta_updated_at = now
        log.info("delta updated: %.2f °C (n=%d, raw_mean=%.2f)", state.delta_c, n, mean_delta)
        return True


def estimate_delta(
    samples: list[TempPairSample],
    *,
    steady_tol_c: float = 1.0,
) -> Optional[tuple[float, int]]:
    """Return (mean_delta, n) preferring night + steady floor≈setpoint."""
    night_steady: list[float] = []
    night_any: list[float] = []
    any_steady: list[float] = []

    for s in samples:
        d = s.floor_c - s.air_c
        steady = (
            s.setpoint_c is not None
            and abs(s.floor_c - s.setpoint_c) <= steady_tol_c
        )
        if not s.is_day:
            night_any.append(d)
            if steady:
                night_steady.append(d)
        if steady:
            any_steady.append(d)

    for pool in (night_steady, night_any, any_steady):
        if len(pool) >= 5:
            trimmed = _trim(pool)
            return statistics.fmean(trimmed), len(trimmed)
    return None


def _trim(values: list[float]) -> list[float]:
    if len(values) < 10:
        return values
    ordered = sorted(values)
    cut = max(1, len(ordered) // 10)
    return ordered[cut:-cut] or ordered
