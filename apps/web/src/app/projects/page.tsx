import Link from "next/link";
import { FolderLock, Plus } from "lucide-react";
import { ApiStatus } from "@/components/api-status";
export default function ProjectsPage() {
  return (
    <section className="project-page">
      <div className="project-heading">
        <div>
          <p className="eyebrow">WORKSPACE / PROJECTS</p>
          <h1>Project workspace</h1>
        </div>
        <button
          className="button primary"
          disabled
          aria-describedby="project-note"
        >
          <Plus size={17} aria-hidden="true" />
          Create project
        </button>
      </div>
      <div className="project-empty">
        <div className="empty-icon">
          <FolderLock size={30} aria-hidden="true" />
        </div>
        <h2>Your authorized testing starts here.</h2>
        <p id="project-note">
          Project storage and authentication are not connected. Creation becomes
          available after workspace access is implemented.
        </p>
        <Link className="text-link" href="/auth">
          View authentication status
        </Link>
      </div>
      <ApiStatus />
    </section>
  );
}
