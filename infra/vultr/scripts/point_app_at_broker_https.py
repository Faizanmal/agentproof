#!/usr/bin/env python3
"""Switch BROKER_URL to HTTPS and install CA bundle for the app VM."""

from __future__ import annotations

import subprocess
from pathlib import Path

APP_ENV = Path("/etc/agentproof/app.env")
BROKER_CA = Path("/etc/agentproof/broker-ca.crt")
CA_BUNDLE = Path("/etc/agentproof/ca-bundle.crt")
SYSTEM_CA = Path("/etc/ssl/certs/ca-certificates.crt")


def main() -> None:
    lines: list[str] = []
    found = False
    for line in APP_ENV.read_text().splitlines():
        if line.startswith("BROKER_URL="):
            lines.append("BROKER_URL=https://10.51.96.4:8090")
            found = True
            continue
        if line.startswith(("SSL_CERT_FILE=", "REQUESTS_CA_BUNDLE=", "BROKER_CA_FILE=")):
            continue
        lines.append(line)
    if not found:
        lines.append("BROKER_URL=https://10.51.96.4:8090")

    CA_BUNDLE.write_bytes(SYSTEM_CA.read_bytes() + b"\n" + BROKER_CA.read_bytes())
    lines.append(f"SSL_CERT_FILE={CA_BUNDLE}")
    APP_ENV.write_text("\n".join(lines) + "\n")
    print("BROKER_URL_HTTPS")


if __name__ == "__main__":
    main()
