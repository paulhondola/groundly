<script lang="ts">
  import { api, getJSON, pageURL } from "../api";
  import MasteryBadge from "../components/MasteryBadge.svelte";
  import SourceItem from "../components/SourceItem.svelte";
  import LiquidMetalButton from "../components/effects/LiquidMetalButton.svelte";
  import Badge, { type BadgeVariant } from "../components/ui/Badge.svelte";
  import Card from "../components/ui/Card.svelte";
  import { cn, onPress } from "../components/ui/utils";
  import { runStore } from "../run.svelte";
  import type { AnswerState, AttemptResult, TreeResponse } from "../types";

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

  let result = $state<AttemptResult | null>(null);
  let courseTitle = $state("");
  let scopeTitle = $state("Whole course");
  let loading = $state(true);
  let error = $state<string | null>(null);

  const canGrade = $derived(Boolean(result?.provisional && runStore.available));

  async function loadResult(
    silent: boolean,
    activeSubject: string,
    activeAttemptId: number,
  ): Promise<void> {
    if (!silent) {
      loading = true;
      error = null;
    }
    try {
      const [attemptRes, treeRes] = await Promise.all([
        getJSON<AttemptResult>(api(activeSubject, "/attempts/" + activeAttemptId)),
        getJSON<TreeResponse>(api(activeSubject, "/tree")),
      ]);
      if (activeSubject !== subject || activeAttemptId !== attemptId) {
        return;
      }
      const course = treeRes.title || activeSubject;
      let scope = attemptRes.scope_topic_id == null ? "Whole course" : "Removed topic";
      for (const t of treeRes.tree) {
        for (const node of [t, ...t.subtopics]) {
          if (node.id === attemptRes.scope_topic_id) {
            scope = node.title;
          }
        }
      }
      result = attemptRes;
      courseTitle = course;
      scopeTitle = scope;
      document.title = "Result · " + course + " · Groundly";
      if (!silent) {
        loading = false;
      }
    } catch (err: unknown) {
      if (!silent && activeSubject === subject && activeAttemptId === attemptId) {
        error = err instanceof Error ? err.message : "Could not load attempt result";
        loading = false;
      }
    }
  }

  $effect(() => {
    const activeSubject = subject;
    const activeAttemptId = attemptId;
    void runStore.attach();
    runStore.setHooks({
      subject: activeSubject,
      onRefresh: () => loadResult(true, activeSubject, activeAttemptId),
      onEnd: () => loadResult(true, activeSubject, activeAttemptId),
    });
    void loadResult(false, activeSubject, activeAttemptId);

    return () => {
      runStore.setHooks({ subject: null, onRefresh: null, onEnd: null });
    };
  });
</script>

<div class="mx-auto max-w-3xl space-y-6">
  <div class="space-y-1">
    <a
      href={pageURL(subject)}
      class="back inline-flex items-center text-xs font-medium text-muted-foreground hover:text-foreground"
    >
      ← Back to the map
    </a>
    {#if result}
      <h1 class="text-2xl font-semibold tracking-tight text-foreground">{courseTitle}</h1>
      <p class="text-sm text-muted-foreground">{scopeTitle} · {result.submitted_at}</p>
    {/if}
  </div>

  {#if loading}
    <p class="text-sm text-muted-foreground">Loading results…</p>
  {:else if error}
    <Card class="border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive" role="alert">
      {error}
    </Card>
  {:else if result}
    <Card class="space-y-3 p-6">
      <p class="score text-3xl font-semibold tracking-tight text-foreground">
        {result.score.correct} / {result.score.graded} correct
      </p>
      {#if result.provisional}
        <div class="start flex flex-wrap items-center justify-between gap-3 pt-1">
          <p class="text-sm text-muted-foreground">
            provisional, {result.score.awaiting} awaiting review
          </p>
          {#if canGrade}
            <LiquidMetalButton
              label="Grade now"
              disabled={runStore.busy}
              {...onPress(() => void runStore.start(subject, { kind: "grade" }))}
            />
          {/if}
        </div>
      {/if}
    </Card>

    <div class="space-y-4">
      {#each result.answers as a, i (a.answer_id)}
        <article
          class={cn(
            "answer rounded-xl border border-border bg-card p-5 shadow-xs",
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
                <ul class="sources mt-2 space-y-2" role="list">
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
