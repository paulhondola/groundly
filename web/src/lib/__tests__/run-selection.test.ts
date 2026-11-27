import { describe, expect, it } from "vite-plus/test";
import { createRunSelectionStore } from "../run-selection.svelte";

const providers = [
  {
    id: "claude",
    label: "Claude Code",
    models: [{ id: "sonnet", label: "Sonnet", efforts: ["low", "high"] }],
  },
  {
    id: "codex",
    label: "Codex",
    models: [{ id: "sample", label: "Sample", efforts: ["medium"] }],
  },
] as const;

function memoryStorage() {
  const items = new Map<string, string>();
  return {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => items.set(key, value),
  };
}

describe("run selection", () => {
  it("restores the last launched configuration for each provider", () => {
    const memory = memoryStorage();
    const state = createRunSelectionStore(memory);
    state.selectProvider("claude");
    state.selectModel("sonnet", ["low", "high"]);
    state.selectEffort("high");
    state.rememberLaunch({ provider: "claude", model: "sonnet", effort: "high" });
    state.selectProvider("codex");
    expect(state.current).toEqual({ provider: "codex", model: null, effort: null });
    state.rememberLaunch({ provider: "codex", model: "sample", effort: "medium" });
    const reloaded = createRunSelectionStore(memory);
    expect(reloaded.current).toEqual({ provider: "codex", model: "sample", effort: "medium" });
    reloaded.selectProvider("claude");
    expect(reloaded.current).toEqual({ provider: "claude", model: "sonnet", effort: "high" });
  });

  it("distinguishes temporarily missing metadata from a removed explicit model", () => {
    const state = createRunSelectionStore(memoryStorage());
    state.rememberLaunch({ provider: "claude", model: "sonnet", effort: "high" });
    expect(state.issue([{ ...providers[0], models: null }])).toBe("catalog_unavailable");
    expect(state.issue([{ ...providers[0], models: [] }])).toBe("invalid_selection");
    expect(state.current?.model).toBe("sonnet");
    state.selectModel(null, []);
    expect(state.current?.effort).toBeNull();
    expect(state.issue([{ ...providers[0], models: null }])).toBeNull();
  });

  it("keeps staged edits out of storage until a launch is accepted", () => {
    const memory = memoryStorage();
    const state = createRunSelectionStore(memory);
    state.rememberLaunch({ provider: "claude", model: "sonnet", effort: "high" });
    state.selectEffort("low");
    expect(createRunSelectionStore(memory).current?.effort).toBe("high");
  });
});
