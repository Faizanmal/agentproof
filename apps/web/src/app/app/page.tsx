"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, FolderKanban, ShieldCheck } from "lucide-react";
import { createApiClient } from "@agentproof/contracts";
import type { components } from "@agentproof/contracts/schema";
import { EmptyState, PageHeader, Panel, StatusPill } from "@/components/app-ui";
import { useWorkspace } from "@/components/workspace-context";

type Project = components["schemas"]["ProjectView"];
type Run = components["schemas"]["RunView"];

export default function OverviewPage() {
  const { workspaces, selectedWorkspaceId } = useWorkspace();
  const [projects, setProjects] = useState<Project[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    if (!selectedWorkspaceId) { setProjects([]); setRuns([]); setLoading(false); return () => controller.abort(); }
    setLoading(true);
    void (async () => {
      try {
        const api = createApiClient();
        const result = await api.GET("/api/v1/projects", { params: { query: { workspace_id: selectedWorkspaceId, limit: 25 } }, signal: controller.signal });
        if (!result.data) throw new Error(result.response.status === 401 ? "Your session expired. Sign in again." : `Projects could not load (${result.response.status}).`);
        const records = result.data.data;
        setProjects(records);
        const runPages = await Promise.all(records.slice(0, 8).map((project) => api.GET("/api/v1/projects/{project_id}/runs", { params: { path: { project_id: project.id }, query: { limit: 5 } }, signal: controller.signal })));
        setRuns(runPages.flatMap((page) => page.data?.data ?? []).sort((left, right) => right.created_at.localeCompare(left.created_at)).slice(0, 8));
        setError("");
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not connect to AgentProof.");
      } finally { if (!controller.signal.aborted) setLoading(false); }
    })();
    return () => controller.abort();
  }, [selectedWorkspaceId]);

  return (
    <div className="app-page">
      <PageHeader eyebrow="WORKSPACE OVERVIEW" title="Good testing starts with clear scope." description="Track your verified targets, reviewed plans, and browser runs from one place." action={workspaces.length ? { href: "/app/projects/new", label: "New project" } : undefined} />
      {error && <div className="app-alert error" role="alert">{error}<button className="text-button" onClick={() => location.reload()}>Retry</button></div>}
      {!selectedWorkspaceId ? <EmptyState title="Create your first workspace" text="Workspaces contain your projects, verified targets, and test-only credentials." action={{ href: "/app/settings", label: "Set up workspace" }} /> : loading ? <p className="app-loading-inline" role="status">Loading your workspace data…</p> : (
        <>
          <div className="overview-metrics" aria-label="Workspace totals">
            <Panel><p className="metric-label">Projects</p><p className="metric-value">{projects.length}</p><p className="metric-note">Loaded from this workspace</p></Panel>
            <Panel><p className="metric-label">Recent runs</p><p className="metric-value">{runs.length}</p><p className="metric-note">Latest 5 runs for up to 8 listed projects</p></Panel>
            <Panel><p className="metric-label">Verification</p><p className="metric-value metric-word">Review targets</p><p className="metric-note">Open a project to review origin proof</p></Panel>
          </div>
          <div className="overview-columns">
            <Panel>
              <div className="panel-heading"><div><h2>Projects</h2><p>Only projects visible to your workspace membership.</p></div><Link href="/app/projects" className="subtle-link">View all</Link></div>
              {projects.length ? <div className="app-list">{projects.slice(0, 5).map((project) => <Link className="list-row" key={project.id} href={`/app/projects/${project.id}`}><span className="row-icon"><FolderKanban size={17} aria-hidden="true" /></span><span className="row-main"><strong>{project.name}</strong><small>{project.base_url}</small></span><StatusPill value={project.status} /><ArrowRight size={16} aria-hidden="true" /></Link>)}</div> : <EmptyState title="No projects yet" text="Add a project for a site you own or are explicitly authorized to test." action={{ href: "/app/projects/new", label: "Create a project" }} />}
            </Panel>
            <Panel>
              <div className="panel-heading"><div><h2>Recent runs</h2><p>Live run data from your projects.</p></div></div>
              {runs.length ? <div className="app-list">{runs.slice(0, 6).map((run) => <Link className="list-row" key={run.id} href={`/app/runs/${run.id}`}><span className="row-icon"><ShieldCheck size={17} aria-hidden="true" /></span><span className="row-main"><strong>{String(run.summary_json.goal ?? "QA run")}</strong><small>{new Date(run.created_at).toLocaleString()}</small></span><StatusPill value={run.verdict ?? run.status} /></Link>)}</div> : <div className="app-empty compact"><p>No runs have been requested in this workspace.</p><Link href="/app/projects">Choose a verified project</Link></div>}
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}
