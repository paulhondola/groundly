<script lang="ts">
  import { decodeParam, parsePositiveInt, route } from "../router";
  import NotFoundView from "../views/NotFoundView.svelte";
  import ResultView from "../views/ResultView.svelte";

  const subject = $derived(decodeParam(route.params.subject));
  const attemptId = $derived(parsePositiveInt(route.params.id));
</script>

{#if subject !== null && attemptId !== null}
  <ResultView {subject} {attemptId} />
{:else}
  <NotFoundView />
{/if}
