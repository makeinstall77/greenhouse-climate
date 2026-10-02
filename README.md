# greenhouse-climate

Лёгкий контроллер тёплого пола балкона по температуре воздуха greenhouse.
Работает **вне** HAOS и ESPHome: HA только как шина датчиков/актуатора и UI.

- Host: `greenhouse-climate.sweethome.local` / **`172.16.10.240`** (mgmt, LXC)
- Port: `8080`
- Code: this repo (GitHub `makeinstall77/greenhouse-climate`)

## Behaviour

1. Читает воздух (`AIR_ENTITY`), пол (`FLOOR_CLIMATE_ENTITY`), день/ночь (`SUN_ENTITY`).
2. Учит Δ = floor − air из HA history (ночь + установившийся режим), EMA в `state.json`.
3. Если control enabled — пишет setpoint пола к `air_target + Δ` (± margin), с rate-limit.
4. Днём при признаках солнечного нагрева снижает setpoint на `SOLAR_OFFSET_C`.
5. UI: HA helpers + Lovelace card. HTTP `/v1/control` — для будущего TG-бота.

## API

| Method | Path | Role |
|---|---|---|
| `GET` | `/health` | liveness |
| `GET` | `/v1/status` | state snapshot |
| `GET`/`PATCH`/`POST` | `/v1/control` | `{enabled, target_c}` |

Optional header: `X-Api-Key: $API_KEY`.

```bash
curl -sS http://172.16.10.240:8080/v1/status
curl -sS -X PATCH http://172.16.10.240:8080/v1/control \
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
| `TemperatureHistorySource` | `adapters/ha_history.py` |

Смена термостата/датчика = новый adapter + entity IDs в env.

## HA one-time setup

1. Long-lived access token (права на climate, sensor, sun, input_*, history).
2. Helpers: [`ha/helpers.yaml`](ha/helpers.yaml) → package / configuration.
3. Card: [`ha/lovelace-card.yaml`](ha/lovelace-card.yaml).

## Install on LXC (`172.16.10.240`)

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

Suggested CT: **1 vCPU / 128 MiB**, no Docker. DNS/DHCP: `greenhouse-climate` → `172.16.10.240`.

Start with `DRY_RUN=1` until status looks sane.

## Develop

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
HA_TOKEN=… DRY_RUN=1 STATE_PATH=./state.json python -m greenhouse_climate
```

## Out of scope (for now)

- Telegram bot (API ready)
- Humidity / illuminance adapters (ports reserved)
- Direct LocalTuya / ESPHome (possible as new adapters)
