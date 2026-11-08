#!/usr/bin/env python3
from pathlib import Path

path = Path("/etc/agentproof/app.env")
lines: list[str] = []
for line in path.read_text().splitlines():
    if line.startswith("MAIL_FROM=") and not (
        line.startswith('MAIL_FROM="') or line.startswith("MAIL_FROM='")
    ):
        value = line.split("=", 1)[1]
        lines.append(f'MAIL_FROM="{value}"')
    else:
        lines.append(line)
path.write_text("\n".join(lines) + "\n")
path.chmod(0o600)
print("MAIL_FROM_QUOTED")
