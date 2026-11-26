import { createQuery } from "@tanstack/svelte-query";
import { apiClient, unwrap } from "./api/client";
import type { components } from "./api/schema.d.ts";
import { queryKeys } from "./api/queries";
import { type OrbMode, orbModeForHosts } from "./components/orb/orb";

export type ActivityHost = components["schemas"]["ActivityHost"];

const POLL_MS = 3000;
const DONE_MS = 2000;

const VERBS: Record<ActivityHost["orb"], string> = {
  listening: "connected",
  reading: "reading",
  mapping: "mapping",
  writing: "writing for",
};

/** The host the header speaks for: the first one at work, else the first one. */
export function pickHost(hosts: readonly ActivityHost[]): ActivityHost | null {
  return hosts.find((h) => h.orb !== "listening") ?? hosts[0] ?? null;
}

export function hostName(host: ActivityHost): string {
  return host.client ?? "MCP host";
}

/** "Claude Code · reading apd", "Claude Code connected", "No host connected". */
export function hostLine(hosts: readonly ActivityHost[]): string {
  const host = pickHost(hosts);
  if (!host) {
    return "No host connected";
  }
  if (host.orb === "listening") {
    return hosts.length > 1 ? hosts.length + " hosts connected" : hostName(host) + " connected";
  }
  return hostName(host) + " · " + VERBS[host.orb] + (host.subject ? " " + host.subject : "");
}

function newestDone(hosts: readonly ActivityHost[]): number | null {
  let newest: number | null = null;
  for (const h of hosts) {
    const at = h.last_done_at ? Date.parse(h.last_done_at) : NaN;
    if (!Number.isNaN(at) && (newest === null || at > newest)) {
      newest = at;
    }
  }
  return newest;
}

/**
 * Polls GET /api/activity every 3 s while the tab is visible. A failed read leaves `available`
 * false so callers show a quiet fallback; the poll keeps going and recovers on its own.
 * Call during component init.
 */
export function useActivity() {
  const query = createQuery(() => ({
    queryKey: queryKeys.activity(),
    queryFn: async ({ signal }: { signal: AbortSignal }) =>
      unwrap(await apiClient.GET("/api/activity", { signal })),
    refetchInterval: POLL_MS,
    staleTime: 0,
    retry: false,
  }));

  let done = $state(false);
  let seen: number | null | undefined;
  let timer: ReturnType<typeof setTimeout> | null = null;

  // `done` is not a server state: show it for 2 s when a gate's acceptance is newer than the
  // last one this page saw (never on the first read, which would replay an old one).
  $effect(() => {
    const hosts = query.data?.hosts;
    if (!hosts) {
      return;
    }
    const newest = newestDone(hosts);
    if (seen !== undefined && newest !== null && (seen === null || newest > seen)) {
      done = true;
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(() => {
        done = false;
        timer = null;
      }, DONE_MS);
    }
    seen = newest;
  });

  $effect(() => () => {
    if (timer !== null) clearTimeout(timer);
  });

  return {
    get available(): boolean {
      return query.data !== undefined && !query.isError;
    },
    get hosts() {
      return query.data?.hosts ?? [];
    },
    get indexing() {
      return query.data?.indexing ?? [];
    },
    get recent() {
      return query.data?.recent ?? [];
    },
    get mode(): OrbMode {
      return done ? "done" : orbModeForHosts(query.data?.hosts ?? []);
    },
  };
}
