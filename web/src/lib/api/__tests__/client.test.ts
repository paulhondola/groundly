import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { ApiError, apiClient, materialURL, pageURL, testQuery, unwrap } from "../client";
import { createAppQueryClient, queryKeys, shouldRetryQuery } from "../queries";
import { createEventStream, setEventStreamFactory, type EventSourceLike } from "../sse";

describe("unwrap", () => {
  it("returns data on 200 OK responses", () => {
    const payload = [{ subject: "PDSS", title: "PDSS", has_tree: true }];
    const response = new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
    expect(unwrap({ data: payload, response })).toEqual(payload);
  });

  it("throws ApiError on empty 2xx responses with undefined data", () => {
    const response = new Response(null, { status: 204 });
    try {
      unwrap({ data: undefined, response });
      expect.unreachable("expected unwrap to throw");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.message).toBe("request failed (204)");
      expect(apiErr.status).toBe(204);
      expect(apiErr.code).toBeNull();
      expect(apiErr.detail).toBeNull();
    }
  });

  it("throws ApiError with detail and code on JSON ErrorResponse (400 and 404)", () => {
    const res400 = new Response(
      JSON.stringify({ error: "unknown_question", detail: "Question 99 was not found." }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
    expect(() =>
      unwrap({
        error: { error: "unknown_question", detail: "Question 99 was not found." },
        response: res400,
      }),
    ).toThrowError(ApiError);

    try {
      unwrap({
        error: { error: "unknown_question", detail: "Question 99 was not found." },
        response: res400,
      });
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.message).toBe("Question 99 was not found.");
      expect(apiErr.status).toBe(400);
      expect(apiErr.code).toBe("unknown_question");
      expect(apiErr.detail).toBe("Question 99 was not found.");
    }

    const res404 = new Response(
      JSON.stringify({ error: "unknown_subject", detail: "Unknown subject: NOPE" }),
      { status: 404, headers: { "Content-Type": "application/json" } },
    );
    try {
      unwrap({
        error: { error: "unknown_subject", detail: "Unknown subject: NOPE" },
        response: res404,
      });
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.message).toBe("Unknown subject: NOPE");
      expect(apiErr.status).toBe(404);
      expect(apiErr.code).toBe("unknown_subject");
      expect(apiErr.detail).toBe("Unknown subject: NOPE");
    }
  });

  it("throws ApiError('request failed (502)', 502, null, null) on non-JSON 502 Bad Gateway body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("<html><body>502 Bad Gateway</body></html>", {
        status: 502,
        headers: { "Content-Type": "text/html" },
      }),
    );
    const originalFetch = globalThis.fetch;
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    try {
      const result = await apiClient.GET("/api/subjects");
      try {
        unwrap(result);
        expect.unreachable("expected unwrap to throw");
      } catch (err) {
        expect(err).toBeInstanceOf(ApiError);
        const apiErr = err as ApiError;
        expect(apiErr.message).toBe("request failed (502)");
        expect(apiErr.status).toBe(502);
        expect(apiErr.code).toBeNull();
        expect(apiErr.detail).toBeNull();
      }
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("URL helpers", () => {
  it("builds pageURL, materialURL, and testQuery", () => {
    expect(pageURL("CS 101")).toBe("/s/CS%20101");
    expect(pageURL("PDSS", "/attempts/4")).toBe("/s/PDSS/attempts/4");
    expect(materialURL("PDSS", 3)).toBe("/s/PDSS/materials/3");
    expect(materialURL("PDSS", 3, 4)).toBe("/s/PDSS/materials/3#page=4");
    expect(testQuery(7, 10)).toBe("/test?topic=7&n=10");
    expect(testQuery(null, 20)).toBe("/test?n=20");
  });
});

describe("queries", () => {
  it("shouldRetryQuery skips client errors and retries transient errors up to 2 times", () => {
    for (const status of [400, 401, 403, 404, 409, 413, 415, 422]) {
      expect(shouldRetryQuery(0, new ApiError("fail", status))).toBe(false);
    }

    for (const status of [502, 503]) {
      expect(shouldRetryQuery(0, new ApiError("upstream", status))).toBe(true);
      expect(shouldRetryQuery(1, new ApiError("upstream", status))).toBe(true);
      expect(shouldRetryQuery(2, new ApiError("upstream", status))).toBe(false);
    }

    expect(shouldRetryQuery(0, new TypeError("Failed to fetch"))).toBe(true);
    expect(shouldRetryQuery(1, new TypeError("Failed to fetch"))).toBe(true);
    expect(shouldRetryQuery(2, new TypeError("Failed to fetch"))).toBe(false);
  });

  it("queryKeys share subject prefix for invalidation", () => {
    const subjectKey = queryKeys.subject("PDSS");
    expect(queryKeys.allSubjects()).toEqual(["subject"]);
    expect(subjectKey).toEqual(["subject", "PDSS"]);
    expect(queryKeys.subjects()).toEqual(["subjects"]);
    expect(queryKeys.tree("PDSS").slice(0, subjectKey.length)).toEqual(subjectKey);
    expect(queryKeys.attempts("PDSS").slice(0, subjectKey.length)).toEqual(subjectKey);
    expect(queryKeys.attempt("PDSS", 42).slice(0, subjectKey.length)).toEqual(subjectKey);
  });

  it("createAppQueryClient configures staleTime, retry, and refetchOnWindowFocus", () => {
    const client = createAppQueryClient();
    const defaults = client.getDefaultOptions().queries;
    expect(defaults?.staleTime).toBe(10_000);
    expect(defaults?.retry).toBe(shouldRetryQuery);
    expect(defaults?.refetchOnWindowFocus).toBe(true);
  });
});

describe("sse", () => {
  afterEach(() => {
    setEventStreamFactory(null);
  });

  it("uses custom factory when configured and falls back to global EventSource", () => {
    const fakeStream: EventSourceLike = {
      onerror: null,
      addEventListener: vi.fn(),
      close: vi.fn(),
    };
    const factory = vi.fn(() => fakeStream);
    setEventStreamFactory(factory);
    expect(createEventStream("/api/runs/stream")).toBe(fakeStream);
    expect(factory).toHaveBeenCalledWith("/api/runs/stream");

    setEventStreamFactory(null);
    const originalEventSource = globalThis.EventSource;
    const seenUrls: string[] = [];
    class MockES {
      onerror = null;
      addEventListener = vi.fn();
      close = vi.fn();
      constructor(url: string) {
        seenUrls.push(url);
      }
    }
    globalThis.EventSource = MockES as unknown as typeof EventSource;
    try {
      const stream = createEventStream("/api/runs/stream");
      expect(stream).toBeInstanceOf(MockES);
      expect(seenUrls).toEqual(["/api/runs/stream"]);
    } finally {
      globalThis.EventSource = originalEventSource;
    }
  });
});
