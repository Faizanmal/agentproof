"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { createApiClient, getIdentity } from "@agentproof/contracts";
export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const [message, setMessage] = useState("Checking session…");
  const [busy, setBusy] = useState(false);
  const [verificationUrl, setVerificationUrl] = useState<string | null>(null);
  const register = mode === "register";

  useEffect(() => {
    let active = true;
    getIdentity().then((identity) => {
      if (!active) return;
      if (identity) {
        setMessage("You are already signed in.");
        router.replace("/app");
      } else setMessage(register ? "Create an account to start your workspace." : "Sign in to continue.");
    }).catch(() => {
      if (active) setMessage("The API is unavailable. Check your connection and try again.");
    });
    return () => { active = false; };
  }, [register, router]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setVerificationUrl(null);
    setMessage(register ? "Creating your account…" : "Signing in…");
    try {
      const api = createApiClient();
      const body = {
        email: String(values.get("email") ?? ""),
        password: String(values.get("password") ?? ""),
      };
      const result = register
        ? await api.POST("/api/v1/auth/register", { body })
        : await api.POST("/api/v1/auth/login", { body });
      if (result.data) {
        form.reset();
        setMessage(register
          ? "Account created. Verify your email before approving a plan or starting a run."
          : "Signed in. Opening your workspace…");
        if (register) setVerificationUrl("pending");
        else {
          const next = new URLSearchParams(window.location.search).get("next");
          router.replace(next?.startsWith("/app") ? next : "/app");
        }
      } else {
        const status = result.response.status;
        setMessage(status === 409 ? "An account with this email already exists. Try signing in." : status === 429 ? "Too many attempts. Wait a little and try again." : status === 503 && register ? "Registration is unavailable because email delivery or session signing is not configured." : status === 401 ? "Email or password was not accepted." : `Could not ${register ? "create account" : "sign in"} (HTTP ${status}).`);
      }
    } catch {
      setMessage("The API is unavailable. Your form was not submitted; retry when connection is restored.");
    } finally { setBusy(false); }
  }

  async function openVerification() {
    const result = await createApiClient().GET("/api/v1/dev/mail").catch(() => undefined);
    const url = result?.data?.verification_url ?? result?.data?.url ?? null;
    if (url) setVerificationUrl(url);
    else setMessage("The development mailbox is unavailable. Check the configured email delivery service.");
  }

  return (
    <div className="auth-card">
      <div className="auth-card-heading">
        <span className="auth-card-icon" aria-hidden="true">AP</span>
        <p className="eyebrow">{register ? "CREATE YOUR ACCOUNT" : "WELCOME BACK"}</p>
        <h1>{register ? "Start with a verified target" : "Sign in to AgentProof"}</h1>
        <p>{register ? "Your workspace stays private. Testing remains gated by target ownership proof and plan approval." : "Continue to your authorized projects, plans and run evidence."}</p>
      </div>
      <p role="status" aria-live="polite" className="auth-status">{message}</p>
      <form className="connected-form" onSubmit={submit}>
        <label htmlFor="account-email">Email address</label>
        <input id="account-email" name="email" type="email" autoComplete="email" required maxLength={254} />
        <label htmlFor="account-password">Password</label>
        <input id="account-password" name="password" type="password" autoComplete={register ? "new-password" : "current-password"} required minLength={register ? 12 : 1} maxLength={256} />
        {register && <p className="form-hint">Use at least 12 characters. Never reuse a test-site password here.</p>}
        <button className="button primary auth-submit" disabled={busy}>{busy ? "Please wait…" : register ? "Create account" : "Sign in"}</button>
      </form>
      {verificationUrl === "pending" && <button className="button secondary" onClick={openVerification}>Open development verification email</button>}
      {verificationUrl && verificationUrl !== "pending" && <a className="button secondary" href={verificationUrl}>Verify email</a>}
      {!register && (
        <p className="auth-switch">
          <Link href="/auth/forgot">Forgot password?</Link>
        </p>
      )}
      <p className="auth-switch">{register ? <>Already registered? <Link href="/login">Sign in</Link></> : <>New to AgentProof? <Link href="/register">Create an account</Link></>}</p>
      <p className="auth-privacy">Secure, HTTP-only cookies. Session tokens are never stored in browser storage.</p>
    </div>
  );
}
