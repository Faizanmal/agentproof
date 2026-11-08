# ADR 0007: Private execution evidence and reproducible reports

Status: accepted; evidence import used on the Challenge 1 Vultr demo path.

## Decision

Browser evidence is accepted only from the broker-collected runner trace and
artifact bytes. The API validates artifact names, MIME signatures, size, JSON
shape, redacts nested secrets, computes SHA-256, and records a redaction audit
before registering evidence. Traces and screenshots live outside the web
static tree. Development uses `ARTIFACT_STORAGE_ROOT`; production requires an
absolute path on an operator-managed private, encrypted Vultr block-storage
mount with restricted service ownership. This repository does not configure a
public object bucket or claim an object-storage price or availability guarantee.

Downloads require an authenticated session and tenant/run authorization, are
integrity-checked, bounded, sent as attachments with `application/octet-stream`,
`nosniff`, and sandbox CSP. Links in reports identify the authenticated API
download route; they are not bearer URLs. Expired artifacts are not listed or
downloadable. Operators must back up and expire the private volume according to
the retention policy.

The report UI renders API fields as React text. Markdown export escapes table
and HTML delimiters. JSON has a versioned schema. Playwright export includes
only semantically supported plan actions with a corresponding recorded step;
unexecuted or unsupported actions are `TODO_MANUAL`. It contains symbolic test
credential environment references only. The exported test preserves the
original run verdict and cannot claim a new pass.

## Redaction boundary and limitations

The runner masks editable fields and explicit sensitive DOM markers in
screenshots, excludes screenshots when visible page text matches the configured
email/phone detectors, redacts URL query values and nested secret-keyed JSON,
and sanitizes console/request-failure text. Response bodies and request headers
are not captured. This is not OCR and cannot guarantee detection of arbitrary
PII rendered as an image or unusual text encoding. Protected pages can disable
screenshot capture. Only actual runner output contributes assertion evidence;
model output can never create a pass, failure, or artifact.

The bounded clean-sandbox investigation replay is not enabled in this phase.
Without a confirmed isolated side-effect-free fixture and a second broker lease,
automatic replay could duplicate state-changing test actions. A timeout alone
is therefore recorded as `INCONCLUSIVE`; confirmed/likely classification is
based on observed assertion outcomes and occurrence evidence, not LLM claims.

## Consequences

Reports remain available only while artifacts are unexpired and the user has
run access. A private Vultr volume is the current production storage adapter;
HA object storage, PDF export, OCR redaction and automated failure replay remain
future work. Never mount the evidence root under `apps/web/public`.
