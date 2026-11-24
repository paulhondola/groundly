import {
  ApiError,
  RUNS_API,
  RUNS_STOP_API,
  RUNS_STREAM_API,
  getJSON,
  postJSON,
  startRunURL,
} from "./api";
import type {
  RunSnapshot,
  RunsInfo,
  SSEDeltaEvent,
  SSEPhaseEvent,
  SSEStepEvent,
  StartRunBody,
  StopRunResponse,
} from "./types";

export const MAX_STREAM_CHARS = 2000;
export const MAX_STEPS = 50;

export interface RunHooks {
  subject: string | null;
  onRefresh?: (() => void | Promise<void>) | null;
  onEnd?: (() => void | Promise<void>) | null;
}

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

export function createRunStore() {
  let available = $state(false);
  let reason = $state<string | null>(null);
  let target = $state(0);
  let run = $state<RunSnapshot | null>(null);
  let watched = $state<number | null>(null);
  let starting = $state(false);
  let message = $state<string | null>(null);
  let dismissed = $state(false);

  const busy = $derived(starting || Boolean(run && run.status === "running"));
  const watchedRun = $derived(run && run.id === watched ? run : null);

  let hooks: RunHooks = { subject: null, onRefresh: null, onEnd: null };
  let source: EventSource | null = null;
  let endedRunId: number | null = null;

  function closeStream(): void {
    if (source) {
      source.close();
      source = null;
    }
  }

  async function notifyEnd(runId: number | null): Promise<void> {
    if (runId !== null && endedRunId === runId) {
      return;
    }
    if (runId !== null) {
      endedRunId = runId;
    }
    if (hooks.onEnd) {
      await hooks.onEnd();
    }
  }

  function applySettled(settled: RunSnapshot): void {
    run = settled;
    if (watched === null || watched === settled.id) {
      watched = settled.id;
      message = outcomeText(settled);
    }
    closeStream();
    void notifyEnd(settled.id);
  }

  function openStream(): void {
    closeStream();
    if (typeof EventSource === "undefined") {
      return;
    }
    const es = new EventSource(RUNS_STREAM_API);
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
          void notifyEnd(watched);
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
      run = {
        ...run,
        steps: [...run.steps, data.step].slice(-MAX_STEPS),
      };
      if (
        data.refresh &&
        hooks.onRefresh &&
        hooks.subject !== null &&
        run.subject === hooks.subject
      ) {
        void hooks.onRefresh();
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
      applySettled(snap);
    });

    es.onerror = () => {
      if (source !== es) {
        return;
      }
      closeStream();
      void reconcileAfterStreamError();
    };
  }

  async function reconcileAfterStreamError(): Promise<void> {
    let info: RunsInfo;
    try {
      info = await getJSON<RunsInfo>(RUNS_API);
    } catch {
      run = null;
      message = "Lost the page server — reload the page when it is back.";
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
      openStream();
      return;
    }
    if (current && (watched === null || current.id === watched)) {
      applySettled(current);
      return;
    }
    run = current;
    if (watched !== null) {
      message = "Lost track of the run — the page server restarted.";
      await notifyEnd(watched);
    }
  }

  async function attach(): Promise<RunsInfo | null> {
    let info: RunsInfo;
    try {
      info = await getJSON<RunsInfo>(RUNS_API);
    } catch {
      return null;
    }
    available = info.available;
    reason = info.reason;
    target = info.target;
    run = info.run;
    if (info.run && info.run.status === "running") {
      dismissed = false;
      if (watched !== info.run.id || !source) {
        watched = info.run.id;
        endedRunId = null;
        openStream();
      }
    }
    return info;
  }

  async function start(subject: string, body: StartRunBody): Promise<void> {
    starting = true;
    watched = null;
    endedRunId = null;
    message = null;
    dismissed = false;
    closeStream();
    try {
      const started = await postJSON<RunSnapshot>(startRunURL(subject), body);
      run = started;
      watched = started.id;
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
    try {
      const res = await postJSON<StopRunResponse>(RUNS_STOP_API, {});
      if (res.run && res.run.status !== "running") {
        applySettled(res.run);
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : "unknown error";
      message = "Could not stop: " + detail;
    }
  }

  function dismiss(): void {
    dismissed = true;
    message = null;
  }

  function setHooks(next: RunHooks): void {
    hooks = next;
  }

  function destroy(): void {
    closeStream();
    hooks = { subject: null, onRefresh: null, onEnd: null };
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
    setHooks,
    destroy,
  };
}

export const runStore = createRunStore();
