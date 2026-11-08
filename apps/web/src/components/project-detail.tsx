"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ArrowRight, Check, Copy, KeyRound, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { createApiClient, getIdentity } from "@agentproof/contracts";
import type { components } from "@agentproof/contracts/schema";
import { EmptyState, PageHeader, Panel, StatusPill } from "@/components/app-ui";

type Project = components["schemas"]["ProjectView"];
type Origin = components["schemas"]["OriginView"];
type Challenge = components["schemas"]["OriginChallenge"];
type Secret = components["schemas"]["SecretView"];
type Plan = components["schemas"]["PlanView"];
type TargetPolicy = components["schemas"]["TargetPolicy"];

export function ProjectDetail({ settings = false }: { settings?: boolean }) {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [project, setProject] = useState<Project | null>(null);
  const [origins, setOrigins] = useState<Origin[]>([]);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [policy, setPolicy] = useState<TargetPolicy | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [proofMethod, setProofMethod] = useState<"https-file" | "dns-txt" | "demo">("https-file");
  const [attested, setAttested] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    const api = createApiClient();
    const [projectResult, originResult, secretResult, planResult, policyResult] = await Promise.all([
      api.GET("/api/v1/projects/{project_id}", { params: { path: { project_id: id } }, signal }),
      api.GET("/api/v1/projects/{project_id}/authorized-origins", { params: { path: { project_id: id } }, signal }),
      api.GET("/api/v1/projects/{project_id}/secrets", { params: { path: { project_id: id } }, signal }),
      api.GET("/api/v1/projects/{project_id}/plans", { params: { path: { project_id: id }, query: { limit: 20 } }, signal }),
      api.GET("/api/v1/projects/{project_id}/target-policy", { params: { path: { project_id: id } }, signal }),
    ]);
    if (!projectResult.data) throw new Error(projectResult.response.status === 404 ? "This project is missing or you do not have workspace access." : `Project could not load (${projectResult.response.status}).`);
    setProject(projectResult.data);
    setOrigins(originResult.data?.data ?? []);
    setSecrets(secretResult.data?.data ?? []);
    setPlans(planResult.data?.data ?? []);
    setPolicy(policyResult.data ?? null);
  }, [id]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void load(controller.signal).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "The API is unavailable.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [load]);

  async function mutate(callback: (csrf: string) => Promise<string | void>) {
    const identity = await getIdentity();
    if (!identity) { setError("Your session expired. Sign in again."); return; }
    setBusy(true); setMessage(""); setError("");
    try {
      const result = await callback(identity.csrf_token);
      if (result) setMessage(result);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The request failed."); }
    finally { setBusy(false); }
  }

  async function issueChallenge(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); const values = new FormData(event.currentTarget);
    await mutate(async (csrf) => {
      const result = await createApiClient(csrf).POST("/api/v1/projects/{project_id}/verification-challenges", {
        params: { path: { project_id: id } },
        body: { origin: String(values.get("origin") ?? ""), method: proofMethod },
      });
      if (!result.data) throw new Error(result.response.status === 403 ? "This verification method is not available to your workspace role or demo allowlist." : `Could not issue proof challenge (HTTP ${result.response.status}).`);
      setChallenge(result.data); setAttested(false); return "Challenge issued. Publish the exact token, then verify it before planning a run.";
    });
  }

  async function confirmChallenge() {
    if (!challenge) return;
    await mutate(async (csrf) => {
      const result = await createApiClient(csrf).POST("/api/v1/projects/{project_id}/verify-target", {
        params: { path: { project_id: id } }, body: { origin_id: challenge.id, attestation: challenge.method === "demo" && attested },
      });
      if (!result.data) {
        const code = typeof result.error === "object" && result.error && "code" in result.error
          ? String((result.error as { code?: string }).code ?? "")
          : "";
        const messages: Record<string, string> = {
          CHALLENGE_EXPIRED: "This challenge expired. Issue a fresh challenge, republish the new token, then verify again.",
          CHALLENGE_REUSED: "This challenge was already used. Issue a fresh challenge and publish the new token.",
          PROOF_NOT_FOUND: "No proof file or TXT record was found at the expected location. Publish the exact token, then retry.",
          PROOF_HTTP_ERROR: "The proof URL did not return HTTP 200. Confirm the well-known path is publicly reachable over HTTPS.",
          PROOF_EMPTY_OR_INVALID: "The proof body was empty or longer than 128 bytes. Publish only the exact challenge token.",
          PROOF_FETCH_ERROR: "AgentProof could not fetch the proof (TLS, DNS, or network). Fix connectivity and retry; do not mark the target verified manually.",
          VERIFICATION_FAILED: "A proof was fetched but did not match this challenge. Republish the exact current token and retry, or issue a fresh challenge.",
          OWNER_ATTESTATION_REQUIRED: "Demo attestation requires workspace owner confirmation on an allowlisted origin.",
          DEMO_SCOPE_DENIED: "Demo verification is not available for this origin or role.",
        };
        throw new Error(messages[code] ?? (result.response.status === 409
          ? "Verification failed. Issue a fresh challenge if the previous one expired or was already used."
          : `Target verification failed (HTTP ${result.response.status}).`));
      }
      setChallenge(null); return `Origin verified until ${new Date(result.data.expires_at).toLocaleString()}.`;
    });
  }

  async function addCredential(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const values = new FormData(form);
    if (values.get("test-account") !== "yes") { setError("Confirm that this is a TEST account before saving credentials."); return; }
    await mutate(async (csrf) => {
      const result = await createApiClient(csrf).POST("/api/v1/projects/{project_id}/secrets", {
        params: { path: { project_id: id } }, body: { label: String(values.get("label")), username: String(values.get("username")), password: String(values.get("password")), confirmed_test_account: true },
      });
      if (!result.data) throw new Error(result.response.status === 403 ? "Only workspace administrators can manage credentials." : `Credentials were not stored (HTTP ${result.response.status}).`);
      form.reset(); return `Test credential “${result.data.label}” stored. Its values cannot be viewed again.`;
    });
  }

  async function deleteCredential(secret: Secret) {
    if (!window.confirm(`Delete test credential “${secret.label}”? Existing approved plans that reference it may need review.`)) return;
    await mutate(async (csrf) => {
      const result = await createApiClient(csrf).DELETE("/api/v1/projects/{project_id}/secrets/{secret_id}", { params: { path: { project_id: id, secret_id: secret.id } } });
      if (!result.response.ok) throw new Error(`Credential could not be deleted (HTTP ${result.response.status}).`);
      return `Deleted test credential “${secret.label}”.`;
    });
  }

  async function savePolicy(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!policy) return;
    const values = new FormData(event.currentTarget);
    const allowed_paths = String(values.get("allowed-paths") ?? "").split("\n").map((value) => value.trim()).filter(Boolean);
    const credential_ref_ids = secrets.filter((secret) => values.get(`credential-${secret.id}`) === "on").map((secret) => secret.id);
    await mutate(async (csrf) => {
      const updated = { ...policy, allowed_paths, max_duration_seconds: Number(values.get("duration")), max_actions: Number(values.get("actions")), credential_ref_ids };
      const result = await createApiClient(csrf).PATCH("/api/v1/projects/{project_id}/target-policy", { params: { path: { project_id: id } }, body: updated });
      if (!result.data) throw new Error(result.response.status === 409 ? "Policy origin proof has expired or is not verified. Revalidate the target first." : `Policy could not be saved (HTTP ${result.response.status}).`);
      setPolicy(result.data); return "Policy saved. Scope changes require a fresh plan approval.";
    });
  }

  async function approveAndRun(plan: Plan, approvedStepIds: string[], highRisk: boolean) {
    if (!hasValidOrigin || !policy) { setError("A verified, unexpired target origin is required before starting a run."); return; }
    await mutate(async (csrf) => {
      const api = createApiClient(csrf);
      let approvedPlan = plan;
      if (plan.status !== "approved") {
        if (!plan.authorization_digest) throw new Error("Plan is missing its authorization digest. Generate a new plan.");
        const result = await api.POST("/api/v1/plans/{plan_id}/approve", { params: { path: { plan_id: plan.id } }, body: { version: plan.version, authorization_digest: plan.authorization_digest, approved_step_ids: approvedStepIds, approve_high_risk: highRisk } });
        if (!result.data) throw new Error(result.response.status === 409 ? "Plan or target scope changed. Review the latest plan before approving." : `Approval failed (HTTP ${result.response.status}).`);
        approvedPlan = result.data;
      }
      const run = await api.POST("/api/v1/projects/{project_id}/runs", { params: { path: { project_id: id } }, body: { plan_id: approvedPlan.id, idempotency_key: crypto.randomUUID(), max_duration_seconds: policy.max_duration_seconds } });
      if (!run.data) throw new Error(run.response.status === 409 ? "Approval is stale. Refresh the plan and review it again." : `Run could not be queued (HTTP ${run.response.status}).`);
      router.push(`/app/runs/${run.data.run_id ?? run.data.id}`);
      return "Approved plan queued.";
    });
  }

  if (loading) return <div className="app-page"><p role="status">Loading project and authorization records…</p></div>;
  if (!project) return <div className="app-page"><div role="alert" className="app-alert error">{error || "Project not found or unavailable."}</div><Link href="/app/projects">Back to projects</Link></div>;
  const hasValidOrigin = origins.some((origin) => origin.verified_at && new Date(origin.expires_at) > new Date());
  return <div className="app-page">
    <PageHeader eyebrow={`PROJECT / ${settings ? "SETTINGS" : "OVERVIEW"}`} title={settings ? `${project.name} settings` : project.name} description={project.base_url} action={settings ? { href: `/app/projects/${id}`, label: "Project overview" } : { href: `/app/projects/${id}/plans/new`, label: "Create test plan" }} />
    <nav className="project-tabs" aria-label="Project sections"><Link href={`/app/projects/${id}`} aria-current={!settings ? "page" : undefined}>Overview</Link><Link href={`/app/projects/${id}/runs`}>Runs</Link><Link href={`/app/projects/${id}/settings`} aria-current={settings ? "page" : undefined}>Settings</Link></nav>
    {error && <div className="app-alert error" role="alert">{error}</div>}{message && <div className="app-alert success" role="status">{message}</div>}
    {!settings && <>
      <Panel>
        <div className="panel-heading"><div><h2>Target authorization</h2><p>Proof is tied to this exact origin and expires. Testing stays disabled until verification succeeds.</p></div><StatusPill value={hasValidOrigin ? "verified" : "unverified"} /></div>
        {origins.map((origin) => <div className="origin-record" key={origin.id}><span className="origin-check">{origin.verified_at && new Date(origin.expires_at) > new Date() ? <Check size={16} /> : <ShieldCheck size={16} />}</span><span><strong>{origin.normalized_origin}</strong><small>{origin.verified_at ? `Verified ${new Date(origin.verified_at).toLocaleString()} · expires ${new Date(origin.expires_at).toLocaleString()}` : "Verification pending or expired"}</small></span><StatusPill value={origin.verified_at && new Date(origin.expires_at) > new Date() ? "verified" : "expired"} /></div>)}
        {challenge && <div className="proof-challenge"><p><strong>Publish this single-use proof token</strong></p><p>{challenge.instructions}</p>{challenge.method === "demo" ? <label className="checkbox-line"><input type="checkbox" checked={attested} onChange={(event) => setAttested(event.target.checked)} />I own this administrative demo target and confirm this attestation.</label> : <><div className="token-row"><code>{challenge.challenge}</code><button className="icon-button" aria-label="Copy proof token" onClick={() => void navigator.clipboard.writeText(challenge.challenge)}><Copy size={16} /></button></div><p className="field-note">Challenge expires {new Date(challenge.expires_at).toLocaleString()}. Publish exactly this value at the proof location.</p></> }<button className="button primary" disabled={busy || (challenge.method === "demo" && !attested)} onClick={confirmChallenge}>Verify published proof</button></div>}
        {!hasValidOrigin && <form className="app-form inline-form" onSubmit={issueChallenge}><label htmlFor="proof-origin">Origin to verify</label><input id="proof-origin" name="origin" type="url" defaultValue={project.base_url} required /><label htmlFor="proof-method">Proof method</label><select id="proof-method" value={proofMethod} onChange={(event) => setProofMethod(event.target.value as typeof proofMethod)}><option value="https-file">HTTPS well-known file</option><option value="dns-txt">DNS TXT record</option><option value="demo">Administrative demo allowlist</option></select><p className="field-note">Local/demo attestation is restricted to the configured administrative allowlist and is not available for arbitrary websites.</p><button className="button secondary" disabled={busy}>Issue proof challenge</button></form>}
      </Panel>
      <Panel>
        <div className="panel-heading"><div><h2>TEST credentials</h2><p>Encrypted test-account storage only. Browser login execution is not available in this release.</p></div><KeyRound size={18} aria-hidden="true" /></div>
        <div className="credential-warning">Authenticated workflows that fill credential: references are rejected at plan time. Never store a real customer or production account. Stored values are write-only and never appear in plans, screenshots, traces, or logs.</div>
        {secrets.length > 0 && <div className="credential-list">{secrets.map((secret) => <div className="credential-row" key={secret.id}><span><strong>{secret.label}</strong><small>Added {new Date(secret.created_at).toLocaleDateString()} · values hidden · login execution unsupported</small></span><button className="icon-button danger-icon" aria-label={`Delete ${secret.label}`} onClick={() => deleteCredential(secret)}><Trash2 size={16} /></button></div>)}</div>}
        <form className="app-form credential-form" onSubmit={addCredential}><label htmlFor="secret-label">Credential label</label><input id="secret-label" name="label" required pattern="[a-z][a-z0-9-]{0,78}" placeholder="e2e-staging-user" /><label htmlFor="secret-user">Test username</label><input id="secret-user" name="username" type="text" autoComplete="off" required maxLength={256} /><label htmlFor="secret-password">Test password</label><input id="secret-password" name="password" type="password" autoComplete="new-password" required maxLength={256} /><label className="checkbox-line"><input name="test-account" type="checkbox" value="yes" required />I confirm these credentials belong to a TEST account.</label><button className="button secondary" disabled={busy}>Store test credentials</button></form>
      </Panel>
      <Panel><div className="panel-heading"><div><h2>Plans and runs</h2><p>Generated plans require human review and approval before a browser run is queued.</p></div><Link className="subtle-link" href={`/app/projects/${id}/plans/new`}>New plan <ArrowRight size={15} /></Link></div>
        {!hasValidOrigin && <div className="app-alert warning">Verify or revalidate the target before creating a plan or starting a run.</div>}
        {plans.length ? <div className="plan-list">{plans.map((plan) => <PlanCard key={plan.id} plan={plan} onRun={approveAndRun} disabled={!hasValidOrigin || busy} />)}</div> : <EmptyState title="No plans yet" text="Describe a safe QA journey after verifying the target origin." action={hasValidOrigin ? { href: `/app/projects/${id}/plans/new`, label: "Create a plan" } : undefined} />}
      </Panel>
    </>}
    {settings && <ProjectSettings project={project} policy={policy} secrets={secrets} busy={busy} savePolicy={savePolicy} mutate={mutate} />}
  </div>;
}

function PlanCard({ plan, onRun, disabled }: { plan: Plan; onRun: (plan: Plan, stepIds: string[], highRisk: boolean) => Promise<void>; disabled: boolean }) {
  const [approvedSteps, setApprovedSteps] = useState<string[]>([]);
  const [highRisk, setHighRisk] = useState(false);
  const planJson = plan.plan_json && "steps" in plan.plan_json ? plan.plan_json as components["schemas"]["QAPlanV1"] : null;
  return <article className="plan-card"><div className="plan-card-heading"><div><h3>{plan.goal}</h3><p>Version {plan.version} · Risk: {plan.risk_level}</p></div><StatusPill value={plan.status} /></div>{plan.rejection_code && <p className="app-alert error">Plan rejected: {plan.rejection_code}</p>}{planJson && <><ol className="review-steps">{planJson.steps.map((step) => <li key={step.id}><div><strong>{step.action.replaceAll("_", " ")}</strong><span>{step.url_path ?? step.target?.name ?? "No target"}</span>{step.assertion && <small>Expected {step.assertion.kind}: {String(step.assertion.expected)}</small>}</div>{step.requires_approval && <label className="checkbox-line"><input type="checkbox" checked={approvedSteps.includes(step.id)} onChange={(event) => setApprovedSteps(event.target.checked ? [...approvedSteps, step.id] : approvedSteps.filter((id) => id !== step.id))} disabled={plan.status !== "draft"} />Approve action</label>}</li>)}</ol>{plan.risk_level === "high" && <label className="checkbox-line"><input type="checkbox" checked={highRisk} onChange={(event) => setHighRisk(event.target.checked)} disabled={plan.status !== "draft"} />I reviewed the HIGH risk flags and approve this scoped plan.</label>}<div className="plan-card-actions"><button className="button primary" disabled={disabled || plan.status === "rejected" || (plan.risk_level === "high" && plan.status !== "approved" && !highRisk)} onClick={() => void onRun(plan, approvedSteps, highRisk)}>{plan.status === "approved" ? "Run approved plan" : "Approve & run"}</button><Link className="subtle-link" href={`/app/projects/${plan.project_id}/runs`}>Project runs</Link></div></>}</article>;
}

function ProjectSettings({ project, policy, secrets, busy, savePolicy, mutate }: { project: Project; policy: TargetPolicy | null; secrets: Secret[]; busy: boolean; savePolicy: (event: React.FormEvent<HTMLFormElement>) => Promise<void>; mutate: (callback: (csrf: string) => Promise<string | void>) => Promise<void> }) {
  const router = useRouter();
  const [error, setError] = useState("");
  return <>
    <Panel><div className="panel-heading"><div><h2>Target policy</h2><p>Policy limits are enforced by the backend and included in each approved run scope.</p></div></div>{policy ? <form className="app-form policy-form" onSubmit={savePolicy}><label htmlFor="allowed-paths">Allowed paths <span className="label-help">One plain path or path glob per line</span></label><textarea id="allowed-paths" name="allowed-paths" rows={4} defaultValue={(policy.allowed_paths ?? []).join("\n")} required /><label htmlFor="allowed-methods">Allowed methods</label><div className="policy-readonly">{policy.allowed_methods.join(", ")} · mutations stay blocked unless explicitly scoped to a demo fixture</div><div className="policy-number-grid"><label>Maximum duration (seconds)<input name="duration" type="number" min={1} max={600} defaultValue={policy.max_duration_seconds} required /></label><label>Maximum actions<input name="actions" type="number" min={1} max={50} defaultValue={policy.max_actions} required /></label></div><fieldset><legend>Credential references allowed for test login</legend>{secrets.length ? secrets.map((secret) => <label className="checkbox-line" key={secret.id}><input type="checkbox" name={`credential-${secret.id}`} defaultChecked={(policy.credential_ref_ids ?? []).includes(secret.id)} />{secret.label}</label>) : <p className="field-note">Add a TEST credential before selecting login references.</p>}</fieldset><button className="button primary" disabled={busy}>Save target policy</button></form> : <p role="status">Verify a target origin to create its initial policy.</p>}</Panel>
    <Panel><div className="panel-heading"><div><h2>Project details</h2><p>Changes to project scope can invalidate existing approvals.</p></div></div><p><strong>{project.name}</strong></p><p className="origin-text">{project.base_url}</p><Link href={`/app/projects/${project.id}`} className="button secondary">Back to project</Link></Panel>
    <Panel><div className="panel-heading"><div><h2>Archive project</h2><p>Archived projects cannot accept new plans or runs.</p></div></div>{error && <p role="alert" className="form-status error-text">{error}</p>}<button className="button danger-button" onClick={() => { if (!window.confirm(`Archive ${project.name}?`)) return; void mutate(async (csrf) => { const result = await createApiClient(csrf).PATCH("/api/v1/projects/{project_id}", { params: { path: { project_id: project.id } }, body: { status: "archived" } }); if (!result.data) { setError(`Could not archive project (HTTP ${result.response.status}).`); return; } router.replace("/app/projects"); return "Project archived."; }); }}>Archive project</button></Panel>
  </>;
}
