#!/usr/bin/env bash
# Track A: fixture-only Docker network on sandbox-vm with egress locked to app IP:443.
set -euo pipefail
APP_IP="${1:?app public IPv4 required}"
NET_NAME="${2:-agentproof_fixture_net}"

docker network inspect "$NET_NAME" >/dev/null 2>&1 || docker network create \
  --driver bridge \
  --opt com.docker.network.bridge.enable_ip_masquerade=true \
  "$NET_NAME"

BRIDGE_ID="$(docker network inspect -f '{{.Id}}' "$NET_NAME" | cut -c1-12)"
BRIDGE_IF="br-${BRIDGE_ID}"

# Refresh DOCKER-USER chain rules for this bridge.
iptables -D DOCKER-USER -i "$BRIDGE_IF" -j DROP 2>/dev/null || true
iptables -D DOCKER-USER -i "$BRIDGE_IF" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
iptables -D DOCKER-USER -i "$BRIDGE_IF" -d "$APP_IP" -p tcp --dport 443 -j ACCEPT 2>/dev/null || true
iptables -D DOCKER-USER -i "$BRIDGE_IF" -d "$APP_IP" -p tcp --dport 80 -j ACCEPT 2>/dev/null || true

iptables -I DOCKER-USER 1 -i "$BRIDGE_IF" -d "$APP_IP" -p tcp --dport 443 -j ACCEPT
iptables -I DOCKER-USER 2 -i "$BRIDGE_IF" -d "$APP_IP" -p tcp --dport 80 -j ACCEPT
iptables -I DOCKER-USER 3 -i "$BRIDGE_IF" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A DOCKER-USER -i "$BRIDGE_IF" -j DROP

echo "FIXTURE_NET_READY name=$NET_NAME bridge=$BRIDGE_IF allow=$APP_IP:80/443"
