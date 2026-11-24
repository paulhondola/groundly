import { beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { createRouter, parseRoute, shouldInterceptLink } from "../router.svelte";

describe("parseRoute", () => {
  it("parses root, subject tree, test query, attempt result, and unknown paths", () => {
    expect(parseRoute("/")).toEqual({ kind: "subjects" });
    expect(parseRoute("/s/PDSS")).toEqual({ kind: "tree", subject: "PDSS" });
    expect(parseRoute("/s/CS%20101")).toEqual({ kind: "tree", subject: "CS 101" });
    expect(parseRoute("/s/PDSS/test", "?topic=7&n=10")).toEqual({
      kind: "test",
      subject: "PDSS",
      topicId: 7,
      n: 10,
    });
    expect(parseRoute("/s/PDSS/test", "?n=20")).toEqual({
      kind: "test",
      subject: "PDSS",
      topicId: null,
      n: 20,
    });
    expect(parseRoute("/s/PDSS/attempts/42")).toEqual({
      kind: "result",
      subject: "PDSS",
      attemptId: 42,
    });
    expect(parseRoute("/unknown")).toEqual({ kind: "not_found", pathname: "/unknown" });
    expect(parseRoute("/s/PDSS/extra")).toEqual({
      kind: "not_found",
      pathname: "/s/PDSS/extra",
    });
    expect(parseRoute("/s/PDSS/attempts/notanint")).toEqual({
      kind: "not_found",
      pathname: "/s/PDSS/attempts/notanint",
    });
    expect(parseRoute("/s/%E0%A4%A")).toEqual({
      kind: "not_found",
      pathname: "/s/%E0%A4%A",
    });
  });
});

describe("shouldInterceptLink", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  function makeAnchor(href: string, attrs: Record<string, string> = {}): HTMLAnchorElement {
    const a = document.createElement("a");
    a.href = href;
    for (const [k, v] of Object.entries(attrs)) {
      a.setAttribute(k, v);
    }
    document.body.appendChild(a);
    return a;
  }

  function makeClick(overrides: Partial<MouseEvent> = {}): MouseEvent {
    const event = {
      button: 0,
      metaKey: false,
      ctrlKey: false,
      shiftKey: false,
      altKey: false,
      defaultPrevented: false,
      preventDefault: vi.fn(),
      ...overrides,
    };
    return event as unknown as MouseEvent;
  }

  it("intercepts same-origin navigation links and ignores new-tab, modifier, external, and material PDF links", () => {
    expect(shouldInterceptLink(makeAnchor("/s/PDSS"), makeClick())).toBe(true);
    expect(shouldInterceptLink(makeAnchor("/s/PDSS", { target: "_blank" }), makeClick())).toBe(
      false,
    );
    expect(shouldInterceptLink(makeAnchor("/s/PDSS", { download: "" }), makeClick())).toBe(false);
    expect(shouldInterceptLink(makeAnchor("/s/PDSS"), makeClick({ metaKey: true }))).toBe(false);
    expect(shouldInterceptLink(makeAnchor("/s/PDSS"), makeClick({ ctrlKey: true }))).toBe(false);
    expect(shouldInterceptLink(makeAnchor("/s/PDSS"), makeClick({ shiftKey: true }))).toBe(false);
    expect(shouldInterceptLink(makeAnchor("/s/PDSS"), makeClick({ altKey: true }))).toBe(false);
    expect(shouldInterceptLink(makeAnchor("/s/PDSS"), makeClick({ button: 1 }))).toBe(false);
    expect(shouldInterceptLink(makeAnchor("/s/PDSS"), makeClick({ defaultPrevented: true }))).toBe(
      false,
    );
    expect(
      shouldInterceptLink(makeAnchor(`${location.protocol}//other.example/s/PDSS`), makeClick()),
    ).toBe(false);
    expect(shouldInterceptLink(makeAnchor("/s/PDSS/materials/1"), makeClick())).toBe(false);
  });
});

describe("createRouter.navigate", () => {
  it("scrolls to the top on navigation without a hash and keeps scroll on hash links", () => {
    const scrollSpy = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const r = createRouter();

    r.navigate("/s/PDSS");
    expect(r.route).toEqual({ kind: "tree", subject: "PDSS" });
    expect(scrollSpy).toHaveBeenCalledWith(0, 0);

    scrollSpy.mockClear();
    r.navigate("/s/PDSS#section");
    expect(scrollSpy).not.toHaveBeenCalled();

    r.destroy();
    scrollSpy.mockRestore();
  });
});
