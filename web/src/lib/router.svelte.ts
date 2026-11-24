export type Route =
  | { kind: "subjects" }
  | { kind: "tree"; subject: string }
  | { kind: "test"; subject: string; topicId: number | null; n: number }
  | { kind: "result"; subject: string; attemptId: number }
  | { kind: "not_found"; pathname: string };

function decodeSegment(segment: string): string | null {
  try {
    const decoded = decodeURIComponent(segment);
    return decoded ? decoded : null;
  } catch {
    return null;
  }
}

export function parseRoute(pathname: string, search = ""): Route {
  if (pathname === "/") {
    return { kind: "subjects" };
  }
  const parts = pathname.split("/");
  if (parts[1] !== "s" || !parts[2]) {
    return { kind: "not_found", pathname };
  }
  const subject = decodeSegment(parts[2]);
  if (subject === null) {
    return { kind: "not_found", pathname };
  }
  if (parts.length === 3 || (parts.length === 4 && parts[3] === "")) {
    return { kind: "tree", subject };
  }
  if (parts.length === 4 && parts[3] === "test") {
    const params = new URLSearchParams(search);
    const rawTopic = params.get("topic");
    const topicId = rawTopic !== null && /^\d+$/.test(rawTopic) ? Number(rawTopic) : null;
    const rawN = params.get("n");
    const parsedN = rawN !== null && /^\d+$/.test(rawN) ? Number(rawN) : 10;
    const n = parsedN > 0 ? parsedN : 10;
    return { kind: "test", subject, topicId, n };
  }
  if (parts.length === 5 && parts[3] === "attempts" && /^\d+$/.test(parts[4])) {
    return { kind: "result", subject, attemptId: Number(parts[4]) };
  }
  return { kind: "not_found", pathname };
}

export function shouldInterceptLink(anchor: HTMLAnchorElement, event: MouseEvent): boolean {
  if (event.defaultPrevented || event.button !== 0) {
    return false;
  }
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
    return false;
  }
  if (anchor.hasAttribute("download")) {
    return false;
  }
  const target = anchor.getAttribute("target");
  if (target && target !== "_self") {
    return false;
  }
  const rawHref = anchor.getAttribute("href");
  if (!rawHref || rawHref.startsWith("#")) {
    return false;
  }
  let url: URL;
  try {
    url = new URL(anchor.href, window.location.href);
  } catch {
    return false;
  }
  if (url.origin !== window.location.origin) {
    return false;
  }
  if (
    /^\/s\/[^/]+\/materials\//.test(url.pathname) ||
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/static/")
  ) {
    return false;
  }
  return true;
}

export function createRouter() {
  let pathname = $state(typeof window !== "undefined" ? window.location.pathname : "/");
  let search = $state(typeof window !== "undefined" ? window.location.search : "");
  const route = $derived(parseRoute(pathname, search));

  function syncFromLocation(): void {
    if (typeof window === "undefined") {
      return;
    }
    pathname = window.location.pathname;
    search = window.location.search;
  }

  if (typeof window !== "undefined") {
    window.addEventListener("popstate", syncFromLocation);
  }

  function navigate(url: string, opts: { replace?: boolean } = {}): void {
    if (typeof window === "undefined") {
      return;
    }
    const resolved = new URL(url, window.location.href);
    const nextUrl = resolved.pathname + resolved.search + resolved.hash;
    if (opts.replace) {
      window.history.replaceState(null, "", nextUrl);
    } else {
      window.history.pushState(null, "", nextUrl);
    }
    pathname = resolved.pathname;
    search = resolved.search;
    if (!resolved.hash) {
      window.scrollTo(0, 0);
    }
  }

  function interceptClick(event: MouseEvent): void {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    const anchor = target.closest("a");
    if (!(anchor instanceof HTMLAnchorElement)) {
      return;
    }
    if (!shouldInterceptLink(anchor, event)) {
      return;
    }
    event.preventDefault();
    const resolved = new URL(anchor.href, window.location.href);
    navigate(resolved.pathname + resolved.search + resolved.hash);
  }

  function destroy(): void {
    if (typeof window !== "undefined") {
      window.removeEventListener("popstate", syncFromLocation);
    }
  }

  return {
    get pathname() {
      return pathname;
    },
    get search() {
      return search;
    },
    get route() {
      return route;
    },
    navigate,
    interceptClick,
    destroy,
  };
}

export const router = createRouter();
