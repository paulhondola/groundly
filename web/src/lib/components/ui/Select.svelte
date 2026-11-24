<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLSelectAttributes } from "svelte/elements";
  import { cn } from "./utils";

  type Props = {
    value?: string;
    class?: string;
    children?: Snippet;
  } & Omit<HTMLSelectAttributes, "value" | "class">;

  let { value = $bindable(""), class: className = "", children, ...rest }: Props = $props();
</script>

<select
  bind:value
  class={cn(
    "inline-flex h-9 rounded-md border border-input bg-background px-2.5 py-1 text-sm text-foreground shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
    className,
  )}
  {...rest}
>
  {@render children?.()}
</select>
