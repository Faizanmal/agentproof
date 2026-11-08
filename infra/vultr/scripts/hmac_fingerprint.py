#!/usr/bin/env python3
import hashlib
import sys
from pathlib import Path

path = Path(sys.argv[1])
key_name = sys.argv[2] if len(sys.argv) > 2 else "BROKER_HMAC_KEY"
value = ""
for line in path.read_text().splitlines():
    if line.startswith(key_name + "="):
        value = line.split("=", 1)[1]
        break
digest = hashlib.sha256(value.encode()).hexdigest()[:16]
print(f"{path}:{key_name}:len={len(value)}:fp={digest}")
