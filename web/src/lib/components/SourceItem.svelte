<script lang="ts">
  import { pageURL } from "../api";
  import type { TopicSource } from "../types";

  interface Props {
    subject: string;
    src: TopicSource;
  }

  let { subject, src }: Props = $props();

  const label = $derived(
    String(src.filename) + (src.page != null ? " · p." + Number(src.page) : ""),
  );
  const href = $derived(
    pageURL(
      subject,
      "/materials/" +
        Number(src.material_id) +
        (src.page != null ? "#page=" + Number(src.page) : ""),
    ),
  );
</script>

<li class="text-sm">
  {#if src.pdf === true}
    <a
      {href}
      target="_blank"
      rel="noopener"
      class="font-medium text-primary underline decoration-border underline-offset-4 hover:decoration-primary"
    >
      {label}
    </a>
  {:else}
    <div class="space-y-1.5">
      <div class="text-xs font-medium text-muted-foreground">{label}</div>
      <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
      <pre
        tabindex="0"
        aria-label={"Text from " + label}
        class="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-muted/50 p-3 font-mono text-xs leading-relaxed text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >{src.text}</pre>
    </div>
  {/if}
</li>
