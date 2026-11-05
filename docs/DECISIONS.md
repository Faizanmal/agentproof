# Implementation decisions

2026-11-05 — Phase 01

- Official `create-next-app@latest` help was executed before scaffolding. Its
  generated versions are Next.js 16.4.5 and React 19.3.0. Disable telemetry feedback,
  nested Git initialization, React Compiler and cache components for this scaffold.
  Replace remote Google fonts with system fonts to avoid build-time font downloads.
- Pin the observed Node 26.5.0 and Python 3.13.1 toolchains in version manifests;
  pnpm is 11.18.0 and uv is 0.8.3. Corepack is absent; use the installed pnpm shim.
  No global package-manager installation or enablement was performed on the host.
  These pins are reproducibility choices, not an assertion of current security
  patch status. Refresh and audit base images before any production release.
- The three Python apps were created using `uv init --app --python 3.13 --vcs none`.
  A uv workspace shares `uv.lock`; `uv lock` and `uv sync --frozen --all-packages`
  verify the workspace configuration. Docker images select only their own package
  and omit development tools. The runner never receives API settings.
- Initial resolution failed because arq requires `redis<6`. Adjust the Python
  Redis client to `>=5.3,<6`; Redis **server** remains version 7. This is a client
  compatibility adjustment, not a server-stack substitution.
- Default uv cache access was denied in the sandbox. Portable scripts set
  `UV_CACHE_DIR=.cache/uv`. Network installation and Docker access require the
  execution environment's reviewed access; failures are recorded rather than
  bypassed. No cloud provisioning, inference calls or public deployment occurred.
- Docker base images use explicit version tags. Tags may be mutable; digest
  pinning, vulnerability scanning and updated patch versions are release gates.
- Frontend browser tests and Vitest are deferred: the shell has no domain logic,
  and browser execution is prohibited until the sandbox broker exists. HTTP smoke,
  backend tests, strict types, lint and production build provide Phase 01 checks.

Official references inspected:

- [Next.js CLI](https://nextjs.org/docs/app/api-reference/cli/create-next-app)
- [Next.js 16 upgrade requirements](https://nextjs.org/docs/app/guides/upgrading/version-16)
- [uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/)
- [Compose health-based startup](https://docs.docker.com/compose/how-tos/startup-order/)
- [Vultr inference API](https://api.vultrinference.com/docs/reference)

Vultr's documented base URL and model-discovery route are verified from its
documentation only. Credentials, selected model and actual inference responses
remain unverified and are not required for the Phase 01 health scaffold.

- Confirmed pnpm 11 settings in [official pnpm documentation](https://pnpm.io/settings).
  Non-registry settings belong in pnpm-workspace.yaml, including saveExact,
  engineStrict and the local store. Pin resolved direct dependencies exactly and
  use TypeScript 5.9.3 consistently across packages.
- Existing unrelated services occupy the default UI/API/Postgres host ports.
  Verification used explicit loopback overrides 13000/18000/15432; defaults are
  preserved. See the phase report for the exact default-port gate limitation.
