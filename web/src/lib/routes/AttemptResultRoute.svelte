<script lang="ts">
  import { parsePositiveInt, route } from "../router";
  import NotFoundView from "../views/NotFoundView.svelte";
  import ResultView from "../views/ResultView.svelte";

  // sv-router already decodes :subject once via decodeURIComponent in matchRoute.
  const subject = $derived(route.params.subject || null);
  const attemptId = $derived(parsePositiveInt(route.params.id));
</script>

{#if subject !== null && attemptId !== null}
  <ResultView {subject} {attemptId} />
{:else}
  <NotFoundView />
{/if}
