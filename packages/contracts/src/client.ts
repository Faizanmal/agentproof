import createClient from "openapi-fetch";
import type { paths } from "./schema";

/** Relative URLs preserve same-origin cookies. No browser-held API credentials. */
export function createApiClient() {
  return createClient<paths>({ baseUrl: "", credentials: "same-origin" });
}
