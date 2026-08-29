<p align="center">
  <img src="app/static/favicon.png" width="128" height="128" alt="homelab-manager">
</p>

# homelab-manager

[![Version](https://img.shields.io/badge/Version-2.0.0-informational)](https://github.com/juanjimpad/lxc-manager)
[![License: Non-Commercial](https://img.shields.io/badge/License-Non--Commercial-orange.svg)](./LICENSE)
[![Built with Cursor Grok 4.6](https://img.shields.io/badge/Built%20with-Cursor%20Grok%204.6-000000)](https://cursor.com)
[![Ko-fi](https://img.shields.io/badge/Support-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/juanjimpad)

A small panel for a mixed homelab: Ubuntu LTS, Raspberry Pi OS, Proxmox
(LXC + VMs), Synology, Unraid, Docker hosts. Same look as the original
lxc-manager (Pico.css, light Unraid-like cards). Grafana-ish metrics,
Portainer-ish containers, lxc-manager guests — without the weight of any
of those three.

## Architecture

```
                    ┌─────────────────────┐
                    │  homelab-manager    │
                    │  (web UI + SQLite)  │
                    │  cluster key 0600   │
                    └──────────▲──────────┘
           HMAC-SHA256 heartbeat / jobs
     ┌─────────────┬───────────┼───────────┬─────────────┐
     │             │           │           │             │
 homelab-     homelab-    homelab-    homelab-      homelab-
 client       client      client      client        client
 Ubuntu       Pi OS       Proxmox     Synology      Unraid
 Docker       apt         LXC/VM      DSM           Docker
```

- **`homelab-manager`** — one process, one SQLite file, one web UI.
  First boot writes a **cluster key** next to the DB (`CLUSTER_KEY`,
  mode 0600). Every client signs its HTTP calls with HMAC-SHA256 of
  that key. The key is never sent as a Bearer token.
- **`homelab-client`** — stdlib-only Python, systemd unit. Heartbeats
  (~20s) with CPU, RAM, temperature, OS, and a snapshot of Docker
  containers / Proxmox LXC+VMs. Polls jobs from the manager: host
  updates (apt / synopkg / Unraid plugin check), Docker pull, LXC
  `apt-upgrade`, client self-update.
- **Optional Proxmox API module** — the original lxc-manager guests
  table (schedules, PBS backups, security audit) still works if you
  fill in `HLMGR_PVE_*`. It is not required to run the dashboard.
- **Telegram** — token, chat id, and per-event toggles live in
  Settings (env vars are only the bootstrap).
- **Self-update** — Settings → Check / Update now does `git fetch` +
  fast-forward of `origin/main`, `pip install -r requirements.txt`,
  then systemd restarts the panel. Same idea on each client
  (`self-update` job). Works while the repo is private (the clone
  already has credentials) and after it is public.

Code layout:

- **`app/core/`** — config (HLMGR_* with LXCMGR_* aliases), SQLite,
  login/session, cluster key, client HMAC, Telegram, self-update,
  Jinja, UI strings.
- **`app/modules/dashboard/`** — home: client cards.
- **`app/modules/clients/`** — `/api/v1/heartbeat` + job queue +
  per-machine page.
- **`app/modules/settings/`** — cluster key, Telegram, self-update.
- **`app/modules/update|security|backups/`** — original LXC/VM module,
  only started when Proxmox API credentials are set.
- **`client/homelab_client.py`** — the agent.

## Installation

### Manager

Same as before: Debian LXC, then inside it `./install.sh`. Fill in
`.env` (`HLMGR_SESSION_SECRET` at minimum). Start:

```
systemctl start homelab-manager
```

The cluster key is printed on first boot and stored at
`$APP_DIR/CLUSTER_KEY`. Copy it into every client.

If you already run lxc-manager 1.x: `git pull`, rerun `install.sh`,
keep your existing `.env` (LXCMGR_* still works). The unit
`lxc-manager.service` is kept as an alias.

### Client (each machine)

From a clone of this repo, as root:

```
./client/install.sh --url https://<manager> --key '<cluster-key>' --name dell-5060
```

`python3` is the only dependency. On Synology/Unraid without systemd,
run `python3 /usr/local/lib/homelab-client/homelab_client.py` from Task
Scheduler / a user script, with `/etc/homelab-client/client.env`
present (the install script still writes it).

### Optional: Proxmox LXC module

If you want the original guests table (PBS backups, security audit,
weekly apt via the host agent): keep tagging guests `managed` /
`auto-update`, install `agent/lxc-manager-agent.sh` on each Proxmox
host with a forced-command SSH key, and set `HLMGR_PVE_*` in `.env`.
See the 1.x README history for the exact `pveum` roles. New machines
should prefer **homelab-client on the Proxmox host** — it already
lists LXC/VMs and can run `lxc-update` jobs locally (`pct exec`),
without opening SSH from the panel.

## Security

- Cluster key is HMAC, not a session cookie. Timestamps must be within
  5 minutes; nonces cannot be replayed.
- The panel login is unchanged (PBKDF2, CSRF, fail2ban on `/login`).
- Clients run as root *on the machine they manage* because apt/docker/
  pct need it. They only speak HMAC to the manager — they do not
  accept inbound shells from it.
- Self-update is `git merge --ff-only origin/main`, never a free
  command.

## Deliberately out of scope for this version

- Pushing jobs through NAT *from* the manager (clients always poll).
- Unraid OS upgrades beyond the plugin-check script.
- Synology DSM GUI packages beyond `synopkg upgradeall`.
- Windows clients.
