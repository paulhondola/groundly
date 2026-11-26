<script lang="ts">
  import FileText from "@lucide/svelte/icons/file-text";
  import { type SourceItem, materialURL } from "../api/client";

  interface Props {
    subject: string;
    src: SourceItem;
    /** Start with the passage shown. */
    open?: boolean;
  }

  let { subject, src, open = false }: Props = $props();

  // svelte-ignore state_referenced_locally
  let expanded = $state(open);

  const label = $derived(
    String(src.filename) + (src.page != null ? " · p." + Number(src.page) : ""),
  );
  const href = $derived(materialURL(subject, src.material_id, src.page));
  const hasPassage = $derived(src.text.trim() !== "");
</script>

{#snippet passagePre()}
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <pre
    tabindex="0"
    aria-label={"Text from " + label}
    class="max-h-48 overflow-auto rounded-lg border border-border bg-well p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-foreground/85 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
  >{src.text}</pre>
{/snippet}

<li class="space-y-2 px-3 py-2.5 text-[13px]">
  <div class="flex items-center gap-2.5">
    <FileText size={16} strokeWidth={1.5} class="shrink-0 text-subtle-foreground" aria-hidden="true" />
    {#if src.pdf === true}
      <a
        {href}
        target="_blank"
        rel="noopener"
        class="min-w-0 flex-1 truncate text-foreground underline-offset-4 hover:underline"
      >
        {src.filename}
      </a>
    {:else}
      <span class="min-w-0 flex-1 truncate text-foreground">{src.filename}</span>
    {/if}
    {#if src.page != null}
      <span class="font-mono text-xs text-subtle-foreground">p.{Number(src.page)}</span>
    {/if}
    {#if src.pdf === true && hasPassage}
      <button
        type="button"
        aria-expanded={expanded}
        onclick={() => (expanded = !expanded)}
        class="rounded-md border border-border px-1.5 py-0.5 text-xs text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        Passage
      </button>
    {/if}
  </div>
  {#if src.pdf !== true || (expanded && hasPassage)}
    {@render passagePre()}
  {/if}
</li>
