# ADR 0002: Persisted accounts and tenant boundaries

Date: 2026-11-06. Status: accepted; deployed on the Challenge 1 Vultr app VM.

Use SQLAlchemy async sessions and PostgreSQL tables, never application dictionaries
for persistence. Alembic owns explicit reversible DDL. Records use UUIDs and UTC
TIMESTAMPTZ; events use monotonically increasing BIGINT IDs. Clock timestamps plus
UUID tie-breakers support stable descending keyset pagination. Run steps retain a
unique ordinal; events are replayed by event ID. Foreign keys cascade project/run
children; audit records survive project deletion within their workspace. Model
usage retains provider accounting with a nullable run reference.

## Authentication

Argon2id uses 64 MiB, three iterations and one lane. New passwords require 12–256
characters. Login failure is generic, and unknown accounts perform a dummy hash
verification. Redis atomically counts hashed identity and peer-IP keys with TTL;
rate-limit failure returns 503 rather than bypassing enforcement. Forwarded-IP
headers are not trusted. Production ingress proxy trust is a separate release task.

Access and refresh values are random opaque credentials. Only SHA-256 hashes are
stored. Both cookies are HttpOnly, SameSite=Lax, path /api/v1, and Secure in
production. Access lifetime defaults to 15 minutes; refresh families have an
absolute seven-day deadline. Refresh consumes a locked session row and creates a
new session. Replaying a consumed refresh token revokes the entire family, including
its newest access credential. Logout revokes the family even after access expiry.
Disabled users cannot authenticate. No account token is returned in JSON or placed
in JavaScript/localStorage.

A synchronizer CSRF value is HMAC-derived from the session UUID and a server key.
This non-authenticating CSRF value is returned to authenticated clients for the
X-CSRF-Token header; it is kept only in client memory. A read-only /auth/csrf
endpoint recovers that value from a valid refresh cookie. All mutating API requests
must have the exact configured Origin, or a matching Referer when Origin is absent.
POST/PATCH also require application/json. Login and registration require those
origin/content-type checks; cookie-authenticated mutations additionally require
CSRF. CORS remains disabled. Web recovery is bounded and deduplicated; Web Locks
serialize refresh across tabs where supported.

## Verification and mail

Registration creates an unverified account and a one-hour, one-use verification
hash. Unverified users may configure workspaces/projects but cannot approve plans
or queue runs. Verification is POST-only. Links use a fragment, so the token never
enters an HTTP request URL or access log. The web page removes the fragment before
submitting the token in its JSON body.

Development exposes /dev/mail only to the authenticated recipient, only with
APP_ENV=development and MAIL_BACKEND=development. This is the explicitly requested
local mail sink; it exposes the one-use verification link, never an account cookie
or stored project credential. Mail links are AES-256-GCM ciphertext in the outbox,
with recipient/context-bound associated data and an explicit key version.

Production registration requires MAIL_BACKEND=smtp, SMTP_HOST and MAIL_FROM.
The explicit deliver-mail operator command uses STARTTLS with certificate/hostname
verification, optional SMTP authentication and a bounded batch. Delivery is at least
once: an interrupted batch may resend the same single-use link. No live SMTP
verification or email sending occurred during implementation. Configure a private
operator job before production signup; no silent external messages are sent.

## Authorization and state

All resource queries contain membership predicates before retrieval. Foreign or
missing IDs return 404. Viewers read; members write project/plan/run resources;
admins/owners also manage memberships, delete inactive projects and read audit
records. Only owners may grant/manage admin membership; owner memberships cannot
be edited through the generic member endpoint. No ownership transfer is implemented.
Workspace/project row locks serialize permission-sensitive writes and plan versions.

Run creation requires a verified user, an approved typed plan, an active project and
an unexpired verified exact base origin. Target verification is not fabricated:
Phase 03 reservations return 501 only in development and are absent in production.
A queued row is persisted work, not evidence of worker or browser execution. The
scheduler/runner remain disabled. Run limits are server-chosen; clients cannot set
lifecycle or verdict fields. State transitions use compare-and-swap and a database
transition trigger. Terminal runs reject further updates. PASS/FAIL transitions
require the trusted observation path; planner request schemas cannot set verdict.

The event stream checks authentication/membership before streaming and on every
poll, replays persisted events, has bounded duration and closes after revocation.
Only server-generated redacted state payloads are written in this phase.

## Artifacts and key operations

Artifact metadata excludes storage keys from API responses. Downloads require
membership, unexpired sanitized evidence, root-confined paths, size and SHA-256
verification. Content is sent as an attachment with nosniff and sandbox headers.
A database trigger creates a durable cleanup outbox on every artifact deletion,
including cascades. The bounded cleanup command also expires old artifacts, retries
filesystem failures at most three times, and tolerates already removed files.
Do not reuse immutable storage keys. The artifact root must be private to trusted
services; runners receive no host mounts. Symlink/path checks do not replace that
filesystem trust boundary.

MASTER_ENCRYPTION_KEY is a base64 32-byte key and SESSION_SIGNING_KEY has at least
32 bytes. Bootstrap initializes empty local keys without printing or overwriting
existing values. Production uses environment/secret-manager injection. Retain the
old encryption key until a controlled re-encryption completes; unsupported key
versions fail closed. Never change the active key version over existing ciphertext
without migration. No project secret endpoints are enabled in this phase.

References inspected: [OWASP sessions](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html),
[OWASP CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html),
[SQLAlchemy async](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html).
