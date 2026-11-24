import { createSubscriber } from "svelte/reactivity";
import { createRouter } from "sv-router";
import AttemptResultRoute from "./routes/AttemptResultRoute.svelte";
import SubjectTestRoute from "./routes/SubjectTestRoute.svelte";
import SubjectTreeRoute from "./routes/SubjectTreeRoute.svelte";
import NotFoundView from "./views/NotFoundView.svelte";
import SubjectsView from "./views/SubjectsView.svelte";

let malformedPathnameValue: string | null = null;
let pendingMalformedPathname: string | null = null;
let notifyMalformedPathname: (() => void) | undefined;

const subscribeMalformedPathname = createSubscriber((update) => {
  notifyMalformedPathname = update;
  return () => {
    notifyMalformedPathname = undefined;
  };
});

function setMalformedPathname(value: string | null): void {
  if (malformedPathnameValue === value) {
    return;
  }
  malformedPathnameValue = value;
  notifyMalformedPathname?.();
}

export function getMalformedPathname(): string | null {
  subscribeMalformedPathname();
  return malformedPathnameValue;
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

export function hasMalformedPercentEncoding(pathname: string): boolean {
  const pathOnly = pathname.split(/[?#]/, 1)[0];
  for (const segment of pathOnly.split("/")) {
    if (!segment) {
      continue;
    }
    try {
      decodeURIComponent(segment);
    } catch {
      return true;
    }
  }
  return false;
}

if (typeof window !== "undefined" && hasMalformedPercentEncoding(window.location.pathname)) {
  const rawPathname = window.location.pathname;
  setMalformedPathname(rawPathname);
  pendingMalformedPathname = rawPathname;
  window.history.replaceState(window.history.state, "", "/not-found");
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
    },
    afterLoad(context) {
      if (pendingMalformedPathname !== null && context.pathname === "/not-found") {
        setMalformedPathname(pendingMalformedPathname);
      } else {
        setMalformedPathname(null);
      }
      pendingMalformedPathname = null;
    },
  },
});

async function navigateNotFound(rawPathname: string) {
  pendingMalformedPathname = rawPathname;
  try {
    return await router.navigate("/not-found" as "/");
  } finally {
    pendingMalformedPathname = null;
  }
}

export const navigate: typeof router.navigate = async (...args) => {
  const [target] = args;
  if (typeof target === "string" && hasMalformedPercentEncoding(target)) {
    return navigateNotFound(target);
  }
  try {
    return await (
      router as { navigate: (...a: typeof args) => ReturnType<typeof router.navigate> }
    ).navigate(...args);
  } catch (err) {
    if (err instanceof URIError) {
      const fallbackPath =
        typeof target === "string"
          ? target
          : typeof window !== "undefined"
            ? window.location.pathname
            : "/not-found";
      return navigateNotFound(fallbackPath);
    }
    throw err;
  }
};

export const { isActive, route } = router;

function handleWindowClick(event: MouseEvent): void {
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey
  ) {
    return;
  }
  const target = event.target;
  if (!(target instanceof Element)) {
    return;
  }
  const anchor = target.closest("a");
  if (!(anchor instanceof HTMLAnchorElement)) {
    return;
  }
  if (
    anchor.hasAttribute("target") ||
    anchor.hasAttribute("download") ||
    !anchor.hasAttribute("href") ||
    anchor.getAttribute("href")?.startsWith("#")
  ) {
    return;
  }
  let url: URL;
  try {
    url = new URL(anchor.href, window.location.href);
  } catch {
    return;
  }
  if (url.origin !== window.location.origin) {
    return;
  }
  if (isMaterialPath(url.pathname)) {
    event.stopImmediatePropagation();
    return;
  }
  if (hasMalformedPercentEncoding(url.pathname)) {
    event.preventDefault();
    event.stopImmediatePropagation();
    void navigate(url.pathname as "/");
  }
}

if (typeof window !== "undefined") {
  window.addEventListener("click", handleWindowClick, { capture: true });
}
