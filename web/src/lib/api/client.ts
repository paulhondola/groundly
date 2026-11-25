import createClient from "openapi-fetch";
import type { components, paths } from "./schema.d.ts";

export type SourceItem = components["schemas"]["SourceItem"];
export type TopicProgress = components["schemas"]["TopicProgress"];
export type TopicNode = components["schemas"]["TopicNode"];
export type TreeResponse = components["schemas"]["TreeResponse"];
export type DrawnQuestion = components["schemas"]["DrawnQuestion"];
export type DrawTestResponse = components["schemas"]["DrawTestResponse"];
export type SubmittedAnswer =
  | components["schemas"]["SubmittedChoiceAnswer"]
  | components["schemas"]["SubmittedOpenAnswer"];
export type AnswerState = components["schemas"]["AttemptAnswer"]["state"];
export type AttemptResult = components["schemas"]["AttemptResultResponse"];
export type RunPhase = RunSnapshot["phase"];
export type RunKind = RunSnapshot["kind"];
export type ThoughtChannel = RunSnapshot["thought_channel"];
export type RunSnapshot = components["schemas"]["RunSnapshot"];
export type RunsInfo = components["schemas"]["RunsInfoResponse"];
export type AuthStatusResponse = components["schemas"]["AuthStatusResponse"];
export type RemoteInfoResponse = components["schemas"]["RemoteInfoResponse"];
export type PairedDevice = components["schemas"]["PairedDevice"];
export type StartRunBody = { kind: "write"; topic_id: number } | { kind: "grade" };

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

const SafeRequest: typeof Request = class extends Request {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    if (typeof input === "string" && input.startsWith("/") && typeof location !== "undefined") {
      super(`${location.origin}${input}`, init);
    } else {
      super(input, init);
    }
  }
};

export const apiClient = createClient<paths>({
  baseUrl: "",
  credentials: "same-origin",
  Request: SafeRequest,
  fetch: (request) => globalThis.fetch(request),
});

let onUnpairedHandler: (() => void) | null = null;

export function setOnUnpaired(handler: (() => void) | null): void {
  onUnpairedHandler = handler;
}

apiClient.use({
  onResponse({ request, response }) {
    if (response.status === 401 && onUnpairedHandler) {
      const requestPath =
        typeof location !== "undefined"
          ? new URL(request.url, location.origin).pathname
          : request.url;
      if (!requestPath.endsWith("/api/pair")) {
        onUnpairedHandler();
      }
    }
    return undefined;
  },
});

export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok || result.data === undefined) {
    const errObj =
      result.error && typeof result.error === "object" && !Array.isArray(result.error)
        ? (result.error as Record<string, unknown>)
        : null;
    const code = errObj && typeof errObj.error === "string" ? errObj.error : null;
    const detail = errObj && typeof errObj.detail === "string" ? errObj.detail : null;
    const message = detail || code || `request failed (${result.response.status})`;
    throw new ApiError(message, result.response.status, code, detail);
  }
  return result.data;
}

export function pageURL(subject: string, rest = ""): string {
  return "/s/" + encodeURIComponent(subject) + rest;
}

export function materialURL(
  subject: string,
  materialId: number,
  page: number | null = null,
): string {
  return pageURL(
    subject,
    "/materials/" + Number(materialId) + (page != null ? "#page=" + Number(page) : ""),
  );
}

export function testQuery(topicId: number | null, n: number): string {
  return "/test?" + (topicId != null ? "topic=" + topicId + "&" : "") + "n=" + n;
}
