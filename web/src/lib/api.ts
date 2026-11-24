export const RUNS_API = "/api/runs";
export const RUNS_STREAM_API = "/api/runs/stream";
export const RUNS_STOP_API = "/api/runs/stop";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly detail: string | null;

  constructor(
    message: string,
    status: number,
    code: string | null = null,
    detail: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

export async function getJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  let body: Record<string, unknown> | null = null;
  try {
    const parsed: unknown = await res.json();
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      body = parsed as Record<string, unknown>;
    } else if (res.ok) {
      return parsed as T;
    }
  } catch {
    /* non-JSON failure body */
  }
  const errorCode = body && typeof body.error === "string" ? body.error : null;
  const detail = body && typeof body.detail === "string" ? body.detail : null;
  if (!res.ok || errorCode) {
    const message = detail || errorCode || `request failed (${res.status})`;
    throw new ApiError(message, res.status, errorCode, detail);
  }
  return body as T;
}

export function postJSON<T>(url: string, body: unknown): Promise<T> {
  return getJSON<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function api(subject: string, rest: string): string {
  return "/api/subjects/" + encodeURIComponent(subject) + rest;
}

export function pageURL(subject: string, rest = ""): string {
  return "/s/" + encodeURIComponent(subject) + rest;
}

export function testQuery(topicId: number | null, n: number): string {
  return "/test?" + (topicId != null ? "topic=" + topicId + "&" : "") + "n=" + n;
}

export function startRunURL(subject: string): string {
  return api(subject, "/runs");
}
