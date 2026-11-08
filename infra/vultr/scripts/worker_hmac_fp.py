#!/usr/bin/env python3
import hashlib
import os

value = os.environ.get("BROKER_HMAC_KEY", "")
print(f"worker:len={len(value)}:fp={hashlib.sha256(value.encode()).hexdigest()[:16]}")
