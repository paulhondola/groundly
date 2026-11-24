import { QueryClient } from "@tanstack/svelte-query";
import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

vi.hoisted(() => {
  if (typeof globalThis.IntersectionObserver === "undefined") {
    globalThis.IntersectionObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof IntersectionObserver;
  }
});

import App from "../../../App.svelte";
import type { AttemptResult, DrawTestResponse, TopicNode, TreeResponse } from "../../api/client";
import { queryClient, queryKeys } from "../../api/queries";
import { navigate, route } from "../../router";
import { createThemeStore } from "../../theme.svelte";
import ResultView from "../ResultView.svelte";
import SubjectsView from "../SubjectsView.svelte";
import TestView from "../TestView.svelte";
import TreeView from "../TreeView.svelte";
import QueryHarness from "./QueryHarness.svelte";
import { loadTest, storageKey } from "../test-session";

function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
}

function requestPathname(input: RequestInfo | URL): string {
  if (input instanceof Request) {
    return new URL(input.url).pathname;
  }
  if (input instanceof URL) {
    return input.pathname;
  }
  return new URL(String(input), location.origin).pathname;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeTopic(id: number, title: string, questions: number): TopicNode {
  return {
    id,
    title,
    summary: "Summary for " + title,
    sources: [],
    subtopics: [],
    progress: {
      questions,
      answered: 0,
      recent: 0,
      recent_correct: 0,
      state: "not_started",
      subtopics: null,
      subtopics_mastered: null,
    },
  };
}

describe("TestView", () => {
  const originalFetch = globalThis.fetch;
  let container: HTMLDivElement;

  beforeEach(async () => {
    sessionStorage.clear();
    vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    await navigate("/");
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(async () => {
    await navigate("/");
    container.remove();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("clears unanswered warning when the question is answered, and never re-saves after unknown_question", async () => {
    const qc = createTestQueryClient();
    const tree: TreeResponse = { title: "Parallel Systems", tree: [makeTopic(7, "Locks", 2)] };
    const drawn: DrawTestResponse = {
      questions: [
        {
          id: 101,
          topic_id: 7,
          kind: "single",
          stem: "What prevents race conditions?",
          code: null,
          options: [
            { id: 1, text: "Mutex" },
            { id: 2, text: "Sleep" },
          ],
        },
      ],
    };

    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const pathname = requestPathname(input);
      if (pathname === "/api/subjects/PDSS/tree") {
        return Promise.resolve(jsonResponse(tree));
      }
      if (pathname === "/api/subjects/PDSS/test") {
        return Promise.resolve(jsonResponse(drawn));
      }
      if (pathname === "/api/subjects/PDSS/attempts") {
        return Promise.resolve(
          jsonResponse({ error: "unknown_question", detail: "question 101 was removed" }, 400),
        );
      }
      return Promise.resolve(jsonResponse({}));
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const instance = mount(QueryHarness, {
      target: container,
      props: { client: qc, component: TestView, props: { subject: "PDSS", topicId: 7, n: 5 } },
    });

    await vi.waitFor(() => {
      expect(container.textContent).toContain("What prevents race conditions?");
    });
    const key = storageKey("PDSS", 7);
    expect(loadTest(key)).not.toBeNull();

    // Click Submit with 1 unanswered question -> alertdialog appears
    const submitBtn = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Submit",
    );
    submitBtn?.click();
    flushSync();
    const dialogText = container
      .querySelector('[role="alertdialog"]')
      ?.textContent?.replace(/\s+/g, " ");
    expect(dialogText).toContain("1 question is unanswered");

    // Answer the question -> confirmation banner disappears immediately
    const radio = container.querySelector<HTMLInputElement>('input[type="radio"]');
    radio?.click();
    flushSync();
    expect(container.querySelector('[role="alertdialog"]')).toBeNull();

    // Submit again -> server responds unknown_question -> storage dropped and never re-saved
    submitBtn?.click();
    await vi.waitFor(() => {
      expect(container.textContent).toContain("question 101 was removed");
    });
    expect(loadTest(key)).toBeNull();

    // Interacting with another option after unknown_question must never re-save to sessionStorage
    const radios = container.querySelectorAll<HTMLInputElement>('input[type="radio"]');
    radios[1]?.click();
    flushSync();
    expect(loadTest(key)).toBeNull();

    void unmount(instance);
  });

  it("refreshes tree title on subject invalidation without redrawing GET /api/subjects/{subject}/test (Review Focus #4)", async () => {
    const qc = createTestQueryClient();
    let treeCalls = 0;
    let testCalls = 0;
    const drawn: DrawTestResponse = {
      questions: [
        {
          id: 101,
          topic_id: 7,
          kind: "single",
          stem: "What prevents race conditions?",
          code: null,
          options: [
            { id: 1, text: "Mutex" },
            { id: 2, text: "Sleep" },
          ],
        },
      ],
    };

    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const pathname = requestPathname(input);
      if (pathname === "/api/subjects/PDSS/tree") {
        treeCalls += 1;
        return Promise.resolve(
          jsonResponse({
            title: treeCalls === 1 ? "Parallel Systems" : "Parallel & Distributed Systems",
            tree: [makeTopic(7, treeCalls === 1 ? "Locks" : "Locks & Mutexes", 2)],
          }),
        );
      }
      if (pathname === "/api/subjects/PDSS/test") {
        testCalls += 1;
        return Promise.resolve(jsonResponse(drawn));
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    const instance = mount(QueryHarness, {
      target: container,
      props: { client: qc, component: TestView, props: { subject: "PDSS", topicId: 7, n: 5 } },
    });

    await vi.waitFor(() => {
      expect(container.textContent).toContain("What prevents race conditions?");
    });
    expect(container.textContent).toContain("Parallel Systems");
    expect(container.textContent).toContain("Locks");
    expect(treeCalls).toBe(1);
    expect(testCalls).toBe(1);

    const radio = container.querySelector<HTMLInputElement>('input[type="radio"]');
    radio?.click();
    flushSync();
    expect(radio?.checked).toBe(true);
    const key = storageKey("PDSS", 7);
    const initialToken = loadTest(key)?.token;
    expect(initialToken).toBeTruthy();

    await qc.invalidateQueries({ queryKey: queryKeys.subject("PDSS") });

    await vi.waitFor(() => {
      expect(container.textContent).toContain("Parallel & Distributed Systems");
    });
    expect(container.textContent).toContain("Locks & Mutexes");
    expect(treeCalls).toBe(2);
    expect(testCalls).toBe(1);
    expect(radio?.checked).toBe(true);
    expect(loadTest(key)?.token).toBe(initialToken);

    void unmount(instance);
  });

  it("submits answers, drops draft, invalidates subject queries, and navigates to attempt result", async () => {
    const qc = createTestQueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    let submittedBody: unknown = null;
    const tree: TreeResponse = { title: "Intro CS", tree: [makeTopic(7, "Basics", 2)] };
    const drawn: DrawTestResponse = {
      questions: [
        {
          id: 101,
          topic_id: 7,
          kind: "single",
          stem: "What prevents race conditions?",
          code: null,
          options: [
            { id: 1, text: "Mutex" },
            { id: 2, text: "Sleep" },
          ],
        },
      ],
    };

    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const pathname = requestPathname(input);
      if (pathname === "/api/subjects/CS%20101/tree") {
        return jsonResponse(tree);
      }
      if (pathname === "/api/subjects/CS%20101/test") {
        return jsonResponse(drawn);
      }
      if (pathname === "/api/subjects/CS%20101/attempts" && input instanceof Request) {
        submittedBody = await input.json();
        return jsonResponse({ id: 42 }, 201);
      }
      return jsonResponse({});
    }) as unknown as typeof fetch;

    const instance = mount(QueryHarness, {
      target: container,
      props: { client: qc, component: TestView, props: { subject: "CS 101", topicId: 7, n: 5 } },
    });

    await vi.waitFor(() => {
      expect(container.textContent).toContain("What prevents race conditions?");
    });

    const key = storageKey("CS 101", 7);
    const storedToken = loadTest(key)?.token;
    expect(storedToken).toBeTruthy();

    const mutexLabel = Array.from(container.querySelectorAll("label")).find((l) =>
      l.textContent?.includes("Mutex"),
    );
    mutexLabel?.querySelector<HTMLInputElement>('input[type="radio"]')?.click();
    flushSync();

    const submitBtn = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Submit",
    );
    submitBtn?.click();

    await vi.waitFor(() => {
      expect(window.location.pathname).toBe("/s/CS%20101/attempts/42");
    });

    expect(submittedBody).toEqual({
      token: storedToken,
      topic_id: 7,
      answers: [{ question_id: 101, selected: [1] }],
    });
    expect(loadTest(key)).toBeNull();
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: queryKeys.subject("CS 101") });
    expect(route.params.subject).toBe("CS 101");
    expect(route.params.id).toBe("42");

    void unmount(instance);
  });
});

describe("SubjectsView", () => {
  const originalFetch = globalThis.fetch;
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    container.remove();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders subjects from GET /api/subjects and caches them in QueryClient", async () => {
    const qc = createTestQueryClient();
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const pathname = requestPathname(input);
      if (pathname === "/api/runs") {
        return Promise.resolve(
          jsonResponse({ available: false, reason: "no_host", target: 10, run: null }),
        );
      }
      if (pathname === "/api/subjects") {
        return Promise.resolve(
          jsonResponse([
            { subject: "PDSS", title: "Parallel Systems", has_tree: true, error: null },
            { subject: "OS", title: "", has_tree: false, error: null },
          ]),
        );
      }
      return Promise.resolve(jsonResponse([]));
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const instance = mount(QueryHarness, {
      target: container,
      props: { client: qc, component: SubjectsView },
    });

    await vi.waitFor(() => {
      expect(container.textContent).toContain("Parallel Systems");
    });

    expect(document.title).toBe("Groundly");
    expect(container.textContent).toContain("PDSS");
    expect(container.textContent).toContain("topic map ready");
    expect(container.textContent).toContain("OS");
    expect(container.textContent).toContain("no map yet");
    expect(container.querySelector('a[href="/s/PDSS"]')).not.toBeNull();
    expect(qc.getQueryData(queryKeys.subjects())).toHaveLength(2);

    void unmount(instance);
  });

  it("displays an alert when GET /api/subjects fails", async () => {
    const qc = createTestQueryClient();
    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const pathname = requestPathname(input);
      if (pathname === "/api/runs") {
        return Promise.resolve(
          jsonResponse({ available: false, reason: "no_host", target: 10, run: null }),
        );
      }
      if (pathname === "/api/subjects") {
        return Promise.resolve(
          jsonResponse({ error: "db_error", detail: "Database unavailable" }, 500),
        );
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    const instance = mount(QueryHarness, {
      target: container,
      props: { client: qc, component: SubjectsView },
    });

    await vi.waitFor(() => {
      const alert = container.querySelector('[role="alert"]');
      expect(alert?.textContent).toContain("Database unavailable");
    });

    void unmount(instance);
  });
});

describe("TreeView", () => {
  const originalFetch = globalThis.fetch;
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    container.remove();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("resets stale title and actions on unmount and ignores late responses after unmount", async () => {
    const qc = createTestQueryClient();
    let resolveSlowTree!: (res: Response) => void;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const pathname = requestPathname(input);
      if (pathname === "/api/runs") {
        return Promise.resolve(
          jsonResponse({ available: false, reason: "no_host", target: 10, run: null }),
        );
      }
      if (pathname === "/api/subjects/PDSS/tree") {
        return new Promise<Response>((resolve) => {
          resolveSlowTree = resolve;
        });
      }
      return Promise.resolve(jsonResponse([]));
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const instance = mount(QueryHarness, {
      target: container,
      props: { client: qc, component: TreeView, props: { subject: "PDSS" } },
    });
    flushSync();

    await vi.waitFor(() => {
      expect(resolveSlowTree).toBeTypeOf("function");
    });

    // Navigate away before /tree resolves
    void unmount(instance);
    document.title = "Groundly";

    resolveSlowTree(jsonResponse({ title: "Parallel Systems", tree: [makeTopic(1, "Intro", 5)] }));
    await new Promise((r) => setTimeout(r, 0));

    expect(document.title).toBe("Groundly");
  });

  it("updates the open topic drawer question count in place on SSE invalidation without closing the drawer", async () => {
    const qc = createTestQueryClient();
    let treeCalls = 0;
    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const pathname = requestPathname(input);
      if (pathname === "/api/runs") {
        return Promise.resolve(
          jsonResponse({ available: false, reason: "no_host", target: 10, run: null }),
        );
      }
      if (pathname === "/api/subjects/PDSS/tree") {
        treeCalls += 1;
        const questions = treeCalls === 1 ? 2 : 10;
        return Promise.resolve(
          jsonResponse({ title: "Parallel Systems", tree: [makeTopic(7, "Locks", questions)] }),
        );
      }
      if (pathname === "/api/subjects/PDSS/attempts") {
        return Promise.resolve(jsonResponse([]));
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    const instance = mount(QueryHarness, {
      target: container,
      props: { client: qc, component: TreeView, props: { subject: "PDSS" } },
    });

    await vi.waitFor(() => {
      expect(container.textContent).toContain("Locks");
    });

    const topicBtn = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("Locks"),
    );
    topicBtn?.click();
    flushSync();

    const drawer = container.querySelector('aside[aria-label="Topic details"]');
    expect(drawer?.classList.contains("open")).toBe(true);
    expect(drawer?.textContent?.replace(/\s+/g, " ")).toContain("2 questions");

    await qc.invalidateQueries({ queryKey: queryKeys.subject("PDSS") });

    await vi.waitFor(() => {
      expect(drawer?.textContent?.replace(/\s+/g, " ")).toContain("10 questions");
    });
    expect(drawer?.classList.contains("open")).toBe(true);
    expect(drawer?.textContent).toContain("Locks");

    void unmount(instance);
  });

  it("clears open drawer and filter and does not flash PDSS topics when switching to pending PASSC", async () => {
    const qc = createTestQueryClient();
    let resolvePasscTree!: (res: Response) => void;
    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const pathname = requestPathname(input);
      if (pathname === "/api/runs") {
        return Promise.resolve(
          jsonResponse({ available: false, reason: "no_host", target: 10, run: null }),
        );
      }
      if (pathname === "/api/subjects/PDSS/tree") {
        return Promise.resolve(
          jsonResponse({ title: "Parallel Systems", tree: [makeTopic(7, "Locks", 2)] }),
        );
      }
      if (pathname === "/api/subjects/PASSC/tree") {
        return new Promise<Response>((resolve) => {
          resolvePasscTree = resolve;
        });
      }
      if (
        pathname === "/api/subjects/PDSS/attempts" ||
        pathname === "/api/subjects/PASSC/attempts"
      ) {
        return Promise.resolve(jsonResponse([]));
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    const instance = mount(QueryHarness, {
      target: container,
      props: { client: qc, component: TreeView, props: { subject: "PDSS" } },
    });

    await vi.waitFor(() => {
      expect(container.textContent).toContain("Locks");
    });

    // Open Locks drawer and set search filter
    const topicBtn = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("Locks"),
    );
    topicBtn?.click();
    flushSync();

    const searchInput = container.querySelector<HTMLInputElement>(
      'input[aria-label="Filter topics"]',
    );
    expect(searchInput).not.toBeNull();
    if (searchInput) {
      searchInput.value = "Lock";
      searchInput.dispatchEvent(new Event("input", { bubbles: true }));
    }
    flushSync();
    expect(container.querySelector("aside.open")?.textContent?.replace(/\s+/g, " ")).toContain(
      "2 questions",
    );

    // Switch subject from cached PDSS to pending PASSC
    instance.setProps({ subject: "PASSC" });
    flushSync();

    // Must immediately show loading and NOT flash PDSS topic titles or open drawer
    expect(container.textContent).toContain("Loading topic map…");
    expect(container.textContent).not.toContain("Locks");
    expect(container.querySelector("aside.open")).toBeNull();
    expect(container.textContent?.replace(/\s+/g, " ")).not.toContain("2 questions");

    // Resolve PASSC and ensure filter was reset (Capabilities is visible) and drawer remains closed
    await vi.waitFor(() => {
      expect(resolvePasscTree).toBeTypeOf("function");
    });
    resolvePasscTree(
      jsonResponse({ title: "Applied Security", tree: [makeTopic(9, "Capabilities", 4)] }),
    );
    await vi.waitFor(() => {
      expect(container.textContent).toContain("Capabilities");
    });
    expect(container.querySelector("aside.open")).toBeNull();

    void unmount(instance);
  });
});

describe("ResultView", () => {
  const originalFetch = globalThis.fetch;
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    container.remove();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders fallback phrase when host is unavailable and resolves topic scope via tree", async () => {
    const qc = createTestQueryClient();
    const attempt: AttemptResult = {
      id: 4,
      submitted_at: "2026-11-24 12:00",
      scope_topic_id: 7,
      score: { correct: 0, graded: 0, awaiting: 1 },
      provisional: true,
      answers: [
        {
          answer_id: 10,
          question_id: 101,
          kind: "open",
          state: "awaiting",
          removed: false,
          stem: "Explain deadlock.",
          code: null,
          options: [],
          text: "Four Coffman conditions.",
          reference_answer: "Mutual exclusion, hold and wait, no preemption, circular wait.",
          comment: null,
          sources: [],
        },
      ],
      topics: [],
    };
    const tree: TreeResponse = { title: "Parallel Systems", tree: [makeTopic(7, "Deadlocks", 5)] };

    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const pathname = requestPathname(input);
      if (pathname === "/api/runs") {
        return Promise.resolve(
          jsonResponse({ available: false, reason: "no_host", target: 10, run: null }),
        );
      }
      if (pathname === "/api/subjects/PDSS/attempts/4") {
        return Promise.resolve(jsonResponse(attempt));
      }
      if (pathname === "/api/subjects/PDSS/tree") {
        return Promise.resolve(jsonResponse(tree));
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    const instance = mount(QueryHarness, {
      target: container,
      props: { client: qc, component: ResultView, props: { subject: "PDSS", attemptId: 4 } },
    });

    await vi.waitFor(() => {
      expect(container.textContent).toContain("Deadlocks · 2026-11-24 12:00");
    });
    expect(container.textContent).toContain("grade my test");
    expect(container.textContent).not.toContain("Grade now");

    void unmount(instance);
  });

  it("refreshes awaiting open-answer verdicts in place when subject queries are invalidated", async () => {
    const qc = createTestQueryClient();
    let attemptCalls = 0;
    const makeAttempt = (graded: boolean): AttemptResult => ({
      id: 4,
      submitted_at: "2026-11-24 12:00",
      scope_topic_id: 7,
      score: graded
        ? { correct: 1, graded: 1, awaiting: 0 }
        : { correct: 0, graded: 0, awaiting: 1 },
      provisional: !graded,
      answers: [
        {
          answer_id: 10,
          question_id: 101,
          kind: "open",
          state: graded ? "correct" : "awaiting",
          removed: false,
          stem: "Explain deadlock.",
          code: null,
          options: [],
          text: "Four Coffman conditions.",
          reference_answer: "Mutual exclusion, hold and wait, no preemption, circular wait.",
          comment: graded ? "Covers all four Coffman conditions." : null,
          sources: [],
        },
      ],
      topics: [],
    });
    const tree: TreeResponse = { title: "Parallel Systems", tree: [makeTopic(7, "Deadlocks", 5)] };

    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const pathname = requestPathname(input);
      if (pathname === "/api/runs") {
        return Promise.resolve(
          jsonResponse({ available: false, reason: "no_host", target: 10, run: null }),
        );
      }
      if (pathname === "/api/subjects/PDSS/attempts/4") {
        attemptCalls += 1;
        return Promise.resolve(jsonResponse(makeAttempt(attemptCalls > 1)));
      }
      if (pathname === "/api/subjects/PDSS/tree") {
        return Promise.resolve(jsonResponse(tree));
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    const instance = mount(QueryHarness, {
      target: container,
      props: { client: qc, component: ResultView, props: { subject: "PDSS", attemptId: 4 } },
    });

    await vi.waitFor(() => {
      expect(container.textContent).toContain("Awaiting review");
    });
    expect(container.textContent).toContain("0 / 0 correct");
    expect(container.textContent).toContain("provisional, 1 awaiting review");
    expect(attemptCalls).toBe(1);

    await qc.invalidateQueries({ queryKey: queryKeys.subject("PDSS") });

    await vi.waitFor(() => {
      expect(container.textContent).toContain("1 / 1 correct");
    });
    expect(attemptCalls).toBe(2);
    expect(container.textContent).toContain("Correct");
    expect(container.textContent).toContain("Covers all four Coffman conditions.");
    expect(container.textContent).not.toContain("Awaiting review");

    void unmount(instance);
  });

  it("renders attempt 404 error immediately while tree query is still pending", async () => {
    const qc = createTestQueryClient();
    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const pathname = requestPathname(input);
      if (pathname === "/api/runs") {
        return Promise.resolve(
          jsonResponse({ available: false, reason: "no_host", target: 10, run: null }),
        );
      }
      if (pathname === "/api/subjects/PDSS/attempts/999") {
        return Promise.resolve(
          jsonResponse({ error: "unknown_attempt", detail: "Unknown attempt 999" }, 404),
        );
      }
      if (pathname === "/api/subjects/PDSS/tree") {
        return new Promise<Response>(() => {});
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    const instance = mount(QueryHarness, {
      target: container,
      props: { client: qc, component: ResultView, props: { subject: "PDSS", attemptId: 999 } },
    });

    await vi.waitFor(() => {
      expect(container.querySelector('[role="alert"]')?.textContent).toContain(
        "Unknown attempt 999",
      );
    });
    expect(container.textContent).not.toContain("Loading results…");

    void unmount(instance);
  });
});

describe("App shell", () => {
  const originalFetch = globalThis.fetch;
  let container: HTMLDivElement;

  beforeEach(async () => {
    sessionStorage.clear();
    queryClient.clear();
    vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    await navigate("/");
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(async () => {
    await navigate("/");
    queryClient.clear();
    container.remove();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders QueryClientProvider + sv-router views and derives breadcrumb without double-decoding %", async () => {
    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const pathname = requestPathname(input);
      if (pathname === "/api/runs") {
        return Promise.resolve(
          jsonResponse({ available: false, reason: "no_host", target: 10, run: null }),
        );
      }
      if (pathname === "/api/subjects") {
        return Promise.resolve(
          jsonResponse([{ subject: "100%", title: "100% Course", has_tree: true, error: null }]),
        );
      }
      if (pathname === "/api/subjects/100%25/tree" || pathname === "/api/subjects/PDSS/tree") {
        return Promise.resolve(jsonResponse({ title: "Course Title", tree: [] }));
      }
      if (
        pathname === "/api/subjects/100%25/attempts" ||
        pathname === "/api/subjects/PDSS/attempts"
      ) {
        return Promise.resolve(jsonResponse([]));
      }
      if (pathname === "/api/subjects/100%25/test") {
        return Promise.resolve(jsonResponse({ questions: [] }));
      }
      if (pathname === "/api/subjects/PDSS/attempts/42") {
        return Promise.resolve(
          jsonResponse({
            id: 42,
            submitted_at: "2026-11-24 12:00",
            scope_topic_id: null,
            score: { correct: 1, graded: 1, awaiting: 0 },
            provisional: false,
            answers: [],
            topics: [],
          }),
        );
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    const instance = mount(App, { target: container });

    await vi.waitFor(() => {
      expect(container.textContent).toContain("100% Course");
    });
    expect(container.querySelector('nav[aria-label="Breadcrumb"]')).toBeNull();

    await navigate("/s/:subject", { params: { subject: "100%" } });
    await vi.waitFor(() => {
      const nav = container.querySelector('nav[aria-label="Breadcrumb"]');
      expect(nav?.textContent?.replace(/\s+/g, " ").trim()).toBe("/ 100%");
    });

    await navigate("/s/:subject/test", { params: { subject: "100%" }, search: { n: 5 } });
    await vi.waitFor(() => {
      const nav = container.querySelector('nav[aria-label="Breadcrumb"]');
      expect(nav?.textContent?.replace(/\s+/g, " ").trim()).toBe("/ 100% / Test");
    });
    const breadcrumbLink = container.querySelector('nav[aria-label="Breadcrumb"] a');
    expect(breadcrumbLink?.getAttribute("href")).toBe("/s/100%25");

    await navigate("/s/:subject/attempts/:id", { params: { subject: "PDSS", id: "42" } });
    await vi.waitFor(() => {
      const nav = container.querySelector('nav[aria-label="Breadcrumb"]');
      expect(nav?.textContent?.replace(/\s+/g, " ").trim()).toBe("/ PDSS / Result");
    });

    await navigate("/s/:subject/attempts/:id", { params: { subject: "PDSS", id: "abc" } });
    await vi.waitFor(() => {
      expect(container.textContent).toContain("Page not found");
    });
    expect(container.querySelector('nav[aria-label="Breadcrumb"]')).toBeNull();

    await unmount(instance);
  });
});

describe("createThemeStore", () => {
  afterEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark");
    vi.unstubAllGlobals();
  });

  it("persists mode, toggles dark class, and reacts to system preference changes", () => {
    const listeners: Array<(ev: MediaQueryListEvent) => void> = [];
    const removeSpy = vi.fn();
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation(() => ({
        matches: false,
        media: "(prefers-color-scheme: dark)",
        addEventListener: (_: string, cb: (ev: MediaQueryListEvent) => void) => {
          listeners.push(cb);
        },
        removeEventListener: removeSpy,
      })),
    );

    const store = createThemeStore();
    expect(store.mode).toBe("system");
    expect(store.resolved).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    expect(listeners).toHaveLength(1);
    listeners[0]({ matches: true } as MediaQueryListEvent);
    expect(store.resolved).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    store.setMode("light");
    expect(localStorage.getItem("groundly-theme")).toBe("light");
    expect(store.resolved).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    store.destroy();
    expect(removeSpy).toHaveBeenCalledTimes(1);
  });
});
