# vulcan-notify

Polling service that detects changes in Vulcan/eduVulcan school e-journal and sends push notifications via [ntfy.sh](https://ntfy.sh).

Solves the problem of eduVulcan paywalling push notifications behind a subscription, while the web version (which is legally required to remain free) has no notification support.

## What it monitors

- **Grades** (new and changed)
- **Messages** (if not server-gated by subscription)
- **Attendance** (absences, late arrivals)
- **Announcements**

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- [ntfy app](https://ntfy.sh) on your phone (iOS/Android)

## Setup

```bash
# Clone and install
git clone https://github.com/yourname/vulcan-notify.git
cd vulcan-notify
uv sync

# Install Playwright browsers (needed for auth)
uv run playwright install chromium

# Configure
cp .env.example .env
# Edit .env - set your NTFY_TOPIC to something unique and secret

# Authenticate with eduVulcan (opens browser)
uv run vulcan-notify auth

# Test connectivity
uv run vulcan-notify test

# Run the service
uv run vulcan-notify run
```

## Docker

```bash
# First: run auth locally to get credential.json
uv run vulcan-notify auth

# Then deploy
mkdir data
cp credential.json data/
cp .env.example .env  # edit with your settings
docker compose up -d
```

## Commands

| Command | Description |
|---------|-------------|
| `vulcan-notify auth` | Interactive browser login, registers device credential |
| `vulcan-notify test` | Test API connectivity and send a test notification |
| `vulcan-notify run` | Start the polling service (default) |

## How it works

1. **Auth**: Playwright opens a browser for you to log into eduvulcan.pl. JWT tokens are intercepted from the auth redirect and used to register an RSA keypair as a "mobile device" via the [iris](https://github.com/bbrjpl1310b/iris) library.

2. **Polling**: Every 5 minutes (configurable), the service fetches grades, messages, attendance, and announcements from the Vulcan API.

3. **Diffing**: Each item is hashed and compared against SQLite state. New or changed items trigger notifications.

4. **Notifications**: Changes are pushed via ntfy.sh - subscribe to your topic in the ntfy app to receive them.

On first run, all existing data is stored without sending notifications (baseline). Only subsequent changes trigger alerts.

## Credits

Built on top of [iris](https://github.com/bbrjpl1310b/iris) (AGPL-3.0) - the only working eduVulcan API client as of early 2026.
