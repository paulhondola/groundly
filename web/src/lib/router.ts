import { createRouter, type HooksContext } from "sv-router";
import AttemptResultRoute from "./routes/AttemptResultRoute.svelte";
import SubjectTestRoute from "./routes/SubjectTestRoute.svelte";
import SubjectTreeRoute from "./routes/SubjectTreeRoute.svelte";
import NotFoundView from "./views/NotFoundView.svelte";
import SubjectsView from "./views/SubjectsView.svelte";

export type BeforeLoadContext = HooksContext;

export type BeforeLoadGuard = (
  context: BeforeLoadContext,
) => void | boolean | Promise<void | boolean>;

let beforeLoadGuard: BeforeLoadGuard | null = null;

export function setBeforeLoadGuard(guard: BeforeLoadGuard | null): void {
  beforeLoadGuard = guard;
}

export function decodeParam(segment: string | undefined): string | null {
  if (!segment) {
    return null;
  }
  try {
    const decoded = decodeURIComponent(segment);
    return decoded ? decoded : null;
  } catch {
    return null;
  }
}

export function parsePositiveInt(raw: string | undefined | null): number | null {
  if (raw == null || !/^\d+$/.test(raw)) {
    return null;
  }
  const parsed = Number(raw);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    return null;
  }
  return parsed;
}

export function parseTestSearch(params: URLSearchParams): { topicId: number | null; n: number } {
  const topicId = parsePositiveInt(params.get("topic"));
  const n = parsePositiveInt(params.get("n")) ?? 10;
  return { topicId, n };
}

export function isMaterialPath(pathname: string): boolean {
  return /^\/s\/[^/]+\/materials\//.test(pathname);
}

const router = createRouter({
  "/": SubjectsView,
  "/s/:subject": SubjectTreeRoute,
  "/s/:subject/test": SubjectTestRoute,
  "/s/:subject/attempts/:id": AttemptResultRoute,
  "*": NotFoundView,
  hooks: {
    beforeLoad(context) {
      if (isMaterialPath(context.pathname)) {
        return false;
      }
      return beforeLoadGuard?.(context);
    },
  },
});

export const p: typeof router.p = (...args) => router.p(...args);
export const navigate: typeof router.navigate = (...args) =>
  (router as { navigate: (...a: typeof args) => ReturnType<typeof router.navigate> }).navigate(
    ...args,
  );
export const { isActive, route } = router;
