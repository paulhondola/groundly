<script lang="ts">
  import { searchParams } from "sv-router";
  import { decodeParam, parseTestSearch, route } from "../router";
  import NotFoundView from "../views/NotFoundView.svelte";
  import TestView from "../views/TestView.svelte";

  const subject = $derived(decodeParam(route.params.subject));
  const parsed = $derived(parseTestSearch(searchParams.toURLSearchParams()));
</script>

{#if subject !== null}
  <TestView {subject} topicId={parsed.topicId} n={parsed.n} />
{:else}
  <NotFoundView />
{/if}
