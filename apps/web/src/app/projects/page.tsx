import { ProjectWorkspace } from "@/components/project-workspace";
import { ApiStatus } from "@/components/api-status";
export default function ProjectsPage() {
  return (
    <section className="project-page">
      <p className="eyebrow">WORKSPACE / PROJECTS</p>
      <h1>Project workspace</h1>
      <ProjectWorkspace />
      <ApiStatus />
    </section>
  );
}
