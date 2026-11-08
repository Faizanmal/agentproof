# ADR 0004: Sole Vultr gateway and reviewed QA plans

Date: 2026-11-07. Status: implemented; Vultr Serverless Inference used for live plan
generation on the Challenge 1 demo. Browser execution is a separate broker path
([ADR 0005](0005-isolated-sandbox-execution.md)), not part of this gateway.

## Provider protocol and observed capabilities

Official references checked: [quickstart](https://api.vultrinference.com/docs/get-started/quickstart), [chat completion reference](https://api.vultrinference.com/docs/reference/create-chat-completion), [live model catalog](https://api.vultrinference.com/v1/models). The protocol uses POST /v1/chat/completions, bearer authentication and max_completion_tokens. The live catalog listed 33 models during inspection. The configured deepseek-v4.1-flash-1017 existed, advertised text/schema output and image input, and returned its exact ID in the real smoke. Image support is catalog metadata only: this gateway deliberately sends text only and has not verified vision.

Configuration requires VULTR_INFERENCE_API_KEY and VULTR_INFERENCE_MODEL; there is no default model. VULTR_INFERENCE_BASE_URL defaults to the official HTTPS v1 origin and the gateway refuses alternative providers, arbitrary proxies and redirects. Model IDs must resolve uniquely in the current catalog, either by exact ID or provider-advertised alias. Never guess aliases or accept silently routed default models. The returned model must equal the resolved catalog entry. No OpenAI/Anthropic transport or fake generation fallback exists.

Only model_gateway.py owns provider transport. Planner/agent callers use it. Runner has no gateway import or model key and remains disabled. A regression test enforces the runner source boundary.

## Bounded transport and accounting

HTTPX uses certificate validation, trust_env=false, no redirects, bounded timeout and a 2 MB response limit. Per draft, at most four POST attempts share a call/token budget, including transient retries and schema repairs. Each completion is capped at INFERENCE_MAX_COMPLETION_TOKENS (4096 default, 8192 maximum); total accounted tokens default to 32768. The planner also has a 90-second deadline. Temperature zero is requested only when advertised; this improves consistency without claiming deterministic provider output. Retry only rate limits, connection/timeouts and transient 500/502/503/504 failures, at most twice with exponential jitter. Authentication, other HTTP failures, model mismatch and policy denial fail immediately.

Schema mode is requested only when the selected catalog advertises it. Strict schema guarantees are not assumed. An explicit provider schema/response_format rejection may consume one bounded attempt to downgrade to advertised JSON object or plain text mode. That capability downgrade is retained across repair calls in the same gateway, so an explicitly rejected schema mode is not repeatedly requested. Generic 400 errors are not retried. Unsupported schema mode never skips local validation. Models without schema support receive a JSON schema in a text prompt. One repair request is allowed after invalid JSON/schema output. Repair includes only known schema field names, error types and a fixed whitelist of validator messages; raw provider output and validation inputs never enter repair prompts or logs.

Actual reported prompt/completion token counts are stored per attempt. Missing usage and cost remain NULL. Before dispatch, the gateway bounds prompt size and reserves a conservative UTF-8 byte/token upper bound plus framing and the completion limit. Attempts without usage retain that full reservation for budget enforcement; reserved tokens are not reported as actual usage. Base token prices are read from current selected model metadata only; incomplete/conditional pricing yields NULL, and available base-price estimates are not invoices or cached-token discount guarantees. Billing usage for lost/timed-out responses can remain unknown. A process crash after dispatch but before accounting can also leave unknown usage; no background dispatch resumes such drafts. Future run-scoped agents must use a persistent shared run budget before dispatch.

## Plan contract and policy

QAPlanV1 has a fixed action enum, bounded 1–15 unique steps, relative plain paths, observable assertions, bounded waits, and symbolic credential/synthetic value references. It cannot carry shell commands, JavaScript execution, Docker configuration, arbitrary URLs, tools or credential input values. CSS is restricted to body/main/headings, exact IDs or exact data-testid selectors; uniqueness or explicit disambiguation remains mandatory. Locators require uniqueness at runtime unless an explicit nth is supplied. Role locators require role plus accessible name; other strategies must use role=null. Actual locator uniqueness remains an execution-time check for Phase 05.

The separate risk engine checks every navigation/current path and URL assertion against test scope and TargetPolicy. It checks action/page/time budgets and credential references. Public arbitrary control activation is denied unless a CSS selector was explicitly reviewed in policy; forms and synthetic/credential input require approved demo fixture scope. Prohibited payments, messaging, destructive operations, account changes, password resets and exports remain denied regardless of approval. Ambiguous interactive controls and test-credential login become HIGH risk with explicit step consent. A model can increase risk but cannot lower the server's decision.

Goals and page text are quoted JSON data in a user message, separated from fixed system instructions. No page content can change origin grants, secret references, budget, tools or deterministic validation. Credentials are never read from the vault by the planner. Users must not paste credentials into goals/page text. Future DOM collection must exclude form values and authentication responses before reaching this boundary. Narrative assertions are inert data, never executable JavaScript.

## Persistence, tenancy and approval

A verified member creates a durable draft before provider I/O; version allocation is serialized by the project lock. Provider I/O does not hold the project lock. Success stores validated QAPlanV1 and server risk; failure retains a rejected draft with a safe error code. model_usage records actual attempts and plan_events persists plan.generated/plan.rejected. No raw rejected output is stored. The plan events endpoint is tenant-scoped and bounded. Generation is rate limited to ten requests per workspace/hour.

A signed authorization object captures project origin, exact policy/version, test scope and grant expiries. Approval must supply both version and authorization_digest; sensitive step IDs and HIGH-risk consent are never preselected. It checks tenant, verified email, role, current grants, signature, schema, deterministic risk and unchanged scope. Policy patch rejects approved drafts, and run creation rechecks approval against current authorization. PostgreSQL prevents mutation of approved plan content/scope/version/approval evidence. Queued execution admission also refuses rejected plans. Each run signature binds the exact approved plan ID, version and content digest. Its origin is the reviewed base origin, credentials are narrowed to the one explicitly selected TEST credential (or none), and a separate signed test-scope path check intersects project policy on every connection. A workspace-wide policy never silently broadens a narrower reviewed plan. Changing scope creates a fresh draft; it never carries forward approval.

Existing legacy plan JSON remains readable, but cannot obtain new approval or execution without current signed authorization and QAPlanV1 validation. This preserves earlier user records without trusting their older schema. Migration rollback converts missing usage to legacy zero counts because the old schema cannot express unknown usage; export evidence before rolling back.

## Local/live verification and boundaries

Run the safe POSIX wrapper:

    sh scripts/check-vultr-inference.sh

PowerShell equivalent:

    uv run --frozen --all-packages python scripts/check_vultr_inference.py

Both use the sole gateway and print only status, model and usage metadata. They never echo tokens or generated text. Missing configuration reports LIVE_INFERENCE_BLOCKED with exact required variable names. A real local persisted-planner/approval smoke is:

    uv run --frozen --all-packages python scripts/authorization_smoke.py --generate-plan

The local smoke fixture and API are temporary; its smoke account/workspace are local records and the smoke project is removed. The live inference smoke and live generated-plan approval both passed with configured Vultr credentials; earlier schema rejections were observable failures and fixed without substituting outputs. Unit/integration tests use deterministic recorded transport fixtures, never live inference.

A parsed or approved plan does not itself launch a browser. Dispatch is a separate worker→broker step after explicit approval ([ADR 0005](0005-isolated-sandbox-execution.md), [ADR 0006](0006-durable-run-workflow.md)). Approval cannot waive sandbox or egress controls.
