<script lang="ts">
  import { type SourceItem, materialURL } from "../api/client";

  interface Props {
    subject: string;
    src: SourceItem;
  }

  let { subject, src }: Props = $props();

  let expanded = $state(false);

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
    class="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-muted/50 p-3 font-mono text-xs leading-relaxed text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
  >{src.text}</pre>
{/snippet}

<li class="text-sm">
  {#if src.pdf === true}
    <div class="space-y-1.5">
      <div class="flex flex-wrap items-center gap-2">
        <a
          {href}
          target="_blank"
          rel="noopener"
          class="font-medium text-primary underline decoration-border underline-offset-4 hover:decoration-primary"
        >
          {label}
        </a>
        {#if hasPassage}
          <button
            type="button"
            aria-expanded={expanded}
            onclick={() => (expanded = !expanded)}
            class="inline-flex items-center rounded border border-border px-1.5 py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Passage
          </button>
        {/if}
      </div>
      {#if expanded && hasPassage}
        {@render passagePre()}
      {/if}
    </div>
  {:else}
    <div class="space-y-1.5">
      <div class="text-xs font-medium text-muted-foreground">{label}</div>
      {@render passagePre()}
    </div>
  {/if}
</li>
