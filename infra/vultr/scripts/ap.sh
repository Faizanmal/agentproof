#!/usr/bin/env bash
# Short ops wrapper for app / sandbox VMs.
#
#   sudo bash /opt/agentproof/infra/vultr/scripts/ap.sh logs
#   sudo bash .../ap.sh logs worker
#   sudo bash .../ap.sh tail api 120
#   sudo bash .../ap.sh ps
#   sudo bash .../ap.sh mail
#   sudo bash .../ap.sh broker
#   sudo bash .../ap.sh broker -f
#   sudo bash .../ap.sh health
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
RELEASE_ENV="${RELEASE_ENV:-/etc/agentproof/release.env}"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/infra/compose.prod.yml}"
CMD="${1:-}"
shift || true

# Install short `dc` once so operators never type --env-file / -f.
bash "$ROOT/infra/vultr/scripts/install_dc.sh" >/dev/null 2>&1 || true

dc() {
  bash "$ROOT/infra/vultr/scripts/dc.sh" --profile execution "$@"
}

usage() {
  cat <<'EOF'
Usage: ap.sh <command> [args]

  logs [svc...]       Follow compose logs (default: api worker web caddy)
  tail [svc...] [N]   Last N lines, no follow (default N=80)
  ps                  Compose status
  mail                Run deliver-mail once via api
  broker [-f] [N]     Sandbox: journalctl agentproof-broker
  health              Live check + service status
  help                This text
EOF
}

case "$CMD" in
  ""|help|-h|--help)
    usage
    ;;

  logs)
    if [[ $# -gt 0 ]]; then
      dc logs -f --tail=80 "$@"
    else
      dc logs -f --tail=80 api worker web caddy
    fi
    ;;

  tail)
    n=80
    services=()
    for arg in "$@"; do
      if [[ "$arg" =~ ^[0-9]+$ ]]; then
        n="$arg"
      else
        services+=("$arg")
      fi
    done
    if [[ ${#services[@]} -eq 0 ]]; then
      services=(api worker web caddy)
    fi
    dc logs --tail="$n" "${services[@]}"
    ;;

  ps|status)
    dc ps
    ;;

  mail|deliver-mail)
    dc exec -T api python -m agentproof_api.cli deliver-mail
    ;;

  broker)
    follow=0
    n=80
    for arg in "$@"; do
      if [[ "$arg" == "-f" || "$arg" == "--follow" ]]; then
        follow=1
      elif [[ "$arg" =~ ^[0-9]+$ ]]; then
        n="$arg"
      fi
    done
    if [[ "$follow" -eq 1 ]]; then
      journalctl -u agentproof-broker -f -n "$n"
    else
      journalctl -u agentproof-broker -n "$n" --no-pager
    fi
    ;;

  health)
    if [[ -f "$RELEASE_ENV" ]]; then
      APP_DOMAIN="$(grep -E '^APP_DOMAIN=' "$RELEASE_ENV" | cut -d= -f2- || true)"
      if [[ -n "$APP_DOMAIN" ]]; then
        echo -n "app live: "
        curl -fsS "https://${APP_DOMAIN}/api/v1/health/live" || echo FAIL
        echo
      fi
      dc ps
    fi
    if systemctl list-unit-files agentproof-broker.service &>/dev/null; then
      echo -n "broker unit: "
      systemctl is-active agentproof-broker || true
      BIND="$(grep -E '^BROKER_BIND_HOST=' /etc/agentproof/broker.env 2>/dev/null | cut -d= -f2- || true)"
      PORT="$(grep -E '^BROKER_PORT=' /etc/agentproof/broker.env 2>/dev/null | cut -d= -f2- || echo 8090)"
      if [[ -n "$BIND" ]]; then
        echo -n "broker health: "
        curl -sk "https://${BIND}:${PORT}/healthz" || echo FAIL
        echo
      fi
    fi
    ;;

  *)
    echo "Unknown command: $CMD" >&2
    usage >&2
    exit 2
    ;;
esac
