"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
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
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const loading = loadedFor !== id;

  useEffect(() => {
    const controller = new AbortController();
    const projectId = id;
    const api = createApiClient();
    void Promise.all([
      api.GET("/api/v1/projects/{project_id}", { params: { path: { project_id: projectId } }, signal: controller.signal }),
      api.GET("/api/v1/projects/{project_id}/runs", { params: { path: { project_id: projectId }, query: { limit: 100 } }, signal: controller.signal }),
    ])
      .then(([projectResult, runResult]) => {
        if (controller.signal.aborted) return;
        if (!projectResult.data) {
          setError(projectResult.response.status === 404 ? "Project not found or forbidden." : "Project could not be loaded.");
          setLoadedFor(projectId);
          return;
        }
        setProject(projectResult.data);
        setRuns(runResult.data?.data ?? []);
        if (!runResult.data) setError(`Run history could not load (HTTP ${runResult.response.status}).`);
        else setError("");
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

  return (
    <div className="app-page">
      <PageHeader
        eyebrow="PROJECT / RUNS"
        title={project ? `${project.name} runs` : "Run history"}
        description="Runs are queued only from approved plans and use this project's verified authorization snapshot."
        action={project ? { href: `/app/projects/${id}/plans/new`, label: "Create test" } : undefined}
      />
      {error && (
        <div className="app-alert error" role="alert">
          {error}
        </div>
      )}
      {loading ? (
        <p role="status">Loading run history…</p>
      ) : runs.length ? (
        <Panel className="table-panel">
          <div className="run-table">
            {runs.map((run) => (
              <Link className="run-list-row" href={`/app/runs/${run.id}`} key={run.id}>
                <span className="row-main">
                  <strong>{String(run.summary_json.goal ?? "Approved QA plan")}</strong>
                  <small>
                    Started{" "}
                    {run.started_at
                      ? new Date(run.started_at).toLocaleString()
                      : `queued ${new Date(run.created_at).toLocaleString()}`}{" "}
                    · {run.id.slice(0, 8)}
                  </small>
                </span>
                <StatusPill value={run.verdict ?? run.status} />
                <span className="text-muted">Open</span>
              </Link>
            ))}
          </div>
        </Panel>
      ) : (
        <EmptyState
          title="No runs for this project"
          text="Create a test plan, review its steps, and approve it before execution can start."
          action={
            project
              ? { href: `/app/projects/${id}/plans/new`, label: "Create a test plan" }
              : { href: "/app/projects", label: "Back to projects" }
          }
        />
      )}
    </div>
  );
}
