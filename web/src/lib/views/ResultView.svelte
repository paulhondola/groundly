<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { type AnswerState, apiClient, pageURL, unwrap } from "../api/client";
  import { queryKeys, treeQueryOptions } from "../api/queries";
  import MasteryBadge from "../components/MasteryBadge.svelte";
  import CopyField from "../components/CopyField.svelte";
  import RunAction from "../components/RunAction.svelte";
  import SourceItem from "../components/SourceItem.svelte";
  import Badge, { type BadgeVariant } from "../components/ui/Badge.svelte";
  import Card from "../components/ui/Card.svelte";
  import { cn } from "../components/ui/utils";
  import { runStore } from "../run.svelte";
  import { findTopicById } from "./tree-filter";

  interface Props {
    subject: string;
    attemptId: number;
  }

  const STATE_WORDS: Record<AnswerState, string> = {
    correct: "Correct",
    wrong: "Wrong",
    awaiting: "Awaiting review",
    unreviewable: "Not reviewed",
  };

  function badgeVariantFor(state: AnswerState): BadgeVariant {
    if (state === "correct") {
      return "mastered";
    }
    if (state === "wrong") {
      return "destructive";
    }
    if (state === "awaiting") {
      return "awaiting";
    }
    return "secondary";
  }

  let { subject, attemptId }: Props = $props();

  const attemptQuery = createQuery(() => ({
    queryKey: queryKeys.attempt(subject, attemptId),
    queryFn: async ({ signal }) =>
      unwrap(
        await apiClient.GET("/api/subjects/{subject}/attempts/{id}", {
          params: { path: { subject, id: attemptId } },
          signal,
        }),
      ),
  }));

  const treeQuery = createQuery(() => treeQueryOptions(subject));

  const result = $derived(attemptQuery.data ?? null);
  const courseTitle = $derived(treeQuery.data ? treeQuery.data.title || subject : subject);
  const scopeTitle = $derived(
    !result || result.scope_topic_id == null
      ? "Whole course"
      : treeQuery.data
        ? (findTopicById(treeQuery.data.tree, result.scope_topic_id)?.title ?? "Removed topic")
        : "Whole course",
  );
  // Read both getters unconditionally so TanStack Query v6 tracks treeQuery.isPending on mount.
  const pending = $derived.by(() => {
    const attemptPending = attemptQuery.isPending;
    const treePending = treeQuery.isPending;
    return attemptPending || treePending;
  });
  const error = $derived(
    attemptQuery.isError && !attemptQuery.data
      ? attemptQuery.error instanceof Error
        ? attemptQuery.error.message
        : "Could not load attempt result"
      : treeQuery.isError && !treeQuery.data
        ? treeQuery.error instanceof Error
          ? treeQuery.error.message
          : "Could not load attempt result"
        : null,
  );
  const loading = $derived(pending && !error);

  const canGrade = $derived(Boolean(result?.provisional && runStore.available));

  $effect(() => {
    void subject;
    void attemptId;
    void runStore.attach();
  });

  $effect(() => {
    if (result) {
      document.title = "Result · " + courseTitle + " · Groundly";
    }
  });
</script>

<div class="mx-auto max-w-3xl space-y-6">
  <div class="space-y-1">
    <a
      href={pageURL(subject)}
      class="back inline-flex items-center text-[13px] text-muted-foreground hover:text-foreground"
    >
      ← Back to the map
    </a>
    {#if !loading && !error && result}
      <h1 class="text-[32px] leading-tight font-semibold tracking-[-0.02em] text-foreground">{courseTitle}</h1>
      <p class="text-sm text-muted-foreground">{scopeTitle} · {result.submitted_at}</p>
    {/if}
  </div>

  {#if loading}
    <p class="text-sm text-muted-foreground">Loading results…</p>
  {:else if error}
    <Card class="border-destructive/40 p-4 text-sm text-destructive" role="alert">
      {error}
    </Card>
  {:else if result}
    <Card class="space-y-3 p-6">
      <p class="score text-[32px] font-semibold tracking-[-0.02em] text-foreground">
        {result.score.correct} / {result.score.graded} correct
      </p>
      {#if result.provisional}
        <div class="start flex flex-wrap items-center justify-between gap-3 pt-1">
          <p class="text-sm text-muted-foreground">
            provisional, {result.score.awaiting} awaiting review
          </p>
          {#if canGrade}
            <RunAction {subject} kind="grade" label="Grade now" scopeLabel={courseTitle} />
          {:else}
            <CopyField text={"Grade my written answers for " + subject + "."} label="Copy prompt" />
          {/if}
        </div>
      {/if}
    </Card>

    <div class="space-y-4">
      {#each result.answers as a, i (a.answer_id)}
        <article
          class={cn(
            "answer rounded-[14px] border border-border bg-card p-5",
            "state-" + a.state,
          )}
        >
          <div class="answer-head flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-center gap-2">
              <strong class="text-sm font-semibold text-foreground">{i + 1}.</strong>
              <Badge variant={badgeVariantFor(a.state)}>{STATE_WORDS[a.state]}</Badge>
            </div>
            <span class="font-mono text-xs text-muted-foreground">Question #{a.question_id}</span>
          </div>

          {#if a.removed}
            <p class="mt-3 text-sm text-muted-foreground">Question removed</p>
            {#if a.state === "unreviewable"}
              <p class="mt-1 text-xs text-muted-foreground">
                Cannot be reviewed — the question was removed.
              </p>
            {/if}
          {:else}
            <p class="stem mt-3 text-sm font-medium leading-relaxed text-foreground">{a.stem}</p>
            {#if a.code}
              <pre
                class="code mt-3 overflow-x-auto rounded-lg border border-border bg-muted/60 p-3 font-mono text-xs leading-relaxed text-foreground"
              >{a.code}</pre>
            {/if}

            {#if a.kind === "open"}
              <div class="mt-4 space-y-3 text-sm">
                <div>
                  <h4 class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                    Your answer
                  </h4>
                  <p class="said mt-1 whitespace-pre-wrap text-foreground">
                    {a.text && a.text.trim() ? a.text : "No answer"}
                  </p>
                </div>
                <div>
                  <h4 class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                    Reference answer
                  </h4>
                  <p class="said mt-1 whitespace-pre-wrap text-foreground">{a.reference_answer}</p>
                </div>
                {#if a.state === "awaiting"}
                  {#if !canGrade}
                    <p class="text-xs text-muted-foreground">
                      Awaiting review — say
                      <code class="rounded bg-muted px-1.5 py-0.5 font-mono text-foreground">
                        grade my test
                      </code>
                      to your agent.
                    </p>
                  {/if}
                {:else if a.comment}
                  <div>
                    <h4 class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                      Review
                    </h4>
                    <p class="said mt-1 whitespace-pre-wrap text-foreground">{a.comment}</p>
                  </div>
                {/if}
              </div>
            {:else}
              {@const yours = a.options.filter((o) => o.selected).map((o) => o.text)}
              {@const right = a.options.filter((o) => o.correct).map((o) => o.text)}
              <div class="mt-4 grid gap-3 text-sm sm:grid-cols-2">
                <div>
                  <h4 class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                    Your answer
                  </h4>
                  <p class="said mt-1 text-foreground">
                    {yours.length ? yours.join("; ") : "No answer"}
                  </p>
                </div>
                <div>
                  <h4 class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                    Correct answer
                  </h4>
                  <p class="said mt-1 text-foreground">{right.join("; ")}</p>
                </div>
              </div>
            {/if}

            {#if a.sources.length > 0}
              <details class="mt-4 border-t border-border/70 pt-3">
                <summary class="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">
                  Sources ({a.sources.length})
                </summary>
                <ul class="sources mt-2 divide-y divide-border overflow-hidden rounded-[10px] border border-border" role="list">
                  {#each a.sources as src (src.chunk_id)}
                    <SourceItem {subject} {src} />
                  {/each}
                </ul>
              </details>
            {/if}
          {/if}
        </article>
      {/each}
    </div>

    {#if result.topics.length > 0}
      <section class="space-y-3" aria-label="Topics">
        <h3 class="text-lg font-semibold text-foreground">Topics</h3>
        <ul class="history space-y-2" role="list">
          {#each result.topics as t (t.id)}
            <li class="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-card px-4 py-2.5 text-sm">
              <span class="font-medium text-foreground">{t.title}</span>
              <MasteryBadge progress={t} showBar />
            </li>
          {/each}
        </ul>
      </section>
    {/if}
  {/if}
</div>
