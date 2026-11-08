"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ExternalLink, FileDown, RotateCcw } from "lucide-react";
import { createApiClient, getIdentity } from "@agentproof/contracts";
import type { components } from "@agentproof/contracts/schema";
import { EmptyState, PageHeader, Panel, StatusPill } from "@/components/app-ui";

type Report = components["schemas"]["RunReport"];
type Run = components["schemas"]["RunView"];
type Plan = components["schemas"]["PlanView"];

export function RunReportView() {
  const params = useParams<{ id?: string; runId?: string }>();
  const runId = params.id ?? params.runId ?? "";
  const router = useRouter();
  const [report, setReport] = useState<Report | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const api = createApiClient();
        const [reportResult, runResult] = await Promise.all([
          api.GET("/api/v1/runs/{run_id}/report", { params: { path: { run_id: runId }, query: { format: "json" } }, signal: controller.signal }),
          api.GET("/api/v1/runs/{run_id}", { params: { path: { run_id: runId } }, signal: controller.signal }),
        ]);
        if (controller.signal.aborted) return;
        if (!reportResult.data || !runResult.data) throw new Error(reportResult.response.status === 401 ? "Your session expired. Sign in again to view this private report." : reportResult.response.status === 404 ? "Run missing or access forbidden." : "Report could not be loaded.");
        const planResult = await api.GET("/api/v1/plans/{plan_id}", { params: { path: { plan_id: runResult.data.plan_id } }, signal: controller.signal });
        setReport(reportResult.data); setRun(runResult.data); setPlan(planResult.data ?? null);
      } catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Report service unavailable."); }
    })();
    return () => controller.abort();
  }, [runId]);

  async function rerun() {
    if (!run) return;
    if (!window.confirm("Queue a new run using this exact approved plan and its current verified policy scope? The server will reject stale authorization.")) return;
    const identity = await getIdentity();
    if (!identity) { setError("Your session expired. Sign in again."); return; }
    setBusy(true); setError(""); setMessage("Revalidating plan and target scope…");
    try {
      const result = await createApiClient(identity.csrf_token).POST("/api/v1/projects/{project_id}/runs", { params: { path: { project_id: run.project_id } }, body: { plan_id: run.plan_id, idempotency_key: crypto.randomUUID(), max_duration_seconds: Number(run.limits_json.duration_seconds ?? 180) } });
      if (!result.data) throw new Error(result.response.status === 409 ? "The plan approval or target scope is stale. Re-review the current plan before rerunning." : `Run could not be queued (HTTP ${result.response.status}).`);
      router.push(`/app/runs/${result.data.run_id ?? result.data.id}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not queue rerun."); setMessage(""); }
    finally { setBusy(false); }
  }

  if (error && !report) return <div className="app-page"><PageHeader eyebrow="RUN / REPORT" title="Report unavailable" /><div role="alert" className="app-alert error">{error}</div><Link href="/login">Sign in</Link></div>;
  if (!report || !run) return <div className="app-page"><p role="status">Loading verified run evidence…</p></div>;
  const base = `/api/v1/runs/${encodeURIComponent(runId)}`;
  const assertions = report.steps.filter((step) => String(step.action.action).startsWith("assert_"));
  return <div className="app-page">
    <PageHeader eyebrow="RUN / REPORT" title={report.run.goal} description={`Run ${report.run.id} · ${new Date(report.run.created_at).toLocaleString()} · ${report.run.model ?? "Model not recorded"}`} action={{ href: `/app/runs/${runId}`, label: "Back to run" }} />
    {error && <div role="alert" className="app-alert error">{error}</div>}{message && <div role="status" className="app-alert info">{message}</div>}
    <Panel className="report-summary"><div><span className="app-eyebrow">OBSERVED VERDICT</span><StatusPill value={report.run.verdict ?? report.run.status} /><p>Only recorded runner assertions and broker artifacts count as execution evidence.</p></div><div className="report-counts"><div><strong>{report.steps.length}</strong><span>Steps recorded</span></div><div><strong>{assertions.length}</strong><span>Assertions</span></div><div><strong>{report.findings.length}</strong><span>Findings</span></div><div><strong>{report.artifacts.length}</strong><span>Artifacts</span></div></div></Panel>
    <div className="report-toolbar"><nav aria-label="Report exports"><a href={`${base}/report?format=json`}><FileDown size={15} aria-hidden="true" />JSON</a><a href={`${base}/report?format=markdown`}><FileDown size={15} aria-hidden="true" />Markdown</a><a href={`${base}/export/playwright`}><FileDown size={15} aria-hidden="true" />Playwright ZIP</a></nav><button className="button secondary" disabled={busy || plan?.status !== "approved"} onClick={rerun}><RotateCcw size={15} aria-hidden="true" />{busy ? "Checking scope…" : "Rerun approved plan"}</button></div>
    <div className="app-alert info">Rerun uses the same approved plan. If its version, ownership proof, or policy scope changed, the API requires a fresh review and approval.</div>
    <section className="report-section" aria-labelledby="findings-title"><div className="section-title"><div><p className="app-eyebrow">OBSERVED RESULTS</p><h2 id="findings-title">Findings</h2></div></div>{report.findings.length === 0 ? <Panel><EmptyState title="No findings recorded" text="This means the run did not persist a finding. It does not imply PASS unless the objective assertions completed successfully." /></Panel> : <div className="finding-cards">{report.findings.map((finding) => <Panel key={finding.fingerprint} className="finding-card"><div className="finding-title-row"><div><h3>{finding.title}</h3><p>{finding.classification} · {finding.confidence.toFixed(2)} confidence · {finding.severity}</p></div><StatusPill value={finding.verdict} /></div><div className="expected-observed"><div><span>Expected</span><pre>{JSON.stringify(finding.expected_json, null, 2)}</pre></div><div><span>Observed</span><pre>{JSON.stringify(finding.observed_json, null, 2)}</pre></div></div><div><h4>Reproduction steps</h4><ol>{finding.repro_steps_json.map((step, index) => <li key={`${finding.fingerprint}-${index}`}>{step}</li>)}</ol></div>{finding.hypotheses_json.length > 0 && <div className="app-alert warning"><strong>Unconfirmed possibilities</strong><ul>{finding.hypotheses_json.map((hypothesis) => <li key={hypothesis}>{hypothesis}</li>)}</ul></div>}<div className="finding-evidence"><span>Evidence</span>{finding.evidence_artifact_ids.length ? finding.evidence_artifact_ids.map((artifactId) => <a key={artifactId} href={`/api/v1/artifacts/${artifactId}/download`}>{artifactId.slice(0, 8)} <ExternalLink size={13} aria-hidden="true" /></a>) : <small>No artifact attached</small>}</div></Panel>)}</div>}</section>
    <section className="report-section" aria-labelledby="assertions-title"><div className="section-title"><div><p className="app-eyebrow">PLAYWRIGHT OBSERVATIONS</p><h2 id="assertions-title">Assertions and steps</h2></div></div>{report.steps.length === 0 ? <Panel><p>No actual browser steps were recorded. A plan or model response alone is not execution evidence.</p></Panel> : <Panel className="report-steps"><div className="report-step-head"><span>Step</span><span>Action / expected</span><span>Observed status</span></div>{report.steps.map((step) => <div className="report-step-row" key={step.id}><span>{step.ordinal + 1}</span><div><strong>{String(step.action.action).replaceAll("_", " ")}</strong><small>{JSON.stringify(step.expected)}</small><small>{JSON.stringify(step.observed)}</small></div><StatusPill value={step.status} /></div>)}</Panel>}</section>
    <section className="report-section" aria-labelledby="artifacts-title"><div className="section-title"><div><p className="app-eyebrow">PRIVATE, EXPIRING FILES</p><h2 id="artifacts-title">Evidence artifacts</h2></div></div>{report.artifacts.length ? <Panel className="artifact-table">{report.artifacts.map((artifact) => <div className="artifact-row" key={artifact.id}><div><strong>{artifact.kind} · {artifact.mime_type}</strong><small>{artifact.bytes.toLocaleString()} bytes · SHA-256 {artifact.sha256}</small><small>{artifact.redaction_state} · expires {new Date(artifact.expires_at).toLocaleString()}</small></div><a className="button secondary" href={artifact.download_url} download>Download</a></div>)}</Panel> : <Panel><p>No downloadable artifacts were recorded.</p></Panel>}</section>
    <details className="report-metadata"><summary>Run and policy metadata</summary><pre>{JSON.stringify({ schema_version: report.schema_version, policy_digest: report.run.policy_digest, model: report.run.model, first_seen_at: report.findings.map((finding) => finding.first_seen_at) }, null, 2)}</pre></details>
  </div>;
}

