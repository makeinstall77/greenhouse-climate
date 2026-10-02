# LXC 118 — greenhouse-climate

Mgmt: **172.16.10.240** (`greenhouse-climate.sweethome.local`). Next `/10` after haos `.230`.

Suggested on PVE (adjust template/storage):

```bash
# From CT 104 template or ubuntu cloud image — example only
pct create 118 local:vztmpl/ubuntu-26.04-standard_...tar.zst \
  --hostname greenhouse-climate \
  --memory 128 --cores 1 --swap 0 \
  --net0 name=eth0,bridge=vmbr0,ip=172.16.10.240/24,gw=172.16.10.1 \
  --rootfs local-lvm:4 \
  --unprivileged 1

# gwng dhcpd: host greenhouse-climate { fixed-address 172.16.10.240; ... }
# ns1/ns2 Pi-hole dns.hosts: 172.16.10.240 greenhouse-climate.sweethome.local
```

Inside CT after first boot:

```bash
apt-get update && apt-get install -y python3 git
# deploy from git clone or scp from dev machine
./deploy/install.sh
# set HA_TOKEN in /etc/greenhouse-climate.env, DRY_RUN=1 first
systemctl restart greenhouse-climate
curl -sS http://127.0.0.1:8080/health
```
