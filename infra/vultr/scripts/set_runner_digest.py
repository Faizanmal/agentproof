#!/usr/bin/env python3
import subprocess
from pathlib import Path

image_id = subprocess.check_output(
    ["docker", "image", "inspect", "agentproof-runner:local", "--format", "{{.Id}}"],
    text=True,
).strip()
path = Path("/etc/agentproof/broker.env")
vals: dict[str, str] = {}
for line in path.read_text().splitlines():
    if "=" in line:
        key, value = line.split("=", 1)
        vals[key] = value
vals["BROKER_RUNNER_IMAGE_DIGEST"] = image_id
order = [
    "BROKER_HMAC_KEY",
    "BROKER_BIND_HOST",
    "BROKER_PORT",
    "BROKER_TLS_CERT",
    "BROKER_TLS_KEY",
    "BROKER_SANDBOX_NETWORK",
    "BROKER_SANDBOX_NETWORK_ALLOWLIST",
    "BROKER_RUNNER_ADD_HOSTS",
    "BROKER_MAX_CONCURRENT",
    "BROKER_STATE_PATH",
    "BROKER_ARTIFACT_PATH",
    "BROKER_RUNNER_IMAGE_DIGEST",
]
path.write_text("\n".join(f"{key}={vals[key]}" for key in order if key in vals) + "\n")
path.chmod(0o600)
print("DIGEST_SET", image_id[:24])
