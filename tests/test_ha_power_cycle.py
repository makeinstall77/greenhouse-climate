from __future__ import annotations

from greenhouse_climate.adapters.ha_power_cycle import (
    HaPowerCycler,
    NullPowerCycler,
    make_power_cycler,
)


class FakeHa:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.fail = False

    def call_service(self, domain: str, service: str, data: dict):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((domain, service, data))
        return None


def test_null_cycler():
    assert NullPowerCycler().reboot() is False
    assert make_power_cycler(FakeHa(), "").reboot() is False
    assert make_power_cycler(FakeHa(), "  ").reboot() is False


def test_ha_cycler_presses_button():
    ha = FakeHa()
    c = HaPowerCycler(ha, "button.panel_ssr_thermostat_reboot")
    assert c.reboot() is True
    assert ha.calls == [
        ("button", "press", {"entity_id": "button.panel_ssr_thermostat_reboot"})
    ]


def test_ha_cycler_dry_run():
    ha = FakeHa()
    c = HaPowerCycler(ha, "button.panel_ssr_thermostat_reboot", dry_run=True)
    assert c.reboot() is True
    assert ha.calls == []


def test_ha_cycler_failure():
    ha = FakeHa()
    ha.fail = True
    c = HaPowerCycler(ha, "button.panel_ssr_thermostat_reboot")
    assert c.reboot() is False
