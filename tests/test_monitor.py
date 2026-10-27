from __future__ import annotations

from dataclasses import dataclass, field

from greenhouse_climate.governor import TickResult
from greenhouse_climate.monitor import (
    KEY_COLD,
    KEY_FLOOR,
    KEY_WRITE_HVAC,
    AlertMonitor,
    MonitorParams,
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


def _mon(after: float = 1800.0) -> tuple[AlertMonitor, FakeNotifier, ControlState]:
    notifier = FakeNotifier()
    mon = AlertMonitor(notifier, MonitorParams(alert_after_s=after, deadband_c=0.5))
    state = ControlState(enabled=True, target_c=22.0)
    return mon, notifier, state


def test_flap_under_threshold_sends_nothing():
    mon, notifier, state = _mon()
    t0 = 1_000.0
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0)
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 600)
    mon.evaluate(state, _tick(), now=t0 + 700)  # recovered early
    assert notifier.messages == []
    assert KEY_FLOOR not in state.alert_fault_since


def test_continuous_floor_fault_sends_once():
    mon, notifier, state = _mon()
    t0 = 1_000.0
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0)
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 1799)
    assert notifier.messages == []
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 1800)
    assert len(notifier.messages) == 1
    assert notifier.messages[0]["event"] == "floor_down"
    assert "недоступен" in notifier.messages[0]["text"]
    # subsequent ticks: no duplicate
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 2000)
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 3000)
    assert len(notifier.messages) == 1


def test_recovery_sends_up():
    mon, notifier, state = _mon()
    t0 = 1_000.0
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0)
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 1800)
    mon.evaluate(state, _tick(), now=t0 + 1900)
    assert len(notifier.messages) == 2
    assert notifier.messages[1]["event"] == "floor_up"
    assert "снова доступен" in notifier.messages[1]["text"]
    assert KEY_FLOOR not in state.alert_sent


def test_write_hvac_and_cold_are_separate_events():
    mon, notifier, state = _mon()
    t0 = 1_000.0
    cold_tick = _tick(air_c=18.0, degraded=["hvac", "write"])
    mon.evaluate(state, cold_tick, now=t0)
    mon.evaluate(state, cold_tick, now=t0 + 1800)
    events = {m["event"] for m in notifier.messages}
    assert events == {"write_hvac_down", "cold_down"}
    assert any("HVAC не heat" in m["text"] for m in notifier.messages)
    assert any("запись уставки" in m["text"] for m in notifier.messages)
    assert any("18.0°C" in m["text"] for m in notifier.messages)


def test_disabled_clears_timers_without_alert():
    mon, notifier, state = _mon()
    t0 = 1_000.0
    mon.evaluate(state, _tick(degraded=["floor"], air_c=10.0), now=t0)
    assert KEY_FLOOR in state.alert_fault_since
    state.enabled = False
    mon.evaluate(state, _tick(degraded=["floor"], air_c=10.0), now=t0 + 1800)
    assert notifier.messages == []
    assert KEY_FLOOR not in state.alert_fault_since
    assert KEY_COLD not in state.alert_fault_since


def test_enqueue_failure_retries_next_tick():
    mon, notifier, state = _mon()
    notifier.fail = True
    t0 = 1_000.0
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0)
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 1800)
    assert notifier.messages == []
    assert not state.alert_sent.get(KEY_FLOOR)
    notifier.fail = False
    mon.evaluate(state, _tick(degraded=["floor"]), now=t0 + 1810)
    assert len(notifier.messages) == 1
    assert state.alert_sent.get(KEY_FLOOR) is True


def test_cold_not_triggered_inside_deadband():
    mon, notifier, state = _mon()
    t0 = 1_000.0
    # target 22, deadband 0.5 → 21.6 is still ok
    mon.evaluate(state, _tick(air_c=21.6), now=t0)
    mon.evaluate(state, _tick(air_c=21.6), now=t0 + 1800)
    assert notifier.messages == []
    assert KEY_COLD not in state.alert_fault_since
    assert KEY_WRITE_HVAC not in state.alert_fault_since
