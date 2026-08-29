<p align="center">
  <img src="app/static/favicon.png" width="128" height="128" alt="lxc-manager">
</p>

# lxc-manager

[![Version](https://img.shields.io/badge/Version-1.1.5-informational)](https://github.com/juanjimpad/lxc-manager)
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

The HTML panel and the JSON API are two adapters over the same core.
Neither owns the domain: routers only call module services and serialize.

```
Browser (Pico + htmx) ──HTML──► app/web/ ──┐
                                          ├── modules/* services ──► app/core/
Future app ────────────JSON──► app/api/ ──┘         │
                                                    ├── SQLite
                                                    ├── Proxmox API (httpx)
                                                    └── SSH agent on each host
```

- **`app/core/`** — shared infrastructure: the Proxmox API client, SSH
  transport to hosts/VMs, the SQLite schema and connection, login/session
  (cookie or `Authorization: Bearer`), Telegram, the single Jinja2Templates
  instance, and the UI strings. Domain errors (`GuestNotFound`, `InvalidCron`)
  live here too.
- **`app/modules/update/`**, **`security/`**, **`backups/`** — the original
  modules. Each exposes **service functions** (no `Request`, no Jinja). The
  update module discovers guests by tag (`managed`) via the Proxmox API;
  weekly apt schedules are enabled automatically only when the guest also
  has `auto-update`. For each scheduled/manual run: safety snapshot →
  system packages → app update (if the type allows it) → health check →
  Telegram notification.
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
  PBS storage (sequentially), then a **PBS integrity verify** of the
  latest snapshot on each storage (agent action `pbs-verify`), then
  refreshes all storages' status.
  The Update module's pre-update dump does the same (all PBS storages).
  Guests are classified as LXC or VM; VMs get an OS probe (Proxmox
  `ostype` + `/etc/os-release` over SSH when configured). Package
  updates run only on Debian/Ubuntu (and all LXC); Windows skips the
  OS layer but still gets the backups.
- **`app/modules/selfupdate/`** — GitHub tag check + replace of the
  panel's own `app/` tree (see [Updating the panel](#updating-the-panel)).
- **`app/web/`** — HTML adapter (Pico.css + htmx). Same URLs as before
  (`/`, `/guest/{vmid}`, `/partials/...`). Fail2ban, bookmarks and the
  templates do not change.
- **`app/api/`** — JSON adapter mounted at `/api`. OpenAPI UI at `/api/docs`
  (see [JSON API](#json-api) below).
- **`app/main.py`** — mounts static files, session middleware, the web
  router and the API sub-app; starts SQLite + the scheduler.
- **`agent/lxc-manager-agent.sh`** — installed on **every Proxmox host**
  (not on the panel's LXC), as the target of a restricted `command=` in
  root's `authorized_keys`. The panel's SSH key never opens a shell: it
  can only invoke this script, which only accepts a fixed list of
  actions (`apt-upgrade`, `apt-list`, `app-update`, `app-version`,
  `health-check`, `sys-info`, `security-audit`, `pbs-verify`) on guests tagged
  `managed`. **Adding a new capability means adding a `case` to the
  agent, never widening what the key itself can do.**
- **Own login** (`app/core/auth.py`): username/password, signed-cookie
  session (Starlette `SessionMiddleware`), PBKDF2-HMAC-SHA256 hashing via
  `hashlib` (no new dependency). Seeds an admin user on first boot if the
  `users` table is empty (`LXCMGR_ADMIN_USER`/`LXCMGR_ADMIN_PASSWORD` from
  `.env`, or generated and printed once to the log if left blank).
  Password change at `/settings`. The JSON API accepts that same cookie
  **or** `Authorization: Bearer` with `LXCMGR_API_TOKEN`.
- **UI:** Pico.css (classless) + htmx — no Node.js, no build step. Both
  libraries vendored under `app/static/`. All UI copy lives in
  `app/core/strings.py`, kept out of the templates and route code.

### JSON API

Intended for a later host app (sidecar): this process still runs as
uvicorn on port 8500; the other app talks HTTP. Importing the Python
services as a library is possible but constrained (config is read from
the environment at import, APScheduler is in-process, pending runs are
in-memory) — the HTTP API is the supported integration path.

Authenticate every `/api/v1` call except `POST /api/v1/login`:

- Cookie session from `POST /api/v1/login` (or the HTML `/login`), or
- `Authorization: Bearer <LXCMGR_API_TOKEN>` (optional; unset/empty
  disables Bearer). CSRF applies to HTML form posts only.

Unauthenticated API calls return **401 JSON**, never a redirect to `/login`.

| Method | Path | What it does |
| --- | --- | --- |
| POST | `/api/v1/login` | Session cookie |
| POST | `/api/v1/logout` | Clear session |
| GET | `/api/v1/me` | Current identity (`admin` or `api`) |
| POST | `/api/v1/settings/password` | Change password (session user only) |
| GET | `/api/v1/guests` | List + last run / security / backups |
| POST | `/api/v1/guests/sync` | Rediscover from Proxmox |
| GET | `/api/v1/guests/{vmid}` | Guest detail |
| GET | `/api/v1/guests/{vmid}/kernel` | Live kernel string |
| GET | `/api/v1/guests/{vmid}/runs` | Update history |
| POST | `/api/v1/guests/{vmid}/runs` | Run now (202) |
| PUT | `/api/v1/guests/{vmid}/schedule` | Cron + enabled |
| GET | `/api/v1/guests/{vmid}/security` | Cached audit |
| POST | `/api/v1/guests/{vmid}/security` | Check now |
| GET | `/api/v1/guests/{vmid}/backups` | Cached PBS status + history |
| POST | `/api/v1/guests/{vmid}/backups` | Back up now (202) |
| GET | `/api/v1/version` | Panel current vs latest GitHub tag |
| POST | `/api/v1/self-update` | Install latest tag and restart (202) |

```
curl -sS -H "Authorization: Bearer $LXCMGR_API_TOKEN" \
  https://lxc-manager.example/api/v1/guests
```

Contract tests: `pip install -r tests/requirements.txt && pytest`.

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
   -hex 32`) and (optional) Telegram and `LXCMGR_API_TOKEN` for
   machine-to-machine calls to `/api/v1`.
6. Tag every guest to manage with `managed` (panel, backups, security).
   Add `auto-update` as well if it should join the weekly apt schedule:
   `pct set <vmid> --tags "managed;auto-update;<other-tags>"`.
7. `systemctl start lxc-manager`.

## Updating the panel

Guest apt jobs are unrelated: this only replaces **this** program.

The live install is a copy of `app/` + `requirements.txt`, not a git
checkout. A newer **semver tag** (`v1.1.0`) on
`github.com/juanjimpad/lxc-manager` shows a sticky bar (logged-in pages
only). The panel checks GitHub **once a day** (and when you click
Check for updates). It **only proposes** — it never installs by itself.
Confirm downloads that tag's tarball, **replaces the whole
`app/` tree** (files removed in the new release disappear — it is not
a merge), runs `pip install -r requirements.txt`, and exits. systemd
`Restart=always` brings uvicorn back; the open tab reloads when the
new version is serving. `.env` and the SQLite DB are not touched.
If current already matches latest, Settings shows **Up to date**.

The host agent (`agent/lxc-manager-agent.sh` on each Proxmox node) and
a changed systemd unit are **not** part of that apply — re-run
`install.sh` as root when those change.

`LXCMGR_SELF_UPDATE=0` hides the bar and rejects apply.
`LXCMGR_UPDATE_REPO` defaults to `juanjimpad/lxc-manager` (must be
`owner/name`).

Releases: bump `APP_VERSION` in `app/core/version.py` **and** push a
matching `vX.Y.Z` tag, or the bar never offers the build. "Latest" is
the highest semver **git tag**, not GitHub's Latest Release (a tag
alone is enough; creating a GitHub Release is optional).

First time this feature exists on a box that was installed as 1.0.x:
copy/install 1.1.0 once by hand (`./install.sh` as root, which also
picks up `Restart=always`). Later panel versions can use the bar.

`GET /api/v1/version` and `POST /api/v1/self-update` are the same
contract for a sidecar.

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
- Pushing a new `lxc-manager-agent.sh` onto every Proxmox host from
  the panel self-update bar.

## Security

- The panel's SSH key toward each host always carries a restricted
  `command=` — after installing, verify a shell attempt fails:
  `ssh -i <key> root@<host> whoami` should return "unknown action", not
  a session.
- The Proxmox API token carries its own minimal role (`VM.Audit` +
  `VM.Backup`), not a broader built-in role.
- The agent checks the `managed` tag on every invocation — a guest
  without that tag (for example, something as critical as PBS itself)
  is never reachable no matter what's asked of it.
