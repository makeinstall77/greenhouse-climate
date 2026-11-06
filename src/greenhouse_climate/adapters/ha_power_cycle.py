"""Home Assistant button.press power cycler."""

from __future__ import annotations

import logging
from typing import Optional

from greenhouse_climate.ha_rest import HaRest

log = logging.getLogger("greenhouse_climate.power_cycle")


class NullPowerCycler:
    def reboot(self) -> bool:
        return False


class HaPowerCycler:
    """Press an ESPHome/HA ``button.*`` entity to start a reboot script."""

    def __init__(
        self,
        ha: HaRest,
        entity_id: str,
        *,
        dry_run: bool = False,
    ) -> None:
        self.ha = ha
        self.entity_id = (entity_id or "").strip()
        self.dry_run = dry_run

    def reboot(self) -> bool:
        if not self.entity_id:
            return False
        if self.dry_run:
            log.info("dry_run: would press %s", self.entity_id)
            return True
        try:
            self.ha.call_service(
                "button",
                "press",
                {"entity_id": self.entity_id},
            )
            log.warning("power cycle requested via %s", self.entity_id)
            return True
        except Exception as exc:
            log.warning("power cycle failed (%s): %s", self.entity_id, exc)
            return False


def make_power_cycler(
    ha: HaRest,
    entity_id: Optional[str],
    *,
    dry_run: bool = False,
) -> HaPowerCycler | NullPowerCycler:
    if not entity_id or not str(entity_id).strip():
        return NullPowerCycler()
    return HaPowerCycler(ha, str(entity_id).strip(), dry_run=dry_run)
