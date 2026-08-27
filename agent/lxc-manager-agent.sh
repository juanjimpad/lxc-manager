#!/bin/bash
# lxc-manager-agent.sh — forced-command target for the lxc-manager SSH key.
# Invoked only via `command=` in authorized_keys; never run interactively.
# Whitelist of actions on purpose: adding a capability means adding a case
# here, never widening what the key itself can do.
set -euo pipefail

read -r action vmid <<< "${SSH_ORIGINAL_COMMAND:-}"

case "$action" in
  apt-upgrade|apt-list|app-update|app-version|health-check|sys-info|security-audit) ;;
  *) echo "unknown action: $action" >&2; exit 1 ;;
esac

[[ "$vmid" =~ ^[0-9]+$ ]] || { echo "bad vmid" >&2; exit 1; }

pct list 2>/dev/null | awk -v v="$vmid" '$1==v{f=1} END{exit !f}' \
  || { echo "no such lxc: $vmid" >&2; exit 1; }

tags=$(pct config "$vmid" | awk -F': ' '/^tags:/{print $2}')
[[ ";$tags;" == *";auto-update;"* ]] \
  || { echo "vmid $vmid not tagged auto-update" >&2; exit 1; }

case "$tags" in
  *proxy*)     app=npm ;;
  *adblock*)   app=adguard ;;
  *dashboard*) app=glance ;;
  *network*)   app=ddns ;;
  *git*)       app=gitea ;;
  *)           app=unknown ;;
esac

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
esac
