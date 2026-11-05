# E2E gate

Browser tests are deferred until a disposable broker-managed sandbox and an owned
fixture target exist. `scripts/smoke.py` performs HTTP checks only. Do not use a
developer-host browser or test arbitrary websites to bypass this gate.
