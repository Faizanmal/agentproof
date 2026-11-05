"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { createApiClient } from "@agentproof/contracts";
export function ApiStatus() {
  const [status, setStatus] = useState("Not checked");
  const [checking, setChecking] = useState(false);
  async function check() {
    setChecking(true);
    setStatus("Checking dependencies…");
    try {
      const { data, response } = await createApiClient().GET(
        "/api/v1/health/ready",
        { signal: AbortSignal.timeout(8000) },
      );
      setStatus(
        data?.status === "ready"
          ? "API ready · PostgreSQL and Redis responded"
          : response.status === 503
            ? "API not ready · A required dependency is unavailable"
            : "API unavailable · Readiness could not be verified",
      );
    } catch {
      setStatus("API unavailable · Readiness could not be verified");
    } finally {
      setChecking(false);
    }
  }
  return (
    <div className="api-status">
      <div>
        <span className="eyebrow">LOCAL API READINESS</span>
        <p role="status" aria-live="polite">
          {status}
        </p>
      </div>
      <Button
        className="button secondary"
        type="button"
        disabled={checking}
        onClick={check}
      >
        {checking ? "Checking…" : "Check connection"}
      </Button>
    </div>
  );
}
