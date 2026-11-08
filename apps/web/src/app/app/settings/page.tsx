"use client";

import { useEffect, useState } from "react";
import { createApiClient, getIdentity } from "@agentproof/contracts";
import type { components } from "@agentproof/contracts/schema";
import { PageHeader, Panel } from "@/components/app-ui";
import { useWorkspace } from "@/components/workspace-context";

type Identity = components["schemas"]["Identity"];
type Member = components["schemas"]["MemberView"];
export default function SettingsPage() {
  const { workspaces, selectedWorkspaceId, setSelectedWorkspaceId } = useWorkspace();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [workspaceName, setWorkspaceName] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { let active = true; void getIdentity().then((value) => { if (active) setIdentity(value ?? null); }).catch(() => { if (active) setError("The account details could not load."); }); return () => { active = false; }; }, []);
  useEffect(() => { const controller = new AbortController(); if (!selectedWorkspaceId) { setMembers([]); return () => controller.abort(); } void createApiClient().GET("/api/v1/workspaces/{workspace_id}/members", { params: { path: { workspace_id: selectedWorkspaceId }, query: { limit: 100 } }, signal: controller.signal }).then(({ data }) => { if (!controller.signal.aborted) setMembers(data?.data ?? []); }).catch(() => { if (!controller.signal.aborted) setMembers([]); }); return () => controller.abort(); }, [selectedWorkspaceId]);
  async function createWorkspace(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); const user = await getIdentity(); if (!user) { setError("Session expired. Sign in again."); return; }
    setBusy(true); setMessage(""); setError("");
    try {
      const result = await createApiClient(user.csrf_token).POST("/api/v1/workspaces", { body: { name: workspaceName.trim() } });
      if (!result.data) throw new Error(`Workspace could not be created (HTTP ${result.response.status}).`);
      setMessage("Workspace created. Reload this page to refresh the workspace switcher."); setWorkspaceName(""); location.reload();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Workspace service unavailable."); }
    finally { setBusy(false); }
  }
  return <div className="app-page"><PageHeader eyebrow="ACCOUNT / SETTINGS" title="Settings" description="Manage your account and the workspaces you can access." />{error && <div role="alert" className="app-alert error">{error}</div>}{message && <p role="status" className="app-alert success">{message}</p>}
    <Panel><div className="panel-heading"><div><h2>Account</h2><p>Your secure session uses HTTP-only cookies.</p></div></div><div className="settings-field"><span>Email</span><strong>{identity?.user.email ?? "Loading…"}</strong></div><div className="settings-field"><span>Email verification</span><strong>{identity?.user.email_verified_at ? `Verified ${new Date(identity.user.email_verified_at).toLocaleDateString()}` : "Verification required for plan approval and runs"}</strong></div>{identity && !identity.user.email_verified_at && <p className="field-note">Use the verification message delivered to your account. In development, the message is available from the existing verification flow.</p>}</Panel>
    <Panel><div className="panel-heading"><div><h2>Workspace membership</h2><p>Your workspace access is loaded from server-side membership records.</p></div></div>{workspaces.length ? <div className="membership-list">{workspaces.map((workspace) => { const workspaceMembers = workspace.id === selectedWorkspaceId ? members : []; const role = workspaceMembers.find((member) => member.user_id === identity?.user.id)?.role; return <button className={`membership-row ${workspace.id === selectedWorkspaceId ? "selected" : ""}`} key={workspace.id} onClick={() => setSelectedWorkspaceId(workspace.id)}><span className="workspace-avatar">{workspace.name.slice(0, 1).toUpperCase()}</span><span><strong>{workspace.name}</strong><small>{role ?? (workspace.id === selectedWorkspaceId ? "Member role unavailable" : "Select to load your role")}</small></span></button>; })}</div> : <p className="text-muted">No workspace memberships yet.</p>}<form className="app-form create-workspace-form" onSubmit={createWorkspace}><label htmlFor="workspace-name">Create a workspace</label><input id="workspace-name" value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} maxLength={120} required placeholder="Product QA" /><button className="button primary" disabled={busy}>{busy ? "Creating…" : "Create workspace"}</button></form></Panel>
    <Panel><div className="panel-heading"><div><h2>Security notes</h2><p>Credentials and authorization are scoped to projects.</p></div></div><ul className="security-list"><li>Access and refresh tokens are held in secure, HTTP-only cookies.</li><li>Mutation requests use a session-bound CSRF token held only in memory.</li><li>Stored test credentials are encrypted and write-only in the interface.</li><li>Run evidence is private and requires workspace authorization.</li></ul></Panel>
  </div>;
}
