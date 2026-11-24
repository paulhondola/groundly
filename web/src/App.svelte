<script lang="ts">
  import { pageURL } from "./lib/api";
  import Brand from "./lib/components/Brand.svelte";
  import RunStrip from "./lib/components/RunStrip.svelte";
  import Button from "./lib/components/ui/Button.svelte";
  import Card from "./lib/components/ui/Card.svelte";
  import Select from "./lib/components/ui/Select.svelte";
  import { router } from "./lib/router.svelte";
  import { type ThemeMode, theme } from "./lib/theme.svelte";
  import ResultView from "./lib/views/ResultView.svelte";
  import SubjectsView from "./lib/views/SubjectsView.svelte";
  import TestView from "./lib/views/TestView.svelte";
  import TreeView from "./lib/views/TreeView.svelte";

  const route = $derived(router.route);

  function onThemeChange(event: Event): void {
    const select = event.currentTarget;
    if (select instanceof HTMLSelectElement) {
      theme.setMode(select.value as ThemeMode);
    }
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="min-h-screen bg-background text-foreground" onclick={(e) => router.interceptClick(e)}>
  <header class="sticky top-0 z-30 border-b border-border bg-background/90 backdrop-blur">
    <div class="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-3 sm:px-6">
      <div class="flex flex-wrap items-center gap-3">
        <Brand />
        {#if route.kind !== "subjects" && route.kind !== "not_found"}
          <nav aria-label="Breadcrumb" class="flex items-center gap-2 text-xs text-muted-foreground">
            <span>/</span>
            <a href={pageURL(route.subject)} class="hover:text-foreground">{route.subject}</a>
            {#if route.kind === "test"}
              <span>/</span>
              <span class="text-foreground">Test</span>
            {:else if route.kind === "result"}
              <span>/</span>
              <span class="text-foreground">Result</span>
            {/if}
          </nav>
        {/if}
      </div>

      <label class="flex items-center gap-2 text-xs text-muted-foreground">
        <span>Theme</span>
        <Select value={theme.mode} aria-label="Theme" onchange={onThemeChange}>
          <option value="system">System</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </Select>
      </label>
    </div>
  </header>

  <main class="mx-auto max-w-6xl space-y-6 px-4 py-6 sm:px-6">
    <RunStrip />

    {#if route.kind === "subjects"}
      <SubjectsView />
    {:else if route.kind === "tree"}
      <TreeView subject={route.subject} />
    {:else if route.kind === "test"}
      <TestView subject={route.subject} topicId={route.topicId} n={route.n} />
    {:else if route.kind === "result"}
      <ResultView subject={route.subject} attemptId={route.attemptId} />
    {:else}
      <Card class="mx-auto max-w-md space-y-3 p-6">
        <h1 class="text-lg font-semibold text-foreground">Page not found</h1>
        <p class="text-sm text-muted-foreground">
          No view exists for <code class="font-mono text-foreground">{route.pathname}</code>.
        </p>
        <Button href="/" size="sm">All subjects</Button>
      </Card>
    {/if}
  </main>
</div>
