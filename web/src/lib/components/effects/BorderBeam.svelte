<script lang="ts">
  import { cn } from "../ui/utils";

  interface Props {
    active?: boolean;
    class?: string;
  }

  let { active = false, class: className = "" }: Props = $props();
</script>

{#if active}
  <div class={cn("border-beam", className)} aria-hidden="true"></div>
{/if}
