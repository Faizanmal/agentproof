"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { createApiClient, getIdentity } from "@agentproof/contracts";
import { PageHeader, Panel } from "@/components/app-ui";
import { useWorkspace } from "@/components/workspace-context";

export default function NewProjectPage() {
  const router = useRouter();
  const { selectedWorkspaceId, workspaces } = useWorkspace();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const origin = String(values.get("origin") ?? "").trim();
    try {
      const parsed = new URL(origin);
      if (
        !selectedWorkspaceId ||
        !["https:", "http:"].includes(parsed.protocol) ||
        parsed.username ||
        parsed.password ||
        parsed.pathname !== "/" ||
        parsed.search ||
        parsed.hash
      )
        throw new Error(
          "Enter an exact origin, such as https://staging.example.com, without a path or credentials.",
        );
    } catch (reason) {
      setMessage(
        reason instanceof Error ? reason.message : "Enter a valid origin URL.",
      );
      return;
    }
    const identity = await getIdentity();
    if (!identity) {
      setMessage("Your session expired. Sign in again.");
      return;
    }
    setBusy(true);
    setMessage("Creating project…");
    try {
      const result = await createApiClient(identity.csrf_token).POST(
        "/api/v1/projects",
        {
          body: {
            workspace_id: selectedWorkspaceId,
            name: String(values.get("name") ?? "").trim(),
            base_url: origin,
          },
        },
      );
      if (result.data) router.replace(`/app/projects/${result.data.id}`);
      else
        setMessage(
          result.response.status === 403
            ? "You do not have permission to create projects in this workspace."
            : `Project could not be created (HTTP ${result.response.status}).`,
        );
    } catch {
      setMessage("The API is unavailable. Your project was not created.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="app-page narrow-page">
      <PageHeader
        eyebrow="PROJECTS / NEW"
        title="Add a target project"
        description="Register the exact site origin. AgentProof will require proof before planning or testing."
      />
      <Panel>
        <form className="app-form" onSubmit={submit}>
          <label htmlFor="project-name">Project name</label>
          <input
            id="project-name"
            name="name"
            required
            maxLength={120}
            placeholder="Staging storefront"
          />
          <label htmlFor="project-origin">Exact origin</label>
          <input
            id="project-origin"
            name="origin"
            type="url"
            required
            maxLength={2048}
            placeholder="https://staging.example.com"
          />
          <p className="field-note">
            Origin only: no path, query, fragment, username, or password. Use a
            system you own or are explicitly authorized to test.
          </p>
          <label htmlFor="project-workspace">Workspace</label>
          <select
            id="project-workspace"
            value={selectedWorkspaceId}
            disabled
            onChange={() => undefined}
          >
            <option value="">
              {workspaces.length
                ? "Select a workspace"
                : "No workspace available"}
            </option>
            {workspaces.map((workspace) => (
              <option key={workspace.id} value={workspace.id}>
                {workspace.name}
              </option>
            ))}
          </select>
          {message && (
            <p role="status" className="form-status">
              {message}
            </p>
          )}
          <div className="form-actions">
            <Link className="button secondary" href="/app/projects">
              Cancel
            </Link>
            <button
              className="button primary"
              disabled={busy || !selectedWorkspaceId}
            >
              {busy ? "Creating…" : "Create project"}
            </button>
          </div>
        </form>
      </Panel>
    </div>
  );
}
