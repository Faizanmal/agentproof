# Web

Official create-next-app scaffold adapted for AgentProof. Run commands from the
repository root. `pnpm --filter web dev` binds to loopback through the portable
dev command; `pnpm --filter web build` produces the standalone application.
`API_INTERNAL_URL` configures the server-only rewrite target at build/config time.
