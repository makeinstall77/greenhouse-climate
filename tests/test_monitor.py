from __future__ import annotations

from dataclasses import dataclass, field

from greenhouse_climate.governor import TickResult
from greenhouse_climate.monitor import (
    KEY_COLD,
    KEY_FLOOR,
    KEY_WRITE_HVAC,
    AlertMonitor,
    MonitorParams,
    PanelLinkMonitor,
    PanelLinkParams,
    RebootParams,
)
from greenhouse_climate.state_store import ControlState


@dataclass
class FakeNotifier:
    messages: list[dict] = field(default_factory=list)
    fail: bool = False

    def send(
        self,
        text: str,
        *,
        event: str = "",
        dedup_key: str = "",
        priority: int = 50,
    ) -> bool:
        if self.fail:
            return False
        self.messages.append(
            {
                "text": text,
                "event": event,
                "dedup_key": dedup_key,
                "priority": priority,
            }
        )
        return True


@dataclass
class FakeCycler:
    calls: int = 0
    ok: bool = True

    def reboot(self) -> bool:
        self.calls += 1
        return self.ok


def _tick(
    *,
    air_c: float = 22.0,
    degraded: list[str] | None = None,
    mode: str = "hold",
) -> TickResult:
    return TickResult(
        mode=mode,
        air_c=air_c,
        floor_c=26.0,
        setpoint_c=26.0,
        desired_c=26.0,
        written=False,
        solar_guess=False,
        falling_guess=False,
        likely_sun=None,
        is_day=False,
        delta_c=4.0,
        degraded=list(degraded or []),
    )


def _mon(
    after: float = 1800.0,
    *,
    cycler: FakeCycler | None = None,
) -> tuple[AlertMonitor, FakeNotifier, ControlState, FakeCycler]:
    notifier = FakeNotifier()
    cycler = cycler or FakeCycler()
    mon = AlertMonitor(
        notifier,
        MonitorParams(alert_after_s=after, deadband_c=0.5),
        power_cycler=cycler,
    )
    state = ControlState(enabled=True, target_c=22.0)
    return mon, notifier, state, cycler


def _reboot(**kwargs) -> RebootParams:
    base = dict(enabled=True, cooldown_s=7200.0, max_per_day=2)
    base.update(kwargs)
    return RebootParams(**base)


def test_flap_under_threshold_sends_nothing():
    mon, notifier, state, _ = _mon()
    t0 = 1_000.0
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0, reboot=_reboot())
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 600, reboot=_reboot())
    mon.evaluate(state, _tick(), now=t0 + 700, reboot=_reboot())  # recovered early
    assert notifier.messages == []
    assert KEY_FLOOR not in state.alert_fault_since


def test_continuous_floor_fault_sends_once():
    mon, notifier, state, cycler = _mon()
    t0 = 1_000.0
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0, reboot=_reboot())
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 1799, reboot=_reboot())
    assert notifier.messages == []
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 1800, reboot=_reboot())
    assert len(notifier.messages) == 1
    assert notifier.messages[0]["event"] == "floor_down"
    assert "недоступен" in notifier.messages[0]["text"]
    assert "запрошен reboot" in notifier.messages[0]["text"]
    assert cycler.calls == 1
    # subsequent ticks: no duplicate
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 2000, reboot=_reboot())
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 3000, reboot=_reboot())
    assert len(notifier.messages) == 1
    assert cycler.calls == 1


def test_recovery_sends_up():
    mon, notifier, state, _ = _mon()
    t0 = 1_000.0
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0, reboot=_reboot())
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 1800, reboot=_reboot())
    mon.evaluate(state, _tick(), now=t0 + 1900, reboot=_reboot())
    assert len(notifier.messages) == 2
    assert notifier.messages[1]["event"] == "floor_up"
    assert "снова доступен" in notifier.messages[1]["text"]
    assert KEY_FLOOR not in state.alert_sent


def test_write_hvac_and_cold_are_separate_events():
    mon, notifier, state, cycler = _mon()
    t0 = 1_000.0
    cold_tick = _tick(air_c=18.0, degraded=["hvac", "write"])
    mon.evaluate(state, cold_tick, now=t0, reboot=_reboot())
    mon.evaluate(state, cold_tick, now=t0 + 1800, reboot=_reboot())
    events = {m["event"] for m in notifier.messages}
    assert events == {"write_hvac_down", "cold_down"}
    assert any("HVAC не heat" in m["text"] for m in notifier.messages)
    assert any("запись уставки" in m["text"] for m in notifier.messages)
    assert any("18.0°C" in m["text"] for m in notifier.messages)
    # cold must not reboot; write_hvac does once
    assert cycler.calls == 1
    assert any("запрошен reboot" in m["text"] for m in notifier.messages)
    assert all(
        "запрошен reboot" not in m["text"]
        for m in notifier.messages
        if m["event"] == "cold_down"
    )


def test_disabled_clears_timers_without_alert():
    mon, notifier, state, _ = _mon()
    t0 = 1_000.0
    mon.evaluate(state, _tick(degraded=["floor"], air_c=10.0), now=t0, reboot=_reboot())
    assert KEY_FLOOR in state.alert_fault_since
    state.enabled = False
    mon.evaluate(
        state, _tick(degraded=["floor"], air_c=10.0), now=t0 + 1800, reboot=_reboot()
    )
    assert notifier.messages == []
    assert KEY_FLOOR not in state.alert_fault_since
    assert KEY_COLD not in state.alert_fault_since


def test_enqueue_failure_retries_next_tick():
    mon, notifier, state, _ = _mon()
    notifier.fail = True
    t0 = 1_000.0
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0, reboot=_reboot())
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 1800, reboot=_reboot())
    assert notifier.messages == []
    assert not state.alert_sent.get(KEY_FLOOR)
    notifier.fail = False
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 1810, reboot=_reboot())
    assert len(notifier.messages) == 1
    assert state.alert_sent.get(KEY_FLOOR) is True


def test_cold_not_triggered_inside_deadband():
    mon, notifier, state, cycler = _mon()
    t0 = 1_000.0
    # target 22, deadband 0.5 → 21.6 is still ok
    mon.evaluate(state, _tick(air_c=21.6), now=t0, reboot=_reboot())
    mon.evaluate(state, _tick(air_c=21.6), now=t0 + 1800, reboot=_reboot())
    assert notifier.messages == []
    assert KEY_COLD not in state.alert_fault_since
    assert KEY_WRITE_HVAC not in state.alert_fault_since
    assert cycler.calls == 0


def test_autoreboot_disabled_skips_cycler():
    mon, notifier, state, cycler = _mon()
    t0 = 1_000.0
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0, reboot=_reboot(enabled=False))
    mon.evaluate(
        state, _tick(degraded=["floor"]), now=t0 + 1800, reboot=_reboot(enabled=False)
    )
    assert cycler.calls == 0
    assert "запрошен reboot" not in notifier.messages[0]["text"]


def test_autoreboot_cooldown():
    mon, notifier, state, cycler = _mon()
    t0 = 1_000.0
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0, reboot=_reboot())
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 1800, reboot=_reboot())
    assert cycler.calls == 1
    # recover then fault again within cooldown
    mon.evaluate(state, _tick(), now=t0 + 1900, reboot=_reboot())
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 2000, reboot=_reboot())
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 3800, reboot=_reboot())
    assert cycler.calls == 1
    assert any("cooldown" in m["text"] for m in notifier.messages)


def test_autoreboot_max_per_day():
    mon, notifier, state, cycler = _mon()
    t0 = 1_700_000_000.0  # fixed unix
    rb = _reboot(max_per_day=1, cooldown_s=0)
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0, reboot=rb)
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 1800, reboot=rb)
    assert cycler.calls == 1
    mon.evaluate(state, _tick(), now=t0 + 1900, reboot=rb)
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 2000, reboot=rb)
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 3800, reboot=rb)
    assert cycler.calls == 1
    assert any("лимит" in m["text"] for m in notifier.messages)


def test_panel_link_down_and_up():
    notifier = FakeNotifier()
    states = {"switch.panel_ssr_thermostat_power": {"state": "unavailable"}}

    def get_state(entity_id: str) -> dict:
        return states[entity_id]

    mon = PanelLinkMonitor(
        notifier,
        entity_id="switch.panel_ssr_thermostat_power",
        get_state=get_state,
        params=PanelLinkParams(after_s=300),
    )
    state = ControlState()
    t0 = 1_000.0
    mon.evaluate(state, now=t0)
    mon.evaluate(state, now=t0 + 299)
    assert notifier.messages == []
    mon.evaluate(state, now=t0 + 300)
    assert len(notifier.messages) == 1
    assert notifier.messages[0]["event"] == "panel_link_down"
    mon.evaluate(state, now=t0 + 400)
    assert len(notifier.messages) == 1
    states["switch.panel_ssr_thermostat_power"] = {"state": "on"}
    mon.evaluate(state, now=t0 + 500)
    assert len(notifier.messages) == 2
    assert notifier.messages[1]["event"] == "panel_link_up"
    assert state.panel_link_alert_sent is False


def test_panel_link_empty_entity_noop():
    notifier = FakeNotifier()
    mon = PanelLinkMonitor(
        notifier,
        entity_id="",
        get_state=lambda _e: {"state": "unavailable"},
        params=PanelLinkParams(after_s=1),
    )
    state = ControlState()
    mon.evaluate(state, now=1_000.0)
    mon.evaluate(state, now=1_010.0)
    assert notifier.messages == []
