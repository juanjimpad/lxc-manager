#!/bin/bash
# Installs homelab-manager (evolved from lxc-manager) into an already-created
# Debian LXC. Runs INSIDE the target LXC, as root.
set -euo pipefail

APP_USER=lxcmgr
APP_DIR=/home/$APP_USER/lxc-manager
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

id "$APP_USER" &>/dev/null || useradd -m -s /bin/bash "$APP_USER"

apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git fail2ban curl sqlite3

mkdir -p "$APP_DIR"
cp -r "$REPO_DIR"/app "$APP_DIR"/
cp -r "$REPO_DIR"/client "$APP_DIR"/
cp "$REPO_DIR"/requirements.txt "$APP_DIR"/

if [ ! -f "$APP_DIR/.env" ]; then
  cp "$REPO_DIR"/.env.example "$APP_DIR/.env"
  SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  # portable in-place: write a new file (busybox-safe)
  if grep -q '^LXCMGR_SESSION_SECRET=$' "$APP_DIR/.env" 2>/dev/null \
     || grep -q '^HLMGR_SESSION_SECRET=$' "$APP_DIR/.env" 2>/dev/null \
     || ! grep -q 'SESSION_SECRET=' "$APP_DIR/.env"; then
    printf '\nHLMGR_SESSION_SECRET=%s\n' "$SECRET" >> "$APP_DIR/.env"
  fi
  echo "Edit $APP_DIR/.env with real values before starting the service."
fi

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

chown -R "$APP_USER:$APP_USER" "/home/$APP_USER"

if [ ! -f "/home/$APP_USER/.ssh/id_ed25519" ]; then
  mkdir -p "/home/$APP_USER/.ssh"
  chown "$APP_USER:$APP_USER" "/home/$APP_USER/.ssh"
  chmod 700 "/home/$APP_USER/.ssh"
  su "$APP_USER" -c "ssh-keygen -t ed25519 -f /home/$APP_USER/.ssh/id_ed25519 -N '' -C homelab-manager"
  echo
  echo "New public key — only needed if you still use the Proxmox host agent"
  echo "(see README.md). Prefer homelab-client on each machine instead:"
  echo
  cat "/home/$APP_USER/.ssh/id_ed25519.pub"
  echo
fi

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
  for host in "${LXCMGR_HOST_M700:-${HLMGR_HOST_M700:-}}" "${LXCMGR_HOST_5060:-${HLMGR_HOST_5060:-}}"; do
    [ -n "$host" ] || continue
    ssh-keyscan -H -T 5 "$host" >> "$KNOWN" 2>/dev/null || true
  done
  chown "$APP_USER:$APP_USER" "$KNOWN"
fi

if [ ! -f "$APP_DIR/pve-root-ca.pem" ]; then
  echo "Tip: copy /etc/pve/pve-root-ca.pem from a Proxmox host to"
  echo "  $APP_DIR/pve-root-ca.pem and set HLMGR_PVE_CA_FILE to that path"
  echo "  only if you still use the Proxmox API module."
fi

install -m 750 -o root -g root "$REPO_DIR"/agent/lxc-manager-agent.sh /usr/local/sbin/lxc-manager-agent.sh 2>/dev/null || \
  echo "Note: agent/lxc-manager-agent.sh is for Proxmox HOSTs, not this LXC."

install -m 644 -o root -g root "$REPO_DIR"/systemd/homelab-manager.service \
  /etc/systemd/system/homelab-manager.service
# Keep the old unit name as an alias so existing enablement still works.
install -m 644 -o root -g root "$REPO_DIR"/systemd/homelab-manager.service \
  /etc/systemd/system/lxc-manager.service
systemctl daemon-reload
systemctl enable homelab-manager

install -m 644 -o root -g root "$REPO_DIR"/fail2ban/lxcmanager-auth-filter.conf \
  /etc/fail2ban/filter.d/lxcmanager-auth.conf
if [ ! -f /etc/fail2ban/jail.d/lxcmanager-auth.conf ]; then
  install -m 644 -o root -g root "$REPO_DIR"/fail2ban/lxcmanager-auth-jail.conf.example \
    /etc/fail2ban/jail.d/lxcmanager-auth.conf
  echo "fail2ban: jail installed with a minimal ignoreip (localhost only)."
fi
systemctl enable --now fail2ban >/dev/null 2>&1 || systemctl restart fail2ban

echo
echo "Installed. Fill in $APP_DIR/.env and start with: systemctl start homelab-manager"
echo "Cluster key is created on first start at $APP_DIR/CLUSTER_KEY (mode 0600)."
echo "Copy that key into each client: ./client/install.sh --url https://<manager> --key <key>"
