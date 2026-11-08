#!/usr/bin/env python3
import hashlib
import os

from agentproof_api.settings import Settings

settings = Settings()
value = settings.broker_hmac_key.get_secret_value()
print("settings_len", len(value))
print("settings_fp", hashlib.sha256(value.encode()).hexdigest()[:16])
print("environ_len", len(os.environ.get("BROKER_HMAC_KEY", "")))
print(
    "environ_fp",
    hashlib.sha256(os.environ.get("BROKER_HMAC_KEY", "").encode()).hexdigest()[:16],
)
print("broker_url", settings.broker_url)
print("ssl", os.environ.get("SSL_CERT_FILE"))
