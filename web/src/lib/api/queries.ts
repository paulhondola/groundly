import { QueryClient } from "@tanstack/svelte-query";
import { ApiError } from "./client";

export const queryKeys = {
  subjects: () => ["subjects"] as const,
  subject: (subject: string) => ["subject", subject] as const,
  tree: (subject: string) => ["subject", subject, "tree"] as const,
  attempts: (subject: string) => ["subject", subject, "attempts"] as const,
  attempt: (subject: string, attemptId: number) =>
    ["subject", subject, "attempts", attemptId] as const,
};

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
