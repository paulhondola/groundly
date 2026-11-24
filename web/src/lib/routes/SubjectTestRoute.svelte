<script lang="ts">
  import { searchParams } from "sv-router";
  import { parseTestSearch, route } from "../router";
  import NotFoundView from "../views/NotFoundView.svelte";
  import TestView from "../views/TestView.svelte";

  // sv-router already decodes :subject once via decodeURIComponent in matchRoute.
  const subject = $derived(route.params.subject || null);
  const parsed = $derived(parseTestSearch(searchParams.toURLSearchParams()));
</script>

{#if subject !== null}
  <TestView {subject} topicId={parsed.topicId} n={parsed.n} />
{:else}
  <NotFoundView />
{/if}
