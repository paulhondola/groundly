import type { ProviderInfo, RunSelection } from "./api/client";

const STORAGE_KEY = "groundly-run-selection";
const PROVIDER_LABELS = { claude: "Claude Code", codex: "Codex" } as const;
type Provider = RunSelection["provider"];
type Selection = Required<RunSelection>;
type Preferences = { active: Provider | null; byProvider: Partial<Record<Provider, Selection>> };
type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;

const isProvider = (id: unknown): id is Provider => id === "claude" || id === "codex";

function readPreferences(storage: Storage | undefined): Preferences {
  const preferences: Preferences = { active: null, byProvider: {} };
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    if (!raw) return preferences;
    const stored: unknown = JSON.parse(raw);
    if (!stored || typeof stored !== "object" || !("version" in stored) || stored.version !== 1) {
      return preferences;
    }
    if ("byProvider" in stored && stored.byProvider && typeof stored.byProvider === "object") {
      const entries = stored.byProvider as Record<string, unknown>;
      for (const id of Object.keys(PROVIDER_LABELS).filter(isProvider)) {
        const pair = entries[id];
        if (!pair || typeof pair !== "object" || !("model" in pair) || !("effort" in pair))
          continue;
        const { model, effort } = pair;
        if (
          (model === null || typeof model === "string") &&
          (effort === null || typeof effort === "string")
        ) {
          preferences.byProvider[id] = { provider: id, model, effort };
        }
      }
    }
    if ("active" in stored && isProvider(stored.active) && preferences.byProvider[stored.active]) {
      preferences.active = stored.active;
    }
  } catch {
    // The current page can still use an agent when storage is blocked or corrupted.
  }
  return preferences;
}

export const providerLabel = (id: Provider): string => PROVIDER_LABELS[id];
export const effortLabel = (effort: string | null | undefined): string =>
  effort ? effort.charAt(0).toUpperCase() + effort.slice(1) : "Default";

export function selectionLabel(selection: RunSelection, provider?: ProviderInfo): string {
  const model = provider?.models?.find((entry) => entry.id === selection.model);
  return [
    provider?.label ?? providerLabel(selection.provider),
    model?.label ?? selection.model ?? "Provider default",
    effortLabel(selection.effort),
  ].join(" · ");
}

export function createRunSelectionStore(storage?: Storage) {
  const saved = readPreferences(storage);
  let configured = $state(saved.active !== null);
  let current = $state<Selection | null>(
    saved.active ? (saved.byProvider[saved.active] ?? null) : null,
  );

  function selectProvider(provider: Provider) {
    if (current?.provider !== provider) {
      current = saved.byProvider[provider] ?? { provider, model: null, effort: null };
    }
  }

  function selectModel(model: string | null, efforts: readonly string[]) {
    if (current) {
      const effort = current.effort && efforts.includes(current.effort) ? current.effort : null;
      current = { ...current, model, effort };
    }
  }

  function selectEffort(effort: string | null) {
    if (current) current = { ...current, effort };
  }

  function issue(providers: readonly ProviderInfo[]): string | null {
    if (!current) return "choose_provider";
    const provider = providers.find((entry) => entry.id === current?.provider);
    if (!provider) return "provider_unavailable";
    if (current.model === null && current.effort === null) return null;
    if (provider.models === null) return "catalog_unavailable";
    const model = provider.models.find(
      (entry) => entry.id === (current?.model ?? provider.default_model),
    );
    return !model || (current.effort !== null && !model.efforts.includes(current.effort))
      ? "invalid_selection"
      : null;
  }

  function rememberLaunch(selection: RunSelection) {
    const accepted = {
      provider: selection.provider,
      model: selection.model ?? null,
      effort: selection.effort ?? null,
    };
    saved.active = accepted.provider;
    saved.byProvider[accepted.provider] = accepted;
    current ??= accepted;
    configured = true;
    try {
      storage?.setItem(STORAGE_KEY, JSON.stringify({ version: 1, ...saved }));
    } catch {
      // The preference remains available in this tab.
    }
  }

  return {
    get current() {
      return current;
    },
    get configured() {
      return configured;
    },
    selectProvider,
    selectModel,
    selectEffort,
    issue,
    rememberLaunch,
  };
}

function browserStorage(): Storage | undefined {
  try {
    return typeof window === "undefined" ? undefined : window.localStorage;
  } catch {
    return undefined;
  }
}

export const runSelection = createRunSelectionStore(browserStorage());
