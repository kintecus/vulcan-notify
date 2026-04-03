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

## 4. Tailscale (recommended)

Install Tailscale inside the LXC for direct SSH access from your Mac:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --hostname tools
```

The LXC is now reachable at `tools.dwelf-forel.ts.net`. Set up SSH key auth:

```bash
# From your Mac
ssh-copy-id root@tools.dwelf-forel.ts.net
```

## 5. GitHub deploy key

Generate a deploy key on the LXC for read-only GitHub access:

```bash
ssh-keygen -t ed25519 -C "vulcan-notify-deploy" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

Add the public key as a read-only deploy key at `github.com/kintecus/vulcan-notify/settings/keys`.

## 6. Management

```bash
# View logs
docker compose logs --tail 50

# Restart (e.g., after .env changes)
docker compose restart

# Update to latest
git pull && docker compose up -d --build

# Run a one-off sync
docker compose exec vulcan-notify uv run vulcan-notify sync

# Check session validity
docker compose exec vulcan-notify uv run vulcan-notify test
```

## 7. Auto-deploy (CI/CD)

A systemd timer on the LXC polls GitHub every 5 minutes and rebuilds the container if main has new commits. Deploy notifications are sent via ntfy.sh.

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

### Instant deploy from your Mac

Push to GitHub, then:

```bash
./deploy.sh
```

This SSHs to the tools LXC and runs pull + rebuild. Override the host with `TOOLS_HOST=<host> ./deploy.sh`.

## 8. Monitoring

```bash
# Deploy history
journalctl -u vulcan-deploy --no-pager -n 50

# Container status
ssh root@tools.dwelf-forel.ts.net "cd /opt/vulcan-notify && docker compose ps"

# Recent sync logs
ssh root@tools.dwelf-forel.ts.net "cd /opt/vulcan-notify && docker compose logs --tail 30"
```

## 9. DNS fix for LXC

If DNS doesn't work inside the LXC (common with Tailscale on the PVE host), set it during LXC creation or override:

```bash
pct set 103 -nameserver "1.1.1.1 8.8.8.8"
```
