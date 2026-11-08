#!/usr/bin/env bash
# Install or refresh the agentproof-broker systemd unit on the sandbox VM.
# Does not rotate secrets, open firewall ports, or change billing.
# Usage:
#   sudo bash /opt/agentproof/infra/vultr/scripts/install_broker_service.sh
#   sudo bash /opt/agentproof/infra/vultr/scripts/install_broker_service.sh --bootstrap-venv
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
UNIT_SRC="$ROOT/infra/vultr/systemd/agentproof-broker.service"
UNIT_DST=/etc/systemd/system/agentproof-broker.service
BROKER_ENV="${BROKER_ENV:-/etc/agentproof/broker.env}"
VENV="${BROKER_VENV:-/opt/agentproof/.venv-broker}"
BOOTSTRAP_VENV=0
# Keep host broker on the same minor as apps/* requires-python (>=3.13,<3.14).
PYTHON_MINOR="3.13"

for arg in "$@"; do
  case "$arg" in
    --bootstrap-venv) BOOTSTRAP_VENV=1 ;;
    -h|--help)
      echo "Usage: $0 [--bootstrap-venv]"
      exit 0
      ;;
    *)
      echo "Unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

find_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi
  for candidate in /root/.local/bin/uv /usr/local/bin/uv; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

pick_python() {
  for candidate in "python${PYTHON_MINOR}" python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      ver="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
      if [[ "$ver" == "$PYTHON_MINOR" ]]; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

bootstrap_venv() {
  local uv_bin py
  rm -rf "$VENV"
  if py="$(pick_python)"; then
    echo "==> bootstrap broker venv with system $py at $VENV"
    "$py" -m venv "$VENV"
    "$VENV/bin/pip" install -U pip hatchling
    "$VENV/bin/pip" install -e "$ROOT/apps/broker"
  else
    echo "==> system Python ${PYTHON_MINOR} missing; using uv to install it"
    if ! uv_bin="$(find_uv)"; then
      echo "Installing uv 0.8.3 to /root/.local/bin ..."
      curl -LsSf https://astral.sh/uv/0.8.3/install.sh | sh
      uv_bin="$(find_uv)" || {
        echo "uv install failed; install Python ${PYTHON_MINOR} manually." >&2
        exit 1
      }
    fi
    # Keep CPython outside /root so systemd can exec it.
    export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/opt/agentproof/uv-python}"
    install -d -m 0755 "$UV_PYTHON_INSTALL_DIR"
    "$uv_bin" python install "$PYTHON_MINOR"
    "$uv_bin" venv "$VENV" --python "$PYTHON_MINOR"
    "$uv_bin" pip install --python "$VENV/bin/python" -U pip hatchling
    "$uv_bin" pip install --python "$VENV/bin/python" -e "$ROOT/apps/broker"
  fi
  "$VENV/bin/python" -c "import sys; assert sys.version_info[:2] == (3, 13), sys.version"
  "$VENV/bin/python" -c "import agentproof_broker.main"
  test -x "$VENV/bin/python"
  echo "broker python=$("$VENV/bin/python" --version)"
  echo "broker python realpath=$(readlink -f "$VENV/bin/python")"
}

if [[ ! -f "$UNIT_SRC" ]]; then
  echo "Missing unit source: $UNIT_SRC" >&2
  exit 1
fi
if [[ ! -f "$BROKER_ENV" ]]; then
  echo "Missing $BROKER_ENV (create mode 600 with HMAC/TLS/digest first)." >&2
  exit 1
fi
if [[ ! -x "$VENV/bin/agentproof-broker" ]]; then
  if [[ "$BOOTSTRAP_VENV" -eq 1 ]]; then
    bootstrap_venv
  else
    echo "Missing $VENV/bin/agentproof-broker." >&2
    echo "This host has no apt python${PYTHON_MINOR}; bootstrap with uv:" >&2
    echo "  sudo bash $0 --bootstrap-venv" >&2
    exit 1
  fi
else
  "$VENV/bin/python" -c "import sys; assert sys.version_info[:2] == (3, 13), sys.version" || {
    echo "Existing $VENV is not Python ${PYTHON_MINOR}. Recreate with:" >&2
    echo "  sudo bash $0 --bootstrap-venv" >&2
    exit 1
  }
fi

install -d -m 0755 /var/lib/agentproof-broker /var/lib/agentproof-broker/artifacts
install -m 0644 "$UNIT_SRC" "$UNIT_DST"
systemctl daemon-reload
systemctl enable agentproof-broker.service
systemctl restart agentproof-broker.service
sleep 2
systemctl --no-pager --full status agentproof-broker.service || true
systemctl is-active agentproof-broker.service
echo "Installed and started agentproof-broker.service"
