#!/bin/bash
# Installs homelab-client on the machine it should manage (Ubuntu, Raspberry
# Pi OS, Proxmox host, Synology DSM 7 with python3, Unraid). Runs as root.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
URL=""
KEY=""
NAME=""

while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 --url https://manager.example --key <cluster-key> [--name my-host]"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$URL" ] || [ -z "$KEY" ]; then
  echo "Usage: $0 --url https://manager.example --key <cluster-key> [--name my-host]" >&2
  exit 2
fi

if ! command -v python3 >/dev/null; then
  echo "python3 is required" >&2
  exit 1
fi

install -d -m 755 /usr/local/lib/homelab-client
install -m 755 "$REPO_DIR"/client/homelab_client.py /usr/local/lib/homelab-client/homelab_client.py
install -d -m 700 /etc/homelab-client
install -d -m 700 /var/lib/homelab-client

umask 077
cat > /etc/homelab-client/client.env <<EOF
HLMGR_URL=$URL
HLMGR_KEY=$KEY
HLMGR_NAME=$NAME
HLMGR_ID_FILE=/var/lib/homelab-client/id
EOF
chmod 600 /etc/homelab-client/client.env

if command -v systemctl >/dev/null && [ -d /etc/systemd/system ]; then
  install -m 644 "$REPO_DIR"/systemd/homelab-client.service /etc/systemd/system/homelab-client.service
  systemctl daemon-reload
  systemctl enable --now homelab-client
  echo "homelab-client enabled. Check: systemctl status homelab-client"
else
  echo "No systemd — run in the foreground or a cron @reboot:"
  echo "  python3 /usr/local/lib/homelab-client/homelab_client.py"
fi
