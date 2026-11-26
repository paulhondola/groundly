<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import Search from "@lucide/svelte/icons/search";
  import X from "@lucide/svelte/icons/x";
  import { tick } from "svelte";
  import { type TopicNode, apiClient, pageURL, testQuery, unwrap } from "../api/client";
  import { queryKeys, treeQueryOptions } from "../api/queries";
  import { progressLine, progressRatio } from "../components/MasteryBadge.svelte";
  import MasteryRing from "../components/MasteryRing.svelte";
  import SourceItem from "../components/SourceItem.svelte";
  import StatusDot from "../components/StatusDot.svelte";
  import Button from "../components/ui/Button.svelte";
  import Card from "../components/ui/Card.svelte";
  import Select from "../components/ui/Select.svelte";
  import CopyField from "../components/CopyField.svelte";
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

  const treeQuery = createQuery(() => treeQueryOptions(subject));

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

<div class="space-y-7">
  <div class="flex flex-wrap items-end justify-between gap-4">
    <div class="min-w-0 space-y-2.5">
      <a href="/" class="back text-[13px] text-muted-foreground hover:text-foreground">
        ← All subjects
      </a>
      <h1 class="text-[32px] leading-tight font-semibold tracking-[-0.02em] break-words text-foreground">
        {title || subject}
      </h1>
      {#if title}
        <p class="font-mono text-xs text-subtle-foreground">{subject}</p>
      {/if}
      {#if tree.length > 0}
        <div class="flex flex-wrap items-center gap-4 text-[13px] text-muted-foreground">
          <span class="flex items-center gap-1.5">
            <StatusDot state="mastered" />Mastered
          </span>
          <span class="flex items-center gap-1.5">
            <StatusDot state="in_progress" />Learning
          </span>
          <span class="flex items-center gap-1.5"><StatusDot state="not_started" />Not tested</span>
        </div>
      {/if}
    </div>

    <div class="flex w-full flex-wrap gap-2 sm:w-auto">
      {#if tree.length > 0}
        <label
          class="flex h-10 min-w-0 basis-full items-center gap-2 rounded-[10px] border border-border bg-card px-3 focus-within:ring-2 focus-within:ring-ring sm:w-72 sm:basis-auto"
        >
          <Search size={16} class="shrink-0 text-subtle-foreground" aria-hidden="true" />
          <input
            type="search"
            bind:value={query}
            aria-label="Filter topics"
            placeholder="Filter topics or summaries"
            class="min-w-0 flex-1 bg-transparent text-sm text-foreground placeholder:text-subtle-foreground focus:outline-none"
          />
        </label>
      {/if}
      <Button variant="outline" size="lg" href={"/connections?subject=" + encodeURIComponent(subject)}>
        Share
      </Button>
      {#if hasAnyQuestions}
        <Button size="lg" href={pageURL(subject, "/test?n=10")} class="primary">
          Test the whole course
        </Button>
      {/if}
    </div>
  </div>

  {#if treeQuery.isPending}
    <p class="text-sm text-muted-foreground">Loading topic map…</p>
  {:else if error}
    <Card class="border-destructive/40 p-4 text-sm text-destructive" role="alert">
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
    {#if query.trim() !== ""}
      <p class="-mt-3 text-xs text-muted-foreground">
        Showing {visibleTree.length} of {tree.length} topics
      </p>
    {/if}

    <div class="layout flex flex-wrap items-start gap-6">
      <div class="min-w-0 flex-[999_1_560px] space-y-3">
        {#if visibleTree.length === 0}
          <Card class="p-6 text-sm text-muted-foreground">
            No topics match "{query.trim()}".
          </Card>
        {:else}
          <!-- role="list": Safari drops list semantics from list-style: none -->
          <ul class="tree space-y-3" role="list">
            {#each visibleTree as topic (topic.id)}
              <li
                class={cn(
                  "topic space-y-3.5 rounded-[14px] border p-5 transition-colors",
                  selectedTopicId === topic.id
                    ? "border-ring/50 bg-accent/50 shadow-[0_0_0_4px_color-mix(in_oklab,var(--foreground)_4%,transparent)]"
                    : "border-border bg-card",
                )}
              >
                <button
                  type="button"
                  aria-pressed={selectedTopicId === topic.id}
                  onclick={(event) => void openTopic(topic, event)}
                  class={cn(
                    "node big flex w-full flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-lg text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4 focus-visible:ring-offset-card focus-visible:outline-none",
                    topic.sources.length === 0 && "nosrc",
                  )}
                >
                  <span class="text-base font-medium text-foreground">{topic.title}</span>
                  <span class="text-xs text-subtle-foreground">
                    {#if topic.sources.length === 0}
                      sources removed
                    {:else}
                      {progressLine(topic.progress)}
                    {/if}
                  </span>
                </button>

                {#if topic.subtopics.length > 0}
                  <ul
                    class="chips flex flex-wrap gap-2"
                    role="list"
                    aria-label={"Subtopics of " + topic.title}
                  >
                    {#each topic.subtopics as sub (sub.id)}
                      <li>
                        <button
                          type="button"
                          aria-pressed={selectedTopicId === sub.id}
                          title={progressLine(sub.progress)}
                          onclick={(event) => void openTopic(sub, event)}
                          class={cn(
                            "node chip inline-flex min-h-9 items-center gap-2 rounded-[10px] border px-3 py-1.5 text-left text-sm text-foreground transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                            sub.sources.length === 0 ? "nosrc border-dashed" : "",
                            selectedTopicId === sub.id
                              ? "border-foreground/60 bg-accent"
                              : "border-border bg-background hover:border-ring/40 hover:bg-accent",
                          )}
                        >
                          <StatusDot state={sub.progress?.state} />
                          <span>{sub.title}</span>
                          <span class="sr-only">, {progressLine(sub.progress)}</span>
                          {#if sub.sources.length === 0}
                            <span class="text-xs text-subtle-foreground">sources removed</span>
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
          <section class="space-y-3 pt-5" aria-label="Past tests">
            <h2 class="text-[13px] font-medium tracking-[0.08em] text-subtle-foreground uppercase">
              Past tests
            </h2>
            <ul class="history divide-y divide-border overflow-hidden rounded-[14px] border border-border bg-card" role="list">
              {#each attempts as a (a.id)}
                <li>
                  <a
                    href={pageURL(subject, "/attempts/" + Number(a.id))}
                    class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-3 text-sm hover:bg-accent/60"
                  >
                    <span class="text-foreground">
                      {a.scope_title || "Whole course"}
                      <span class="ml-2 font-mono text-xs text-subtle-foreground">{a.submitted_at}</span>
                    </span>
                    <span class="text-muted-foreground">
                      {a.score.correct} / {a.score.graded} correct{a.provisional
                        ? ", awaiting review"
                        : ""}
                    </span>
                  </a>
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
          "panel min-w-0 flex-[1_1_360px] rounded-[14px] border border-border bg-card p-6 text-card-foreground md:sticky md:top-24 md:max-h-[calc(100vh-7rem)] md:max-w-[420px] md:overflow-y-auto",
          selectedTopic
            ? "open max-md:fixed max-md:inset-x-0 max-md:bottom-0 max-md:z-40 max-md:max-h-[82vh] max-md:overflow-y-auto max-md:rounded-b-none max-md:shadow-2xl"
            : "max-md:hidden",
        )}
      >
        {#if selectedTopic}
          <div class="space-y-5">
            <div class="panel-head flex items-start justify-between gap-3">
              <h2
                bind:this={panelHeadingEl}
                class="text-xl leading-snug font-semibold tracking-[-0.01em] text-foreground focus:outline-none"
              >
                {selectedTopic.title}
              </h2>
              <Button
                variant="ghost"
                size="icon"
                class="close shrink-0 text-muted-foreground"
                aria-label="Close panel"
                onclick={closePanel}
              >
                <X size={16} aria-hidden="true" />
              </Button>
            </div>

            <div class="flex items-center gap-3.5">
              <MasteryRing ratio={progressRatio(selectedTopic.progress) / 100} size={44} label={progressLine(selectedTopic.progress)} />
              <div class="flex flex-col gap-0.5">
                <span class="text-[15px] text-foreground">{progressLine(selectedTopic.progress)}</span>
                <span class="text-[13px] text-muted-foreground">
                  {selectedQuestionCount}
                  {selectedQuestionCount === 1 ? " question" : " questions"} in the bank
                </span>
              </div>
            </div>

            {#if selectedQuestionCount > 0}
              <div class="start flex gap-2">
                <label
                  class="flex h-11 items-center gap-2 rounded-[10px] border border-border bg-background pl-3 text-[13px] text-muted-foreground"
                >
                  <span>Questions</span>
                  <Select bind:value={selectedCount} class="h-10 border-0 bg-transparent shadow-none">
                    {#each chooserOptions(selectedQuestionCount) as opt (opt.value)}
                      <option value={opt.value} disabled={opt.disabled}>{opt.value}</option>
                    {/each}
                  </Select>
                </label>
                <Button
                  href={pageURL(subject, testQuery(selectedTopic.id, Number(selectedCount)))}
                  size="lg"
                  class="primary flex-1"
                >
                  Start test
                </Button>
              </div>
            {/if}

            {#if selectedWriteInfo}
              <Button
                variant="outline"
                size="lg"
                class="w-full"
                disabled={runStore.busy}
                onclick={() =>
                  void runStore.start(subject, {
                    kind: "write",
                    topic_id: selectedTopic.id,
                  })}
              >
                {selectedWriteInfo.label}
              </Button>
            {:else if selectedQuestionCount === 0}
              <div class="notice space-y-2 rounded-xl border border-border bg-well p-4 text-[13px] text-muted-foreground">
                <p>Say to your agent:</p>
                <CopyField text={writePrompt(selectedTopic.title, subject)} label="Copy prompt" />
              </div>
            {/if}

            {#if selectedTopic.summary}
              <p class="summary text-sm leading-relaxed text-foreground/85">
                {selectedTopic.summary}
              </p>
            {/if}

            <div class="space-y-2.5">
              <h3 class="text-xs font-medium tracking-[0.08em] text-subtle-foreground uppercase">
                Sources
              </h3>
              {#if selectedSources.length === 0}
                <p class="text-xs text-muted-foreground">
                  sources removed — ask your agent to update the map
                </p>
              {:else}
                <ul class="divide-y divide-border overflow-hidden rounded-[10px] border border-border" role="list">
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
