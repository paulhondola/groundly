import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it } from "vite-plus/test";
import SourceItem from "../SourceItem.svelte";

describe("SourceItem passage toggle", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    container.remove();
  });

  it("renders a Passage button beside PDF links when src.text is non-empty and reveals plain-text <pre>", () => {
    const snippet = 'Mutex rules: <script>alert("xss")</script> & <b>plain</b>';
    const inst = mount(SourceItem, {
      target: container,
      props: {
        subject: "PDSS",
        src: {
          chunk_id: 1,
          material_id: 3,
          filename: "slides.pdf",
          page: 4,
          heading_path: null,
          text: snippet,
          pdf: true,
        },
      },
    });
    flushSync();

    const anchor = container.querySelector("a");
    expect(anchor?.getAttribute("target")).toBe("_blank");
    expect(anchor?.getAttribute("href")).toBe("/s/PDSS/materials/3#page=4");

    const button = container.querySelector("button");
    expect(button).not.toBeNull();
    expect(button?.textContent?.trim()).toBe("Passage");
    expect(button?.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector("pre")).toBeNull();

    button?.click();
    flushSync();

    expect(button?.getAttribute("aria-expanded")).toBe("true");
    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toBe(snippet);
    expect(pre?.querySelector("*")).toBeNull();

    button?.click();
    flushSync();

    expect(button?.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector("pre")).toBeNull();

    void unmount(inst);
  });

  it("omits the Passage button on PDF links when src.text is blank", () => {
    const inst = mount(SourceItem, {
      target: container,
      props: {
        subject: "PDSS",
        src: {
          chunk_id: 2,
          material_id: 3,
          filename: "slides.pdf",
          page: 4,
          heading_path: null,
          text: "   \n\t  ",
          pdf: true,
        },
      },
    });
    flushSync();

    const anchor = container.querySelector("a");
    expect(anchor?.getAttribute("target")).toBe("_blank");
    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector("pre")).toBeNull();

    void unmount(inst);
  });
});
