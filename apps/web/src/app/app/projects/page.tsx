"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight, Plus } from "lucide-react";
import { createApiClient } from "@agentproof/contracts";
import type { components } from "@agentproof/contracts/schema";
import { EmptyState, PageHeader, Panel, StatusPill } from "@/components/app-ui";
import { useWorkspace } from "@/components/workspace-context";

type Project = components["schemas"]["ProjectView"];
export default function ProjectsPage() {
  const { selectedWorkspaceId, workspaces } = useWorkspace();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const loading = Boolean(selectedWorkspaceId) && loadedFor !== selectedWorkspaceId;

  useEffect(() => {
    if (!selectedWorkspaceId) return;
    const workspaceId = selectedWorkspaceId;
    const controller = new AbortController();
    createApiClient()
      .GET("/api/v1/projects", {
        params: { query: { workspace_id: workspaceId, limit: 100 } },
        signal: controller.signal,
      })
      .then(({ data, response }) => {
        if (controller.signal.aborted) return;
        if (!data) {
          setError(
            response.status === 401
              ? "Your session expired. Sign in again."
              : response.status === 403
                ? "Your membership does not allow this project list."
                : `Projects could not load (${response.status}).`,
          );
        } else {
          setProjects(data.data);
          setError("");
        }
        setLoadedFor(workspaceId);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError(
            "AgentProof is offline. Your projects are not available until the API reconnects.",
          );
          setLoadedFor(workspaceId);
        }
      });
    return () => controller.abort();
  }, [selectedWorkspaceId]);

  return (
    <div className="app-page">
      <PageHeader
        eyebrow="WORKSPACE / PROJECTS"
        title="Projects"
        description="Targets and tests are scoped to the selected workspace."
        action={workspaces.length ? { href: "/app/projects/new", label: "New project" } : undefined}
      />
      {error && (
        <div role="alert" className="app-alert error">
          {error}
          <button className="text-button" onClick={() => location.reload()}>
            Retry
          </button>
        </div>
      )}
      {!selectedWorkspaceId ? (
        <EmptyState
          title="Choose or create a workspace"
          text="A workspace is required before you can add projects."
          action={{ href: "/app/settings", label: "Open settings" }}
        />
      ) : loading ? (
        <p role="status" className="app-loading-inline">
          Loading projects…
        </p>
      ) : projects.length === 0 ? (
        <EmptyState
          title="No projects in this workspace"
          text="Add the exact origin of a site you own or have permission to test."
          action={{ href: "/app/projects/new", label: "Create your first project" }}
        />
      ) : (
        <Panel className="table-panel">
          <div className="table-heading">
            <div>
              <h2>All projects</h2>
              <p>
                {projects.length} project{projects.length === 1 ? "" : "s"} loaded from the API
              </p>
            </div>
            <Link href="/app/projects/new" className="button secondary">
              <Plus size={16} aria-hidden="true" />
              Add project
            </Link>
          </div>
          <div className="project-table" role="table" aria-label="Projects">
            <div className="project-table-head" role="row">
              <span role="columnheader">Project</span>
              <span role="columnheader">Origin</span>
              <span role="columnheader">Status</span>
              <span role="columnheader">Created</span>
              <span className="sr-only">Open</span>
            </div>
            {projects.map((project) => (
              <Link
                role="row"
                className="project-table-row"
                key={project.id}
                href={`/app/projects/${project.id}`}
              >
                <span role="cell">
                  <strong>{project.name}</strong>
                  <small>Project {project.id.slice(0, 8)}</small>
                </span>
                <span role="cell" className="origin-cell">
                  {project.base_url}
                </span>
                <span role="cell">
                  <StatusPill value={project.status} />
                </span>
                <span role="cell">{new Date(project.created_at).toLocaleDateString()}</span>
                <span role="cell">
                  <ArrowUpRight size={16} aria-label="Open project" />
                </span>
              </Link>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
