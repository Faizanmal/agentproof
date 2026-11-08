# AgentProof owned subscription fixture

This standalone FastAPI + SQLite app is an owned deterministic QA target. It
does not use a random website, external email, payments, or third-party account.
All registered addresses must end in the reserved `.test` suffix. Registration
verification stays in the fixture's local fake mailbox.

`make demo-seed` starts it on `127.0.0.1:19090`, creates two baseline synthetic
subscriptions, and generates a fresh random seed password and private fixture
admin token on each process start. Both secrets are stored in the ignored local
file `.cache/agentproof-demo/credentials.json`; the password is never printed.
The seeded address defaults to `qa@example.test` and can be changed with
`FIXTURE_SEED_EMAIL`. This is not a production account or reusable password.

The dashboard count is stored separately from subscription rows so the fixture
can contain an observable bug. `BUG_MODE=on` (default) intentionally fails to
refresh the persisted count after a synthetic cancellation: two baseline rows
plus one created row produce a stale displayed count of three after cancel. With
`BUG_MODE=off`, cancellation recomputes the count and displays two. The fixture
UI labels this as fixture-only defect mode. `make demo-fixture-bug-on/off`
switches the mode and reseeds only its two baseline records.

The only reset endpoint is `POST /__agentproof_fixture__/admin/reset?run_id=…`.
It requires a random admin token, loopback peer, and UUID-like run ID; it deletes
only labels starting with that exact `AgentProof-E2E-<runid>-` prefix. There is
no all-data reset. The private admin token is available only in the ignored
fixture credential file. `/__agentproof_fixture__/admin/status` is similarly
loopback/admin protected.

The dashboard and `/demo/untrusted` include quoted prompt-injection text and a
literal metadata-address link. The link is inert unless a browser clicks it;
the tests verify it is never requested. No real metadata endpoint is contacted.

`tests/unit/test_owned_fixture.py` checks SQLite-backed state, fake-mailbox
verification, test-only labels, restricted reset, and both defect modes.
`tests/e2e/test_observed_fixture_defect.py` uses real Chromium to perform the
subscription journey in bug-on and bug-off modes and writes actual screenshots
to `.cache/agentproof-demo/evidence`.

## Sandbox limitation

The local fixture listens on loopback and uses HTTP for local development. The
current broker accepts only public HTTPS targets, defaults to Docker network
`none`, and this repository does not yet have a packet-tested TLS egress gateway
that safely reaches a fixture. Therefore `make demo-run` refuses to dispatch this
HTTP fixture and `make demo-containment` reports `REAL_CONTAINMENT_BLOCKED`
unless the missing sandbox runtime/network controls are implemented. Do not
attach a routable Docker network as a workaround; no end-to-end sandbox result is
claimed from the direct fixture browser tests.
