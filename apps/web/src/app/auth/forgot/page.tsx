"use client";

import Link from "next/link";
import { useState } from "react";
import { createApiClient } from "@agentproof/contracts";

export default function ForgotPasswordPage() {
  const [message, setMessage] = useState(
    "Enter the email for your AgentProof account. If it exists, we will send a one-use reset link.",
  );
  const [busy, setBusy] = useState(false);
  const [resetUrl, setResetUrl] = useState<string | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const email = String(new FormData(event.currentTarget).get("email") ?? "");
    setBusy(true);
    setResetUrl(null);
    setMessage("Sending reset instructions…");
    try {
      const result = await createApiClient().POST("/api/v1/auth/forgot-password", {
        body: { email },
      });
      if (result.data) {
        setMessage(result.data.message);
        if (result.data.development_reset_url) {
          setResetUrl(result.data.development_reset_url);
        }
      } else {
        const status = result.response.status;
        setMessage(
          status === 429
            ? "Too many attempts. Wait a little and try again."
            : status === 503
              ? "Password reset is unavailable because email delivery is not configured."
              : `Could not request a reset (HTTP ${status}).`,
        );
      }
    } catch {
      setMessage("The API is unavailable. Your form was not submitted; retry when connection is restored.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="auth-page">
      <div className="auth-card">
        <div className="auth-card-heading">
          <span className="auth-card-icon" aria-hidden="true">
            AP
          </span>
          <p className="eyebrow">ACCOUNT RECOVERY</p>
          <h1>Reset your password</h1>
          <p>We email a one-hour, one-use link. The token stays in the page fragment until you submit it.</p>
        </div>
        <p role="status" aria-live="polite" className="auth-status">
          {message}
        </p>
        <form className="connected-form" onSubmit={submit}>
          <label htmlFor="reset-email">Email address</label>
          <input
            id="reset-email"
            name="email"
            type="email"
            autoComplete="username"
            required
            maxLength={254}
          />
          <button className="button primary auth-submit" disabled={busy}>
            {busy ? "Please wait…" : "Send reset link"}
          </button>
        </form>
        {resetUrl && (
          <a className="button secondary" href={resetUrl}>
            Open development reset email
          </a>
        )}
        <p className="auth-switch">
          Remembered it? <Link href="/login">Sign in</Link>
        </p>
      </div>
    </section>
  );
}
