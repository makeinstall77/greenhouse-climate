#!/usr/bin/env bash
# Install onto the greenhouse-climate LXC (run on the CT as root).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

apt-get install -y python3
mkdir -p /usr/local/lib/greenhouse-climate /var/lib/greenhouse-climate
rm -rf /usr/local/lib/greenhouse-climate/greenhouse_climate
cp -a "$ROOT/src/greenhouse_climate" /usr/local/lib/greenhouse-climate/
echo '/usr/local/lib/greenhouse-climate' > /usr/lib/python3/dist-packages/greenhouse-climate.pth

install -m 644 "$ROOT/deploy/greenhouse-climate.service" /etc/systemd/system/greenhouse-climate.service
if [[ ! -f /etc/greenhouse-climate.env ]]; then
  install -m 600 "$ROOT/deploy/config.example.env" /etc/greenhouse-climate.env
  echo "Created /etc/greenhouse-climate.env — set HA_TOKEN before start"
fi

systemctl daemon-reload
systemctl enable greenhouse-climate.service
echo "Done. Edit /etc/greenhouse-climate.env then: systemctl restart greenhouse-climate"
