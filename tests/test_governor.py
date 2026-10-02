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

    def read_c(self) -> float:
        return self.value


@dataclass
class FakeFloor:
    current: float
    setpoint: float
    mode: str = "heat"
    writes: list[float] = field(default_factory=list)

    def read_current_c(self) -> float:
        return self.current

    def read_setpoint_c(self) -> Optional[float]:
        return self.setpoint

    def write_setpoint_c(self, temperature: float) -> None:
        self.writes.append(temperature)
        self.setpoint = temperature

    def read_hvac_mode(self) -> Optional[str]:
        return self.mode

    def set_hvac_mode(self, mode: str) -> None:
        self.mode = mode


@dataclass
class FakeDay:
    day: bool = True

    def is_day(self) -> bool:
        return self.day


@dataclass
class FakeHistory:
    samples: list[TempPairSample] = field(default_factory=list)

    def fetch_pairs(self, *, hours: float) -> list[TempPairSample]:
        return list(self.samples)


def _gov(
    air: float,
    floor: float,
    sp: float,
    *,
    day: bool = False,
    delta: float = 4.0,
) -> tuple[SetpointGovernor, ControlState, FakeFloor]:
    floor_dev = FakeFloor(current=floor, setpoint=sp)
    history = FakeHistory()
    delta_model = DeltaModel(history, refresh_s=1e9, default_delta_c=delta)
    gov = SetpointGovernor(
        FakeAir(air),
        floor_dev,
        FakeDay(day),
        delta_model,
        GovernorParams(min_write_interval_s=0.0, setpoint_step_c=0.5),
    )
    state = ControlState(enabled=True, target_c=22.0, delta_c=delta)
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
    # desired ~ 22+4=26, already at floor → may skip write
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


def test_estimate_delta_prefers_night_steady():
    samples = [
        TempPairSample(1, 20.0, 24.0, 24.0, False),
        TempPairSample(2, 20.0, 24.5, 24.0, False),
        TempPairSample(3, 20.0, 24.0, 24.0, False),
        TempPairSample(4, 20.0, 24.2, 24.0, False),
        TempPairSample(5, 20.0, 24.1, 24.0, False),
        # sunny outliers
        TempPairSample(6, 28.0, 26.0, 30.0, True),
        TempPairSample(7, 30.0, 26.0, 30.0, True),
    ]
    est = estimate_delta(samples)
    assert est is not None
    mean, n = est
    assert n >= 5
    assert 3.5 < mean < 5.0
