import { QueryClient } from "@tanstack/svelte-query";
import { mount, unmount } from "svelte";
import { searchParams } from "sv-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import QueryHarness from "../views/__tests__/QueryHarness.svelte";

vi.hoisted(() => {
  if (typeof globalThis.IntersectionObserver === "undefined") {
    globalThis.IntersectionObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof IntersectionObserver;
  }
});

import {
  decodeParam,
  getMalformedPathname,
  hasMalformedPercentEncoding,
  isMaterialPath,
  navigate,
  p,
  parsePositiveInt,
  parseTestSearch,
  route,
  setBeforeLoadGuard,
} from "../router";
import AttemptResultRoute from "../routes/AttemptResultRoute.svelte";
import SubjectTreeRoute from "../routes/SubjectTreeRoute.svelte";
import NotFoundView from "../views/NotFoundView.svelte";

describe("decodeParam", () => {
  it("decodes valid segments and returns null on empty, undefined, or malformed percent-encoding", () => {
    expect(decodeParam("PDSS")).toBe("PDSS");
    expect(decodeParam("CS%20101")).toBe("CS 101");
    expect(decodeParam("")).toBeNull();
    expect(decodeParam(undefined)).toBeNull();
    expect(() => decodeParam("%E0%A4%A")).not.toThrow();
    expect(decodeParam("%E0%A4%A")).toBeNull();
  });
});

describe("parsePositiveInt", () => {
  it("accepts positive safe integers and rejects zero, negative, non-numeric, float, non-safe, empty, and null", () => {
    expect(parsePositiveInt("1")).toBe(1);
    expect(parsePositiveInt("42")).toBe(42);
    expect(parsePositiveInt("0")).toBeNull();
    expect(parsePositiveInt("-5")).toBeNull();
    expect(parsePositiveInt("abc")).toBeNull();
    expect(parsePositiveInt("1.5")).toBeNull();
    expect(parsePositiveInt("9007199254740993")).toBeNull();
    expect(parsePositiveInt("")).toBeNull();
    expect(parsePositiveInt(null)).toBeNull();
    expect(parsePositiveInt(undefined)).toBeNull();
  });
});

describe("parseTestSearch", () => {
  it("parses topic and n with defaults and validation", () => {
    expect(parseTestSearch(new URLSearchParams("?topic=7&n=10"))).toEqual({
      topicId: 7,
      n: 10,
    });
    expect(parseTestSearch(new URLSearchParams("?n=20"))).toEqual({
      topicId: null,
      n: 20,
    });
    expect(parseTestSearch(new URLSearchParams(""))).toEqual({
      topicId: null,
      n: 10,
    });
    expect(parseTestSearch(new URLSearchParams("?n=0"))).toEqual({
      topicId: null,
      n: 10,
    });
    expect(parseTestSearch(new URLSearchParams("?n=abc"))).toEqual({
      topicId: null,
      n: 10,
    });
    expect(parseTestSearch(new URLSearchParams("?topic=abc&n=5"))).toEqual({
      topicId: null,
      n: 5,
    });
  });
});

describe("isMaterialPath", () => {
  it("detects material PDF paths and ignores non-material routes", () => {
    expect(isMaterialPath("/s/PDSS/materials/1")).toBe(true);
    expect(isMaterialPath("/s/CS%20101/materials/42")).toBe(true);
    expect(isMaterialPath("/s/PDSS")).toBe(false);
    expect(isMaterialPath("/s/PDSS/test")).toBe(false);
    expect(isMaterialPath("/")).toBe(false);
  });
});

describe("sv-router navigation, param encoding, and beforeLoad guard", () => {
  let scrollSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(async () => {
    setBeforeLoadGuard(null);
    scrollSpy = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    await navigate("/");
    scrollSpy.mockClear();
  });

  afterEach(() => {
    setBeforeLoadGuard(null);
    scrollSpy.mockRestore();
  });

  it("navigates to /s/PDSS, updates route state, and scrolls to top", async () => {
    await navigate("/s/:subject", { params: { subject: "PDSS" } });
    expect(route.pathname).toBe("/s/PDSS");
    expect(route.params.subject).toBe("PDSS");
    expect(scrollSpy).toHaveBeenCalledWith({ top: 0, left: 0, behavior: undefined });
  });

  it("encodes params once and round-trips spaces through decodeParam without double-encoding", async () => {
    expect(p("/s/:subject/attempts/:id", { params: { subject: "CS 101", id: "42" } })).toBe(
      "/s/CS%20101/attempts/42",
    );

    await navigate("/s/:subject", { params: { subject: "CS 101" } });
    expect(window.location.pathname).toBe("/s/CS%20101");
    expect(decodeParam(route.params.subject)).toBe("CS 101");
  });

  it("skips scrolling when scrollToTop is false", async () => {
    await navigate("/s/:subject", { params: { subject: "PDSS" }, scrollToTop: false });
    expect(route.pathname).toBe("/s/PDSS");
    expect(scrollSpy).not.toHaveBeenCalled();
  });

  it("runs setBeforeLoadGuard before route transitions, cancels when false, and resets cleanly", async () => {
    const guard = vi.fn().mockReturnValue(false);
    setBeforeLoadGuard(guard);

    await navigate("/s/:subject", { params: { subject: "PDSS" } });
    expect(guard).toHaveBeenCalledTimes(1);
    expect(guard.mock.calls[0][0].pathname).toBe("/s/PDSS");
    expect(route.pathname).toBe("/");

    setBeforeLoadGuard(null);
    await navigate("/s/:subject", { params: { subject: "PDSS" } });
    expect(route.pathname).toBe("/s/PDSS");
  });

  it("refuses to match /s/:subject/materials/:id as an SPA transition", async () => {
    await navigate("/s/:subject", { params: { subject: "PDSS" } });
    expect(route.pathname).toBe("/s/PDSS");

    await navigate("/s/PDSS/materials/1" as "/");
    expect(route.pathname).toBe("/s/PDSS");
  });

  it("matches test, attempt, and wildcard routes and syncs searchParams", async () => {
    await navigate("/s/:subject/test", {
      params: { subject: "PDSS" },
      search: { topic: 7, n: 10 },
    });
    expect(route.pathname).toBe("/s/PDSS/test");
    expect(route.params.subject).toBe("PDSS");
    expect(parseTestSearch(searchParams.toURLSearchParams())).toEqual({ topicId: 7, n: 10 });

    await navigate("/s/:subject/attempts/:id", {
      params: { subject: "PDSS", id: "42" },
    });
    expect(route.pathname).toBe("/s/PDSS/attempts/42");
    expect(route.params.subject).toBe("PDSS");
    expect(route.params.id).toBe("42");

    await navigate("/unknown" as "/");
    expect(route.pathname).toBe("/unknown");
  });

  it("handles malformed percent-encoding without throwing URIError and clears on valid navigation", async () => {
    expect(hasMalformedPercentEncoding("/s/%E0%A4%A")).toBe(true);
    expect(hasMalformedPercentEncoding("/s/100%25")).toBe(false);

    await expect(navigate("/s/%E0%A4%A" as "/")).resolves.toBeDefined();
    expect(route.pathname).toBe("/not-found");
    expect(getMalformedPathname()).toBe("/s/%E0%A4%A");

    await navigate("/s/:subject", { params: { subject: "PDSS" } });
    expect(route.pathname).toBe("/s/PDSS");
    expect(getMalformedPathname()).toBeNull();
  });

  it("does not hijack clicks on material links lacking target=_blank and prevents URIError on malformed links", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const bubbleListener = vi.fn((e: Event) => e.preventDefault());
    window.addEventListener("click", bubbleListener);

    try {
      const materialAnchor = document.createElement("a");
      materialAnchor.href = "/s/PDSS/materials/1";
      container.appendChild(materialAnchor);

      const materialClick = new MouseEvent("click", {
        bubbles: true,
        cancelable: true,
        button: 0,
      });
      materialAnchor.dispatchEvent(materialClick);
      expect(materialClick.defaultPrevented).toBe(false);
      expect(bubbleListener).not.toHaveBeenCalled();

      const badAnchor = document.createElement("a");
      badAnchor.href = "/s/%E0%A4%A";
      container.appendChild(badAnchor);

      const badClick = new MouseEvent("click", {
        bubbles: true,
        cancelable: true,
        button: 0,
      });
      badAnchor.dispatchEvent(badClick);
      expect(badClick.defaultPrevented).toBe(true);
      expect(bubbleListener).not.toHaveBeenCalled();
      await vi.waitFor(() => {
        expect(getMalformedPathname()).toBe("/s/%E0%A4%A");
      });
    } finally {
      window.removeEventListener("click", bubbleListener);
      container.remove();
    }
  });
});

describe("NotFoundView, SubjectTreeRoute, and AttemptResultRoute", () => {
  const originalFetch = globalThis.fetch;
  let container: HTMLDivElement;

  beforeEach(async () => {
    setBeforeLoadGuard(null);
    vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    container = document.createElement("div");
    document.body.appendChild(container);
    await navigate("/");
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    container.remove();
    vi.restoreAllMocks();
  });

  it("renders NotFoundView with malformed pathname, route.pathname, or explicit pathname prop", async () => {
    await navigate("/s/%E0%A4%A" as "/");
    const malformedView = mount(NotFoundView, { target: container });
    expect(container.textContent).toContain("Page not found");
    expect(container.textContent).toContain("/s/%E0%A4%A");
    await unmount(malformedView);

    await navigate("/s/PDSS/extra" as "/");
    const defaultView = mount(NotFoundView, { target: container });
    expect(container.textContent).toContain("Page not found");
    expect(container.textContent).toContain("/s/PDSS/extra");
    const link = container.querySelector("a");
    expect(link?.getAttribute("href")).toBe("/");
    expect(link?.textContent).toContain("All subjects");
    await unmount(defaultView);

    const customView = mount(NotFoundView, {
      target: container,
      props: { pathname: "/custom/missing" },
    });
    expect(container.textContent).toContain("/custom/missing");
    await unmount(customView);
  });

  it("preserves % in SubjectTreeRoute without double-decoding or falling back to NotFoundView", async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.endsWith("/tree")) {
        return new Response(JSON.stringify({ title: "100% Course", tree: [], attempts: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/attempts")) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ active: null, available: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;

    await navigate("/s/:subject", { params: { subject: "100%" } });
    expect(window.location.pathname).toBe("/s/100%25");
    expect(route.params.subject).toBe("100%");

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = mount(QueryHarness, {
      target: container,
      props: { client: qc, component: SubjectTreeRoute },
    });
    expect(container.textContent).not.toContain("Page not found");
    await unmount(view);
  });

  it("renders NotFoundView inside AttemptResultRoute when attempt id is invalid", async () => {
    for (const invalidId of ["0", "-5", "notanint"]) {
      await navigate("/s/:subject/attempts/:id", {
        params: { subject: "PDSS", id: invalidId },
      });
      const view = mount(AttemptResultRoute, { target: container });
      expect(container.textContent).toContain("Page not found");
      expect(container.textContent).toContain(`/s/PDSS/attempts/${invalidId}`);
      await unmount(view);
    }
  });
});
