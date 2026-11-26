<script lang="ts">
  import type { TopicProgress } from "../api/client";
  import { cn } from "./ui/utils";

  interface Props {
    state: TopicProgress["state"] | undefined;
  }

  let { state = "not_started" }: Props = $props();
</script>

<!-- Green = mastered, half green = learning, hollow = not tested. The chip's text says it too. -->
<span
  aria-hidden="true"
  class={cn(
    "size-2 shrink-0 rounded-full",
    state === "mastered" && "bg-mastered",
    state === "in_progress" && "bg-[linear-gradient(90deg,var(--status-mastered)_50%,var(--border)_50%)]",
    state === "not_started" && "border-[1.5px] border-[var(--status-empty)]",
  )}
></span>
