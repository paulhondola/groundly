import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { runStore } from "../../run.svelte";
import { createThemeStore } from "../../theme.svelte";
import type { AttemptResult, DrawTestResponse, TopicNode, TreeResponse } from "../../types";
import ResultView from "../ResultView.svelte";
import TestView from "../TestView.svelte";
import TreeView from "../TreeView.svelte";
import { loadTest, storageKey } from "../test-session";

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

  beforeEach(() => {
    sessionStorage.clear();
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    container.remove();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("clears unanswered warning when the question is answered, and never re-saves after unknown_question", async () => {
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

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(tree))
      .mockResolvedValueOnce(jsonResponse(drawn))
      .mockResolvedValueOnce(
        jsonResponse({ error: "unknown_question", detail: "question 101 was removed" }, 400),
      );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const instance = mount(TestView, {
      target: container,
      props: { subject: "PDSS", topicId: 7, n: 5 },
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
    runStore.setHooks({ subject: null, onRefresh: null, onEnd: null });
    vi.restoreAllMocks();
  });

  it("resets stale title and actions on unmount and ignores late responses after unmount", async () => {
    let resolveSlowTree!: (res: Response) => void;
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === "/api/runs") {
        return Promise.resolve(
          jsonResponse({ available: false, reason: "no_host", target: 10, run: null }),
        );
      }
      if (url === "/api/subjects/PDSS/tree") {
        return new Promise<Response>((resolve) => {
          resolveSlowTree = resolve;
        });
      }
      return Promise.resolve(jsonResponse([]));
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const instance = mount(TreeView, {
      target: container,
      props: { subject: "PDSS" },
    });
    flushSync();

    // Navigate away before /tree resolves
    void unmount(instance);
    document.title = "Groundly";

    resolveSlowTree(jsonResponse({ title: "Parallel Systems", tree: [makeTopic(1, "Intro", 5)] }));
    await new Promise((r) => setTimeout(r, 0));

    expect(document.title).toBe("Groundly");
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
    runStore.setHooks({ subject: null, onRefresh: null, onEnd: null });
    vi.restoreAllMocks();
  });

  it("renders fallback phrase when host is unavailable and resolves topic scope via tree", async () => {
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

    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (url === "/api/runs") {
        return Promise.resolve(
          jsonResponse({ available: false, reason: "no_host", target: 10, run: null }),
        );
      }
      if (url === "/api/subjects/PDSS/attempts/4") {
        return Promise.resolve(jsonResponse(attempt));
      }
      if (url === "/api/subjects/PDSS/tree") {
        return Promise.resolve(jsonResponse(tree));
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    const instance = mount(ResultView, {
      target: container,
      props: { subject: "PDSS", attemptId: 4 },
    });

    await vi.waitFor(() => {
      expect(container.textContent).toContain("Deadlocks · 2026-11-24 12:00");
    });
    expect(container.textContent).toContain("grade my test");
    expect(container.textContent).not.toContain("Grade now");

    void unmount(instance);
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
