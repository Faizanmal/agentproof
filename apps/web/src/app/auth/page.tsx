import Link from "next/link";
import { LockKeyhole } from "lucide-react";
export default function AuthPage() {
  return (
    <section className="center-page">
      <div className="empty-icon">
        <LockKeyhole size={28} aria-hidden="true" />
      </div>
      <p className="eyebrow">IDENTITY & ACCESS</p>
      <h1>Sign in</h1>
      <p className="status-label">not connected</p>
      <p>
        Authentication is not available in this foundation preview. No
        credentials are collected.
      </p>
      <Link className="button secondary" href="/projects">
        View the project shell
      </Link>
    </section>
  );
}
