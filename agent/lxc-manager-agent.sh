#!/bin/bash
# lxc-manager-agent.sh — forced-command target for the lxc-manager SSH key.
# Invoked only via `command=` in authorized_keys; never run interactively.
# Whitelist of actions on purpose: adding a capability means adding a case
# here, never widening what the key itself can do.
set -euo pipefail

read -r action vmid <<< "${SSH_ORIGINAL_COMMAND:-}"

case "$action" in
  apt-upgrade|apt-list|app-update|app-version|health-check|sys-info|security-audit|pbs-verify) ;;
  *) echo "unknown action: $action" >&2; exit 1 ;;
esac

[[ "$vmid" =~ ^[0-9]+$ ]] || { echo "bad vmid" >&2; exit 1; }

# Resolve LXC or QEMU guest + managed tag (pbs-verify must work for both).
guest_kind=
tags=
if pct status "$vmid" &>/dev/null; then
  guest_kind=ct
  tags=$(pct config "$vmid" | awk -F': ' '/^tags:/{print $2}')
elif qm status "$vmid" &>/dev/null; then
  guest_kind=vm
  tags=$(qm config "$vmid" | awk -F': ' '/^tags:/{print $2}')
else
  echo "no such guest: $vmid" >&2
  exit 1
fi

[[ ";$tags;" == *";managed;"* ]] \
  || { echo "vmid $vmid not tagged managed" >&2; exit 1; }

# Exact tag match (same map as app/core/config.py TAG_APP_TYPE).
app=unknown
IFS=';' read -r -a tag_arr <<< "$tags"
for t in "${tag_arr[@]}"; do
  case "$t" in
    proxy)     app=npm; break ;;
    adblock)   app=adguard; break ;;
    dashboard) app=glance; break ;;
    network)   app=ddns; break ;;
    git)       app=gitea; break ;;
    docker)    app=docker-host; break ;;
  esac
done

# pct-only actions
if [[ "$action" != pbs-verify && "$guest_kind" != ct ]]; then
  echo "action $action requires an LXC (vmid $vmid is a VM)" >&2
  exit 1
fi

pbs_verify_guest() {
  # For every PBS storage on this host: verify the latest snapshot of
  # this guest. Uses the same credentials PVE already stores under
  # /etc/pve/priv/storage/*.pw — never printed.
  local backup_type=$1
  local ok_all=1
  local sid server datastore user pw fp latest upid enc status exitst i

  while IFS= read -r sid; do
    [[ -n "$sid" ]] || continue
    server=$(awk -v s="$sid" '
      $1=="pbs:" && $2==s {f=1; next}
      f && /^pbs:/ {exit}
      f && $1=="server" {print $2; exit}
    ' /etc/pve/storage.cfg)
    datastore=$(awk -v s="$sid" '
      $1=="pbs:" && $2==s {f=1; next}
      f && /^pbs:/ {exit}
      f && $1=="datastore" {print $2; exit}
    ' /etc/pve/storage.cfg)
    user=$(awk -v s="$sid" '
      $1=="pbs:" && $2==s {f=1; next}
      f && /^pbs:/ {exit}
      f && $1=="username" {print $2; exit}
    ' /etc/pve/storage.cfg)
    fp=$(awk -v s="$sid" '
      $1=="pbs:" && $2==s {f=1; next}
      f && /^pbs:/ {exit}
      f && $1=="fingerprint" {print $2; exit}
    ' /etc/pve/storage.cfg)
    pw_file="/etc/pve/priv/storage/${sid}.pw"
    if [[ -z "$server" || -z "$datastore" || -z "$user" || ! -f "$pw_file" ]]; then
      echo "pbs-verify $sid: incomplete storage config" >&2
      ok_all=0
      continue
    fi
    pw=$(cat "$pw_file")

    export PBS_REPOSITORY="${user}@${server}:${datastore}"
    export PBS_PASSWORD="$pw"
    export PBS_FINGERPRINT="$fp"
    latest=$(proxmox-backup-client snapshot list "${backup_type}/${vmid}" --output-format json 2>/dev/null \
      | python3 -c '
import sys, json
rows = json.load(sys.stdin)
if not rows:
    sys.exit(2)
rows.sort(key=lambda r: r["backup-time"], reverse=True)
print(rows[0]["backup-time"])
') || {
      echo "pbs-verify $sid: no snapshots for ${backup_type}/${vmid}" >&2
      ok_all=0
      continue
    }

    upid=$(curl -sk -H "Authorization: PBSAPIToken=${user}:${pw}" \
      --data-urlencode "backup-type=${backup_type}" \
      --data-urlencode "backup-id=${vmid}" \
      --data-urlencode "backup-time=${latest}" \
      --data-urlencode "ignore-verified=true" \
      --data-urlencode "outdated-after=0" \
      "https://${server}:8007/api2/json/admin/datastore/${datastore}/verify" \
      | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"])') || {
      echo "pbs-verify $sid: verify POST failed" >&2
      ok_all=0
      continue
    }

    enc=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$upid")
    status=running
    exitst=
    for i in $(seq 1 200); do
      read -r status exitst < <(curl -sk -H "Authorization: PBSAPIToken=${user}:${pw}" \
        "https://${server}:8007/api2/json/nodes/localhost/tasks/${enc}/status" \
        | python3 -c 'import sys,json; d=json.load(sys.stdin)["data"]; print(d.get("status",""), d.get("exitstatus",""))') || true
      if [[ "$status" == "stopped" ]]; then
        break
      fi
      sleep 3
    done
    if [[ "$status" != "stopped" || "$exitst" != "OK" ]]; then
      echo "pbs-verify $sid: FAILED (status=$status exit=$exitst)" >&2
      ok_all=0
    else
      echo "pbs-verify $sid: ok (snapshot ${backup_type}/${vmid}/${latest})"
    fi
  done < <(awk '/^pbs:/{print $2}' /etc/pve/storage.cfg)

  [[ "$ok_all" -eq 1 ]]
}

case "$action" in
  apt-upgrade)
    pct exec "$vmid" -- bash -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade'
    ;;
  apt-list)
    pct exec "$vmid" -- bash -c 'apt-get update -qq && apt list --upgradable 2>/dev/null'
    ;;
  app-version)
    case "$app" in
      adguard) pct exec "$vmid" -- /opt/AdGuardHome/AdGuardHome --version ;;
      glance)  pct exec "$vmid" -- /opt/glance/glance --version ;;
      gitea)   pct exec "$vmid" -- /usr/local/bin/gitea --version ;;
      npm)     pct exec "$vmid" -- bash -c "grep '\"version\"' /opt/nginxproxymanager/backend/package.json" ;;
      *)       echo "no app-version handler for app type: $app" >&2; exit 1 ;;
    esac
    ;;
  app-update)
    # Only AdGuard has an official non-interactive mechanism — the rest
    # (glance/gitea/ddns/npm) are "notify only" on purpose, never auto,
    # even if the caller asks for it.
    case "$app" in
      adguard) pct exec "$vmid" -- /opt/AdGuardHome/AdGuardHome --update ;;
      *)       echo "app-update not permitted for app type: $app" >&2; exit 1 ;;
    esac
    ;;
  health-check)
    case "$app" in
      adguard) pct exec "$vmid" -- curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/ ;;
      glance)  pct exec "$vmid" -- curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:8082/ ;;
      gitea)   pct exec "$vmid" -- curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/ ;;
      npm)     pct exec "$vmid" -- curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:81/ ;;
      ddns)    pct exec "$vmid" -- systemctl is-active cloudflare-ddns.service ;;
      docker-host)
        # Docker daemon up; prefer HTTP check if vaultwarden is present,
        # otherwise no unhealthy containers and at least one running.
        pct exec "$vmid" -- bash -c '
          docker info >/dev/null 2>&1 || { echo 000; exit 1; }
          if docker ps --format "{{.Names}}" | grep -qx vaultwarden; then
            curl -sf -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/ || echo 000
          else
            unhealthy=$(docker ps -q --filter health=unhealthy | wc -l)
            running=$(docker ps -q | wc -l)
            if [ "$unhealthy" -gt 0 ] || [ "$running" -eq 0 ]; then echo 000; exit 1; fi
            echo 200
          fi
        '
        ;;
      *)       echo "no health-check handler for app type: $app" >&2; exit 1 ;;
    esac
    ;;
  sys-info)
    # An LXC shares the host's kernel — this is informational, not "this
    # specific guest's kernel" (it is for real for VMs, but that case
    # doesn't go through this agent today).
    pct exec "$vmid" -- uname -r
    ;;
  security-audit)
    # Read-only. Same fields, same order, as SECURITY_AUDIT_SCRIPT in
    # app/core/agent.py (VM side) — keep the two in sync by hand if a
    # field is added. sshd -T (effective config, not the raw file)
    # because a drop-in can win over the main file — this actually
    # happened with PasswordAuthentication on m70q.
    pct exec "$vmid" -- bash -c '
      ssh_active=$(systemctl is-active ssh 2>/dev/null || systemctl is-active sshd 2>/dev/null || echo unknown)
      pw_auth=$(sshd -T 2>/dev/null | awk "/^passwordauthentication /{print \$2}")
      permit_root=$(sshd -T 2>/dev/null | awk "/^permitrootlogin /{print \$2}")
      keys=0
      for f in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do [ -s "$f" ] && keys=$((keys+1)); done
      f2b=$(systemctl is-active fail2ban 2>/dev/null || echo not-installed)
      sudo_nopasswd=$(grep -rhE "NOPASSWD" /etc/sudoers /etc/sudoers.d/ 2>/dev/null | grep -vc "^#")
      ports=$(ss -tlnH 2>/dev/null | awk "{print \$4}" | grep -oE "[0-9]+$" | sort -un | paste -sd, -)
      echo "ssh_active=$ssh_active"
      echo "password_auth=$pw_auth"
      echo "permit_root_login=$permit_root"
      echo "authorized_keys_files=$keys"
      echo "fail2ban=$f2b"
      echo "sudo_nopasswd_lines=$sudo_nopasswd"
      echo "listening_ports=$ports"
    '
    ;;
  pbs-verify)
    pbs_verify_guest "$guest_kind"
    ;;
esac
