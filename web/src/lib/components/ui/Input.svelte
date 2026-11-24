<script lang="ts">
  import type { HTMLInputAttributes } from "svelte/elements";
  import { cn } from "./utils";

  type Props = {
    value?: string;
    class?: string;
  } & Omit<HTMLInputAttributes, "value" | "class">;

  let { value = $bindable(""), class: className = "", ...rest }: Props = $props();
</script>

<input
  bind:value
  class={cn(
    "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm text-foreground shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
    className,
  )}
  {...rest}
/>
