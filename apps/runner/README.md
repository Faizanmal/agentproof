# Disposable runner

`agentproof-runner` is a minimal Playwright/Chromium process. It accepts exactly one broker-copied, strict JSON manifest containing QAPlanV1, verified origins, bounded paths and limits. There is no shell/script action, Docker client, inference client, database client, or user-selected image/command/mount support.

The broker starts this image read-only as UID 10001, with all Linux capabilities dropped, `no-new-privileges`, Docker's default seccomp profile, cgroup CPU/memory/PID limits, bounded tmpfs and `/dev/shm`, and a hard container lifetime. The broker pins the image by registry digest. Temporary manifest and screenshots are inside the disposable container and disappear when it is removed. No host path or device is mounted.

The runner checks every Playwright HTTP request's origin, allowed path, method and current DNS answers; it refuses non-global IP answers and cross-origin redirects. These checks are defense in depth. Browser hooks cannot prevent raw socket traffic or pin Chromium's DNS result. Until a separately tested VM firewall/egress gateway enforces default-deny and connection-time DNS/IP pinning, public execution is a P0 containment failure. The broker defaults to Docker `--network=none` and public execution must remain disabled.

Credential references currently fail closed because a broker-side one-use credential delivery channel is not connected to execution. No credentials are put into the runner environment. Screenshot artifacts are hashed and reported as metadata; authenticated one-time artifact upload is also not yet connected.
# Chromium browser test gate

From the repository root, use the `browser-test` Docker target documented in
`infra/sandbox/README.md`. It runs the E2E module against a real Chromium process
and writes its screenshot plus JSON trace to pytest's temporary directory. These
tests are skipped in the host test suite unless `AGENTPROOF_RUNNER_BROWSER=1` is
set in the runner image. Docker is unavailable in the current host environment,
so the real-browser checks have not yet been executed here.
