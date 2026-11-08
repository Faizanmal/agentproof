"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { createApiClient, getIdentity } from "@agentproof/contracts";
import type { components } from "@agentproof/contracts/schema";
import { PageHeader, Panel } from "@/components/app-ui";

type Project = components["schemas"]["ProjectView"];
type Origin = components["schemas"]["OriginView"];
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
  const [policy, setPolicy] = useState<TargetPolicy | null>(null);
  const [goal, setGoal] = useState("");
  const [startPath, setStartPath] = useState("/");
  const [allowedPaths, setAllowedPaths] = useState("/*");
  const [duration, setDuration] = useState(180);
  const [busy, setBusy] = useState(false);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const loading = loadedFor !== id;

  useEffect(() => {
    const controller = new AbortController();
    const projectId = id;
    const api = createApiClient();
    void Promise.all([
      api.GET("/api/v1/projects/{project_id}", { params: { path: { project_id: projectId } }, signal: controller.signal }),
      api.GET("/api/v1/projects/{project_id}/authorized-origins", { params: { path: { project_id: projectId } }, signal: controller.signal }),
      api.GET("/api/v1/projects/{project_id}/target-policy", { params: { path: { project_id: projectId } }, signal: controller.signal }),
    ])
      .then(([p, o, t]) => {
        if (controller.signal.aborted) return;
        if (!p.data) {
          setError("Project missing or access denied.");
          setLoadedFor(projectId);
          return;
        }
        setProject(p.data);
        setOrigins(o.data?.data ?? []);
        setPolicy(t.data ?? null);
        if (t.data) {
          setDuration(Math.min(180, t.data.max_duration_seconds));
          setAllowedPaths((t.data.allowed_paths ?? ["/*"]).join("\n"));
        }
        setError("");
        setLoadedFor(projectId);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "API unavailable.");
          setLoadedFor(projectId);
        }
      });
    return () => controller.abort();
  }, [id]);

  const verified = origins.some((origin) => origin.verified_at && new Date(origin.expires_at) > new Date());

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setBusy(true); setMessage("Requesting a constrained plan from the configured model…");
    const identity = await getIdentity();
    if (!identity) { setBusy(false); setError("Your session expired. Sign in again."); return; }
    const paths = allowedPaths.split("\n").map((path) => path.trim()).filter(Boolean);
    try {
      const result = await createApiClient(identity.csrf_token).POST("/api/v1/projects/{project_id}/plans", {
        params: { path: { project_id: id } }, body: { goal: goal.trim(), untrusted_page_text: "", scope: { start_path: startPath.trim(), allowed_paths: paths, credential_ref_id: null } },
      });
      if (!result.data) {
        const code = typeof result.error === "object" && result.error && "code" in result.error
          ? String((result.error as { code?: string }).code ?? "")
          : "";
        const byCode: Record<string, string> = {
          AUTHENTICATED_TESTING_UNSUPPORTED: "Authenticated login plans are not supported yet. Remove credential references and use an unauthenticated fixture goal.",
          FIXTURE_PERMISSION_REQUIRED: "This goal needs fixture form permissions the current target policy does not allow. Use a heading-only assertion on the static fixture, or an owned demo fixture with forms enabled.",
          PLAN_GOAL_CHANGED: "The model altered the requested goal. Retry; AgentProof will not silently substitute an easier goal.",
          VULTR_MALFORMED_RESPONSE: "Vultr returned a malformed plan response. Retry; if it persists, check the configured model supports structured JSON.",
          VULTR_UNAVAILABLE: "Vultr inference is temporarily unavailable (upstream error). Retry shortly; no offline fallback was used.",
          LIVE_INFERENCE_BLOCKED: "Live Vultr inference is not configured. Set a valid API key and model; production never uses a mock planner.",
          EMAIL_VERIFICATION_REQUIRED: "Verify your email before generating plans.",
        };
        setError(byCode[code] ?? (result.response.status === 503 ? "Plan generation is unavailable. Check Vultr inference configuration and retry; no run was started." : result.response.status === 409 ? "Target authorization or policy changed. Revalidate the origin and try again." : `Plan was not generated (HTTP ${result.response.status}${code ? `: ${code}` : ""}).`));
        setMessage("");
        return;
      }
      setMessage(result.data.status === "rejected" ? `The planner rejected this request: ${result.data.rejection_code ?? "policy validation failed"}.` : "Draft saved. Review each locator, action, risk flag, and assertion before approval.");
      router.push(`/app/projects/${id}?review=${result.data.id}`);
    } catch { setError("The plan service is unavailable. No browser run was started."); setMessage(""); }
    finally { setBusy(false); }
  }

  if (loading) return <div className="app-page"><p role="status">Checking target proof and project scope…</p></div>;
  return <div className="app-page narrow-page"><PageHeader eyebrow="PROJECT / NEW TEST" title="Describe a test journey" description={project ? `Plan scope: ${project.base_url}` : "Project scope"} action={{ href: `/app/projects/${id}`, label: "Back to project" }} />
    {error && <div className="app-alert error" role="alert">{error}</div>}
    {!verified ? <Panel><div className="app-alert warning">A verified, unexpired origin is required. AgentProof will not generate plans for unverified project targets.</div><Link href={`/app/projects/${id}`}>Review authorization proof</Link></Panel> : <><Panel><form className="app-form" onSubmit={submit}><label htmlFor="plan-template">Start from a template <span className="optional-label">Optional</span></label><select id="plan-template" defaultValue="" onChange={(event) => { const value = templates[event.target.value as keyof typeof templates]; if (value) setGoal(value); }}><option value="">Write a custom goal</option>{Object.keys(templates).map((key) => <option key={key}>{key}</option>)}</select><label htmlFor="plan-goal">What should the test verify?</label><textarea id="plan-goal" value={goal} onChange={(event) => setGoal(event.target.value)} required maxLength={4000} rows={5} placeholder="Verify a synthetic test user can sign in and sees the expected dashboard summary." /><p className="field-note">Describe expected behavior and test data. Never include passwords, API tokens, or instructions copied from untrusted page content.</p><div className="policy-number-grid"><label htmlFor="start-path">Starting path<input id="start-path" value={startPath} onChange={(event) => setStartPath(event.target.value)} required pattern="/.*" maxLength={2048} /></label><label htmlFor="run-duration">Run-time cap (seconds)<input id="run-duration" type="number" min={1} max={policy?.max_duration_seconds ?? 600} value={duration} onChange={(event) => setDuration(Number(event.target.value))} required /></label></div><label htmlFor="allowed-paths">Allowed path patterns <span className="label-help">One path or glob per line</span></label><textarea id="allowed-paths" value={allowedPaths} onChange={(event) => setAllowedPaths(event.target.value)} rows={3} required /><label htmlFor="test-credential">Test login credential <span className="optional-label">Unavailable</span></label><select id="test-credential" value="" disabled aria-disabled="true"><option value="">Authenticated login execution is not supported in this release</option></select><p className="field-note">Plans that reference stored credentials are rejected. Use unauthenticated fixture goals only.</p><div className="policy-summary"><strong>Enforced scope</strong><span>Origins: {policy?.authorized_origins.join(", ") ?? project?.base_url}</span><span>Methods: {policy?.allowed_methods.join(", ") ?? "GET, HEAD"}</span><span>Maximum actions: {policy?.max_actions ?? 50}</span><span>Cost estimate: unavailable; provider pricing is not configured in this workspace.</span></div><button className="button primary" disabled={busy || !goal.trim()}>{busy ? "Generating draft…" : "Generate reviewable plan"}</button>{message && <p role="status" className="form-status">{message}</p>}</form></Panel><div className="app-alert info">Plan generation creates a draft only. A person must review and approve the exact scope before any run can be queued.</div></>}
  </div>;
}
