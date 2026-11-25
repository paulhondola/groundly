import type { QueryClient } from "@tanstack/svelte-query";
import { ApiError, apiClient, setOnUnpaired, unwrap } from "./api/client";
import { queryClient } from "./api/queries";
import { runStore } from "./run.svelte";

const UNPAIRED_MESSAGE = "This device is no longer paired — scan the current QR code to reconnect.";

function takePendingPairToken(): string | null {
  if (typeof window === "undefined" || window.location.pathname !== "/pair") {
    return null;
  }
  const rawHash = window.location.hash;
  if (rawHash.length <= 1) {
    return null;
  }
  window.history.replaceState(window.history.state, "", "/");
  const rawToken = rawHash.slice(1);
  try {
    return decodeURIComponent(rawToken);
  } catch {
    return rawToken;
  }
}

export function createAuthStore(qc: QueryClient = queryClient, onUnpaired?: () => void) {
  let ready = $state(false);
  let access = $state<"loopback" | "tailscale">("loopback");
  let paired = $state(true);
  let tailscaleEnabled = $state(false);
  let pairedDevices = $state(0);
  let error = $state<string | null>(null);
  let canRetry = $state(false);
  let pendingToken: string | null = null;
  let inFlight: Promise<void> | null = null;

  function resetForTests(options?: { ready?: boolean }): void {
    ready = options?.ready ?? false;
    access = "loopback";
    paired = true;
    tailscaleEnabled = false;
    pairedDevices = 0;
    error = null;
    inFlight = null;
    canRetry = false;
    pendingToken = null;
  }

  function handleUnpaired(): void {
    ready = true;
    access = "tailscale";
    paired = false;
    error = UNPAIRED_MESSAGE;
    canRetry = false;
    pendingToken = null;
    onUnpaired?.();
    qc.clear();
  }

  async function loadSession(): Promise<void> {
    ready = false;
    canRetry = false;
    error = null;
    try {
      const status = unwrap(await apiClient.GET("/api/auth"));
      access = status.access;
      tailscaleEnabled = status.tailscale_enabled;
      pairedDevices = status.paired_devices;
      paired = status.paired;

      if (access === "tailscale" && pendingToken) {
        try {
          unwrap(await apiClient.POST("/api/pair", { body: { token: pendingToken } }));
          paired = true;
          qc.clear();
        } catch (err) {
          if (!(err instanceof ApiError) || err.status >= 500 || err.status === 429) {
            throw err;
          }
          // A rejected QR leaves the session confirmed by /api/auth intact.
          if (!paired) error = err.message;
        }
      }
      pendingToken = null;
    } catch (err) {
      paired = false;
      canRetry = true;
      error = err instanceof Error ? err.message : "Could not connect to Groundly.";
    } finally {
      ready = true;
    }
  }

  function bootstrap(): Promise<void> {
    if (!inFlight) {
      pendingToken = takePendingPairToken() ?? pendingToken;
      inFlight = loadSession().finally(() => {
        inFlight = null;
      });
    }
    return inFlight;
  }

  return {
    get ready() {
      return ready;
    },
    get access() {
      return access;
    },
    get paired() {
      return paired;
    },
    get tailscaleEnabled() {
      return tailscaleEnabled;
    },
    get pairedDevices() {
      return pairedDevices;
    },
    get canRetry() {
      return canRetry;
    },
    get error() {
      return error;
    },
    bootstrap,
    handleUnpaired,
    resetForTests,
  };
}

export const authStore = createAuthStore(queryClient, () => runStore.disconnect());
setOnUnpaired(() => {
  if (authStore.ready) {
    authStore.handleUnpaired();
  }
});
