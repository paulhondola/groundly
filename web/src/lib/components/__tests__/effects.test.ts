import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { createRunStore } from "../../run.svelte";
import type { RunSnapshot } from "../../api/client";
import RunStrip from "../RunStrip.svelte";
import SourceItem from "../SourceItem.svelte";
import Orb from "../orb/Orb.svelte";
import { frame, geometry, orbModeForHosts } from "../orb/orb";
import { orbModeForRun, phaseLabelFor } from "../orb/orb-state";

class MockEventSource {
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

describe("orbModeForRun and phaseLabelFor", () => {
  it("maps run phases to orb modes and labels", () => {
    expect(orbModeForRun("starting")).toBe("listening");
    expect(orbModeForRun("reading")).toBe("reading");
    expect(orbModeForRun("writing")).toBe("writing");
    expect(orbModeForRun("thinking")).toBe("writing");
    expect(orbModeForRun(null)).toBe("writing");

    expect(phaseLabelFor("writing", "write", "Drafting questions")).toBe("Drafting questions");
    expect(phaseLabelFor("writing", "grade", null)).toBe("Recording verdicts");
    expect(phaseLabelFor("starting", "write", null)).toBe("Starting");
  });
});

describe("orb geometry", () => {
  it("drops the dot count below 28 px and keeps every dot inside the square", () => {
    expect(geometry("listening", 16).pts.length).toBeLessThan(geometry("listening", 64).pts.length);
    expect(geometry("mapping", 64).edges.length).toBeGreaterThan(0);
    for (const mode of ["idle", "listening", "reading", "mapping", "writing", "done"] as const) {
      const { dots } = frame(geometry(mode, 48), mode, 48, 3.7);
      expect(dots.length).toBeGreaterThan(0);
      for (const d of dots) {
        expect(d.x).toBeGreaterThanOrEqual(0);
        expect(d.x).toBeLessThanOrEqual(48);
        expect(d.alpha).toBeGreaterThanOrEqual(0);
        expect(d.alpha).toBeLessThanOrEqual(1);
      }
    }
  });

  it("shows idle with no host, a working host over listening ones", () => {
    expect(orbModeForHosts([])).toBe("idle");
    expect(orbModeForHosts([{ orb: "listening" }])).toBe("listening");
    expect(orbModeForHosts([{ orb: "listening" }, { orb: "reading" }])).toBe("reading");
    expect(orbModeForHosts([{ orb: "reading" }, { orb: "writing" }])).toBe("writing");
  });
});

describe("Orb", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    container.remove();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders a static frame with role=img under prefers-reduced-motion without rAF loop", () => {
    const arc = vi.fn();
    const ctxStub = {
      clearRect: vi.fn(),
      setTransform: vi.fn(),
      beginPath: vi.fn(),
      arc,
      fill: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(),
      fillStyle: "",
      strokeStyle: "",
      lineWidth: 1,
      globalAlpha: 1,
    } as unknown as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctxStub);
    const rafSpy = vi.spyOn(window, "requestAnimationFrame");
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((query: string) => ({
        matches: query.includes("prefers-reduced-motion"),
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );

    const instance = mount(Orb, {
      target: container,
      props: { mode: "writing", size: 32, label: "Writing" },
    });
    flushSync();

    const canvas = container.querySelector("canvas");
    expect(canvas).not.toBeNull();
    expect(canvas?.getAttribute("role")).toBe("img");
    expect(canvas?.getAttribute("aria-label")).toBe("Writing");
    expect(rafSpy).not.toHaveBeenCalled();
    expect(arc).toHaveBeenCalled();

    void unmount(instance);
  });

  it("is hidden from assistive tech when it carries no label", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const instance = mount(Orb, { target: container, props: { mode: "idle" } });
    flushSync();
    const canvas = container.querySelector("canvas");
    expect(canvas?.getAttribute("aria-hidden")).toBe("true");
    expect(canvas?.hasAttribute("role")).toBe(false);
    void unmount(instance);
  });
});

describe("RunStrip text-only safety and status output", () => {
  const originalFetch = globalThis.fetch;
  const originalEventSource = globalThis.EventSource;
  let container: HTMLDivElement;

  beforeEach(() => {
    MockEventSource.instances = [];
    container = document.createElement("div");
    document.body.appendChild(container);
    globalThis.EventSource = MockEventSource as unknown as typeof EventSource;
  });

  afterEach(() => {
    container.remove();
    globalThis.fetch = originalFetch;
    globalThis.EventSource = originalEventSource;
    vi.restoreAllMocks();
  });

  it("renders thought, last 6 steps, and summary strictly as text nodes with role=status", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const store = createRunStore();
    const xssThought = '<script>alert("xss")</script> Thinking';
    const xssSummary = "<img src=x onerror=alert(1)> Stored 4.";
    const steps = Array.from({ length: 8 }, (_, i) => `Step ${i + 1} <script>bad()</script>`);

    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(makeSnapshot({ id: 77, subject: "PDSS" }), 202),
      ) as unknown as typeof fetch;

    const instance = mount(RunStrip, {
      target: container,
      props: { store },
    });
    flushSync();

    await store.start("PDSS", { kind: "write", topic_id: 7 });
    const es = MockEventSource.instances[0];
    es.emit("delta", { id: 77, channel: "thinking", text: xssThought, reset: true });
    for (const s of steps) {
      es.emit("step", { id: 77, step: s, refresh: false });
    }
    flushSync();

    expect(container.querySelectorAll("script")).toHaveLength(0);
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(container.textContent).toContain(xssThought);
    const renderedSteps = container.querySelectorAll("ol.steps li");
    expect(renderedSteps).toHaveLength(6);
    expect(renderedSteps[0].textContent).toBe(steps[2]);
    expect(renderedSteps[5].textContent).toBe(steps[7]);

    es.emit(
      "done",
      makeSnapshot({
        id: 77,
        subject: "PDSS",
        status: "done",
        phase: null,
        steps,
        summary: xssSummary,
      }),
    );
    flushSync();

    expect(container.querySelectorAll("script")).toHaveLength(0);
    expect(container.querySelectorAll("img")).toHaveLength(0);
    const outcome = container.querySelector("p.outcome");
    expect(outcome?.getAttribute("role")).toBe("status");
    expect(outcome?.textContent).toBe(xssSummary);

    store.destroy();
    void unmount(instance);
  });

  it("hides Dismiss while starting and disables Stop while stopping", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const store = createRunStore();

    let resolveStart!: (res: Response) => void;
    let resolveStop!: (res: Response) => void;
    globalThis.fetch = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            resolveStart = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            resolveStop = resolve;
          }),
      ) as unknown as typeof fetch;

    const instance = mount(RunStrip, {
      target: container,
      props: { store },
    });
    flushSync();

    const startPromise = store.start("PDSS", { kind: "write", topic_id: 7 });
    flushSync();

    expect(container.textContent).toContain("Starting your agent");
    expect(container.querySelectorAll("button")).toHaveLength(0);

    await vi.waitFor(() => {
      expect(resolveStart).toBeTypeOf("function");
    });
    resolveStart(jsonResponse(makeSnapshot({ id: 78, subject: "PDSS" }), 202));
    await startPromise;
    flushSync();

    const stopBtn = container.querySelector("button");
    expect(stopBtn?.textContent?.trim()).toBe("Stop");
    expect(stopBtn?.disabled).toBe(false);

    const stopPromise = store.stop();
    flushSync();
    expect(stopBtn?.disabled).toBe(true);
    expect(stopBtn?.textContent?.trim()).toBe("Stopping…");

    await vi.waitFor(() => {
      expect(resolveStop).toBeTypeOf("function");
    });
    resolveStop(
      jsonResponse({
        run: makeSnapshot({ id: 78, subject: "PDSS", status: "stopped", phase: null }),
      }),
    );
    await stopPromise;
    flushSync();

    expect(container.querySelector("button")?.textContent?.trim()).toBe("Dismiss");

    store.destroy();
    void unmount(instance);
  });
});

describe("SourceItem", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    container.remove();
  });

  it("links PDFs only when src.pdf === true and renders non-PDFs in focusable pre", () => {
    const pdfInst = mount(SourceItem, {
      target: container,
      props: {
        subject: "PDSS",
        src: {
          chunk_id: 1,
          material_id: 3,
          filename: "slides.pdf",
          page: 4,
          heading_path: null,
          text: "Slide body",
          pdf: true,
        },
      },
    });
    flushSync();

    const anchor = container.querySelector("a");
    expect(anchor?.getAttribute("href")).toBe("/s/PDSS/materials/3#page=4");
    expect(anchor?.getAttribute("target")).toBe("_blank");
    expect(anchor?.getAttribute("rel")).toBe("noopener");
    void unmount(pdfInst);

    container.replaceChildren();

    const textInst = mount(SourceItem, {
      target: container,
      props: {
        subject: "PDSS",
        src: {
          chunk_id: 2,
          material_id: 3,
          filename: "notes.pdf",
          page: 4,
          heading_path: null,
          text: "Quoted chunk",
          pdf: false,
        },
      },
    });
    flushSync();

    expect(container.querySelector("a")).toBeNull();
    const pre = container.querySelector("pre");
    expect(pre?.getAttribute("tabindex")).toBe("0");
    expect(pre?.getAttribute("aria-label")).toBe("Text from notes.pdf · p.4");
    expect(pre?.textContent).toBe("Quoted chunk");
    void unmount(textInst);
  });
});
