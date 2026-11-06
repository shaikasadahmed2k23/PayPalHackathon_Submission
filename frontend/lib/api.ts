import { getOwnerToken } from "./store";

/** Build-time constant: baked into the JS bundle by `next build`. Changing it means rebuilding the web service. */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

// ---- "waking up" state (Render free tier sleeps after 15 min idle; first request takes ~30-50 s) ----
let waking = false;
const listeners = new Set<() => void>();
function setWaking(v: boolean): void {
  if (waking === v) return;
  waking = v;
  listeners.forEach((l) => l());
}
export function subscribeWaking(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}
export const getWaking = (): boolean => waking;

const SLOW_MS = 4000; // a request still pending after this probably hit a sleeping server
const RETRY_EVERY_MS = 3000;
const GIVE_UP_AFTER_MS = 90_000;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

/** FastAPI errors: detail is a string, an object with .message (close), or a list (422). */
function describe(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? JSON.stringify(d)).join("; ");
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  return fallback;
}

/** A gateway answer from a spinning-up server (not one of our own JSON errors): safe to retry. */
function isColdStart(status: number, data: unknown): boolean {
  if (status === 503 || status === 504) return true;
  return status === 502 && !(data && typeof data === "object" && "detail" in data);
}

/** owner: allowance id whose owner token (saved on this device) is sent as X-Owner-Token. */
export async function api<T>(path: string, init?: { method?: string; body?: unknown; owner?: number }): Promise<T> {
  const started = Date.now();
  const ownerToken = init?.owner !== undefined ? await getOwnerToken(init.owner).catch(() => undefined) : undefined;
  const headers: Record<string, string> = {};
  if (init?.body !== undefined) headers["Content-Type"] = "application/json";
  if (ownerToken) headers["X-Owner-Token"] = ownerToken;
  for (;;) {
    const slow = setTimeout(() => setWaking(true), SLOW_MS);
    let res: Response | null = null;
    let text = "";
    try {
      res = await fetch(API_URL + path, {
        method: init?.method ?? (init?.body !== undefined ? "POST" : "GET"),
        headers,
        body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
      });
      text = await res.text();
    } catch {
      res = null; // network failure: offline, or the server is still starting
    } finally {
      clearTimeout(slow);
    }

    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      /* non-JSON error body */
    }

    const retryable = res === null || isColdStart(res.status, data);
    if (retryable) {
      // Truly offline devices fail fast (offline mode must stay snappy); otherwise wait for the server.
      if (res === null && typeof navigator !== "undefined" && !navigator.onLine) {
        setWaking(false);
        throw new ApiError("Can't reach the LeashPay server (are you offline?)", 0);
      }
      if (Date.now() - started < GIVE_UP_AFTER_MS) {
        setWaking(true);
        await sleep(RETRY_EVERY_MS);
        continue;
      }
      setWaking(false);
      throw new ApiError("The LeashPay server isn't responding. Try again in a minute.", res?.status ?? 0);
    }

    setWaking(false);
    if (!res!.ok) {
      const detail = (data as { detail?: unknown } | null)?.detail;
      throw new ApiError(describe(detail, text || res!.statusText), res!.status);
    }
    return data as T;
  }
}

export const money = (n: number): string => `$${n.toFixed(2)}`;
