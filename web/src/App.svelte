<script lang="ts">
  import { QueryClientProvider } from "@tanstack/svelte-query";
  import { onMount } from "svelte";
  import { Router } from "sv-router";
  import { pageURL } from "./lib/api/client";
  import { queryClient } from "./lib/api/queries";
  import { authStore } from "./lib/auth.svelte";
  import Brand from "./lib/components/Brand.svelte";
  import RemoteDialog from "./lib/components/RemoteDialog.svelte";
  import RunStrip from "./lib/components/RunStrip.svelte";
  import Button from "./lib/components/ui/Button.svelte";
  import Card from "./lib/components/ui/Card.svelte";
  import Select from "./lib/components/ui/Select.svelte";
  import { isActive, parsePositiveInt, route } from "./lib/router";
  import { type ThemeMode, theme } from "./lib/theme.svelte";
  import PairView from "./lib/views/PairView.svelte";

  onMount(() => {
    void authStore.bootstrap();
  });

  const breadcrumb = $derived.by(() => {
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

  const showRemoteDialog = $derived(
    authStore.ready &&
      authStore.access === "loopback" &&
      (authStore.tailscaleEnabled || authStore.pairedDevices > 0),
  );

  function onThemeChange(event: Event): void {
    const select = event.currentTarget;
    if (select instanceof HTMLSelectElement) {
      theme.setMode(select.value as ThemeMode);
    }
  }
</script>

<svelte:window
  ononline={() => {
    if (authStore.canRetry) void authStore.bootstrap();
  }}
/>

<QueryClientProvider client={queryClient}>
  <div class="min-h-screen bg-background text-foreground">
    <header class="sticky top-0 z-30 border-b border-border bg-background/90 backdrop-blur">
      <div class="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-3 sm:px-6">
        <div class="flex flex-wrap items-center gap-3">
          <Brand />
          {#if authStore.ready && authStore.paired && breadcrumb}
            <nav aria-label="Breadcrumb" class="flex items-center gap-2 text-xs text-muted-foreground">
              <span>/</span>
              <a href={pageURL(breadcrumb.subject)} class="hover:text-foreground">
                {breadcrumb.subject}
              </a>
              {#if breadcrumb.page}
                <span>/</span>
                <span class="text-foreground">{breadcrumb.page}</span>
              {/if}
            </nav>
          {/if}
        </div>

        <div class="flex items-center gap-3">
          {#if showRemoteDialog}
            <RemoteDialog pairedCount={authStore.pairedDevices} />
          {/if}

          <label class="flex items-center gap-2 text-xs text-muted-foreground">
            <span>Theme</span>
            <Select value={theme.mode} aria-label="Theme" onchange={onThemeChange}>
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </Select>
          </label>
        </div>
      </div>
    </header>

    <main class="mx-auto max-w-6xl space-y-6 px-4 py-6 sm:px-6">
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
