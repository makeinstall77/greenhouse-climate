"""Debounced thermostat health alerts via tg-notifier."""

from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional, Union

from greenhouse_climate.adapters.notifier import Notifier, NullNotifier
from greenhouse_climate.governor import TickResult
from greenhouse_climate.state_store import ControlState

log = logging.getLogger("greenhouse_climate.monitor")

KEY_FLOOR = "floor"
KEY_WRITE_HVAC = "write_hvac"
KEY_COLD = "cold"

TextFn = Callable[[str], str]


@dataclass(frozen=True)
class MonitorParams:
    alert_after_s: float = 1800.0
    deadband_c: float = 0.5


class AlertMonitor:
    def __init__(
        self,
        notifier: Notifier,
        params: MonitorParams,
    ) -> None:
        self.notifier = notifier or NullNotifier()
        self.params = params

    def evaluate(
        self,
        state: ControlState,
        result: TickResult,
        *,
        now: Optional[float] = None,
    ) -> None:
        now = time.time() if now is None else now
        if not state.enabled:
            # Drop timers while disabled so re-enable starts a fresh window.
            for key in (KEY_FLOOR, KEY_WRITE_HVAC, KEY_COLD):
                self._clear_quiet(state, key)
            return

        degraded = set(result.degraded or [])
        self._track(
            state,
            KEY_FLOOR,
            bad="floor" in degraded,
            now=now,
            down_text=lambda dur: (
                f"🔴 greenhouse: термостат недоступен {dur} "
                f"(нет чтения пола)"
            ),
            up_text="🟢 greenhouse: термостат снова доступен",
            event="floor_down",
            up_event="floor_up",
            detail=result,
        )

        reasons: list[str] = []
        if "write" in degraded:
            reasons.append("запись уставки")
        if "hvac" in degraded:
            reasons.append("HVAC не heat")
        reason_s = ", ".join(reasons) if reasons else "управление"
        self._track(
            state,
            KEY_WRITE_HVAC,
            bad="write" in degraded or "hvac" in degraded,
            now=now,
            down_text=lambda dur: (
                f"🔴 greenhouse: термостат не управляется {dur} ({reason_s})"
            ),
            up_text="🟢 greenhouse: управление термостатом восстановилось",
            event="write_hvac_down",
            up_event="write_hvac_up",
            detail=result,
        )

        cold = (
            result.air_c is not None
            and result.air_c < state.target_c - self.params.deadband_c
        )
        air_s = f"{result.air_c:.1f}" if result.air_c is not None else "?"
        target_s = f"{state.target_c:.1f}"
        self._track(
            state,
            KEY_COLD,
            bad=cold,
            now=now,
            down_text=lambda dur: (
                f"🔴 greenhouse: воздух {html.escape(air_s)}°C при цели "
                f"{html.escape(target_s)}°C уже {dur}"
            ),
            up_text=(
                f"🟢 greenhouse: воздух снова у цели "
                f"({html.escape(air_s)}°C / {html.escape(target_s)}°C)"
            ),
            event="cold_down",
            up_event="cold_up",
            detail=result,
        )

    def _clear_quiet(self, state: ControlState, key: str) -> None:
        state.alert_fault_since.pop(key, None)
        state.alert_sent.pop(key, None)

    def _track(
        self,
        state: ControlState,
        key: str,
        *,
        bad: bool,
        now: float,
        down_text: Union[str, TextFn],
        up_text: str,
        event: str,
        up_event: str,
        detail: TickResult,
    ) -> None:
        since_map = state.alert_fault_since
        sent_map = state.alert_sent

        if bad:
            if key not in since_map:
                since_map[key] = now
            since = since_map[key]
            elapsed = now - since
            if elapsed >= self.params.alert_after_s and not sent_map.get(key):
                dur = _fmt_duration(elapsed)
                text = down_text(dur) if callable(down_text) else down_text
                ok = self.notifier.send(
                    text,
                    event=event,
                    dedup_key=f"greenhouse-climate:{key}:down",
                    priority=50,
                )
                if ok:
                    sent_map[key] = True
                    log.warning(
                        "alert %s after %.0fs degraded=%s air=%s",
                        key,
                        elapsed,
                        detail.degraded,
                        detail.air_c,
                    )
                else:
                    log.warning("alert %s enqueue failed (will retry)", key)
            return

        if sent_map.get(key):
            ok = self.notifier.send(
                up_text,
                event=up_event,
                dedup_key=f"greenhouse-climate:{key}:up",
                priority=50,
            )
            if ok:
                log.info("alert %s cleared", key)
            else:
                log.warning("alert %s recovery enqueue failed", key)
        since_map.pop(key, None)
        sent_map.pop(key, None)


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин"
    hours = minutes // 60
    rem = minutes % 60
    if rem == 0:
        return f"{hours} ч"
    return f"{hours} ч {rem} мин"
