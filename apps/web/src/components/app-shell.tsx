"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  FolderKanban,
  LayoutDashboard,
  LogOut,
  Menu,
  Settings,
  ShieldCheck,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { createApiClient, getIdentity } from "@agentproof/contracts";
import type { components } from "@agentproof/contracts/schema";
import { ThemeToggle } from "@/components/theme-toggle";
import { WorkspaceContext } from "@/components/workspace-context";

type Identity = components["schemas"]["Identity"];
type Workspace = components["schemas"]["WorkspaceView"];

const navigation = [
  { href: "/app", label: "Overview", icon: LayoutDashboard, member: false },
  { href: "/app/projects", label: "Projects", icon: FolderKanban, member: true },
  { href: "/app/findings", label: "Findings", icon: AlertTriangle, member: true },
  { href: "/app/settings", label: "Settings", icon: Settings, member: false },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("");
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void getIdentity()
      .then(async (current) => {
        if (controller.signal.aborted) return;
        if (!current) {
          setIdentity(null);
          return;
        }
        setIdentity(current);
        const result = await createApiClient().GET("/api/v1/workspaces", {
          params: { query: { limit: 100 } },
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if (result.response.status === 401) {
          setIdentity(null);
          return;
        }
        setWorkspaces(result.data?.data ?? []);
        setSelectedWorkspaceId((currentId) => currentId || result.data?.data[0]?.id || "");
        setOffline(!result.response.ok);
      })
      .catch(() => {
        if (!controller.signal.aborted) setOffline(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [pathname]);

  async function logout() {
    if (!identity) return;
    try {
      const response = await createApiClient(identity.csrf_token).POST(
        "/api/v1/auth/logout",
      );
      if (response.response.ok) {
        setIdentity(null);
        router.replace("/login?reason=signed-out");
        router.refresh();
      } else setMessage("Sign out failed. Please try again.");
    } catch {
      setMessage("The API is unavailable; your session could not be ended.");
    }
  }

  if (loading) {
    return <main className="app-loading" role="status">Checking your secure session…</main>;
  }
  if (!identity) {
    return (
      <main className="app-auth-required">
        <div className="app-auth-card">
          <ShieldCheck size={24} aria-hidden="true" />
          <h1>{offline ? "Connection unavailable" : "Sign in required"}</h1>
          <p>
            {offline
              ? "AgentProof could not reach the API. Check your connection and retry."
              : "Your session may have expired. Sign in to continue to this workspace."}
          </p>
          {offline && <button className="button secondary" onClick={() => location.reload()}>Retry connection</button>}
          {!offline && <Link className="button primary" href={`/login?next=${encodeURIComponent(pathname)}`}>Sign in</Link>}
        </div>
      </main>
    );
  }

  const visibleNavigation = navigation.filter((item) => !item.member || workspaces.length > 0);
  return (
    <WorkspaceContext.Provider value={{ workspaces, selectedWorkspaceId, setSelectedWorkspaceId }}>
    <div className="app-frame">
      {menuOpen && <button className="app-scrim" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />}
      <aside className={`app-sidebar ${menuOpen ? "is-open" : ""}`} aria-label="Workspace navigation">
        <div className="app-brand-row">
          <Link href="/app" className="app-brand" onClick={() => setMenuOpen(false)}>
            <span className="app-brand-mark"><ShieldCheck size={19} aria-hidden="true" /></span>
            <span>AgentProof</span>
          </Link>
          <button className="icon-button app-mobile-close" aria-label="Close navigation" onClick={() => setMenuOpen(false)}><X size={18} /></button>
        </div>
        <p className="app-workspace-label">WORKSPACE</p>
        {workspaces.length > 0 ? <label className="app-workspace-select"><span className="sr-only">Active workspace</span><span className="workspace-avatar">{workspaces.find((workspace) => workspace.id === selectedWorkspaceId)?.name.slice(0, 1).toUpperCase() ?? "A"}</span><select aria-label="Active workspace" value={selectedWorkspaceId} onChange={(event) => setSelectedWorkspaceId(event.target.value)}>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}</select></label> : <div className="app-workspace-pill"><span className="workspace-avatar">A</span><span className="truncate">No workspace yet</span></div>}
        <nav className="app-nav" aria-label="Primary">
          {visibleNavigation.map(({ href, label, icon: Icon }) => {
            const active = href === "/app" ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);
            return <Link key={href} href={href} aria-current={active ? "page" : undefined} className={`app-nav-link ${active ? "active" : ""}`} onClick={() => setMenuOpen(false)}><Icon size={17} aria-hidden="true" /><span>{label}</span></Link>;
          })}
        </nav>
        <div className="app-sidebar-bottom">
          <div className="app-authenticated-user"><span className="user-dot" aria-hidden="true" /><span className="truncate">{identity.user.email}</span></div>
          <button className="app-nav-link app-logout" onClick={logout}><LogOut size={17} aria-hidden="true" /><span>Sign out</span></button>
        </div>
      </aside>
      <div className="app-main-column">
        <header className="app-topbar">
          <button className="icon-button app-mobile-menu" aria-label="Open navigation" onClick={() => setMenuOpen(true)}><Menu size={20} /></button>
          <div className="app-breadcrumb"><span>AgentProof</span><span aria-hidden="true">/</span><strong>{pathname.split("/").filter(Boolean).at(-1)?.replaceAll("-", " ") ?? "Overview"}</strong></div>
          <div className="app-topbar-actions"><span className="app-auth-status"><Activity size={14} aria-hidden="true" /> Session active</span><ThemeToggle /></div>
        </header>
        {offline && <div className="app-connection-banner" role="status">API connection interrupted. Data may be out of date.</div>}
        {message && <div className="app-inline-message" role="status">{message}</div>}
        <main className="app-content">{children}</main>
      </div>
    </div>
    </WorkspaceContext.Provider>
  );
}
