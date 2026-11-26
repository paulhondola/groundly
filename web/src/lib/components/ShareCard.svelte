<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import Check from "@lucide/svelte/icons/check";
  import FileText from "@lucide/svelte/icons/file-text";
  import X from "@lucide/svelte/icons/x";
  import { apiClient, unwrap } from "../api/client";
  import { queryKeys } from "../api/queries";
  import { formatBytes } from "../time";
  import Button from "./ui/Button.svelte";
  import Select from "./ui/Select.svelte";

  interface Props {
    /** Subject to preselect, e.g. from the topic map's Share link. */
    initial?: string | null;
  }

  let { initial = null }: Props = $props();

  const subjectsQuery = createQuery(() => ({
    queryKey: queryKeys.subjects(),
    queryFn: async ({ signal }) => unwrap(await apiClient.GET("/api/subjects", { signal })),
  }));

  // svelte-ignore state_referenced_locally
  let chosen = $state(initial ?? "");
  let materials = $state(true);
  let exporting = $state(false);
  let exportError = $state<string | null>(null);

  const subjects = $derived(subjectsQuery.data ?? []);
  const subject = $derived(
    subjects.some((s) => s.subject === chosen) ? chosen : (subjects[0]?.subject ?? ""),
  );

  const infoQuery = createQuery(() => ({
    queryKey: queryKeys.exportInfo(subject),
    queryFn: async ({ signal }) =>
      unwrap(
        await apiClient.GET("/api/subjects/{subject}/export/info", {
          params: { path: { subject } },
          signal,
        }),
      ),
    enabled: subject !== "",
  }));

  const info = $derived(infoQuery.data ?? null);
  const infoError = $derived(
    infoQuery.isError ? (infoQuery.error?.message ?? "Could not read this subject") : null,
  );
  const bytes = $derived(info ? info.store_bytes + (materials ? info.materials_bytes : 0) : 0);

  $effect.pre(() => {
    void subject;
    void materials;
    exportError = null;
  });

  async function download(): Promise<void> {
    if (!subject || exporting) {
      return;
    }
    exporting = true;
    exportError = null;
    try {
      // ponytail: the bundle is read into memory before it is saved, so a refusal (409: the
      // manifest is corrupt) shows its message here instead of saving an error as a file.
      // A plain link would stream large bundles; it cannot show why an export failed.
      const blob = unwrap(
        await apiClient.GET("/api/subjects/{subject}/export", {
          params: { path: { subject }, query: { materials } },
          parseAs: "blob",
        }),
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = info?.filename ?? subject + ".groundly";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      exportError = err instanceof Error ? err.message : "the bundle could not be built";
    } finally {
      exporting = false;
    }
  }
</script>

<section
  aria-labelledby="share-heading"
  class="flex flex-col gap-5 rounded-2xl border border-border bg-card p-6"
>
  <h2
    id="share-heading"
    class="text-[13px] font-medium tracking-[0.08em] text-subtle-foreground uppercase"
  >
    Share a course
  </h2>

  {#if subjectsQuery.isPending}
    <p class="text-sm text-muted-foreground">Loading subjects…</p>
  {:else if subjects.length === 0}
    <p class="text-sm text-muted-foreground">No subjects to share yet.</p>
  {:else}
    <label class="flex flex-col gap-1.5 text-[13px] text-muted-foreground">
      Course
      <Select
        value={subject}
        onchange={(e) => (chosen = e.currentTarget.value)}
        class="h-11 w-full rounded-[10px] bg-background px-3 text-sm"
      >
        {#each subjects as s (s.subject)}
          <option value={s.subject}>{s.title ? s.title + " · " + s.subject : s.subject}</option>
        {/each}
      </Select>
    </label>

    <label class="flex min-h-11 items-center gap-3 text-sm text-foreground">
      <input type="checkbox" bind:checked={materials} class="size-[18px] accent-foreground" />
      Include the course files
    </label>

    <!-- What a bundle holds and what it never holds: decision 53, UC-30. Not optional copy. -->
    <div class="export-statement flex flex-col gap-3 rounded-xl border border-border bg-background p-4 text-[13px]">
      <p class="text-foreground">
        This bundle contains everything indexed in this subject, including its topic map and
        question bank.
      </p>
      <ul class="flex flex-col gap-1.5 text-muted-foreground" role="list">
        <li class="flex gap-2">
          <Check size={16} class="mt-px shrink-0 text-mastered" aria-hidden="true" />
          <span><code class="font-mono text-foreground">store.db</code>, exported whole: the indexed
            text, the topic map and the question bank</span>
        </li>
        <li class="flex gap-2">
          {#if materials}
            <Check size={16} class="mt-px shrink-0 text-mastered" aria-hidden="true" />
            <span><code class="font-mono text-foreground">materials/</code>: your course files, so
              citations open the right page</span>
          {:else}
            <X size={16} class="mt-px shrink-0 text-subtle-foreground" aria-hidden="true" />
            <span><code class="font-mono text-foreground">materials/</code> left out: citations
              will not open a page</span>
          {/if}
        </li>
      </ul>
      <p class="pt-1 text-foreground">Never exported, from this computer or a paired device:</p>
      <ul class="flex flex-col gap-1.5 text-muted-foreground" role="list">
        <li class="flex gap-2">
          <X size={16} class="mt-px shrink-0 text-missed" aria-hidden="true" />
          <span><code class="font-mono text-foreground">progress.db</code>: your tests, answers and
            mastery</span>
        </li>
        <li class="flex gap-2">
          <X size={16} class="mt-px shrink-0 text-missed" aria-hidden="true" />
          <span><code class="font-mono text-foreground">remote.db</code>: your paired devices</span>
        </li>
        <li class="flex gap-2">
          <X size={16} class="mt-px shrink-0 text-missed" aria-hidden="true" />
          <span><code class="font-mono text-foreground">activity.db</code>: what your hosts and
            indexing runs did</span>
        </li>
      </ul>
    </div>

    <div class="flex items-center gap-3 rounded-xl border border-border bg-background p-3.5">
      <FileText size={24} strokeWidth={1.4} class="shrink-0 text-subtle-foreground" aria-hidden="true" />
      <div class="flex min-w-0 flex-1 flex-col gap-0.5">
        <span class="truncate font-mono text-[13px] text-foreground">
          {info?.filename ?? subject + ".groundly"}
        </span>
        <span class="text-xs text-subtle-foreground">
          {#if infoError}
            {infoError}
          {:else if info}
            {formatBytes(bytes)} before compression · open it with <code class="font-mono">groundly import</code>
          {:else}
            Measuring…
          {/if}
        </span>
      </div>
    </div>

    {#if exportError}
      <p class="text-sm text-destructive" role="alert">{exportError}</p>
    {/if}

    <Button size="lg" disabled={exporting || infoError !== null} onclick={() => void download()}>
      {exporting ? "Building the bundle…" : "Export file"}
    </Button>
  {/if}
</section>
