<script lang="ts">
  import { hostName, useActivity } from "../activity.svelte";
  import { authStore } from "../auth.svelte";
  import CopyField from "../components/CopyField.svelte";
  import DevicesCard from "../components/DevicesCard.svelte";
  import ShareCard from "../components/ShareCard.svelte";
  import Orb from "../components/orb/Orb.svelte";
  import { ago } from "../time";

  const activity = useActivity();

  const ORB_WORDS = {
    listening: "Listening",
    reading: "Reading",
    mapping: "Mapping",
    writing: "Writing",
  } as const;

  const initialSubject =
    typeof location === "undefined" ? null : new URLSearchParams(location.search).get("subject");

  $effect(() => {
    document.title = "Connections · Groundly";
  });
</script>

<div class="space-y-7">
  <div class="space-y-2">
    <h1 class="text-[32px] leading-tight font-semibold tracking-[-0.02em] text-foreground">
      Connections
    </h1>
    <p class="text-[15px] text-muted-foreground">
      Who is talking to your course library, and how to take a course with you.
    </p>
  </div>

  <div class="grid grid-cols-1 items-start gap-5 lg:grid-cols-[repeat(auto-fit,minmax(340px,1fr))]">
    <section
      aria-labelledby="hosts-heading"
      class="flex flex-col gap-5 rounded-2xl border border-border bg-card p-6"
    >
      <h2
        id="hosts-heading"
        class="text-[13px] font-medium tracking-[0.08em] text-subtle-foreground uppercase"
      >
        MCP hosts
      </h2>

      {#if !activity.available}
        <div class="flex items-center gap-4">
          <Orb mode="idle" size={64} />
          <p class="text-sm text-muted-foreground">Activity is not available right now.</p>
        </div>
      {:else if activity.hosts.length === 0}
        <div class="flex items-center gap-4">
          <Orb mode="idle" size={64} />
          <div class="flex flex-col gap-1">
            <span class="text-[17px] font-medium text-foreground">No host connected</span>
            <span class="text-[13px] text-muted-foreground">
              Start Claude Code or another MCP host with Groundly added.
            </span>
          </div>
        </div>
      {:else}
        <ul class="flex flex-col gap-4" role="list">
          {#each activity.hosts as host, i (host.started_at + ":" + i)}
            <li class="flex items-center gap-4">
              <Orb
                mode={activity.mode === "done" ? "done" : host.orb}
                size={64}
                label={hostName(host) + ": " + ORB_WORDS[host.orb]}
              />
              <div class="flex min-w-0 flex-col gap-1">
                <span class="truncate text-[17px] font-medium text-foreground">{hostName(host)}</span>
                <span class="text-[13px] text-muted-foreground">
                  {ORB_WORDS[host.orb]}{host.subject ? " · " + host.subject : ""} · over {host.transport}
                </span>
                <span class="text-xs text-subtle-foreground">
                  {host.last_call_at ? "Last call " + ago(host.last_call_at) : "No calls yet"}
                </span>
              </div>
            </li>
          {/each}
        </ul>
      {/if}

      {#if activity.indexing.length > 0}
        <ul class="flex flex-col gap-3" role="list" aria-label="Indexing">
          {#each activity.indexing as run, i (run.subject + ":" + run.started_at + ":" + i)}
            {@const pct = run.total > 0 ? Math.round((run.done / run.total) * 100) : 0}
            <li class="flex flex-col gap-2 rounded-xl border border-border bg-background p-3.5">
              <div class="flex items-center gap-2.5 text-[13px]">
                <Orb mode="reading" size={24} />
                <span class="shimmer min-w-0 flex-1 truncate">Indexing {run.subject}</span>
                <span class="font-mono text-xs text-subtle-foreground">{run.done}/{run.total}</span>
              </div>
              <div
                class="h-1 overflow-hidden rounded bg-border"
                role="progressbar"
                aria-label={"Indexing " + run.subject}
                aria-valuemin={0}
                aria-valuemax={run.total}
                aria-valuenow={run.done}
              >
                <div class="h-full rounded bg-foreground" style={`width:${pct}%`}></div>
              </div>
              {#if run.current_file}
                <span class="truncate font-mono text-[11px] text-subtle-foreground">
                  {run.current_file}
                </span>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}

      <div class="flex flex-col gap-2">
        <span class="text-[13px] text-muted-foreground">Add Groundly to Claude Code</span>
        <CopyField text="claude mcp add --scope user groundly -- groundly mcp" />
      </div>

      {#if activity.recent.length > 0}
        <div class="flex flex-col text-[13px]">
          <h3 class="pb-1.5 text-subtle-foreground">Recent activity</h3>
          <ul class="divide-y divide-border/60" role="list">
            {#each activity.recent as item, i (item.at + ":" + i)}
              <li class="flex justify-between gap-3 py-2">
                <span class="min-w-0 text-foreground">
                  {item.text}{item.subject ? " · " + item.subject : ""}
                </span>
                <span class="shrink-0 text-subtle-foreground">{ago(item.at)}</span>
              </li>
            {/each}
          </ul>
        </div>
      {/if}
    </section>

    {#if authStore.access === "loopback"}
      <DevicesCard />
    {/if}

    <ShareCard initial={initialSubject} />
  </div>
</div>
