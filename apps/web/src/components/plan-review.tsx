"use client";
import { useEffect, useState } from "react";
import { createApiClient } from "@agentproof/contracts";
import type { components } from "@agentproof/contracts/schema";
type Plan = components["schemas"]["PlanView"];
type Secret = components["schemas"]["SecretView"];
type QAPlan = components["schemas"]["QAPlanV1"];

export function PlanReview({
  projectId,
  csrf,
}: {
  projectId: string;
  csrf: string;
}) {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let active = true;
    createApiClient()
      .GET("/api/v1/projects/{project_id}/plans", {
        params: { path: { project_id: projectId } },
      })
      .then(({ data }) => {
        if (active) setPlans(data?.data ?? []);
      })
      .catch(() => {
        if (active) setMessage("Plans could not load.");
      });
    createApiClient()
      .GET("/api/v1/projects/{project_id}/secrets", {
        params: { path: { project_id: projectId } },
      })
      .then(({ data }) => {
        if (active) setSecrets(data?.data ?? []);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [projectId]);
  async function generate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setMessage("Requesting a plan from Vultr…");
    try {
      const result = await createApiClient(csrf).POST(
        "/api/v1/projects/{project_id}/plans",
        {
          params: { path: { project_id: projectId } },
          body: {
            goal: String(data.get("goal")),
            untrusted_page_text: "",
            scope: {
              start_path: String(data.get("start_path")),
              allowed_paths: [String(data.get("allowed_path"))],
              credential_ref_id:
                String(data.get("credential_ref_id") ?? "") || null,
            },
          },
        },
      );
      if (result.data) {
        setPlans((p) => [result.data!, ...p]);
        setMessage("Draft generated. Review every action before approval.");
      } else
        setMessage(
          "Plan rejected or unavailable (HTTP " +
            result.response.status +
            "). " +
            (result.error?.code ?? ""),
        );
    } catch {
      setMessage("Plan service is unavailable.");
    } finally {
      setBusy(false);
    }
  }
  async function approve(event: React.FormEvent<HTMLFormElement>, plan: Plan) {
    event.preventDefault();
    if (!plan.authorization_digest) return;
    const data = new FormData(event.currentTarget);
    setBusy(true);
    try {
      const result = await createApiClient(csrf).POST(
        "/api/v1/plans/{plan_id}/approve",
        {
          params: { path: { plan_id: plan.id } },
          body: {
            version: plan.version,
            authorization_digest: plan.authorization_digest,
            approved_step_ids: data.getAll("approved_step").map(String),
            approve_high_risk: data.get("high_risk") === "on",
          },
        },
      );
      if (result.data) {
        setPlans((p) => p.map((v) => (v.id === plan.id ? result.data! : v)));
        setMessage(
          "Reviewed plan approved. Browser execution remains disabled.",
        );
      } else
        setMessage(
          "Approval rejected (HTTP " +
            result.response.status +
            "). " +
            (result.error?.code ?? ""),
        );
    } catch {
      setMessage("Approval service is unavailable.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="QA plans">
      <h3>Generate a reviewed QA plan</h3>
      <p>
        Verify target ownership first. Do not include passwords, tokens or
        account values in the goal.
      </p>
      <p role="status" aria-live="polite">
        {message}
      </p>
      <form className="connected-form" onSubmit={generate}>
        <label>
          QA goal
          <textarea name="goal" required maxLength={4000} />
        </label>
        <label>
          Starting path
          <input name="start_path" required defaultValue="/" pattern="/.*" />
        </label>
        <label>
          Allowed path or glob
          <input name="allowed_path" required defaultValue="/*" pattern="/.*" />
        </label>
        <label>
          TEST credential scope
          <select name="credential_ref_id">
            <option value="">No credentials</option>
            {secrets.map((secret) => (
              <option key={secret.id} value={secret.id}>
                {secret.label}
              </option>
            ))}
          </select>
        </label>
        <button className="button secondary" disabled={busy}>
          Generate with Vultr
        </button>
      </form>
      {plans.map((plan) => {
        const raw = plan.plan_json;
        const parsed =
          raw && "schema_version" in raw && raw.schema_version === "1.0"
            ? (raw as QAPlan)
            : null;
        return (
          <article key={plan.id}>
            <h4>
              Plan version {plan.version}: {plan.status}
            </h4>
            <p>{plan.goal}</p>
            {plan.rejection_code && <p>Rejected: {plan.rejection_code}</p>}
            {parsed && (
              <>
                <p>
                  Risk: {plan.risk_level}. Scope:{" "}
                  {JSON.stringify(plan.scope_json)}
                </p>
                {parsed.assumptions.length > 0 && (
                  <p>Assumptions: {parsed.assumptions.join("; ")}</p>
                )}
                {parsed.warnings.map((v, i) => (
                  <p key={i}>{v}</p>
                ))}
                <form onSubmit={(e) => approve(e, plan)}>
                  <ol>
                    {parsed.steps.map((step) => (
                      <li key={step.id}>
                        <p>
                          {step.id}: {step.action} {step.url_path ?? ""}{" "}
                          {step.target?.name ?? ""}
                        </p>
                        {step.target && (
                          <p>
                            Target: {step.target.strategy}
                            {step.target.role
                              ? " " + step.target.role
                              : ""} — {step.target.name}.
                            {step.target.nth != null
                              ? " Match index: " + step.target.nth + "."
                              : " Must match one element."}
                          </p>
                        )}
                        {step.action === "wait_for" && (
                          <p>Maximum wait: {step.timeout_ms} ms.</p>
                        )}
                        {step.value_ref && (
                          <p>Uses symbolic reference: {step.value_ref}</p>
                        )}
                        {step.assertion && (
                          <p>
                            Assert {step.assertion.kind}:{" "}
                            {String(step.assertion.expected)}
                          </p>
                        )}
                        {step.requires_approval && (
                          <label>
                            <input
                              type="checkbox"
                              name="approved_step"
                              value={step.id}
                              required
                              disabled={plan.status !== "draft"}
                            />{" "}
                            I explicitly approve this action.
                          </label>
                        )}
                      </li>
                    ))}
                  </ol>
                  <p>Success criteria: {parsed.success_criteria.join("; ")}</p>
                  {plan.risk_level === "high" && (
                    <label>
                      <input
                        name="high_risk"
                        type="checkbox"
                        required
                        disabled={plan.status !== "draft"}
                      />{" "}
                      I reviewed the HIGH risk plan and approve its scoped
                      actions.
                    </label>
                  )}
                  {plan.status === "draft" && (
                    <button className="button primary" disabled={busy}>
                      Approve this version and scope
                    </button>
                  )}
                </form>
              </>
            )}
            {!parsed && plan.status !== "rejected" && (
              <p>
                Legacy or incomplete draft. Generate a new version before
                approval.
              </p>
            )}
          </article>
        );
      })}
    </section>
  );
}
