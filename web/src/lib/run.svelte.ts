import type { QueryClient } from "@tanstack/svelte-query";
import {
  ApiError,
  type RunSnapshot,
  type RunsInfo,
  type StartRunBody,
  apiClient,
  unwrap,
} from "./api/client";
import { queryClient, queryKeys } from "./api/queries";
import {
  type EventSourceLike,
  type SSEDeltaEvent,
  type SSEPhaseEvent,
  type SSEStepEvent,
  createEventStream,
} from "./api/sse";

export const MAX_STREAM_CHARS = 2000;
export const MAX_STEPS = 50;
export const MAX_STREAM_RETRIES = 2;

export function outcomeText(run: RunSnapshot): string {
  if (run.status === "done") {
    return run.summary || "Done.";
  }
  if (run.status === "stopped") {
    return "Stopped. What was already stored stays.";
  }
  return "Failed: " + (run.error ? run.error.detail : "the run ended without a result");
}

function parseEventJSON<T>(raw: string): T | null {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function createRunStore(qc: QueryClient = queryClient) {
  let available = $state(false);
  let reason = $state<string | null>(null);
  let target = $state(0);
  let run = $state<RunSnapshot | null>(null);
  let watched = $state<number | null>(null);
  let starting = $state(false);
  let stopping = $state(false);
  let message = $state<string | null>(null);
  let dismissed = $state(false);

  const busy = $derived(starting || Boolean(run && run.status === "running"));
  const watchedRun = $derived(run && run.id === watched ? run : null);

  let source: EventSourceLike | null = null;
  let endedRunId: number | null = null;
  let streamErrors = 0;
  let stateSeq = 0;

  function closeStream(): void {
    if (source) {
      source.close();
      source = null;
    }
  }

  function notifyEnd(runId: number | null, runSubject: string | null = null): void {
    if (runId !== null && endedRunId === runId) {
      return;
    }
    if (runId !== null) {
      endedRunId = runId;
    }
    if (runSubject !== null) {
      void qc.invalidateQueries({ queryKey: queryKeys.subject(runSubject) });
    } else {
      void qc.invalidateQueries({ queryKey: queryKeys.allSubjects() });
    }
  }

  function applySettled(settled: RunSnapshot): void {
    run = settled;
    if (watched === null || watched === settled.id) {
      watched = settled.id;
      message = outcomeText(settled);
    }
    closeStream();
    notifyEnd(settled.id, settled.subject);
  }

  function openStream(): void {
    closeStream();
    const es = createEventStream("/api/runs/stream");
    if (!es) {
      return;
    }
    source = es;

    es.addEventListener("snapshot", (ev: MessageEvent<string>) => {
      if (source !== es) {
        return;
      }
      const snap = parseEventJSON<RunSnapshot | null>(ev.data);
      if (snap === null) {
        if (watched !== null) {
          run = null;
          closeStream();
          message = "Lost track of the run — the page server restarted.";
          notifyEnd(watched);
          return;
        }
        run = null;
        closeStream();
        return;
      }
      if (snap.status !== "running") {
        applySettled(snap);
        return;
      }
      run = snap;
      if (watched === null) {
        watched = snap.id;
      }
    });

    es.addEventListener("phase", (ev: MessageEvent<string>) => {
      if (source !== es) {
        return;
      }
      const data = parseEventJSON<SSEPhaseEvent>(ev.data);
      if (!data || !run || run.id !== data.id) {
        return;
      }
      streamErrors = 0;
      run = {
        ...run,
        phase: data.phase,
        active_tool: data.active_tool,
      };
    });

    es.addEventListener("delta", (ev: MessageEvent<string>) => {
      if (source !== es) {
        return;
      }
      const data = parseEventJSON<SSEDeltaEvent>(ev.data);
      if (!data || !run || run.id !== data.id) {
        return;
      }
      streamErrors = 0;
      const nextThought = data.reset
        ? data.text.slice(-MAX_STREAM_CHARS)
        : (run.thought + data.text).slice(-MAX_STREAM_CHARS);
      run = {
        ...run,
        thought: nextThought,
        thought_channel: data.channel,
      };
    });

    es.addEventListener("step", (ev: MessageEvent<string>) => {
      if (source !== es) {
        return;
      }
      const data = parseEventJSON<SSEStepEvent>(ev.data);
      if (!data || !run || run.id !== data.id) {
        return;
      }
      streamErrors = 0;
      run = {
        ...run,
        steps: [...run.steps, data.step].slice(-MAX_STEPS),
      };
      if (data.refresh) {
        void qc.invalidateQueries({ queryKey: queryKeys.subject(run.subject) });
      }
    });

    es.addEventListener("done", (ev: MessageEvent<string>) => {
      if (source !== es) {
        return;
      }
      const snap = parseEventJSON<RunSnapshot>(ev.data);
      if (!snap) {
        closeStream();
        return;
      }
      streamErrors = 0;
      applySettled(snap);
    });

    es.onerror = () => {
      if (source !== es) {
        return;
      }
      streamErrors += 1;
      closeStream();
      void reconcileAfterStreamError();
    };
  }

  async function reconcileAfterStreamError(): Promise<void> {
    const seq = stateSeq;
    let info: RunsInfo;
    try {
      info = unwrap(await apiClient.GET("/api/runs"));
    } catch {
      if (seq !== stateSeq) {
        return;
      }
      run = null;
      message = "Lost the page server — reload the page when it is back.";
      return;
    }
    if (seq !== stateSeq) {
      return;
    }
    available = info.available;
    reason = info.reason;
    target = info.target;
    const current = info.run;
    if (current && current.status === "running") {
      run = current;
      watched = current.id;
      dismissed = false;
      if (streamErrors <= MAX_STREAM_RETRIES) {
        openStream();
      } else {
        message = "Lost live stream connection — reload the page to reconnect.";
      }
      return;
    }
    if (current && (watched === null || current.id === watched)) {
      applySettled(current);
      return;
    }
    run = current;
    if (watched !== null) {
      message = "Lost track of the run — the page server restarted.";
      notifyEnd(watched);
    }
  }

  async function attach(): Promise<RunsInfo | null> {
    const seq = ++stateSeq;
    let info: RunsInfo;
    try {
      info = unwrap(await apiClient.GET("/api/runs"));
    } catch {
      return null;
    }
    if (seq !== stateSeq) {
      return info;
    }
    available = info.available;
    reason = info.reason;
    target = info.target;
    const incoming = info.run;
    if (incoming && incoming.id === endedRunId) {
      return info;
    }
    if (
      incoming &&
      incoming.status === "running" &&
      source !== null &&
      watched === incoming.id &&
      run?.id === incoming.id &&
      run.status === "running"
    ) {
      return info;
    }
    run = incoming;
    if (incoming && incoming.status === "running") {
      dismissed = false;
      if (watched !== incoming.id || !source) {
        watched = incoming.id;
        endedRunId = null;
        streamErrors = 0;
        openStream();
      }
    }
    return info;
  }

  async function start(subject: string, body: StartRunBody): Promise<void> {
    stateSeq += 1;
    starting = true;
    watched = null;
    endedRunId = null;
    streamErrors = 0;
    message = null;
    dismissed = false;
    closeStream();
    try {
      const started = unwrap(
        await apiClient.POST("/api/subjects/{subject}/runs", {
          params: { path: { subject } },
          body,
        }),
      );
      run = started;
      watched = started.id;
      dismissed = false;
      if (started.status === "running") {
        openStream();
      } else {
        applySettled(started);
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : "unknown error";
      message = "Could not start: " + detail;
      if (err instanceof ApiError && err.status === 409) {
        await attach();
      }
    } finally {
      starting = false;
    }
  }

  async function stop(): Promise<void> {
    if (stopping) {
      return;
    }
    stopping = true;
    try {
      const res = unwrap(await apiClient.POST("/api/runs/stop", { body: {} }));
      if (res.run && res.run.status !== "running") {
        applySettled(res.run);
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : "unknown error";
      message = "Could not stop: " + detail;
    } finally {
      stopping = false;
    }
  }

  function dismiss(): void {
    dismissed = true;
    message = null;
  }

  function disconnect(): void {
    stateSeq += 1;
    closeStream();
  }

  function destroy(): void {
    closeStream();
  }

  return {
    get available() {
      return available;
    },
    get reason() {
      return reason;
    },
    get target() {
      return target;
    },
    get run() {
      return run;
    },
    get watched() {
      return watched;
    },
    get watchedRun() {
      return watchedRun;
    },
    get starting() {
      return starting;
    },
    get stopping() {
      return stopping;
    },
    get message() {
      return message;
    },
    get busy() {
      return busy;
    },
    get dismissed() {
      return dismissed;
    },
    attach,
    start,
    stop,
    dismiss,
    disconnect,
    destroy,
  };
}

export const runStore = createRunStore(queryClient);
