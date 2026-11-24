<script lang="ts">
  import { createRunStore, runStore } from "../run.svelte";
  import BorderBeam from "./effects/BorderBeam.svelte";
  import ThinkingOrb from "./effects/ThinkingOrb.svelte";
  import { phaseLabelFor } from "./effects/orb-state";
  import Badge from "./ui/Badge.svelte";
  import Button from "./ui/Button.svelte";
  import { cn } from "./ui/utils";

  interface Props {
    store?: ReturnType<typeof createRunStore>;
    class?: string;
  }

  const KIND_WORDS = {
    write: "Writing questions",
    grade: "Grading written answers",
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
    class={cn(
      "run relative overflow-hidden rounded-xl border border-border bg-card p-4 text-card-foreground shadow-sm",
      className,
    )}
  >
    <BorderBeam active={working} />

    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="flex min-w-0 items-center gap-3">
        {#if working}
          <ThinkingOrb
            phase={activeRun?.phase ?? "starting"}
            kind={activeRun?.kind ?? "write"}
            size={32}
            label={phaseLabel}
          />
        {/if}
        <div class="flex min-w-0 flex-wrap items-center gap-2">
          <h2 class="truncate text-sm font-semibold text-foreground">{heading}</h2>
          {#if working}
            <Badge variant="progress">{phaseLabel}</Badge>
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
          <Button variant="ghost" size="sm" onclick={() => store.dismiss()}>
            Dismiss
          </Button>
        {/if}
      </div>
    </div>

    {#if activeRun && activeRun.thought}
      <div class="mt-3 rounded-lg border border-border/70 bg-muted/40 p-3">
        <div class="mb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          {channelLabel}
        </div>
        <pre
          bind:this={thoughtBox}
          class="max-h-36 overflow-y-auto whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-foreground"
        >{activeRun.thought}</pre>
      </div>
    {/if}

    {#if recentSteps.length > 0}
      <ol class="steps mt-3 list-decimal space-y-1 pl-5 text-xs text-muted-foreground">
        {#each recentSteps as step, idx (`${idx}-${step}`)}
          <li>{step}</li>
        {/each}
      </ol>
    {/if}

    <p
      class={cn(
        "outcome text-sm",
        store.message ? "mt-3 font-medium text-foreground" : "sr-only",
      )}
      role="status"
    >
      {store.message ?? ""}
    </p>
  </section>
{/if}
