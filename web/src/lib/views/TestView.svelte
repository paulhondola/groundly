<script lang="ts">
  import { createMutation, createQuery, useQueryClient } from "@tanstack/svelte-query";
  import { ApiError, type SubmittedAnswer, apiClient, pageURL, unwrap } from "../api/client";
  import { queryKeys } from "../api/queries";
  import Button from "../components/ui/Button.svelte";
  import Card from "../components/ui/Card.svelte";
  import { cn } from "../components/ui/utils";
  import { navigate } from "../router";
  import {
    type StoredTest,
    createTestPersister,
    dropTest,
    isAnswered,
    loadTest,
    saveTest,
    shouldIgnoreShortcut,
    shuffled,
    storageKey,
  } from "./test-session";
  import { findTopicById, writePrompt } from "./tree-filter";

  interface Props {
    subject: string;
    topicId: number | null;
    n: number;
  }

  let { subject, topicId, n }: Props = $props();

  const qc = useQueryClient();

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

  let test = $state<StoredTest | null>(null);
  let loading = $state(true);
  let loadError = $state<string | null>(null);
  let submitError = $state<string | null>(null);
  let confirming = $state(false);
  let submitting = $state(false);

  const courseTitle = $derived(treeQuery.data ? treeQuery.data.title || subject : "");
  const scopeTitle = $derived(
    topicId == null
      ? "Whole course"
      : treeQuery.data
        ? (findTopicById(treeQuery.data.tree, topicId)?.title ?? "Removed topic")
        : "Whole course",
  );
  const treeError = $derived(
    treeQuery.isError && !treeQuery.data
      ? treeQuery.error instanceof Error
        ? treeQuery.error.message
        : "Could not load test"
      : null,
  );
  const isLoading = $derived(loading || (treeQuery.isPending && !treeQuery.data));
  const displayError = $derived(loadError ?? treeError);

  const key = $derived(storageKey(subject, topicId));
  const currentQuestion = $derived(test ? (test.questions[test.current] ?? null) : null);
  const total = $derived(test ? test.questions.length : 0);
  const answeredCount = $derived(
    test ? test.questions.filter((q) => isAnswered(q, test?.answers[q.id])).length : 0,
  );
  const unansweredCount = $derived(total - answeredCount);
  const progressPercent = $derived(total > 0 ? Math.round((answeredCount / total) * 100) : 0);

  let persister: ReturnType<typeof createTestPersister> | null = null;
  let sessionSeq = 0;

  interface SubmitVariables {
    subject: string;
    key: string;
    persister: ReturnType<typeof createTestPersister> | null;
    seq: number;
    body: {
      token: string;
      topic_id: number | null;
      answers: SubmittedAnswer[];
    };
  }

  const submitMutation = createMutation(() => ({
    mutationFn: async ({ subject: activeSubject, body }: SubmitVariables) =>
      unwrap(
        await apiClient.POST("/api/subjects/{subject}/attempts", {
          params: { path: { subject: activeSubject } },
          body,
        }),
      ),
    onSuccess: (created, vars) => {
      dropTest(vars.key);
      void qc.invalidateQueries({ queryKey: queryKeys.subject(vars.subject) });
      if (vars.seq === sessionSeq) {
        void navigate("/s/:subject/attempts/:id", {
          params: { subject: vars.subject, id: String(created.id) },
        });
      }
    },
    onError: (err, vars) => {
      if (err instanceof ApiError && err.code === "unknown_question") {
        vars.persister?.markDead();
      }
      if (vars.seq === sessionSeq) {
        confirming = false;
        submitError =
          err instanceof Error
            ? err.message
            : "the test could not be submitted — try Submit again";
      }
    },
    onSettled: (_data, _err, vars) => {
      if (vars.seq === sessionSeq) {
        submitting = false;
      }
    },
  }));

  const sending = $derived(submitting || submitMutation.isPending);

  function store(): void {
    if (test && persister) {
      persister.save($state.snapshot(test) as StoredTest);
    }
  }

  $effect(() => {
    const activeSubject = subject;
    const activeTopicId = topicId;
    const activeN = n;
    const activeKey = storageKey(activeSubject, activeTopicId);
    const seq = ++sessionSeq;
    const controller = new AbortController();

    persister = createTestPersister(activeKey);
    loading = true;
    loadError = null;
    submitError = null;
    confirming = false;
    submitting = false;
    test = null;

    (async () => {
      try {
        let loaded = loadTest(activeKey);
        if (!loaded || loaded.n !== activeN || !Array.isArray(loaded.questions)) {
          const drawn = unwrap(
            await apiClient.GET("/api/subjects/{subject}/test", {
              params: {
                path: { subject: activeSubject },
                query: { ...(activeTopicId != null ? { topic: activeTopicId } : {}), n: activeN },
              },
              signal: controller.signal,
            }),
          );
          if (controller.signal.aborted || seq !== sessionSeq) {
            return;
          }
          loaded = {
            n: activeN,
            token: crypto.randomUUID(),
            questions: drawn.questions.map((q) => ({ ...q, options: shuffled(q.options) })),
            answers: {},
            current: 0,
          };
          if (loaded.questions.length > 0) {
            saveTest(activeKey, loaded);
          }
        }
        if (!controller.signal.aborted && seq === sessionSeq) {
          test = loaded;
          loading = false;
        }
      } catch (err: unknown) {
        if (controller.signal.aborted || seq !== sessionSeq) {
          return;
        }
        loadError = err instanceof Error ? err.message : "Could not load test";
        loading = false;
      }
    })();

    return () => {
      controller.abort();
      sessionSeq += 1;
    };
  });

  $effect(() => {
    if (courseTitle) {
      document.title = "Test · " + courseTitle + " · Groundly";
    }
  });

  function go(index: number): void {
    if (!test || index < 0 || index >= test.questions.length) {
      return;
    }
    test.current = index;
    confirming = false;
    submitError = null;
    store();
  }

  function setOpenAnswer(questionId: number, value: string): void {
    if (!test) {
      return;
    }
    test.answers[questionId] = value;
    confirming = false;
    store();
  }

  function toggleOption(questionId: number, optionId: number, kind: "single" | "multiple"): void {
    if (!test) {
      return;
    }
    if (kind === "single") {
      test.answers[questionId] = [optionId];
    } else {
      const existing = test.answers[questionId];
      const set = new Set(Array.isArray(existing) ? existing : []);
      if (set.has(optionId)) {
        set.delete(optionId);
      } else {
        set.add(optionId);
      }
      test.answers[questionId] = [...set];
    }
    confirming = false;
    store();
  }

  function submit(): void {
    if (!test || sending) {
      return;
    }
    if (unansweredCount > 0 && !confirming) {
      confirming = true;
      return;
    }
    submitting = true;
    submitError = null;
    const answers: SubmittedAnswer[] = test.questions.map((q) =>
      q.kind === "open"
        ? {
            question_id: q.id,
            text: typeof test?.answers[q.id] === "string" ? (test.answers[q.id] as string) : "",
          }
        : {
            question_id: q.id,
            selected: Array.isArray(test?.answers[q.id]) ? (test.answers[q.id] as number[]) : [],
          },
    );
    submitMutation.mutate({
      subject,
      key,
      persister,
      seq: sessionSeq,
      body: {
        token: test.token,
        topic_id: topicId,
        answers,
      },
    });
  }

  function handleWindowKeydown(event: KeyboardEvent): void {
    if (!test || !currentQuestion) {
      return;
    }
    if (event.metaKey || event.ctrlKey || event.altKey || shouldIgnoreShortcut(event.target)) {
      return;
    }
    if (event.key === "ArrowLeft") {
      if (test.current > 0) {
        event.preventDefault();
        go(test.current - 1);
      }
      return;
    }
    if (event.key === "ArrowRight") {
      if (test.current < test.questions.length - 1) {
        event.preventDefault();
        go(test.current + 1);
      }
      return;
    }
    if (currentQuestion.kind !== "open" && /^[1-9]$/.test(event.key)) {
      const idx = Number(event.key) - 1;
      const option = currentQuestion.options[idx];
      if (option) {
        event.preventDefault();
        toggleOption(currentQuestion.id, option.id, currentQuestion.kind);
      }
    }
  }
</script>

<svelte:window onkeydown={handleWindowKeydown} />

<div class="mx-auto max-w-3xl space-y-6">
  <div class="space-y-1">
    <a
      href={pageURL(subject)}
      class="back inline-flex items-center text-xs font-medium text-muted-foreground hover:text-foreground"
    >
      ← Back to the map
    </a>
    {#if courseTitle}
      <h1 class="text-2xl font-semibold tracking-tight text-foreground">{courseTitle}</h1>
      <p class="text-sm text-muted-foreground">{scopeTitle}</p>
    {/if}
  </div>

  {#if isLoading}
    <p class="text-sm text-muted-foreground">Drawing test…</p>
  {:else if displayError}
    <Card class="border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive" role="alert">
      {displayError}
    </Card>
  {:else if !test || test.questions.length === 0}
    <Card class="notice p-6 text-sm">
      Say to your agent:
      <code class="font-mono text-foreground">
        {writePrompt(scopeTitle === "Whole course" ? courseTitle : scopeTitle, subject)}
      </code>
    </Card>
  {:else if currentQuestion}
    <div class="space-y-3">
      <div class="flex items-center justify-between text-xs text-muted-foreground">
        <span class="counter">Question {test.current + 1} of {total}</span>
        <span>{answeredCount} of {total} answered</span>
      </div>
      <div class="h-1.5 w-full overflow-hidden rounded-full bg-muted" aria-hidden="true">
        <div
          class="h-full rounded-full bg-primary transition-all"
          style={`width:${progressPercent}%`}
        ></div>
      </div>

      <nav aria-label="Questions" class="dots flex flex-wrap gap-1.5 pt-1">
        {#each test.questions as q, i (q.id)}
          {@const done = isAnswered(q, test.answers[q.id])}
          <button
            type="button"
            aria-label={"Question " + (i + 1) + (done ? ", answered" : "")}
            aria-current={i === test.current ? "step" : undefined}
            onclick={() => go(i)}
            class={cn(
              "dot inline-flex h-8 min-w-8 items-center justify-center rounded-md border px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              i === test.current
                ? "border-primary bg-primary text-primary-foreground"
                : done
                  ? "answered border-ring/40 bg-accent text-accent-foreground"
                  : "border-border bg-card text-muted-foreground hover:text-foreground",
            )}
          >
            {i + 1}
          </button>
        {/each}
      </nav>
    </div>

    {#if submitError}
      <Card
        class="notice bad border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive"
        role="alert"
      >
        {submitError}
      </Card>
    {/if}

    <Card class="p-6">
      <fieldset class="question space-y-4">
        <legend class="text-base font-medium leading-relaxed text-foreground">
          {currentQuestion.stem}
        </legend>

        {#if currentQuestion.code}
          <pre
            class="code overflow-x-auto rounded-lg border border-border bg-muted/60 p-4 font-mono text-xs leading-relaxed text-foreground"
          >{currentQuestion.code}</pre>
        {/if}

        {#if currentQuestion.kind === "open"}
          <textarea
            maxlength="4000"
            rows="6"
            aria-label="Your answer"
            value={typeof test.answers[currentQuestion.id] === "string"
              ? (test.answers[currentQuestion.id] as string)
              : ""}
            oninput={(event) => setOpenAnswer(currentQuestion.id, event.currentTarget.value)}
            class="w-full rounded-lg border border-input bg-background p-3 text-sm text-foreground shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            placeholder="Write your answer…"
          ></textarea>
        {:else}
          {@const selectedList = Array.isArray(test.answers[currentQuestion.id])
            ? (test.answers[currentQuestion.id] as number[])
            : []}
          {#if currentQuestion.kind === "multiple"}
            <p class="text-xs text-muted-foreground">Choose every answer that applies.</p>
          {/if}
          <div class="space-y-2">
            {#each currentQuestion.options as o, idx (o.id)}
              {@const checked = selectedList.includes(o.id)}
              <label
                class={cn(
                  "option flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm transition-colors",
                  checked
                    ? "border-primary bg-accent/60"
                    : "border-border bg-background hover:bg-muted/40",
                )}
              >
                <input
                  type={currentQuestion.kind === "single" ? "radio" : "checkbox"}
                  name={"q" + currentQuestion.id}
                  {checked}
                  onchange={() => {
                    if (currentQuestion.kind !== "open") {
                      toggleOption(currentQuestion.id, o.id, currentQuestion.kind);
                    }
                  }}
                  class="mt-0.5 h-4 w-4 accent-primary"
                />
                <span class="flex-1 leading-relaxed text-foreground">{o.text}</span>
                {#if idx < 9}
                  <kbd
                    class="hidden rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline-block"
                  >
                    {idx + 1}
                  </kbd>
                {/if}
              </label>
            {/each}
          </div>
        {/if}
      </fieldset>
    </Card>

    <div class="actions flex items-center justify-between gap-3">
      <Button
        variant="outline"
        disabled={test.current === 0}
        onclick={() => test && go(test.current - 1)}
      >
        Back
      </Button>
      {#if test.current < total - 1}
        <Button class="primary" onclick={() => test && go(test.current + 1)}>Next</Button>
      {:else}
        <Button class="primary" disabled={sending} onclick={() => submit()}>
          {sending ? "Submitting…" : "Submit"}
        </Button>
      {/if}
    </div>

    {#if confirming && unansweredCount > 0}
      <div
        role="alertdialog"
        aria-label="Confirm submit"
        class="notice wait flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--status-awaiting)]/40 bg-[color-mix(in_oklab,var(--status-awaiting)_10%,transparent)] p-4 text-sm"
      >
        <p class="text-foreground">
          {unansweredCount}
          {unansweredCount === 1 ? " question is" : " questions are"} unanswered and will count as wrong
        </p>
        <div class="flex items-center gap-2">
          <Button class="primary" size="sm" disabled={sending} onclick={() => submit()}>
            Submit anyway
          </Button>
          <Button variant="ghost" size="sm" onclick={() => (confirming = false)}>
            Go back
          </Button>
        </div>
      </div>
    {/if}
  {/if}
</div>
