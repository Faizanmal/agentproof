"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { createApiClient, getIdentity } from "@agentproof/contracts";
import type { components } from "@agentproof/contracts/schema";
import { PageHeader, Panel } from "@/components/app-ui";

type Project = components["schemas"]["ProjectView"];
type Origin = components["schemas"]["OriginView"];
type Secret = components["schemas"]["SecretView"];
type TargetPolicy = components["schemas"]["TargetPolicy"];

const templates = {
  "Login smoke": "Verify a test account can sign in and reaches the expected signed-in page. Do not change account settings or trigger external communications.",
  "CRUD scenario": "Check the owned staging fixture can create, read, update, and remove a synthetic test record using only fixture-scoped actions. Ask for approval before any cleanup step.",
  "Subscription lifecycle": "Verify the test fixture subscription lifecycle and dashboard totals using a synthetic test account. Do not purchase, cancel a real subscription, or contact external services.",
};

export function NewPlan() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [project, setProject] = useState<Project | null>(null);
  const [origins, setOrigins] = useState<Origin[]>([]);
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [policy, setPolicy] = useState<TargetPolicy | null>(null);
  const [goal, setGoal] = useState("");
  const [startPath, setStartPath] = useState("/");
  const [allowedPaths, setAllowedPaths] = useState("/*");
  const [credentialId, setCredentialId] = useState("");
  const [duration, setDuration] = useState(180);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async (signal?: AbortSignal) => {
    const api = createApiClient();
    const [p, o, s, t] = await Promise.all([
      api.GET("/api/v1/projects/{project_id}", { params: { path: { project_id: id } }, signal }),
      api.GET("/api/v1/projects/{project_id}/authorized-origins", { params: { path: { project_id: id } }, signal }),
      api.GET("/api/v1/projects/{project_id}/secrets", { params: { path: { project_id: id } }, signal }),
      api.GET("/api/v1/projects/{project_id}/target-policy", { params: { path: { project_id: id } }, signal }),
    ]);
    if (!p.data) throw new Error("Project missing or access denied.");
    setProject(p.data); setOrigins(o.data?.data ?? []); setSecrets(s.data?.data ?? []); setPolicy(t.data ?? null);
    if (t.data) { setDuration(Math.min(180, t.data.max_duration_seconds)); setAllowedPaths((t.data.allowed_paths ?? ["/*"]).join("\n")); }
  }, [id]);
  useEffect(() => { const controller = new AbortController(); void load(controller.signal).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "API unavailable."); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [load]);
  const verified = origins.some((origin) => origin.verified_at && new Date(origin.expires_at) > new Date());

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setBusy(true); setMessage("Requesting a constrained plan from the configured model…");
    const identity = await getIdentity();
    if (!identity) { setBusy(false); setError("Your session expired. Sign in again."); return; }
    const paths = allowedPaths.split("\n").map((path) => path.trim()).filter(Boolean);
    try {
      const result = await createApiClient(identity.csrf_token).POST("/api/v1/projects/{project_id}/plans", {
        params: { path: { project_id: id } }, body: { goal: goal.trim(), untrusted_page_text: "", scope: { start_path: startPath.trim(), allowed_paths: paths, credential_ref_id: credentialId || null } },
      });
      if (!result.data) { setError(result.response.status === 503 ? "Plan generation is unavailable. Check Vultr inference configuration and retry; no run was started." : result.response.status === 409 ? "Target authorization or policy changed. Revalidate the origin and try again." : `Plan was not generated (HTTP ${result.response.status}).`); setMessage(""); return; }
      setMessage(result.data.status === "rejected" ? `The planner rejected this request: ${result.data.rejection_code ?? "policy validation failed"}.` : "Draft saved. Review each locator, action, risk flag, and assertion before approval.");
      router.push(`/app/projects/${id}?review=${result.data.id}`);
    } catch { setError("The plan service is unavailable. No browser run was started."); setMessage(""); }
    finally { setBusy(false); }
  }

  if (loading) return <div className="app-page"><p role="status">Checking target proof and project scope…</p></div>;
  return <div className="app-page narrow-page"><PageHeader eyebrow="PROJECT / NEW TEST" title="Describe a test journey" description={project ? `Plan scope: ${project.base_url}` : "Project scope"} action={{ href: `/app/projects/${id}`, label: "Back to project" }} />
    {error && <div className="app-alert error" role="alert">{error}</div>}
    {!verified ? <Panel><div className="app-alert warning">A verified, unexpired origin is required. AgentProof will not generate plans for unverified project targets.</div><Link href={`/app/projects/${id}`}>Review authorization proof</Link></Panel> : <><Panel><form className="app-form" onSubmit={submit}><label htmlFor="plan-template">Start from a template <span className="optional-label">Optional</span></label><select id="plan-template" defaultValue="" onChange={(event) => { const value = templates[event.target.value as keyof typeof templates]; if (value) setGoal(value); }}><option value="">Write a custom goal</option>{Object.keys(templates).map((key) => <option key={key}>{key}</option>)}</select><label htmlFor="plan-goal">What should the test verify?</label><textarea id="plan-goal" value={goal} onChange={(event) => setGoal(event.target.value)} required maxLength={4000} rows={5} placeholder="Verify a synthetic test user can sign in and sees the expected dashboard summary." /><p className="field-note">Describe expected behavior and test data. Never include passwords, API tokens, or instructions copied from untrusted page content.</p><div className="policy-number-grid"><label htmlFor="start-path">Starting path<input id="start-path" value={startPath} onChange={(event) => setStartPath(event.target.value)} required pattern="/.*" maxLength={2048} /></label><label htmlFor="run-duration">Run-time cap (seconds)<input id="run-duration" type="number" min={1} max={policy?.max_duration_seconds ?? 600} value={duration} onChange={(event) => setDuration(Number(event.target.value))} required /></label></div><label htmlFor="allowed-paths">Allowed path patterns <span className="label-help">One path or glob per line</span></label><textarea id="allowed-paths" value={allowedPaths} onChange={(event) => setAllowedPaths(event.target.value)} rows={3} required /><label htmlFor="test-credential">Test login credential <span className="optional-label">Optional</span></label><select id="test-credential" value={credentialId} onChange={(event) => setCredentialId(event.target.value)}><option value="">No credentials</option>{secrets.map((secret) => <option key={secret.id} value={secret.id}>{secret.label}</option>)}</select><div className="policy-summary"><strong>Enforced scope</strong><span>Origins: {policy?.authorized_origins.join(", ") ?? project?.base_url}</span><span>Methods: {policy?.allowed_methods.join(", ") ?? "GET, HEAD"}</span><span>Maximum actions: {policy?.max_actions ?? 50}</span><span>Cost estimate: unavailable; provider pricing is not configured in this workspace.</span></div><button className="button primary" disabled={busy || !goal.trim()}>{busy ? "Generating draft…" : "Generate reviewable plan"}</button>{message && <p role="status" className="form-status">{message}</p>}</form></Panel><div className="app-alert info">Plan generation creates a draft only. A person must review and approve the exact scope before any run can be queued.</div></>}
  </div>;
}
