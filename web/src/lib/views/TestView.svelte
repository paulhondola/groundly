<script lang="ts">
  import { createMutation, createQuery, useQueryClient } from "@tanstack/svelte-query";
  import Check from "@lucide/svelte/icons/check";
  import X from "@lucide/svelte/icons/x";
  import { ApiError, type SubmittedAnswer, apiClient, pageURL, unwrap } from "../api/client";
  import { queryKeys, treeQueryOptions } from "../api/queries";
  import SourceItem from "../components/SourceItem.svelte";
  import Button from "../components/ui/Button.svelte";
  import Card from "../components/ui/Card.svelte";
  import { cn } from "../components/ui/utils";
  import { navigate } from "../router";
  import {
    type StoredTest,
    createTestPersister,
    generateAttemptToken,
    isAnswered,
    loadTest,
    optionLetter,
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

  const treeQuery = createQuery(() => treeQueryOptions(subject));

  let test = $state<StoredTest | null>(null);
  let loading = $state(true);
  let loadError = $state<string | null>(null);
  let submitError = $state<string | null>(null);
  let confirming = $state(false);
  let sending = $state(false);

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
  const displayError = $derived(loadError ?? treeError);
  const isLoading = $derived((loading || (treeQuery.isPending && !treeQuery.data)) && !displayError);

  const currentQuestion = $derived(test ? (test.questions[test.current] ?? null) : null);
  const total = $derived(test ? test.questions.length : 0);
  const answeredCount = $derived(
    test ? test.questions.filter((q) => isAnswered(q, test?.answers[q.id])).length : 0,
  );
  const unansweredCount = $derived(total - answeredCount);
  const checkResult = $derived(
    test && currentQuestion ? (test.checked?.[currentQuestion.id] ?? null) : null,
  );
  const checkedResults = $derived(test ? Object.values(test.checked ?? {}) : []);
  const correctCount = $derived(checkedResults.filter((r) => r.correct).length);
  const keyLetters = $derived(
    checkResult && currentQuestion
      ? currentQuestion.options
          .map((o, i) => (checkResult.key.includes(o.id) ? optionLetter(i) : null))
          .filter((l) => l !== null)
      : [],
  );
  const canCheck = $derived(
    Boolean(
      test &&
        currentQuestion &&
        currentQuestion.kind !== "open" &&
        !checkResult &&
        isAnswered(currentQuestion, test.answers[currentQuestion.id]),
    ),
  );

  const checkable = $derived(
    Boolean(currentQuestion && currentQuestion.kind !== "open" && !checkResult),
  );

  let checking = $state(false);
  let checkError = $state<string | null>(null);

  let persister: ReturnType<typeof createTestPersister> | null = null;
  let sessionSeq = 0;

  interface SubmitVariables {
    subject: string;
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
    onSuccess: async (created, vars) => {
      vars.persister?.markDead();
      void qc.invalidateQueries({ queryKey: queryKeys.subject(vars.subject) });
      if (vars.seq === sessionSeq) {
        await navigate("/s/:subject/attempts/:id", {
          params: { subject: vars.subject, id: String(created.id) },
        });
        if (vars.seq === sessionSeq) {
          sending = false;
        }
      }
    },
    onError: (err, vars) => {
      if (err instanceof ApiError && err.code === "unknown_question") {
        vars.persister?.markDead();
      }
      if (vars.seq === sessionSeq) {
        sending = false;
        confirming = false;
        submitError =
          err instanceof Error
            ? err.message
            : "the test could not be submitted — try Submit again";
      }
    },
  }));

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
    checkError = null;
    checking = false;
    confirming = false;
    sending = false;
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
            token: generateAttemptToken(),
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
    checkError = null;
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
    if (!test || test.checked?.[questionId]) {
      return; // a checked answer is locked
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

  async function check(): Promise<void> {
    const q = currentQuestion;
    if (!test || !q || q.kind === "open" || test.checked?.[q.id] || checking) {
      return;
    }
    const selected = test.answers[q.id];
    if (!Array.isArray(selected) || selected.length === 0) {
      return;
    }
    const seq = sessionSeq;
    checking = true;
    checkError = null;
    try {
      const result = unwrap(
        await apiClient.POST("/api/subjects/{subject}/questions/{id}/check", {
          params: { path: { subject, id: q.id } },
          body: { selected },
        }),
      );
      if (seq === sessionSeq && test) {
        test.checked = { ...test.checked, [q.id]: result };
        store();
      }
    } catch (err) {
      if (seq === sessionSeq) {
        checkError = err instanceof Error ? err.message : "the answer could not be checked";
      }
    } finally {
      if (seq === sessionSeq) {
        checking = false;
      }
    }
  }

  function submit(): void {
    if (!test || sending) {
      return;
    }
    if (unansweredCount > 0 && !confirming) {
      confirming = true;
      return;
    }
    sending = true;
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

<div class="mx-auto max-w-[760px] space-y-7">
  <div class="flex items-center gap-3">
    <Button
      variant="outline"
      size="icon"
      href={pageURL(subject)}
      aria-label="Leave the test, back to the map"
      class="back shrink-0 text-muted-foreground"
    >
      <X size={16} aria-hidden="true" />
    </Button>
    {#if courseTitle}
      <div class="flex min-w-0 flex-col gap-0.5">
        <h1 class="truncate text-[15px] font-medium text-foreground">{scopeTitle}</h1>
        <p class="truncate font-mono text-xs text-subtle-foreground">
          {courseTitle}{total > 0 ? " · " + total + (total === 1 ? " question" : " questions") : ""}
        </p>
      </div>
    {/if}
  </div>

  {#if isLoading}
    <p class="text-sm text-muted-foreground">Drawing test…</p>
  {:else if displayError}
    <Card class="border-destructive/40 p-4 text-sm text-destructive" role="alert">
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
      <div class="flex flex-wrap justify-between gap-2 text-[13px] text-muted-foreground">
        <span class="counter">Question {test.current + 1} of {total}</span>
        <span>
          {answeredCount} of {total} answered{checkedResults.length > 0
            ? " · " + correctCount + " correct · " + (checkedResults.length - correctCount) + " missed"
            : ""}
        </span>
      </div>
      <nav
        aria-label="Questions"
        class="dots grid gap-1.5"
        style={`grid-template-columns:repeat(${total},minmax(0,1fr))`}
      >
        {#each test.questions as q, i (q.id)}
          {@const done = isAnswered(q, test.answers[q.id])}
          {@const result = test.checked?.[q.id]}
          <button
            type="button"
            aria-label={"Question " +
              (i + 1) +
              (result ? (result.correct ? ", correct" : ", missed") : done ? ", answered" : "")}
            aria-current={i === test.current ? "step" : undefined}
            onclick={() => go(i)}
            class="dot group flex h-6 items-center rounded focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            <span
              class={cn(
                "h-1.5 w-full rounded-[3px] transition-colors",
                result
                  ? result.correct
                    ? "answered bg-mastered"
                    : "answered bg-missed"
                  : i === test.current
                    ? "bg-foreground/60"
                    : done
                      ? "answered bg-foreground/35"
                      : "bg-border group-hover:bg-ring/40",
              )}
            ></span>
          </button>
        {/each}
      </nav>
    </div>

    {#if submitError}
      <Card class="notice bad border-destructive/40 p-4 text-sm text-destructive" role="alert">
        {submitError}
      </Card>
    {/if}

    <fieldset class="question space-y-6" disabled={checkResult !== null}>
      <legend class="mb-6 text-2xl leading-snug font-semibold tracking-[-0.01em] text-foreground">
        {currentQuestion.stem}
      </legend>

      {#if currentQuestion.code}
        <pre
          class="code overflow-x-auto rounded-xl border border-border bg-muted p-4 font-mono text-[13px] leading-relaxed text-foreground"
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
          class="w-full rounded-xl border border-border bg-card p-4 text-[15px] text-foreground placeholder:text-subtle-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          placeholder="Write your answer…"
        ></textarea>
        <p class="text-xs text-subtle-foreground">
          Written answers are graded after you submit, by your agent.
        </p>
      {:else}
        {@const selectedList = Array.isArray(test.answers[currentQuestion.id])
          ? (test.answers[currentQuestion.id] as number[])
          : []}
        {#if currentQuestion.kind === "multiple"}
          <p class="text-[13px] text-muted-foreground">Choose every answer that applies.</p>
        {/if}
        <div class="space-y-2.5">
          {#each currentQuestion.options as o, idx (o.id)}
            {@const picked = selectedList.includes(o.id)}
            {@const isKey = checkResult?.key.includes(o.id) ?? false}
            <label
              class={cn(
                "option flex min-h-14 items-center gap-3.5 rounded-xl border px-4 py-3 text-[15px] leading-snug transition-colors has-[input:focus-visible]:ring-2 has-[input:focus-visible]:ring-ring",
                checkResult
                  ? isKey
                    ? picked
                      ? "border-mastered/60 bg-mastered/[0.08]"
                      : "border-dashed border-mastered/60"
                    : picked
                      ? "border-missed/60 bg-missed/[0.07]"
                      : "border-border bg-card opacity-70"
                  : picked
                    ? "cursor-pointer border-foreground bg-card"
                    : "cursor-pointer border-border bg-card hover:border-ring/40",
              )}
            >
              <input
                type={currentQuestion.kind === "single" ? "radio" : "checkbox"}
                name={"q" + currentQuestion.id}
                checked={picked}
                onchange={() => {
                  if (currentQuestion.kind !== "open") {
                    toggleOption(currentQuestion.id, o.id, currentQuestion.kind);
                  }
                }}
                class="sr-only"
              />
              <span
                aria-hidden="true"
                class={cn(
                  "grid size-7 shrink-0 place-items-center rounded-lg font-mono text-xs",
                  checkResult && isKey
                    ? picked
                      ? "bg-mastered text-background"
                      : "border border-mastered text-mastered"
                    : checkResult && picked
                      ? "bg-missed text-background"
                      : picked
                        ? "bg-foreground text-background"
                        : "bg-secondary text-muted-foreground",
                )}
              >
                {optionLetter(idx)}
              </span>
              <span class="flex-1 text-foreground">
                {o.text}{#if checkResult && picked}<span class="sr-only"> (your answer)</span>{/if}
              </span>
              {#if checkResult && isKey}
                <Check size={18} class="shrink-0 text-mastered" aria-label="Correct answer" />
              {/if}
            </label>
          {/each}
        </div>
      {/if}
    </fieldset>

    {#if checkError}
      <p class="text-sm text-destructive" role="alert">{checkError}</p>
    {/if}

    {#if checkResult}
      <div
        role="status"
        class={cn(
          "space-y-3 rounded-xl border bg-card p-[18px]",
          checkResult.correct ? "border-mastered/35" : "border-missed/35",
        )}
      >
        <p class={cn("text-[15px] font-medium", checkResult.correct ? "text-mastered" : "text-missed")}>
          {#if checkResult.correct}
            Correct
          {:else}
            Not quite. The {keyLetters.length === 1 ? "answer is" : "answers are"}
            {keyLetters.join(", ")}.
          {/if}
        </p>
        {#if checkResult.sources.length > 0}
          <p class="text-[13px] text-muted-foreground">What your course says:</p>
          <ul class="divide-y divide-border overflow-hidden rounded-[10px] border border-border" role="list">
            {#each checkResult.sources as src (src.chunk_id)}
              <SourceItem {subject} {src} open />
            {/each}
          </ul>
        {/if}
      </div>
    {/if}

    <div class="actions flex flex-wrap items-center justify-between gap-3">
      <Button
        variant="outline"
        size="lg"
        disabled={test.current === 0}
        onclick={() => test && go(test.current - 1)}
      >
        Back
      </Button>
      <div class="flex flex-wrap items-center gap-2">
        {#if test.current < total - 1}
          <Button
            variant={checkable ? "outline" : "default"}
            size="lg"
            class={checkable ? "" : "primary"}
            onclick={() => test && go(test.current + 1)}
          >
            {checkResult ? "Next question" : "Next"}
          </Button>
        {:else}
          <Button
            variant={checkable ? "outline" : "default"}
            size="lg"
            class={checkable ? "" : "primary"}
            disabled={sending}
            onclick={() => submit()}
          >
            {sending ? "Submitting…" : "Submit"}
          </Button>
        {/if}
        {#if checkable}
          <Button size="lg" class="primary" disabled={!canCheck || checking} onclick={() => void check()}>
            {checking ? "Checking…" : "Check answer"}
          </Button>
        {/if}
      </div>
    </div>

    {#if confirming && unansweredCount > 0}
      <div
        role="alertdialog"
        aria-label="Confirm submit"
        class="notice wait flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-4 text-sm"
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
