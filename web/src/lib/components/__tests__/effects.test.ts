import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { createRunStore } from "../../run.svelte";
import type { RunSnapshot } from "../../types";
import RunStrip from "../RunStrip.svelte";
import SourceItem from "../SourceItem.svelte";
import LiquidMetalButton from "../effects/LiquidMetalButton.svelte";
import ThinkingOrb from "../effects/ThinkingOrb.svelte";
import { orbStateFor, phaseLabelFor } from "../effects/orb-state";

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

describe("orbStateFor and phaseLabelFor", () => {
  it("maps run phases and kinds to expected orb states and labels", () => {
    expect(orbStateFor("starting", "write")).toBe("connecting");
    expect(orbStateFor("reading", "write")).toBe("searching");
    expect(orbStateFor("writing", "write")).toBe("composing");
    expect(orbStateFor("writing", "grade")).toBe("solving");
    expect(orbStateFor("thinking", "write")).toBe("working");
    expect(orbStateFor(null, "write")).toBe("working");

    expect(phaseLabelFor("writing", "write", "Drafting questions")).toBe("Drafting questions");
    expect(phaseLabelFor("writing", "grade", null)).toBe("Recording verdicts");
    expect(phaseLabelFor("starting", "write", null)).toBe("Starting");
  });
});

describe("LiquidMetalButton fallback", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    container.remove();
    vi.restoreAllMocks();
  });

  it("renders accessible fallback button when WebGL context is unavailable", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const onClick = vi.fn();
    const instance = mount(LiquidMetalButton, {
      target: container,
      props: {
        label: "Write questions",
        onclick: onClick,
      },
    });
    flushSync();

    const btn = container.querySelector("button");
    expect(btn).not.toBeNull();
    expect(btn?.getAttribute("type")).toBe("button");
    expect(btn?.textContent?.trim()).toBe("Write questions");
    btn?.click();
    expect(onClick).toHaveBeenCalledTimes(1);

    void unmount(instance);
  });

  it("skips WebGL shader mount when prefers-reduced-motion matches", () => {
    const getContextSpy = vi.spyOn(HTMLCanvasElement.prototype, "getContext");
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((query: string) => ({
        matches: query.includes("prefers-reduced-motion"),
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
    const onClick = vi.fn();
    const instance = mount(LiquidMetalButton, {
      target: container,
      props: {
        label: "Grade now",
        onclick: onClick,
      },
    });
    flushSync();

    expect(getContextSpy).not.toHaveBeenCalled();
    expect(container.querySelector("button")?.textContent?.trim()).toBe("Grade now");

    void unmount(instance);
    vi.unstubAllGlobals();
  });
});

describe("ThinkingOrb", () => {
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
    const ctxStub = {
      clearRect: vi.fn(),
      setTransform: vi.fn(),
      beginPath: vi.fn(),
      arc: vi.fn(),
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

    const instance = mount(ThinkingOrb, {
      target: container,
      props: {
        phase: "thinking",
        kind: "write",
        size: 32,
      },
    });
    flushSync();

    const canvas = container.querySelector("canvas");
    expect(canvas).not.toBeNull();
    expect(canvas?.getAttribute("role")).toBe("img");
    expect(canvas?.getAttribute("aria-label")).toBeTruthy();
    expect(rafSpy).not.toHaveBeenCalled();

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
