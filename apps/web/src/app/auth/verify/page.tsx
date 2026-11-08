"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { createApiClient } from "@agentproof/contracts";
export default function Verify() {
  const token = useRef("");
  const [complete, setComplete] = useState(false);
  const [message, setMessage] = useState(
    "Read your verification link, then confirm below.",
  );
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const value =
      new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "";
    window.history.replaceState(null, "", window.location.pathname);
    if (value) token.current = value;
  }, []);
  async function verify() {
    if (!token.current) {
      setMessage("This page requires a valid verification link.");
      return;
    }
    setBusy(true);
    try {
      const result = await createApiClient().POST("/api/v1/auth/verify-email", {
        body: { token: token.current },
      });
      setMessage(
        result.response.ok
          ? "Email verified. You can return to your account."
          : "The link is invalid, expired or already used.",
      );
      token.current = "";
      setComplete(true);
    } catch {
      setMessage("The API is unavailable; try again.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="center-page">
      <h1>Verify email</h1>
      <p role="status">{message}</p>
      <button
        className="button primary"
        disabled={complete || busy}
        onClick={verify}
      >
        Confirm verification
      </button>
      <p>
        <Link className="text-link" href="/auth">
          Return to account
        </Link>
      </p>
    </section>
  );
}
