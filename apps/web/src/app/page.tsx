import Link from "next/link";
import {
  ArrowRight,
  FileCheck2,
  Fingerprint,
  ScanLine,
  ShieldCheck,
} from "lucide-react";
const journey = [
  {
    icon: Fingerprint,
    title: "Authorize the target",
    text: "Define exact origins and safe testing rules for a site you are allowed to test.",
  },
  {
    icon: ScanLine,
    title: "Review the journey",
    text: "Inspect a constrained test plan before an isolated browser takes any action.",
  },
  {
    icon: FileCheck2,
    title: "Follow the evidence",
    text: "Assess observed assertions, reproducible findings, and sanitized artifacts.",
  },
];
export default function Home() {
  return (
    <>
      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">
            <span className="eyebrow-line" />
            BUILT FOR ACCOUNTABLE QA
          </p>
          <h1>
            Evidence before
            <br />
            <span>confidence.</span>
          </h1>
          <p className="hero-description">
            Turn a testing goal into a reviewable browser journey. Keep
            authorization explicit, execution contained, and every finding
            grounded in evidence.
          </p>
          <div className="hero-actions">
            <Link className="button primary" href="/projects">
              Explore the project shell{" "}
              <ArrowRight size={17} aria-hidden="true" />
            </Link>
            <Link className="text-link" href="#workflow">
              See the intended workflow{" "}
              <ArrowRight size={16} aria-hidden="true" />
            </Link>
          </div>
          <p className="availability">
            Foundation preview · Authentication and test execution are not
            connected.
          </p>
        </div>
        <aside className="boundary-card" aria-labelledby="boundary-title">
          <div className="card-topline">
            <span>EXECUTION BOUNDARY</span>
            <ShieldCheck size={19} aria-hidden="true" />
          </div>
          <h2 id="boundary-title">
            Every action needs
            <br />a clear permission.
          </h2>
          <div className="boundary-flow">
            <div>
              <span className="flow-mark">A</span>
              <span>Authorized origin</span>
            </div>
            <span className="flow-connector" aria-hidden="true" />
            <div>
              <span className="flow-mark">B</span>
              <span>Validated plan</span>
            </div>
            <span className="flow-connector" aria-hidden="true" />
            <div>
              <span className="flow-mark">C</span>
              <span>Isolated execution</span>
            </div>
          </div>
          <p className="card-note">
            Planned architecture. Containment will be verified before browser
            execution is enabled.
          </p>
        </aside>
      </section>
      <section
        id="workflow"
        className="workflow"
        aria-labelledby="workflow-title"
      >
        <div className="section-heading">
          <p className="eyebrow">FROM INTENT TO OBSERVATION</p>
          <h2 id="workflow-title">A journey you can inspect.</h2>
        </div>
        <div className="workflow-grid">
          {journey.map(({ icon: Icon, title, text }) => (
            <article key={title}>
              <Icon size={25} strokeWidth={1.5} aria-hidden="true" />
              <h3>{title}</h3>
              <p>{text}</p>
            </article>
          ))}
        </div>
      </section>
    </>
  );
}
