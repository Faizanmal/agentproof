import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowRight, CircleHelp } from "lucide-react";

export function PageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description?: string;
  action?: { href: string; label: string };
}) {
  return (
    <header className="page-heading">
      <div><p className="app-eyebrow">{eyebrow}</p><h1>{title}</h1>{description && <p className="page-description">{description}</p>}</div>
      {action && <Link className="button primary" href={action.href}>{action.label}<ArrowRight size={16} aria-hidden="true" /></Link>}
    </header>
  );
}

export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`app-panel ${className}`}>{children}</section>;
}

export function EmptyState({ title, text, action }: { title: string; text: string; action?: { href: string; label: string } }) {
  return <div className="app-empty"><span className="empty-state-icon"><CircleHelp size={21} aria-hidden="true" /></span><h2>{title}</h2><p>{text}</p>{action && <Link className="button primary" href={action.href}>{action.label}</Link>}</div>;
}

export function StatusPill({ value }: { value: string }) {
  const normalized = value.toLowerCase().replaceAll("_", "-");
  const className = ["pass", "completed"].includes(normalized) ? "success" : ["fail", "failed", "timed-out"].includes(normalized) ? "danger" : ["error", "inconclusive", "investigating"].includes(normalized) ? "warning" : "neutral";
  return <span className={`status-pill ${className}`} aria-label={`Status: ${value.replaceAll("_", " ")}`}>{value.replaceAll("_", " ")}</span>;
}
