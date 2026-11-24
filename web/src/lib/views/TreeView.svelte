<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { tick } from "svelte";
  import { type TopicNode, apiClient, pageURL, testQuery, unwrap } from "../api/client";
  import { queryKeys } from "../api/queries";
  import MasteryBadge from "../components/MasteryBadge.svelte";
  import SourceItem from "../components/SourceItem.svelte";
  import LiquidMetalButton from "../components/effects/LiquidMetalButton.svelte";
  import Badge from "../components/ui/Badge.svelte";
  import Button from "../components/ui/Button.svelte";
  import Card from "../components/ui/Card.svelte";
  import Input from "../components/ui/Input.svelte";
  import Select from "../components/ui/Select.svelte";
  import { cn } from "../components/ui/utils";
  import { runStore } from "../run.svelte";
  import {
    dedupeSources,
    filterTree,
    findTopicById,
    writeButtonInfo,
    writePrompt,
  } from "./tree-filter";

  interface Props {
    subject: string;
  }

  let { subject }: Props = $props();

  const treeQuery = createQuery(() => ({
    queryKey: queryKeys.tree(subject),
    queryFn: async ({ signal }) =>
      unwrap(
        await apiClient.GET("/api/subjects/{subject}/tree", {
          params: { path: { subject } },
          signal,
        }),
      ),
  }));

  const attemptsQuery = createQuery(() => ({
    queryKey: queryKeys.attempts(subject),
    queryFn: async ({ signal }) =>
      unwrap(
        await apiClient.GET("/api/subjects/{subject}/attempts", {
          params: { path: { subject } },
          signal,
        }),
      ),
  }));

  let query = $state("");
  let selectedTopicId = $state<number | null>(null);
  let selectedCount = $state("10");

  let panelEl = $state<HTMLElement | null>(null);
  let panelHeadingEl = $state<HTMLHeadingElement | null>(null);
  let lastTriggerEl = $state<HTMLButtonElement | null>(null);

  const tree = $derived(treeQuery.data?.tree ?? []);
  const title = $derived(treeQuery.data?.title ?? "");
  const attempts = $derived(attemptsQuery.data ?? []);
  const error = $derived(
    treeQuery.isError && !treeQuery.data
      ? treeQuery.error instanceof Error
        ? treeQuery.error.message
        : "Could not load topic tree"
      : null,
  );

  const visibleTree = $derived(filterTree(tree, query));
  const selectedTopic = $derived(findTopicById(tree, selectedTopicId));
  const hasAnyQuestions = $derived(tree.some((t) => t.progress && t.progress.questions > 0));
  const selectedQuestionCount = $derived(selectedTopic?.progress?.questions ?? 0);
  const selectedWriteInfo = $derived(
    selectedTopic ? writeButtonInfo(runStore.available, runStore.target, selectedTopic) : null,
  );
  const selectedSources = $derived(selectedTopic ? dedupeSources(selectedTopic.sources) : []);

  $effect.pre(() => {
    void subject;
    selectedTopicId = null;
    query = "";
    lastTriggerEl = null;
  });

  $effect(() => {
    void subject;
    void runStore.attach();
  });

  $effect(() => {
    document.title = (title || subject) + " · Groundly";
  });

  async function openTopic(node: TopicNode, event: MouseEvent): Promise<void> {
    const trigger = event.currentTarget;
    if (trigger instanceof HTMLButtonElement) {
      lastTriggerEl = trigger;
    }
    selectedTopicId = node.id;
    const count = node.progress?.questions ?? 0;
    selectedCount = count > 5 ? "10" : "5";
    await tick();
    if (panelEl) {
      panelEl.scrollTop = 0;
    }
    if (panelHeadingEl) {
      panelHeadingEl.tabIndex = -1;
      panelHeadingEl.focus({ preventScroll: true });
    }
  }

  function closePanel(): void {
    selectedTopicId = null;
    lastTriggerEl?.focus();
  }

  function handlePanelKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape" && selectedTopic) {
      event.preventDefault();
      closePanel();
    }
  }

  function chooserOptions(count: number): Array<{ value: string; disabled: boolean }> {
    const lengths = [5, 10, 20];
    let previous = 0;
    return lengths.map((n) => {
      const disabled = previous >= count;
      previous = n;
      return { value: String(n), disabled };
    });
  }
</script>

<div class="space-y-6">
  <div class="flex flex-wrap items-start justify-between gap-4">
    <div class="space-y-1">
      <a
        href="/"
        class="back inline-flex items-center text-xs font-medium text-muted-foreground hover:text-foreground"
      >
        ← All subjects
      </a>
      <h1 class="text-2xl font-semibold tracking-tight text-foreground">{title || subject}</h1>
      {#if title}
        <p class="font-mono text-xs text-muted-foreground">{subject}</p>
      {/if}
    </div>

    {#if hasAnyQuestions}
      <Button href={pageURL(subject, "/test?n=10")} class="primary">
        Test the whole course
      </Button>
    {/if}
  </div>

  {#if treeQuery.isPending}
    <p class="text-sm text-muted-foreground">Loading topic map…</p>
  {:else if error}
    <Card class="border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive" role="alert">
      {error}
    </Card>
  {:else if tree.length === 0}
    <Card class="notice space-y-2 p-6 text-sm">
      <p>
        No map yet. In Claude Code, say:
        <code class="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
          map my {subject} course.
        </code>
      </p>
      <p class="text-muted-foreground">
        This needs the Groundly skills: run
        <code class="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
          groundly skill install
        </code>
        first.
      </p>
    </Card>
  {:else}
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="w-full max-w-sm">
        <Input
          type="search"
          bind:value={query}
          aria-label="Filter topics"
          placeholder="Filter topics or summaries…"
        />
      </div>
      {#if query.trim() !== ""}
        <p class="text-xs text-muted-foreground">
          Showing {visibleTree.length} of {tree.length} topics
        </p>
      {/if}
    </div>

    <div class="layout grid gap-6 md:grid-cols-[minmax(0,1fr)_22rem] md:items-start">
      <div class="space-y-6">
        {#if visibleTree.length === 0}
          <Card class="p-6 text-sm text-muted-foreground">
            No topics match "{query.trim()}".
          </Card>
        {:else}
          <!-- role="list": Safari drops list semantics from list-style: none -->
          <ul class="tree space-y-4" role="list">
            {#each visibleTree as topic (topic.id)}
              <li class="topic rounded-xl border border-border bg-card p-4 shadow-xs">
                <button
                  type="button"
                  aria-pressed={selectedTopicId === topic.id}
                  onclick={(event) => void openTopic(topic, event)}
                  class={cn(
                    "node big flex w-full flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    topic.sources.length === 0 ? "nosrc border-dashed" : "border-transparent",
                    selectedTopicId === topic.id
                      ? "bg-accent text-accent-foreground"
                      : "hover:bg-accent/60",
                  )}
                >
                  <span class="font-semibold text-foreground">{topic.title}</span>
                  <span class="flex flex-wrap items-center gap-2">
                    {#if topic.sources.length === 0}
                      <Badge variant="outline" class="border-dashed">sources removed</Badge>
                    {/if}
                    {#if topic.progress && topic.progress.state !== "not_started"}
                      <MasteryBadge progress={topic.progress} showBar />
                    {/if}
                  </span>
                </button>

                {#if topic.subtopics.length > 0}
                  <ul
                    class="chips mt-3 flex flex-wrap gap-2"
                    role="list"
                    aria-label={"Subtopics of " + topic.title}
                  >
                    {#each topic.subtopics as sub (sub.id)}
                      <li>
                        <button
                          type="button"
                          aria-pressed={selectedTopicId === sub.id}
                          onclick={(event) => void openTopic(sub, event)}
                          class={cn(
                            "node chip inline-flex flex-wrap items-center gap-2 rounded-lg border px-3 py-1.5 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            sub.sources.length === 0 ? "nosrc border-dashed" : "border-border",
                            selectedTopicId === sub.id
                              ? "border-ring bg-accent text-accent-foreground"
                              : "bg-background hover:bg-accent/60",
                          )}
                        >
                          <span>{sub.title}</span>
                          {#if sub.sources.length === 0}
                            <Badge variant="outline" class="border-dashed">sources removed</Badge>
                          {/if}
                          {#if sub.progress && sub.progress.state !== "not_started"}
                            <MasteryBadge progress={sub.progress} />
                          {/if}
                        </button>
                      </li>
                    {/each}
                  </ul>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}

        {#if attempts.length > 0}
          <section class="space-y-3" aria-label="Past tests">
            <h2 class="text-lg font-semibold text-foreground">Past tests</h2>
            <ul class="history space-y-2" role="list">
              {#each attempts as a (a.id)}
                <li class="rounded-lg border border-border bg-card px-4 py-2.5 text-sm">
                  <a
                    href={pageURL(subject, "/attempts/" + Number(a.id))}
                    class="font-medium text-primary underline decoration-border underline-offset-4 hover:decoration-primary"
                  >
                    {a.submitted_at} · {a.scope_title || "Whole course"}
                  </a>
                  <span class="text-muted-foreground">
                    {" — "}
                    {a.score.correct} / {a.score.graded} correct{a.provisional
                      ? ", awaiting review"
                      : ""}
                  </span>
                </li>
              {/each}
            </ul>
          </section>
        {/if}
      </div>

      <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
      <aside
        bind:this={panelEl}
        aria-label="Topic details"
        onkeydown={handlePanelKeydown}
        class={cn(
          "panel rounded-xl border border-border bg-card p-5 text-card-foreground shadow-sm md:sticky md:top-20 md:max-h-[calc(100vh-6rem)] md:overflow-y-auto",
          selectedTopic
            ? "open max-md:fixed max-md:inset-x-0 max-md:bottom-0 max-md:z-40 max-md:max-h-[82vh] max-md:overflow-y-auto max-md:rounded-b-none max-md:shadow-xl"
            : "max-md:hidden",
        )}
      >
        {#if selectedTopic}
          <div class="space-y-4">
            <div class="panel-head flex items-start justify-between gap-3">
              <h2 bind:this={panelHeadingEl} class="text-lg font-semibold text-foreground">
                {selectedTopic.title}
              </h2>
              <Button variant="ghost" size="sm" class="close" onclick={closePanel}>
                Close
              </Button>
            </div>

            <MasteryBadge progress={selectedTopic.progress} showBar />

            {#if selectedQuestionCount > 0}
              <div class="space-y-2">
                <p class="text-sm text-foreground">
                  {selectedQuestionCount}
                  {selectedQuestionCount === 1 ? " question" : " questions"}
                </p>
                <div class="start flex flex-wrap items-center gap-2">
                  <label class="flex items-center gap-2 text-xs text-muted-foreground">
                    <span>Questions</span>
                    <Select bind:value={selectedCount}>
                      {#each chooserOptions(selectedQuestionCount) as opt (opt.value)}
                        <option value={opt.value} disabled={opt.disabled}>{opt.value}</option>
                      {/each}
                    </Select>
                  </label>
                  <Button
                    href={pageURL(subject, testQuery(selectedTopic.id, Number(selectedCount)))}
                    size="sm"
                    class="primary"
                  >
                    Start test
                  </Button>
                </div>
              </div>
            {/if}

            {#if selectedWriteInfo}
              <LiquidMetalButton
                label={selectedWriteInfo.label}
                disabled={runStore.busy}
                onclick={() =>
                  void runStore.start(subject, {
                    kind: "write",
                    topic_id: selectedTopic.id,
                  })}
              />
            {:else if selectedQuestionCount === 0}
              <p class="notice rounded-lg border border-border bg-muted/40 p-3 text-xs leading-relaxed">
                Say to your agent:
                <code class="font-mono text-foreground">
                  {writePrompt(selectedTopic.title, subject)}
                </code>
              </p>
            {/if}

            {#if selectedTopic.summary}
              <p class="summary text-sm leading-relaxed text-foreground">
                {selectedTopic.summary}
              </p>
            {/if}

            <div class="space-y-2">
              <h3 class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                Sources
              </h3>
              {#if selectedSources.length === 0}
                <p class="text-xs text-muted-foreground">
                  sources removed — ask your agent to update the map
                </p>
              {:else}
                <ul class="space-y-2.5" role="list">
                  {#each selectedSources as src (src.chunk_id)}
                    <SourceItem {subject} {src} />
                  {/each}
                </ul>
              {/if}
            </div>
          </div>
        {:else}
          <p class="text-sm text-muted-foreground">
            Select a topic to see its summary and sources.
          </p>
        {/if}
      </aside>
    </div>
  {/if}
</div>
