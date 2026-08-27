<p align="center">
  <img src="app/static/favicon.png" width="128" height="128" alt="lxc-manager">
</p>

# lxc-manager

[![Version](https://img.shields.io/badge/Version-1.0.0-informational)](https://github.com/juanjimpad/lxc-manager)
[![License: Non-Commercial](https://img.shields.io/badge/License-Non--Commercial-orange.svg)](./LICENSE)
[![Built with Cursor Grok 4.5](https://img.shields.io/badge/Built%20with-Cursor%20Grok%204.5-000000)](https://cursor.com)
[![Ko-fi](https://img.shields.io/badge/Support-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/juanjimpad)

Centralized web panel to schedule and run LXC/VM updates across a
Proxmox cluster — born for a two-node homelab, but nothing about that
homelab is hardcoded except through `.env`.

## Why it exists

The Proxmox API has no equivalent to `pct exec` for containers (only
lifecycle: start/stop/status/snapshot/vzdump/console). To run
`apt upgrade` or an app's update inside an LXC from a central panel,
something has to talk to the Proxmox host it lives on, not the API.

## Architecture

Code segmented by module, each with its own FastAPI router:

- **`app/core/`** — shared by every module: the Proxmox API client, SSH
  transport to hosts/VMs, the SQLite schema and connection, login/session,
  Telegram, the single Jinja2Templates instance, and the UI strings.
- **`app/modules/update/`** — the original module. Discovers guests by
  tag (`auto-update` by default) via the Proxmox API, schedules a weekly
  run per guest (editable from the UI), and for each one: safety
  snapshot → system packages → app update (if the type allows it) →
  health check → Telegram notification.
- **`app/modules/security/`** — audits SSH/fail2ban/sudo/ports per guest
  (Is SSH active? Does it allow username+password? Are keys loaded? Is
  `fail2ban` active? `PermitRootLogin`? Passwordless `sudo`? Listening
  ports?), shown on each guest's page. **Always cached**
  (`security_checks` table) — never fetched on every visit, only via the
  "Check now" button or the automatic weekly sweep (Sundays 03:00). Uses
  `sshd -T` (effective config) instead of reading `sshd_config` raw — an
  `Include`/drop-in can win over the main file.
- **`app/modules/backups/`** — is each guest's PBS backup chain
  actually healthy, **per PBS storage**? Discovers every Proxmox
  storage with `type=pbs` (or an explicit `LXCMGR_PBS_STORAGES` list)
  and makes one `GET .../storage/<pbs>/content` call per storage — no
  SSH, no new permission. The guest page renders one subsection per
  storage (last backup, verification, size); the main table is OK only
  when **every** storage is healthy. A newly added PBS backend shows up
  on the next sync with no code change. Flags a storage red if its last
  backup is older than 36h or PBS marked it as failed verification (a
  backup that's simply never been verified is neutral, not a failure).
  Refreshed automatically every hour and manually via "Back up now" —
  which triggers a *real* on-demand `vzdump` to **every** discovered
  PBS storage (sequentially), then refreshes all storages' status.
  The Update module's pre-update dump does the same (all PBS storages).
  Guests are classified as LXC or VM; VMs get an OS probe (Proxmox
  `ostype` + `/etc/os-release` over SSH when configured). Package
  updates run only on Debian/Ubuntu (and all LXC); Windows skips the
  OS layer but still gets the backups.
- **`app/main.py`** — deliberately thin: mounts static files, session,
  the login/settings routes (cross-cutting, they don't belong to any
  module), and includes each module's router.
- **`agent/lxc-manager-agent.sh`** — installed on **every Proxmox host**
  (not on the panel's LXC), as the target of a restricted `command=` in
  root's `authorized_keys`. The panel's SSH key never opens a shell: it
  can only invoke this script, which only accepts a fixed list of
  actions (`apt-upgrade`, `apt-list`, `app-update`, `app-version`,
  `health-check`, `sys-info`, `security-audit`) on guests tagged
  `auto-update`. **Adding a new capability means adding a `case` to the
  agent, never widening what the key itself can do.**
- **Own login** (`app/core/auth.py`): username/password, signed-cookie
  session (Starlette `SessionMiddleware`), PBKDF2-HMAC-SHA256 hashing via
  `hashlib` (no new dependency). Seeds an admin user on first boot if the
  `users` table is empty (`LXCMGR_ADMIN_USER`/`LXCMGR_ADMIN_PASSWORD` from
  `.env`, or generated and printed once to the log if left blank).
  Password change at `/settings`.
- **UI:** Pico.css (classless) + htmx — no Node.js, no build step. Both
  libraries vendored under `app/static/`. All UI copy lives in
  `app/core/strings.py`, kept out of the templates and route code.

## Installation

1. On the Proxmox host that will host the panel: create the LXC (Debian,
   no community-script template — it adds nothing here), network and
   key-based SSH already sorted.
2. Inside the LXC: `./install.sh` (creates the `lxcmgr` service user,
   virtualenv, systemd unit, generates the panel's SSH key pair).
3. On **every Proxmox host** to manage: install
   `agent/lxc-manager-agent.sh` under `/usr/local/sbin/`, and add the
   `authorized_keys` line that `install.sh` prints when it generates the
   key:

   ```
   command="/usr/local/sbin/lxc-manager-agent.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty <public key>
   ```
4. Create a Proxmox API user/token with only what's needed — two roles,
   not one with more permission than necessary:

   ```
   pveum role add LxcManagerAPI -privs 'VM.Audit,VM.Backup'
   pveum user add lxc-manager@pve
   pveum aclmod / -user lxc-manager@pve -role LxcManagerAPI

   # vzdump ALSO needs this permission on the specific destination
   # storage (not on / — otherwise the snapshot fails with 403).
   pveum role add LxcManagerBackupStorage -privs 'Datastore.AllocateSpace'
   pveum aclmod /storage/<your-pbs-storage> -user lxc-manager@pve -role LxcManagerBackupStorage

   pveum user token add lxc-manager@pve api --privsep 0
   ```
5. Fill in `.env` (copied from `.env.example`) with the API URL, the
   token, each host's IP, an `LXCMGR_SESSION_SECRET` (`openssl rand
   -hex 32`) and (optional) Telegram.
6. Tag every guest to manage with `auto-update` (in addition to its
   existing tags): `pct set <vmid> -tags <current-tags>,auto-update`.
7. `systemctl start lxc-manager`.

`install.sh` already installs **fail2ban** (package + filter + jail,
with a minimal `ignoreip` to start — `localhost` only) protecting
`/login`. Two things to check afterwards, not automatable because they
depend on your network:

- Add your trusted IPs to `ignoreip` in
  `/etc/fail2ban/jail.d/lxcmanager-auth.conf` before exposing the panel
  beyond `localhost` (otherwise any login failure from your own IP can
  end up banning you).
- **If the panel sits behind a reverse proxy** (NPM, Traefik...), the
  `ExecStart` in `systemd/lxc-manager.service` already carries
  `--proxy-headers --forwarded-allow-ips=<proxy IP>` — fill in that IP.
  Without this, both the panel and fail2ban see the proxy's IP, not the
  real client's, and a jail like that would end up banning the proxy
  itself instead of whoever is failing to log in.

## Deliberately out of scope for this version

- SSH key management / enabling-disabling password access on the
  managed guests.
- Updating Proxmox VE itself (the host).
- A `config-check` action (reading specific config files) — fits the
  agent's action architecture, not implemented yet.

## Security

- The panel's SSH key toward each host always carries a restricted
  `command=` — after installing, verify a shell attempt fails:
  `ssh -i <key> root@<host> whoami` should return "unknown action", not
  a session.
- The Proxmox API token carries its own minimal role (`VM.Audit` +
  `VM.Backup`), not a broader built-in role.
- The agent checks the `auto-update` tag on every invocation — a guest
  without that tag (for example, something as critical as PBS itself)
  is never reachable no matter what's asked of it.
