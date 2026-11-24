import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema.d.ts";

export type { components, paths };

export type SubjectSummary = components["schemas"]["SubjectSummary"];
export type SourceItem = components["schemas"]["SourceItem"];
export type TopicProgress = components["schemas"]["TopicProgress"];
export type MasteryState = TopicProgress["state"];
export type TopicNode = components["schemas"]["TopicNode"];
export type TreeResponse = components["schemas"]["TreeResponse"];
export type QuestionKind = DrawnQuestion["kind"];
export type QuestionOption = components["schemas"]["QuestionOption"];
export type DrawnQuestion = components["schemas"]["DrawnQuestion"];
export type DrawTestResponse = components["schemas"]["DrawTestResponse"];
export type SubmittedChoiceAnswer = components["schemas"]["SubmittedChoiceAnswer"];
export type SubmittedOpenAnswer = components["schemas"]["SubmittedOpenAnswer"];
export type SubmittedAnswer = SubmittedChoiceAnswer | SubmittedOpenAnswer;
export type SubmitAttemptResponse = components["schemas"]["SubmitAttemptResponse"];
export type AttemptScore = components["schemas"]["AttemptScore"];
export type AttemptSummary = components["schemas"]["AttemptSummary"];
export type AttemptOption = components["schemas"]["AttemptOption"];
export type AnswerState = AttemptAnswer["state"];
export type AttemptAnswer = components["schemas"]["AttemptAnswer"];
export type AttemptTopicSummary = components["schemas"]["AttemptTopicSummary"];
export type AttemptResult = components["schemas"]["AttemptResultResponse"];
export type RunPhase = RunSnapshot["phase"];
export type RunKind = RunSnapshot["kind"];
export type RunStatus = RunSnapshot["status"];
export type ThoughtChannel = RunSnapshot["thought_channel"];
export type RunErrorDetail = components["schemas"]["RunErrorDetail"];
export type RunSnapshot = components["schemas"]["RunSnapshot"];
export type RunsInfo = components["schemas"]["RunsInfoResponse"];
export type StartRunBody = { kind: "write"; topic_id: number } | { kind: "grade" };
export type StopRunResponse = components["schemas"]["StopRunResponse"];
export type ErrorResponse = components["schemas"]["ErrorResponse"];

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

export interface ClientConfig {
  baseUrl?: string;
  getAuthHeaders?: () => Record<string, string> | Promise<Record<string, string>>;
  onUnauthorized?: (response: Response) => void;
}

let clientConfig: ClientConfig = {};

export function configureClient(config: ClientConfig): void {
  clientConfig = { ...clientConfig, ...config };
}

export function resetClientConfig(): void {
  clientConfig = {};
}

function resolveRequestUrl(requestUrl: string, baseUrl: string): string {
  const trimmedBase = baseUrl.replace(/\/+$/, "");
  if (requestUrl.startsWith("/")) {
    return `${trimmedBase}${requestUrl}`;
  }
  const parsed = new URL(requestUrl);
  return `${trimmedBase}${parsed.pathname}${parsed.search}`;
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

const clientMiddleware: Middleware = {
  async onRequest({ request }) {
    let nextRequest = request;

    if (clientConfig.baseUrl) {
      const targetUrl = resolveRequestUrl(request.url, clientConfig.baseUrl);
      if (targetUrl !== request.url) {
        nextRequest = new SafeRequest(targetUrl, request);
      }
    }

    if (clientConfig.getAuthHeaders) {
      const authHeaders = await clientConfig.getAuthHeaders();
      const entries = Object.entries(authHeaders);
      if (entries.length > 0) {
        const headers = new Headers(nextRequest.headers);
        for (const [key, value] of entries) {
          headers.set(key, value);
        }
        nextRequest = new SafeRequest(nextRequest, { headers });
      }
    }

    return nextRequest;
  },
  onResponse({ response }) {
    if (response.status === 401) {
      clientConfig.onUnauthorized?.(response);
    }
    return response;
  },
};

export const apiClient = createClient<paths>({
  baseUrl: "",
  credentials: "same-origin",
  Request: SafeRequest,
  fetch: (request) => globalThis.fetch(request),
});

apiClient.use(clientMiddleware);

export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    const errObj =
      result.error && typeof result.error === "object" && !Array.isArray(result.error)
        ? (result.error as Record<string, unknown>)
        : null;
    const code = errObj && typeof errObj.error === "string" ? errObj.error : null;
    const detail = errObj && typeof errObj.detail === "string" ? errObj.detail : null;
    const message = detail || code || `request failed (${result.response.status})`;
    throw new ApiError(message, result.response.status, code, detail);
  }
  return result.data as T;
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
