# Sandbox boundary — not implemented in Phase 01

No browser is launched by this scaffold. No runner is defined in application Compose.
Only a private broker will access the sandbox runtime. Before enabling execution,
implement signed typed jobs, replay protection, an origin-constrained egress gateway,
redirect/DNS/private-network controls, a nonroot read-only runner with dropped caps,
seccomp, tmpfs, and server-enforced CPU, memory, process, time and artifact limits.
Use a separate sandbox VM on Vultr for production. Containers alone do not establish
production tenant isolation. Ephemeral test-login credentials are the only permitted
runner secrets; model, database and cloud administration credentials are forbidden.

Initial budgets: 10 minutes, 50 actions, 4 model calls, 2 retries per recoverable
action, 1 live sandbox per workspace, 1 GiB RAM, 1 vCPU, 256 MB artifacts, 7-day retention.
These are future enforcement requirements, not implemented guarantees in this phase.
