"""HTTP API + control loop (stdlib only)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import urlparse

from greenhouse_climate.adapters import (
    HaAirTemperature,
    HaDayNight,
    HaFloorThermostat,
    HaSunWeatherHint,
    HaTemperatureHistory,
)
from greenhouse_climate.config import Settings, load_env_file
from greenhouse_climate.delta_model import DeltaModel
from greenhouse_climate.governor import GovernorParams, SetpointGovernor
from greenhouse_climate.ha_rest import HaRest
from greenhouse_climate.state_store import StateStore

log = logging.getLogger("greenhouse_climate")


class Controller:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = StateStore(settings.state_path)
        self.store.load()
        self.ha = HaRest(settings.ha_url, settings.ha_token)
        self.air = HaAirTemperature(self.ha, settings.air_entity)
        self.floor = HaFloorThermostat(
            self.ha, settings.floor_climate_entity, dry_run=settings.dry_run
        )
        self.day_night = HaDayNight(self.ha, settings.sun_entity)
        self.solar_hint = HaSunWeatherHint(
            self.ha, self.day_night, settings.weather_entity
        )
        history = HaTemperatureHistory(
            self.ha,
            air_entity=settings.air_entity,
            floor_climate_entity=settings.floor_climate_entity,
            sun_entity=settings.sun_entity,
        )
        self.delta = DeltaModel(
            history,
            hours=settings.delta_hours,
            refresh_s=settings.delta_refresh_s,
            ema_alpha=settings.delta_ema_alpha,
            steady_tol_c=settings.delta_steady_tol_c,
            default_delta_c=settings.default_delta_c,
        )
        self.governor = SetpointGovernor(
            self.air,
            self.floor,
            self.day_night,
            self.delta,
            GovernorParams(
                deadband_c=settings.deadband_c,
                floor_min_c=settings.floor_min_c,
                floor_max_c=settings.floor_max_c,
                setpoint_step_c=settings.setpoint_step_c,
                min_write_interval_s=settings.min_write_interval_s,
                boost_day_c=settings.boost_day_c,
                boost_night_c=settings.boost_night_c,
                margin_c=settings.margin_c,
                solar_offset_c=settings.solar_offset_c,
                trend_fall_c=settings.trend_fall_c,
                trend_rise_c=settings.trend_rise_c,
                trend_window_s=settings.trend_window_s,
                trend_min_s=settings.trend_min_s,
                trend_max_s=settings.trend_max_s,
            ),
            solar_hint=self.solar_hint,
        )
        self._lock = threading.RLock()
        self._last_tick: Optional[dict[str, Any]] = None
        self._stop = threading.Event()

    def status(self) -> dict[str, Any]:
        with self._lock:
            st = self.store.state
            out = {
                "ok": True,
                "enabled": st.enabled,
                "target_c": st.target_c,
                "mode": st.mode,
                "delta_c": self.delta.effective_delta(st),
                "delta_samples": st.delta_samples,
                "delta_updated_at": st.delta_updated_at,
                "solar_guess": st.solar_guess,
                "falling_guess": st.falling_guess,
                "likely_sun": st.likely_sun,
                "degraded": list(st.degraded),
                "last_setpoint_c": st.last_setpoint_c,
                "dry_run": self.settings.dry_run,
                "last_tick": self._last_tick,
            }
            return out

    def set_control(self, *, enabled: Optional[bool] = None, target_c: Optional[float] = None) -> dict[str, Any]:
        with self._lock:
            st = self.store.state
            if enabled is not None:
                st.enabled = bool(enabled)
            if target_c is not None:
                st.target_c = float(target_c)
            self.store.save()
            self._push_helpers()
            return self.status()

    def sync_helpers_from_ha(self) -> None:
        """Pull control settings from HA input_* helpers (UI card)."""
        with self._lock:
            st = self.store.state
            try:
                en = self.ha.get_state(self.settings.helper_enabled)
                en_state = en.get("state")
                if en_state in {"on", "off"}:
                    st.enabled = en_state == "on"
                    st.helper_enabled_seen = en_state
                tgt = self.ha.get_state(self.settings.helper_target)
                tgt_val = float(tgt.get("state"))
                st.target_c = tgt_val
                st.helper_target_seen = tgt_val
            except Exception as exc:
                log.warning("helper sync failed: %s", exc)

    def _push_helpers(self) -> None:
        st = self.store.state
        try:
            en = "on" if st.enabled else "off"
            self.ha.call_service(
                "input_boolean",
                "turn_on" if st.enabled else "turn_off",
                {"entity_id": self.settings.helper_enabled},
            )
            self.ha.call_service(
                "input_number",
                "set_value",
                {"entity_id": self.settings.helper_target, "value": st.target_c},
            )
            st.helper_enabled_seen = en
            st.helper_target_seen = st.target_c
        except Exception as exc:
            log.warning("helper push failed: %s", exc)

    def loop_once(self) -> None:
        self.sync_helpers_from_ha()
        with self._lock:
            result = self.governor.tick(self.store.state)
            self._last_tick = {
                "mode": result.mode,
                "air_c": result.air_c,
                "floor_c": result.floor_c,
                "setpoint_c": result.setpoint_c,
                "desired_c": result.desired_c,
                "written": result.written,
                "solar_guess": result.solar_guess,
                "falling_guess": result.falling_guess,
                "likely_sun": result.likely_sun,
                "degraded": list(result.degraded),
                "is_day": result.is_day,
                "delta_c": result.delta_c,
                "ts": time.time(),
            }
            self.store.save()

    def run_loop(self) -> None:
        log.info("control loop started (interval=%.0fs dry_run=%s)", self.settings.loop_seconds, self.settings.dry_run)
        while not self._stop.is_set():
            try:
                self.loop_once()
            except Exception:
                log.exception("control loop tick failed")
            self._stop.wait(self.settings.loop_seconds)

    def stop(self) -> None:
        self._stop.set()


def make_handler(controller: Controller, api_key: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            log.debug("http: " + fmt, *args)

        def _auth_ok(self) -> bool:
            if not api_key:
                return True
            return self.headers.get("X-Api-Key") == api_key

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {}
            return data if isinstance(data, dict) else {}

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/health":
                self._send(200, {"ok": True})
                return
            if not self._auth_ok():
                self._send(401, {"ok": False, "error": "unauthorized"})
                return
            if path == "/v1/status":
                self._send(200, controller.status())
                return
            if path == "/v1/control":
                self._send(200, controller.status())
                return
            self._send(404, {"ok": False, "error": "not found"})

        def do_PATCH(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if not self._auth_ok():
                self._send(401, {"ok": False, "error": "unauthorized"})
                return
            if path != "/v1/control":
                self._send(404, {"ok": False, "error": "not found"})
                return
            data = self._read_json()
            enabled = data.get("enabled")
            target = data.get("target_c", data.get("target"))
            if enabled is not None and not isinstance(enabled, bool):
                self._send(400, {"ok": False, "error": "enabled must be bool"})
                return
            if target is not None:
                try:
                    target = float(target)
                except (TypeError, ValueError):
                    self._send(400, {"ok": False, "error": "target_c must be number"})
                    return
            self._send(200, controller.set_control(enabled=enabled, target_c=target))

        def do_POST(self) -> None:  # noqa: N802
            # Allow POST as alias for PATCH /v1/control (TG bots often prefer POST)
            if urlparse(self.path).path == "/v1/control":
                self.do_PATCH()
                return
            self._send(404, {"ok": False, "error": "not found"})

    return Handler


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    env_path = os.environ.get("GREENHOUSE_CLIMATE_ENV", "/etc/greenhouse-climate.env")
    load_env_file(env_path)
    # Also allow local .env during development
    load_env_file(os.path.join(os.getcwd(), ".env"))

    settings = Settings.from_env()
    controller = Controller(settings)

    handler = make_handler(controller, settings.api_key)
    server = ThreadingHTTPServer((settings.listen, settings.port), handler)
    loop_thread = threading.Thread(target=controller.run_loop, name="control-loop", daemon=True)
    loop_thread.start()

    log.info("listening on %s:%s", settings.listen, settings.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        controller.stop()
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
