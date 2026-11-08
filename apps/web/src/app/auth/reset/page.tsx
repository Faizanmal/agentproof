"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { createApiClient } from "@agentproof/contracts";

export default function ResetPasswordPage() {
  const router = useRouter();
  const token = useRef("");
  const [message, setMessage] = useState(
    "Choose a new password of at least 12 characters.",
  );
  const [busy, setBusy] = useState(false);
  const [complete, setComplete] = useState(false);

  useEffect(() => {
    const value = new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "";
    window.history.replaceState(null, "", window.location.pathname);
    if (value) token.current = value;
    else setMessage("This page requires a valid password reset link.");
  }, []);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token.current) {
      setMessage("This page requires a valid password reset link.");
      return;
    }
    const password = String(new FormData(event.currentTarget).get("password") ?? "");
    setBusy(true);
    setMessage("Updating your password…");
    try {
      const result = await createApiClient().POST("/api/v1/auth/reset-password", {
        body: { token: token.current, password },
      });
      if (result.response.ok) {
        token.current = "";
        setComplete(true);
        setMessage("Password updated. Sign in with your new password.");
        router.prefetch("/login");
      } else {
        const status = result.response.status;
        setMessage(
          status === 400
            ? "The link is invalid, expired or already used."
            : status === 429
              ? "Too many attempts. Wait a little and try again."
              : `Could not update the password (HTTP ${status}).`,
        );
      }
    } catch {
      setMessage("The API is unavailable; try again.");
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
          <h1>Choose a new password</h1>
          <p>Active sessions are revoked after a successful reset.</p>
        </div>
        <p role="status" aria-live="polite" className="auth-status">
          {message}
        </p>
        {!complete && (
          <form className="connected-form" onSubmit={submit}>
            <label htmlFor="new-password">New password</label>
            <input
              id="new-password"
              name="password"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={256}
            />
            <p className="form-hint">Use at least 12 characters. Never reuse a test-site password here.</p>
            <button className="button primary auth-submit" disabled={busy || !token.current}>
              {busy ? "Please wait…" : "Update password"}
            </button>
          </form>
        )}
        <p className="auth-switch">
          <Link href="/login">Return to sign in</Link>
        </p>
      </div>
    </section>
  );
}
