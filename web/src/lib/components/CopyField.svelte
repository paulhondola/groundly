<script lang="ts">
  import { cn } from "./ui/utils";

  interface Props {
    text: string;
    label?: string;
    class?: string;
  }

  let { text, label = "Copy", class: className = "" }: Props = $props();

  let copied = $state(false);
  let timer: ReturnType<typeof setTimeout> | null = null;

  $effect(() => () => {
    if (timer !== null) clearTimeout(timer);
  });

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return; // no clipboard on plain HTTP; the text stays selectable
    }
    copied = true;
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(() => {
      copied = false;
      timer = null;
    }, 2000);
  }
</script>

<div
  class={cn(
    "flex items-center gap-2 rounded-[10px] border border-border bg-background py-2 pr-2 pl-3",
    className,
  )}
>
  <code class="min-w-0 flex-1 truncate font-mono text-xs text-foreground/85 select-all">{text}</code>
  <button
    type="button"
    onclick={() => void copy()}
    class="h-8 shrink-0 rounded-lg bg-secondary px-2.5 text-xs text-secondary-foreground hover:bg-secondary/70 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
  >
    {copied ? "Copied" : label}
  </button>
</div>
