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

import type { DrawTestResponse, TreeResponse } from "../../api/client";
import { hostLine } from "../../activity.svelte";
import { authStore } from "../../auth.svelte";
import ShareCard from "../../components/ShareCard.svelte";
import { ago, formatBytes } from "../../time";
import ConnectionsView from "../ConnectionsView.svelte";
import TestView from "../TestView.svelte";
import QueryHarness from "./QueryHarness.svelte";
import { loadTest, storageKey } from "../test-session";

function pathOf(input: RequestInfo | URL): string {
  return new URL(input instanceof Request ? input.url : String(input), location.origin).pathname;
}

function methodOf(input: RequestInfo | URL, init?: RequestInit): string {
  return input instanceof Request ? input.method : (init?.method ?? "GET");
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function text(): string {
  return container.textContent?.replace(/\s+/g, " ") ?? "";
}

function client(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

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

describe("time helpers", () => {
  it("formats ages and sizes", () => {
    const now = Date.parse("2026-11-26T12:00:00Z");
    expect(ago("2026-11-26T11:59:40Z", now)).toBe("just now");
    expect(ago("2026-11-26T11:55:00Z", now)).toBe("5 min ago");
    expect(ago("2026-11-26T09:00:00Z", now)).toBe("3 h ago");
    expect(ago("2026-11-25T09:00:00Z", now)).toBe("yesterday");
    expect(ago("2026-11-21T12:00:00Z", now)).toBe("5 d ago");
    expect(ago("not a date", now)).toBe("not a date");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(48_200_000)).toBe("48 MB");
  });

  it("says what the busiest host is doing", () => {
    const host = {
      client: "Claude Code",
      transport: "stdio" as const,
      started_at: "2026-11-26T10:00:00Z",
      last_call_at: null,
      subject: "apd",
      last_done_at: null,
    };
    expect(hostLine([])).toBe("No host connected");
    expect(hostLine([{ ...host, orb: "listening" }])).toBe("Claude Code connected");
    expect(
      hostLine([
        { ...host, orb: "listening" },
        { ...host, client: null, orb: "reading" },
      ]),
    ).toBe("MCP host · reading apd");
  });
});

describe("TestView check", () => {
  const tree: TreeResponse = { title: "Parallel Systems", tree: [] };
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
      {
        id: 102,
        topic_id: 7,
        kind: "single",
        stem: "Second question",
        code: null,
        options: [{ id: 3, text: "Yes" }],
      },
    ],
  };

  it("locks a checked answer, shows the key and the cited passage, and keeps the lock on reload", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0.99); // shuffled() keeps the order
    const checks: unknown[] = [];
    globalThis.fetch = vi
      .fn()
      .mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = pathOf(input);
        if (path === "/api/subjects/PDSS/tree") return json(tree);
        if (path === "/api/subjects/PDSS/test") return json(drawn);
        if (path === "/api/subjects/PDSS/questions/101/check" && methodOf(input, init) === "POST") {
          checks.push(await (input as Request).json());
          return json({
            correct: false,
            key: [1],
            sources: [
              {
                chunk_id: 9,
                material_id: 3,
                filename: "curs.pdf",
                page: 12,
                heading_path: null,
                text: "<img src=x onerror=alert(1)> a mutex excludes",
                pdf: false,
              },
            ],
          });
        }
        return json({});
      }) as unknown as typeof fetch;

    const props = { subject: "PDSS", topicId: 7, n: 5 };
    const first = mount(QueryHarness, {
      target: container,
      props: { client: client(), component: TestView, props },
    });
    await vi.waitFor(() =>
      expect(container.textContent).toContain("What prevents race conditions?"),
    );

    const radios = () => container.querySelectorAll<HTMLInputElement>('input[type="radio"]');
    const checkButton = () =>
      Array.from(container.querySelectorAll("button")).find(
        (b) => b.textContent?.trim() === "Check answer",
      );
    expect(checkButton()?.disabled).toBe(true);

    radios()[1].click(); // "Sleep", the wrong one
    flushSync();
    expect(checkButton()?.disabled).toBe(false);
    checkButton()?.click();

    await vi.waitFor(() => expect(text()).toContain("Not quite. The answer is A."));
    expect(checks).toEqual([{ selected: [2] }]);
    expect(checkButton()).toBeUndefined();
    expect(container.querySelector("pre")?.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent?.toLowerCase()).not.toContain("verified");

    // Locked: picking another option changes nothing, by click or by keyboard shortcut.
    radios()[0].click();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "1" }));
    flushSync();
    const key = storageKey("PDSS", 7);
    expect(loadTest(key)?.answers[101]).toEqual([2]);
    expect(loadTest(key)?.checked?.[101]?.key).toEqual([1]);
    void unmount(first);

    container.replaceChildren();
    const second = mount(QueryHarness, {
      target: container,
      props: { client: client(), component: TestView, props },
    });
    await vi.waitFor(() => expect(text()).toContain("Not quite. The answer is A."));
    expect(container.querySelector<HTMLFieldSetElement>("fieldset")?.disabled).toBe(true);
    void unmount(second);
  });
});

describe("ShareCard", () => {
  it("states what a bundle holds and never holds, previews the size, and shows a 409's message", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = pathOf(input);
      if (path === "/api/subjects") {
        return json([
          { subject: "apd", title: "Parallel", has_tree: true, error: null },
          { subject: "pm", title: "", has_tree: false, error: null },
        ]);
      }
      if (path === "/api/subjects/pm/export/info") {
        return json({
          filename: "pm.groundly",
          store_bytes: 2_000_000,
          materials_bytes: 3_000_000,
        });
      }
      if (path === "/api/subjects/pm/export") {
        return json(
          { error: "manifest_invalid", detail: "manifest.json is corrupt or unreadable: x" },
          409,
        );
      }
      return json({});
    }) as unknown as typeof fetch;

    const instance = mount(QueryHarness, {
      target: container,
      props: { client: client(), component: ShareCard, props: { initial: "pm" } },
    });
    await vi.waitFor(() => expect(container.textContent).toContain("5.0 MB before compression"));

    const statement =
      container.querySelector(".export-statement")?.textContent?.replace(/\s+/g, " ") ?? "";
    expect(statement).toContain(
      "everything indexed in this subject, including its topic map and question bank",
    );
    expect(statement).toContain("store.db, exported whole");
    expect(statement).toContain("materials/");
    expect(statement).toContain("Never exported, from this computer or a paired device");
    for (const file of ["progress.db", "remote.db", "activity.db"]) {
      expect(statement).toContain(file);
    }

    container.querySelector<HTMLInputElement>('input[type="checkbox"]')?.click();
    flushSync();
    expect(container.textContent).toContain("2.0 MB before compression");
    expect(container.textContent).toContain("materials/ left out");

    Array.from(container.querySelectorAll("button"))
      .find((b) => b.textContent?.trim() === "Export file")
      ?.click();
    await vi.waitFor(() =>
      expect(container.querySelector('[role="alert"]')?.textContent).toContain(
        "manifest.json is corrupt",
      ),
    );
    void unmount(instance);
  });
});

describe("ConnectionsView", () => {
  it("renders activity strings as text and falls back quietly when activity fails", async () => {
    const xss = "<img src=x onerror=alert(1)>";
    let activityStatus = 200;
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = pathOf(input);
      if (path === "/api/activity") {
        if (activityStatus !== 200) return json({ error: "internal" }, activityStatus);
        return json({
          hosts: [
            {
              client: xss,
              transport: "stdio",
              started_at: "2026-11-26T10:00:00Z",
              last_call_at: null,
              subject: "apd",
              orb: "reading",
              last_done_at: null,
            },
          ],
          indexing: [
            {
              subject: "pm",
              done: 2,
              total: 8,
              current_file: xss + ".pdf",
              started_at: "2026-11-26T10:00:00Z",
            },
          ],
          recent: [
            { at: "2026-11-26T10:00:00Z", subject: "apd", text: "Wrote 3 questions · " + xss },
          ],
        });
      }
      if (path === "/api/subjects") return json([]);
      if (path === "/api/remote")
        return json({ active: false, pairing_url: null, qr_matrix: null, devices: [] });
      return json({});
    }) as unknown as typeof fetch;

    const qc = client();
    const instance = mount(QueryHarness, {
      target: container,
      props: { client: qc, component: ConnectionsView },
    });
    await vi.waitFor(() => expect(container.textContent).toContain("Indexing pm"));
    expect(container.textContent).toContain(xss + ".pdf");
    expect(container.textContent).toContain("Wrote 3 questions · " + xss);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")).toBe(
      "2",
    );

    activityStatus = 500;
    await qc.refetchQueries({ queryKey: ["activity"] });
    await vi.waitFor(() =>
      expect(container.textContent).toContain("Activity is not available right now."),
    );
    void unmount(instance);
  });

  it("hides the devices card from a paired Tailscale device and never asks for /api/remote", async () => {
    const paths: string[] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = pathOf(input);
      paths.push(path);
      if (path === "/api/auth") {
        return json({
          access: "tailscale",
          paired: true,
          tailscale_enabled: true,
          paired_devices: 1,
        });
      }
      if (path === "/api/activity") return json({ hosts: [], indexing: [], recent: [] });
      if (path === "/api/subjects") return json([]);
      return json({});
    }) as unknown as typeof fetch;
    authStore.resetForTests();
    await authStore.bootstrap();
    expect(authStore.access).toBe("tailscale");

    const instance = mount(QueryHarness, {
      target: container,
      props: { client: client(), component: ConnectionsView },
    });
    await vi.waitFor(() => expect(container.textContent).toContain("No host connected"));
    expect(container.textContent).not.toContain("Study on another device");
    expect(paths).not.toContain("/api/remote");
    void unmount(instance);
    authStore.resetForTests();
  });
});
