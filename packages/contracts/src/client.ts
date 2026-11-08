import createClient from "openapi-fetch";
import type { paths } from "./schema";

/** Relative URLs preserve same-origin cookies. No browser-held API credentials. */
export function createApiClient(csrfToken?: string) {
  return createClient<paths>({
    baseUrl: "",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
    },
  });
}

/** One bounded refresh attempt; only the non-authenticating CSRF value enters JS. */
let identityRequest:
  | Promise<import("./schema").components["schemas"]["Identity"] | undefined>
  | undefined;

async function loadIdentity() {
  const api = createApiClient();
  const initial = await api.GET("/api/v1/auth/me");
  if (initial.data || initial.response.status !== 401) return initial.data;
  async function recover() {
    const current = await api.GET("/api/v1/auth/me");
    if (current.data || current.response.status !== 401) return current.data;
    const csrf = await api.GET("/api/v1/auth/csrf");
    if (!csrf.data) return undefined;
    const refreshed = await createApiClient(csrf.data.csrf_token).POST(
      "/api/v1/auth/refresh",
    );
    return refreshed.data;
  }
  if (typeof navigator !== "undefined" && "locks" in navigator) {
    return navigator.locks.request("agentproof-session-refresh", recover);
  }
  return recover();
}

export function getIdentity() {
  if (!identityRequest)
    identityRequest = loadIdentity().finally(() => {
      identityRequest = undefined;
    });
  return identityRequest;
}
