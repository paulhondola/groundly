export type ThemeMode = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "groundly-theme";

function readStoredMode(): ThemeMode {
  if (typeof window === "undefined") {
    return "system";
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "light" || raw === "dark" || raw === "system") {
      return raw;
    }
  } catch {
    /* storage unavailable */
  }
  return "system";
}

function readSystemDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function createThemeStore() {
  let mode = $state<ThemeMode>(readStoredMode());
  let systemDark = $state<boolean>(readSystemDark());
  const resolved = $derived<ResolvedTheme>(
    mode === "system" ? (systemDark ? "dark" : "light") : mode,
  );

  function applyDocumentClass(next: ResolvedTheme): void {
    if (typeof document === "undefined") {
      return;
    }
    document.documentElement.classList.toggle("dark", next === "dark");
  }

  applyDocumentClass(resolved);

  let mediaQuery: MediaQueryList | null = null;
  function onMediaChange(event: MediaQueryListEvent): void {
    systemDark = event.matches;
    applyDocumentClass(resolved);
  }

  if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
    mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    mediaQuery.addEventListener("change", onMediaChange);
  }

  function setMode(next: ThemeMode): void {
    mode = next;
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        /* storage unavailable */
      }
    }
    applyDocumentClass(resolved);
  }

  function destroy(): void {
    mediaQuery?.removeEventListener("change", onMediaChange);
  }

  return {
    get mode() {
      return mode;
    },
    get resolved() {
      return resolved;
    },
    setMode,
    destroy,
  };
}

export const theme = createThemeStore();
