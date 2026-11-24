<script lang="ts">
  import { getJSON, pageURL } from "../api";
  import Badge from "../components/ui/Badge.svelte";
  import Card from "../components/ui/Card.svelte";
  import { runStore } from "../run.svelte";
  import type { SubjectSummary } from "../types";

  let subjects = $state<SubjectSummary[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);

  $effect(() => {
    document.title = "Groundly";
    runStore.setHooks({ subject: null });
    void runStore.attach();

    let cancelled = false;
    loading = true;
    error = null;
    getJSON<SubjectSummary[]>("/api/subjects")
      .then((list) => {
        if (!cancelled) {
          subjects = list;
          loading = false;
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          error = err instanceof Error ? err.message : "Could not load subjects";
          loading = false;
        }
      });

    return () => {
      cancelled = true;
    };
  });
</script>

<div class="space-y-6">
  <div class="space-y-1">
    <h1 class="text-2xl font-semibold tracking-tight text-foreground">Subjects</h1>
    <p class="text-sm text-muted-foreground">Pick a subject to see its topic map.</p>
  </div>

  {#if loading}
    <p class="text-sm text-muted-foreground">Loading subjects…</p>
  {:else if error}
    <Card class="border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive" role="alert">
      {error}
    </Card>
  {:else if subjects.length === 0}
    <Card class="p-6 text-sm text-muted-foreground">
      No subjects yet. Index one with
      <code class="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground">
        groundly index
      </code>.
    </Card>
  {:else}
    <ul class="subjects grid gap-4 sm:grid-cols-2 lg:grid-cols-3" role="list">
      {#each subjects as s (s.subject)}
        <li>
          <a
            href={pageURL(s.subject)}
            class="block h-full rounded-xl transition-transform hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Card class="flex h-full flex-col justify-between gap-3 p-5 hover:border-ring/60">
              <div class="space-y-1">
                <h2 class="text-base font-semibold text-foreground">{s.title || s.subject}</h2>
                {#if s.title}
                  <p class="font-mono text-xs text-muted-foreground">{s.subject}</p>
                {/if}
              </div>
              <div>
                {#if s.error}
                  <Badge variant="destructive">{s.error}</Badge>
                {:else if s.has_tree}
                  <Badge variant="mastered">topic map ready</Badge>
                {:else}
                  <Badge variant="secondary">no map yet</Badge>
                {/if}
              </div>
            </Card>
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</div>
