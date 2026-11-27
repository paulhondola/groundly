<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import Search from "@lucide/svelte/icons/search";
  import { useActivity } from "../activity.svelte";
  import { apiClient, pageURL, unwrap } from "../api/client";
  import { queryKeys } from "../api/queries";
  import CopyField from "../components/CopyField.svelte";
  import MasteryRing from "../components/MasteryRing.svelte";
  import RunAction from "../components/RunAction.svelte";
  import Orb from "../components/orb/Orb.svelte";
  import Card from "../components/ui/Card.svelte";
  import { runStore } from "../run.svelte";
  import { ago } from "../time";

  const subjectsQuery = createQuery(() => ({
    queryKey: queryKeys.subjects(),
    queryFn: async ({ signal }) => unwrap(await apiClient.GET("/api/subjects", { signal })),
  }));
  const activity = useActivity();

  let filter = $state("");
  let seenActivity: string | undefined;

  const subjects = $derived(subjectsQuery.data ?? []);
  const error = $derived(
    subjectsQuery.isError && !subjectsQuery.data
      ? subjectsQuery.error instanceof Error
        ? subjectsQuery.error.message
        : "Could not load subjects"
      : null,
  );
  const visible = $derived.by(() => {
    const needle = filter.trim().toLowerCase();
    return needle === ""
      ? subjects
      : subjects.filter(
          (s) =>
            s.subject.toLowerCase().includes(needle) || s.title.toLowerCase().includes(needle),
        );
  });
  const ready = $derived(visible.filter((s) => s.has_tree && !s.error));
  const unmapped = $derived(visible.filter((s) => !s.has_tree || s.error));
  const mappedCount = $derived(subjects.filter((s) => s.has_tree).length);
  const mapping = $derived(
    new Set([
      ...activity.hosts.filter((h) => h.orb === "mapping").map((h) => h.subject),
      ...(runStore.run?.kind === "map" && runStore.run.status === "running"
        ? [runStore.run.subject]
        : []),
    ]),
  );
  const completedMap = $derived(
    runStore.watchedRun?.kind === "map" && runStore.watchedRun.status !== "running"
      ? ready.find((s) => s.subject === runStore.watchedRun?.subject)
      : undefined,
  );

  $effect(() => {
    document.title = "Groundly";
    void runStore.attach();
  });

  $effect(() => {
    const latest = activity.recent[0];
    const signature = latest && latest.at + latest.subject + latest.text;
    if (signature && signature !== seenActivity) {
      seenActivity = signature;
      void subjectsQuery.refetch();
    }
  });
</script>

<div class="space-y-8">
  <div class="flex flex-wrap items-end justify-between gap-4">
    <div class="space-y-2">
      <h1 class="text-[32px] leading-tight font-semibold tracking-[-0.02em] text-foreground">
        Subjects
      </h1>
      {#if subjects.length > 0}
        <p class="text-[15px] text-muted-foreground">
          {subjects.length}
          {subjects.length === 1 ? "subject" : "subjects"} · {mappedCount} with a topic map
        </p>
      {/if}
    </div>
    {#if subjects.length > 0}
      <label
        class="flex h-10 w-full items-center gap-2 rounded-[10px] border border-border bg-card px-3 focus-within:ring-2 focus-within:ring-ring sm:w-72"
      >
        <Search size={16} class="shrink-0 text-subtle-foreground" aria-hidden="true" />
        <span class="sr-only">Search subjects</span>
        <input
          type="search"
          bind:value={filter}
          placeholder="Search subjects"
          class="min-w-0 flex-1 bg-transparent text-sm text-foreground placeholder:text-subtle-foreground focus:outline-none"
        />
      </label>
    {/if}
  </div>

  {#if subjectsQuery.isPending}
    <p class="text-sm text-muted-foreground">Loading subjects…</p>
  {:else if error}
    <Card class="border-destructive/40 p-4 text-sm text-destructive" role="alert">
      {error}
    </Card>
  {:else if subjects.length === 0}
    <Card class="space-y-3 p-6 text-sm text-muted-foreground">
      <p>No subjects yet. Index a folder of course files:</p>
      <CopyField text="groundly index <folder>" class="max-w-md" />
    </Card>
  {:else if visible.length === 0}
    <p class="text-sm text-muted-foreground">No subjects match "{filter.trim()}".</p>
  {:else}
    {#if completedMap && !runStore.dismissed && !runStore.watchedRun?.map_written}
      <a
        href={pageURL(completedMap.subject)}
        class="inline-flex rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >Open topic map</a>
    {/if}
    {#if ready.length > 0}
      <section class="space-y-3.5" aria-labelledby="ready-heading">
        <h2
          id="ready-heading"
          class="text-[13px] font-medium tracking-[0.08em] text-subtle-foreground uppercase"
        >
          Ready to study
        </h2>
        <ul class="subjects grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(280px,1fr))]" role="list">
          {#each ready as s (s.subject)}
            {@const leaves = s.leaves ?? 0}
            {@const mastered = s.leaves_mastered ?? 0}
            <li>
              <a
                href={pageURL(s.subject)}
                class="flex h-full flex-col gap-[18px] rounded-[14px] border border-border bg-card p-[22px] transition-colors hover:border-ring/40 hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                <div class="flex items-start justify-between gap-3">
                  <div class="flex min-w-0 flex-col gap-1.5">
                    <span class="text-base leading-snug font-medium break-words text-foreground">
                      {s.title || s.subject}
                    </span>
                    <span class="font-mono text-xs text-subtle-foreground">{s.subject}</span>
                  </div>
                  <MasteryRing
                    ratio={leaves > 0 ? mastered / leaves : 0}
                    label={mastered + " of " + leaves + " mastered"}
                  />
                </div>
                <div class="flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-muted-foreground">
                  {#if s.topics != null}
                    <span>{s.topics} {s.topics === 1 ? "topic" : "topics"}</span>
                  {/if}
                  {#if s.leaves != null}
                    <span>{mastered} of {leaves} mastered</span>
                  {/if}
                  <span>{s.last_test_at ? "Last test " + ago(s.last_test_at) : "Not tested yet"}</span>
                </div>
              </a>
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    {#if unmapped.length > 0}
      <section class="space-y-3.5" aria-labelledby="unmapped-heading">
        <h2
          id="unmapped-heading"
          class="text-[13px] font-medium tracking-[0.08em] text-subtle-foreground uppercase"
        >
          Indexed, no map yet
        </h2>
        <ul class="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(280px,1fr))]" role="list">
          {#each unmapped as s (s.subject)}
            <li
              class="flex h-full flex-col gap-4 rounded-[14px] border border-dashed border-ring/30 p-[22px]"
            >
              <div class="flex items-center justify-between gap-3">
                <a
                  href={pageURL(s.subject)}
                  class="min-w-0 text-base font-medium break-words text-foreground hover:underline"
                >
                  {s.title || s.subject}
                </a>
                {#if mapping.has(s.subject)}
                  <Orb mode="mapping" size={32} label="Building the topic map" />
                {:else}
                  <span class="font-mono text-xs text-subtle-foreground">{s.subject}</span>
                {/if}
              </div>
              {#if s.error}
                <p class="text-[13px] text-destructive" role="alert">{s.error}</p>
              {:else if mapping.has(s.subject)}
                <p class="shimmer text-[13px]">Grouping pages into topics…</p>
              {:else}
                {#if s.can_map && runStore.available}
                  <RunAction subject={s.subject} kind="map" label="Create topic map" scopeLabel={s.title || s.subject} />
                {:else if !s.can_map}
                  <p class="text-xs text-muted-foreground">Index course materials to create a topic map.</p>
                {/if}
                <CopyField text={"Map my " + s.subject + " course."} label="Copy prompt" />
                <span class="text-xs text-subtle-foreground">
                  Paste into your MCP host (needs <code class="font-mono">groundly skill install</code>)
                </span>
              {/if}
            </li>
          {/each}
        </ul>
      </section>
    {/if}
  {/if}
</div>
