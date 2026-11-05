import Link from "next/link";
import { ShieldCheck } from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
export function SiteHeader() {
  return (
    <header className="site-header">
      <Link className="brand" href="/" aria-label="AgentProof home">
        <span className="brand-icon">
          <ShieldCheck size={23} aria-hidden="true" />
        </span>
        AgentProof<span className="stage">FOUNDATION</span>
      </Link>
      <nav aria-label="Main navigation">
        <Link href="/projects">Projects</Link>
        <Link href="/auth">Sign in</Link>
        <ThemeToggle />
      </nav>
    </header>
  );
}
