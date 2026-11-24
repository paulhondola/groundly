import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import type { RunSnapshot } from "../api/client";
import { createAppQueryClient } from "../api/queries";
import { type EventSourceLike, setEventStreamFactory } from "../api/sse";
import { createRunStore } from "../run.svelte";

class MockEventSource implements EventSourceLike {
  static instances: MockEventSource[] = [];

  readonly url: string;
  closed = false;
  onerror: ((ev: Event) => void) | null = null;
  private listeners = new Map<string, Array<(ev: MessageEvent<string>) => void>>();

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: (ev: MessageEvent<string>) => void): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(cb);
    this.listeners.set(type, existing);
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string, data: unknown): void {
    const event = { data: JSON.stringify(data) } as MessageEvent<string>;
    for (const cb of this.listeners.get(type) ?? []) {
      cb(event);
    }
  }

  triggerError(): void {
    this.onerror?.(new Event("error"));
  }
}

function makeSnapshot(overrides: Partial<RunSnapshot> = {}): RunSnapshot {
  return {
    id: 1,
    kind: "write",
    subject: "PDSS",
    topic_id: 7,
    status: "running",
    phase: "starting",
    active_tool: null,
    thought: "",
    thought_channel: null,
    steps: [],
    summary: null,
    error: null,
    started_at: "2026-11-24T10:00:00+00:00",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("createRunStore", () => {
  const originalFetch = globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    MockEventSource.instances = [];
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    setEventStreamFactory((url) => new MockEventSource(url));
  });

  afterEach(() => {
    setEventStreamFactory(null);
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("streams snapshot, phase, coalesced deltas capped at 2000 chars, subject-scoped step refresh, query invalidation, and done", async () => {
    const qc = createAppQueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const store = createRunStore(qc);
    const onRefresh = vi.fn();
    const onEnd = vi.fn();
    store.setHooks({ subject: "PDSS", onRefresh, onEnd });

    const initial = makeSnapshot({ id: 10, subject: "PDSS" });
    fetchMock.mockResolvedValueOnce(jsonResponse(initial, 202));

    await store.start("PDSS", { kind: "write", topic_id: 7 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const startReq = fetchMock.mock.calls[0][0] as Request;
    expect(startReq.method).toBe("POST");
    expect(new URL(startReq.url).pathname).toBe("/api/subjects/PDSS/runs");
    expect(await startReq.json()).toEqual({ kind: "write", topic_id: 7 });

    expect(MockEventSource.instances).toHaveLength(1);
    const es = MockEventSource.instances[0];
    expect(es.url).toBe("/api/runs/stream");

    es.emit("snapshot", initial);
    expect(store.run?.id).toBe(10);

    es.emit("phase", { id: 10, phase: "writing", active_tool: "Drafting questions" });
    expect(store.run?.phase).toBe("writing");
    expect(store.run?.active_tool).toBe("Drafting questions");

    es.emit("delta", { id: 10, channel: "thinking", text: "Initial plan", reset: true });
    expect(store.run?.thought).toBe("Initial plan");
    expect(store.run?.thought_channel).toBe("thinking");

    const overflow = "x".repeat(2050);
    es.emit("delta", { id: 10, channel: "text", text: overflow, reset: false });
    expect(store.run?.thought).toHaveLength(2000);
    expect(store.run?.thought).toBe(overflow.slice(-2000));
    expect(store.run?.thought_channel).toBe("text");

    es.emit("step", { id: 10, step: "Submitting 1 questions", refresh: false });
    expect(store.run?.steps).toEqual(["Submitting 1 questions"]);
    expect(onRefresh).not.toHaveBeenCalled();
    expect(invalidateSpy).not.toHaveBeenCalled();

    es.emit("step", { id: 10, step: "1 stored, 0 rejected", refresh: true });
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(invalidateSpy).toHaveBeenCalledTimes(1);
    expect(invalidateSpy).toHaveBeenLastCalledWith({ queryKey: ["subject", "PDSS"] });

    // Review Focus #4: ignore hook refresh while viewing another subject, while still invalidating the run's subject cache
    store.setHooks({ subject: "ALGO", onRefresh, onEnd });
    es.emit("step", { id: 10, step: "2 stored, 0 rejected", refresh: true });
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(invalidateSpy).toHaveBeenCalledTimes(2);
    expect(invalidateSpy).toHaveBeenLastCalledWith({ queryKey: ["subject", "PDSS"] });

    // Switch back to the run's subject before settlement so onEnd fires for it
    store.setHooks({ subject: "PDSS", onRefresh, onEnd });

    const settled = makeSnapshot({
      id: 10,
      subject: "PDSS",
      status: "done",
      phase: null,
      active_tool: null,
      thought: "",
      thought_channel: null,
      steps: ["Submitting 1 questions", "1 stored, 0 rejected", "2 stored, 0 rejected"],
      summary: "Stored 2 questions.",
    });
    es.emit("done", settled);

    expect(es.closed).toBe(true);
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(invalidateSpy).toHaveBeenCalledTimes(3);
    expect(invalidateSpy).toHaveBeenLastCalledWith({ queryKey: ["subject", "PDSS"] });
    expect(store.message).toBe("Stored 2 questions.");
    expect(store.busy).toBe(false);
    store.destroy();
  });

  it("invalidates subject query once on done and never twice for the same settled run ID", async () => {
    const qc = createAppQueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const store = createRunStore(qc);
    const onEnd = vi.fn();
    store.setHooks({ subject: "PDSS", onEnd });

    fetchMock.mockResolvedValueOnce(jsonResponse(makeSnapshot({ id: 15, subject: "PDSS" }), 202));
    await store.start("PDSS", { kind: "write", topic_id: 7 });

    const es = MockEventSource.instances[0];
    const settled = makeSnapshot({
      id: 15,
      subject: "PDSS",
      status: "done",
      phase: null,
      summary: "Stored 4 questions.",
    });

    es.emit("done", settled);
    expect(invalidateSpy).toHaveBeenCalledTimes(1);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["subject", "PDSS"] });
    expect(onEnd).toHaveBeenCalledTimes(1);

    // Duplicate settlement for the same run ID (e.g. an in-flight stop() resolving after done)
    fetchMock.mockResolvedValueOnce(jsonResponse({ run: settled }));
    await store.stop();

    expect(invalidateSpy).toHaveBeenCalledTimes(1);
    expect(onEnd).toHaveBeenCalledTimes(1);
    store.destroy();
  });

  it("invalidates all subject queries and reports restart when snapshot is null while watching a running job", async () => {
    const qc = createAppQueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const store = createRunStore(qc);
    const onEnd = vi.fn();
    store.setHooks({ subject: "PDSS", onEnd });

    fetchMock.mockResolvedValueOnce(jsonResponse(makeSnapshot({ id: 19, subject: "PDSS" }), 202));
    await store.start("PDSS", { kind: "write", topic_id: 7 });

    const es = MockEventSource.instances[0];
    es.emit("snapshot", null);

    expect(es.closed).toBe(true);
    expect(store.run).toBeNull();
    expect(store.message).toBe("Lost track of the run — the page server restarted.");
    expect(invalidateSpy).toHaveBeenCalledTimes(1);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["subject"] });
    expect(onEnd).toHaveBeenCalledTimes(1);
    store.destroy();
  });

  it("closes EventSource immediately when initial snapshot is already settled (Review Focus #1)", async () => {
    const qc = createAppQueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const store = createRunStore(qc);
    const onEnd = vi.fn();
    store.setHooks({ subject: "PDSS", onEnd });

    fetchMock.mockResolvedValueOnce(jsonResponse(makeSnapshot({ id: 21 }), 202));
    await store.start("PDSS", { kind: "write", topic_id: 7 });

    const es = MockEventSource.instances[0];
    es.emit(
      "snapshot",
      makeSnapshot({
        id: 21,
        status: "done",
        phase: null,
        summary: "Stored 5.",
      }),
    );

    expect(es.closed).toBe(true);
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(invalidateSpy).toHaveBeenCalledTimes(1);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["subject", "PDSS"] });
    expect(store.message).toBe("Stored 5.");
    store.destroy();
  });

  it("attaches to an active run when start() receives 409 conflict", async () => {
    const qc = createAppQueryClient();
    const store = createRunStore(qc);
    const active = makeSnapshot({ id: 33, kind: "grade", subject: "ALGO", topic_id: null });

    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(
          { error: "run_in_progress", detail: "a run is already working — wait or stop it" },
          409,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          available: true,
          reason: null,
          target: 10,
          run: active,
        }),
      );

    await store.start("PDSS", { kind: "write", topic_id: 7 });

    expect(store.message).toContain("Could not start:");
    expect(store.watched).toBe(33);
    expect(store.run?.id).toBe(33);
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toBe("/api/runs/stream");
    store.destroy();
  });

  it("closes EventSource on error, probes GET /api/runs once, and reports lost server on failure (Review Focus #5)", async () => {
    const qc = createAppQueryClient();
    const store = createRunStore(qc);
    const onEnd = vi.fn();
    store.setHooks({ subject: "PDSS", onEnd });

    fetchMock
      .mockResolvedValueOnce(jsonResponse(makeSnapshot({ id: 44 }), 202))
      .mockRejectedValueOnce(new TypeError("NetworkError"));

    await store.start("PDSS", { kind: "write", topic_id: 7 });
    const es = MockEventSource.instances[0];

    es.triggerError();
    await vi.waitFor(() => {
      expect(store.message).toBe("Lost the page server — reload the page when it is back.");
    });

    expect(es.closed).toBe(true);
    expect(store.run).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const probeReq = fetchMock.mock.calls[1][0] as Request;
    expect(probeReq.method).toBe("GET");
    expect(new URL(probeReq.url).pathname).toBe("/api/runs");
    expect(MockEventSource.instances).toHaveLength(1);
    expect(onEnd).not.toHaveBeenCalled();
    store.destroy();
  });

  it("reconciles settled run when EventSource errors after backend stopped the run", async () => {
    const qc = createAppQueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const store = createRunStore(qc);
    const onEnd = vi.fn();
    store.setHooks({ subject: "PDSS", onEnd });

    fetchMock
      .mockResolvedValueOnce(jsonResponse(makeSnapshot({ id: 55 }), 202))
      .mockResolvedValueOnce(
        jsonResponse({
          available: true,
          reason: null,
          target: 10,
          run: makeSnapshot({ id: 55, status: "stopped", phase: null }),
        }),
      );

    await store.start("PDSS", { kind: "write", topic_id: 7 });
    const es = MockEventSource.instances[0];
    es.triggerError();

    await vi.waitFor(() => {
      expect(onEnd).toHaveBeenCalledTimes(1);
    });

    expect(es.closed).toBe(true);
    expect(invalidateSpy).toHaveBeenCalledTimes(1);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["subject", "PDSS"] });
    expect(store.message).toBe("Stopped. What was already stored stays.");
    store.destroy();
  });

  it("attach() preserves active SSE stream state and ignores stale running response after SSE settlement", async () => {
    const qc = createAppQueryClient();
    const store = createRunStore(qc);
    const onEnd = vi.fn();
    store.setHooks({ subject: "PDSS", onEnd });

    fetchMock.mockResolvedValueOnce(jsonResponse(makeSnapshot({ id: 66 }), 202));
    await store.start("PDSS", { kind: "write", topic_id: 7 });
    const es = MockEventSource.instances[0];
    es.emit("delta", { id: 66, channel: "thinking", text: "Live SSE tokens", reset: true });
    es.emit("step", { id: 66, step: "Reading the course", refresh: false });

    // 1. Mid-run route change triggers attach(), which gets an older GET /api/runs snapshot:
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        available: true,
        reason: null,
        target: 10,
        run: makeSnapshot({ id: 66, thought: "", steps: [] }),
      }),
    );
    await store.attach();

    expect(store.run?.thought).toBe("Live SSE tokens");
    expect(store.run?.steps).toEqual(["Reading the course"]);
    expect(MockEventSource.instances).toHaveLength(1);

    // 2. Race: attach() is in flight while SSE delivers `done`, then stale attach() resolves with status "running":
    let resolveAttach!: (res: Response) => void;
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveAttach = resolve;
        }),
    );
    const pendingAttach = store.attach();
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });

    es.emit(
      "done",
      makeSnapshot({
        id: 66,
        status: "done",
        phase: null,
        summary: "Stored 3.",
      }),
    );
    resolveAttach(
      jsonResponse({
        available: true,
        reason: null,
        target: 10,
        run: makeSnapshot({ id: 66, status: "running" }),
      }),
    );
    await pendingAttach;

    expect(store.run?.status).toBe("done");
    expect(store.busy).toBe(false);
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(MockEventSource.instances).toHaveLength(1);
    store.destroy();
  });

  it("caps repeated EventSource error reconnects when GET /api/runs remains running", async () => {
    const qc = createAppQueryClient();
    const store = createRunStore(qc);

    fetchMock.mockResolvedValueOnce(jsonResponse(makeSnapshot({ id: 88 }), 202));
    await store.start("PDSS", { kind: "write", topic_id: 7 });

    const runningInfo = () =>
      jsonResponse({
        available: true,
        reason: null,
        target: 10,
        run: makeSnapshot({ id: 88, status: "running" }),
      });

    for (let i = 0; i < 4; i++) {
      const currentEs = MockEventSource.instances[MockEventSource.instances.length - 1];
      if (currentEs.closed) {
        break;
      }
      fetchMock.mockResolvedValueOnce(runningInfo());
      currentEs.triggerError();
      await vi.waitFor(() => {
        expect(fetchMock).toHaveBeenCalledTimes(i + 2);
      });
      await new Promise((r) => setTimeout(r, 0));
    }

    // Initial connect (1) + capped retries (2) = exactly 3 EventSource connections total
    expect(MockEventSource.instances).toHaveLength(3);
    expect(store.message).toContain("Lost live stream connection");
    store.destroy();
  });

  it("skips onEnd when settled run belongs to a different subject and guards duplicate stop() calls", async () => {
    const qc = createAppQueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const store = createRunStore(qc);
    const onEnd = vi.fn();
    store.setHooks({ subject: "ALGO", onEnd });

    fetchMock.mockResolvedValueOnce(jsonResponse(makeSnapshot({ id: 90, subject: "PDSS" }), 202));
    await store.start("PDSS", { kind: "write", topic_id: 7 });

    let resolveStop!: (res: Response) => void;
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveStop = resolve;
        }),
    );

    const p1 = store.stop();
    const p2 = store.stop();
    expect(store.stopping).toBe(true);
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
    const stopReq = fetchMock.mock.calls[1][0] as Request;
    expect(stopReq.method).toBe("POST");
    expect(new URL(stopReq.url).pathname).toBe("/api/runs/stop");
    expect(await stopReq.json()).toEqual({});

    resolveStop(
      jsonResponse({
        run: makeSnapshot({ id: 90, subject: "PDSS", status: "stopped", phase: null }),
      }),
    );
    await Promise.all([p1, p2]);

    expect(store.stopping).toBe(false);
    expect(onEnd).not.toHaveBeenCalled();
    expect(invalidateSpy).toHaveBeenCalledTimes(1);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["subject", "PDSS"] });
    store.destroy();
  });
});
