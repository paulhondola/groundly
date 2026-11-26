<script module lang="ts">
  import type { TopicProgress } from "../api/client";

  export function progressLine(p: TopicProgress | null): string {
    if (!p || p.state === "not_started") {
      return "Not started";
    }
    if (p.state === "mastered") {
      return "Mastered";
    }
    if (p.subtopics != null) {
      return p.subtopics_mastered + " of " + p.subtopics + " subtopics mastered";
    }
    if (p.answered >= 10) {
      return p.recent_correct + " of last 10 correct";
    }
    return p.answered + " answered";
  }

  /** 0..100: how far a topic is towards mastered. */
  export function progressRatio(p: TopicProgress | null): number {
    if (!p || p.state === "not_started") {
      return 0;
    }
    if (p.state === "mastered") {
      return 100;
    }
    if (p.subtopics != null && p.subtopics > 0) {
      return Math.round(((p.subtopics_mastered ?? 0) / p.subtopics) * 100);
    }
    if (p.recent > 0) {
      return Math.round((p.recent_correct / Math.max(p.recent, 10)) * 100);
    }
    return 10;
  }
</script>

<script lang="ts">
  import Badge, { type BadgeVariant } from "./ui/Badge.svelte";
  import { cn } from "./ui/utils";

  interface Props {
    progress: TopicProgress | null;
    showBar?: boolean;
    class?: string;
  }

  let { progress, showBar = false, class: className = "" }: Props = $props();

  const label = $derived(progressLine(progress));
  const variant = $derived<BadgeVariant>(
    progress?.state === "mastered"
      ? "mastered"
      : progress?.state === "in_progress"
        ? "progress"
        : "secondary",
  );

  const ratio = $derived(progressRatio(progress));
</script>

<div class={cn("inline-flex items-center gap-2", className)}>
  <Badge {variant}>{label}</Badge>
  {#if showBar && progress && progress.state !== "not_started"}
    <div
      class="h-1.5 w-16 overflow-hidden rounded-full bg-muted"
      aria-hidden="true"
    >
      <div
        class={cn(
          "h-full rounded-full transition-all",
          progress.state === "mastered" ? "bg-[var(--status-mastered)]" : "bg-[var(--status-progress)]",
        )}
        style={`width:${Math.max(4, Math.min(100, ratio))}%`}
      ></div>
    </div>
  {/if}
</div>
