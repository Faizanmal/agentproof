# ADR 0003: Project scope, target proof and credential isolation

Date: 2026-11-07. Status: accepted; used on the Challenge 1 Vultr demo (HTTPS-file origin proof). Supersedes the development-wide loopback exception.

## Authorization boundary

All endpoints require an authenticated cookie session. Mutations require same-origin CSRF checks. Verified email and workspace owner/admin role are required to issue/consume challenges, change policy or store TEST credentials. Demo attestation requires the workspace owner. Tenant visibility is checked before object lookup; other tenants receive 404. Secret responses contain only ID, label and creation time; no credential-read HTTP route exists.

A challenge is separate from a grant: random 256-bit token returned once, SHA-256 hash stored, expiry default 30 minutes. Publish the exact token at DNS TXT _agentproof-verification.<hostname>, or HTTPS /.well-known/agentproof-verification. Confirmation retrieves proof server-side, never accepting a caller-supplied proof result. A row lock makes consumption single use. Expired, malformed and reused challenges fail. Explicit revalidation requires a fresh challenge; success renews the grant for AUTHORIZATION_TTL_SECONDS (30 days by default). Snapshots cannot outlive grants.

A URL parser normalizes scheme, IDNA hostname and nondefault port. Reject userinfo, encoded authorities, fragments, ambiguous IPv4 encodings, IP literals and trailing-dot aliases. Public targets require HTTPS in every environment. Every DNS answer must be globally routable: private, loopback, link-local, metadata, multicast, reserved and IPv6 transition/mapped addresses fail. HTTPS proof resolves again immediately before connection, rejects answer changes, pins the validated numeric IP, preserves original Host/TLS SNI and validates the certificate with system trust. No environment proxy or redirects are followed. Only 200 and at most 128 bytes are eligible. TXT reads are bounded; target DNS is checked first. DNSSEC is not required: TXT proof trusts the configured resolver and authoritative DNS control.

## Restricted demo exception

Demo mode defaults off. It requires administrative DEMO_MODE_ENABLED=true and exact DEMO_ORIGINS configuration, workspace-owner attestation and IPv4 loopback on a non-service port. Example: DEMO_ORIGINS=["http://127.0.0.1:19090"]. Production additionally requires PRODUCTION_DEMO_ENABLED=true. A request cannot add its own allowlist entry. Arbitrary LAN or tunnel hosts are ineligible.

This is a consent shortcut for an operator-controlled development fixture, not cryptographic ownership. Keep it in a dedicated development instance. Future sandboxes must use a separately isolated fixture network; never forward API-host loopback to production runners.

## Deterministic policy and snapshots

TargetPolicy rejects unknown fields, unverified origins, cross-project credential references and excessive limits. The action guard enforces exact origins, decoded normalized path globs, methods, budgets, reviewed selectors and synthetic typing. Payment/purchase/payout, messaging/email/invite, deletion, account/password changes and export/download routes remain denied. WebSockets and downloads are denied entirely. POST/forms require owner/admin policy permission on an allowlisted demo and fixture paths under /demo/ or /**agentproof_fixture**/. Public-site writes cannot be enabled through this API.

Each queued run receives an HMAC-SHA256 signed snapshot with run/project IDs, policy version, origins and expiry. PostgreSQL rejects snapshot UPDATEs. Later policy edits cannot broaden an existing run. Domain-separated snapshot signatures use SESSION_SIGNING_KEY; broker jobs use independent BROKER_HMAC_KEY. Changing signing keys invalidates existing snapshots.

The connection-ticket adapter covers navigation, iframe, redirect, fetch/XHR and WebSocket channels. Every connection resolves afresh twice and rejects changed answers. Redirects require a previous URL and retain its exact origin. A consumer must connect to the returned IP with original TLS SNI and no second hostname lookup. Deny evidence contains only code and run ID, no target query/body/credentials.

Selectors and action labels cannot prove arbitrary JavaScript non-destructive.
Trusted counters, robots parsing through the same guarded transport, and OS-level
coverage for DNS/workers/service workers/WebRTC remain defense-in-depth goals for
broader public-target execution ([ADR 0005](0005-isolated-sandbox-execution.md)).
Private admission already serializes on the workspace row and refuses a second
pending/active lease across projects; `max_concurrent_runs` is fixed at one.
These APIs authorize scope; they do not by themselves launch a browser. A passing
guard test is not a multi-tenant public-target containment claim.

## Credentials and broker

Creation requires confirmed_test_account=true. Each secret has a unique AES-256 data key and nonce; versioned AES-256-GCM master key wrapping uses an independent nonce and project/row associated data. Legacy direct-AES rows are readable privately for migration. ENCRYPTION_PREVIOUS_KEYS maps retired versions to keys during rotation. Operator command:

    uv run --frozen --all-packages python -m agentproof_api.cli rotate-credentials

Configure a new MASTER_ENCRYPTION_KEY and ENCRYPTION_KEY_VERSION plus previous-version key map; repeat bounded batches (100 maximum) until zero; verify stored versions before removing retired keys. Failures roll back the batch. Never print plaintext or commit real keys.

The private broker context authenticates a signed login job expiring within 30 seconds, checks the immutable snapshot credential IDs/expiry and exact active sandbox lease/running run, then atomically persists a one-use receipt before decrypting. Another sandbox and replay fail. Its plaintext dictionary clears on exit, including exceptions. Python cannot guarantee secure RAM erasure: terminate the disposable runner after use. Network transport and sandbox activation are deferred to Phase 05; require mutual transport authentication plus HMAC then.

Never include credentials in planner/LLM messages or broad events. Disable HAR, trace, video, screenshots and response capture throughout authentication. Mask all input/password fields in later screenshots. Discard credential-bearing response bodies and strip Authorization/Cookie/Set-Cookie. Recursive redaction of known values is defense in depth, not a safe binary artifact sanitizer. Keep artifacts pending until sanitized; downloads already refuse unsanitized artifacts. Browser artifact recording remains disabled.

## Evidence and limits

Tests mock public DNS and pinned HTTPS transports; no public website is contacted. Negative containment substitutes local decoy addresses and asserts no socket attempt. PostgreSQL/Redis integration checks tenant boundaries, consumption/expiry, immutable snapshots and one-use delivery. No deployment or public crawler is enabled.

References: [OWASP SSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html), [Python address classifications](https://docs.python.org/3/library/ipaddress.html).
