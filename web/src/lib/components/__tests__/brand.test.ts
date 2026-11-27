import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it } from "vite-plus/test";
import Brand from "../Brand.svelte";

describe("Brand", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    container.remove();
  });

  it("shows the dark-stroke mark in light theme and the light-stroke mark under .dark", () => {
    const inst = mount(Brand, { target: container });
    const imgs = [...container.querySelectorAll("img")];
    expect(imgs).toHaveLength(2);
    const [light, dark] = imgs;
    expect(light.getAttribute("src")).toBe("/static/groundly-mark-light.svg");
    expect(light.classList.contains("dark:hidden")).toBe(true);
    expect(dark.getAttribute("src")).toBe("/static/groundly-mark-dark.svg");
    expect(dark.classList.contains("hidden")).toBe(true);
    expect(dark.classList.contains("dark:block")).toBe(true);
    for (const img of imgs) expect(img.getAttribute("alt")).toBe("");
    expect(container.innerHTML).not.toContain("claude-code.svg");
    unmount(inst);
  });
});
