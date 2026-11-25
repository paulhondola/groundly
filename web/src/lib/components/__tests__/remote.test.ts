import { QueryClient } from "@tanstack/svelte-query";
import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import QueryHarness from "../../views/__tests__/QueryHarness.svelte";
import QrCode from "../QrCode.svelte";
import RemoteDialog from "../RemoteDialog.svelte";

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

describe("QrCode", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    container.remove();
  });

  it("renders <svg role='img'> with a single <path> encoding active modules with quiet zone", () => {
    const instance = mount(QrCode, {
      target: container,
      props: {
        matrix: [
          [true, false],
          [false, true],
        ],
      },
    });
    flushSync();

    const svg = container.querySelector('svg[role="img"]');
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute("viewBox")).toBe("0 0 6 6");

    const paths = svg?.querySelectorAll("path") ?? [];
    expect(paths).toHaveLength(1);
    expect(paths[0]?.getAttribute("d")).toBe("M2,2h1v1h-1zM3,3h1v1h-1z");

    void unmount(instance);
  });
});

describe("RemoteDialog", () => {
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

  it("toggles open, displays paired devices from GET /api/remote, and revokes single or all devices", async () => {
    const qc = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });

    let devices = [
      {
        id: 7,
        label: "iPhone · Safari",
        peer_ip: "100.64.0.10",
        created_at: "2026-11-25T08:00:00+00:00",
        last_seen_at: 1764057600,
      },
    ];
    let pairingUrl = "macbook.tail1234.ts.net:8765/pair#tok1";
    const calls: Array<{ method: string; path: string }> = [];

    globalThis.fetch = vi
      .fn()
      .mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const path = requestPathname(input);
        const method = input instanceof Request ? input.method : (init?.method ?? "GET");
        calls.push({ method, path });

        if (path === "/api/remote" && method === "GET") {
          return Promise.resolve(
            jsonResponse({
              active: true,
              host: "macbook.tail1234.ts.net",
              pairing_url: pairingUrl,
              qr_matrix: [
                [true, false],
                [false, true],
              ],
              devices,
            }),
          );
        }
        if (path === "/api/remote/devices/7/revoke" && method === "POST") {
          devices = [];
          return Promise.resolve(jsonResponse({ revoked: true }));
        }
        if (path === "/api/remote/revoke-all" && method === "POST") {
          devices = [];
          pairingUrl = "macbook.tail1234.ts.net:8765/pair#tok2";
          return Promise.resolve(jsonResponse({ revoked: 1 }));
        }
        return Promise.resolve(jsonResponse({}));
      }) as unknown as typeof fetch;

    const instance = mount(QueryHarness, {
      target: container,
      props: {
        client: qc,
        component: RemoteDialog,
        props: { pairedCount: 1 },
      },
    });
    flushSync();

    // Closed initially — does not fetch /api/remote until opened
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(calls).toHaveLength(0);

    const trigger = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("Devices"),
    );
    expect(trigger).toBeDefined();
    trigger?.click();
    flushSync();

    await vi.waitFor(() => {
      expect(container.textContent).toContain("iPhone · Safari");
    });
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
    expect(container.querySelector('svg[role="img"]')).not.toBeNull();
    expect(container.textContent).toContain("100.64.0.10");

    // Click per-device Revoke
    const revokeBtn = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Revoke",
    );
    expect(revokeBtn).toBeDefined();
    revokeBtn?.click();

    await vi.waitFor(() => {
      expect(calls).toContainEqual({ method: "POST", path: "/api/remote/devices/7/revoke" });
    });
    await vi.waitFor(() => {
      expect(container.textContent).not.toContain("iPhone · Safari");
    });

    // Re-populate devices via fresh query invalidation and test Revoke all
    devices = [
      {
        id: 8,
        label: "iPad · Safari",
        peer_ip: "100.64.0.11",
        created_at: "2026-11-25T08:05:00+00:00",
        last_seen_at: 1764057900,
      },
    ];
    await qc.invalidateQueries({ queryKey: ["remote"] });
    await vi.waitFor(() => {
      expect(container.textContent).toContain("iPad · Safari");
    });

    const revokeAllBtn = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Revoke all",
    );
    expect(revokeAllBtn).toBeDefined();
    revokeAllBtn?.click();

    await vi.waitFor(() => {
      expect(calls).toContainEqual({ method: "POST", path: "/api/remote/revoke-all" });
    });
    await vi.waitFor(() => {
      expect(container.textContent).not.toContain("iPad · Safari");
    });

    // Toggle closed
    trigger?.click();
    flushSync();
    expect(container.querySelector('[role="dialog"]')).toBeNull();

    void unmount(instance);
  });

  it("renders inactive Tailscale message while still listing existing devices, and closes on Escape", async () => {
    const qc = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });

    globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      if (requestPathname(input) === "/api/remote") {
        return Promise.resolve(
          jsonResponse({
            active: false,
            host: null,
            pairing_url: null,
            qr_matrix: null,
            devices: [
              {
                id: 3,
                label: "Android · Chrome",
                peer_ip: "100.64.0.22",
                created_at: "2026-11-25T07:00:00+00:00",
                last_seen_at: 1764054000,
              },
            ],
          }),
        );
      }
      return Promise.resolve(jsonResponse({}));
    }) as unknown as typeof fetch;

    const instance = mount(QueryHarness, {
      target: container,
      props: {
        client: qc,
        component: RemoteDialog,
        props: { pairedCount: 1 },
      },
    });
    flushSync();

    const trigger = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("Devices"),
    );
    trigger?.click();
    flushSync();

    await vi.waitFor(() => {
      expect(container.textContent).toContain("Android · Chrome");
    });
    expect(container.textContent).toContain("Tailscale remote access is inactive");
    expect(container.querySelector('svg[role="img"]')).toBeNull();

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    flushSync();
    expect(container.querySelector('[role="dialog"]')).toBeNull();

    void unmount(instance);
  });
});
