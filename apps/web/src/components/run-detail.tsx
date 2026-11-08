"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  CircleCheck,
  CircleDot,
  ImageOff,
  Radio,
  RefreshCw,
} from "lucide-react";
import { createApiClient, getIdentity } from "@agentproof/contracts";
import type { components } from "@agentproof/contracts/schema";
import { PageHeader, Panel, StatusPill } from "@/components/app-ui";

type Run = components["schemas"]["RunView"];
type Step = components["schemas"]["StepView"];
type Artifact = components["schemas"]["ArtifactView"];
type Finding = components["schemas"]["FindingView"];
type EventRecord = {
  id: number;
  event_type: string;
  payload_json: Record<string, unknown>;
};
const terminal = new Set(["completed", "failed", "cancelled", "timed_out"]);
const lifecycle = [
  "queued",
  "planning",
  "awaiting_approval",
  "running",
  "investigating",
  "completed",
];
const eventNames = [
  "run.queued",
  "run.planning",
  "plan.generated",
  "plan.approval_required",
  "run.started",
  "step.started",
  "step.completed",
  "step.failed",
  "artifact.created",
  "finding.created",
  "sandbox.policy_denied",
  "run.investigating",
  "run.completed",
  "run.failed",
  "run.cancel.requested",
  "run.cancelled",
  "run.timed_out",
];

export function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const lastEventId = useRef(0);
  const [run, setRun] = useState<Run | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [feed, setFeed] = useState<"connecting" | "live" | "disconnected">(
    "connecting",
  );
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const loading = loadedFor !== id;
  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const api = createApiClient();
      const [runResult, stepResult, artifactResult, findingResult] =
        await Promise.all([
          api.GET("/api/v1/runs/{run_id}", {
            params: { path: { run_id: id } },
            signal,
          }),
          api.GET("/api/v1/runs/{run_id}/steps", {
            params: { path: { run_id: id }, query: { limit: 100 } },
            signal,
          }),
          api.GET("/api/v1/runs/{run_id}/artifacts", {
            params: { path: { run_id: id }, query: { limit: 100 } },
            signal,
          }),
          api.GET("/api/v1/runs/{run_id}/findings", {
            params: { path: { run_id: id }, query: { limit: 100 } },
            signal,
          }),
        ]);
      if (!runResult.data)
        throw new Error(
          runResult.response.status === 401
            ? "Your session expired. Sign in again."
            : runResult.response.status === 404
              ? "Run not found or you do not have access to it."
              : `Run state could not be loaded (HTTP ${runResult.response.status}).`,
        );
      setRun(runResult.data);
      setSteps(stepResult.data?.data ?? []);
      setArtifacts(artifactResult.data?.data ?? []);
      setFindings(findingResult.data?.data ?? []);
    },
    [id],
  );
  useEffect(() => {
    const controller = new AbortController();
    const runId = id;
    const api = createApiClient();
    void Promise.all([
      api.GET("/api/v1/runs/{run_id}", {
        params: { path: { run_id: runId } },
        signal: controller.signal,
      }),
      api.GET("/api/v1/runs/{run_id}/steps", {
        params: { path: { run_id: runId }, query: { limit: 100 } },
        signal: controller.signal,
      }),
      api.GET("/api/v1/runs/{run_id}/artifacts", {
        params: { path: { run_id: runId }, query: { limit: 100 } },
        signal: controller.signal,
      }),
      api.GET("/api/v1/runs/{run_id}/findings", {
        params: { path: { run_id: runId }, query: { limit: 100 } },
        signal: controller.signal,
      }),
    ])
      .then(([runResult, stepResult, artifactResult, findingResult]) => {
        if (controller.signal.aborted) return;
        if (!runResult.data) {
          setError(
            runResult.response.status === 401
              ? "Your session expired. Sign in again."
              : runResult.response.status === 404
                ? "Run not found or you do not have access to it."
                : `Run state could not be loaded (HTTP ${runResult.response.status}).`,
          );
          setLoadedFor(runId);
          return;
        }
        setRun(runResult.data);
        setSteps(stepResult.data?.data ?? []);
        setArtifacts(artifactResult.data?.data ?? []);
        setFindings(findingResult.data?.data ?? []);
        setError("");
        setLoadedFor(runId);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(
            reason instanceof Error ? reason.message : "API unavailable.",
          );
          setLoadedFor(runId);
        }
      });
    const source = new EventSource(
      `/api/v1/runs/${encodeURIComponent(runId)}/events?after_id=${lastEventId.current}`,
      { withCredentials: true },
    );
    source.onopen = () => setFeed("live");
    source.onerror = () => setFeed("disconnected");
    const onEvent = (event: MessageEvent<string>) => {
      const eventId = Number(event.lastEventId);
      if (!Number.isSafeInteger(eventId) || eventId <= lastEventId.current)
        return;
      lastEventId.current = eventId;
      try {
        const record = JSON.parse(event.data) as EventRecord;
        setEvents((previous) =>
          previous.some((item) => item.id === eventId)
            ? previous
            : [...previous, record].slice(-40),
        );
      } catch {
        /* malformed event is ignored; polling remains authoritative */
      }
      void refresh(controller.signal).catch(() => undefined);
    };
    for (const name of eventNames)
      source.addEventListener(name, onEvent as EventListener);
    const interval = setInterval(() => {
      void refresh(controller.signal).catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error ? reason.message : "Run refresh failed.",
          );
      });
    }, 5000);
    return () => {
      controller.abort();
      source.close();
      clearInterval(interval);
    };
  }, [id, refresh]);

  async function cancelRun() {
    const identity = await getIdentity();
    if (!identity) {
      setError("Your session expired. Sign in again before cancelling.");
      return;
    }
    setCancelBusy(true);
    setError("");
    setMessage("Sending cancellation request…");
    try {
      const result = await createApiClient(identity.csrf_token).POST(
        "/api/v1/runs/{run_id}/cancel",
        { params: { path: { run_id: id } } },
      );
      if (result.data) {
        setRun(result.data);
        setMessage(
          result.data.status === "cancelled"
            ? "Run cancelled."
            : "Cancellation requested. Waiting for the sandbox to stop safely.",
        );
      } else
        setError(
          `Cancellation could not be requested (HTTP ${result.response.status}).`,
        );
    } catch {
      setError(
        "The API could not receive the cancellation request. Run state is being refreshed.",
      );
    } finally {
      setCancelBusy(false);
    }
  }

  if (loading)
    return (
      <div className="app-page">
        <p role="status">Loading run state and evidence…</p>
      </div>
    );
  if (!run)
    return (
      <div className="app-page">
        <div className="app-alert error" role="alert">
          {error || "Run unavailable."}
        </div>
        <Link href="/login">Sign in</Link>
      </div>
    );
  const activeIndex = lifecycle.indexOf(run.status);
  const isTerminal = terminal.has(run.status);
  return (
    <div className="app-page">
      <PageHeader
        eyebrow="RUN / EXECUTION"
        title={String(run.summary_json.goal ?? "QA run")}
        description={`Run ${run.id} · Created ${new Date(run.created_at).toLocaleString()}`}
        action={{ href: `/app/runs/${id}/report`, label: "Open report" }}
      />
      {error && (
        <div className="app-alert error" role="alert">
          {error} <Link href="/login">Sign in again</Link>
        </div>
      )}
      {message && (
        <div className="app-alert info" role="status">
          {message}
        </div>
      )}
      <div className="run-status-banner">
        <div>
          <span className="app-eyebrow">CURRENT STATE</span>
          <div className="run-current-status">
            <StatusPill value={run.verdict ?? run.status} />
            <span className="run-feed-status">
              <Radio size={15} aria-hidden="true" />
              {feed === "live"
                ? "Live feed connected"
                : feed === "connecting"
                  ? "Connecting to live feed…"
                  : "LIVE FEED DISCONNECTED · polling run state"}
            </span>
          </div>
        </div>
        <div className="run-controls">
          {!isTerminal && (
            <button
              className="button danger-button"
              disabled={cancelBusy}
              onClick={cancelRun}
            >
              <Ban size={16} aria-hidden="true" />
              {cancelBusy ? "Cancelling…" : "Cancel run"}
            </button>
          )}
          <button
            className="icon-button"
            aria-label="Refresh run state"
            onClick={() => void refresh()}
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </div>
      <Panel className="timeline-panel">
        <h2>Run lifecycle</h2>
        <ol className="run-timeline">
          {lifecycle.map((state, index) => {
            const done = activeIndex >= 0 && index < activeIndex;
            const current = index === activeIndex;
            const skipped =
              run.status === "failed" ||
              run.status === "cancelled" ||
              run.status === "timed_out";
            return (
              <li
                key={state}
                className={`${done ? "done" : ""} ${current ? "current" : ""}`}
              >
                <span className="timeline-dot">
                  {done ? (
                    <CircleCheck size={15} aria-hidden="true" />
                  ) : (
                    <CircleDot size={15} aria-hidden="true" />
                  )}
                </span>
                <span>{state.replaceAll("_", " ")}</span>
                {current && <small>Current</small>}
                {skipped && index >= Math.max(activeIndex, 0) && (
                  <small>{run.status.replaceAll("_", " ")}</small>
                )}
              </li>
            );
          })}
        </ol>
        {run.status === "awaiting_approval" && (
          <p className="app-alert warning">
            Approval is blocking execution. Review the plan and approve it in
            the project before a browser starts.
          </p>
        )}
      </Panel>
      <div className="run-detail-grid">
        <Panel>
          <div className="panel-heading">
            <div>
              <h2>Steps</h2>
              <p>Only observed runner events appear here.</p>
            </div>
            <span className="text-muted">{steps.length} recorded</span>
          </div>
          {steps.length ? (
            <ol className="step-list">
              {steps.map((step) => (
                <li key={step.id}>
                  <span className="step-sequence">{step.ordinal + 1}</span>
                  <div className="step-body">
                    <strong>
                      {String(
                        step.typed_action_json.action ?? "Action",
                      ).replaceAll("_", " ")}
                    </strong>
                    <small>
                      {String(
                        step.typed_action_json.target &&
                          typeof step.typed_action_json.target === "object"
                          ? ((
                              step.typed_action_json.target as Record<
                                string,
                                unknown
                              >
                            ).name ?? "Target")
                          : (step.typed_action_json.url_path ??
                              "Scoped browser action"),
                      )}
                    </small>
                    {Boolean(step.observed_json.error_code) && (
                      <small className="error-text">
                        {String(step.observed_json.error_code)}
                      </small>
                    )}
                  </div>
                  <StatusPill
                    value={String(
                      step.observed_json.assertion &&
                        typeof step.observed_json.assertion === "object"
                        ? ((
                            step.observed_json.assertion as Record<
                              string,
                              unknown
                            >
                          ).status ?? step.status)
                        : step.status,
                    )}
                  />
                </li>
              ))}
            </ol>
          ) : (
            <div className="app-empty compact">
              <p>Steps appear after planning and execution events arrive.</p>
            </div>
          )}
        </Panel>
        <Panel>
          <div className="panel-heading">
            <div>
              <h2>Evidence</h2>
              <p>
                Private artifacts expire after the configured retention window.
              </p>
            </div>
          </div>
          {artifacts.length ? (
            <div className="screenshot-grid">
              {artifacts
                .filter((item) => item.kind === "screenshot")
                .map((artifact) => (
                  <ArtifactPreview key={artifact.id} artifact={artifact} />
                ))}
            </div>
          ) : (
            <div className="app-empty compact">
              <ImageOff size={20} aria-hidden="true" />
              <p>No screenshots or trace artifacts have been recorded yet.</p>
            </div>
          )}
          {artifacts
            .filter((item) => item.kind !== "screenshot")
            .map((artifact) => (
              <a
                className="artifact-download-row"
                href={`/api/v1/artifacts/${artifact.id}/download`}
                key={artifact.id}
                download
              >
                <span>
                  {artifact.kind} · {artifact.mime_type}
                </span>
                <small>
                  {artifact.bytes.toLocaleString()} bytes · expires{" "}
                  {new Date(artifact.expires_at).toLocaleDateString()}
                </small>
              </a>
            ))}
        </Panel>
      </div>
      <div className="run-detail-grid">
        <Panel>
          <div className="panel-heading">
            <div>
              <h2>Findings</h2>
              <p>Derived from actual assertion evidence.</p>
            </div>
          </div>
          {findings.length ? (
            findings.map((finding) => (
              <article className="finding-row" key={finding.id}>
                <div>
                  <strong>{finding.title}</strong>
                  <p>
                    {finding.classification} · {finding.severity} ·{" "}
                    {finding.occurrence_count} observed
                  </p>
                </div>
                <Link
                  href={`/app/runs/${id}/report`}
                  aria-label={`View report for ${finding.title}`}
                >
                  <ArrowRight size={16} />
                </Link>
              </article>
            ))
          ) : (
            <p className="text-muted">
              No findings have been recorded for this run.
            </p>
          )}
        </Panel>
        <Panel>
          <div className="panel-heading">
            <div>
              <h2>Policy and network signals</h2>
              <p>Redacted data from the broker and browser runner.</p>
            </div>
            <AlertTriangle size={17} aria-hidden="true" />
          </div>
          <div className="event-list">
            {events
              .filter(
                (event) =>
                  event.event_type.includes("policy") ||
                  event.event_type.startsWith("sandbox.") ||
                  event.event_type === "run.failed",
              )
              .slice(-8)
              .map((event) => (
                <div key={event.id}>
                  <StatusPill value={event.event_type} />
                  <small>
                    Event {event.id} ·{" "}
                    {String(
                      event.payload_json.reason ??
                        event.payload_json.error_code ??
                        "Policy event",
                    )}
                  </small>
                </div>
              ))}
          </div>
          {events.length === 0 && (
            <p className="text-muted">
              No recent policy denial or runner error events.
            </p>
          )}
        </Panel>
      </div>
      <div className="run-feed-footer">
        <span className="run-feed-status">
          <Radio size={14} aria-hidden="true" />
          {feed === "live"
            ? "Live event stream"
            : feed === "disconnected"
              ? "LIVE FEED DISCONNECTED"
              : "Connecting"}
        </span>
        <span>
          {events.length} unique events received · polling fallback every 5
          seconds
        </span>
      </div>
    </div>
  );
}

function ArtifactPreview({ artifact }: { artifact: Artifact }) {
  const [source, setSource] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    void fetch(`/api/v1/artifacts/${artifact.id}/download`, {
      credentials: "same-origin",
      signal: controller.signal,
      cache: "no-store",
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Artifact unavailable");
        const data = await response.blob();
        objectUrl = URL.createObjectURL(
          new Blob([data], { type: artifact.mime_type }),
        );
        setSource(objectUrl);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifact.id, artifact.mime_type]);
  return (
    <figure className="screenshot-card">
      {source ? (
        <a href={source} target="_blank" rel="noreferrer">
          <img
            src={source}
            loading="lazy"
            alt={`Sanitized browser screenshot for run step ${artifact.step_id ?? "final state"}`}
          />
        </a>
      ) : (
        <div className="screenshot-placeholder" role="status">
          {error
            ? "Screenshot unavailable or expired"
            : "Loading private screenshot…"}
        </div>
      )}
      <figcaption>
        PNG/WebP · {artifact.bytes.toLocaleString()} bytes ·{" "}
        <a href={`/api/v1/artifacts/${artifact.id}/download`} download>
          Download
        </a>
      </figcaption>
    </figure>
  );
}
