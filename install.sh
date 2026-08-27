#!/bin/bash
# Installs lxc-manager into an already-created, running Debian LXC (this
# script doesn't create the LXC itself — that's done on the Proxmox host
# with `pct create`, see README.md). Runs INSIDE the target LXC, as root.
set -euo pipefail

APP_USER=lxcmgr
APP_DIR=/home/$APP_USER/lxc-manager
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

id "$APP_USER" &>/dev/null || useradd -m -s /bin/bash "$APP_USER"

apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git fail2ban curl sqlite3

mkdir -p "$APP_DIR"
cp -r "$REPO_DIR"/app "$APP_DIR"/
cp "$REPO_DIR"/requirements.txt "$APP_DIR"/

if [ ! -f "$APP_DIR/.env" ]; then
  cp "$REPO_DIR"/.env.example "$APP_DIR"/.env
  echo "Edit $APP_DIR/.env with real values before starting the service."
fi

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

chown -R "$APP_USER:$APP_USER" "/home/$APP_USER"

if [ ! -f "/home/$APP_USER/.ssh/id_ed25519" ]; then
  mkdir -p "/home/$APP_USER/.ssh"
  chown "$APP_USER:$APP_USER" "/home/$APP_USER/.ssh"
  chmod 700 "/home/$APP_USER/.ssh"
  su "$APP_USER" -c "ssh-keygen -t ed25519 -f /home/$APP_USER/.ssh/id_ed25519 -N '' -C lxc-manager"
  echo
  echo "New public key — add it to root's authorized_keys on EVERY Proxmox"
  echo "host to manage, with a forced command (see README.md):"
  echo
  cat "/home/$APP_USER/.ssh/id_ed25519.pub"
  echo
fi

# known_hosts for StrictHostKeyChecking=yes (IPs from .env if already filled)
KNOWN="/home/$APP_USER/.ssh/known_hosts"
if [ ! -s "$KNOWN" ]; then
  touch "$KNOWN"
  chown "$APP_USER:$APP_USER" "$KNOWN"
  chmod 600 "$KNOWN"
  # shellcheck disable=SC1091
  if [ -f "$APP_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$APP_DIR/.env"
    set +a
  fi
  for host in "${LXCMGR_HOST_M700:-192.168.1.8}" "${LXCMGR_HOST_5060:-192.168.1.6}" 192.168.1.112; do
    ssh-keyscan -H -T 5 "$host" >> "$KNOWN" 2>/dev/null || true
  done
  chown "$APP_USER:$APP_USER" "$KNOWN"
  echo "SSH known_hosts seeded at $KNOWN (review/replace if keyscan failed)."
fi

# Proxmox cluster CA for API TLS verification (optional at install time)
if [ ! -f "$APP_DIR/pve-root-ca.pem" ]; then
  echo "Tip: copy /etc/pve/pve-root-ca.pem from a Proxmox host to"
  echo "  $APP_DIR/pve-root-ca.pem and set LXCMGR_PVE_CA_FILE to that path."
fi

install -m 750 -o root -g root "$REPO_DIR"/agent/lxc-manager-agent.sh /usr/local/sbin/lxc-manager-agent.sh 2>/dev/null || \
  echo "Note: agent/lxc-manager-agent.sh is installed on the Proxmox HOST, not here — see README.md"

cp "$REPO_DIR"/systemd/lxc-manager.service /etc/systemd/system/lxc-manager.service
systemctl daemon-reload
systemctl enable lxc-manager

install -m 644 -o root -g root "$REPO_DIR"/fail2ban/lxcmanager-auth-filter.conf \
  /etc/fail2ban/filter.d/lxcmanager-auth.conf
if [ ! -f /etc/fail2ban/jail.d/lxcmanager-auth.conf ]; then
  install -m 644 -o root -g root "$REPO_DIR"/fail2ban/lxcmanager-auth-jail.conf.example \
    /etc/fail2ban/jail.d/lxcmanager-auth.conf
  echo "fail2ban: jail installed with a minimal ignoreip (localhost only)."
  echo "  Add your trusted IPs to /etc/fail2ban/jail.d/lxcmanager-auth.conf"
  echo "  before exposing the panel beyond localhost."
fi
systemctl enable --now fail2ban >/dev/null 2>&1 || systemctl restart fail2ban

echo "Installed. Fill in $APP_DIR/.env and start with: systemctl start lxc-manager"
