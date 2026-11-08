"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ShieldCheck } from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
export function SiteHeader() {
  const pathname = usePathname();
  if (pathname === "/app" || pathname.startsWith("/app/")) return null;
  return (
    <header className="site-header">
      <Link className="brand" href="/">
        <span className="brand-icon">
          <ShieldCheck size={23} aria-hidden="true" />
        </span>
        AgentProof<span className="stage" aria-hidden="true">QA WORKSPACE</span>
      </Link>
      <nav aria-label="Main navigation">
        <Link href="/app/projects">Workspace</Link>
        <Link href="/login">Sign in</Link>
        <Link className="header-create" href="/register">Create account</Link>
        <ThemeToggle />
      </nav>
    </header>
  );
}
