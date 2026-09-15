# Deployment on Proxmox homelab

Deploy vulcan-notify as a Docker container inside a dedicated "tools" LXC on Proxmox.

## 1. Create the tools LXC

On the Proxmox host (ssh root@pve):

```bash
# Download Ubuntu 24.04 template
pveam update
pveam download local ubuntu-24.04-standard_24.04-2_amd64.tar.zst

# Create LXC (adjust VMID as needed)
pct create 103 local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst \
  --hostname tools \
  --cores 2 \
  --memory 1024 \
  --swap 512 \
  --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1

# Start and enter
pct start 103
pct enter 103
```

Resources: 2 cores, 1GB RAM, 8GB disk. `nesting=1` is required for Docker inside LXC.

## 2. Install Docker

Inside the LXC:

```bash
apt-get update && apt-get upgrade -y
apt-get install -y ca-certificates curl gnupg git

# Docker official repo
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable docker
docker run --rm hello-world
```

## 3. Deploy vulcan-notify

```bash
cd /opt
git clone <repo-url> vulcan-notify
cd vulcan-notify

# Create data dir and config
mkdir -p data
cp .env.example .env
```

Edit `.env` with your credentials:

```
VULCAN_LOGIN=your.email@example.com
VULCAN_PASSWORD=your_password
LOG_LEVEL=INFO
```

Note: do NOT set `CALENDAR_MAP` - calendar integration requires macOS and is disabled by default.

Build and start:

```bash
docker compose up -d --build
docker compose logs -f
```

The first sync will auto-login via headless Chromium using your credentials and save the session to `data/session.json`. Subsequent syncs reuse the session until it expires, then re-authenticate automatically.

## 4. Remote access

LXC 103 is **not** a tailnet node and does not run Tailscale. It is reached through the PVE host, which is one, using `pct exec`. The `tools.dwelf-forel.ts.net` name older notes refer to never resolved — do not try to reach the LXC directly.

Set up SSH key auth to the PVE host instead:

```bash
# From your Mac
ssh-copy-id root@pve.dwelf-forel.ts.net

# Then reach the LXC through it
ssh root@pve.dwelf-forel.ts.net "pct exec 103 -- sh -lc 'cd /opt/vulcan-notify && docker compose ps'"
```

## 5. GitHub deploy key

Generate a deploy key on the LXC for read-only GitHub access:

```bash
ssh-keygen -t ed25519 -C "vulcan-notify-deploy" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

Add the public key as a read-only deploy key at `github.com/kintecus/vulcan-notify/settings/keys`.

## 6. Management

Two containers run off one image (see `docker-compose.yml`): **`vulcan-api`** serves port 8585, **`vulcan-sync`** runs the poll loop. There is no `vulcan-notify` service any more — it was split on 2026-09-15 so each process gets its own restart supervision.

```bash
# View logs — pick the container, the two are very different in volume
docker compose logs --tail 50 vulcan-sync   # sync activity
docker compose logs --tail 50 vulcan-api    # HTTP server

# Restart (e.g., after .env changes)
docker compose restart

# Update to latest
git pull && docker compose up -d --build --remove-orphans

# Run a one-off sync without disturbing the loop
docker compose run --rm vulcan-sync uv run vulcan-notify sync

# Check session validity
docker compose run --rm vulcan-sync uv run vulcan-notify test
```

`--remove-orphans` matters on any manual `up`: without it a leftover container from the pre-split layout keeps port 8585 bound and the new API cannot start.

## 7. Auto-deploy (CI/CD)

**Pushing to `main` deploys.** A systemd timer on the LXC polls `origin/main` every 5 minutes and rebuilds if there are new commits, so a push reaches production within ~5 minutes with no action from you. There is no GitHub Actions workflow — this timer is the whole CI/CD path. Deploy notifications go to ntfy.sh.

`vulcan-deploy.sh` builds the image before recreating the container and rolls `HEAD` back on a build failure, so a broken build never takes the service down and never leaves the timer dormant on undeployed code.

### Install the systemd units

```bash
ln -sf /opt/vulcan-notify/deploy/vulcan-deploy.service /etc/systemd/system/
ln -sf /opt/vulcan-notify/deploy/vulcan-deploy.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now vulcan-deploy.timer
```

### Verify

```bash
# Timer is active
systemctl list-timers vulcan-deploy*

# Manual trigger
systemctl start vulcan-deploy.service
journalctl -u vulcan-deploy --no-pager -n 20
```

### Skipping the 5-minute wait

Push to GitHub, then:

```bash
./deploy.sh
```

This only shortcuts the timer's polling interval; it is not the sole deploy path. It SSHes to the **PVE host** and `pct exec`s into LXC 103 to run pull + rebuild, because the LXC itself is not reachable over the tailnet. Override with `PVE_HOST=<host> ./deploy.sh`.

## 8. Monitoring

```bash
# Deploy history
journalctl -u vulcan-deploy --no-pager -n 50

# Container status
ssh root@pve.dwelf-forel.ts.net "pct exec 103 -- sh -lc 'cd /opt/vulcan-notify && docker compose ps'"

# Recent sync logs
ssh root@pve.dwelf-forel.ts.net "pct exec 103 -- sh -lc 'cd /opt/vulcan-notify && docker compose logs --tail 30 vulcan-sync'"

# Is the data actually current? (503 = stale; ?soft=1 for the same body as 200)
ssh root@pve.dwelf-forel.ts.net "pct exec 103 -- curl -s http://localhost:8585/api/health" | jq '.status, .stale_sections'
```

## 9. DNS fix for LXC

If DNS doesn't work inside the LXC (common with Tailscale on the PVE host), set it during LXC creation or override:

```bash
pct set 103 -nameserver "1.1.1.1 8.8.8.8"
```
