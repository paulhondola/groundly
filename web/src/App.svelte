<script lang="ts">
  import { QueryClientProvider } from "@tanstack/svelte-query";
  import { onMount } from "svelte";
  import { Router } from "sv-router";
  import { pageURL } from "./lib/api/client";
  import { queryClient } from "./lib/api/queries";
  import { authStore } from "./lib/auth.svelte";
  import Monitor from "@lucide/svelte/icons/monitor";
  import Moon from "@lucide/svelte/icons/moon";
  import Sun from "@lucide/svelte/icons/sun";
  import Brand from "./lib/components/Brand.svelte";
  import HostPill from "./lib/components/HostPill.svelte";
  import RunStrip from "./lib/components/RunStrip.svelte";
  import Button from "./lib/components/ui/Button.svelte";
  import Card from "./lib/components/ui/Card.svelte";
  import { isActive, parsePositiveInt, route } from "./lib/router";
  import { type ThemeMode, theme } from "./lib/theme.svelte";
  import PairView from "./lib/views/PairView.svelte";

  onMount(() => {
    void authStore.bootstrap();
  });

  const breadcrumb = $derived.by(() => {
    if (isActive("/connections")) {
      return { subject: null, page: "Connections" as const };
    }
    const subject = route.params.subject || null;
    if (!subject) {
      return null;
    }
    if (isActive("/s/:subject")) {
      return { subject, page: null };
    }
    if (isActive("/s/:subject/test")) {
      return { subject, page: "Test" as const };
    }
    if (isActive("/s/:subject/attempts/:id") && parsePositiveInt(route.params.id) !== null) {
      return { subject, page: "Result" as const };
    }
    return null;
  });

  const showDevices = $derived(
    authStore.ready &&
      authStore.access === "loopback" &&
      (authStore.tailscaleEnabled || authStore.pairedDevices > 0),
  );

  const NEXT_THEME: Record<ThemeMode, ThemeMode> = { system: "dark", dark: "light", light: "system" };
  const THEME_WORDS: Record<ThemeMode, string> = { system: "system", dark: "dark", light: "light" };
</script>

<svelte:window
  ononline={() => {
    if (authStore.canRetry) void authStore.bootstrap();
  }}
/>

<QueryClientProvider client={queryClient}>
  <div class="min-h-screen bg-background text-foreground">
    <header class="sticky top-0 z-30 border-b border-border/70 bg-background/90 backdrop-blur">
      <div class="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
        <div class="flex min-w-0 flex-wrap items-center gap-2.5">
          <Brand />
          {#if authStore.ready && authStore.paired && breadcrumb}
            <nav aria-label="Breadcrumb" class="flex min-w-0 items-center gap-2.5 text-sm">
              <span class="text-[var(--status-empty)]" aria-hidden="true">/</span>
              {#if breadcrumb.subject}
                <a
                  href={pageURL(breadcrumb.subject)}
                  class="truncate font-mono text-[13px] text-subtle-foreground hover:text-foreground"
                >
                  {breadcrumb.subject}
                </a>
              {/if}
              {#if breadcrumb.subject && breadcrumb.page}
                <span class="text-[var(--status-empty)]" aria-hidden="true">/</span>
              {/if}
              {#if breadcrumb.page}
                <span class="text-muted-foreground">{breadcrumb.page}</span>
              {/if}
            </nav>
          {/if}
        </div>

        <div class="flex min-w-0 flex-wrap items-center gap-2">
          {#if authStore.ready && authStore.paired}
            <HostPill />
          {/if}
          {#if showDevices}
            <Button variant="outline" href="/connections">
              Devices
              {#if authStore.pairedDevices > 0}
                <span class="rounded-md bg-secondary px-1.5 font-mono text-[11px] text-muted-foreground">
                  {authStore.pairedDevices}
                </span>
              {/if}
            </Button>
          {/if}
          <Button
            variant="outline"
            size="icon"
            aria-label={"Theme: " + THEME_WORDS[theme.mode] + ". Switch to " + THEME_WORDS[NEXT_THEME[theme.mode]]}
            title={"Theme: " + THEME_WORDS[theme.mode]}
            onclick={() => theme.setMode(NEXT_THEME[theme.mode])}
            class="text-muted-foreground"
          >
            {#if theme.mode === "dark"}
              <Moon size={16} aria-hidden="true" />
            {:else if theme.mode === "light"}
              <Sun size={16} aria-hidden="true" />
            {:else}
              <Monitor size={16} aria-hidden="true" />
            {/if}
          </Button>
        </div>
      </div>
    </header>

    <main class="mx-auto max-w-7xl space-y-6 px-4 pt-8 pb-16 sm:px-6 sm:pt-12">
      {#if !authStore.ready}
        <p class="text-sm text-muted-foreground">Connecting…</p>
      {:else if !authStore.paired}
        {#if !authStore.canRetry && authStore.access === "tailscale"}
          <PairView />
        {:else}
          <Card
            class="border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive"
            role="alert"
          >
            {authStore.error ?? "Could not connect to Groundly."}
            <Button variant="outline" size="sm" onclick={() => void authStore.bootstrap()}>
              Retry
            </Button>
          </Card>
        {/if}
      {:else}
        <RunStrip />
        <Router />
      {/if}
    </main>
  </div>
</QueryClientProvider>
