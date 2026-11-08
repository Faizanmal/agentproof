"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { PlanReview } from "./plan-review";
import { createApiClient, getIdentity } from "@agentproof/contracts";
import type { components } from "@agentproof/contracts/schema";
type Workspace = components["schemas"]["WorkspaceView"];
type Project = components["schemas"]["ProjectView"];
export function ProjectWorkspace() {
  const [csrf, setCsrf] = useState("");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selected, setSelected] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [message, setMessage] = useState("Checking workspace access…");
  const [busy, setBusy] = useState(false);
  const [nextWorkspace, setNextWorkspace] = useState<string | null>(null);
  const [nextProject, setNextProject] = useState<string | null>(null);
  const [challenge, setChallenge] = useState<{
    projectId: string;
    originId: string;
    value: string;
  } | null>(null);
  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const api = createApiClient();
        const identity = await getIdentity();
        if (!identity) {
          if (active) setMessage("Sign in to access your workspace.");
          return;
        }
        const result = await api.GET("/api/v1/workspaces");
        if (active) {
          setCsrf(identity.csrf_token);
          setWorkspaces(result.data?.data ?? []);
          setNextWorkspace(result.data?.next_cursor ?? null);
          setSelected(result.data?.data[0]?.id ?? "");
          setMessage(
            result.data
              ? "Workspace access connected."
              : "Workspaces could not load.",
          );
        }
      } catch {
        if (active) setMessage("The API is unavailable.");
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    let active = true;
    if (!selected) return;
    createApiClient()
      .GET("/api/v1/projects", {
        params: { query: { workspace_id: selected } },
      })
      .then(({ data }) => {
        if (active) {
          setProjects(data?.data ?? []);
          setNextProject(data?.next_cursor ?? null);
          if (!data) setMessage("Projects could not load.");
        }
      })
      .catch(() => {
        if (active) setMessage("The API is unavailable.");
      });
    return () => {
      active = false;
    };
  }, [selected]);
  async function create(
    event: React.FormEvent<HTMLFormElement>,
    kind: "workspace" | "project",
  ) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    try {
      const api = createApiClient(csrf);
      if (kind === "workspace") {
        const result = await api.POST("/api/v1/workspaces", {
          body: { name: String(data.get("name")) },
        });
        if (result.data) {
          setWorkspaces([...workspaces, result.data]);
          setSelected(result.data.id);
          setMessage("Workspace created.");
          form.reset();
        } else
          setMessage(
            "Workspace creation failed (HTTP " + result.response.status + ").",
          );
      } else {
        const result = await api.POST("/api/v1/projects", {
          body: {
            workspace_id: selected,
            name: String(data.get("name")),
            base_url: String(data.get("base_url")),
          },
        });
        if (result.data) {
          setProjects([result.data, ...projects]);
          setMessage(
            "Project created. Target verification is required before testing.",
          );
          form.reset();
        } else
          setMessage(
            "Project creation failed (HTTP " + result.response.status + ").",
          );
      }
    } catch {
      setMessage("The API is unavailable.");
    } finally {
      setBusy(false);
    }
  }
  async function authorize(project: Project, form: HTMLFormElement) {
    const origin = String(new FormData(form).get("origin") ?? project.base_url);
    setBusy(true);
    try {
      const issued = await createApiClient(csrf).POST(
        "/api/v1/projects/{project_id}/verification-challenges",
        {
          params: { path: { project_id: project.id } },
          body: {
            origin,
            method: String(new FormData(form).get("method") ?? "https-file") as
              "https-file" | "dns-txt" | "demo",
          },
        },
      );
      if (!issued.data) {
        setMessage(
          "Origin challenge failed (HTTP " + issued.response.status + ").",
        );
        return;
      }
      setChallenge({
        projectId: project.id,
        originId: issued.data.id,
        value: issued.data.challenge,
      });
      setMessage(issued.data.instructions);
      form.reset();
    } catch {
      setMessage("The API is unavailable.");
    } finally {
      setBusy(false);
    }
  }
  async function confirmOrigin(projectId: string, originId: string) {
    setBusy(true);
    try {
      const result = await createApiClient(csrf).POST(
        "/api/v1/projects/{project_id}/verify-target",
        {
          params: { path: { project_id: projectId } },
          body: { origin_id: originId, attestation: true },
        },
      );
      if (result.data?.verified_at) {
        setChallenge(null);
        setMessage("Target origin verified.");
      } else {
        const code =
          typeof result.error === "object" && result.error && "code" in result.error
            ? String((result.error as { code?: string }).code ?? "")
            : "";
        const messages: Record<string, string> = {
          CHALLENGE_EXPIRED:
            "This challenge expired. Issue a fresh challenge, republish the new token, then verify again.",
          CHALLENGE_REUSED:
            "This challenge was already used. Issue a fresh challenge and publish the new token.",
          PROOF_NOT_FOUND:
            "No proof file or TXT record was found. Publish the exact token, then retry.",
          PROOF_HTTP_ERROR:
            "The proof URL did not return HTTP 200. Confirm the well-known path is reachable over HTTPS.",
          PROOF_EMPTY_OR_INVALID:
            "The proof body was empty or longer than 128 bytes. Publish only the exact challenge token.",
          PROOF_FETCH_ERROR:
            "AgentProof could not fetch the proof. Fix TLS/DNS/network and retry; do not mark verified manually.",
          VERIFICATION_FAILED:
            "A proof was fetched but did not match this challenge. Republish the exact current token.",
        };
        setMessage(
          messages[code] ??
            "Target verification failed (HTTP " + result.response.status + ").",
        );
      }
    } catch {
      setMessage("The API is unavailable.");
    } finally {
      setBusy(false);
    }
  }
  async function saveLogin(projectId: string, form: HTMLFormElement) {
    const data = new FormData(form);
    if (data.get("test_account") !== "on") {
      setMessage("Confirm this is a TEST account.");
      return;
    }
    setBusy(true);
    try {
      const result = await createApiClient(csrf).POST(
        "/api/v1/projects/{project_id}/secrets",
        {
          params: { path: { project_id: projectId } },
          body: {
            label: String(data.get("label") ?? ""),
            username: String(data.get("username") ?? ""),
            password: String(data.get("password") ?? ""),
            confirmed_test_account: true,
          },
        },
      );
      if (result.data) {
        form.reset();
        setMessage("Test login stored for " + result.data.label + ".");
      } else
        setMessage(
          "Test login was not stored (HTTP " + result.response.status + ").",
        );
    } catch {
      setMessage("The API is unavailable.");
    } finally {
      setBusy(false);
    }
  }
  async function more(kind: "workspace" | "project") {
    setBusy(true);
    try {
      const api = createApiClient();
      if (kind === "workspace" && nextWorkspace) {
        const result = await api.GET("/api/v1/workspaces", {
          params: { query: { cursor: nextWorkspace } },
        });
        if (result.data) {
          setWorkspaces([...workspaces, ...result.data.data]);
          setNextWorkspace(result.data.next_cursor ?? null);
        } else setMessage("More workspaces could not load.");
      }
      if (kind === "project" && nextProject) {
        const result = await api.GET("/api/v1/projects", {
          params: { query: { workspace_id: selected, cursor: nextProject } },
        });
        if (result.data) {
          setProjects([...projects, ...result.data.data]);
          setNextProject(result.data.next_cursor ?? null);
        } else setMessage("More projects could not load.");
      }
    } catch {
      setMessage("The API is unavailable.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <p role="status" aria-live="polite">
        {message}
      </p>
      {!csrf ? (
        <Link className="button primary" href="/auth">
          Sign in
        </Link>
      ) : (
        <>
          <form
            className="connected-form"
            onSubmit={(e) => create(e, "workspace")}
          >
            <label>
              New workspace name
              <input name="name" required maxLength={120} />
            </label>
            <button className="button secondary" disabled={busy}>
              Create workspace
            </button>
          </form>
          <label className="workspace-select">
            Workspace
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="">Select a workspace</option>
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </label>
          {nextWorkspace && (
            <button
              className="button secondary"
              disabled={busy}
              onClick={() => more("workspace")}
            >
              Load more workspaces
            </button>
          )}
          {selected && (
            <>
              <form
                className="connected-form"
                onSubmit={(e) => create(e, "project")}
              >
                <label>
                  Project name
                  <input name="name" required maxLength={120} />
                </label>
                <label>
                  Authorized target URL
                  <input
                    name="base_url"
                    type="url"
                    required
                    placeholder="https://your-owned-site.example"
                    maxLength={2048}
                  />
                </label>
                <button className="button primary" disabled={busy}>
                  Create project
                </button>
              </form>
              <div className="workflow-grid">
                {projects.map((p) => (
                  <article className="project-record" key={p.id}>
                    <h2>{p.name}</h2>
                    <p>{p.base_url}</p>
                    <p>{p.status}</p>
                    <PlanReview projectId={p.id} csrf={csrf} />
                    <form
                      className="connected-form"
                      onSubmit={(event) => {
                        event.preventDefault();
                        void authorize(p, event.currentTarget);
                      }}
                    >
                      <label>
                        Exact origin
                        <input
                          name="origin"
                          type="url"
                          required
                          defaultValue={p.base_url}
                          maxLength={2048}
                        />
                      </label>
                      <label>
                        Proof method
                        <select name="method">
                          <option value="https-file">HTTPS file</option>
                          <option value="dns-txt">DNS TXT</option>
                          <option value="demo">
                            Administrative local demo
                          </option>
                        </select>
                      </label>
                      <button className="button secondary" disabled={busy}>
                        Issue verification challenge
                      </button>
                    </form>
                    {challenge?.projectId === p.id && (
                      <>
                        <label>
                          Single-use challenge token
                          <input readOnly value={challenge.value} />
                        </label>
                        <button
                          className="button primary"
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            window.confirm(
                              "I attest that I own this target or have explicit authorization to test it.",
                            ) && void confirmOrigin(p.id, challenge.originId)
                          }
                        >
                          Confirm published challenge
                        </button>
                      </>
                    )}
                    <form
                      className="connected-form"
                      onSubmit={(event) => {
                        event.preventDefault();
                        void saveLogin(p.id, event.currentTarget);
                      }}
                    >
                      <label>
                        Test login label
                        <input
                          name="label"
                          required
                          pattern="[a-z][a-z0-9-]{0,78}"
                          autoComplete="off"
                        />
                      </label>
                      <label>
                        Test username
                        <input name="username" required autoComplete="off" />
                      </label>
                      <label>
                        Test password
                        <input
                          name="password"
                          type="password"
                          required
                          autoComplete="new-password"
                        />
                      </label>
                      <label>
                        <input name="test_account" type="checkbox" required /> I
                        confirm these credentials are for a TEST account.
                      </label>
                      <button className="button secondary" disabled={busy}>
                        Store test login
                      </button>
                    </form>
                  </article>
                ))}
              </div>
              {projects.length === 0 && <p>No projects in this workspace.</p>}
              {nextProject && (
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => more("project")}
                >
                  Load more projects
                </button>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
