"""Load settings from environment / env file."""

from __future__ import annotations

import os
from dataclasses import dataclass


def load_env_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ.setdefault(key, value)


def _f(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _i(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _b(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    ha_url: str
    ha_token: str
    listen: str
    port: int
    api_key: str
    state_path: str
    loop_seconds: float

    air_entity: str
    floor_climate_entity: str
    sun_entity: str
    weather_entity: str
    helper_enabled: str
    helper_target: str

    deadband_c: float
    floor_min_c: float
    floor_max_c: float
    setpoint_step_c: float
    min_write_interval_s: float
    boost_day_c: float
    boost_night_c: float
    margin_c: float
    solar_offset_c: float
    trend_fall_c: float
    trend_rise_c: float
    trend_window_s: float
    trend_min_s: float
    trend_max_s: float
    default_delta_c: float
    delta_hours: float
    delta_refresh_s: float
    delta_ema_alpha: float
    delta_steady_tol_c: float
    dry_run: bool

    @classmethod
    def from_env(cls) -> "Settings":
        ha_url = os.environ.get("HA_URL", "http://172.16.10.230:8123").rstrip("/")
        ha_token = os.environ.get("HA_TOKEN", "")
        if not ha_token:
            raise SystemExit("HA_TOKEN is required")
        return cls(
            ha_url=ha_url,
            ha_token=ha_token,
            listen=os.environ.get("LISTEN", "0.0.0.0"),
            port=_i("PORT", 8080),
            api_key=os.environ.get("API_KEY", ""),
            state_path=os.environ.get(
                "STATE_PATH", "/var/lib/greenhouse-climate/state.json"
            ),
            loop_seconds=_f("LOOP_SECONDS", 90.0),
            air_entity=os.environ.get(
                "AIR_ENTITY", "sensor.greenhouse_bmp280_temperature"
            ),
            floor_climate_entity=os.environ.get(
                "FLOOR_CLIMATE_ENTITY",
                "climate.smart_thermostat_local_teplyi_pol_local",
            ),
            sun_entity=os.environ.get("SUN_ENTITY", "sun.sun"),
            weather_entity=os.environ.get(
                "WEATHER_ENTITY", "weather.forecast_home_assistant"
            ),
            helper_enabled=os.environ.get(
                "HELPER_ENABLED", "input_boolean.greenhouse_climate_enabled"
            ),
            helper_target=os.environ.get(
                "HELPER_TARGET", "input_number.greenhouse_climate_target"
            ),
            deadband_c=_f("DEADBAND_C", 0.5),
            floor_min_c=_f("FLOOR_MIN_C", 15.0),
            floor_max_c=_f("FLOOR_MAX_C", 35.0),
            setpoint_step_c=_f("SETPOINT_STEP_C", 0.5),
            min_write_interval_s=_f("MIN_WRITE_INTERVAL_S", 180.0),
            boost_day_c=_f("BOOST_DAY_C", 2.0),
            boost_night_c=_f("BOOST_NIGHT_C", 3.0),
            margin_c=_f("MARGIN_C", 1.0),
            solar_offset_c=_f("SOLAR_OFFSET_C", 1.5),
            # Trend thresholds are °C/hour over TREND_WINDOW_S (not per-tick Δ).
            trend_fall_c=_f("TREND_FALL_C", 0.1),
            trend_rise_c=_f("TREND_RISE_C", 0.8),
            trend_window_s=_f("TREND_WINDOW_S", 1200.0),
            trend_min_s=_f("TREND_MIN_S", 600.0),
            trend_max_s=_f("TREND_MAX_S", 7200.0),
            default_delta_c=_f("DEFAULT_DELTA_C", 4.0),
            delta_hours=_f("DELTA_HOURS", 48.0),
            delta_refresh_s=_f("DELTA_REFRESH_S", 1800.0),
            delta_ema_alpha=_f("DELTA_EMA_ALPHA", 0.2),
            delta_steady_tol_c=_f("DELTA_STEADY_TOL_C", 1.0),
            dry_run=_b("DRY_RUN", False),
        )
