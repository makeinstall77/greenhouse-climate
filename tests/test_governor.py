from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from greenhouse_climate.delta_model import DeltaModel, estimate_delta
from greenhouse_climate.governor import GovernorParams, SetpointGovernor
from greenhouse_climate.ports import TempPairSample
from greenhouse_climate.state_store import ControlState


@dataclass
class FakeAir:
    value: float
    fail: bool = False

    def read_c(self) -> float:
        if self.fail:
            raise RuntimeError("air unavailable")
        return self.value


@dataclass
class FakeFloor:
    current: float
    setpoint: float
    mode: str = "heat"
    writes: list[float] = field(default_factory=list)
    fail_current: bool = False
    fail_write: bool = False

    def read_current_c(self) -> float:
        if self.fail_current:
            raise RuntimeError("floor unavailable")
        return self.current

    def read_setpoint_c(self) -> Optional[float]:
        return self.setpoint

    def write_setpoint_c(self, temperature: float) -> None:
        if self.fail_write:
            raise RuntimeError("write failed")
        self.writes.append(temperature)
        self.setpoint = temperature

    def read_hvac_mode(self) -> Optional[str]:
        return self.mode

    def set_hvac_mode(self, mode: str) -> None:
        self.mode = mode


@dataclass
class FakeDay:
    day: bool = True
    fail: bool = False

    def is_day(self) -> bool:
        if self.fail:
            raise RuntimeError("sun unavailable")
        return self.day


@dataclass
class FakeHistory:
    samples: list[TempPairSample] = field(default_factory=list)

    def fetch_pairs(self, *, hours: float) -> list[TempPairSample]:
        return list(self.samples)


@dataclass
class FakeSolarHint:
    value: Optional[bool] = None
    fail: bool = False

    def likely_sun(self) -> Optional[bool]:
        if self.fail:
            raise RuntimeError("weather unavailable")
        return self.value


def _gov(
    air: float,
    floor: float,
    sp: float,
    *,
    day: bool = False,
    delta: float = 4.0,
    target: float = 22.0,
    solar_hint: Optional[FakeSolarHint] = None,
    air_src: Optional[FakeAir] = None,
    floor_dev: Optional[FakeFloor] = None,
    day_src: Optional[FakeDay] = None,
) -> tuple[SetpointGovernor, ControlState, FakeFloor]:
    floor_dev = floor_dev or FakeFloor(current=floor, setpoint=sp)
    history = FakeHistory()
    delta_model = DeltaModel(history, refresh_s=1e9, default_delta_c=delta)
    gov = SetpointGovernor(
        air_src or FakeAir(air),
        floor_dev,
        day_src or FakeDay(day),
        delta_model,
        GovernorParams(min_write_interval_s=0.0, setpoint_step_c=0.5),
        solar_hint=solar_hint,
    )
    state = ControlState(enabled=True, target_c=target, delta_c=delta)
    return gov, state, floor_dev


def test_heat_raises_setpoint_when_air_cold():
    gov, state, floor = _gov(air=18.0, floor=20.0, sp=20.0, day=False)
    result = gov.tick(state, now=1_000.0)
    assert result.mode == "heat"
    assert floor.writes
    assert floor.writes[-1] > 20.0


def test_hold_near_target_uses_delta():
    gov, state, floor = _gov(air=22.0, floor=26.0, sp=26.0, day=False, delta=4.0)
    result = gov.tick(state, now=1_000.0)
    assert result.mode == "hold"
    assert result.desired_c == 26.0


def test_disabled_does_not_write():
    gov, state, floor = _gov(air=10.0, floor=20.0, sp=20.0)
    state.enabled = False
    result = gov.tick(state, now=1_000.0)
    assert result.mode == "disabled"
    assert floor.writes == []


def test_cool_lowers_setpoint():
    gov, state, floor = _gov(air=25.0, floor=28.0, sp=28.0, day=False, delta=4.0)
    result = gov.tick(state, now=1_000.0)
    assert result.mode == "cool"
    assert floor.writes
    assert floor.writes[-1] < 28.0


def test_falling_blocks_cool_and_keeps_setpoint():
    # User scenario: air 25.9 → falling toward 25, floor sp 33.
    gov, state, floor = _gov(
        air=25.9, floor=30.0, sp=33.0, day=False, delta=4.0, target=25.0
    )
    state.last_air_c = 26.2
    state.last_air_at = 1_000.0
    result = gov.tick(state, now=1_200.0)
    assert result.falling_guess is True
    assert result.mode == "hold"
    assert floor.writes == []
    assert result.desired_c == 33.0


def test_cool_without_falling_still_lowers():
    gov, state, floor = _gov(
        air=25.6, floor=30.0, sp=33.0, day=False, delta=4.0, target=25.0
    )
    state.last_air_c = 25.5
    state.last_air_at = 1_000.0
    result = gov.tick(state, now=1_200.0)
    assert result.falling_guess is False
    assert result.mode == "cool"
    assert floor.writes
    assert floor.writes[-1] < 33.0


def test_heat_when_cold_even_if_falling():
    gov, state, floor = _gov(
        air=24.0, floor=28.0, sp=28.0, day=False, delta=4.0, target=25.0
    )
    state.last_air_c = 24.5
    state.last_air_at = 1_000.0
    result = gov.tick(state, now=1_200.0)
    assert result.falling_guess is True
    assert result.mode == "heat"
    assert floor.writes
    assert floor.writes[-1] >= 28.0


def test_trend_solar_beats_cloudy_weather():
    hint = FakeSolarHint(value=False)  # weather says cloudy
    gov, state, floor = _gov(
        air=23.0, floor=26.0, sp=26.0, day=True, delta=4.0, target=22.0, solar_hint=hint
    )
    state.last_air_c = 22.5
    state.last_air_at = 1_000.0
    # Rising without floor drive → solar_guess; above deadband → cool + offset.
    result = gov.tick(state, now=1_200.0)
    assert result.solar_guess is True
    assert result.likely_sun is False
    assert result.mode == "cool"
    # cool + solar: 22+4-1-1.5 = 23.5; without solar would be 25.0
    assert result.desired_c is not None
    assert result.desired_c <= 24.0


def test_falling_beats_sunny_weather():
    hint = FakeSolarHint(value=True)
    gov, state, floor = _gov(
        air=25.9, floor=30.0, sp=33.0, day=True, delta=4.0, target=25.0, solar_hint=hint
    )
    state.last_air_c = 26.5
    state.last_air_at = 1_000.0
    result = gov.tick(state, now=1_200.0)
    assert result.falling_guess is True
    assert result.likely_sun is True
    assert result.mode == "hold"
    assert floor.writes == []


def test_weather_early_hint_without_trend():
    hint = FakeSolarHint(value=True)
    gov, state, floor = _gov(
        air=23.0, floor=28.0, sp=28.0, day=True, delta=4.0, target=22.0, solar_hint=hint
    )
    # No prior air sample → no trend; likely_sun True + air above target → soft hint.
    result = gov.tick(state, now=1_000.0)
    assert result.solar_guess is False
    assert result.likely_sun is True
    assert result.mode == "cool"
    # cool + solar: 22+4-1-1.5 = 23.5
    assert result.desired_c == 23.5


def test_degraded_when_air_missing():
    air = FakeAir(20.0, fail=True)
    gov, state, floor = _gov(
        air=20.0, floor=24.0, sp=24.0, air_src=air
    )
    result = gov.tick(state, now=1_000.0)
    assert result.mode == "degraded"
    assert "air" in result.degraded
    assert floor.writes == []


def test_degraded_when_floor_missing():
    floor = FakeFloor(current=24.0, setpoint=24.0, fail_current=True)
    gov, state, _ = _gov(air=20.0, floor=24.0, sp=24.0, floor_dev=floor)
    result = gov.tick(state, now=1_000.0)
    assert result.mode == "degraded"
    assert "floor" in result.degraded
    assert floor.writes == []


def test_weather_failure_does_not_abort_tick():
    hint = FakeSolarHint(fail=True)
    gov, state, floor = _gov(
        air=18.0, floor=20.0, sp=20.0, day=False, solar_hint=hint
    )
    result = gov.tick(state, now=1_000.0)
    assert result.mode == "heat"
    assert "weather" in result.degraded
    assert floor.writes


def test_write_failure_does_not_abort_tick():
    floor = FakeFloor(current=20.0, setpoint=20.0, fail_write=True)
    gov, state, _ = _gov(air=18.0, floor=20.0, sp=20.0, floor_dev=floor)
    result = gov.tick(state, now=1_000.0)
    assert result.mode == "heat"
    assert result.written is False
    assert "write" in result.degraded


def test_sun_failure_assumes_night_boost():
    day = FakeDay(day=True, fail=True)
    gov, state, floor = _gov(
        air=18.0, floor=20.0, sp=20.0, day_src=day, delta=4.0, target=22.0
    )
    result = gov.tick(state, now=1_000.0)
    assert result.is_day is False
    assert "sun" in result.degraded
    assert result.mode == "heat"
    assert floor.writes


def test_estimate_delta_prefers_night_steady():
    samples = [
        TempPairSample(1, 20.0, 24.0, 24.0, False),
        TempPairSample(2, 20.0, 24.5, 24.0, False),
        TempPairSample(3, 20.0, 24.0, 24.0, False),
        TempPairSample(4, 20.0, 24.2, 24.0, False),
        TempPairSample(5, 20.0, 24.1, 24.0, False),
        TempPairSample(6, 28.0, 26.0, 30.0, True),
        TempPairSample(7, 30.0, 26.0, 30.0, True),
    ]
    est = estimate_delta(samples)
    assert est is not None
    mean, n = est
    assert n >= 5
    assert 3.5 < mean < 5.0
