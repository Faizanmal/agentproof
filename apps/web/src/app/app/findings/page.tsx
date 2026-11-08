"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { createApiClient } from "@agentproof/contracts";
import type { components } from "@agentproof/contracts/schema";
import { EmptyState, PageHeader, Panel, StatusPill } from "@/components/app-ui";
import { useWorkspace } from "@/components/workspace-context";

type Finding = components["schemas"]["FindingView"];
type Project = components["schemas"]["ProjectView"];
type Run = components["schemas"]["RunView"];
type FindingRecord = { finding: Finding; run: Run; project: Project };
export default function FindingsPage() {
  const { workspaces } = useWorkspace();
  const [items, setItems] = useState<FindingRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const api = createApiClient();
        const projectPages = await Promise.all(
          workspaces.map((workspace) =>
            api.GET("/api/v1/projects", {
              params: { query: { workspace_id: workspace.id, limit: 25 } },
              signal: controller.signal,
            }),
          ),
        );
        const projects = projectPages.flatMap((page) => page.data?.data ?? []);
        const runPages = await Promise.all(
          projects.slice(0, 20).map((project) =>
            api.GET("/api/v1/projects/{project_id}/runs", {
              params: {
                path: { project_id: project.id },
                query: { limit: 5 },
              },
              signal: controller.signal,
            }),
          ),
        );
        const runPairs = runPages.flatMap((page, index) =>
          (page.data?.data ?? []).map((run) => ({
            run,
            project: projects[index],
          })),
        );
        const findingPages = await Promise.all(
          runPairs.slice(0, 50).map(({ run }) =>
            api.GET("/api/v1/runs/{run_id}/findings", {
              params: { path: { run_id: run.id }, query: { limit: 20 } },
              signal: controller.signal,
            }),
          ),
        );
        if (controller.signal.aborted) return;
        setItems(
          findingPages.flatMap((page, index) =>
            (page.data?.data ?? []).map((finding) => ({
              finding,
              run: runPairs[index].run,
              project: runPairs[index].project,
            })),
          ),
        );
      } catch {
        if (!controller.signal.aborted)
          setError(
            "Findings could not load. Check your session and API connection.",
          );
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [workspaces]);
  return (
    <div className="app-page">
      <PageHeader
        eyebrow="WORKSPACE / FINDINGS"
        title="Findings"
        description="Observed assertion findings from the latest five runs in up to twenty accessible projects. Open a project to review its complete run history."
      />
      {error && (
        <div className="app-alert error" role="alert">
          {error}
        </div>
      )}
      {loading ? (
        <p role="status">Loading finding evidence…</p>
      ) : items.length ? (
        <div className="finding-cards">
          {items.map(({ finding, run, project }) => (
            <Panel
              className="finding-card"
              key={`${run.id}-${finding.fingerprint}`}
            >
              <div className="finding-title-row">
                <div>
                  <p className="app-eyebrow">
                    {project.name} · RUN {run.id.slice(0, 8)}
                  </p>
                  <h2>{finding.title}</h2>
                  <p>
                    {finding.classification} · {finding.confidence.toFixed(2)}{" "}
                    confidence
                  </p>
                </div>
                <StatusPill value={finding.severity} />
              </div>
              <div className="finding-summary">
                <AlertTriangle size={16} aria-hidden="true" />
                <span>
                  {finding.repro_steps_json.length} reproduction steps ·{" "}
                  {finding.evidence_artifact_ids.length} evidence artifacts
                </span>
              </div>
              <Link className="subtle-link" href={`/app/runs/${run.id}/report`}>
                Open evidence report <span aria-hidden="true">→</span>
              </Link>
            </Panel>
          ))}
        </div>
      ) : (
        <Panel>
          <EmptyState
            title="No findings recorded"
            text="Findings appear when actual browser assertions record a mismatch. Plans, model suggestions, and timeouts alone are not reported as confirmed bugs."
          />
        </Panel>
      )}
    </div>
  );
}
