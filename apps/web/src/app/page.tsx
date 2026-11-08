import Link from "next/link";
import {
  ArrowDownRight,
  ArrowRight,
  CheckCheck,
  Fingerprint,
  FileSearch,
  ShieldCheck,
} from "lucide-react";

const useCases = [
  {
    title: "Login smoke checks",
    detail:
      "Review a synthetic-account journey across sign-in and the first authenticated page.",
  },
  {
    title: "Staging regressions",
    detail:
      "Compare expected assertions with screenshots, browser observations, and redacted failures.",
  },
  {
    title: "Subscription scenarios",
    detail:
      "Exercise explicitly approved staging fixture flows without touching real billing.",
  },
];

export default function Home() {
  return (
    <>
      <section className="marketing-hero">
        <div className="marketing-copy">
          <p className="app-eyebrow">
            <span className="live-dot" aria-hidden="true" />
            AUTHORIZED BROWSER QA
          </p>
          <h1>
            Make every test
            <br />
            <em>accountable.</em>
          </h1>
          <p className="marketing-lead">
            Describe a browser journey. Verify the target belongs in scope.
            Review the plan before any run. Get reproducible artifacts grounded
            in what Chromium actually observed.
          </p>
          <div className="marketing-actions">
            <Link className="button primary" href="/register">
              Create your workspace <ArrowRight size={16} aria-hidden="true" />
            </Link>
            <Link className="subtle-link" href="#how-it-works">
              See how it works <ArrowDownRight size={15} aria-hidden="true" />
            </Link>
          </div>
          <p className="marketing-note">
            A plan is never a run. Every run requires verified target
            authorization and human approval.
          </p>
        </div>
        <div
          className="marketing-diagram"
          aria-label="Authorization flow: verify origin, review plan, inspect evidence"
        >
          <div className="diagram-heading">
            <span>THE CONTROL PATH</span>
            <ShieldCheck size={17} aria-hidden="true" />
          </div>
          <div className="diagram-step">
            <span className="diagram-icon">
              <Fingerprint size={16} aria-hidden="true" />
            </span>
            <div>
              <strong>Prove the origin</strong>
              <small>HTTPS file or DNS TXT challenge</small>
            </div>
            <span className="diagram-state">01</span>
          </div>
          <div className="diagram-line" aria-hidden="true" />
          <div className="diagram-step">
            <span className="diagram-icon">
              <CheckCheck size={16} aria-hidden="true" />
            </span>
            <div>
              <strong>Approve the plan</strong>
              <small>Actions, paths, and risk in scope</small>
            </div>
            <span className="diagram-state">02</span>
          </div>
          <div className="diagram-line" aria-hidden="true" />
          <div className="diagram-step">
            <span className="diagram-icon">
              <FileSearch size={16} aria-hidden="true" />
            </span>
            <div>
              <strong>Inspect real evidence</strong>
              <small>Assertions, screenshots, and trace</small>
            </div>
            <span className="diagram-state">03</span>
          </div>
          <p className="diagram-footnote">
            Illustrative workflow · no live run or target data is shown here.
          </p>
        </div>
      </section>
      <section id="how-it-works" className="marketing-section">
        <div className="marketing-section-title">
          <p className="app-eyebrow">A REVIEWABLE TEST LOOP</p>
          <h2>From test intent to observed evidence.</h2>
        </div>
        <div className="use-case-grid">
          {useCases.map((item, index) => (
            <article className="use-case-card" key={item.title}>
              <span>0{index + 1}</span>
              <h3>{item.title}</h3>
              <p>{item.detail}</p>
            </article>
          ))}
        </div>
      </section>
      <section className="marketing-cta">
        <div>
          <p className="app-eyebrow">START WITH AUTHORIZATION</p>
          <h2>Bring a staging target you control.</h2>
          <p>
            Create a private workspace, verify an exact origin, and review your
            first plan.
          </p>
        </div>
        <Link className="button primary" href="/register">
          Get started <ArrowRight size={16} aria-hidden="true" />
        </Link>
      </section>
      <section className="public-demo-note">
        <span className="demo-label">DEMO / ILLUSTRATIVE</span>
        <p>
          The public page contains no fabricated run results, performance
          charts, or demo account. Connect your own authorized staging fixture
          to explore the workflow.
        </p>
      </section>
    </>
  );
}
