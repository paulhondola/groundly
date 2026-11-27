<script lang="ts">
  import { Check, ChevronDown, ChevronLeft, ChevronRight } from "@lucide/svelte";
  import { Popover } from "bits-ui";
  import { tick } from "svelte";
  import type { RunKind, RunSelection } from "../api/client";
  import { effortLabel, providerLabel, runSelection, selectionLabel } from "../run-selection.svelte";
  import { runStore } from "../run.svelte";
  import Button, { buttonVariants } from "./ui/Button.svelte";

  interface Props {
    subject: string;
    kind: RunKind;
    topicId?: number;
    label: string;
    scopeLabel?: string;
  }

  let { subject, kind, topicId, label, scopeLabel }: Props = $props();
  let open = $state(false);
  let pending = $state(false);
  let spread = $state(false);
  let section = $state<"summary" | "model" | "effort">("summary");
  let panel = $state<HTMLDivElement | null>(null);

  const selection = $derived(runSelection.current);
  const provider = $derived(runStore.providers.find((item) => item.id === selection?.provider));
  const model = $derived(provider?.models?.find((item) => item.id === selection?.model));
  const defaultModel = $derived(
    provider?.models?.find((item) => item.id === provider?.default_model),
  );
  const efforts = $derived((selection?.model ? model : defaultModel)?.efforts ?? []);
  const problem = $derived(runSelection.issue(runStore.providers));
  const problemText: Record<string, string> = {
    provider_unavailable: "The selected provider is unavailable. Choose another provider to run.",
    catalog_unavailable: "Model options could not be loaded. Refresh the list or choose provider defaults.",
    invalid_selection: "The saved model or effort is unavailable. Choose a supported setting.",
  };
  const needsChoice = new Set([
    "no_host", "incompatible_host", "provider_unavailable", "catalog_unavailable", "invalid_selection",
  ]);
  const rowClass = "flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

  function setOpen(next: boolean) {
    open = next;
    section = "summary";
    if (next && !selection && runStore.providers[0]) {
      runSelection.selectProvider(runStore.providers[0].id);
    }
    spread = next && Boolean(selection && !runStore.providers.some((p) => p.id === selection.provider));
    if (!next) pending = false;
  }

  async function show(next: typeof section, focus?: string) {
    section = next;
    await tick();
    const target = focus
      ? panel?.querySelector<HTMLButtonElement>(focus)
      : (panel?.querySelector<HTMLButtonElement>('[aria-pressed="true"]') ??
        panel?.querySelector<HTMLButtonElement>("[data-menu-back]"));
    target?.focus();
  }

  function moveFocus(event: KeyboardEvent) {
    const directions: Record<string, number> = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 };
    const direction = directions[event.key];
    if (!direction || !(event.currentTarget instanceof HTMLButtonElement)) return;
    const options = Array.from(
      event.currentTarget.parentElement?.querySelectorAll("button") ?? [],
    );
    const index = options.indexOf(event.currentTarget);
    if (index < 0) return;
    event.preventDefault();
    options[(index + direction + options.length) % options.length]?.focus();
  }

  function chooseProvider(id: RunSelection["provider"]) {
    runSelection.selectProvider(id);
    spread = false;
    void show("summary", "[data-provider-control]");
  }

  function chooseModel(id: string | null, availableEfforts: string[]) {
    runSelection.selectModel(id, availableEfforts);
    void show("summary", "[data-model-control]");
  }

  function chooseEffort(effort: string | null) {
    runSelection.selectEffort(effort);
    void show("summary", "[data-effort-control]");
  }

  async function launch() {
    if (runStore.busy) return;
    if (!selection || problem) {
      pending = true;
      setOpen(true);
      return;
    }
    const requested = { ...selection };
    setOpen(false);
    const started = await runStore.start(subject, { kind, topic_id: topicId, selection: requested });
    if (started?.selection) {
      runSelection.rememberLaunch(started.selection);
    } else if (runStore.startError && needsChoice.has(runStore.startError)) {
      await runStore.attach();
      pending = true;
      setOpen(true);
    }
  }

  function primary() {
    if (!runSelection.configured || problem) {
      pending = true;
      setOpen(true);
      return;
    }
    void launch();
  }
</script>

<div class="flex min-w-0 flex-wrap items-center gap-2">
  <Button disabled={runStore.busy} onclick={primary}>{label}</Button>
  <Popover.Root {open} onOpenChange={setOpen}>
    <Popover.Trigger
      class={buttonVariants({ variant: "outline", class: "max-w-full justify-between" })}
      aria-label={selection ? "Agent configuration: " + selectionLabel(selection, provider) : "Choose agent"}
    >
      <span class="truncate">{selection ? selectionLabel(selection, provider) : "Choose agent"}</span>
      <ChevronDown size={15} class="shrink-0 text-muted-foreground" aria-hidden="true" />
    </Popover.Trigger>
    <Popover.Portal>
      <Popover.Content
        align="end"
        sideOffset={8}
        collisionPadding={12}
        aria-label="Agent configuration"
        class="z-50 w-80 max-w-[calc(100vw-24px)] rounded-[14px] border border-border bg-card p-2 text-card-foreground shadow-xl"
      >
        <div bind:this={panel}>
          {#if section === "summary"}
            {#if pending}
              <div class="px-3 pt-2 pb-1">
                <p class="text-sm font-medium">{label}</p>
                <p class="text-xs break-words text-muted-foreground">{scopeLabel ?? subject}</p>
              </div>
            {/if}
            {#if runStore.providers.length > 1 || !provider}
              <button type="button" class={rowClass} data-provider-control aria-expanded={spread} onclick={() => (spread = !spread)}>
                <span>Provider</span>
                <span class="flex items-center gap-2 text-muted-foreground">
                  {provider?.label ?? (selection ? providerLabel(selection.provider) : "Choose")}
                  <ChevronDown size={14} aria-hidden="true" />
                </span>
              </button>
            {:else}
              <div class="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                <span>Provider</span><span class="text-muted-foreground">{provider.label}</span>
              </div>
            {/if}
            {#if spread && runStore.providers.length}
              <div class="provider-choices flex flex-wrap gap-1.5 px-3 pb-2" role="group" aria-label="Providers">
                {#each runStore.providers as entry (entry.id)}
                  <Button
                    size="sm"
                    variant={selection?.provider === entry.id ? "secondary" : "outline"}
                    aria-pressed={selection?.provider === entry.id}
                    onclick={() => chooseProvider(entry.id)}
                    onkeydown={moveFocus}
                  >{entry.label}</Button>
                {/each}
              </div>
            {/if}
            <button type="button" class={rowClass} data-model-control disabled={!provider} onclick={() => void show("model")}>
              <span>Model</span>
              <span class="flex min-w-0 items-center gap-2 text-muted-foreground">
                <span class="truncate">{model?.label ?? selection?.model ?? "Provider default"}</span>
                <ChevronRight size={14} class="shrink-0" aria-hidden="true" />
              </span>
            </button>
            <button type="button" class={rowClass} data-effort-control disabled={!provider} onclick={() => void show("effort")}>
              <span>Effort</span>
              <span class="flex items-center gap-2 text-muted-foreground">
                {effortLabel(selection?.effort)}
                <ChevronDown size={14} aria-hidden="true" />
              </span>
            </button>
            {#if problem && problemText[problem]}
              <p class="px-3 py-2 text-xs text-muted-foreground" role="status">{problemText[problem]}</p>
              {#if problem === "catalog_unavailable"}
                <Button size="sm" variant="ghost" onclick={() => void runStore.attach()}>Refresh models</Button>
              {/if}
            {/if}
            {#if pending}
              <div class="flex justify-end border-t border-border px-2 pt-2">
                <Button size="sm" disabled={problem !== null || runStore.busy} onclick={() => void launch()}>Run</Button>
              </div>
            {/if}
          {:else}
            <button type="button" data-menu-back class={rowClass} onclick={() => void show("summary", section === "model" ? "[data-model-control]" : "[data-effort-control]")}>
              <span class="flex items-center gap-2"><ChevronLeft size={15} aria-hidden="true" />{section === "model" ? "Model" : "Effort"}</span>
            </button>
            {#if section === "model"}
              <div class="max-h-[min(18rem,45vh)] overflow-y-auto" role="group" aria-label="Models">
                <button type="button" class={rowClass} aria-pressed={selection?.model === null}
                  onclick={() => chooseModel(null, defaultModel?.efforts ?? [])} onkeydown={moveFocus}>
                  Provider default
                  {#if selection?.model === null}<Check size={15} aria-hidden="true" />{/if}
                </button>
                {#each provider?.models ?? [] as entry (entry.id)}
                  <button type="button" class={rowClass} aria-pressed={selection?.model === entry.id}
                    onclick={() => chooseModel(entry.id, entry.efforts)} onkeydown={moveFocus}>
                    {entry.label}
                    {#if selection?.model === entry.id}<Check size={15} aria-hidden="true" />{/if}
                  </button>
                {/each}
              </div>
            {:else}
              <div role="group" aria-label="Effort levels">
                {#each [null, ...efforts] as effort (effort)}
                  <button type="button" class={rowClass} aria-pressed={selection?.effort === effort}
                    onclick={() => chooseEffort(effort)} onkeydown={moveFocus}>
                    {effortLabel(effort)}
                    {#if selection?.effort === effort}<Check size={15} aria-hidden="true" />{/if}
                  </button>
                {/each}
              </div>
            {/if}
          {/if}
        </div>
      </Popover.Content>
    </Popover.Portal>
  </Popover.Root>
</div>

<style>
  @keyframes fan-out {
    from { opacity: 0.45; transform: translateY(-5px) scaleX(0.86); }
    to { opacity: 1; transform: none; }
  }
  .provider-choices {
    transform-origin: right center;
    animation: fan-out 220ms cubic-bezier(0.22, 1, 0.36, 1);
  }
  @media (prefers-reduced-motion: reduce) {
    .provider-choices { animation: none; }
  }
</style>
