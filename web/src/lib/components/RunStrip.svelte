<script lang="ts">
  import { createRunStore, runStore } from "../run.svelte";
  import { pageURL } from "../api/client";
  import { selectionLabel } from "../run-selection.svelte";
  import Check from "@lucide/svelte/icons/check";
  import Orb from "./orb/Orb.svelte";
  import { orbModeForRun, phaseLabelFor } from "./orb/orb-state";
  import Button from "./ui/Button.svelte";
  import { cn } from "./ui/utils";

  interface Props {
    store?: ReturnType<typeof createRunStore>;
    class?: string;
  }

  const KIND_WORDS = {
    write: "Writing questions",
    grade: "Grading written answers",
    map: "Creating a topic map",
  } as const;

  let { store = runStore, class: className = "" }: Props = $props();

  const activeRun = $derived(store.watchedRun);
  const working = $derived(activeRun ? activeRun.status === "running" : store.starting);
  const visible = $derived(!store.dismissed && Boolean(activeRun || store.starting || store.message));
  const heading = $derived(
    activeRun
      ? KIND_WORDS[activeRun.kind] + " · " + activeRun.subject
      : store.starting
        ? "Starting your agent"
        : "Agent run",
  );
  const phaseLabel = $derived(
    activeRun
      ? phaseLabelFor(activeRun.phase, activeRun.kind, activeRun.active_tool)
      : "Starting",
  );
  const channelLabel = $derived(
    activeRun?.thought_channel === "text" ? "Response" : "Thinking",
  );
  const recentSteps = $derived(activeRun ? activeRun.steps.slice(-6) : []);

  let thoughtBox = $state<HTMLPreElement | null>(null);

  $effect(() => {
    const text = activeRun?.thought ?? "";
    const el = thoughtBox;
    if (el && text) {
      el.scrollTop = el.scrollHeight;
    }
  });
</script>

{#if visible}
  <section
    aria-label="Agent run"
    class={cn("run rounded-[14px] border border-border bg-card p-4 text-card-foreground", className)}
  >
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="flex min-w-0 items-center gap-3">
        {#if working}
          <Orb
            mode={orbModeForRun(activeRun?.phase ?? "starting")}
            size={32}
            label={phaseLabel}
          />
        {/if}
        <div class="flex min-w-0 flex-col gap-0.5">
          <h2 class="truncate text-sm font-medium text-foreground">{heading}</h2>
          {#if activeRun?.selection}
            <span class="break-words text-xs text-muted-foreground">
              {selectionLabel(activeRun.selection, store.providers.find((p) => p.id === activeRun.selection.provider))}
            </span>
          {/if}
          {#if working}
            <span class="shimmer truncate text-xs">{phaseLabel}</span>
          {/if}
        </div>
      </div>

      <div class="flex items-center gap-2">
        {#if activeRun && working}
          <Button
            variant="outline"
            size="sm"
            disabled={store.stopping}
            onclick={() => void store.stop()}
          >
            {store.stopping ? "Stopping…" : "Stop"}
          </Button>
        {:else if !working}
          {#if activeRun?.kind === "map" && (activeRun.map_written || activeRun.error?.reason === "map_already_exists")}
            <Button variant="outline" size="sm" href={pageURL(activeRun.subject)}>Open topic map</Button>
          {/if}
          <Button variant="ghost" size="sm" onclick={() => store.dismiss()}>
            Dismiss
          </Button>
        {/if}
      </div>
    </div>

    {#if activeRun && activeRun.thought}
      <div class="mt-3 rounded-xl border border-border bg-well p-3">
        <div class="mb-1 text-[11px] font-medium tracking-[0.08em] text-subtle-foreground uppercase">
          {channelLabel}
        </div>
        <pre
          bind:this={thoughtBox}
          class="max-h-36 overflow-y-auto font-mono text-xs leading-relaxed break-words whitespace-pre-wrap text-foreground/85"
        >{activeRun.thought}</pre>
      </div>
    {/if}

    {#if recentSteps.length > 0}
      <ol class="steps mt-3 space-y-2 text-[13px] text-muted-foreground">
        {#each recentSteps as step, idx (`${idx}-${step}`)}
          <li class="flex items-center gap-2.5">
            <Check size={16} class="shrink-0 text-mastered" aria-hidden="true" />{step}
          </li>
        {/each}
      </ol>
    {/if}

    <p
      class={cn("outcome text-sm", store.message ? "mt-3 font-medium text-foreground" : "sr-only")}
      role="status"
    >
      {store.message ?? ""}
    </p>
  </section>
{/if}
