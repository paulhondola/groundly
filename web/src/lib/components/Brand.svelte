<script lang="ts">
  import { cn } from "./ui/utils";

  interface Props {
    class?: string;
  }

  let { class: className = "" }: Props = $props();
</script>

<a
  href="/"
  class={cn(
    "inline-flex items-center gap-2.5 text-lg font-semibold tracking-tight text-foreground hover:opacity-85",
    className,
  )}
>
  <img src="/static/groundly-mark-light.svg" alt="" class="h-6 w-6 shrink-0 dark:hidden" />
  <img src="/static/groundly-mark-dark.svg" alt="" class="hidden h-6 w-6 shrink-0 dark:block" />
  <span>Groundly</span>
</a>
