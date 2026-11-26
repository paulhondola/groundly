import { QueryClient } from "@tanstack/svelte-query";
import { ApiError, apiClient, unwrap } from "./client";

export const queryKeys = {
  subjects: () => ["subjects"] as const,
  allSubjects: () => ["subject"] as const,
  subject: (subject: string) => ["subject", subject] as const,
  tree: (subject: string) => ["subject", subject, "tree"] as const,
  attempts: (subject: string) => ["subject", subject, "attempts"] as const,
  attempt: (subject: string, attemptId: number) =>
    ["subject", subject, "attempts", attemptId] as const,
  remote: () => ["remote"] as const,
  activity: () => ["activity"] as const,
  exportInfo: (subject: string) => ["subject", subject, "export"] as const,
};

export function treeQueryOptions(subject: string) {
  return {
    queryKey: queryKeys.tree(subject),
    queryFn: async ({ signal }: { signal: AbortSignal }) =>
      unwrap(
        await apiClient.GET("/api/subjects/{subject}/tree", {
          params: { path: { subject } },
          signal,
        }),
      ),
  };
}

export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
    return false;
  }
  return failureCount < 2;
}

export function createAppQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        retry: shouldRetryQuery,
        refetchOnWindowFocus: true,
      },
    },
  });
}

export const queryClient: QueryClient = createAppQueryClient();
