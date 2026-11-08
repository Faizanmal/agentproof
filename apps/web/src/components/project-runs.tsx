"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { createApiClient } from "@agentproof/contracts";
import type { components } from "@agentproof/contracts/schema";
import { EmptyState, PageHeader, Panel, StatusPill } from "@/components/app-ui";

type Run = components["schemas"]["RunView"];
type Project = components["schemas"]["ProjectView"];
export function ProjectRuns() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(async (signal?: AbortSignal) => {
    const api = createApiClient();
    const [projectResult, runResult] = await Promise.all([
      api.GET("/api/v1/projects/{project_id}", { params: { path: { project_id: id } }, signal }),
      api.GET("/api/v1/projects/{project_id}/runs", { params: { path: { project_id: id }, query: { limit: 100 } }, signal }),
    ]);
    if (!projectResult.data) throw new Error(projectResult.response.status === 404 ? "Project not found or forbidden." : "Project could not be loaded.");
    setProject(projectResult.data); setRuns(runResult.data?.data ?? []);
    if (!runResult.data) setError(`Run history could not load (HTTP ${runResult.response.status}).`); else setError("");
  }, [id]);
  useEffect(() => { const controller = new AbortController(); void load(controller.signal).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "API unavailable."); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [load]);
  return <div className="app-page"><PageHeader eyebrow="PROJECT / RUNS" title={project ? `${project.name} runs` : "Run history"} description="Runs are queued only from approved plans and use this project's verified authorization snapshot." action={project ? { href: `/app/projects/${id}/plans/new`, label: "Create test" } : undefined} />{error && <div className="app-alert error" role="alert">{error}</div>}{loading ? <p role="status">Loading run history…</p> : runs.length ? <Panel className="table-panel"><div className="run-table">{runs.map((run) => <Link className="run-list-row" href={`/app/runs/${run.id}`} key={run.id}><span className="row-main"><strong>{String(run.summary_json.goal ?? "Approved QA plan")}</strong><small>Started {run.started_at ? new Date(run.started_at).toLocaleString() : `queued ${new Date(run.created_at).toLocaleString()}`} · {run.id.slice(0, 8)}</small></span><StatusPill value={run.verdict ?? run.status} /><span className="text-muted">Open</span></Link>)}</div></Panel> : <EmptyState title="No runs for this project" text="Create a test plan, review its steps, and approve it before execution can start." action={project ? { href: `/app/projects/${id}/plans/new`, label: "Create a test plan" } : { href: "/app/projects", label: "Back to projects" }} />}</div>;
}
