"""Debounced thermostat health alerts, autoreboot, and panel link watch."""

from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol, Union, runtime_checkable

from greenhouse_climate.adapters.notifier import Notifier, NullNotifier
from greenhouse_climate.governor import TickResult
from greenhouse_climate.state_store import ControlState

log = logging.getLogger("greenhouse_climate.monitor")

KEY_FLOOR = "floor"
KEY_WRITE_HVAC = "write_hvac"
KEY_COLD = "cold"
REBOOT_KEYS = frozenset({KEY_FLOOR, KEY_WRITE_HVAC})

TextFn = Callable[[str], str]


@runtime_checkable
class PowerCycler(Protocol):
    def reboot(self) -> bool: ...


class NullPowerCycler:
    def reboot(self) -> bool:
        return False


@dataclass(frozen=True)
class MonitorParams:
    alert_after_s: float = 1800.0
    deadband_c: float = 0.5


@dataclass(frozen=True)
class RebootParams:
    enabled: bool = False
    cooldown_s: float = 7200.0
    max_per_day: int = 2


@dataclass(frozen=True)
class PanelLinkParams:
    after_s: float = 300.0


class AlertMonitor:
    def __init__(
        self,
        notifier: Notifier,
        params: MonitorParams,
        *,
        power_cycler: Optional[PowerCycler] = None,
    ) -> None:
        self.notifier = notifier or NullNotifier()
        self.params = params
        self.power_cycler: PowerCycler = power_cycler or NullPowerCycler()

    def evaluate(
        self,
        state: ControlState,
        result: TickResult,
        *,
        now: Optional[float] = None,
        reboot: Optional[RebootParams] = None,
    ) -> None:
        now = time.time() if now is None else now
        reboot = reboot or RebootParams()
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
            reboot=reboot,
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
            reboot=reboot,
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
            reboot=reboot,
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
        reboot: RebootParams,
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
                reboot_note = ""
                if key in REBOOT_KEYS:
                    reboot_note = self._maybe_reboot(state, reboot, now=now)
                    if reboot_note:
                        text = f"{text} {reboot_note}"
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

    def _maybe_reboot(
        self,
        state: ControlState,
        reboot: RebootParams,
        *,
        now: float,
    ) -> str:
        if not reboot.enabled or reboot.max_per_day <= 0:
            return ""
        day = _utc_day(now)
        if state.reboot_day != day:
            state.reboot_day = day
            state.reboot_count_day = 0
        if state.reboot_count_day >= reboot.max_per_day:
            log.info("autoreboot skipped: max_per_day=%s", reboot.max_per_day)
            return "(reboot: лимит/сутки)"
        if state.reboot_last_at and (now - state.reboot_last_at) < reboot.cooldown_s:
            log.info("autoreboot skipped: cooldown")
            return "(reboot: cooldown)"
        if not self.power_cycler.reboot():
            log.warning("autoreboot request failed")
            return "(reboot: ошибка запроса)"
        state.reboot_last_at = now
        state.reboot_count_day += 1
        return "+ запрошен reboot"


class PanelLinkMonitor:
    """Watch ESPHome/HA entity availability for the panel SSR block."""

    def __init__(
        self,
        notifier: Notifier,
        *,
        entity_id: str,
        get_state: Callable[[str], dict],
        params: Optional[PanelLinkParams] = None,
    ) -> None:
        self.notifier = notifier or NullNotifier()
        self.entity_id = (entity_id or "").strip()
        self.get_state = get_state
        self.params = params or PanelLinkParams()

    def evaluate(
        self,
        state: ControlState,
        *,
        now: Optional[float] = None,
    ) -> None:
        if not self.entity_id:
            return
        now = time.time() if now is None else now
        try:
            payload = self.get_state(self.entity_id)
            raw = str(payload.get("state") or "").strip().lower()
        except Exception as exc:
            log.warning("panel link state failed: %s", exc)
            raw = "unavailable"

        bad = raw in {"unavailable", "unknown", ""}
        if bad:
            if state.panel_link_fault_since is None:
                state.panel_link_fault_since = now
            elapsed = now - state.panel_link_fault_since
            if elapsed >= self.params.after_s and not state.panel_link_alert_sent:
                ok = self.notifier.send(
                    "🔴 panel: нет связи с блоком реле",
                    event="panel_link_down",
                    dedup_key="panel:link:down",
                    priority=50,
                )
                if ok:
                    state.panel_link_alert_sent = True
                    log.warning("panel link down after %.0fs", elapsed)
                else:
                    log.warning("panel link down enqueue failed")
            return

        if state.panel_link_alert_sent:
            ok = self.notifier.send(
                "🟢 panel: блок реле снова в сети",
                event="panel_link_up",
                dedup_key="panel:link:up",
                priority=50,
            )
            if ok:
                log.info("panel link recovered")
            else:
                log.warning("panel link up enqueue failed")
                return
        state.panel_link_fault_since = None
        state.panel_link_alert_sent = False


def _utc_day(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")


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
