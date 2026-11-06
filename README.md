# greenhouse-climate

Лёгкий контроллер тёплого пола балкона по температуре воздуха greenhouse.
Работает **вне** HAOS и ESPHome: HA только как шина датчиков/актуатора и UI.

- Host: `greenhouse-climate.sweethome.local` / **`172.16.10.170`** (mgmt, LXC)
- Port: `8080`
- Code: this repo (GitHub `makeinstall77/greenhouse-climate`)

## Behaviour

1. Читает воздух (`AIR_ENTITY`), пол (`FLOOR_CLIMATE_ENTITY`), день/ночь (`SUN_ENTITY`), опционально погоду (`WEATHER_ENTITY`, Met.no).
2. Учит Δ = floor − air из HA history (ночь + установившийся режим), EMA в `state.json`.
3. Если control enabled — пишет setpoint пола к `air_target + Δ` (± margin), с rate-limit.
4. **Тренд воздуха приоритетен** (°C/ч за `TREND_WINDOW_S`, по умолчанию 20 мин): при падении **никогда** не снижает уставку пола (`falling_guess`); при росте без нагрева пола снижает на `SOLAR_OFFSET_C` (`solar_guess`).
5. Sun + weather — вторичный early hint (`likely_sun`); при конфликте с трендом побеждает тренд (ночное падение / открытое окно / ошибка прогноза).
6. Недоступность air/floor/sun/weather/записи → `degraded` / warning, tick не падает.
7. Мониторинг (в том же loop): floor недоступен / write|hvac / воздух ниже цели — после **30 мин** непрерывного отказа шлёт в tg-notifier (`NOTIFIER_URL`), при восстановлении — «ок». Краткие обрывы связи не алертят.
8. При floor/write_hvac алерте — опциональный **autoreboot** термостата через ESPHome `button.panel_ssr_thermostat_reboot` (knobs в HA helpers).
9. UI: HA helpers + Lovelace card. HTTP `/v1/control` — для будущего TG-бота.

## API

| Method | Path | Role |
|---|---|---|
| `GET` | `/health` | liveness |
| `GET` | `/v1/status` | state snapshot (`falling_guess`, `likely_sun`, `degraded`, …) |
| `GET`/`PATCH`/`POST` | `/v1/control` | `{enabled, target_c}` |

Optional header: `X-Api-Key: $API_KEY`.

```bash
curl -sS http://172.16.10.170:8080/v1/status
curl -sS -X PATCH http://172.16.10.170:8080/v1/control \
  -H 'Content-Type: application/json' \
  -d '{"enabled":true,"target_c":22}'
```

## Device ports

Governor depends only on ports in `ports.py`:

| Port | Default adapter |
|---|---|
| `AirTemperatureSource` | `adapters/ha_air.py` |
| `FloorThermostat` | `adapters/ha_floor.py` |
| `DayNightSource` | `adapters/ha_sun.py` |
| `SolarHintSource` | `adapters/ha_weather.py` (sun + `weather.forecast_home_assistant`) |
| `TemperatureHistorySource` | `adapters/ha_history.py` |

Смена термостата/датчика = новый adapter + entity IDs в env. Пустой `WEATHER_ENTITY=` отключает weather hint.

## HA one-time setup

1. Long-lived access token (права на climate, sensor, sun, weather, input_*, history).
2. Helpers: [`ha/helpers.yaml`](ha/helpers.yaml) → package / configuration.
3. Card: [`ha/lovelace-card.yaml`](ha/lovelace-card.yaml).

## Install on LXC (`172.16.10.170`)

```bash
sudo apt-get install -y python3
sudo mkdir -p /usr/local/lib/greenhouse-climate /var/lib/greenhouse-climate
sudo cp -a src/greenhouse_climate /usr/local/lib/greenhouse-climate/
# PYTHONPATH or sitecustomize — simplest:
echo '/usr/local/lib/greenhouse-climate' | sudo tee /usr/lib/python3/dist-packages/greenhouse-climate.pth
sudo cp deploy/greenhouse-climate.service /etc/systemd/system/
sudo cp deploy/config.example.env /etc/greenhouse-climate.env
sudo chmod 600 /etc/greenhouse-climate.env
# edit HA_TOKEN
sudo systemctl daemon-reload
sudo systemctl enable --now greenhouse-climate.service
```

Suggested CT: **1 vCPU / 128 MiB**, no Docker. DNS/DHCP: `greenhouse-climate` → `172.16.10.170`.

Start with `DRY_RUN=1` until status looks sane.

## Develop

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
HA_TOKEN=… DRY_RUN=1 STATE_PATH=./state.json python -m greenhouse_climate
```

## Panel SSR (ESP32-C3, 4 lines)

Щитовые реле: 3×1ф + 1×3ф (киллсвитч освещения). ESP32-C3 + 4ch SSR рвут **только фазы катушек** (не силовые 25 А / L1–L3).

| CH | GPIO | HA entities | Назначение |
|----|------|-------------|------------|
| 1 | 4 | `switch.panel_ssr_line1_power`, `button.panel_ssr_line1_reboot` | 1ф |
| 2 | 5 | `switch.panel_ssr_line2_power`, `button.panel_ssr_line2_reboot` | 1ф |
| 3 | 6 | `switch.panel_ssr_thermostat_power`, `button.panel_ssr_thermostat_reboot` | термостат |
| 4 | 7 | `switch.panel_ssr_lighting_power`, `button.panel_ssr_lighting_reboot` | 3ф свет |

Fail-safe: каналы нормально ON; нет питания ESP/SSR → катушки OFF. Reboot = OFF 15 с → ON. Модуль SSR — **low-trigger** (`inverted: true`: ON → GPIO LOW).

Firmware: [`esphome/panel-ssr.yaml`](esphome/panel-ssr.yaml) + [`esphome/secrets.yaml.example`](esphome/secrets.yaml.example) (`notifier_url`).

### Notifier events (panel)

| Event | Source | Text |
|-------|--------|------|
| `panel_ch_on` / `panel_ch_off` | ESPHome | включён / выключен канал |
| `panel_ch_reboot` | ESPHome (конец pulse) | перезагружен канал |
| `panel_link_down` / `panel_link_up` | greenhouse-climate | нет связи / снова в сети |

### Autoreboot helpers

В [`ha/helpers.yaml`](ha/helpers.yaml):

- `input_boolean.greenhouse_thermostat_autoreboot`
- `input_number.greenhouse_thermostat_reboot_cooldown_min` (default 120)
- `input_number.greenhouse_thermostat_reboot_max_per_day` (default 2)

Env wiring: `REBOOT_ENTITY`, `PANEL_LINK_ENTITY`, `PANEL_LINK_AFTER_S`, `HELPER_AUTOREBOOT`, `HELPER_REBOOT_COOLDOWN`, `HELPER_REBOOT_MAX`. Пустой `REBOOT_ENTITY=` отключает press.

## Out of scope (for now)

- Telegram bot (API ready)
- Local illuminance sensor (port reserved; weather is interim proxy)
- Humidity adapters (ports reserved)
