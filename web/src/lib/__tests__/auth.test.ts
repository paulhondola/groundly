import { QueryClient } from "@tanstack/svelte-query";
import { mount, unmount } from "svelte";
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

import App from "../../App.svelte";
import { apiClient, setOnUnpaired } from "../api/client";
import { queryClient, queryKeys } from "../api/queries";
import { type EventSourceLike, setEventStreamFactory } from "../api/sse";
import { authStore, createAuthStore } from "../auth.svelte";
import { navigate, route } from "../router";
import { runStore } from "../run.svelte";

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

describe("authStore", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    authStore.resetForTests();
    queryClient.clear();
    MockEventSource.instances = [];
    vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    window.history.replaceState(null, "", "/");
  });

  afterEach(() => {
    authStore.resetForTests();
    queryClient.clear();
    setEventStreamFactory(null);
    setOnUnpaired(() => {
      if (authStore.ready) {
        authStore.handleUnpaired();
      }
    });
    window.history.replaceState(null, "", "/");
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("bootstrap() on loopback resolves access='loopback', paired=true, tailscaleEnabled, and pairedDevices", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const store = createAuthStore(qc);

    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      if (requestPathname(input) === "/api/auth") {
        return Promise.resolve(
          jsonResponse({
            access: "loopback",
            paired: true,
            tailscale_enabled: true,
            paired_devices: 2,
          }),
        );
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    await store.bootstrap();

    expect(store.ready).toBe(true);
    expect(store.access).toBe("loopback");
    expect(store.paired).toBe(true);
    expect(store.tailscaleEnabled).toBe(true);
    expect(store.pairedDevices).toBe(2);
    expect(store.error).toBeNull();
  });

  it("bootstrap() on unpaired Tailscale resolves access='tailscale', paired=false without calling /api/subjects", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      if (requestPathname(input) === "/api/auth") {
        return Promise.resolve(
          jsonResponse({
            access: "tailscale",
            paired: false,
            tailscale_enabled: true,
            paired_devices: 0,
          }),
        );
      }
      if (requestPathname(input) === "/api/subjects") {
        throw new Error("Should not call /api/subjects when unpaired");
      }
      return Promise.resolve(jsonResponse({}));
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const container = document.createElement("div");
    document.body.appendChild(container);
    const instance = mount(App, { target: container });

    try {
      await vi.waitFor(() => {
        expect(authStore.ready).toBe(true);
      });
      expect(authStore.access).toBe("tailscale");
      expect(authStore.paired).toBe(false);
      const paths = fetchMock.mock.calls.map(([input]) => requestPathname(input as RequestInfo));
      expect(paths).toEqual(["/api/auth"]);
      expect(container.textContent).toContain("Pair this device");
    } finally {
      await unmount(instance);
      container.remove();
    }
  });

  it("bootstrap() scrubs /pair#tok123 immediately via history.replaceState and exchanges token via POST /api/pair", async () => {
    const callOrder: string[] = [];
    const replaceSpy = vi
      .spyOn(window.history, "replaceState")
      .mockImplementation((state, title, url) => {
        callOrder.push(`replaceState:${String(url)}`);
        History.prototype.replaceState.call(window.history, state, title, url);
      });

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const store = createAuthStore(qc);

    let pairBody: unknown = null;
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = requestPathname(input);
      callOrder.push(`fetch:${path}`);
      if (path === "/api/auth") {
        return jsonResponse({
          access: "tailscale",
          paired: false,
          tailscale_enabled: true,
          paired_devices: 0,
        });
      }
      if (path === "/api/pair" && input instanceof Request) {
        pairBody = await input.json();
        return jsonResponse({ paired: true });
      }
      return jsonResponse({});
    }) as unknown as typeof fetch;

    History.prototype.replaceState.call(window.history, null, "", "/pair#tok123");
    replaceSpy.mockClear();

    await store.bootstrap();

    expect(callOrder[0]).toBe("replaceState:/");
    expect(callOrder).toEqual(["replaceState:/", "fetch:/api/auth", "fetch:/api/pair"]);
    expect(window.location.pathname).toBe("/");
    expect(window.location.hash).toBe("");
    expect(pairBody).toEqual({ token: "tok123" });
    expect(store.ready).toBe(true);
    expect(store.paired).toBe(true);
    expect(store.error).toBeNull();
  });

  it("bootstrap() also exchanges /pair#<new> when re-scanning from an already-paired session", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const store = createAuthStore(qc);

    let pairBody: unknown = null;
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = requestPathname(input);
      if (path === "/api/auth") {
        return jsonResponse({
          access: "tailscale",
          paired: true,
          tailscale_enabled: true,
          paired_devices: 0,
        });
      }
      if (path === "/api/pair" && input instanceof Request) {
        pairBody = await input.json();
        return jsonResponse({ paired: true });
      }
      return jsonResponse({});
    }) as unknown as typeof fetch;

    window.history.replaceState(null, "", "/pair#fresh-token-999");

    await store.bootstrap();

    expect(window.location.pathname).toBe("/");
    expect(window.location.hash).toBe("");
    expect(pairBody).toEqual({ token: "fresh-token-999" });
    expect(store.ready).toBe(true);
    expect(store.paired).toBe(true);
  });

  it.each([false, true])("preserves paired=%s when a QR has already been used", async (paired) => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const store = createAuthStore(qc);

    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = requestPathname(input);
      if (path === "/api/auth") {
        return Promise.resolve(
          jsonResponse({
            access: "tailscale",
            paired,
            tailscale_enabled: true,
            paired_devices: 0,
          }),
        );
      }
      if (path === "/api/pair") {
        return Promise.resolve(
          jsonResponse(
            {
              error: "invalid_pairing_token",
              detail: "pairing link is invalid or was already used — scan the current QR code",
            },
            401,
          ),
        );
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    window.history.replaceState(null, "", "/pair#burned-tok");

    await store.bootstrap();

    expect(store.ready).toBe(true);
    expect(store.access).toBe("tailscale");
    expect(store.paired).toBe(paired);
    expect(store.canRetry).toBe(false);
    expect(store.error).toBe(
      paired ? null : "pairing link is invalid or was already used — scan the current QR code",
    );
  });

  it("setOnUnpaired middleware invokes handleUnpaired on non-/api/pair 401, clears queryClient and closes SSE, without looping on /api/pair 401", async () => {
    setEventStreamFactory((url) => new MockEventSource(url));
    queryClient.setQueryData(queryKeys.subjects(), [
      { subject: "PDSS", title: "PDSS", has_tree: true },
    ]);

    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = requestPathname(input);
      if (path === "/api/auth") {
        return Promise.resolve(
          jsonResponse({
            access: "tailscale",
            paired: true,
            tailscale_enabled: true,
            paired_devices: 0,
          }),
        );
      }
      if (path === "/api/subjects/PDSS/runs") {
        return Promise.resolve(
          jsonResponse(
            {
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
              started_at: "2026-11-25T08:00:00+00:00",
            },
            202,
          ),
        );
      }
      if (path === "/api/pair") {
        return Promise.resolve(
          jsonResponse({ error: "invalid_pairing_token", detail: "bad token" }, 401),
        );
      }
      if (path === "/api/subjects") {
        return Promise.resolve(
          jsonResponse(
            { error: "unpaired", detail: "scan the pairing QR code on your computer" },
            401,
          ),
        );
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    await authStore.bootstrap();
    expect(authStore.paired).toBe(true);

    await runStore.start("PDSS", { kind: "write", topic_id: 7 });
    expect(MockEventSource.instances).toHaveLength(1);
    const es = MockEventSource.instances[0];
    expect(es.closed).toBe(false);

    const handleUnpairedSpy = vi.spyOn(authStore, "handleUnpaired");

    // 401 from /api/pair must NOT trigger handleUnpaired
    const pairResult = await apiClient.POST("/api/pair", { body: { token: "bad-token-123456" } });
    expect(pairResult.response.status).toBe(401);
    expect(handleUnpairedSpy).not.toHaveBeenCalled();
    expect(authStore.paired).toBe(true);
    expect(es.closed).toBe(false);

    // 401 from any other /api/* route triggers handleUnpaired, closes SSE, clears queryClient
    const subjectsResult = await apiClient.GET("/api/subjects");
    expect(subjectsResult.response.status).toBe(401);
    expect(handleUnpairedSpy).toHaveBeenCalledTimes(1);
    expect(authStore.paired).toBe(false);
    expect(authStore.access).toBe("tailscale");
    expect(authStore.error).toBe(
      "This device is no longer paired — scan the current QR code to reconnect.",
    );
    expect(es.closed).toBe(true);
    expect(queryClient.getQueryData(queryKeys.subjects())).toBeUndefined();
  });

  it("renders RemoteDialog on loopback when Tailscale is enabled or devices exist, and drops paired App to PairView on 401", async () => {
    let authPayload = {
      access: "loopback" as "loopback" | "tailscale",
      paired: true,
      tailscale_enabled: true,
      paired_devices: 0,
    };

    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = requestPathname(input);
      if (path === "/api/auth") {
        return Promise.resolve(jsonResponse(authPayload));
      }
      if (path === "/api/runs") {
        return Promise.resolve(
          jsonResponse({ available: false, reason: "no_host", target: 10, run: null }),
        );
      }
      if (path === "/api/subjects") {
        return Promise.resolve(jsonResponse([]));
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    const container = document.createElement("div");
    document.body.appendChild(container);
    const app1 = mount(App, { target: container });

    try {
      await vi.waitFor(() => {
        expect(container.textContent).toContain("Devices");
      });
      expect(container.textContent).toContain("No subjects yet.");
    } finally {
      await unmount(app1);
      container.remove();
    }

    // Paired Tailscale App drops back to PairView when a non-/api/pair request returns 401
    authStore.resetForTests();
    queryClient.clear();
    authPayload = {
      access: "tailscale",
      paired: true,
      tailscale_enabled: true,
      paired_devices: 0,
    };

    let subjectsStatus = 200;
    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = requestPathname(input);
      if (path === "/api/auth") {
        return Promise.resolve(jsonResponse(authPayload));
      }
      if (path === "/api/runs") {
        return Promise.resolve(
          jsonResponse({ available: false, reason: "no_host", target: 10, run: null }),
        );
      }
      if (path === "/api/subjects") {
        if (subjectsStatus === 401) {
          return Promise.resolve(
            jsonResponse(
              { error: "unpaired", detail: "scan the pairing QR code on your computer" },
              401,
            ),
          );
        }
        return Promise.resolve(jsonResponse([]));
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    const container2 = document.createElement("div");
    document.body.appendChild(container2);
    const app2 = mount(App, { target: container2 });

    try {
      await vi.waitFor(() => {
        expect(container2.textContent).toContain("No subjects yet.");
      });
      expect(container2.textContent).not.toContain("Devices");

      subjectsStatus = 401;
      await apiClient.GET("/api/subjects");

      await vi.waitFor(() => {
        expect(container2.textContent).toContain("Pair this device");
      });
      expect(container2.textContent).toContain(
        "This device is no longer paired — scan the current QR code to reconnect.",
      );
    } finally {
      await unmount(app2);
      container2.remove();
    }
  });

  it("redirects /pair to / when the paired router is active", async () => {
    vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    await navigate("/pair");
    expect(route.pathname).toBe("/");
    expect(window.location.pathname).toBe("/");
  });

  it("ignores non-/api/pair 401 responses while authStore.ready is false", async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ error: "unpaired", detail: "scan the pairing QR code" }, 401),
      ) as unknown as typeof fetch;

    const handleUnpairedSpy = vi.spyOn(authStore, "handleUnpaired");
    expect(authStore.ready).toBe(false);

    const res = await apiClient.GET("/api/subjects");
    expect(res.response.status).toBe(401);
    expect(handleUnpairedSpy).not.toHaveBeenCalled();
    expect(authStore.ready).toBe(false);
    expect(authStore.paired).toBe(true);
    expect(authStore.error).toBeNull();
  });

  it.each(["auth", "pair"])(
    "retries a scrubbed QR after a transient %s failure",
    async (failedStep) => {
      const calls: string[] = [];
      const tokens: unknown[] = [];
      let failing = true;
      globalThis.fetch = vi.fn(async (request: Request) => {
        const path = requestPathname(request);
        calls.push(path);
        if (path === "/api/auth") {
          if (failing && failedStep === "auth") throw new TypeError("Offline");
          return jsonResponse({
            access: "tailscale",
            paired: false,
            tailscale_enabled: true,
            paired_devices: 0,
          });
        }
        if (path === "/api/pair") {
          tokens.push(await request.json());
          if (failing) return jsonResponse({ error: "busy", detail: "Database busy" }, 503);
          return jsonResponse({ paired: true });
        }
        return jsonResponse(path === "/api/subjects" ? [] : { available: false, run: null });
      }) as unknown as typeof fetch;
      window.history.replaceState(null, "", "/pair#retry-token");
      const container = document.createElement("div");
      const app = mount(App, { target: container });
      try {
        await vi.waitFor(() => expect(authStore.canRetry).toBe(true));
        expect(window.location.hash).toBe("");
        failing = false;
        if (failedStep === "auth") {
          window.dispatchEvent(new Event("online"));
        } else {
          const retry = Array.from(container.querySelectorAll("button")).find(
            (button) => button.textContent?.trim() === "Retry",
          );
          expect(retry).toBeDefined();
          retry?.click();
        }
        await vi.waitFor(() => expect(calls).toContain("/api/subjects"));
        expect(authStore.paired).toBe(true);
        expect(authStore.canRetry).toBe(false);
        expect(tokens).toEqual(Array(failedStep === "pair" ? 2 : 1).fill({ token: "retry-token" }));
      } finally {
        await unmount(app);
      }
    },
  );

  it("renders a connection error alert instead of PairView when GET /api/auth fails on loopback", async () => {
    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      if (requestPathname(input) === "/api/auth") {
        return Promise.reject(new TypeError("Failed to fetch"));
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    const container = document.createElement("div");
    document.body.appendChild(container);
    const app = mount(App, { target: container });

    try {
      await vi.waitFor(() => {
        expect(authStore.ready).toBe(true);
      });
      const alert = container.querySelector('[role="alert"]');
      expect(alert?.textContent).toContain("Failed to fetch");
      expect(container.textContent).not.toContain("Pair this device");
      expect(container.textContent).not.toContain("Devices");
    } finally {
      await unmount(app);
      container.remove();
    }
  });
});
